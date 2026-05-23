"""HTTP routes."""
from __future__ import annotations

import json
import uuid
from urllib.parse import urlsplit

import boto3
from fastapi import APIRouter, HTTPException, Query

from crawler.api.schemas import BatchRequest, BatchResponse, ExtractRequest, ExtractResult, JobStatus
from crawler.config import load_settings
from crawler.fetcher.headless import invoke_headless
from crawler.pipeline import extract_pipeline
from crawler.storage.dynamo import JobsRepo, PagesRepo
from crawler.storage.hashing import url_hash as _url_hash
from crawler.storage.s3 import RawHtmlStore

router = APIRouter()


def _settings():
    return load_settings()


def _persist(result: ExtractResult, html: str | None) -> None:
    s = _settings()
    if not s.raw_html_bucket or not s.pages_table:
        return  # local-dev fallback: skip persistence
    store = RawHtmlStore(bucket=s.raw_html_bucket)
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
    PagesRepo(table_name=s.pages_table).put(
        result, s3_html_uri=s3_html_uri, s3_jsonld_uri=s3_jsonld_uri
    )


@router.post("/extract", response_model=ExtractResult, tags=["extract"])
async def extract(req: ExtractRequest) -> ExtractResult:
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
        except Exception:  # noqa: BLE001
            pass  # Headless failed — keep the static result

    try:
        if raw_html is not None:
            _persist(result, raw_html)
    except Exception:  # noqa: BLE001
        pass
    return result


@router.get("/pages", response_model=ExtractResult, tags=["pages"])
def pages_by_url(url: str = Query(..., description="URL to look up")) -> ExtractResult:
    s = _settings()
    result = PagesRepo(table_name=s.pages_table).get(url_hash=_url_hash(url))
    if not result:
        raise HTTPException(status_code=404, detail="not found")
    return result


@router.get("/pages/{url_hash}", response_model=ExtractResult, tags=["pages"])
def pages_by_hash(url_hash: str) -> ExtractResult:
    s = _settings()
    result = PagesRepo(table_name=s.pages_table).get(url_hash=url_hash)
    if not result:
        raise HTTPException(status_code=404, detail="not found")
    return result


@router.post("/batch", response_model=BatchResponse, tags=["batch"])
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


@router.get("/jobs/{job_id}", response_model=JobStatus, tags=["batch"])
def jobs_get(job_id: str) -> JobStatus:
    s = _settings()
    status = JobsRepo(table_name=s.jobs_table).get(job_id=job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return status
