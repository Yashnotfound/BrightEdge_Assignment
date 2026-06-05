"""Smoke tests for the FastAPI app."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from crawler.api.main import app
from crawler.api.schemas import ExtractResult, Topic

# Note: the SSRF guard added to ExtractRequest/BatchRequest resolves hosts
# via DNS at validation time. A global autouse `_stub_dns` fixture in
# tests/conftest.py maps all hosts to a benign public IP so the fake test
# hostnames here (http://x.com, http://blocked.example/, etc.) pass
# validation without a live DNS query.


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


# ---------------------------------------------------------------------------
# Graceful handling of upstream fetch failures (timeouts / firewall blockers)
# ---------------------------------------------------------------------------


def test_extract_returns_degraded_response_when_static_fetch_fails_and_no_headless(
    monkeypatch,
):
    """When the static fetcher errors (timeout, DNS failure, firewall block,
    etc.) AND no headless function is configured, the API must return 200
    with a degraded `ExtractResult` (escalation=failed) rather than crashing
    Lambda with an unhandled exception (which surfaces to the client as a
    generic API Gateway 500)."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("HEADLESS_FUNCTION_NAME", raising=False)

    from crawler.fetcher.static import FetchTimeoutError

    async def boom(*args, **kwargs):
        raise FetchTimeoutError("deadline exhausted")

    monkeypatch.setattr("crawler.api.routes.extract_pipeline", boom)

    client = TestClient(app)
    response = client.post(
        "/extract",
        json={"url": "https://www.example-firewall.com/blocked"},
    )

    # Critical: NOT a 500. The API itself worked; the upstream URL did not.
    assert response.status_code == 200
    data = response.json()
    assert data["fetcher_used"] == "none"
    assert data["http_status"] == 0
    assert data["extraction_confidence"] == 0.0
    assert data["escalation"] == "failed"
    assert data["escalation_error"] == "fetch_failed:FetchTimeoutError"
    assert any(
        e.startswith("static_fetch_failed:FetchTimeoutError") for e in data["errors"]
    )
    # No headless was even attempted, so no headless_fetch_failed marker.
    assert not any(e.startswith("headless_fetch_failed") for e in data["errors"])


def test_extract_falls_back_to_headless_when_static_fetch_fails(monkeypatch):
    """When the static fetcher fails AND headless is configured AND there's
    enough wall-clock budget left, the route invokes headless as a rescue and
    returns the headless result with `escalation=succeeded` plus a
    `reason: static_fetch_failed` marker in escalation_meta. This is exactly
    the path that should help URLs like REI that timeout from Lambda's IPs
    but resolve fine through Playwright via a different egress."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")

    async def boom(*args, **kwargs):
        raise TimeoutError("simulated static timeout")

    monkeypatch.setattr("crawler.api.routes.extract_pipeline", boom)

    headless_result = ExtractResult(
        url="https://www.rei.com/blog/x",
        url_hash="9" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="headless",
        http_status=200,
        title="REI: Indoorsy Friend",
        extraction_confidence=0.85,
        word_count=300,
    )
    monkeypatch.setattr(
        "crawler.api.routes.invoke_headless",
        MagicMock(return_value=headless_result.model_dump(mode="json")),
    )

    client = TestClient(app)
    response = client.post(
        "/extract",
        json={"url": "https://www.rei.com/blog/x"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["fetcher_used"] == "headless"
    assert data["extraction_confidence"] == 0.85
    assert data["title"] == "REI: Indoorsy Friend"
    assert data["escalation"] == "succeeded"
    # The `reason` tag is what tells operators "this was a static-failure
    # rescue", distinct from a normal low-confidence escalation.
    assert data["escalation_meta"]["reason"] == "static_fetch_failed"
    assert data["escalation_meta"]["static_error"] == "TimeoutError"
    assert data["escalation_meta"]["headless_confidence"] == 0.85


def test_extract_returns_degraded_when_static_and_headless_both_fail(monkeypatch):
    """Both fetchers fail → 200 with both error markers and escalation=failed.
    The client gets enough diagnostic detail to act (e.g., flag the URL as
    unfetchable) without a generic 5xx."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")

    async def boom(*args, **kwargs):
        raise TimeoutError("static dead")

    monkeypatch.setattr("crawler.api.routes.extract_pipeline", boom)
    monkeypatch.setattr(
        "crawler.api.routes.invoke_headless",
        MagicMock(side_effect=RuntimeError("playwright crashed")),
    )

    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://blocked.example/"})
    assert response.status_code == 200
    data = response.json()
    assert data["fetcher_used"] == "none"
    assert data["escalation"] == "failed"
    assert data["escalation_error"] == "fetch_failed:TimeoutError"
    # Both static and headless markers present so operators can see both legs
    # of the fallback chain failed.
    assert any(e.startswith("static_fetch_failed:TimeoutError") for e in data["errors"])
    assert any(e.startswith("headless_fetch_failed:RuntimeError") for e in data["errors"])


