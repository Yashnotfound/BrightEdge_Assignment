"""Tests for the static SQS worker."""
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from crawler.api.schemas import ExtractResult


@pytest.fixture
def aws_resources():
    """Provision DDB + S3 like prod."""
    with mock_aws():
        # DDB
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="brightedge-pages",
            AttributeDefinitions=[
                {"AttributeName": "url_hash", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
            ],
            KeySchema=[
                {"AttributeName": "url_hash", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName="brightedge-jobs",
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        # S3
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="raw")
        yield


def test_process_one_extracts_and_persists(aws_resources, monkeypatch):
    """A simple happy path: extract_pipeline returns a result, worker persists it."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("JOBS_TABLE", "brightedge-jobs")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")

    fake_result = ExtractResult(
        url="http://example.com",
        url_hash="a" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="Test",
        extraction_confidence=0.9,
    )

    async def fake_pipeline(url, *, return_html=False):
        return fake_result, "<html>x</html>"

    from crawler.workers import static_worker
    monkeypatch.setattr(static_worker, "extract_pipeline", fake_pipeline)

    # Create the job first so increment works
    from crawler.storage.dynamo import JobsRepo
    JobsRepo(table_name="brightedge-jobs").create(job_id="j1", total=1)

    static_worker._process_one({"url": "http://example.com", "job_id": "j1"})

    # Confirm pages row exists
    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    item = pages.get_item(Key={"url_hash": "a" * 64, "version": 0}).get("Item")
    assert item is not None
    assert item["title"] == "Test"
    # Confirm S3 object exists
    s3 = boto3.client("s3", region_name="us-east-1")
    objs = s3.list_objects_v2(Bucket="raw")
    assert objs["KeyCount"] >= 1  # at least the raw HTML


def _low_confidence_result() -> ExtractResult:
    """Build a static result below the default 0.6 confidence threshold."""
    return ExtractResult(
        url="http://example.com",
        url_hash="b" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="LowConf",
        extraction_confidence=0.1,
    )


def test_process_one_headless_raises_falls_back_to_static(aws_resources, monkeypatch):
    """When invoke_headless raises, the worker must persist the static result
    and bump succeeded exactly once — not double-count via the headless branch."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("JOBS_TABLE", "brightedge-jobs")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")

    fake_result = _low_confidence_result()

    async def fake_pipeline(url, *, return_html=False):
        return fake_result, "<html>x</html>"

    from crawler.workers import static_worker
    monkeypatch.setattr(static_worker, "extract_pipeline", fake_pipeline)

    def boom(url, *, persist=False):
        raise RuntimeError("headless Lambda errored (Unhandled): {...}")

    monkeypatch.setattr("crawler.workers.static_worker.invoke_headless", boom)

    from crawler.storage.dynamo import JobsRepo
    JobsRepo(table_name="brightedge-jobs").create(job_id="j2", total=1)

    increment_calls: list[dict] = []
    real_increment = JobsRepo.increment

    def spy_increment(self, **kwargs):
        increment_calls.append(kwargs)
        return real_increment(self, **kwargs)

    monkeypatch.setattr(JobsRepo, "increment", spy_increment)

    static_worker._process_one({"url": "http://example.com", "job_id": "j2"})

    # Static-result fallback must have landed.
    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    item = pages.get_item(Key={"url_hash": "b" * 64, "version": 0}).get("Item")
    assert item is not None
    assert item["title"] == "LowConf"

    # Exactly one succeeded bump, no failed bump.
    succeeded = sum(c.get("succeeded", 0) for c in increment_calls)
    failed = sum(c.get("failed", 0) for c in increment_calls)
    assert succeeded == 1
    assert failed == 0


def test_process_one_headless_malformed_payload_falls_back(aws_resources, monkeypatch):
    """If invoke_headless returns a non-ExtractResult dict (e.g. an error payload),
    the worker must NOT count it as success — it should fall back to static."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("JOBS_TABLE", "brightedge-jobs")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")

    fake_result = _low_confidence_result()

    async def fake_pipeline(url, *, return_html=False):
        return fake_result, "<html>x</html>"

    from crawler.workers import static_worker
    monkeypatch.setattr(static_worker, "extract_pipeline", fake_pipeline)

    def returns_error_payload(url, *, persist=False):
        # Shape Lambda returns when handler raises — no url_hash.
        return {"errorType": "TimeoutError", "errorMessage": "timeout"}

    monkeypatch.setattr(
        "crawler.workers.static_worker.invoke_headless",
        returns_error_payload,
    )

    from crawler.storage.dynamo import JobsRepo
    JobsRepo(table_name="brightedge-jobs").create(job_id="j3", total=1)

    increment_calls: list[dict] = []
    real_increment = JobsRepo.increment

    def spy_increment(self, **kwargs):
        increment_calls.append(kwargs)
        return real_increment(self, **kwargs)

    monkeypatch.setattr(JobsRepo, "increment", spy_increment)

    static_worker._process_one({"url": "http://example.com", "job_id": "j3"})

    # Static fallback persisted the row.
    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    item = pages.get_item(Key={"url_hash": "b" * 64, "version": 0}).get("Item")
    assert item is not None
    assert item["title"] == "LowConf"

    # Exactly one succeeded bump.
    succeeded = sum(c.get("succeeded", 0) for c in increment_calls)
    failed = sum(c.get("failed", 0) for c in increment_calls)
    assert succeeded == 1
    assert failed == 0


def test_process_one_captcha_body_persists_rejected_marker(aws_resources, monkeypatch):
    """A 200 with a captcha-fingerprint body must NOT be persisted as real
    content. The persist gate replaces the result with a `rejected` marker,
    bumps `failed` (not `succeeded`), and skips the S3 raw-HTML write."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("JOBS_TABLE", "brightedge-jobs")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")
    monkeypatch.delenv("HEADLESS_FUNCTION_NAME", raising=False)

    # Static pipeline returns a result with a CAPTCHA body. Without the gate,
    # this would be persisted with bogus topics like ["captcha", "robot"].
    fake_result = ExtractResult(
        url="http://example.com",
        url_hash="c" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="Just a moment",
        extraction_confidence=0.7,  # high enough to bypass the 4xx confidence floor
    )
    captcha_html = (
        "<html><body>Please complete the CAPTCHA to continue.</body></html>"
    )

    async def fake_pipeline(url, *, return_html=False):
        return fake_result, captcha_html

    from crawler.workers import static_worker
    monkeypatch.setattr(static_worker, "extract_pipeline", fake_pipeline)

    from crawler.storage.dynamo import JobsRepo
    JobsRepo(table_name="brightedge-jobs").create(job_id="jcap", total=1)

    increment_calls: list[dict] = []
    real_increment = JobsRepo.increment

    def spy_increment(self, **kwargs):
        increment_calls.append(kwargs)
        return real_increment(self, **kwargs)

    monkeypatch.setattr(JobsRepo, "increment", spy_increment)

    static_worker._process_one({"url": "http://example.com", "job_id": "jcap"})

    # DDB row was written but as a rejected marker.
    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    item = pages.get_item(Key={"url_hash": "c" * 64, "version": 0}).get("Item")
    assert item is not None
    assert item["fetcher_used"] == "rejected"
    assert float(item["extraction_confidence"]) == 0.0
    assert item["topics"] == []
    # Audit trail: the persist gate's `persistence_rejected:<reason>` tag
    # must survive into the DDB row so analysts can grep by rejection
    # reason without parsing the rest of the field.
    errors = list(item.get("errors") or [])
    assert any(
        e.startswith("persistence_rejected:") for e in errors
    ), f"expected persistence_rejected marker in errors, got {errors}"

    # S3 raw HTML was NOT written (no useful content to store).
    s3 = boto3.client("s3", region_name="us-east-1")
    assert s3.list_objects_v2(Bucket="raw").get("KeyCount", 0) == 0

    # Job counter bumps `failed`, not `succeeded`.
    succeeded = sum(c.get("succeeded", 0) for c in increment_calls)
    failed = sum(c.get("failed", 0) for c in increment_calls)
    assert succeeded == 0
    assert failed == 1


