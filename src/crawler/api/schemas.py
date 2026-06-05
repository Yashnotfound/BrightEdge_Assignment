"""Pydantic models for API contracts and pipeline results."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from crawler.fetcher.url_safety import validate_url


class Topic(BaseModel):
    label: str
    score: float
    sources: list[str] = Field(default_factory=list)


class ExtractResult(BaseModel):
    url: str
    url_hash: str
    fetched_at: datetime
    fetcher_used: str  # "static" | "headless" | "none" | "rejected"
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
    escalation: Literal[
        "not_attempted", "skipped", "succeeded", "no_improvement", "failed"
    ] = "not_attempted"
    escalation_error: str | None = None
    escalation_meta: dict[str, Any] = Field(default_factory=dict)


class ExtractRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def _validate_url_safety(cls, v: str) -> str:
        # SSRF guard: reject internal / metadata / loopback targets. Raises
        # `UnsafeUrlError` (a ValueError subclass) — Pydantic converts that
        # into a 422 response with the field highlighted.
        validate_url(v)
        return v


class BatchRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("urls")
    @classmethod
    def _validate_each_url_safety(cls, v: list[str]) -> list[str]:
        # Validate every URL in the batch. Any unsafe URL fails the whole
        # batch — caller has to fix and resubmit. Cleaner than partial
        # acceptance for the same defensive reasons we reject obviously
        # bad SQL or oversize payloads at the API boundary.
        for url in v:
            validate_url(url)
        return v


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
