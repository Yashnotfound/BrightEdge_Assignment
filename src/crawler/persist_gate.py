"""Persist gate — last-chance filter before DDB/S3 writes.

A live 1000-URL batch run showed 26.4% of persisted rows were "garbage":
Cloudflare 'Access Denied' bodies, 429 rate-limit interstitials, and the
'Just A Moment' challenge page — all of which parsed cleanly into invented
"topics" like ["captcha", "access denied"] that polluted `/pages/by-topic`
queries. This module is the boundary check that catches those.

The two helpers here are pure: no I/O, no global state. They run inside
both worker paths (`workers.static_worker`, `workers.headless_worker`) and
the sync `/extract` route (`api.routes._persist`). Each caller is
responsible for skipping the S3 raw-HTML write and (for the workers)
bumping `failed` instead of `succeeded` on the JobsRepo when the gate
fires.
"""
from __future__ import annotations

from crawler.api.schemas import ExtractResult
from crawler.fetcher.confidence import is_likely_captcha

# HTTP status codes that signal an operational failure (5xx) or a client-side
# block (selected 4xx). 404 is deliberately omitted — an honest "page doesn't
# exist" is fine to persist with a low confidence score.
_UPSTREAM_ERROR_CODES = frozenset({500, 502, 503, 504})
_UPSTREAM_BLOCK_CODES = frozenset({401, 402, 403, 429, 451})
# Above this confidence floor, a 4xx block code is overridden — sites like
# Medium and Reddit sometimes return 403 alongside real article HTML, and we
# don't want the gate to throw out real content.
_BLOCK_CONFIDENCE_FLOOR = 0.5


def reject_reason(result: ExtractResult, html: str | None) -> str | None:
    """Decide whether `result` should be persisted as a `rejected` marker
    row instead of normal content.

    Returns a short reason string (`"upstream_error"`, `"upstream_blocked"`,
    `"captcha"`, or `"interstitial"`) when the result should be rejected, or
    `None` when it passes and should be persisted normally.

    Rules apply in priority order; the first match wins:

    1. `fetcher_used == "none"` — already a degraded fetch_failed result that
       the caller is handling separately. Don't double-reject.
    2. 5xx → `"upstream_error"`. Operational failure; no point storing the
       (possibly empty) error body as if it were a page.
    3. 4xx block codes ({401, 402, 403, 429, 451}) → `"upstream_blocked"`,
       UNLESS the extractor is at least `_BLOCK_CONFIDENCE_FLOOR` confident
       — in which case Medium/Reddit-style "403 + real body" content gets
       through.
    4. Captcha fingerprint in body → `"captcha"`. Catches Cloudflare's
       'Access Denied' challenge and similar bot walls on any status code.
    """
    if result.fetcher_used == "none":
        return None

    if result.http_status in _UPSTREAM_ERROR_CODES:
        return "upstream_error"

    if result.http_status in _UPSTREAM_BLOCK_CODES:
        if result.extraction_confidence >= _BLOCK_CONFIDENCE_FLOOR:
            return None
        return "upstream_blocked"

    if html and is_likely_captcha(result.title, html):
        return "captcha"

    return None


def to_rejected(result: ExtractResult, reason: str) -> ExtractResult:
    """Return a new `ExtractResult` that carries the rejection marker.

    Identity / audit fields (`url`, `url_hash`, `fetched_at`, `http_status`)
    and meta-tag-derived fields (`title`, `description`, `language`,
    `open_graph`, `twitter_card`, `canonical_url`) are preserved verbatim —
    those are often informative even on blocked pages (e.g. a 403 page still
    returns the site's real `<title>`).

    Topic-shaped garbage (`topics`, `extraction_confidence`, `body_text`,
    `word_count`, `json_ld`) is blanked so the row can't pollute future
    `/pages/by-topic` queries. The reason is prepended to `errors[]` as
    `persistence_rejected:<reason>` so analysts can filter DDB rows on it.
    """
    return result.model_copy(
        update={
            "fetcher_used": "rejected",
            "extraction_confidence": 0.0,
            "topics": [],
            "body_text": None,
            "word_count": 0,
            "json_ld": [],
            "errors": [f"persistence_rejected:{reason}", *result.errors],
        }
    )
