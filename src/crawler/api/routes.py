"""HTTP routes."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from urllib.parse import urlsplit

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query

from crawler.api.auth import require_api_key
from crawler.api.schemas import (
    BatchRequest,
    BatchResponse,
    ExtractRequest,
    ExtractResult,
    JobStatus,
)
from crawler.config import load_settings
from crawler.fetcher.headless import invoke_headless
from crawler.persist_gate import build_fetch_failed_result, reject_reason, to_rejected
from crawler.pipeline import extract_pipeline
from crawler.storage.dynamo import JobsRepo, PagesRepo
from crawler.storage.hashing import url_hash as _url_hash
from crawler.storage.s3 import RawHtmlStore

logger = logging.getLogger(__name__)

# Wall-clock budget for one /extract request, sized to fit comfortably under
# the API Lambda's 28s timeout (template.yaml). Leaving ~5s of headroom keeps
# us safe against Mangum/serialization overhead and lets us still attempt a
# headless fallback when the static fetch fails late.
_EXTRACT_BUDGET_SEC = 23.0
# Hard ceiling on the whole pipeline future — belt-and-braces against a
# downstream bug that ignores the deadline.
_EXTRACT_WAIT_FOR_SEC = 24.0
# Slack reserved for the headless-fallback path when static fails. If less
# than this much budget is left we skip headless and return the degraded
# response immediately.
_HEADLESS_FALLBACK_MIN_SEC = 8.0

router = APIRouter()


def _settings():
    return load_settings()


async def _persist(result: ExtractResult, html: str | None) -> ExtractResult:
    """Persist S3 raw HTML + S3 JSON-LD + DynamoDB row concurrently.

    boto3 is sync, so each write runs in a default-executor thread; the three
    are awaited together. DynamoDB write is intentionally sequenced AFTER the
    S3 writes complete so its row references the final S3 URIs.

    Runs the persist gate first: if the result fingerprints as a bot-block /
    rate-limit / captcha interstitial, it's swapped for a `rejected` marker
    and the S3 raw-HTML write is skipped (no useful content to store). The
    DDB row is still written so the audit trail remains visible via /pages.
    Returns the result that was actually persisted (may differ from input).
    """
    reason = reject_reason(result, html)
    if reason is not None:
        result = to_rejected(result, reason)
        html = None  # nothing useful to put in S3

    s = _settings()
    if not s.raw_html_bucket or not s.pages_table:
        return result  # local-dev fallback: skip persistence
    store = RawHtmlStore(bucket=s.raw_html_bucket)
    domain = urlsplit(result.url).netloc.lower()
    fetched_iso = result.fetched_at.isoformat()

    html_task = (
        asyncio.to_thread(
            store.put_raw_html,
            url_hash=result.url_hash, domain=domain,
            fetched_at_iso=fetched_iso, html=html or "",
        )
        if html
        else None
    )
    jsonld_task = (
        asyncio.to_thread(
            store.put_jsonld,
            url_hash=result.url_hash, domain=domain,
            fetched_at_iso=fetched_iso, jsonld=result.json_ld,
        )
        if result.json_ld
        else None
    )

    tasks = [t for t in (html_task, jsonld_task) if t is not None]
    s3_results = await asyncio.gather(*tasks) if tasks else []
    # Re-thread the results back into the right slots
    idx = 0
    s3_html_uri = None
    s3_jsonld_uri = None
    if html_task is not None:
        s3_html_uri = s3_results[idx]
        idx += 1
    if jsonld_task is not None:
        s3_jsonld_uri = s3_results[idx]

    await asyncio.to_thread(
        PagesRepo(table_name=s.pages_table).put,
        result, s3_html_uri=s3_html_uri, s3_jsonld_uri=s3_jsonld_uri,
    )
    return result


async def _try_headless_fallback(
    url: str,
    static_exc: BaseException,
    deadline: float,
) -> tuple[ExtractResult | None, BaseException | None]:
    """Invoke the headless Lambda as a last-resort rescue when the static
    fetcher failed (timeout, firewall block, DNS, etc.).

    Returns `(result, None)` on success, `(None, headless_exc)` if headless
    itself errored, and `(None, None)` when headless wasn't even attempted
    (unconfigured or budget-exhausted). The caller emits the degraded
    response in the latter two cases, carrying `headless_exc` into the
    response's `errors[]` so an operator can see BOTH legs of the fallback
    chain failed.

    Implementation note: `invoke_headless` is a synchronous boto3 call. We
    run it via `asyncio.to_thread` + `asyncio.wait_for(remaining)` so (a)
    the event loop isn't blocked while waiting on the Lambda response,
    and (b) a slow headless can't push us past Lambda's own 28s ceiling.
    """
    settings = _settings()
    if not settings.headless_function_name:
        return None, None
    remaining = deadline - time.monotonic()
    if remaining < _HEADLESS_FALLBACK_MIN_SEC:
        # Not enough wall-clock left for headless to even cold-start, never
        # mind Playwright navigation. Skip rather than guarantee a second
        # Lambda timeout.
        logger.warning(
            "skipping headless fallback after static failure %s: only %.1fs left",
            type(static_exc).__name__,
            remaining,
        )
        return None, None
    try:
        data = await asyncio.wait_for(
            asyncio.to_thread(invoke_headless, url, persist=False),
            timeout=remaining,
        )
        result = ExtractResult(**data)
        # Mark the rescue path so an operator can distinguish a low-confidence
        # escalation from a static-fetch-failure rescue.
        result.escalation = "succeeded"
        result.escalation_meta = {
            "reason": "static_fetch_failed",
            "static_error": type(static_exc).__name__,
            "headless_confidence": result.extraction_confidence,
            "headless_word_count": result.word_count,
        }
        return result, None
    except Exception as exc:  # noqa: BLE001 - caller will emit the degraded response
        logger.exception("headless fallback after static failure also failed")
        return None, exc


@router.post("/extract", response_model=ExtractResult, tags=["extract"],
             dependencies=[Depends(require_api_key)])
async def extract(
    req: ExtractRequest,
    fixture: int = Query(
        0, ge=0, le=1,
        description="If 1 and URL matches a known test URL, returns saved fixture response",
    ),
) -> ExtractResult:
    if fixture == 1:
        url_lc = req.url.lower()
        from crawler import fixtures
        if "amazon.com" in url_lc and "cuisinart" in url_lc:
            return fixtures.amazon_toaster()
        if "blog.rei.com" in url_lc and "indoorsy" in url_lc:
            return fixtures.rei_outdoors()
        if "cnn.com" in url_lc and "tech-jobs-ai" in url_lc:
            return fixtures.cnn_tech()
        # No match → fall through to live fetch

    settings = _settings()
    # Absolute deadline shared by the static fetcher and the headless
    # fallback. Computed in monotonic time so clock skew can't move it.
    deadline = time.monotonic() + _EXTRACT_BUDGET_SEC

    raw_html: str | None = None
    result: ExtractResult | None = None
    static_exc: BaseException | None = None

    try:
        returned = await asyncio.wait_for(
            extract_pipeline(req.url, return_html=True, deadline=deadline),
            timeout=_EXTRACT_WAIT_FOR_SEC,
        )
        if isinstance(returned, tuple):
            result, raw_html = returned
        else:
            result, raw_html = returned, None
    except TimeoutError as exc:
        # Belt-and-braces: the deadline should have fired first, but if a
        # downstream call ignores it we still bail out here before Lambda
        # kills the whole process. `asyncio.TimeoutError` is an alias for the
        # builtin `TimeoutError` since Python 3.11; ruff UP041.
        static_exc = exc
        logger.warning("static fetch wait_for ceiling hit for %s", req.url)
    except Exception as exc:  # noqa: BLE001 - any upstream fetch failure
        static_exc = exc
        logger.warning(
            "static fetch failed for %s: %s",
            req.url,
            type(exc).__name__,
        )

    # ─── Static-fetch failure path ──────────────────────────────────────────
    # Try headless as a rescue (different egress IP, real browser — often
    # bypasses CDN/firewall blocks that defeated the static fetch). Fall back
    # to a degraded 200-OK response with `escalation: "failed"` if headless
    # is unconfigured, over-budget, or also errors. The caller gets enough
    # diagnostic detail in `errors[]` to see exactly which legs failed.
    #
    # Deliberate: the degraded response path does NOT persist anything to
    # S3/DynamoDB — there's no useful HTML to store, and a row keyed only on
    # url_hash with `http_status=0` would just confuse later `/pages` reads.
    # A subsequent `/extract` on the same URL will retry from scratch.
    if static_exc is not None:
        rescue, headless_exc = await _try_headless_fallback(
            req.url, static_exc, deadline,
        )
        if rescue is not None:
            return rescue
        return build_fetch_failed_result(req.url, static_exc, headless_exc)

    # ─── Static fetch succeeded ─────────────────────────────────────────────
    # Existing low-confidence escalation gate. `result` is guaranteed non-None
    # here because `static_exc is None`, but narrow the type explicitly for
    # the type-checker and surface a clear runtime error if the invariant is
    # ever broken (instead of an opaque `assert` that disappears under -O).
    if result is None:
        raise RuntimeError(
            "internal invariant broken: static_exc is None but result is None",
        )
    if result.extraction_confidence < settings.confidence_threshold:
        if not settings.headless_function_name:
            result.escalation = "skipped"
        else:
            try:
                data = invoke_headless(req.url, persist=False)
                headless_result = ExtractResult(**data)
                headless_meta = {
                    "headless_confidence": headless_result.extraction_confidence,
                    "headless_word_count": headless_result.word_count,
                }
                if headless_result.extraction_confidence > result.extraction_confidence:
                    result = headless_result
                    result.escalation = "succeeded"
                    result.escalation_meta = headless_meta
                    raw_html = None  # headless wrote its own copy (or persist=False)
                else:
                    result.escalation = "no_improvement"
                    result.escalation_meta = headless_meta
            except ClientError as exc:
                # Lambda Invoke-level failure (throttled, function offline, IAM).
                # Surface only the error CODE (not the message body) — codes are
                # bounded enums; messages may include account IDs or ARNs.
                code = exc.response.get("Error", {}).get("Code", "")
                result.escalation = "failed"
                result.escalation_error = f"lambda:{code}" if code else "lambda:ClientError"
                logger.warning("headless escalation failed: lambda:%s", code or "ClientError")
            except Exception as exc:  # noqa: BLE001 - degrade gracefully to static result
                # Anything else (ValidationError from a malformed headless payload,
                # network timeout while reading the response, etc.). Return ONLY the
                # exception class name to the client; full traceback goes to logs.
                # Avoids leaking Pydantic schema details or internal paths into the
                # user-facing response body.
                result.escalation = "failed"
                result.escalation_error = type(exc).__name__
                logger.exception("headless escalation failed: %s", type(exc).__name__)

    try:
        if raw_html is not None:
            # _persist may swap `result` for a rejected marker if the persist
            # gate fires (bot-block, rate-limit, captcha). Reflect that in
            # the response so the client sees fetcher_used="rejected" rather
            # than bogus topics.
            result = await _persist(result, raw_html)
    except Exception:  # noqa: BLE001, S110 - persistence failure must not break the response
        pass
    return result


@router.get("/pages", response_model=ExtractResult, tags=["pages"],
            dependencies=[Depends(require_api_key)])
def pages_by_url(url: str = Query(..., description="URL to look up")) -> ExtractResult:
    s = _settings()
    result = PagesRepo(table_name=s.pages_table).get(url_hash=_url_hash(url))
    if not result:
        raise HTTPException(status_code=404, detail="not found")
    return result


@router.get("/pages/{url_hash}", response_model=ExtractResult, tags=["pages"],
            dependencies=[Depends(require_api_key)])
def pages_by_hash(url_hash: str) -> ExtractResult:
    s = _settings()
    result = PagesRepo(table_name=s.pages_table).get(url_hash=url_hash)
    if not result:
        raise HTTPException(status_code=404, detail="not found")
    return result


@router.post("/batch", response_model=BatchResponse, tags=["batch"],
             dependencies=[Depends(require_api_key)])
def batch(req: BatchRequest) -> BatchResponse:
    s = _settings()
    if not s.static_queue_url or not s.jobs_table:
        raise HTTPException(status_code=503, detail="batch path not configured")

    job_id = uuid.uuid4().hex
    JobsRepo(table_name=s.jobs_table).create(job_id=job_id, total=len(req.urls))

    sqs = boto3.client("sqs")
    # Batches of up to 10 per SQS SendMessageBatch call
    for chunk_start in range(0, len(req.urls), 10):
        chunk = req.urls[chunk_start : chunk_start + 10]
        entries = [
            {
                "Id": str(chunk_start + i),
                "MessageBody": json.dumps({"url": url, "job_id": job_id}),
            }
            for i, url in enumerate(chunk)
        ]
        sqs.send_message_batch(QueueUrl=s.static_queue_url, Entries=entries)

    return BatchResponse(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=JobStatus, tags=["batch"],
            dependencies=[Depends(require_api_key)])
def jobs_get(job_id: str) -> JobStatus:
    s = _settings()
    status = JobsRepo(table_name=s.jobs_table).get(job_id=job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return status
