"""API key authentication dependency.

When `API_KEY` env var is empty/unset, the dependency is a no-op (local dev mode).
When `API_KEY` is set, requests must send `Authorization: Bearer <key>` matching it.
"""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request, status


def require_api_key(request: Request) -> None:
    """FastAPI dependency: enforce Bearer token when API_KEY env var is set.

    Raises 401 if the header is missing or doesn't match.
    Returns None on success (dependency injection pattern; no return value needed).
    """
    expected = os.getenv("API_KEY", "").strip()
    if not expected:
        return  # local dev / tests: no auth required

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization header (expected: 'Bearer <token>')",
            headers={"WWW-Authenticate": "Bearer"},
        )
    provided = auth_header[len("bearer ") :].strip()
    # Constant-time compare to prevent timing attacks
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
