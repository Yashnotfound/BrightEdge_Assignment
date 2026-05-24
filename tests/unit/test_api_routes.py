"""Smoke tests for the FastAPI app."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from crawler.api.main import app
from crawler.api.schemas import ExtractResult, Topic


def test_extract_requires_auth_when_key_is_set(monkeypatch):
    """When API_KEY is set, /extract without Authorization returns 401."""
    monkeypatch.setenv("API_KEY", "test-secret-key")
    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://example.com"})
    assert response.status_code == 401
    assert "Bearer" in response.headers.get("WWW-Authenticate", "")


def test_extract_succeeds_with_correct_bearer(monkeypatch):
    """With valid Bearer token, /extract passes through to the handler."""
    monkeypatch.setenv("API_KEY", "test-secret-key")
    fake_result = ExtractResult(
        url="http://example.com",
        url_hash="d" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="OK",
    )
    monkeypatch.setattr(
        "crawler.api.routes.extract_pipeline",
        AsyncMock(return_value=(fake_result, "<html></html>")),
    )
    client = TestClient(app)
    response = client.post(
        "/extract",
        headers={"Authorization": "Bearer test-secret-key"},
        json={"url": "http://example.com"},
    )
    assert response.status_code == 200


def test_extract_rejects_wrong_bearer(monkeypatch):
    """Wrong Bearer token returns 401."""
    monkeypatch.setenv("API_KEY", "test-secret-key")
    client = TestClient(app)
    response = client.post(
        "/extract",
        headers={"Authorization": "Bearer wrong-key"},
        json={"url": "http://example.com"},
    )
    assert response.status_code == 401


def test_health_is_never_protected(monkeypatch):
    """/health should never require auth even when API_KEY is set."""
    monkeypatch.setenv("API_KEY", "test-secret-key")
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


def test_no_auth_required_when_api_key_unset(monkeypatch):
    """Without API_KEY env var, all routes are open (local dev mode)."""
    monkeypatch.delenv("API_KEY", raising=False)
    fake_result = ExtractResult(
        url="http://example.com",
        url_hash="e" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="OK",
    )
    monkeypatch.setattr(
        "crawler.api.routes.extract_pipeline",
        AsyncMock(return_value=(fake_result, "<html></html>")),
    )
    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://example.com"})
    assert response.status_code == 200


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


def test_extract_fixture_mode_rei():
    client = TestClient(app)
    response = client.post(
        "/extract?fixture=1",
        json={"url": "http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["fetcher_used"] == "fixture"
    assert "REI" in data["title"] or "Outdoors" in data["title"]
    assert any(t["label"] == "camping" for t in data["topics"])


def test_extract_fixture_mode_cnn():
    client = TestClient(app)
    response = client.post(
        "/extract?fixture=1",
        json={"url": "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["fetcher_used"] == "fixture"
    assert "AI" in data["title"] or "tech" in data["title"].lower()
    assert any(t["label"] == "ai" for t in data["topics"])


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


# ---------------------------------------------------------------------------
# Escalation tracking — sync /extract path
# ---------------------------------------------------------------------------


def test_extract_escalation_not_attempted_on_high_confidence(monkeypatch):
    """Confidence above threshold → no escalation attempted."""
    monkeypatch.delenv("API_KEY", raising=False)
    fake = ExtractResult(
        url="http://example.com",
        url_hash="f" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        extraction_confidence=0.9,
    )
    monkeypatch.setattr(
        "crawler.api.routes.extract_pipeline",
        AsyncMock(return_value=(fake, "<html></html>")),
    )
    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://example.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["escalation"] == "not_attempted"
    assert data["escalation_meta"] == {}
    assert data["escalation_error"] is None


def test_extract_escalation_skipped_when_headless_unconfigured(monkeypatch):
    """Low confidence + no HEADLESS_FUNCTION_NAME → skipped."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("HEADLESS_FUNCTION_NAME", raising=False)
    fake = ExtractResult(
        url="http://example.com",
        url_hash="1" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        extraction_confidence=0.3,
    )
    monkeypatch.setattr(
        "crawler.api.routes.extract_pipeline",
        AsyncMock(return_value=(fake, "<html></html>")),
    )
    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://example.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["escalation"] == "skipped"
    assert data["escalation_meta"] == {}
    assert data["escalation_error"] is None


