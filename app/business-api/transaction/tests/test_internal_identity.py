"""Verification tests for Transaction internal principals."""

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

from fastmcp import Client
import pytest

from banking_shared import oauth_identity
from banking_transaction import mcp_tools

from banking_transaction.auth.internal_identity import get_customer_id


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


@pytest.mark.parametrize("mode", [None, "hmac"])
def test_hmac_mutation_preserves_verified_identity(
    monkeypatch: pytest.MonkeyPatch, mode: str | None,
) -> None:
    if mode is None:
        monkeypatch.delenv("MCP_IDENTITY_MODE", raising=False)
    else:
        monkeypatch.setenv("MCP_IDENTITY_MODE", mode)
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", TEST_SECRET)

    assert get_customer_id(
        {"authorization": _authorization(int(time.time()) + 60)}, mutation=True,
    ) == "local-customer"


@pytest.mark.parametrize("authorization", ["", "Bearer invalid", _authorization(0)])
def test_hmac_mutation_rejects_invalid_identity(
    monkeypatch: pytest.MonkeyPatch, authorization: str,
) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "hmac")
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", TEST_SECRET)

    with pytest.raises(PermissionError):
        get_customer_id({"authorization": authorization}, mutation=True)


def test_oauth_read_resolves_customer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "oauth")
    monkeypatch.setenv("OAUTH_ISSUER", "https://identity.example.test")
    monkeypatch.setenv("OAUTH_AUDIENCE", "banking-mcp")
    monkeypatch.setenv("OAUTH_JWKS", "synthetic-public-jwks")
    resolver = Mock(return_value="oauth-customer")
    monkeypatch.setattr(oauth_identity, "resolve_oauth_customer_id", resolver)

    assert get_customer_id({"authorization": "Bearer synthetic-oauth"}) == "oauth-customer"
    resolver.assert_called_once_with(
        "Bearer synthetic-oauth", "https://identity.example.test", "banking-mcp", "synthetic-public-jwks",
    )


def test_oauth_mutation_resolver_rejection_never_falls_back_to_hmac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "oauth")
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", TEST_SECRET)
    resolver = Mock(side_effect=PermissionError("Invalid OAuth grant"))
    monkeypatch.setattr(oauth_identity, "resolve_oauth_customer_id", resolver)

    with pytest.raises(PermissionError, match="Invalid OAuth grant"):
        get_customer_id(
            {"authorization": _authorization(int(time.time()) + 60)}, mutation=True,
        )
    assert resolver.call_count == 1


def test_unknown_mode_rejects_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "unsupported")
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", TEST_SECRET)

    with pytest.raises(RuntimeError, match="Unsupported MCP identity mode"):
        get_customer_id(
            {"authorization": _authorization(int(time.time()) + 60)}, mutation=True,
        )


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("reportTransactionDispute", {"preview_token": "synthetic-proposal"}),
        ("respondToDisputeApproval", {"case_id": "synthetic-case", "approved": True}),
        ("respondToDisputeApproval", {"case_id": "synthetic-case", "approved": False}),
    ],
)
async def test_sdk_invalid_oauth_mutations_never_call_service(
    monkeypatch: pytest.MonkeyPatch, tool_name: str, arguments: dict[str, str | bool],
) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "oauth")
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", TEST_SECRET)
    resolver = Mock(side_effect=PermissionError("Invalid OAuth grant"))
    monkeypatch.setattr(oauth_identity, "resolve_oauth_customer_id", resolver)
    accept = Mock()
    approval = Mock()
    monkeypatch.setattr(mcp_tools, "dispute_service", SimpleNamespace(
        accept_transaction_dispute=accept, respond_to_approval=approval,
    ))

    async with Client(mcp_tools.mcp) as client:
        result = await client.call_tool(tool_name, arguments, raise_on_error=False)

    assert result.is_error is True
    assert "Invalid OAuth grant" in result.content[0].text
    assert resolver.call_count == 1
    accept.assert_not_called()
    approval.assert_not_called()


async def test_hmac_mutation_tools_pass_verified_customer_to_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "hmac")
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", TEST_SECRET)
    headers = {"authorization": _authorization(int(time.time()) + 60)}
    case = Mock()
    case.model_dump.return_value = {"caseId": "synthetic-case"}
    accept = Mock(return_value=case)
    approval = Mock(return_value={"status": "IN_REVIEW"})
    monkeypatch.setattr(mcp_tools, "dispute_service", SimpleNamespace(
        accept_transaction_dispute=accept, respond_to_approval=approval,
    ))

    assert await mcp_tools.report_transaction_dispute("synthetic-proposal", headers) == {
        "caseId": "synthetic-case",
    }
    accept.assert_called_once_with("synthetic-proposal", "local-customer")
    for approved in (True, False):
        assert mcp_tools.respond_to_dispute_approval(
            "synthetic-case", approved, headers,
        ) == {"status": "IN_REVIEW"}
        approval.assert_called_with("synthetic-case", "local-customer", approved)
    assert approval.call_count == 2


async def test_oauth_mutation_tools_reject_invalid_grant_before_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "oauth")
    resolver = Mock(side_effect=PermissionError("Invalid OAuth grant"))
    monkeypatch.setattr(oauth_identity, "resolve_oauth_customer_id", resolver)
    accept = Mock()
    approval = Mock()
    monkeypatch.setattr(mcp_tools, "dispute_service", SimpleNamespace(
        accept_transaction_dispute=accept, respond_to_approval=approval,
    ))
    headers = {"authorization": "Bearer synthetic-oauth"}

    with pytest.raises(PermissionError, match="Invalid OAuth grant"):
        await mcp_tools.report_transaction_dispute("synthetic-proposal", headers)
    for approved in (True, False):
        with pytest.raises(PermissionError, match="Invalid OAuth grant"):
            mcp_tools.respond_to_dispute_approval("synthetic-case", approved, headers)
    assert resolver.call_count == 3
    accept.assert_not_called()
    approval.assert_not_called()


async def test_sdk_oauth_read_remains_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "oauth")
    resolver = Mock(return_value="oauth-customer")
    monkeypatch.setattr(oauth_identity, "resolve_oauth_customer_id", resolver)
    transactions = Mock(return_value=[])
    monkeypatch.setattr(mcp_tools, "service", SimpleNamespace(get_transactions=transactions))

    async with Client(mcp_tools.mcp) as client:
        result = await client.call_tool("getLastTransactions", {"product_number": "owned-bank"})

    assert result.is_error is False
    assert result.content == []
    assert resolver.call_count == 1
    transactions.assert_called_once_with("owned-bank", "oauth-customer")