"""HTTP routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from crawler.api.schemas import ExtractRequest, ExtractResult
from crawler.pipeline import extract_pipeline

router = APIRouter()


@router.post("/extract", response_model=ExtractResult, tags=["extract"])
async def extract(req: ExtractRequest) -> ExtractResult:
    try:
        return await extract_pipeline(req.url)
    except Exception as exc:  # noqa: BLE001 - boundary
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc
