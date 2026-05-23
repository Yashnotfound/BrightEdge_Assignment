"""Smoke tests for the FastAPI app."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from crawler.api.main import app
from crawler.api.schemas import ExtractResult, Topic


def test_health_endpoint_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_extract_endpoint_returns_result(monkeypatch):
    fake_result = ExtractResult(
        url="http://example.com",
        url_hash="a" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="Example",
        body_text="content",
        word_count=1,
        topics=[Topic(label="example", score=1.0, sources=["meta:keywords"])],
        extraction_confidence=0.9,
    )
    monkeypatch.setattr(
        "crawler.api.routes.extract_pipeline", AsyncMock(return_value=fake_result)
    )

    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://example.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Example"
    assert data["topics"][0]["label"] == "example"


def test_extract_endpoint_validates_url():
    client = TestClient(app)
    response = client.post("/extract", json={})
    assert response.status_code == 422


def test_index_serves_html():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "BrightEdge Crawler" in response.text
    assert "<!DOCTYPE html>" in response.text or "<h1>" in response.text


def test_pages_by_url_returns_404_for_unknown(monkeypatch):
    # Monkeypatch the PagesRepo.get to return None (simulates not found)
    from crawler.storage.dynamo import PagesRepo
    monkeypatch.setattr(PagesRepo, "get", lambda self, *, url_hash: None)
    client = TestClient(app)
    response = client.get("/pages?url=http://does-not-exist.example/")
    assert response.status_code == 404


def test_pages_by_hash_returns_result(monkeypatch):
    from crawler.storage.dynamo import PagesRepo
    fake = ExtractResult(
        url="http://example.com",
        url_hash="b" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="Cached",
    )
    monkeypatch.setattr(PagesRepo, "get", lambda self, *, url_hash: fake)
    client = TestClient(app)
    response = client.get("/pages/" + ("b" * 64))
    assert response.status_code == 200
    assert response.json()["title"] == "Cached"


def test_batch_returns_503_when_unconfigured(monkeypatch):
    # Default settings (no queue url) → 503
    monkeypatch.delenv("STATIC_QUEUE_URL", raising=False)
    client = TestClient(app)
    response = client.post("/batch", json={"urls": ["http://x.com"]})
    assert response.status_code == 503


def test_batch_enqueues_and_returns_job_id(monkeypatch):
    """With moto SQS + DDB, /batch should create a job and enqueue."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue_url = sqs.create_queue(QueueName="test-queue")["QueueUrl"]
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="test-jobs",
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setenv("STATIC_QUEUE_URL", queue_url)
        monkeypatch.setenv("JOBS_TABLE", "test-jobs")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        client = TestClient(app)
        response = client.post(
            "/batch",
            json={"urls": ["http://a.com/x", "http://b.com/y"]},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        assert len(job_id) == 32  # uuid4 hex

        # Confirm messages were enqueued
        msgs = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)
        assert len(msgs.get("Messages", [])) == 2

        # /jobs/{id} should return the new job
        status_resp = client.get(f"/jobs/{job_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["total"] == 2
        assert status_resp.json()["status"] == "queued"


def test_jobs_get_returns_404_for_unknown(monkeypatch):
    from crawler.storage.dynamo import JobsRepo
    monkeypatch.setattr(JobsRepo, "get", lambda self, *, job_id: None)
    client = TestClient(app)
    response = client.get("/jobs/does-not-exist")
    assert response.status_code == 404


def test_extract_fixture_mode_amazon():
    client = TestClient(app)
    response = client.post(
        "/extract?fixture=1",
        json={"url": "http://www.amazon.com/Cuisinart-CPT-122-Compact-2-SliceToaster/dp/B009GQ034C/"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["fetcher_used"] == "fixture"
    assert "Cuisinart" in data["title"]
    assert any(t["label"] == "toaster" for t in data["topics"])
    assert any("fixture_mode" in e for e in data["errors"])


def test_extract_fixture_mode_ignored_for_non_amazon(monkeypatch):
    """Fixture mode only triggers for the Amazon test URL."""
    fake = ExtractResult(
        url="http://example.com",
        url_hash="c" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
    )
    monkeypatch.setattr(
        "crawler.api.routes.extract_pipeline",
        AsyncMock(return_value=(fake, "<html></html>")),
    )

    client = TestClient(app)
    response = client.post(
        "/extract?fixture=1",
        json={"url": "http://example.com/not-amazon"},
    )
    assert response.status_code == 200
    assert response.json()["fetcher_used"] == "static"  # fixture didn't trigger
