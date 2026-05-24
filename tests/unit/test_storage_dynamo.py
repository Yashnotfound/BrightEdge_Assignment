"""Tests for DynamoDB accessors."""
from datetime import UTC, datetime
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
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


def test_try_claim_for_job_new_claim_returns_true(ddb_tables):
    """First call to claim a (job_id, url_hash) returns True so the caller
    knows it should bump the job counter."""
    pages, _ = ddb_tables
    repo = PagesRepo(table_name=pages)
    result = _make_result("http://x.com")
    repo.put(result, s3_html_uri=None, s3_jsonld_uri=None)

    claimed = repo.try_claim_for_job(url_hash=result.url_hash, job_id="job-A")

    assert claimed is True
    # Side effect: the row now records the claim.
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(pages)
    item = table.get_item(Key={"url_hash": result.url_hash, "version": 0})["Item"]
    assert item["counted_job_ids"] == {"job-A"}


def test_try_claim_for_job_duplicate_returns_false(ddb_tables):
    """A repeated claim for the same (job_id, url_hash) returns False so
    the caller skips the counter bump."""
    pages, _ = ddb_tables
    repo = PagesRepo(table_name=pages)
    result = _make_result("http://x.com")
    repo.put(result, s3_html_uri=None, s3_jsonld_uri=None)

    first = repo.try_claim_for_job(url_hash=result.url_hash, job_id="job-A")
    second = repo.try_claim_for_job(url_hash=result.url_hash, job_id="job-A")

    assert first is True
    assert second is False
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(pages)
    item = table.get_item(Key={"url_hash": result.url_hash, "version": 0})["Item"]
    assert item["counted_job_ids"] == {"job-A"}  # still just the one entry


def test_try_claim_for_job_different_jobs_both_succeed(ddb_tables):
    """Distinct job_ids on the same url_hash each get a True claim."""
    pages, _ = ddb_tables
    repo = PagesRepo(table_name=pages)
    result = _make_result("http://x.com")
    repo.put(result, s3_html_uri=None, s3_jsonld_uri=None)

    assert repo.try_claim_for_job(url_hash=result.url_hash, job_id="job-A") is True
    assert repo.try_claim_for_job(url_hash=result.url_hash, job_id="job-B") is True

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(pages)
    item = table.get_item(Key={"url_hash": result.url_hash, "version": 0})["Item"]
    assert item["counted_job_ids"] == {"job-A", "job-B"}


def test_try_claim_for_job_row_missing_returns_true(ddb_tables):
    """Claiming against a non-existent Pages row still returns True. ADD
    will create the row implicitly; this is an acceptable side effect.

    Also asserts idempotency on the ghost-row path: a second claim with
    the same (job_id, url_hash) returns False even though the row was
    born as a ghost."""
    pages, _ = ddb_tables
    repo = PagesRepo(table_name=pages)

    claimed = repo.try_claim_for_job(url_hash="z" * 64, job_id="job-A")
    second = repo.try_claim_for_job(url_hash="z" * 64, job_id="job-A")

    assert claimed is True
    assert second is False


def test_pages_get_returns_none_on_ghost_row(ddb_tables):
    """A row created only by ``try_claim_for_job`` (no ``put``) has no
    ``url`` attribute. ``get`` must treat it as not-found rather than
    raising KeyError, which would propagate as an unhandled 500."""
    pages, _ = ddb_tables
    repo = PagesRepo(table_name=pages)
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(pages)
    # Seed a ghost row directly: only the keys + counted_job_ids.
    ghost_hash = "g" * 64
    table.put_item(Item={
        "url_hash": ghost_hash,
        "version": 0,
        "counted_job_ids": {"job-x"},
    })

    fetched = repo.get(url_hash=ghost_hash)

    assert fetched is None


