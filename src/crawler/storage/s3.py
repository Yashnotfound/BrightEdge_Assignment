"""S3 wrappers for raw HTML + parsed JSON-LD storage."""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from typing import Any

import boto3


def _date_partition(fetched_at_iso: str) -> str:
    # fetched_at_iso like 2026-05-23T12:34:56Z
    date_part = fetched_at_iso[:10]
    y, m, d = date_part.split("-")
    return f"year={y}/month={m}/day={d}"


@dataclass(frozen=True)
class RawHtmlStore:
    bucket: str

    @property
    def _client(self):
        return boto3.client("s3")

    def put_raw_html(self, *, url_hash: str, domain: str, fetched_at_iso: str, html: str) -> str:
        key = (
            f"raw/domain={domain}/{_date_partition(fetched_at_iso)}/{url_hash}.html.gz"
        )
        body = gzip.compress(html.encode("utf-8"))
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=body,
            ContentType="text/html", ContentEncoding="gzip",
        )
        return f"s3://{self.bucket}/{key}"

    def put_jsonld(self, *, url_hash: str, domain: str, fetched_at_iso: str,
                   jsonld: list[dict[str, Any]]) -> str:
        key = (
            f"jsonld/domain={domain}/{_date_partition(fetched_at_iso)}/{url_hash}.jsonld.json"
        )
        body = json.dumps(jsonld, ensure_ascii=False).encode("utf-8")
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType="application/json",
        )
        return f"s3://{self.bucket}/{key}"
