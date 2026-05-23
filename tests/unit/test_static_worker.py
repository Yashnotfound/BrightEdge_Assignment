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
