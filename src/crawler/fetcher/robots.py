"""Minimal robots.txt fetcher + cache. Per-domain TTL ~24h.

For PoC we ship an in-process LRU; production uses DynamoDB-backed cache
(see design spec §7.4).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from protego import Protego

_CACHE: dict[str, tuple[Protego, float]] = {}
_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class RobotsDecision:
    allowed: bool
    crawl_delay: float | None


async def can_fetch(url: str, user_agent: str) -> RobotsDecision:
    """Return whether `url` is permitted, and any crawl-delay (seconds)."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    robots_url = f"{origin}/robots.txt"

    now = time.time()
    cached = _CACHE.get(origin)
    if cached is None or now - cached[1] > _TTL_SECONDS:
        parser = await _load(robots_url)
        _CACHE[origin] = (parser, now)
    else:
        parser = cached[0]

    return RobotsDecision(
        allowed=parser.can_fetch(url, user_agent),
        crawl_delay=parser.crawl_delay(user_agent),
    )


async def _load(robots_url: str) -> Protego:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.get(robots_url, follow_redirects=True)
            if response.status_code == 200:
                return Protego.parse(response.text)
    except httpx.HTTPError:
        pass
    # No robots.txt or unreachable → permissive
    return Protego.parse("")