def test_pages_put_then_get_round_trips_errors_field(ddb_tables):
    """The ``errors`` list on an ExtractResult must survive a put/get cycle.

    The persist gate stamps ``persistence_rejected:<reason>`` onto
    ``errors`` so analysts can query DDB for WHY a row was rejected.
    Dropping the field silently from put/get defeats the audit trail."""
    pages, _ = ddb_tables
    repo = PagesRepo(table_name=pages)
    result = ExtractResult(
        url="http://blocked.example/x",
        url_hash="e" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="rejected",
        http_status=403,
        extraction_confidence=0.0,
        errors=["persistence_rejected:upstream_blocked", "fetch_attempt:1"],
    )
    repo.put(result, s3_html_uri=None, s3_jsonld_uri=None)

    fetched = repo.get(url_hash=result.url_hash)

    assert fetched is not None
    assert fetched.errors == [
        "persistence_rejected:upstream_blocked",
        "fetch_attempt:1",
    ]


def test_pages_put_then_get_roundtrips_reserved_word_attrs(ddb_tables):
    """Ensure put/get round-trips correctly when attribute names collide
    with DDB reserved words (``url``, ``language``). Guards against any
    future drift between the ``names`` and ``values`` index mappings."""
    pages, _ = ddb_tables
    repo = PagesRepo(table_name=pages)
    result = ExtractResult(
        url="http://reserved.example/", url_hash="r" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static", http_status=200,
        language="en", title="Reserved",
        word_count=42,
        topics=[Topic(label="t1", score=0.9, sources=["s"])],
        extraction_confidence=0.7,
    )
    repo.put(result, s3_html_uri=None, s3_jsonld_uri=None)

    fetched = repo.get(url_hash=result.url_hash)

    assert fetched is not None
    assert fetched.url == "http://reserved.example/"
    assert fetched.language == "en"
    assert fetched.title == "Reserved"


def test_try_claim_for_job_client_error_fails_open(ddb_tables):
    """If DDB raises a ClientError (e.g. counted_job_ids has the wrong
    type after a schema drift), ``try_claim_for_job`` should fail OPEN —
    return True so the caller bumps the counter — rather than raising
    and triggering an SQS redelivery loop that ends in the DLQ."""
    pages, _ = ddb_tables
    repo = PagesRepo(table_name=pages)

    err = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "bad type"}},
        "UpdateItem",
    )

    class _BoomTable:
        def update_item(self, **kwargs):
            raise err

    with patch.object(PagesRepo, "_table", _BoomTable()):
        result = repo.try_claim_for_job(url_hash="h" * 64, job_id="job-A")

    assert result is True


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
    # total=3, succeeded=2, failed=1 → partial (mixed outcome)
    assert status.status == "partial"


def test_jobs_terminal_status_all_succeeded_is_complete(ddb_tables):
    """succeeded == total and failed == 0 → ``complete``."""
    _, jobs = ddb_tables
    repo = JobsRepo(table_name=jobs)
    repo.create(job_id="j-complete", total=3)
    repo.increment(job_id="j-complete", succeeded=3)
    status = repo.get(job_id="j-complete")
    assert status.status == "complete"
    assert status.succeeded == 3
    assert status.failed == 0


def test_jobs_terminal_status_all_failed_is_failed(ddb_tables):
    """failed == total and succeeded == 0 → ``failed`` (regression for the
    bug where this case incorrectly reported ``partial``)."""
    _, jobs = ddb_tables
    repo = JobsRepo(table_name=jobs)
    repo.create(job_id="j-failed", total=2)
    repo.increment(job_id="j-failed", failed=2)
    status = repo.get(job_id="j-failed")
    assert status.status == "failed"
    assert status.succeeded == 0
    assert status.failed == 2


def test_jobs_terminal_status_mixed_is_partial(ddb_tables):
    """Both succeeded >= 1 and failed >= 1 with sum == total → ``partial``."""
    _, jobs = ddb_tables
    repo = JobsRepo(table_name=jobs)
    repo.create(job_id="j-partial", total=4)
    repo.increment(job_id="j-partial", succeeded=3, failed=1)
    status = repo.get(job_id="j-partial")
    assert status.status == "partial"
    assert status.succeeded == 3
    assert status.failed == 1
