"""Tests for the headless worker handler.

These tests deliberately do NOT exercise real Playwright — `_fetch_headless`
is monkeypatched with an async stub. The intent is to verify that:

  1. `handler()` wires {url, persist} → fetch → pipeline → optional persist.
  2. `_persist_headless()` sequences S3 + DynamoDB writes correctly.
"""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws_resources():
    """Provision DDB + S3 to mirror prod, like tests/unit/test_static_worker.py."""
    with mock_aws():
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
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="raw")
        yield


def test_handler_runs_pipeline_and_returns_result(aws_resources, monkeypatch):
    """handler() takes {url, persist}, runs the pipeline, persists when asked."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")

    async def fake_fetch(url: str):
        # Minimal but well-formed HTML so process_html produces a real result.
        return (
            "<html><head><title>Example</title></head>"
            "<body><p>" + ("hello world " * 50) + "</p></body></html>",
            200,
        )

    from crawler.workers import headless_worker
    monkeypatch.setattr(headless_worker, "_fetch_headless", fake_fetch)

    result = headless_worker.handler(
        {"url": "http://example.com", "persist": True},
        context=None,
    )

    assert result["url"] == "http://example.com"
    assert result["fetcher_used"] == "headless"
    assert result["http_status"] == 200
    assert result["title"] == "Example"

    # Confirm the row landed in DDB.
    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    items = pages.scan()["Items"]
    assert len(items) == 1
    assert items[0]["title"] == "Example"

    # And the raw HTML landed in S3.
    s3 = boto3.client("s3", region_name="us-east-1")
    objs = s3.list_objects_v2(Bucket="raw")
    assert objs["KeyCount"] >= 1


def test_handler_skips_persist_when_persist_false(aws_resources, monkeypatch):
    """persist=False short-circuits S3 + DDB writes but still returns a result."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")

    async def fake_fetch(url: str):
        return "<html><head><title>X</title></head><body>x</body></html>", 200

    from crawler.workers import headless_worker
    monkeypatch.setattr(headless_worker, "_fetch_headless", fake_fetch)

    result = headless_worker.handler(
        {"url": "http://example.com", "persist": False},
        context=None,
    )

    assert result["fetcher_used"] == "headless"

    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    assert pages.scan()["Items"] == []
    s3 = boto3.client("s3", region_name="us-east-1")
    assert s3.list_objects_v2(Bucket="raw").get("KeyCount", 0) == 0


def test_handler_default_persist_is_true(aws_resources, monkeypatch):
    """Omitting `persist` defaults to True (matches the route's contract)."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")

    async def fake_fetch(url: str):
        return (
            "<html><head><title>Default</title></head>"
            "<body><p>" + ("word " * 60) + "</p></body></html>",
            200,
        )

    from crawler.workers import headless_worker
    monkeypatch.setattr(headless_worker, "_fetch_headless", fake_fetch)

    headless_worker.handler({"url": "http://example.com"}, context=None)

    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    assert len(pages.scan()["Items"]) == 1


def test_persist_skips_when_settings_unconfigured(monkeypatch):
    """Without PAGES_TABLE / RAW_HTML_BUCKET, _persist_headless is a no-op.

    This is the local-dev fallback referenced in the function's docstring.
    """
    import asyncio
    from datetime import UTC, datetime

    # Set to empty string explicitly — load_settings() reads via
    # `os.getenv(..., "")` so an unset OR empty value both produce ""
    # which is falsy and short-circuits the persistence path.
    monkeypatch.setenv("PAGES_TABLE", "")
    monkeypatch.setenv("RAW_HTML_BUCKET", "")

    from crawler.api.schemas import ExtractResult
    from crawler.workers import headless_worker

    fake_result = ExtractResult(
        url="http://example.com",
        url_hash="a" * 64,
        fetched_at=datetime.now(UTC),
        fetcher_used="headless",
        http_status=200,
    )
    # Should not raise — no AWS clients are touched because settings are empty.
    asyncio.run(headless_worker._persist_headless(fake_result, "<html></html>"))


def test_handler_persists_rejected_marker_for_captcha_body(aws_resources, monkeypatch):
    """When the headless fetch lands on a captcha-fingerprint page (e.g. the
    Cloudflare 'Just a Moment' interstitial), the persist gate must replace
    the result with a rejected marker, skip the S3 raw-HTML write, and still
    write the DDB row so the audit trail remains visible via /pages."""
    monkeypatch.setenv("PAGES_TABLE", "brightedge-pages")
    monkeypatch.setenv("RAW_HTML_BUCKET", "raw")

    async def fake_fetch(url: str):
        return (
            "<html><head><title>Just a Moment</title></head>"
            "<body>Please complete the CAPTCHA to continue.</body></html>",
            200,
        )

    from crawler.workers import headless_worker
    monkeypatch.setattr(headless_worker, "_fetch_headless", fake_fetch)

    result = headless_worker.handler(
        {"url": "http://blocked.example/", "persist": True},
        context=None,
    )

    # The returned (and persisted) result is the rejected marker.
    assert result["fetcher_used"] == "rejected"
    assert result["extraction_confidence"] == 0.0
    assert result["topics"] == []

    pages = boto3.resource("dynamodb", region_name="us-east-1").Table("brightedge-pages")
    items = pages.scan()["Items"]
    assert len(items) == 1
    assert items[0]["fetcher_used"] == "rejected"

    # No raw HTML in S3.
    s3 = boto3.client("s3", region_name="us-east-1")
    assert s3.list_objects_v2(Bucket="raw").get("KeyCount", 0) == 0
