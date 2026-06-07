"""Tests for the headless worker handler.

These tests deliberately do NOT exercise real Playwright — `_fetch_headless`
is monkeypatched with an async stub. The intent is to verify that:

  1. `handler()` wires {url, persist} → fetch → pipeline → optional persist.
  2. `_persist_headless()` sequences S3 + DynamoDB writes correctly.
"""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws_resources():
    """Provision DDB + S3 to mirror prod, like tests/unit/test_static_worker.py."""
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="brightedge-pages",
            AttributeDefinitions=[
                {"AttributeName": "url_hash", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
            ],
            KeySchema=[
                {"AttributeName": "url_hash", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="raw")
        yield


def test_handler_runs_pipeline_and_returns_result(aws_resources, monkeypatch):
    """handler() takes {url, persist}, runs the pipeline, persists when asked."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")

    async def fake_fetch(url: str):
        # Minimal but well-formed HTML so process_html produces a real result.
        return (
            "<html><head><title>Example</title></head>"
            "<body><p>" + ("hello world " * 50) + "</p></body></html>",
            200,
        )

    from crawler.workers import headless_worker
    monkeypatch.setattr(headless_worker, "_fetch_headless", fake_fetch)

    result = headless_worker.handler(
        {"url": "http://example.com", "persist": True},
        context=None,
    )

    assert result["url"] == "http://example.com"
    assert result["fetcher_used"] == "headless"
    assert result["http_status"] == 200
    assert result["title"] == "Example"

    # Confirm the row landed in DDB.
    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    items = pages.scan()["Items"]
    assert len(items) == 1
    assert items[0]["title"] == "Example"

    # And the raw HTML landed in S3.
    s3 = boto3.client("s3", region_name="us-east-1")
    objs = s3.list_objects_v2(Bucket="raw")
    assert objs["KeyCount"] >= 1


def test_handler_skips_persist_when_persist_false(aws_resources, monkeypatch):
    """persist=False short-circuits S3 + DDB writes but still returns a result."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")

    async def fake_fetch(url: str):
        return "<html><head><title>X</title></head><body>x</body></html>", 200

    from crawler.workers import headless_worker
    monkeypatch.setattr(headless_worker, "_fetch_headless", fake_fetch)

    result = headless_worker.handler(
        {"url": "http://example.com", "persist": False},
        context=None,
    )

    assert result["fetcher_used"] == "headless"

    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    assert pages.scan()["Items"] == []
    s3 = boto3.client("s3", region_name="us-east-1")
    assert s3.list_objects_v2(Bucket="raw").get("KeyCount", 0) == 0


def test_handler_default_persist_is_true(aws_resources, monkeypatch):
    """Omitting `persist` defaults to True (matches the route's contract)."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")

    async def fake_fetch(url: str):
        return (
            "<html><head><title>Default</title></head>"
            "<body><p>" + ("word " * 60) + "</p></body></html>",
            200,
        )

    from crawler.workers import headless_worker
    monkeypatch.setattr(headless_worker, "_fetch_headless", fake_fetch)

    headless_worker.handler({"url": "http://example.com"}, context=None)

    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    assert len(pages.scan()["Items"]) == 1


def test_persist_skips_when_settings_unconfigured(monkeypatch):
    """Without PAGES_TABLE / RAW_HTML_BUCKET, _persist_headless is a no-op.

    This is the local-dev fallback referenced in the function's docstring.
    """
    import asyncio
    from datetime import UTC, datetime

    # Set to empty string explicitly — load_settings() reads via
    # `os.getenv(..., "")` so an unset OR empty value both produce ""
    # which is falsy and short-circuits the persistence path.
    monkeypatch.setenv("PAGES_TABLE", "")
    monkeypatch.setenv("RAW_HTML_BUCKET", "")

    from crawler.api.schemas import ExtractResult
    from crawler.workers import headless_worker

    fake_result = ExtractResult(
        url="http://example.com",
        url_hash="a" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="headless",
        http_status=200,
    )
    # Should not raise — no AWS clients are touched because settings are empty.
    asyncio.run(headless_worker._persist_headless(fake_result, "<html></html>"))


def test_persist_writes_s3_for_legitimately_empty_html(monkeypatch):
    """A passing result whose fetched HTML is the empty string MUST still
    trigger the S3 raw-HTML write — empty body on a 200 is valid (degenerate)
    content, not a gate-rejection sentinel.

    Regression: previously the gate path reassigned `html = ""` after firing
    and the S3 write was gated on `if html:`, which silently dropped any
    legitimately-empty body too. The fix uses `None` as the skip sentinel
    so the empty-string case round-trips to S3."""
    import asyncio
    from datetime import UTC, datetime
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")

    from crawler.api.schemas import ExtractResult
    from crawler.workers import headless_worker

    # A passing result: 200, no captcha-triggering title, high enough
    # confidence that no gate rule fires.
    result = ExtractResult(
        url="http://example.com/empty",
        url_hash="e" * 64,
        fetched_at=datetime(2026, 5, 24, tzinfo=UTC),
        fetcher_used="headless",
        http_status=200,
        title="Empty Body Page",
        extraction_confidence=0.9,
    )

    put_raw_html = MagicMock(return_value="s3://raw/x")
    put_jsonld = MagicMock(return_value=None)
    pages_put = MagicMock(return_value=None)

    with (
        patch.object(headless_worker.RawHtmlStore, "put_raw_html", put_raw_html),
        patch.object(headless_worker.RawHtmlStore, "put_jsonld", put_jsonld),
        patch.object(headless_worker.PagesRepo, "put", pages_put),
    ):
        asyncio.run(headless_worker._persist_headless(result, ""))

    # The S3 raw-HTML write MUST have happened — the body, although empty,
    # is real content, not a gate-skip sentinel.
    assert put_raw_html.called, "put_raw_html should be called for empty-but-passing body"
    call_kwargs = put_raw_html.call_args.kwargs
    assert call_kwargs["html"] == ""
    assert call_kwargs["url_hash"] == "e" * 64

    # DDB row still written.
    assert pages_put.called


def test_fetch_headless_rejects_blocked_url_without_booting_chromium(monkeypatch):
    """The SSRF guard at the entry of `_fetch_headless` must short-circuit
    before any Playwright import or browser boot. We assert this by
    monkeypatching `socket.getaddrinfo` to flag 169.254.169.254 → loopback
    range, and confirming `UnsafeUrlError` raises without any
    `async_playwright` activity.

    This is the worker-side analogue of the API-layer Pydantic validation.
    A future code path that direct-invokes the headless lambda without
    routing through `/extract` would otherwise bypass the input check.
    """
    import socket
    import asyncio
    from crawler.fetcher.url_safety import UnsafeUrlError
    from crawler.workers import headless_worker

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "",
                 ("169.254.169.254", port or 0))]
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    # If the guard short-circuits properly, this never touches Playwright.
    # If it doesn't, the test fails with an ImportError or browser-launch
    # error rather than the expected UnsafeUrlError — also a clear signal.
    with pytest.raises(UnsafeUrlError):
        asyncio.run(headless_worker._fetch_headless("http://169.254.169.254/"))


def test_handler_persists_rejected_marker_for_captcha_body(aws_resources, monkeypatch):
    """When the headless fetch lands on a captcha-fingerprint page (e.g. the
    Cloudflare 'Just a Moment' interstitial), the persist gate must replace
    the result with a rejected marker, skip the S3 raw-HTML write, and still
    write the DDB row so the audit trail remains visible via /pages."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")

    async def fake_fetch(url: str):
        return (
            "<html><head><title>Just a Moment</title></head>"
            "<body>Please complete the CAPTCHA to continue.</body></html>",
            200,
        )

    from crawler.workers import headless_worker
    monkeypatch.setattr(headless_worker, "_fetch_headless", fake_fetch)

    result = headless_worker.handler(
        {"url": "http://blocked.example/", "persist": True},
        context=None,
    )

    # The returned (and persisted) result is the rejected marker.
    assert result["fetcher_used"] == "rejected"
    assert result["extraction_confidence"] == 0.0
    assert result["topics"] == []

    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    items = pages.scan()["Items"]
    assert len(items) == 1
    assert items[0]["fetcher_used"] == "rejected"

    # No raw HTML in S3.
    s3 = boto3.client("s3", region_name="us-east-1")
    assert s3.list_objects_v2(Bucket="raw").get("KeyCount", 0) == 0
