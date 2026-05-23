"""Static HTTP fetcher with retries, realistic headers, redirect following."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from crawler.fetcher.user_agents import pick as pick_ua

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
DEFAULT_MAX_BYTES = 5_000_000  # 5 MB hard cap on HTML body
DEFAULT_RETRIES = 2


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
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
    }


async def fetch(
    url: str,
    *,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    retries: int = DEFAULT_RETRIES,
) -> FetchResult:
    """Fetch a URL with retries, returning headers + body. Raises on terminal failure."""
    last_exc: Exception | None = None
    transport = httpx.AsyncHTTPTransport(retries=0)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers=_headers(),
        transport=transport,
        http2=False,
    ) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.get(url)
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
                if attempt == retries:
                    raise
    raise RuntimeError(f"Unreachable: last_exc={last_exc!r}")