def test_extract_escalation_succeeded_when_headless_beats_static(monkeypatch):
    """Low static confidence + headless wins → succeeded + result swapped."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")
    static_result = ExtractResult(
        url="http://example.com",
        url_hash="2" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="Static",
        extraction_confidence=0.3,
        word_count=10,
    )
    headless_result = ExtractResult(
        url="http://example.com",
        url_hash="2" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="headless",
        http_status=200,
        title="Headless",
        extraction_confidence=0.9,
        word_count=500,
    )
    monkeypatch.setattr(
        "crawler.api.routes.extract_pipeline",
        AsyncMock(return_value=(static_result, "<html></html>")),
    )
    monkeypatch.setattr(
        "crawler.api.routes.invoke_headless",
        MagicMock(return_value=headless_result.model_dump(mode="json")),
    )
    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://example.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["escalation"] == "succeeded"
    assert data["escalation_meta"]["headless_confidence"] == 0.9
    assert data["escalation_meta"]["headless_word_count"] == 500
    assert data["fetcher_used"] == "headless"
    assert data["title"] == "Headless"
    assert data["escalation_error"] is None


def test_extract_escalation_no_improvement_when_headless_equal_or_lower(monkeypatch):
    """Headless returned but did not beat static → no_improvement + keep static."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")
    static_result = ExtractResult(
        url="http://example.com",
        url_hash="3" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="Static",
        extraction_confidence=0.3,
        word_count=10,
    )
    headless_result = ExtractResult(
        url="http://example.com",
        url_hash="3" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="headless",
        http_status=200,
        title="Headless",
        extraction_confidence=0.3,
        word_count=25,
    )
    monkeypatch.setattr(
        "crawler.api.routes.extract_pipeline",
        AsyncMock(return_value=(static_result, "<html></html>")),
    )
    monkeypatch.setattr(
        "crawler.api.routes.invoke_headless",
        MagicMock(return_value=headless_result.model_dump(mode="json")),
    )
    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://example.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["escalation"] == "no_improvement"
    assert data["escalation_meta"]["headless_confidence"] == 0.3
    assert data["escalation_meta"]["headless_word_count"] == 25
    # Static result preserved
    assert data["fetcher_used"] == "static"
    assert data["title"] == "Static"
    assert data["escalation_error"] is None


def test_extract_escalation_failed_captures_error(monkeypatch):
    """ClientError on invoke_headless → failed + escalation_error tag."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")
    static_result = ExtractResult(
        url="http://example.com",
        url_hash="4" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="Static",
        extraction_confidence=0.3,
    )
    monkeypatch.setattr(
        "crawler.api.routes.extract_pipeline",
        AsyncMock(return_value=(static_result, "<html></html>")),
    )
    err = ClientError({"Error": {"Code": "TooManyRequestsException"}}, "Invoke")
    monkeypatch.setattr(
        "crawler.api.routes.invoke_headless",
        MagicMock(side_effect=err),
    )
    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://example.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["escalation"] == "failed"
    assert data["escalation_error"].startswith("lambda:")
    assert "TooManyRequestsException" in data["escalation_error"]
    # Static result preserved
    assert data["fetcher_used"] == "static"
    assert data["title"] == "Static"


def test_extract_escalation_failed_on_generic_exception(monkeypatch):
    """Non-AWS exception on invoke_headless (e.g., timeout, malformed payload) →
    failed + escalation_error contains ONLY the exception class name (no message
    body), so we never leak Pydantic ValidationError internals or internal
    paths to the client."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")
    static_result = ExtractResult(
        url="http://example.com",
        url_hash="5" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="Static",
        extraction_confidence=0.3,
    )
    monkeypatch.setattr(
        "crawler.api.routes.extract_pipeline",
        AsyncMock(return_value=(static_result, "<html></html>")),
    )
    # Simulate e.g. a socket timeout reading the Lambda response payload —
    # message includes a path-like detail that should NOT appear in the
    # client-facing response.
    sensitive = "/var/task/internal-secret-path: connection reset by peer"
    monkeypatch.setattr(
        "crawler.api.routes.invoke_headless",
        MagicMock(side_effect=TimeoutError(sensitive)),
    )
    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://example.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["escalation"] == "failed"
    # ONLY the class name is exposed — message content is logged server-side.
    assert data["escalation_error"] == "TimeoutError"
    assert sensitive not in data["escalation_error"]
    # Static result preserved.
    assert data["fetcher_used"] == "static"
    assert data["title"] == "Static"
