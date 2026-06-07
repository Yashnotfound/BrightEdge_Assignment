"""Shared test fixtures."""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch):
    """Stub `socket.getaddrinfo` so the SSRF guard never hits real DNS.

    `crawler.fetcher.url_safety.validate_url` resolves the host to check it
    against the blocked-range list. That guard runs at the entry of
    `fetcher.static.fetch`, inside the headless worker, and in the
    `ExtractRequest` / `BatchRequest` Pydantic validators — so without this
    stub, any unit test that touches those paths would make a live DNS query
    and become network-dependent / flaky in sandboxed CI.

    The default maps every host to a benign public IP (example.com's), so
    validation passes. SSRF tests that need a blocked target override this
    with their own `monkeypatch.setattr(socket, "getaddrinfo", ...)` inside
    the test body — that later setattr wins for the duration of the test and
    reverts afterward.
    """
    def _benign(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port or 0))]
    monkeypatch.setattr(socket, "getaddrinfo", _benign)


@pytest.fixture
def fixture_html() -> dict[str, str]:
    """Return saved HTML for the three test URLs as a dict keyed by short name."""
    files = {
        "amazon": FIXTURES_DIR / "amazon_toaster.html",
        "rei": FIXTURES_DIR / "rei_outdoors.html",
        "cnn": FIXTURES_DIR / "cnn_tech.html",
    }
    return {key: path.read_text(encoding="utf-8") for key, path in files.items() if path.exists()}
