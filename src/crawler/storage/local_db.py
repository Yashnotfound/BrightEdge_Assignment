"""JSON-file-backed PagesRepo and JobsRepo for local development (replaces DynamoDB)."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from crawler.api.schemas import ExtractResult, JobStatus, Topic

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, default=str, indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass(frozen=True)
class LocalPagesRepo:
    """Drop-in replacement for PagesRepo backed by a local JSON file."""

    db_path: Path  # e.g. .local_data/pages.json

    def put(self, result: ExtractResult, *, s3_html_uri: str | None,
            s3_jsonld_uri: str | None) -> None:
        with _lock:
            db = _read_json(self.db_path)
            db[result.url_hash] = {
                "url_hash": result.url_hash,
                "url": result.url,
                "domain": result.url.split("//")[-1].split("/")[0].lower(),
                "fetched_at": result.fetched_at.isoformat(),
                "fetcher_used": result.fetcher_used,
                "http_status": result.http_status,
                "content_type": result.content_type,
                "language": result.language,
                "title": result.title,
                "description": result.description,
                "canonical_url": result.canonical_url,
                "open_graph": result.open_graph,
                "twitter_card": result.twitter_card,
                "json_ld": result.json_ld,
                "topics": [t.model_dump() for t in result.topics],
                "extraction_confidence": result.extraction_confidence,
                "word_count": result.word_count,
                "s3_html_uri": s3_html_uri,
                "s3_jsonld_uri": s3_jsonld_uri,
                "schema_version": 1,
            }
            _write_json(self.db_path, db)
        logger.info("local-db: saved page %s (%s)", result.url_hash[:12], result.url)

    def get(self, *, url_hash: str) -> ExtractResult | None:
        with _lock:
            db = _read_json(self.db_path)
        item = db.get(url_hash)
        if not item:
            return None
        return ExtractResult(
            url=item["url"],
            url_hash=item["url_hash"],
            fetched_at=datetime.fromisoformat(item["fetched_at"]),
            fetcher_used=item["fetcher_used"],
            http_status=int(item["http_status"]),
            content_type=item.get("content_type"),
            language=item.get("language"),
            title=item.get("title"),
            description=item.get("description"),
            canonical_url=item.get("canonical_url"),
            open_graph=dict(item.get("open_graph") or {}),
            twitter_card=dict(item.get("twitter_card") or {}),
            json_ld=list(item.get("json_ld") or []),
            body_text=None,
            word_count=int(item.get("word_count") or 0),
            topics=[Topic(**t) for t in (item.get("topics") or [])],
            extraction_confidence=float(item.get("extraction_confidence") or 0.0),
        )


@dataclass(frozen=True)
class LocalJobsRepo:
    """Drop-in replacement for JobsRepo backed by a local JSON file."""

    db_path: Path  # e.g. .local_data/jobs.json

    def create(self, *, job_id: str, total: int) -> None:
        now = datetime.now(UTC).isoformat()
        with _lock:
            db = _read_json(self.db_path)
            db[job_id] = {
                "job_id": job_id, "status": "queued",
                "total": total, "succeeded": 0, "failed": 0,
                "created_at": now, "updated_at": now,
            }
            _write_json(self.db_path, db)

    def increment(self, *, job_id: str, succeeded: int = 0, failed: int = 0) -> None:
        with _lock:
            db = _read_json(self.db_path)
            job = db.get(job_id)
            if not job:
                return
            job["succeeded"] = job.get("succeeded", 0) + succeeded
            job["failed"] = job.get("failed", 0) + failed
            job["updated_at"] = datetime.now(UTC).isoformat()
            # Recompute status
            if job["succeeded"] + job["failed"] >= job["total"]:
                job["status"] = "complete" if job["failed"] == 0 else "partial"
            elif job["succeeded"] + job["failed"] > 0:
                job["status"] = "running"
            _write_json(self.db_path, db)

    def get(self, *, job_id: str) -> JobStatus | None:
        with _lock:
            db = _read_json(self.db_path)
        item = db.get(job_id)
        if not item:
            return None
        return JobStatus(
            job_id=item["job_id"],
            status=item["status"],
            total=int(item["total"]),
            succeeded=int(item.get("succeeded") or 0),
            failed=int(item.get("failed") or 0),
            manifest_s3_uri=item.get("manifest_s3_uri"),
            created_at=datetime.fromisoformat(item["created_at"]),
            updated_at=datetime.fromisoformat(item["updated_at"]),
        )
