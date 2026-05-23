"""Tests for DynamoDB accessors."""
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from crawler.api.schemas import ExtractResult, Topic
from crawler.storage.dynamo import JobsRepo, PagesRepo


@pytest.fixture
def ddb_tables():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="pages",
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
        client.create_table(
            TableName="jobs",
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield "pages", "jobs"


def _make_result(url: str) -> ExtractResult:
    return ExtractResult(
        url=url, url_hash="h" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static", http_status=200,
        title="T", word_count=100,
        topics=[Topic(label="t1", score=1.0, sources=["s"])],
        extraction_confidence=0.8,
    )


def test_pages_put_and_get(ddb_tables):
    pages, _ = ddb_tables
    repo = PagesRepo(table_name=pages)
    result = _make_result("http://x.com")
    repo.put(result, s3_html_uri="s3://x/h", s3_jsonld_uri=None)
    fetched = repo.get(url_hash=result.url_hash)
    assert fetched is not None
    assert fetched.title == "T"


def test_jobs_lifecycle(ddb_tables):
    _, jobs = ddb_tables
    repo = JobsRepo(table_name=jobs)
    repo.create(job_id="j1", total=3)
    repo.increment(job_id="j1", succeeded=1)
    repo.increment(job_id="j1", failed=1)
    status = repo.get(job_id="j1")
    assert status.succeeded == 1
    assert status.failed == 1
    assert status.status == "running"
    repo.increment(job_id="j1", succeeded=1)
    status = repo.get(job_id="j1")
    # total=3, succeeded=2, failed=1 → partial (any failures → partial)
    assert status.status == "partial"
