"""Tests for Pydantic request schemas — focused on the SSRF guards.

We don't re-test the full `validate_url` matrix here (that's in
`test_url_safety.py`); we verify that the guard is actually wired into the
request models and that Pydantic surfaces failures as `ValidationError`
(which FastAPI converts to HTTP 422).
"""
from __future__ import annotations

import socket

import pytest
from pydantic import ValidationError

from crawler.api.schemas import BatchRequest, ExtractRequest


def _fake_dns(mapping: dict[str, list[str]]):
    """Build a fake `socket.getaddrinfo` keyed by host → list of IPs."""
    def _fake(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(f"unexpected host: {host}")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, port or 0))
            for ip in mapping[host]
        ]
    return _fake


# --- ExtractRequest -------------------------------------------------------

def test_extract_request_accepts_public_url(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"example.com": ["93.184.216.34"]}))
    req = ExtractRequest(url="https://example.com/")
    assert req.url == "https://example.com/"


def test_extract_request_rejects_metadata_url(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_dns({"169.254.169.254": ["169.254.169.254"]}),
    )
    with pytest.raises(ValidationError) as exc:
        ExtractRequest(url="http://169.254.169.254/latest/meta-data/")
    # The error message should mention the blocked address so an API caller
    # can see WHY we rejected.
    assert "blocked" in str(exc.value).lower()


def test_extract_request_rejects_file_scheme():
    with pytest.raises(ValidationError):
        ExtractRequest(url="file:///etc/passwd")


def test_extract_request_rejects_loopback_via_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"localhost": ["127.0.0.1"]}))
    with pytest.raises(ValidationError):
        ExtractRequest(url="http://localhost:8080/")


# --- BatchRequest ---------------------------------------------------------

def test_batch_request_accepts_all_public_urls(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_dns({
            "example.com": ["93.184.216.34"],
            "www.cnn.com": ["151.101.193.67"],
        }),
    )
    req = BatchRequest(urls=["https://example.com/", "https://www.cnn.com/"])
    assert len(req.urls) == 2


def test_batch_request_rejects_whole_batch_if_one_url_unsafe(monkeypatch):
    """Pydantic-idiomatic behavior: a single bad URL fails the whole list.

    Partial acceptance would require a custom failure-list response and
    isn't worth the complexity at this scale. The caller can dedupe and
    resubmit.
    """
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_dns({
            "example.com": ["93.184.216.34"],
            "10.0.0.1": ["10.0.0.1"],
        }),
    )
    with pytest.raises(ValidationError):
        BatchRequest(urls=["https://example.com/", "http://10.0.0.1/"])


def test_batch_request_empty_list_still_rejected_by_min_length():
    # Existing min_length=1 constraint stays in force alongside the new
    # safety check.
    with pytest.raises(ValidationError):
        BatchRequest(urls=[])


def test_batch_request_oversize_list_still_rejected_by_max_length(monkeypatch):
    # max_length=1001 sneaks past validate_url order? Confirm the
    # Pydantic-level max_length check fires before our element-level check.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns({"example.com": ["93.184.216.34"]}))
    with pytest.raises(ValidationError):
        BatchRequest(urls=["https://example.com/"] * 1001)
