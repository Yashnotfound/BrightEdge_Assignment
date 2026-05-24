"""DynamoDB Pages and Jobs accessors."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit

import boto3
from botocore.exceptions import ClientError

from crawler.api.schemas import ExtractResult, JobStatus, Topic

logger = logging.getLogger(__name__)


def _resource(region_name: str = "us-east-1"):
    return boto3.resource("dynamodb", region_name=region_name)


def _to_decimal_safe(value):
    """Recursively convert floats to Decimal for DDB compat."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_to_decimal_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_decimal_safe(v) for k, v in value.items()}
    return value


def _domain_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


@dataclass(frozen=True)
class PagesRepo:
    table_name: str
    region_name: str = "us-east-1"

    @property
    def _table(self):
        return _resource(self.region_name).Table(self.table_name)

    def put(self, result: ExtractResult, *, s3_html_uri: str | None,
            s3_jsonld_uri: str | None) -> None:
        # PoC simplification: store json_ld inline in DDB so /pages cached
        # lookups return the full schema. Production (per design spec §7.3)
        # moves json_ld to S3 to keep DDB items <8KB; we accept that risk
        # at PoC scale because the test URLs have <2KB JSON-LD each.
        #
        # NB: we use UpdateItem (with SET on every content attribute) rather
        # than PutItem because PutItem replaces the whole item, which would
        # wipe the `counted_job_ids` SET that `try_claim_for_job` maintains
        # for idempotent job-counter bumps across SQS at-least-once
        # redeliveries. UpdateItem leaves attributes we don't touch alone.
        attrs = {
            "url": result.url,
            "domain": _domain_of(result.url),
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
            "jsonld_present": bool(result.json_ld),
            "json_ld": result.json_ld,
            "topics": [t.model_dump() for t in result.topics],
            "extraction_confidence": result.extraction_confidence,
            "word_count": result.word_count,
            "s3_html_uri": s3_html_uri,
            "s3_jsonld_uri": s3_jsonld_uri,
            "schema_version": 1,
        }
        attrs = _to_decimal_safe(attrs)
        # Build "SET #a0 = :v0, #a1 = :v1, ..." — placeholders avoid
        # collisions with DDB reserved words like `url`, `domain`, `language`.
        # Index by position rather than by dict iteration so the
        # placeholders are the single source of truth: a future refactor
        # that filters `names` or `values` separately can't silently
        # misalign `#aN` with `:vN`.
        items = list(attrs.items())
        names = {f"#a{i}": k for i, (k, _) in enumerate(items)}
        values = {f":v{i}": v for i, (_, v) in enumerate(items)}
        set_clause = ", ".join(f"#a{i} = :v{i}" for i in range(len(items)))
        self._table.update_item(
            Key={"url_hash": result.url_hash, "version": 0},
            UpdateExpression=f"SET {set_clause}",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def try_claim_for_job(self, *, url_hash: str, job_id: str) -> bool:
        """Atomically record that ``job_id`` has counted this ``url_hash``.

        Returns True if this is the first time ``job_id`` claimed this
        ``url_hash`` (caller should bump the job counter). Returns False
        if ``job_id`` already claimed it (caller should skip the bump).

        Implementation: ``UpdateItem`` with ``ADD counted_job_ids :j`` and
        ``ReturnValues=ALL_OLD``. ``ADD`` on a SET is idempotent — adding
        the same member twice leaves the SET unchanged. We inspect the
        pre-update state (``ALL_OLD``) to detect whether this was a fresh
        claim.

        Side effects:

        * If the Pages row does not exist, ``UpdateItem`` will create it
          (with only ``url_hash`` / ``version`` / ``counted_job_ids``).
          This is an acceptable PoC behaviour: callers normally invoke
          ``put`` first, so this branch only fires on a worker that
          crashed before persistence. The over-counted "ghost" row is
          inert for ``/pages`` reads (no ``fetcher_used``).

        Used by the SQS workers to deduplicate counter bumps across
        at-least-once re-deliveries — see ``docs/modules/workers.md``.
        """
        try:
            response = self._table.update_item(
                Key={"url_hash": url_hash, "version": 0},
                UpdateExpression="ADD counted_job_ids :j",
                ExpressionAttributeValues={":j": {job_id}},
                ReturnValues="ALL_OLD",
            )
        except ClientError as exc:
            # Fail OPEN: if the claim row is in an unexpected state
            # (e.g. `counted_job_ids` was written with a wrong DDB type
            # by a future schema drift, raising ValidationException),
            # we'd rather over-count once than have the entire SQS
            # record fail, redeliver, and eventually DLQ. Over-counting
            # matches the pre-idempotency behaviour of the workers.
            logger.warning(
                "try_claim_for_job failed for url_hash=%s job_id=%s: %s",
                url_hash, job_id, exc,
            )
            return True
        old_attrs = response.get("Attributes") or {}
        old_claims = old_attrs.get("counted_job_ids") or set()
        return job_id not in old_claims

    def get(self, *, url_hash: str) -> ExtractResult | None:
        response = self._table.get_item(Key={"url_hash": url_hash, "version": 0})
        item = response.get("Item")
        if not item:
            return None
        # Ghost-row guard: `try_claim_for_job` (UpdateItem ADD) creates a
        # minimal item with only `url_hash` / `version` / `counted_job_ids`
        # if the row didn't exist yet. `put` always writes `url`, so its
        # absence is a reliable signal that the row has no content body.
        # Treat as not-found rather than raising KeyError out of /pages.
        if "url" not in item:
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
class JobsRepo:
    table_name: str
    region_name: str = "us-east-1"

    @property
    def _table(self):
        return _resource(self.region_name).Table(self.table_name)

    def create(self, *, job_id: str, total: int) -> None:
        now = datetime.now(UTC).isoformat()
        self._table.put_item(Item={
            "job_id": job_id, "status": "queued",
            "total": total, "succeeded": 0, "failed": 0,
            "created_at": now, "updated_at": now,
        })

    def increment(self, *, job_id: str, succeeded: int = 0, failed: int = 0) -> None:
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="ADD succeeded :s, failed :f SET updated_at = :u",
            ExpressionAttributeValues={
                ":s": succeeded, ":f": failed,
                ":u": datetime.now(UTC).isoformat(),
            },
        )
        # Recompute status
        status = self.get(job_id=job_id)
        if status.succeeded + status.failed >= status.total:
            new_status = "complete" if status.failed == 0 else "partial"
            self._set_status(job_id, new_status)
        elif status.succeeded + status.failed > 0:
            self._set_status(job_id, "running")

    def _set_status(self, job_id: str, status: str) -> None:
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :st",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":st": status},
        )

    def get(self, *, job_id: str) -> JobStatus | None:
        response = self._table.get_item(Key={"job_id": job_id})
        item = response.get("Item")
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
