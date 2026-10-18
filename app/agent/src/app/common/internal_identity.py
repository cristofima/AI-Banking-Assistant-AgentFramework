"""Validation and delegation for internal banking identities."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from azure.ai.agentserver.core import get_request_context


@dataclass(frozen=True)
class InternalPrincipal:
    """Verified identity propagated by the BFF."""

    sub: str
    customer_id: str
    email: str | None = None
    locale: str = "en"
    oauth_access_token: str | None = field(default=None, repr=False)
    oauth_expires_at: int | None = None


def resolve_response_locale(value: object) -> str:
    """Allow only supported stored locales; never inject arbitrary claim text."""
    return value if isinstance(value, str) and value in {"es", "pt", "en"} else "en"


def get_internal_principal(secret: str) -> InternalPrincipal:
    """Resolve the signed principal from the current request."""
    identity = get_request_context().user_id
    if not identity:
        raise RuntimeError("Authenticated request identity is required")
    return _verify_envelope(identity, secret)


def create_mcp_authorization(secret: str, lifetime_seconds: int = 60) -> str:
    """Create a short-lived MCP bearer token for the current request identity."""
    principal = get_internal_principal(secret)
    mode = os.environ.get("MCP_IDENTITY_MODE", "hmac")
    if mode == "oauth":
        token = principal.oauth_access_token
        expires_at = principal.oauth_expires_at
        if (
            not isinstance(token, str) or not token or len(token) > 16384
            or any(character.isspace() or ord(character) < 32 for character in token)
            or type(expires_at) is not int or expires_at <= int(time.time())
        ):
            raise ValueError("A current customer OAuth grant is required")
        return f"Bearer {token}"
    if mode != "hmac":
        raise RuntimeError("Unsupported MCP identity mode")
    payload = {
        "customer_id": principal.customer_id,
        "exp": int(time.time()) + lifetime_seconds,
        "sub": principal.sub,
    }
    return f"Bearer {_sign_payload(payload, secret)}"


def mcp_header_provider(secret: str):
    """Build a request-scoped Agent Framework MCP header provider."""

    def provide_headers(_: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": create_mcp_authorization(secret)}

    return provide_headers


def _verify_envelope(token: str, secret: str) -> InternalPrincipal:
    try:
        version, encoded_payload, signature = token.split(".")
    except ValueError as error:
        raise ValueError("Invalid internal identity") from error
    if version != "v1":
        raise ValueError("Unsupported internal identity version")

    expected = hmac.new(secret.encode(), encoded_payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid internal identity signature")

    try:
        padding = "=" * (-len(encoded_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload + padding))
        principal = InternalPrincipal(
            sub=payload["sub"],
            customer_id=payload["customer_id"],
            email=payload.get("email"),
            locale=resolve_response_locale(payload.get("locale")),
            oauth_access_token=payload.get("oauth_access_token"),
            oauth_expires_at=payload.get("oauth_expires_at"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Invalid internal identity payload") from error
    if not principal.sub or not principal.customer_id:
        raise ValueError("Internal identity claims must not be empty")
    if principal.email is not None and (
        not isinstance(principal.email, str) or not principal.email.strip()
    ):
        raise ValueError("Invalid internal identity email")
    return principal


def _sign_payload(payload: dict[str, Any], secret: str) -> str:
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    encoded_payload = base64.urlsafe_b64encode(serialized).rstrip(b"=").decode()
    signature = hmac.new(secret.encode(), encoded_payload.encode(), hashlib.sha256).hexdigest()
    return f"v1.{encoded_payload}.{signature}"