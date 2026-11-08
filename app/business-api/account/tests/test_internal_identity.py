"""Verification tests for Account internal principals."""

import base64
import hashlib
import hmac
import json
import time

import pytest

from banking_shared.internal_identity import get_customer_id


TEST_SECRET = "test-internal-identity-secret-32-bytes"


def _authorization(expires_at: int) -> str:
    payload = json.dumps(
        {"customer_id": "local-customer", "exp": expires_at, "sub": "local-user"},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    signature = hmac.new(TEST_SECRET.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"Bearer v1.{encoded}.{signature}"


def test_valid_principal_returns_customer_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", TEST_SECRET)

    customer_id = get_customer_id(
        {"authorization": _authorization(int(time.time()) + 60)},
    )

    assert customer_id == "local-customer"


@pytest.mark.parametrize(
    ("authorization", "message"),
    [
        ("", "required"),
        ("Bearer v1.invalid.signature", "Invalid"),
        (_authorization(0), "expired"),
    ],
)
def test_invalid_principal_is_denied(
    monkeypatch: pytest.MonkeyPatch,
    authorization: str,
    message: str,
) -> None:
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", TEST_SECRET)

    with pytest.raises(PermissionError, match=message):
        get_customer_id({"authorization": authorization})