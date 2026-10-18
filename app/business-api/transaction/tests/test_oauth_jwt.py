"""Transaction verifies customer MCP grants before reads or dispute mutations."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

from banking_shared.oauth_jwt import public_jwks
from banking_transaction import mcp_tools
from banking_transaction.auth.internal_identity import get_customer_id


@pytest.fixture
def signing_key() -> str:
    return RSAKey.generate_key(2048, private=True).as_pem(private=True).decode()


@pytest.fixture
def oauth_mode(monkeypatch: pytest.MonkeyPatch, signing_key: str) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "oauth")
    monkeypatch.setenv("OAUTH_ISSUER", "https://identity.synthetic.invalid")
    monkeypatch.setenv("OAUTH_AUDIENCE", "banking-mcp")
    monkeypatch.setenv("OAUTH_JWKS", json.dumps(public_jwks(signing_key, "synthetic")))
    monkeypatch.delenv("AUTH_INTERNAL_SECRET", raising=False)
    monkeypatch.delenv("AUTH_USERS_ENDPOINT", raising=False)


def access_token(signing_key: str, changes: dict[str, Any] | None = None) -> str:
    now = int(time.time())
    return jwt.encode({"alg": "RS256", "typ": "at+jwt", "kid": "synthetic"}, {
        "iss": "https://identity.synthetic.invalid", "aud": "banking-mcp",
        "sub": "user", "customer_id": "owned-customer", "email": "test@example.com",
        "locale": "es", "role": "customer", "identity_version": 1,
        "scope": "mcp.access", "client_id": "client", "jti": "unique",
        "iat": now, "exp": now + 3600, **(changes or {}),
    }, RSAKey.import_key(signing_key), algorithms=["RS256"])


@pytest.fixture
def oauth_token(oauth_mode: None, signing_key: str) -> str:
    return access_token(signing_key)


def test_transaction_locally_validates_jwt_for_reads_and_mutations(oauth_token: str) -> None:
    headers = {"authorization": "Bearer " + oauth_token}
    assert get_customer_id(headers) == "owned-customer"
    assert get_customer_id(headers, mutation=True) == "owned-customer"


async def test_verified_customer_mutations_dispatch(
    oauth_token: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Mock()
    case.model_dump.return_value = {"caseId": "synthetic-case"}
    accept = Mock(return_value=case)
    approval = Mock(return_value={"status": "IN_REVIEW"})
    monkeypatch.setattr(mcp_tools, "dispute_service", SimpleNamespace(
        accept_transaction_dispute=accept, respond_to_approval=approval,
    ))
    headers = {"authorization": "Bearer " + oauth_token}
    assert await mcp_tools.report_transaction_dispute("proposal", headers) == {
        "caseId": "synthetic-case",
    }
    accept.assert_called_once_with("proposal", "owned-customer")
    for approved in (True, False):
        assert mcp_tools.respond_to_dispute_approval("case", approved, headers) == {
            "status": "IN_REVIEW",
        }
        approval.assert_called_with("case", "owned-customer", approved)
    assert approval.call_count == 2


@pytest.mark.parametrize("changes", [
    {"scope": "banking.read"}, {"scope": "mcp.access banking.read"},
    {"role": "operator"}, {"role": "admin"}, {"customer_id": ""},
    {"aud": "foreign"}, {"iss": "https://foreign.invalid"}, {"exp": 1},
    {"identity_version": 0},
])
async def test_invalid_signed_grants_never_dispatch_mutations(
    oauth_mode: None, signing_key: str, monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, Any],
) -> None:
    headers = {"authorization": "Bearer " + access_token(signing_key, changes)}
    accept = Mock()
    approval = Mock()
    monkeypatch.setattr(mcp_tools, "dispute_service", SimpleNamespace(
        accept_transaction_dispute=accept, respond_to_approval=approval,
    ))
    with pytest.raises(PermissionError):
        get_customer_id(headers)
    with pytest.raises(PermissionError):
        await mcp_tools.report_transaction_dispute("proposal", headers)
    for approved in (True, False):
        with pytest.raises(PermissionError):
            mcp_tools.respond_to_dispute_approval("case", approved, headers)
    accept.assert_not_called()
    approval.assert_not_called()


def test_transaction_oauth_never_accepts_hmac_fallback(
    oauth_token: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", "synthetic-secret")
    with pytest.raises(PermissionError):
        get_customer_id({"authorization": "Bearer v1.payload.signature"})


def test_transaction_rejects_jwt_for_other_audience(
    oauth_token: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OAUTH_AUDIENCE", "other-mcp")
    with pytest.raises(PermissionError):
        get_customer_id({"authorization": "Bearer " + oauth_token})
