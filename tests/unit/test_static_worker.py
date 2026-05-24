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

    monkeypatch.setattr("crawler.fetcher.headless.invoke_headless", boom)

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
        "crawler.fetcher.headless.invoke_headless",
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
