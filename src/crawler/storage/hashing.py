"""URL normalization and SHA-256 hashing."""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    """Lowercase host, drop fragment, sort query, drop default ports."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    if ":" in netloc:
        host, _, port = netloc.rpartition(":")
        if port.isdigit() and _DEFAULT_PORTS.get(scheme) == int(port):
            netloc = host

    query_pairs = sorted(parse_qsl(parts.query, keep_blank_values=True))
    query = urlencode(query_pairs)

    return urlunsplit((scheme, netloc, parts.path, query, ""))


def url_hash(url: str) -> str:
    """SHA-256 hex digest of the normalized URL."""
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()
