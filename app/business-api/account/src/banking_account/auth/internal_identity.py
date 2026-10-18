"""Verification for short-lived internal MCP principals."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Annotated

from fastapi import Header, HTTPException, status


INVALID_IDENTITY_MESSAGE = "Invalid customer identity"


def get_customer_id(headers: dict[str, str]) -> str:
    """Return the verified customer ID from the MCP authorization header."""
    mode = os.environ.get("MCP_IDENTITY_MODE", "hmac")
    if mode == "oauth":
        from banking_shared.oauth_identity import resolve_oauth_customer_id

        return resolve_oauth_customer_id(
            headers.get("authorization", ""),
            os.environ.get("OAUTH_ISSUER", ""),
            os.environ.get("OAUTH_AUDIENCE", ""),
            os.environ.get("OAUTH_JWKS", ""),
        )
    if mode != "hmac":
        raise RuntimeError("Unsupported MCP identity mode")
    secret = os.environ.get("INTERNAL_IDENTITY_SECRET")
    if not secret:
        raise RuntimeError("Internal identity verification is not configured")

    authorization = headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise PermissionError("Authenticated customer identity is required")

    try:
        version, encoded_payload, signature = token.split(".")
    except ValueError as error:
        raise PermissionError(INVALID_IDENTITY_MESSAGE) from error
    if version != "v1":
        raise PermissionError(INVALID_IDENTITY_MESSAGE)

    expected = hmac.new(secret.encode(), encoded_payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise PermissionError(INVALID_IDENTITY_MESSAGE)

    try:
        padding = "=" * (-len(encoded_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload + padding))
        customer_id = payload["customer_id"]
        expires_at = payload["exp"]
    except (KeyError, TypeError, ValueError) as error:
        raise PermissionError(INVALID_IDENTITY_MESSAGE) from error
    if not isinstance(customer_id, str) or not customer_id or not isinstance(expires_at, int):
        raise PermissionError(INVALID_IDENTITY_MESSAGE)
    if expires_at <= int(time.time()):
        raise PermissionError("Customer identity has expired")
    return customer_id


def get_http_customer_id(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Resolve the internal principal for FastAPI routes."""
    try:
        return get_customer_id({"authorization": authorization or ""})
    except (PermissionError, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error