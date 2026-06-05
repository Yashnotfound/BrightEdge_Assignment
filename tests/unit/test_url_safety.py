"""Tests for the SSRF guard in `crawler.fetcher.url_safety`."""
from __future__ import annotations

import socket

import pytest

from crawler.fetcher.url_safety import UnsafeUrlError, validate_url


# --- Public URLs accepted -------------------------------------------------

def test_validate_public_https_url_passes(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo({"example.com": ["93.184.216.34"]}),
    )
    # No exception → pass
    validate_url("https://example.com/path?q=1")


def test_validate_public_http_url_passes(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo({"www.cnn.com": ["151.101.193.67"]}),
    )
    validate_url("http://www.cnn.com/article")


def test_mixed_case_scheme_and_host_normalize(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo({"example.com": ["93.184.216.34"]}),
    )
    validate_url("HTTP://Example.COM/")


# --- Scheme rejections ----------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://evil.com/_",
    "dict://example.com:11211/stat",
    "javascript:alert(1)",
    "ftp://example.com/",
    "data:text/html,<script>alert(1)</script>",
])
def test_non_http_schemes_rejected(url):
    with pytest.raises(UnsafeUrlError):
        validate_url(url)


# --- IPv4 blocked ranges --------------------------------------------------

@pytest.mark.parametrize("ip", [
    "169.254.169.254",   # AWS instance metadata
    "127.0.0.1",          # loopback
    "127.255.255.254",    # entire 127/8
    "10.0.0.1",           # RFC1918
    "10.255.255.254",     # entire 10/8
    "172.16.0.1",         # RFC1918
    "172.31.255.254",     # high end of 172.16/12
    "192.168.1.1",        # RFC1918
    "0.0.0.0",            # unspecified
    "100.64.0.1",         # CGNAT
    "224.0.0.1",          # multicast
    "169.254.0.1",        # link-local
    "192.0.2.1",          # TEST-NET-1 documentation
    "198.51.100.1",       # TEST-NET-2 documentation
    "203.0.113.1",        # TEST-NET-3 documentation
    "198.19.0.1",         # benchmarking
    "240.0.0.1",          # reserved for future use
])
def test_ipv4_blocked_ranges_rejected(ip, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"target.example": [ip]}))
    with pytest.raises(UnsafeUrlError):
        validate_url(f"http://target.example/")


def test_ipv4_literal_in_url_rejected_without_dns(monkeypatch):
    # When the host is already an IP literal, no DNS lookup is needed.
    # getaddrinfo still gets called but should return the literal back.
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo({"169.254.169.254": ["169.254.169.254"]}),
    )
    with pytest.raises(UnsafeUrlError):
        validate_url("http://169.254.169.254/latest/meta-data/")


def test_ipv6_literal_in_url_rejected(monkeypatch):
    """IPv6 literals in URLs are wrapped in square brackets per RFC 3986.
    urlparse correctly strips the brackets; we just need to confirm the
    end-to-end path validates the bare address against the IPv6 block list.
    """
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo({"::1": ["::1"]}, family=socket.AF_INET6),
    )
    with pytest.raises(UnsafeUrlError):
        validate_url("http://[::1]/")


# --- IPv6 blocked ranges --------------------------------------------------

@pytest.mark.parametrize("ip", [
    "::1",                # loopback
    "fe80::1",            # link-local
    "fc00::1",            # ULA
    "fd00::1",            # ULA upper half
    "2001:db8::1",        # documentation prefix
    "64:ff9b::1",         # NAT64
    "100::1",             # discard prefix
    "ff02::1",            # multicast
])
def test_ipv6_blocked_ranges_rejected(ip, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"v6.example": [ip]}, family=socket.AF_INET6))
    with pytest.raises(UnsafeUrlError):
        validate_url("http://v6.example/")


def test_ipv4_mapped_ipv6_rejected(monkeypatch):
    # `::ffff:127.0.0.1` is IPv4-mapped IPv6 — must reject because it routes to
    # IPv4 loopback. The `ipaddress` stdlib handles the mapping automatically.
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo({"mapped.example": ["::ffff:127.0.0.1"]}, family=socket.AF_INET6),
    )
    with pytest.raises(UnsafeUrlError):
        validate_url("http://mapped.example/")


# --- DNS rebinding case ---------------------------------------------------

def test_dns_rebinding_to_loopback_rejected(monkeypatch):
    """A hostname under attacker control that resolves to 127.0.0.1.

    Pure string-equality checks (`if host == "localhost"`) would miss this.
    Resolving via getaddrinfo and checking the IP catches it.
    """
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo({"evil.attacker.example": ["127.0.0.1"]}),
    )
    with pytest.raises(UnsafeUrlError):
        validate_url("http://evil.attacker.example/")


def test_localhost_hostname_rejected(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo({"localhost": ["127.0.0.1"]}),
    )
    with pytest.raises(UnsafeUrlError):
        validate_url("http://localhost:8080/admin")


def test_multi_address_dns_any_blocked_address_rejects(monkeypatch):
    """If a hostname resolves to multiple IPs and ANY one is blocked, reject.

    A naive defense that only checks the first resolved IP could be bypassed
    by a hostname that resolves to both a public IP and a blocked IP — the
    attacker arranges DNS order and the second request hits the blocked one.
    """
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo({"mixed.example": ["93.184.216.34", "127.0.0.1"]}),
    )
    with pytest.raises(UnsafeUrlError):
        validate_url("http://mixed.example/")


# --- Malformed input ------------------------------------------------------

@pytest.mark.parametrize("url", [
    "",
    "not-a-url",
    "http://",
    "http:///path",
    "://example.com",
    "https:/example.com",  # missing the second slash → host parses empty
])
def test_malformed_inputs_rejected(url):
    with pytest.raises(UnsafeUrlError):
        validate_url(url)


# --- DNS failure handling -------------------------------------------------

def test_dns_failure_rejects(monkeypatch):
    """If the host doesn't resolve at all, we treat it as unsafe rather than
    letting the fetcher hit a generic DNS error later. This is conservative
    but defensible — an unresolvable host can't be a legitimate crawl target."""
    def _fail(*args, **kwargs):
        raise socket.gaierror("nodename nor servname provided, or not known")
    monkeypatch.setattr(socket, "getaddrinfo", _fail)
    with pytest.raises(UnsafeUrlError):
        validate_url("http://nonexistent.invalid/")


# --- Test helpers ---------------------------------------------------------

def _fake_getaddrinfo(host_to_ips: dict[str, list[str]], family: int = socket.AF_INET):
    """Build a fake `socket.getaddrinfo` that returns canned IPs for known hosts.

    The real signature returns
    `[(family, type, proto, canonname, sockaddr), ...]` where `sockaddr[0]` is
    the IP string. We mimic that shape so the production code can read it
    identically.
    """
    def _fake(host, port, *args, **kwargs):
        if host not in host_to_ips:
            raise socket.gaierror(f"unexpected host in test: {host}")
        results = []
        for ip in host_to_ips[host]:
            sockaddr = (ip, port or 0) if family == socket.AF_INET else (ip, port or 0, 0, 0)
            results.append((family, socket.SOCK_STREAM, 0, "", sockaddr))
        return results
    return _fake
