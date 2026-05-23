"""Pydantic models for API contracts and pipeline results."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Topic(BaseModel):
    label: str
    score: float
    sources: list[str] = Field(default_factory=list)


class ExtractResult(BaseModel):
    url: str
    url_hash: str
    fetched_at: datetime
    fetcher_used: str  # "static" | "headless"
    http_status: int
    content_type: str | None = None
    language: str | None = None
    title: str | None = None
    description: str | None = None
    canonical_url: str | None = None
    open_graph: dict[str, str] = Field(default_factory=dict)
    twitter_card: dict[str, str] = Field(default_factory=dict)
    json_ld: list[dict[str, Any]] = Field(default_factory=list)
    body_text: str | None = None
    word_count: int = 0
    topics: list[Topic] = Field(default_factory=list)
    extraction_confidence: float = 0.0
    errors: list[str] = Field(default_factory=list)


class ExtractRequest(BaseModel):
    url: str


class BatchRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=1000)


class BatchResponse(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    job_id: str
    status: str  # queued | running | partial | complete | failed
    total: int
    succeeded: int
    failed: int
    manifest_s3_uri: str | None = None
    created_at: datetime
    updated_at: datetime
