"""Environment-driven configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    pages_table: str
    jobs_table: str
    raw_html_bucket: str
    jobs_bucket: str
    static_queue_url: str
    headless_function_name: str
    aws_region: str
    confidence_threshold: float


def load_settings() -> Settings:
    return Settings(
        pages_table=os.getenv("PAGES_TABLE", "brightedge-pages"),
        jobs_table=os.getenv("JOBS_TABLE", "brightedge-jobs"),
        raw_html_bucket=os.getenv("RAW_HTML_BUCKET", ""),
        jobs_bucket=os.getenv("JOBS_BUCKET", ""),
        static_queue_url=os.getenv("STATIC_QUEUE_URL", ""),
        headless_function_name=os.getenv("HEADLESS_FUNCTION_NAME", ""),
        aws_region=os.getenv("AWS_REGION", "us-east-1"),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.5")),
    )
