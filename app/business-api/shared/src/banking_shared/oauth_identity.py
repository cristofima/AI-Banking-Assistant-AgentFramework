"""Pinned public-JWKS OAuth validation; never performs identity introspection."""
from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from banking_shared.oauth_jwt import validate_access_token

logger = logging.getLogger("uvicorn.error.mcp.oauth")


def resolve_oauth_customer_id(
    authorization: str, issuer: str, audience: str, public_keys: str,
) -> str:
    """Fail closed with no network, shared secret or alternate bearer scheme."""
    target = urlsplit(issuer)
    if (target.scheme != "https" or not target.hostname or target.username
            or target.password or target.query or target.fragment or issuer.endswith("/")
            or not audience.strip() or not public_keys or len(public_keys) > 65536):
        logger.error("MCP OAuth verification rejected: category=configuration")
        raise RuntimeError("OAuth identity verification is not configured")
    try:
        jwks = json.loads(public_keys)
        if not isinstance(jwks, dict):
            raise ValueError("Invalid JWKS")
    except (ValueError, TypeError):
        logger.error("MCP OAuth verification rejected: category=jwks")
        raise RuntimeError("OAuth identity verification is not configured") from None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 8192:
        logger.error("MCP OAuth verification rejected: category=bearer")
        raise PermissionError("Authenticated customer identity is required")
    try:
        return validate_access_token(token, jwks, issuer, audience)["customer_id"]
    except PermissionError:
        logger.error("MCP OAuth verification rejected: category=token_validation")
        raise
