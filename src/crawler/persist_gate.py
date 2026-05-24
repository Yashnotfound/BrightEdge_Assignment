"""Persist gate — last-chance filter before DDB/S3 writes.

A live 1000-URL batch run showed 26.4% of persisted rows were "garbage":
Cloudflare 'Access Denied' bodies, 429 rate-limit interstitials, and the
'Just A Moment' challenge page — all of which parsed cleanly into invented
"topics" like ["captcha", "access denied"] that polluted `/pages/by-topic`
queries. This module is the boundary check that catches those.

The helpers here perform no AWS I/O — they shape `ExtractResult` values
for callers that own the actual DDB/S3 writes. `build_fetch_failed_result`
does read the wall clock via `datetime.now(UTC)`; the gate decision
helpers (`reject_reason`, `to_rejected`) are fully pure. They run inside
both worker paths (`workers.static_worker`, `workers.headless_worker`)
and the sync `/extract` route (`api.routes._persist`). Each caller is
responsible for skipping the S3 raw-HTML write and (for the workers)
bumping `failed` instead of `succeeded` on the JobsRepo when the gate
fires.

`build_fetch_failed_result` is the sibling shape used when no fetcher
returned anything at all (ConnectError, DNS failure, ReadTimeout, firewall
block). It produces a degraded `ExtractResult` with `fetcher_used="none"`,
`http_status=0`, `escalation="failed"`. The sync `/extract` route returns
it to the client (without persisting), while the static SQS worker
persists it as a marker row so SQS at-least-once redelivery doesn't
hammer the same unreachable URL.
"""
from __future__ import annotations

from datetime import UTC, datetime

from crawler.api.schemas import ExtractResult
from crawler.fetcher.confidence import is_likely_captcha
from crawler.storage.hashing import url_hash as _url_hash

# HTTP status codes that signal an operational failure (5xx) or a client-side
# block (selected 4xx). 404 is deliberately omitted — an honest "page doesn't
# exist" is fine to persist with a low confidence score.
_UPSTREAM_ERROR_CODES = frozenset({500, 502, 503, 504})
# Block codes whose body is sometimes still real content (Medium/Reddit 403,
# 451 with a stub landing page, etc.). High confidence on these overrides the
# rejection.
_UPSTREAM_BLOCK_CODES = frozenset({401, 402, 403, 451})
# Rate-limit responses are categorically different: the body is almost always
# empty or a stub, so the extractor's confidence on it is unreliable. These
# always reject regardless of confidence.
_UPSTREAM_RATE_LIMIT_CODES = frozenset({429})
# Above this confidence floor, a 4xx block code is overridden — sites like
# Medium and Reddit sometimes return 403 alongside real article HTML, and we
# don't want the gate to throw out real content.
_BLOCK_CONFIDENCE_FLOOR = 0.5


def reject_reason(result: ExtractResult, html: str | None) -> str | None:
    """Decide whether `result` should be persisted as a `rejected` marker
    row instead of normal content.

    Returns one of `"upstream_error"`, `"upstream_rate_limited"`,
    `"upstream_blocked"`, or `"captcha"` when the result should be rejected,
    or `None` when it passes and should be persisted normally.

    Rules apply in priority order; the first match wins:

    1. `fetcher_used == "none"` — already a degraded fetch_failed result that
       the caller is handling separately. Don't double-reject.
    2. 5xx → `"upstream_error"`. Operational failure; no point storing the
       (possibly empty) error body as if it were a page.
    3. 429 → `"upstream_rate_limited"`. Always rejects, regardless of
       confidence — rate-limit bodies are stubs and the confidence score on
       them is not trustworthy.
    4. 4xx block codes ({401, 402, 403, 451}) → `"upstream_blocked"`,
       UNLESS the extractor is at least `_BLOCK_CONFIDENCE_FLOOR` confident
       — in which case Medium/Reddit-style "403 + real body" content gets
       through.
    5. Captcha fingerprint in body → `"captcha"`. Catches Cloudflare's
       'Access Denied' challenge and similar bot walls on any status code.
    """
    if result.fetcher_used == "none":
        return None

    if result.http_status in _UPSTREAM_ERROR_CODES:
        return "upstream_error"

    if result.http_status in _UPSTREAM_RATE_LIMIT_CODES:
        return "upstream_rate_limited"

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


def build_fetch_failed_result(
    url: str,
    static_exc: BaseException,
    headless_exc: BaseException | None = None,
) -> ExtractResult:
    """Construct an ExtractResult representing a fully-failed fetch.

    `fetcher_used="none"`, `http_status=0`, `extraction_confidence=0.0`,
    `escalation="failed"`, with the exception class names captured in
    `errors[]` and `escalation_error`. Used by both the sync `/extract`
    route (returned to the client, NOT persisted) and the static SQS
    worker (persisted as a fetch-failed marker so SQS redelivery stops).
    """
    errors = [f"static_fetch_failed:{type(static_exc).__name__}"]
    if headless_exc is not None:
        errors.append(f"headless_fetch_failed:{type(headless_exc).__name__}")
    return ExtractResult(
        url=url,
        url_hash=_url_hash(url),
        fetched_at=datetime.now(UTC),
        fetcher_used="none",
        http_status=0,
        extraction_confidence=0.0,
        errors=errors,
        escalation="failed",
        escalation_error=f"fetch_failed:{type(static_exc).__name__}",
    )
