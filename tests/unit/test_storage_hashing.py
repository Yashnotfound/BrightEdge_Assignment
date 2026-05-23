"""Tests for URL normalization and hashing."""
import pytest

from crawler.storage.hashing import normalize_url, url_hash


def test_normalize_lowercases_host():
    assert normalize_url("HTTP://Example.COM/Path") == "http://example.com/Path"


def test_normalize_strips_fragment():
    assert normalize_url("https://example.com/p#section") == "https://example.com/p"


def test_normalize_sorts_query_params():
    assert (
        normalize_url("https://example.com/p?b=2&a=1")
        == "https://example.com/p?a=1&b=2"
    )


def test_normalize_drops_default_port():
    assert normalize_url("https://example.com:443/p") == "https://example.com/p"
    assert normalize_url("http://example.com:80/p") == "http://example.com/p"


def test_url_hash_is_deterministic():
    a = url_hash("https://example.com/p?a=1&b=2")
    b = url_hash("https://example.com/p?b=2&a=1")
    assert a == b
    assert len(a) == 64  # SHA-256 hex


def test_url_hash_differs_for_different_urls():
    assert url_hash("https://example.com/a") != url_hash("https://example.com/b")
