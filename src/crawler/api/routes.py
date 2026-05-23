"""HTTP routes."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from crawler.api.schemas import (
    BatchRequest,
    BatchResponse,
    ExtractRequest,
    ExtractResult,
    JobStatus,
)
from crawler.config import load_settings
from crawler.fetcher.headless import invoke_headless
from crawler.pipeline import extract_pipeline
from crawler.storage.hashing import url_hash as _url_hash

logger = logging.getLogger(__name__)
router = APIRouter()


def _settings():
    return load_settings()


# ---------------------------------------------------------------------------
# Storage helpers — pick local or AWS implementations based on config
# ---------------------------------------------------------------------------

def _get_html_store():
    s = _settings()
    if s.is_local:
        from crawler.storage.local_fs import LocalHtmlStore
        return LocalHtmlStore(base_dir=s.local_data_path)
    from crawler.storage.s3 import RawHtmlStore
    return RawHtmlStore(bucket=s.raw_html_bucket)


def _get_pages_repo():
    s = _settings()
    if s.is_local:
        from crawler.storage.local_db import LocalPagesRepo
        return LocalPagesRepo(db_path=s.local_data_path / "pages.json")
    from crawler.storage.dynamo import PagesRepo
    return PagesRepo(table_name=s.pages_table)


def _get_jobs_repo():
    s = _settings()
    if s.is_local:
        from crawler.storage.local_db import LocalJobsRepo
        return LocalJobsRepo(db_path=s.local_data_path / "jobs.json")
    from crawler.storage.dynamo import JobsRepo
    return JobsRepo(table_name=s.jobs_table)


# ---------------------------------------------------------------------------
# Persistence helper
# ---------------------------------------------------------------------------

def _persist(result: ExtractResult, html: str | None) -> None:
    s = _settings()
    store = _get_html_store()
    domain = urlsplit(result.url).netloc.lower()
    fetched_iso = result.fetched_at.isoformat()
    s3_html_uri = store.put_raw_html(
        url_hash=result.url_hash, domain=domain,
        fetched_at_iso=fetched_iso, html=html or "",
    ) if html else None
    s3_jsonld_uri = store.put_jsonld(
        url_hash=result.url_hash, domain=domain,
        fetched_at_iso=fetched_iso, jsonld=result.json_ld,
    ) if result.json_ld else None
    _get_pages_repo().put(
        result, s3_html_uri=s3_html_uri, s3_jsonld_uri=s3_jsonld_uri,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/extract", response_model=ExtractResult, tags=["extract"])
async def extract(
    req: ExtractRequest,
    fixture: int = Query(
        0, ge=0, le=1,
        description="If 1 and URL matches Amazon test URL, returns saved fixture",
    ),
) -> ExtractResult:
    if fixture == 1 and "amazon.com" in req.url.lower() and "cuisinart" in req.url.lower():
        from crawler.fixtures import amazon_toaster
        return amazon_toaster()

    settings = _settings()
    try:
        returned = await extract_pipeline(req.url, return_html=True)
        if isinstance(returned, tuple):
            result, raw_html = returned
        else:
            result, raw_html = returned, None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc

    if (
        result.extraction_confidence < settings.confidence_threshold
        and settings.headless_function_name
    ):
        try:
            data = invoke_headless(req.url, persist=False)
            headless_result = ExtractResult(**data)
            if headless_result.extraction_confidence > result.extraction_confidence:
                result = headless_result
                raw_html = None  # headless wrote its own copy (or persist=False)
        except Exception:  # noqa: BLE001, S110 - degrade gracefully to static result
            logger.exception("headless escalation failed for %s", req.url)

    try:
        _persist(result, raw_html)
    except Exception:  # noqa: BLE001, S110 - persistence failure must not break the response
        logger.exception("persistence failed for %s", req.url)
    return result


@router.get("/pages", response_model=ExtractResult, tags=["pages"])
def pages_by_url(url: str = Query(..., description="URL to look up")) -> ExtractResult:
    result = _get_pages_repo().get(url_hash=_url_hash(url))
    if not result:
        raise HTTPException(status_code=404, detail="not found")
    return result


@router.get("/pages/{url_hash}", response_model=ExtractResult, tags=["pages"])
def pages_by_hash(url_hash: str) -> ExtractResult:
    result = _get_pages_repo().get(url_hash=url_hash)
    if not result:
        raise HTTPException(status_code=404, detail="not found")
    return result


# ---------------------------------------------------------------------------
# Batch — SQS in prod, in-process background tasks locally
# ---------------------------------------------------------------------------

async def _process_batch_url(url: str, job_id: str) -> None:
    """Process a single URL from a batch — used by local in-process worker."""
    jobs = _get_jobs_repo()
    try:
        returned = await extract_pipeline(url, return_html=True)
        if isinstance(returned, tuple):
            result, raw_html = returned
        else:
            result, raw_html = returned, None

        # Headless escalation for low-confidence results
        settings = _settings()
        if (
            result.extraction_confidence < settings.confidence_threshold
            and settings.headless_function_name
        ):
            try:
                data = invoke_headless(url, persist=False)
                headless_result = ExtractResult(**data)
                if headless_result.extraction_confidence > result.extraction_confidence:
                    result = headless_result
                    raw_html = None
            except Exception:  # noqa: BLE001, S110
                pass

        _persist(result, raw_html)
        jobs.increment(job_id=job_id, succeeded=1)
    except Exception:  # noqa: BLE001
        logger.exception("batch-local: failed on %s", url)
        jobs.increment(job_id=job_id, failed=1)


async def _process_batch_locally(urls: list[str], job_id: str) -> None:
    """Process all batch URLs concurrently (local mode)."""
    tasks = [_process_batch_url(url, job_id) for url in urls]
    await asyncio.gather(*tasks, return_exceptions=True)


@router.post("/batch", response_model=BatchResponse, tags=["batch"])
async def batch(req: BatchRequest, background_tasks: BackgroundTasks) -> BatchResponse:
    s = _settings()
    job_id = uuid.uuid4().hex
    jobs = _get_jobs_repo()
    jobs.create(job_id=job_id, total=len(req.urls))

    if s.is_local:
        # Local mode: process in background tasks
        background_tasks.add_task(_process_batch_locally, req.urls, job_id)
        return BatchResponse(job_id=job_id)

    # Production: send to SQS
    if not s.static_queue_url:
        raise HTTPException(status_code=503, detail="batch path not configured")

    import boto3
    sqs = boto3.client("sqs")
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


@router.get("/jobs/{job_id}", response_model=JobStatus, tags=["batch"])
def jobs_get(job_id: str) -> JobStatus:
    status = _get_jobs_repo().get(job_id=job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return status
