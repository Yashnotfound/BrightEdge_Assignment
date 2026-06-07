"""Static HTTP fetcher with retries, realistic headers, redirect following.

Redirects are followed manually (not via httpx's `follow_redirects=True`) so
that we can re-validate each hop against the SSRF guard in
`crawler.fetcher.url_safety`. Without per-hop validation, an attacker could
submit a benign-looking URL that 302s to e.g. 169.254.169.254 and the input
check would never see the dangerous final target.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from crawler.fetcher.url_safety import UnsafeUrlError, validate_url
from crawler.fetcher.user_agents import pick as pick_ua

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
DEFAULT_MAX_BYTES = 5_000_000  # 5 MB hard cap on HTML body
DEFAULT_RETRIES = 2

# Minimum slack to even attempt a request when a deadline is provided. If the
# remaining budget is below this, we bail out immediately rather than start a
# request that has no realistic chance of completing.
_MIN_REQUEST_BUDGET_SEC = 0.5

# Cap on manual redirect chain length. Matches what httpx's built-in follower
# would have allowed (default 20) — but a long chain is suspicious and 5 is
# enough for legitimate redirect patterns (canonicalisation, http→https,
# mobile↔desktop, AMP↔canonical).
MAX_REDIRECTS = 5


class FetchTimeoutError(httpx.TimeoutException):
    """Raised when the static fetcher's deadline budget is exhausted.

    Subclasses `httpx.TimeoutException` so callers that already catch
    `httpx.HTTPError` keep catching it; new callers can distinguish a
    deadline-exhausted budget from a network-level timeout.
    """


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    http_status: int
    content_type: str
    html: str
    fetched_via: str = "static"


def _headers() -> dict[str, str]:
    return {
        "User-Agent": pick_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }


def _cap_timeout(timeout: httpx.Timeout, remaining: float) -> httpx.Timeout:
    """Return a Timeout whose phase budgets are each clamped to `remaining`.

    httpx's own Timeout dataclass exposes `connect`, `read`, `write`, `pool`
    (each `float | None`). When a deadline is in play we shrink each phase so
    a single hung phase can't overrun the caller's wall-clock budget.
    """
    def _cap(t: float | None) -> float:
        # None means "no per-phase limit"; substitute the remaining budget so
        # we still bound this phase.
        return remaining if t is None else min(t, remaining)
    return httpx.Timeout(
        connect=_cap(timeout.connect),
        read=_cap(timeout.read),
        write=_cap(timeout.write),
        pool=_cap(timeout.pool),
    )


async def fetch(
    url: str,
    *,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    retries: int = DEFAULT_RETRIES,
    deadline: float | None = None,
) -> FetchResult:
    """Fetch a URL with retries, returning headers + body. Raises on terminal failure.

    When `deadline` is provided (a `time.monotonic()`-relative absolute
    timestamp), each attempt's per-phase httpx timeouts are clamped to the
    remaining budget, and retries stop as soon as the budget would not
    accommodate at least one more attempt. This is the contract the route
    handler relies on to guarantee the fetcher returns *before* AWS Lambda
    kills the process — without it the user sees a generic API Gateway
    500 with no body.
    """
    # SSRF guard at the fetcher entry point — defense in depth. The API
    # layer (Pydantic field validator) should have caught unsafe URLs
    # before they reach here, but a future code path enqueuing directly
    # to a worker would bypass that. UnsafeUrlError is a ValueError, not
    # an HTTPError, so it propagates up rather than being swallowed by
    # the retry loop's broad except clause.
    validate_url(url)

    last_exc: Exception | None = None
    transport = httpx.AsyncHTTPTransport(retries=0)
    async with httpx.AsyncClient(
        # Manual redirect following — see `_fetch_with_redirects`. The
        # reason this can't be `follow_redirects=True` is that httpx's
        # built-in follower has no SSRF re-validation hook: an attacker
        # URL that 302s to 169.254.169.254 would be followed silently.
        follow_redirects=False,
        timeout=timeout,
        headers=_headers(),
        transport=transport,
        http2=False,
    ) as client:
        for attempt in range(retries + 1):
            effective_timeout = timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= _MIN_REQUEST_BUDGET_SEC:
                    raise FetchTimeoutError(
                        f"deadline exhausted before attempt {attempt + 1}: "
                        f"remaining={remaining:.2f}s",
                    )
                effective_timeout = _cap_timeout(timeout, remaining)
            try:
                response = await _fetch_with_redirects(
                    client, url, timeout=effective_timeout,
                )
                content = response.content[:max_bytes]
                text = content.decode(
                    response.charset_encoding or "utf-8", errors="replace"
                )
                return FetchResult(
                    url=url,
                    final_url=str(response.url),
                    http_status=response.status_code,
                    content_type=response.headers.get("content-type", "").split(";")[0].strip(),
                    html=text,
                )
            except (httpx.RequestError, httpx.HTTPError) as exc:
                last_exc = exc
                # If the deadline is exhausted, prefer FetchTimeoutError over
                # the raw network error — this is true on the last retry too,
                # which is why we check BEFORE the `if attempt == retries:
                # raise` short-circuit. Without this ordering, the very last
                # attempt of a deadline-exhausted run would surface the
                # underlying ConnectError/ReadTimeout instead of the
                # diagnostic FetchTimeoutError the route handler relies on.
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= _MIN_REQUEST_BUDGET_SEC:
                        raise FetchTimeoutError(
                            f"deadline exhausted after attempt {attempt + 1}: "
                            f"{type(exc).__name__}",
                        ) from exc
                if attempt == retries:
                    raise
    raise RuntimeError(f"Unreachable: last_exc={last_exc!r}")


async def _fetch_with_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: httpx.Timeout,
) -> httpx.Response:
    """GET ``url`` and follow up to ``MAX_REDIRECTS`` redirects manually.

    Each hop's destination is re-validated against the SSRF guard before
    the next request is issued. This defeats the redirect-bypass attack
    that would otherwise sneak past input-only validation.

    Returns the first non-3xx response (or a 3xx with no Location header,
    which is broken upstream but not our problem to retry). Raises
    ``UnsafeUrlError`` if any redirect target is blocked, or
    ``httpx.TooManyRedirects`` if the chain exceeds ``MAX_REDIRECTS``.
    """
    current = url
    # +1 because iteration 0 fetches the original URL; iterations 1..N
    # follow up to MAX_REDIRECTS subsequent Location headers.
    for _hop in range(MAX_REDIRECTS + 1):
        response = await client.get(current, timeout=timeout)
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        if not location:
            # 3xx with no Location is broken upstream. Return the response
            # as-is rather than infinite-looping — the caller will treat
            # it like any other non-2xx outcome.
            return response
        next_url = urljoin(str(response.url), location)
        # Re-validate before the next hop. UnsafeUrlError propagates up
        # to fetch() and then to the route handler, which already knows
        # how to render a failed fetch as a degraded response.
        validate_url(next_url)
        current = next_url
    raise httpx.TooManyRedirects(
        f"exceeded {MAX_REDIRECTS} redirects starting from {url!r}",
    )
