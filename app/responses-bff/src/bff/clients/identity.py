"""Allowlisted HTTP adapter for the identity service."""

from __future__ import annotations

import logging
import ssl
from typing import Any

import httpx
from fastapi import HTTPException, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("uvicorn.error.bff.identity")


def unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail={"code": "SERVICE_UNAVAILABLE"})


async def auth_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    internal_secret: str | None = None,
    preserve_status: bool = False,
) -> Any:
    """Return identity-service JSON without exposing upstream error details."""
    if path == "/internal/introspect" and not internal_secret:
        raise unavailable()
    headers = {}
    if internal_secret:
        headers["Authorization"] = f"Bearer {internal_secret}"
    elif token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = await client.request(method, path, json=payload, headers=headers)
    except httpx.RequestError as exc:
        failure = "timeout" if isinstance(exc, httpx.TimeoutException) else "transport"
        cause: BaseException | None = exc
        for _ in range(8):
            if cause is None:
                break
            if isinstance(cause, ssl.SSLCertVerificationError):
                failure = "tls_certificate_verification"
                break
            cause = cause.__cause__ or cause.__context__
        logger.error("Identity connection failed: category=%s", failure)
        raise unavailable() from None
    if path in {"/oauth/authorize", "/oauth/token", "/internal/oauth/introspect"}:
        if not 200 <= response.status_code < 300:
            logger.error("BFF OAuth upstream rejected: status=%s", response.status_code)
    if response.status_code >= 500:
        raise unavailable()
    if path == "/internal/introspect" and response.status_code not in (200, 401):
        raise unavailable()
    if response.status_code >= 400:
        codes = {401: "AUTH_REQUIRED", 403: "ACCESS_DENIED", 404: "NOT_FOUND",
                 409: "CONFLICT", 422: "INVALID_REQUEST", 400: "INVALID_REQUEST",
                 429: "RATE_LIMITED"}
        code = "INVALID_CREDENTIALS" if path == "/auth/login" and response.status_code == 401 else codes.get(response.status_code, "INVALID_REQUEST")
        raise HTTPException(
            status_code=response.status_code if response.status_code in codes else 503,
            detail={"code": code},
            headers={"WWW-Authenticate": "Bearer"} if response.status_code == 401 else None,
        )
    if not 200 <= response.status_code < 300:
        raise unavailable()
    if response.status_code == 204 and preserve_status:
        return Response(status_code=204)
    try:
        data = response.json()
    except ValueError:
        raise unavailable() from None
    return JSONResponse(content=data, status_code=response.status_code) if preserve_status else data
