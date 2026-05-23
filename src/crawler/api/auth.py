"""API key authentication dependency.

When `API_KEY` env var is empty/unset, the dependency is a no-op (local dev mode).
When `API_KEY` is set, requests must send `Authorization: Bearer <key>` matching it.

Uses `HTTPBearer` from `fastapi.security` so the OpenAPI schema declares a
Bearer security scheme — Swagger UI at `/docs` then renders an Authorize
button that lets reviewers paste the token once instead of editing headers
on every "Try it out".
"""
from __future__ import annotations

import hmac
import os

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Paste the API key from the BrightEdge submission email.",
)


def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    """FastAPI dependency: enforce Bearer token when API_KEY env var is set.

    Raises 401 if the header is missing or doesn't match.
    Returns None on success.
    """
    expected = os.getenv("API_KEY", "").strip()
    if not expected:
        return  # local dev / tests: no auth required

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization header (expected: 'Bearer <token>')",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Constant-time compare to prevent timing attacks
    if not hmac.compare_digest(
        credentials.credentials.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