def test_extract_skips_headless_rescue_when_budget_too_small(monkeypatch):
    """When static fetch fails AND headless is configured BUT there's less
    than _HEADLESS_FALLBACK_MIN_SEC wall-clock remaining, the rescue is
    SKIPPED rather than attempted. Skipping is the right call here: cold-
    starting headless with <8s left would only push us over Lambda's 28s
    ceiling and turn a clean degraded-200 into a hard timeout.

    Deterministically engineered by setting the per-request budget to ~0 so
    the post-static `remaining` is always below the headless-fallback floor.
    Avoids real-time sleeps that flake on overloaded CI runners."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")
    # Force `remaining` to immediately fall below _HEADLESS_FALLBACK_MIN_SEC
    # so the guard trips before invoke_headless is called.
    monkeypatch.setattr("crawler.api.routes._EXTRACT_BUDGET_SEC", 0.0)

    async def boom(*args, **kwargs):
        raise TimeoutError("static dead")

    monkeypatch.setattr("crawler.api.routes.extract_pipeline", boom)

    # If headless were actually invoked, the mock would record a call —
    # but it must NEVER be invoked on this path.
    headless_mock = MagicMock(return_value={})
    monkeypatch.setattr("crawler.api.routes.invoke_headless", headless_mock)

    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://slow.example/"})

    assert response.status_code == 200
    data = response.json()
    # Degraded response — headless was over-budget and skipped.
    assert data["fetcher_used"] == "none"
    assert data["escalation"] == "failed"
    assert data["escalation_error"] == "fetch_failed:TimeoutError"
    # Critical assertion: headless was NEVER invoked because the budget
    # check tripped before the boto3 call.
    assert headless_mock.call_count == 0, (
        f"headless was invoked despite tiny budget: {headless_mock.call_count} times"
    )
    # The errors list should contain the static failure marker but NOT a
    # headless-failure marker (because we never tried).
    assert any(e.startswith("static_fetch_failed:TimeoutError") for e in data["errors"])
    assert not any(e.startswith("headless_fetch_failed") for e in data["errors"])


def test_extract_wait_for_ceiling_falls_back_gracefully(monkeypatch):
    """The 24s `asyncio.wait_for` belt-and-braces around `extract_pipeline`
    is meant to catch downstream bugs that ignore the deadline. When it
    fires, we should still produce a clean degraded-200 response (NOT
    propagate the TimeoutError uncaught, which would surface as a 500)."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("HEADLESS_FUNCTION_NAME", raising=False)

    # Patch the wait_for ceiling down to 0.1s so the test runs fast.
    # The pipeline mock hangs forever — the ceiling MUST fire and convert
    # this into a `TimeoutError` static_exc, then a degraded response.
    monkeypatch.setattr("crawler.api.routes._EXTRACT_WAIT_FOR_SEC", 0.1)

    import asyncio as _asyncio

    async def hangs_forever(*args, **kwargs):
        await _asyncio.sleep(60)  # would block Lambda timeout without wait_for

    monkeypatch.setattr("crawler.api.routes.extract_pipeline", hangs_forever)

    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://hangs.example/"})

    # Critical: clean 200, NOT a propagated TimeoutError → 500.
    assert response.status_code == 200
    data = response.json()
    assert data["fetcher_used"] == "none"
    assert data["escalation"] == "failed"
    # The ceiling raises asyncio.TimeoutError (alias for builtin TimeoutError
    # since 3.11). Either name in errors[] is acceptable.
    assert data["escalation_error"].startswith("fetch_failed:TimeoutError")


# ---------------------------------------------------------------------------
# Persist gate — reject garbage rows (4xx/5xx/captcha) before they pollute DDB
# ---------------------------------------------------------------------------


def test_extract_rejects_captcha_body_before_persist(monkeypatch):
    """A 200 with a captcha-fingerprint body must NOT be persisted as real
    content. The route's response also reflects the rejection so callers
    can see `fetcher_used="rejected"` instead of bogus topics."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("HEADLESS_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("PAGES_TABLE", "test-pages")
    monkeypatch.setenv("RAW_HTML_BUCKET", "test-bucket")

    fake = ExtractResult(
        url="http://blocked.example/x",
        url_hash="g" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="static",
        http_status=200,
        title="Just a moment",
        extraction_confidence=0.7,
        topics=[Topic(label="captcha", score=0.9, sources=["body"])],
    )
    captcha_html = (
        "<html><body>Please complete the CAPTCHA to continue.</body></html>"
    )
    monkeypatch.setattr(
        "crawler.api.routes.extract_pipeline",
        AsyncMock(return_value=(fake, captcha_html)),
    )

    persist_calls: list = []

    def spy_put(self, result, *, s3_html_uri, s3_jsonld_uri):
        persist_calls.append(
            {
                "fetcher_used": result.fetcher_used,
                "topics": result.topics,
                "extraction_confidence": result.extraction_confidence,
                "s3_html_uri": s3_html_uri,
            }
        )

    from crawler.storage.dynamo import PagesRepo
    monkeypatch.setattr(PagesRepo, "put", spy_put)

    s3_put_calls: list = []

    def spy_put_raw_html(self, *, url_hash, domain, fetched_at_iso, html):
        s3_put_calls.append(url_hash)
        return f"s3://x/{url_hash}"

    from crawler.storage.s3 import RawHtmlStore
    monkeypatch.setattr(RawHtmlStore, "put_raw_html", spy_put_raw_html)

    client = TestClient(app)
    response = client.post("/extract", json={"url": "http://blocked.example/x"})

    assert response.status_code == 200
    data = response.json()
    # Response reflects the rejection.
    assert data["fetcher_used"] == "rejected"
    assert data["extraction_confidence"] == 0.0
    assert data["topics"] == []
    assert any(e.startswith("persistence_rejected:captcha") for e in data["errors"])

    # DDB row was persisted (audit trail), but as a marker.
    assert len(persist_calls) == 1
    assert persist_calls[0]["fetcher_used"] == "rejected"
    assert persist_calls[0]["topics"] == []
    assert persist_calls[0]["extraction_confidence"] == 0.0
    # S3 raw HTML write skipped — no useful content to store.
    assert persist_calls[0]["s3_html_uri"] is None
    assert s3_put_calls == []
