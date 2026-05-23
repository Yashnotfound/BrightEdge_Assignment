"""HTTP routes."""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query

from crawler.api.schemas import ExtractRequest, ExtractResult
from crawler.config import load_settings
from crawler.pipeline import extract_pipeline
from crawler.storage.dynamo import PagesRepo
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
    try:
        returned = await extract_pipeline(req.url, return_html=True)
        # Handle both tuple (result, html) and plain ExtractResult (e.g., in tests)
        if isinstance(returned, tuple):
            result, raw_html = returned
        else:
            result, raw_html = returned, None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc
    try:
        _persist(result, raw_html)
    except Exception:  # noqa: BLE001
        pass  # persistence failures should not break the response
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