def test_process_one_headless_returned_rejection_bumps_failed(aws_resources, monkeypatch):
    """When the headless escalation succeeds shape-wise but the headless
    worker itself ran the persist gate and persisted a rejected marker, the
    static worker MUST mirror that on its job counter — `failed=1`, not
    `succeeded=1` — so job-completion counters stay consistent with the
    rest of the gate's semantics (rejected → failed)."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("JOBS_TABLE", "brightedge-jobs")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")

    fake_result = _low_confidence_result()

    async def fake_pipeline(url, *, return_html=False):
        return fake_result, "<html>x</html>"

    from crawler.workers import static_worker
    monkeypatch.setattr(static_worker, "extract_pipeline", fake_pipeline)

    def returns_rejected_payload(url, *, persist=False):
        # Headless persisted its result as a rejected marker (its body
        # triggered the persist gate). Payload has a valid url_hash but
        # fetcher_used == "rejected".
        return {
            "url": url,
            "url_hash": "b" * 64,
            "fetcher_used": "rejected",
            "http_status": 403,
            "extraction_confidence": 0.0,
        }

    monkeypatch.setattr(
        "crawler.workers.static_worker.invoke_headless",
        returns_rejected_payload,
    )

    from crawler.storage.dynamo import JobsRepo
    JobsRepo(table_name="brightedge-jobs").create(job_id="jrej", total=1)

    increment_calls: list[dict] = []
    real_increment = JobsRepo.increment

    def spy_increment(self, **kwargs):
        increment_calls.append(kwargs)
        return real_increment(self, **kwargs)

    monkeypatch.setattr(JobsRepo, "increment", spy_increment)

    static_worker._process_one({"url": "http://example.com", "job_id": "jrej"})

    # The escalation success branch must have bumped `failed`, not `succeeded`,
    # because the headless-side gate already classified the page as rejected.
    succeeded = sum(c.get("succeeded", 0) for c in increment_calls)
    failed = sum(c.get("failed", 0) for c in increment_calls)
    assert succeeded == 0, f"expected no succeeded bump on headless rejection, got {increment_calls}"
    assert failed == 1, f"expected exactly one failed bump, got {increment_calls}"


def test_process_one_idempotent_succeeded_bump_on_redeliver(aws_resources, monkeypatch):
    """SQS at-least-once: a redelivered message must not double-bump
    `succeeded`. The first claim succeeds, the second is a no-op."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("JOBS_TABLE", "brightedge-jobs")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")
    monkeypatch.delenv("HEADLESS_FUNCTION_NAME", raising=False)

    fake_result = ExtractResult(
        url="http://example.com",
        url_hash="d" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="Test",
        extraction_confidence=0.9,
    )

    async def fake_pipeline(url, *, return_html=False):
        return fake_result, "<html>x</html>"

    from crawler.workers import static_worker
    monkeypatch.setattr(static_worker, "extract_pipeline", fake_pipeline)

    from crawler.storage.dynamo import JobsRepo
    JobsRepo(table_name="brightedge-jobs").create(job_id="jidem", total=1)

    increment_calls: list[dict] = []
    real_increment = JobsRepo.increment

    def spy_increment(self, **kwargs):
        increment_calls.append(kwargs)
        return real_increment(self, **kwargs)

    monkeypatch.setattr(JobsRepo, "increment", spy_increment)

    # Two deliveries of the same SQS message body.
    static_worker._process_one({"url": "http://example.com", "job_id": "jidem"})
    static_worker._process_one({"url": "http://example.com", "job_id": "jidem"})

    succeeded = sum(c.get("succeeded", 0) for c in increment_calls)
    failed = sum(c.get("failed", 0) for c in increment_calls)
    assert succeeded == 1, f"expected exactly one succeeded bump, got {increment_calls}"
    assert failed == 0


