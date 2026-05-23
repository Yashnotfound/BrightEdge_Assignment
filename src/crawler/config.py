"""Environment-driven configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    local_data_dir: str

    @property
    def is_local(self) -> bool:
        """True when running in local-dev mode (no AWS infra)."""
        return self.headless_function_name == "LOCAL" or not self.raw_html_bucket

    @property
    def local_data_path(self) -> Path:
        """Resolved path for local filesystem storage."""
        return Path(self.local_data_dir).resolve()


def load_settings() -> Settings:
    return Settings(
        pages_table=os.getenv("PAGES_TABLE", ""),
        jobs_table=os.getenv("JOBS_TABLE", ""),
        raw_html_bucket=os.getenv("RAW_HTML_BUCKET", ""),
        jobs_bucket=os.getenv("JOBS_BUCKET", ""),
        static_queue_url=os.getenv("STATIC_QUEUE_URL", ""),
        headless_function_name=os.getenv("HEADLESS_FUNCTION_NAME", "LOCAL"),
        aws_region=os.getenv("AWS_REGION", "us-east-1"),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.5")),
        local_data_dir=os.getenv("LOCAL_DATA_DIR", ".local_data"),
    )
