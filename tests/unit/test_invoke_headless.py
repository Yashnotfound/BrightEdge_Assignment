"""Tests for ``crawler.fetcher.headless.invoke_headless``.

These tests assert the function surfaces Lambda function errors as
exceptions instead of returning the error-shaped payload to callers.
"""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

import pytest


def _payload_stream(data: bytes) -> io.BytesIO:
    """Mimic the StreamingBody returned under response['Payload']."""
    return io.BytesIO(data)


def test_invoke_headless_raises_on_function_error(monkeypatch):
    """Lambda 200 with FunctionError must raise RuntimeError with an excerpt."""
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")

    error_payload = json.dumps(
        {
            "errorType": "TimeoutError",
            "errorMessage": "page.goto: timeout 30000ms exceeded",
            "stackTrace": ["...frame..."],
        }
    ).encode("utf-8")

    fake_client = MagicMock()
    fake_client.invoke.return_value = {
        "StatusCode": 200,
        "FunctionError": "Unhandled",
        "Payload": _payload_stream(error_payload),
    }
    monkeypatch.setattr("boto3.client", MagicMock(return_value=fake_client))

    from crawler.fetcher.headless import invoke_headless

    with pytest.raises(RuntimeError) as excinfo:
        invoke_headless("http://example.com", persist=True)

    msg = str(excinfo.value)
    assert "Unhandled" in msg
    # Useful excerpt of the payload makes its way into the message.
    assert "TimeoutError" in msg or "timeout" in msg


def test_invoke_headless_returns_payload_on_success(monkeypatch):
    """Regression guard: no FunctionError → return deserialized payload."""
    monkeypatch.setenv("HEADLESS_FUNCTION_NAME", "fake-headless-fn")

    happy_payload = json.dumps(
        {
            "url": "http://example.com",
            "url_hash": "a" * 64,
            "fetcher_used": "headless",
            "http_status": 200,
        }
    ).encode("utf-8")

    fake_client = MagicMock()
    fake_client.invoke.return_value = {
        "StatusCode": 200,
        "Payload": _payload_stream(happy_payload),
    }
    monkeypatch.setattr("boto3.client", MagicMock(return_value=fake_client))

    from crawler.fetcher.headless import invoke_headless

    result = invoke_headless("http://example.com", persist=True)
    assert isinstance(result, dict)
    assert result["url_hash"] == "a" * 64
    assert result["fetcher_used"] == "headless"