def test_process_one_idempotent_failed_bump_on_redeliver_after_rejection(
    aws_resources, monkeypatch
):
    """A redelivered message that the persist gate rejects must bump
    `failed` only once, not twice."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("JOBS_TABLE", "brightedge-jobs")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")
    monkeypatch.delenv("HEADLESS_FUNCTION_NAME", raising=False)

    fake_result = ExtractResult(
        url="http://example.com",
        url_hash="e" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="Just a moment",
        extraction_confidence=0.7,
    )
    captcha_html = (
        "<html><body>Please complete the CAPTCHA to continue.</body></html>"
    )

    async def fake_pipeline(url, *, return_html=False):
        return fake_result, captcha_html

    from crawler.workers import static_worker
    monkeypatch.setattr(static_worker, "extract_pipeline", fake_pipeline)

    from crawler.storage.dynamo import JobsRepo
    JobsRepo(table_name="brightedge-jobs").create(job_id="jrejidem", total=1)

    increment_calls: list[dict] = []
    real_increment = JobsRepo.increment

    def spy_increment(self, **kwargs):
        increment_calls.append(kwargs)
        return real_increment(self, **kwargs)

    monkeypatch.setattr(JobsRepo, "increment", spy_increment)

    static_worker._process_one({"url": "http://example.com", "job_id": "jrejidem"})
    static_worker._process_one({"url": "http://example.com", "job_id": "jrejidem"})

    succeeded = sum(c.get("succeeded", 0) for c in increment_calls)
    failed = sum(c.get("failed", 0) for c in increment_calls)
    assert succeeded == 0
    assert failed == 1, f"expected exactly one failed bump, got {increment_calls}"


def test_process_one_idempotent_succeeded_bump_after_headless_escalation(
    aws_resources, monkeypatch
):
    """The headless-escalation success branch must also gate its counter
    bump on `try_claim_for_job`, so SQS redeliveries don't over-count."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("JOBS_TABLE", "brightedge-jobs")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")

    fake_result = _low_confidence_result()

    async def fake_pipeline(url, *, return_html=False):
        return fake_result, "<html>x</html>"

    from crawler.workers import static_worker
    monkeypatch.setattr(static_worker, "extract_pipeline", fake_pipeline)

    def returns_ok_payload(url, *, persist=False):
        # Headless persisted successfully; payload has a valid url_hash.
        return {
            "url": url,
            "url_hash": "b" * 64,
            "fetcher_used": "headless",
            "http_status": 200,
            "extraction_confidence": 0.95,
        }

    monkeypatch.setattr(
        "crawler.workers.static_worker.invoke_headless",
        returns_ok_payload,
    )

    from crawler.storage.dynamo import JobsRepo, PagesRepo
    JobsRepo(table_name="brightedge-jobs").create(job_id="jhead", total=1)

    # For the escalation path, the static worker doesn't write the Pages
    # row itself — the headless worker does. Pre-seed a minimal row so
    # try_claim_for_job has something to ADD onto. (The real headless
    # Lambda would have written it in production.)
    pages_table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        "brightedge-pages"
    )
    pages_table.put_item(Item={"url_hash": "b" * 64, "version": 0})

    increment_calls: list[dict] = []
    real_increment = JobsRepo.increment

    def spy_increment(self, **kwargs):
        increment_calls.append(kwargs)
        return real_increment(self, **kwargs)

    monkeypatch.setattr(JobsRepo, "increment", spy_increment)

    static_worker._process_one({"url": "http://example.com", "job_id": "jhead"})
    static_worker._process_one({"url": "http://example.com", "job_id": "jhead"})

    succeeded = sum(c.get("succeeded", 0) for c in increment_calls)
    failed = sum(c.get("failed", 0) for c in increment_calls)
    assert succeeded == 1, f"expected exactly one succeeded bump, got {increment_calls}"
    assert failed == 0


