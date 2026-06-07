"""SSRF guard — reject URLs that target internal or special-use addresses.

Three checks: (1) the URL parses with a recognised http(s) scheme and a
non-empty host; (2) the host resolves via DNS to at least one IP; (3) every
resolved IP falls outside the blocked ranges (RFC1918, loopback, link-local,
IPv6 ULA, etc.).

Used at three points:
- Pydantic field validators on `ExtractRequest.url` / `BatchRequest.urls`
  (route-layer enforcement)
- `crawler.fetcher.static.fetch` at the start of each attempt and at each
  redirect hop (defeats redirect-based bypass)
- `crawler.workers.headless_worker._fetch_headless` at the start of
  navigation, with a Playwright route handler aborting any in-page request
  to a blocked host

The function is sync (DNS resolution is fast; running it in an executor
adds complexity without a measurable win at PoC scale).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """Raised when a URL fails the SSRF safety check.

    Subclasses ``ValueError`` so Pydantic field validators can re-raise it
    and FastAPI returns 422 with the bad value highlighted.
    """


_ALLOWED_SCHEMES = frozenset({"http", "https"})

# IPv4 ranges blocked because they're not legitimate crawl targets. Each
# entry is annotated with WHY — so an operator considering removing one
# knows what they're opting back into.
_BLOCKED_IPV4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(cidr)
    for cidr in (
        "0.0.0.0/8",          # "this network" — RFC 1122
        "10.0.0.0/8",         # RFC 1918 private
        "100.64.0.0/10",      # CGNAT shared address space (RFC 6598)
        "127.0.0.0/8",        # loopback
        "169.254.0.0/16",     # link-local — INCLUDES AWS metadata at .169.254
        "172.16.0.0/12",      # RFC 1918 private
        "192.0.0.0/24",       # IETF protocol assignments
        "192.0.2.0/24",       # TEST-NET-1 documentation (RFC 5737)
        "192.168.0.0/16",     # RFC 1918 private
        "198.18.0.0/15",      # benchmarking
        "198.51.100.0/24",    # TEST-NET-2 documentation (RFC 5737)
        "203.0.113.0/24",     # TEST-NET-3 documentation (RFC 5737)
        "224.0.0.0/4",        # multicast
        "240.0.0.0/4",        # reserved for future use
        "255.255.255.255/32", # limited broadcast
    )
)

_BLOCKED_IPV6_NETWORKS: tuple[ipaddress.IPv6Network, ...] = tuple(
    ipaddress.IPv6Network(cidr)
    for cidr in (
        "::/128",            # unspecified
        "::1/128",           # loopback
        "64:ff9b::/96",      # NAT64 (RFC 6146) — would forward IPv4 traffic
        "100::/64",          # discard prefix (RFC 6666)
        "2001:db8::/32",     # documentation prefix (RFC 3849)
        "fc00::/7",          # unique local addresses (RFC 4193)
        "fe80::/10",         # link-local
        "ff00::/8",          # multicast
    )
)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if ``ip`` falls in any of the blocked ranges.

    IPv4-mapped IPv6 addresses (``::ffff:x.x.x.x``) route to IPv4 so we
    check the mapped form against the IPv4 rules — otherwise an attacker
    could bypass by submitting ``::ffff:127.0.0.1`` to reach loopback.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return any(ip.ipv4_mapped in net for net in _BLOCKED_IPV4_NETWORKS)
        return any(ip in net for net in _BLOCKED_IPV6_NETWORKS)
    return any(ip in net for net in _BLOCKED_IPV4_NETWORKS)


def validate_url(url: str) -> None:
    """Raise ``UnsafeUrlError`` if ``url`` is not safe to fetch.

    Safe means: http(s) scheme, parseable host, resolves via DNS, and every
    resolved IP is outside the blocked ranges. The any-blocked-IP rule
    defeats DNS-rebinding attacks where a hostname resolves to a mix of
    public and private addresses.
    """
    if not isinstance(url, str) or not url:
        raise UnsafeUrlError(f"url must be a non-empty string, got: {url!r}")

    parts = urlparse(url)

    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            f"unsupported scheme {scheme!r} in url: {url!r} (only http/https allowed)"
        )

    # `urlparse` returns `hostname` lowercased and stripped of the port; we
    # still defensively `.lower()` in case a future Python changes that.
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeUrlError(f"missing host in url: {url!r}")

    try:
        results = socket.getaddrinfo(
            host, parts.port or 0, type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"dns resolution failed for host {host!r}: {exc}") from exc

    if not results:
        raise UnsafeUrlError(f"dns returned no addresses for host {host!r}")

    for _family, _socktype, _proto, _canonname, sockaddr in results:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise UnsafeUrlError(
                f"could not parse resolved address {ip_str!r} for host {host!r}: {exc}"
            ) from exc
        if _is_blocked_ip(ip):
            raise UnsafeUrlError(
                f"url {url!r} resolves to blocked address {ip} "
                f"(host={host!r})"
            )
