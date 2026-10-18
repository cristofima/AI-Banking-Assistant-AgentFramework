"""Customer consent facade and exact callback binding regressions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import Mock
from urllib.parse import urlencode

import httpx
import jwt
import pytest
from asgi_lifespan import LifespanManager

from bff.config.settings import Settings
from bff.main import create_app

SECRET = "synthetic-signing-secret-at-least-32-bytes"
CALLBACK = "https://client.invalid/callback?existing=value"


@pytest.fixture
def consent() -> dict[str, Any]:
    return {
        "client_id": "synthetic-client", "redirect_uri": CALLBACK,
        "response_type": "code", "scope": "mcp.access", "state": "synthetic-state",
        "code_challenge": "A" * 43, "code_challenge_method": "S256", "approved": True,
    }


async def authorize(
    consent: dict[str, Any], callback: str, role: str = "customer",
    *, authenticated: bool = True,
) -> tuple[httpx.Response, list[str]]:
    config = Settings(
        _env_file=None, responses_upstream_mode="local", jwt_secret_key=SECRET,
        auth_internal_secret="synthetic-internal-secret",
    )
    profile: dict[str, Any] = {
        "sub": "user-1", "email": "demo@example.com", "locale": "es",
        "role": role, "identity_version": 1,
    }
    if role == "customer":
        profile["customer_id"] = "customer-1"
    bearer = jwt.encode(
        {**profile, "iss": config.jwt_issuer, "aud": config.jwt_audience,
         "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        SECRET, algorithm="HS256",
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/internal/introspect":
            return httpx.Response(200, json=profile)
        assert request.url.path == "/oauth/authorize"
        assert request.headers["Authorization"] == "Bearer " + bearer
        return httpx.Response(200, json={"redirect_uri": callback})

    app = create_app(config, auth_transport=httpx.MockTransport(handler))
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://bff",
        ) as client:
            headers = {"Authorization": "Bearer " + bearer} if authenticated else {}
            result = await client.post("/oauth/authorize", json=consent, headers=headers)
    return result, calls


@pytest.mark.parametrize("authenticated", [True, False])
async def test_oauth_boundary_logs_only_error_status(
    consent: dict[str, Any], authenticated: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr("bff.main.oauth_logger", logger)
    callback = CALLBACK + "&code=x&state=synthetic-state"
    result, _ = await authorize(consent, callback, authenticated=authenticated)
    if authenticated:
        assert result.status_code == 200
        logger.error.assert_not_called()
    else:
        assert result.status_code == 401
        logger.error.assert_called_once_with("BFF OAuth request failed: status=%s", 401)


@pytest.mark.parametrize("approved", [True, False])
async def test_consent_preserves_exact_callback_and_state(
    consent: dict[str, Any], approved: bool,
) -> None:
    consent["approved"] = approved
    fields = {"code": "synthetic-code"} if approved else {"error": "access_denied"}
    callback = CALLBACK + "&" + urlencode({**fields, "state": consent["state"]})
    result, calls = await authorize(consent, callback)
    assert result.status_code == 200
    assert result.json() == {"redirect_uri": callback}
    assert result.headers["cache-control"] == "no-store"
    assert calls == ["/internal/introspect", "/oauth/authorize"]


@pytest.mark.parametrize("callback", [
    "https://other.invalid/callback?existing=value&code=x&state=synthetic-state",
    "https://client.invalid/other?existing=value&code=x&state=synthetic-state",
    "https://client.invalid/callback?existing=changed&code=x&state=synthetic-state",
    CALLBACK + "&code=x&state=other",
    CALLBACK + "&code=x&state=synthetic-state&state=synthetic-state",
    CALLBACK + "&error=access_denied&state=synthetic-state",
    CALLBACK + "&code=x&state=synthetic-state&unexpected=x",
    CALLBACK + "&code=x&state=synthetic-state#fragment",
    CALLBACK + "&code=&state=synthetic-state",
    CALLBACK + "&code=x&state=synthetic-state&error_description=denied",
])
async def test_invalid_upstream_callback_fails_closed(
    consent: dict[str, Any], callback: str,
) -> None:
    result, _ = await authorize(consent, callback)
    assert result.status_code == 503
    assert result.json() == {"detail": {"code": "SERVICE_UNAVAILABLE"}}


@pytest.mark.parametrize("role", ["admin", "operator"])
async def test_staff_cannot_authorize(consent: dict[str, Any], role: str) -> None:
    result, calls = await authorize(consent, CALLBACK, role)
    assert result.status_code == 403
    assert calls == ["/internal/introspect"]


async def test_anonymous_cannot_authorize(consent: dict[str, Any]) -> None:
    result, calls = await authorize(consent, CALLBACK, authenticated=False)
    assert result.status_code == 401
    assert calls == []


@pytest.mark.parametrize("field,value", [
    ("approved", "true"), ("redirect_uri", "http://client.invalid/callback"),
    ("code_challenge_method", "plain"), ("unexpected", "value"),
    ("scope", "banking.write"), ("scope", "banking.read"),
])
async def test_invalid_consent_never_reaches_provider(
    consent: dict[str, Any], field: str, value: str,
) -> None:
    consent[field] = value
    result, calls = await authorize(consent, CALLBACK)
    assert result.status_code == 422
    assert "/oauth/authorize" not in calls