def test_process_one_5xx_persists_rejected_marker(aws_resources, monkeypatch):
    """5xx upstream errors are persisted as `upstream_error` markers, not
    counted as successful extractions."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("JOBS_TABLE", "brightedge-jobs")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")
    monkeypatch.delenv("HEADLESS_FUNCTION_NAME", raising=False)

    fake_result = ExtractResult(
        url="http://example.com",
        url_hash="5" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=503,
        extraction_confidence=0.8,
    )

    async def fake_pipeline(url, *, return_html=False):
        return fake_result, "<html>Service Unavailable</html>"

    from crawler.workers import static_worker
    monkeypatch.setattr(static_worker, "extract_pipeline", fake_pipeline)

    from crawler.storage.dynamo import JobsRepo
    JobsRepo(table_name="brightedge-jobs").create(job_id="j5", total=1)

    static_worker._process_one({"url": "http://example.com", "job_id": "j5"})

    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    item = pages.get_item(Key={"url_hash": "5" * 64, "version": 0}).get("Item")
    assert item is not None
    assert item["fetcher_used"] == "rejected"
    assert float(item["extraction_confidence"]) == 0.0
    assert item["http_status"] == 503  # preserved
    # S3 raw HTML write was skipped.
    s3 = boto3.client("s3", region_name="us-east-1")
    assert s3.list_objects_v2(Bucket="raw").get("KeyCount", 0) == 0
