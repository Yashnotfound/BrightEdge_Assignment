"""End-to-end extract pipeline: fetch → extract → classify → result."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Iterator

from bs4 import BeautifulSoup

from crawler.api.schemas import ExtractResult, Topic
from crawler.classifier.fuse import fuse_topics
from crawler.classifier.heuristics import candidates_from_meta_and_jsonld
from crawler.classifier.keyphrases import extract_keyphrases
from crawler.extractor.body import extract_body
from crawler.extractor.jsonld import extract_jsonld
from crawler.extractor.language import detect_language
from crawler.extractor.meta import extract_meta
from crawler.fetcher.confidence import is_likely_captcha, score_confidence
from crawler.fetcher.static import fetch
from crawler.storage.hashing import url_hash

_BODY_TEXT_LIMIT = 50_000  # cap stored body to 50KB

logger = logging.getLogger(__name__)


@contextmanager
def _stage(name: str, timings: dict[str, float]) -> Iterator[None]:
    """Record wall-clock duration of a pipeline stage into `timings` (ms)."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = round((time.perf_counter() - t0) * 1000, 3)


async def extract_pipeline(url: str, *, return_html: bool = False):
    """Return ExtractResult by default; (ExtractResult, raw_html) if return_html=True."""
    fetched = await fetch(url)
    result = _process(
        url=url, html=fetched.html, http_status=fetched.http_status,
        content_type=fetched.content_type, fetcher_used="static",
    )
    if return_html:
        return result, fetched.html
    return result


def process_html(*, url: str, html: str, http_status: int, content_type: str,
                 fetcher_used: str) -> ExtractResult:
    """Public hook for callers that supply HTML (e.g., headless worker)."""
    return _process(url=url, html=html, http_status=http_status,
                    content_type=content_type, fetcher_used=fetcher_used)


def process_html_timed(
    *, url: str, html: str, http_status: int, content_type: str, fetcher_used: str,
) -> tuple[ExtractResult, dict[str, float]]:
    """Like `process_html`, but also returns per-stage wall-clock timings in ms.

    For benchmarking and observability tooling. Production callers should use
    `process_html` — the timings are also emitted via `logger.info("pipeline.timing", ...)`.
    """
    return _process_with_timings(
        url=url, html=html, http_status=http_status,
        content_type=content_type, fetcher_used=fetcher_used,
    )


def _process(*, url: str, html: str, http_status: int, content_type: str,
             fetcher_used: str) -> ExtractResult:
    result, _ = _process_with_timings(
        url=url, html=html, http_status=http_status,
        content_type=content_type, fetcher_used=fetcher_used,
    )
    return result


def _process_with_timings(
    *, url: str, html: str, http_status: int, content_type: str, fetcher_used: str,
) -> tuple[ExtractResult, dict[str, float]]:
    timings: dict[str, float] = {}

    with _stage("soup", timings):
        soup = BeautifulSoup(html, "lxml")
    with _stage("meta", timings):
        meta = extract_meta(html, soup=soup)
    with _stage("jsonld", timings):
        jsonld = extract_jsonld(html, soup=soup)
    with _stage("body", timings):
        body = extract_body(html)
    word_count = len(body.split()) if body else 0
    body_truncated = body[:_BODY_TEXT_LIMIT] if body else None
    with _stage("language", timings):
        language = detect_language(body)

    captcha = is_likely_captcha(meta.title, body)
    confidence = score_confidence(
        title=meta.title,
        body_word_count=word_count,
        has_structured_data=bool(jsonld),
        is_captcha=captcha,
    )

    with _stage("heuristics", timings):
        heuristic_cands = candidates_from_meta_and_jsonld(meta, jsonld)
    with _stage("keyphrases", timings):
        keyphrase_cands = extract_keyphrases(body, language=language)
    with _stage("fuse", timings):
        fused = fuse_topics(heuristic_cands, keyphrase_cands, top_k=10)
    topics = [Topic(label=t.label, score=t.score, sources=t.sources) for t in fused]

    errors = ["captcha_detected"] if captcha else []

    result = ExtractResult(
        url=url,
        url_hash=url_hash(url),
        fetched_at=datetime.now(UTC),
        fetcher_used=fetcher_used,
        http_status=http_status,
        content_type=content_type,
        language=language,
        title=meta.title,
        description=meta.description,
        canonical_url=meta.canonical_url,
        open_graph=meta.open_graph,
        twitter_card=meta.twitter_card,
        json_ld=jsonld,
        body_text=body_truncated,
        word_count=word_count,
        topics=topics,
        extraction_confidence=confidence,
        errors=errors,
    )

    timings["total"] = round(sum(timings.values()), 3)
    logger.info(
        "pipeline.timing",
        extra={"timings_ms": timings, "url": url, "language": language},
    )
    return result, timings
