"""Tests for the static httpx fetcher."""
import time

import httpx
import pytest
import respx

from crawler.fetcher.static import FetchResult, FetchTimeoutError, fetch


@pytest.mark.asyncio
async def test_fetch_returns_html_and_status():
    async with respx.mock(assert_all_called=True) as router:
        router.get("https://example.com/").mock(
            return_value=httpx.Response(200, text="<html><title>Hi</title></html>",
                                        headers={"content-type": "text/html"})
        )
        result = await fetch("https://example.com/")
        assert isinstance(result, FetchResult)
        assert result.http_status == 200
        assert "<title>Hi</title>" in result.html
        assert result.content_type.startswith("text/html")
        assert result.final_url == "https://example.com/"


@pytest.mark.asyncio
async def test_fetch_follows_redirects():
    async with respx.mock(assert_all_called=True) as router:
        router.get("https://example.com/old").mock(
            return_value=httpx.Response(301, headers={"location": "https://example.com/new"})
        )
        router.get("https://example.com/new").mock(
            return_value=httpx.Response(
                200, text="redirected", headers={"content-type": "text/html"},
            )
        )
        result = await fetch("https://example.com/old")
        assert result.http_status == 200
        assert result.final_url == "https://example.com/new"
        assert result.html == "redirected"


@pytest.mark.asyncio
async def test_fetch_handles_non_html():
    async with respx.mock(assert_all_called=True) as router:
        router.get("https://example.com/a.json").mock(
            return_value=httpx.Response(
                200, text='{"a":1}', headers={"content-type": "application/json"},
            )
        )
        result = await fetch("https://example.com/a.json")
        assert result.content_type == "application/json"
        assert result.html == '{"a":1}'


@pytest.mark.asyncio
async def test_fetch_raises_fetch_timeout_error_when_deadline_is_already_past():
    """Pre-exhausted deadline → fetcher raises FetchTimeoutError without ever
    issuing an HTTP request. This is the contract the route handler relies on
    when Lambda is close to its own timeout."""
    past_deadline = time.monotonic() - 1.0  # already 1s in the past
    with pytest.raises(FetchTimeoutError):
        await fetch("https://example.com/", deadline=past_deadline)


@pytest.mark.asyncio
async def test_fetch_stops_retrying_when_deadline_exhausted(monkeypatch):
    """A second attempt is skipped if it can't complete before the deadline.
    Without this, `retries=2` with `read=15s` would happily blow past Lambda's
    28s timeout and the user would see a generic API Gateway 500.

    Uses a virtual `time.monotonic()` clock so the test is deterministic on
    overloaded CI runners — no real `time.sleep()` and no flakiness from
    scheduling jitter relative to a wall-clock deadline.
    """
    # Virtual clock that advances by 0.4s every read. Initial value 0.0.
    # With deadline=1.0 and _MIN_REQUEST_BUDGET_SEC=0.5:
    #   read 1 (pre-attempt 0): 0.4 → remaining 0.6 → proceed
    #   read 2 (post-failure 0): 0.8 → remaining 0.2 → FetchTimeoutError ✓
    clock = {"now": 0.0}

    def fake_monotonic() -> float:
        clock["now"] += 0.4
        return clock["now"]

    monkeypatch.setattr("crawler.fetcher.static.time.monotonic", fake_monotonic)

    call_count = {"n": 0}

    def _side(request):
        call_count["n"] += 1
        raise httpx.ConnectError("simulated DNS failure")

    async with respx.mock(assert_all_called=False) as router:
        router.get("https://example.com/").mock(side_effect=_side)

        deadline = 1.0  # absolute in the virtual clock
        with pytest.raises(FetchTimeoutError):
            await fetch(
                "https://example.com/",
                deadline=deadline,
                retries=2,  # would normally do 3 attempts without the deadline
            )
        # Hard assertion: we must NOT have done all 3 attempts.
        assert call_count["n"] < 3, (
            f"deadline-aware retry budget did not engage: {call_count['n']} attempts"
        )


@pytest.mark.asyncio
async def test_fetch_without_deadline_keeps_existing_retry_behavior():
    """Sanity: existing callers (no deadline kwarg) still get the original
    retry-on-error semantics."""
    call_count = {"n": 0}

    def _side(request):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise httpx.ConnectError("transient")
        return httpx.Response(200, text="ok", headers={"content-type": "text/html"})

    async with respx.mock(assert_all_called=True) as router:
        router.get("https://example.com/").mock(side_effect=_side)
        result = await fetch("https://example.com/")
        assert result.http_status == 200
        assert call_count["n"] == 2  # one retry consumed
