"""SQS-triggered static worker: fetch -> extract -> classify -> persist -> bump job counts."""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlsplit

from aws_lambda_powertools.utilities.batch import (
    BatchProcessor,
    EventType,
    process_partial_response,
)
from aws_lambda_powertools.utilities.data_classes.sqs_event import SQSRecord

from crawler.config import load_settings
from crawler.pipeline import extract_pipeline
from crawler.storage.dynamo import JobsRepo, PagesRepo
from crawler.storage.s3 import RawHtmlStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

processor = BatchProcessor(event_type=EventType.SQS)


def _process_one(message_body: dict) -> None:
    url = message_body["url"]
    job_id = message_body.get("job_id")
    settings = load_settings()
    pages = PagesRepo(table_name=settings.pages_table)
    jobs = JobsRepo(table_name=settings.jobs_table) if job_id else None
    store = RawHtmlStore(bucket=settings.raw_html_bucket)

    try:
        result, raw_html = asyncio.run(extract_pipeline(url, return_html=True))

        # Escalate to headless if confidence is low and headless is configured.
        #
        # Note (escalation observability): the sync `POST /extract` path
        # populates `result.escalation` / `escalation_meta` / `escalation_error`
        # so callers can see what the system did. The async path here does
        # NOT mirror that yet — when headless succeeds, the headless worker
        # persists its OWN ExtractResult (with the default `escalation:
        # "not_attempted"`), and when escalation fails we fall through to
        # persist the static result without flagging the failure. Wiring this
        # through requires either passing an `escalated_from` hint into
        # `invoke_headless` or post-updating the persisted row — both are
        # bigger changes deliberately deferred from the sync-path fix.
        if (
            result.extraction_confidence < settings.confidence_threshold
            and settings.headless_function_name
        ):
            try:
                from crawler.fetcher.headless import invoke_headless
                data = invoke_headless(url, persist=True)  # headless persists itself
                # Defensive guard: if invoke_headless ever returns without
                # raising on a malformed payload (e.g. an error-shape dict),
                # don't blindly count it as success. Force the static fallback.
                if not isinstance(data, dict) or not data.get("url_hash"):
                    raise RuntimeError(
                        f"headless returned unusable payload: {str(data)[:300]}"
                    )
                if jobs:
                    jobs.increment(job_id=job_id, succeeded=1)
                return
            except Exception:  # noqa: BLE001
                logger.exception("headless escalation failed; keeping static result")

        domain = urlsplit(result.url).netloc.lower()
        fetched_iso = result.fetched_at.isoformat()
        s3_html_uri = store.put_raw_html(
            url_hash=result.url_hash, domain=domain,
            fetched_at_iso=fetched_iso, html=raw_html,
        )
        s3_jsonld_uri = (
            store.put_jsonld(
                url_hash=result.url_hash, domain=domain,
                fetched_at_iso=fetched_iso, jsonld=result.json_ld,
            ) if result.json_ld else None
        )
        pages.put(result, s3_html_uri=s3_html_uri, s3_jsonld_uri=s3_jsonld_uri)
        if jobs:
            jobs.increment(job_id=job_id, succeeded=1)
    except Exception:
        logger.exception("static-worker failure on url=%s", url)
        if jobs:
            jobs.increment(job_id=job_id, failed=1)
        raise


def _record_handler(record: SQSRecord) -> None:
    _process_one(json.loads(record.body))


def handler(event: dict, context) -> dict:
    return process_partial_response(
        event=event,
        record_handler=_record_handler,
        processor=processor,
        context=context,
    )
