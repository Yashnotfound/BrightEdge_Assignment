"""Tests for the persist gate (reject_reason / to_rejected).

The gate is a pure function that decides — at the boundary right before
S3/DDB writes — whether an `ExtractResult` represents real content or a
bot-block / rate-limit / interstitial that should be persisted as a
`rejected` marker row instead. Live 1000-URL run showed 26.4% of rows were
garbage that polluted topic queries; this module is the fix.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from crawler.api.schemas import ExtractResult, Topic
from crawler.persist_gate import build_fetch_failed_result, reject_reason, to_rejected
from crawler.storage.hashing import url_hash as _url_hash


def _result(
    *,
    http_status: int = 200,
    fetcher_used: str = "static",
    extraction_confidence: float = 0.0,
    title: str | None = None,
    errors: list[str] | None = None,
) -> ExtractResult:
    return ExtractResult(
        url="https://example.com/x",
        url_hash="a" * 64,
        fetched_at=datetime(2026, 5, 24, tzinfo=UTC),
        fetcher_used=fetcher_used,
        http_status=http_status,
        extraction_confidence=extraction_confidence,
        title=title,
        errors=errors or [],
    )


# ---------------------------------------------------------------------------
# reject_reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_reject_reason_5xx_returns_upstream_error(status: int) -> None:
    """Any 5xx is an operational failure — persist as marker."""
    result = _result(http_status=status, extraction_confidence=0.8)
    assert reject_reason(result, "<html>oops</html>") == "upstream_error"


def test_reject_reason_403_high_confidence_passes() -> None:
    """High-confidence content (Medium/Reddit sometimes 403 with real body)
    must survive the gate — the confidence floor is the safety net."""
    result = _result(http_status=403, extraction_confidence=0.6)
    assert reject_reason(result, "<html>real article</html>") is None


def test_reject_reason_403_low_confidence_blocked() -> None:
    """A 403 with low confidence is a real Cloudflare/CDN block."""
    result = _result(http_status=403, extraction_confidence=0.3)
    assert reject_reason(result, "<html>access denied</html>") == "upstream_blocked"


@pytest.mark.parametrize("status", [401, 402, 403, 451])
def test_reject_reason_4xx_block_codes_low_confidence(status: int) -> None:
    result = _result(http_status=status, extraction_confidence=0.1)
    assert reject_reason(result, None) == "upstream_blocked"


@pytest.mark.parametrize("confidence", [0.0, 0.1, 0.5, 0.95, 1.0])
def test_reject_reason_429_always_blocked_regardless_of_confidence(
    confidence: float,
) -> None:
    """Rate limits don't carry real content — confidence shouldn't save them.

    Unlike the other 4xx block codes (401/402/403/451), a 429 response body
    is usually empty or a stub interstitial; the extractor's confidence
    score on that body is unreliable. So 429 ALWAYS rejects, regardless of
    confidence — the rate-limit reason short-circuits before the confidence
    check.
    """
    result = _result(http_status=429, extraction_confidence=confidence)
    assert reject_reason(result, None) == "upstream_rate_limited"


@pytest.mark.parametrize(
    ("status", "confidence", "expected"),
    [
        # Just below the floor: still rejected.
        (401, 0.49, "upstream_blocked"),
        (402, 0.49, "upstream_blocked"),
        (403, 0.49, "upstream_blocked"),
        (451, 0.49, "upstream_blocked"),
        # Exactly at the floor: passes (rule uses `>=`).
        (401, 0.50, None),
        (402, 0.50, None),
        (403, 0.50, None),
        (451, 0.50, None),
        # Just above the floor: passes.
        (401, 0.51, None),
        (402, 0.51, None),
        (403, 0.51, None),
        (451, 0.51, None),
    ],
)
def test_reject_reason_block_code_confidence_floor_boundary(
    status: int, confidence: float, expected: str | None
) -> None:
    """Boundary-value coverage at the 0.5 confidence floor for non-429 4xx
    block codes. Confidence exactly at the floor passes (inclusive); strictly
    below the floor rejects."""
    result = _result(http_status=status, extraction_confidence=confidence)
    assert reject_reason(result, None) == expected


def test_reject_reason_404_passes() -> None:
    """404 is an honest miss — persist normally."""
    result = _result(http_status=404, extraction_confidence=0.1)
    assert reject_reason(result, None) is None


def test_reject_reason_captcha_body_with_200() -> None:
    """A 200 page whose body fingerprints as a captcha is bot-blocked."""
    result = _result(http_status=200, extraction_confidence=0.2, title="Are you a robot?")
    html = "<html><body>Please complete the CAPTCHA to continue</body></html>"
    assert reject_reason(result, html) == "captcha"


def test_reject_reason_fetcher_used_none_passes() -> None:
    """Degraded fetch_failed results were already handled upstream — don't
    double-reject them. They're routed through the route's degraded path."""
    result = _result(
        fetcher_used="none",
        http_status=0,
        extraction_confidence=0.0,
        errors=["static_fetch_failed:TimeoutError"],
    )
    assert reject_reason(result, None) is None


def test_reject_reason_clean_200_passes() -> None:
    """A normal 200 with real content gets through."""
    result = _result(http_status=200, extraction_confidence=0.8, title="Real Article")
    html = "<html><body>" + ("article words " * 100) + "</body></html>"
    assert reject_reason(result, html) is None


def test_reject_reason_priority_5xx_beats_captcha() -> None:
    """If status is 5xx, upstream_error wins over captcha — operational
    failure is the more accurate label."""
    result = _result(http_status=503, extraction_confidence=0.1, title="captcha")
    html = "<html>captcha</html>"
    assert reject_reason(result, html) == "upstream_error"


def test_reject_reason_no_html_no_captcha_check() -> None:
    """No HTML → can't run captcha fingerprint; falls through to None on a 200."""
    result = _result(http_status=200, extraction_confidence=0.7)
    assert reject_reason(result, None) is None


