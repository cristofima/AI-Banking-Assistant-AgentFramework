"""Synthetic identity fixtures shared by the Account and Transaction test suites."""

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

JWT_SECRET = "synthetic-signing-secret-at-least-32-bytes"
INTERNAL_IDENTITY_SECRET = "test-internal-identity-secret-32-bytes"


def customer_claims() -> dict[str, Any]:
    return {"sub": "user-1", "customer_id": "customer-1", "email": "demo@example.com",
            "locale": "es", "role": "customer", "identity_version": 1,
            "iss": "home-banking-api", "aud": "home-banking-web",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}


def set_jwt_environment(monkeypatch: "pytest.MonkeyPatch") -> None:
    for name, value in {
        "JWT_SECRET_KEY": JWT_SECRET,
        "AUTH_INTERNAL_SECRET": "synthetic-internal-secret",
        "JWT_ISSUER": "home-banking-api",
        "JWT_AUDIENCE": "home-banking-web",
    }.items():
        monkeypatch.setenv(name, value)


def internal_authorization(expires_at: int) -> str:
    payload = json.dumps(
        {"customer_id": "local-customer", "exp": expires_at, "sub": "local-user"},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    signature = hmac.new(
        INTERNAL_IDENTITY_SECRET.encode(), encoded.encode(), hashlib.sha256,
    ).hexdigest()
    return f"Bearer v1.{encoded}.{signature}"
