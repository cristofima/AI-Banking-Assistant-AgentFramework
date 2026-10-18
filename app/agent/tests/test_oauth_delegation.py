"""Request-scoped OAuth delegation without custom MCP headers."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.common.internal_identity import (
    InternalPrincipal,
    _sign_payload,
    create_mcp_authorization,
    mcp_header_provider,
)
from app.adapters.runtime_tools import build_toolbox_tools

SECRET = "synthetic-signing-secret-at-least-32-bytes"


def _envelope(token: object, expires_at: object, subject: str = "user-a") -> str:
    return _sign_payload({
        "sub": subject, "customer_id": subject, "locale": "es",
        "oauth_access_token": token, "oauth_expires_at": expires_at,
    }, SECRET)


def test_oauth_uses_only_standard_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "oauth")
    with patch("app.common.internal_identity.get_request_context") as context:
        context.return_value.user_id = _envelope("synthetic.jwt.token", int(time.time()) + 300)
        assert mcp_header_provider(SECRET)({}) == {
            "Authorization": "Bearer synthetic.jwt.token",
        }
    assert "synthetic.jwt.token" not in repr(InternalPrincipal(
        "user-a", "customer-a", oauth_access_token="synthetic.jwt.token",
    ))


@pytest.mark.parametrize(("token", "expiry"), [
    (None, None), ("", 9999999999), ("token\r\ninjected", 9999999999),
    ("token", 0), ("token", True), ("token", "9999999999"),
])
def test_missing_or_expired_grant_never_falls_back(
    monkeypatch: pytest.MonkeyPatch, token: object, expiry: object,
) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "oauth")
    with patch("app.common.internal_identity.get_request_context") as context:
        context.return_value.user_id = _envelope(token, expiry)
        with pytest.raises(ValueError, match="current customer OAuth grant"):
            create_mcp_authorization(SECRET)


async def test_concurrent_oauth_requests_keep_separate_bearers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "oauth")
    current: ContextVar[str] = ContextVar("oauth_request")

    async def headers_for(subject: str) -> dict[str, str]:
        marker = current.set(_envelope(subject, int(time.time()) + 300, subject))
        try:
            await asyncio.sleep(0)
            return mcp_header_provider(SECRET)({})
        finally:
            current.reset(marker)

    with patch("app.common.internal_identity.get_request_context",
               side_effect=lambda: SimpleNamespace(user_id=current.get())):
        first, second = await asyncio.gather(headers_for("user-a"), headers_for("user-b"))
    assert first == {"Authorization": "Bearer user-a"}
    assert second == {"Authorization": "Bearer user-b"}


def test_oauth_toolbox_does_not_emit_custom_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "oauth")
    with pytest.raises(ValueError, match="direct HTTP"):
        build_toolbox_tools("foundry", "https://example.test", None, SECRET)


def test_unknown_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "unknown")
    with patch("app.common.internal_identity.get_request_context") as context:
        context.return_value.user_id = _envelope("token", 9999999999)
        with pytest.raises(RuntimeError, match="Unsupported MCP identity mode"):
            create_mcp_authorization(SECRET)