# ---------------------------------------------------------------------------
# to_rejected
# ---------------------------------------------------------------------------


def test_to_rejected_preserves_identity_fields() -> None:
    """url, url_hash, fetched_at, http_status are preserved verbatim."""
    fetched = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)
    result = ExtractResult(
        url="https://blocked.example/x",
        url_hash="d" * 64,
        fetched_at=fetched,
        fetcher_used="static",
        http_status=403,
        title="Access Denied",
        description="Cloudflare block",
        canonical_url="https://blocked.example/x",
        open_graph={"og:title": "Access Denied"},
        twitter_card={"twitter:card": "summary"},
        extraction_confidence=0.4,
        topics=[Topic(label="access denied", score=0.9, sources=["meta:keywords"])],
        body_text="some text",
        word_count=42,
        json_ld=[{"@type": "Article"}],
        errors=["prior_error"],
        escalation="failed",
    )
    rejected = to_rejected(result, "upstream_blocked")

    assert rejected.url == "https://blocked.example/x"
    assert rejected.url_hash == "d" * 64
    assert rejected.fetched_at == fetched
    assert rejected.http_status == 403
    # Meta-tag-derived fields kept (often informative even for blocked pages).
    assert rejected.title == "Access Denied"
    assert rejected.description == "Cloudflare block"
    assert rejected.canonical_url == "https://blocked.example/x"
    assert rejected.open_graph == {"og:title": "Access Denied"}
    assert rejected.twitter_card == {"twitter:card": "summary"}


def test_to_rejected_zeroes_garbage_fields() -> None:
    """topics, confidence, body_text, word_count, json_ld must be blanked."""
    result = ExtractResult(
        url="https://blocked.example/x",
        url_hash="e" * 64,
        fetched_at=datetime(2026, 5, 24, tzinfo=UTC),
        fetcher_used="static",
        http_status=403,
        extraction_confidence=0.4,
        topics=[Topic(label="captcha", score=0.9, sources=["body"])],
        body_text="garbage body",
        word_count=10,
        json_ld=[{"@type": "Article"}],
    )
    rejected = to_rejected(result, "upstream_blocked")

    assert rejected.fetcher_used == "rejected"
    assert rejected.extraction_confidence == 0.0
    assert rejected.topics == []
    assert rejected.body_text is None
    assert rejected.word_count == 0
    assert rejected.json_ld == []


def test_to_rejected_prepends_reason_marker_to_errors() -> None:
    """errors[] gains a `persistence_rejected:<reason>` entry at the front,
    so analysts can grep DDB rows by reason without parsing the rest."""
    result = ExtractResult(
        url="https://blocked.example/x",
        url_hash="f" * 64,
        fetched_at=datetime(2026, 5, 24, tzinfo=UTC),
        fetcher_used="static",
        http_status=403,
        errors=["prior_error_a", "prior_error_b"],
    )
    rejected = to_rejected(result, "upstream_blocked")
    assert rejected.errors[0] == "persistence_rejected:upstream_blocked"
    assert "prior_error_a" in rejected.errors
    assert "prior_error_b" in rejected.errors


def test_to_rejected_preserves_escalation() -> None:
    """The escalation chain documents what was tried before — don't lose it."""
    result = ExtractResult(
        url="https://blocked.example/x",
        url_hash="0" * 64,
        fetched_at=datetime(2026, 5, 24, tzinfo=UTC),
        fetcher_used="headless",
        http_status=403,
        escalation="succeeded",
        escalation_meta={"reason": "static_fetch_failed"},
    )
    rejected = to_rejected(result, "upstream_blocked")
    assert rejected.escalation == "succeeded"
    assert rejected.escalation_meta == {"reason": "static_fetch_failed"}


# ---------------------------------------------------------------------------
# build_fetch_failed_result
# ---------------------------------------------------------------------------


class _FakeConnectError(Exception):
    """Stand-in for httpx.ConnectError so the test doesn't pull httpx in."""


def test_build_fetch_failed_result_shape() -> None:
    """Static-only failure: degraded ExtractResult with `fetcher_used="none"`,
    `http_status=0`, `escalation="failed"`, exception class captured in
    `errors[]` and `escalation_error`, deterministic `url_hash`."""
    url = "https://unreachable.example/page"
    exc = _FakeConnectError("dns failure")
    result = build_fetch_failed_result(url, exc)

    assert result.url == url
    assert result.url_hash == _url_hash(url)
    assert result.fetcher_used == "none"
    assert result.http_status == 0
    assert result.extraction_confidence == 0.0
    assert result.escalation == "failed"
    assert result.errors == ["static_fetch_failed:_FakeConnectError"]
    assert result.escalation_error == "fetch_failed:_FakeConnectError"


def test_build_fetch_failed_result_with_headless_exc() -> None:
    """Both legs failed: errors[] carries both class names so an operator
    can see the static and headless leg each tripped."""
    url = "https://unreachable.example/page"
    static_exc = _FakeConnectError("dns failure")

    class _FakeReadTimeout(Exception):
        pass

    headless_exc = _FakeReadTimeout("playwright timeout")
    result = build_fetch_failed_result(url, static_exc, headless_exc)

    assert "static_fetch_failed:_FakeConnectError" in result.errors
    assert "headless_fetch_failed:_FakeReadTimeout" in result.errors
    # escalation_error tracks the static-leg failure class so the row's
    # primary failure marker stays consistent with the static-only case.
    assert result.escalation_error == "fetch_failed:_FakeConnectError"
