"""Filesystem-backed storage for local development (replaces S3)."""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _date_partition(fetched_at_iso: str) -> str:
    date_part = fetched_at_iso[:10]
    y, m, d = date_part.split("-")
    return f"year={y}/month={m}/day={d}"


@dataclass(frozen=True)
class LocalHtmlStore:
    """Drop-in replacement for RawHtmlStore that writes to the local filesystem."""

    base_dir: Path

    def put_raw_html(
        self, *, url_hash: str, domain: str, fetched_at_iso: str, html: str,
    ) -> str:
        rel = f"raw/domain={domain}/{_date_partition(fetched_at_iso)}/{url_hash}.html.gz"
        path = self.base_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(html.encode("utf-8")))
        return f"file://{path}"

    def put_jsonld(
        self, *, url_hash: str, domain: str, fetched_at_iso: str,
        jsonld: list[dict[str, Any]],
    ) -> str:
        rel = f"jsonld/domain={domain}/{_date_partition(fetched_at_iso)}/{url_hash}.jsonld.json"
        path = self.base_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(jsonld, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"file://{path}"
