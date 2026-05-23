"""HTTP routes."""
from __future__ import annotations

import asyncio
import json
import uuid
from urllib.parse import urlsplit

import boto3
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
from crawler.pipeline import extract_pipeline
from crawler.storage.dynamo import JobsRepo, PagesRepo
from crawler.storage.hashing import url_hash as _url_hash
from crawler.storage.s3 import RawHtmlStore

router = APIRouter()


def _settings():
    return load_settings()


async def _persist(result: ExtractResult, html: str | None) -> None:
    """Persist S3 raw HTML + S3 JSON-LD + DynamoDB row concurrently.

    boto3 is sync, so each write runs in a default-executor thread; the three
    are awaited together. DynamoDB write is intentionally sequenced AFTER the
    S3 writes complete so its row references the final S3 URIs.
    """
    s = _settings()
    if not s.raw_html_bucket or not s.pages_table:
        return  # local-dev fallback: skip persistence
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


@router.post("/extract", response_model=ExtractResult, tags=["extract"],
             dependencies=[Depends(require_api_key)])
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
            pass

    try:
        if raw_html is not None:
            await _persist(result, raw_html)
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
