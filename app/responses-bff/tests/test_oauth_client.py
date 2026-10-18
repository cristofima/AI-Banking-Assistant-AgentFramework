"""Bounded confidential grant custody and direct OAuth delegation regressions."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from typing import Any
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey
from fastapi import FastAPI, HTTPException, Request
from pydantic import SecretStr

from bff.config.settings import Settings
from bff.identity.models import AuthenticatedUser
from bff.identity.oauth_client import OAuthClient, OAuthClientConfig, session_binding
from bff.identity.responses import _upstream_headers


@pytest.fixture
def user() -> AuthenticatedUser:
    return AuthenticatedUser(sub="user-1", customer_id="customer-1",
                             email="demo@example.invalid", locale="es",
                             role="customer", identity_version=1)


@pytest.fixture
def signing_key() -> Any:
    return RSAKey.generate_key(2048, private=True, parameters={"kid": "test"})


@pytest.fixture
def oauth(signing_key: Any) -> OAuthClient:
    import json

    public = {**signing_key.as_dict(private=False), "alg": "RS256", "use": "sig"}
    return OAuthClient(OAuthClientConfig(
        mcp_identity_mode="oauth", oauth_client_id="bff-client",
        oauth_client_secret=SecretStr("synthetic-client-secret"),
        oauth_redirect_uri="https://localhost/oauth/callback",
        oauth_issuer="https://identity.invalid", oauth_audience="banking-mcp",
        oauth_jwks=json.dumps({"keys": [public]}),
    ))


def token_for(signing_key: Any, user: AuthenticatedUser, **changes: Any) -> str:
    now = int(time.time())
    claims = {"sub": user.sub, "customer_id": user.customer_id, "email": user.email,
              "locale": user.locale, "identity_version": user.identity_version,
              "role": "customer", "scope": "mcp.access", "client_id": "bff-client",
              "jti": "synthetic-grant", "iss": "https://identity.invalid",
              "aud": "banking-mcp", "iat": now, "exp": now + 3600, **changes}
    return jwt.encode(
        {"alg": "RS256", "typ": "at+jwt", "kid": "test"}, claims, signing_key,
        algorithms=["RS256"],
    )


def begin(oauth: OAuthClient, binding: str = "session-1") -> tuple[str, str]:
    url = oauth.begin(binding)
    fields = parse_qs(urlsplit(url).query)
    assert urlsplit(url).path == "/oauth/consent"
    assert fields["scope"] == ["mcp.access"]
    assert fields["code_challenge_method"] == ["S256"]
    state = fields["state"][0]
    verifier = oauth.pending[state].verifier
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert fields["code_challenge"] == [expected.rstrip(b"=").decode()]
    return state, verifier


async def test_exchange_custodies_signed_customer_grant_once(
    oauth: OAuthClient, signing_key: Any, user: AuthenticatedUser,
) -> None:
    state, verifier = begin(oauth)
    token = token_for(signing_key, user)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/oauth/token"
        assert request.headers["authorization"].startswith("Basic ")
        form = parse_qs(request.content.decode())
        assert form["code_verifier"] == [verifier]
        assert form["code"] == ["synthetic-code"]
        return httpx.Response(200, json={"access_token": token, "token_type": "Bearer",
                                         "scope": "mcp.access"})

    async with httpx.AsyncClient(base_url="http://127.0.0.1:8090",
                                transport=httpx.MockTransport(handler)) as client:
        await oauth.complete("session-1", user, state, "synthetic-code", client)
        with pytest.raises(HTTPException) as caught:
            await oauth.complete("session-1", user, state, "synthetic-code", client)
    assert caught.value.status_code == 400
    grant = oauth.current("session-1")
    assert grant is not None and grant.access_token == token
    assert token not in repr(grant)
    assert oauth.current("session-2") is None
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"sub": "other"}, {"customer_id": "other"}, {"identity_version": 2},
    {"locale": "en"}, {"email": "other@example.invalid"}, {"client_id": "other"},
    {"scope": "banking.write"}, {"scope": "banking.read"},
    {"role": "operator"}, {"role": "admin"}, {"exp": 1},
])
async def test_mismatched_or_invalid_grants_fail_without_retry(
    oauth: OAuthClient, signing_key: Any, user: AuthenticatedUser, changes: dict[str, Any],
) -> None:
    state, _ = begin(oauth)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"access_token": token_for(signing_key, user, **changes),
                                        "token_type": "bearer", "scope": "mcp.access"})

    async with httpx.AsyncClient(base_url="https://identity.invalid",
                                transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HTTPException) as caught:
            await oauth.complete("session-1", user, state, "code", client)
        assert caught.value.status_code == 503
        with pytest.raises(HTTPException) as replay:
            await oauth.complete("session-1", user, state, "code", client)
    assert replay.value.status_code == 400
    assert oauth.current("session-1") is None
    assert calls == ["/oauth/token"]


@pytest.mark.parametrize("body", [None, [], {}, {"token_type": 1},
    {"token_type": "Bearer", "scope": "mcp.access", "access_token": "bad"},
    {"token_type": "Bearer", "scope": "mcp.access", "refresh_token": "bad"},
])
async def test_malformed_token_response_never_creates_grant(
    oauth: OAuthClient, user: AuthenticatedUser, body: Any,
) -> None:
    state, _ = begin(oauth)
    async with httpx.AsyncClient(base_url="https://identity.invalid", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=body),
    )) as client:
        with pytest.raises(HTTPException) as caught:
            await oauth.complete("session-1", user, state, "code", client)
    assert caught.value.detail == {"code": "SERVICE_UNAVAILABLE"}
    assert oauth.current("session-1") is None


async def test_legacy_scope_response_never_custodies_valid_customer_token(
    oauth: OAuthClient, signing_key: Any, user: AuthenticatedUser,
) -> None:
    state, _ = begin(oauth)
    body = {"access_token": token_for(signing_key, user),
            "token_type": "Bearer", "scope": "banking.read"}
    async with httpx.AsyncClient(base_url="https://identity.invalid", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=body),
    )) as client:
        with pytest.raises(HTTPException) as caught:
            await oauth.complete("session-1", user, state, "code", client)
    assert caught.value.detail == {"code": "SERVICE_UNAVAILABLE"}
    assert oauth.current("session-1") is None


async def test_foreign_state_and_remote_http_never_exchange(
    oauth: OAuthClient, user: AuthenticatedUser,
) -> None:
    state, _ = begin(oauth)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(500)

    async with httpx.AsyncClient(base_url="http://identity.invalid",
                                transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HTTPException) as foreign:
            await oauth.complete("other-session", user, state, "code", client)
        assert foreign.value.status_code == 400
        with pytest.raises(HTTPException) as insecure:
            await oauth.complete("session-1", user, state, "code", client)
    assert insecure.value.status_code == 503
    assert calls == []


@pytest.mark.parametrize("failure, category", [
    ("state", "state_binding"), ("transport", "transport"),
    ("status", "upstream_status"), ("response", "token_response"),
    ("jwt", "token_validation"), ("client", "client_binding"),
    ("customer", "customer_binding"),
])
async def test_callback_diagnostics_never_log_grant_material(
    oauth: OAuthClient, user: AuthenticatedUser, signing_key: Any,
    monkeypatch: pytest.MonkeyPatch, failure: str, category: str,
) -> None:
    logger = Mock()
    monkeypatch.setattr("bff.identity.oauth_client.logger", logger)
    state, _ = begin(oauth)

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "transport":
            raise httpx.ConnectError("sensitive-transport-details", request=request)
        if failure == "status":
            return httpx.Response(400, json={"error_description": "sensitive-details"})
        if failure == "response":
            return httpx.Response(200, json={"token_type": "invalid"})
        changes = {"client_id": "foreign"} if failure == "client" else {}
        if failure == "customer":
            changes = {"sub": "foreign"}
        token = "sensitive-invalid-token" if failure == "jwt" else token_for(
            signing_key, user, **changes,
        )
        return httpx.Response(200, json={"access_token": token,
                                        "token_type": "Bearer", "scope": "mcp.access"})

    async with httpx.AsyncClient(base_url="https://identity.invalid",
                                transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HTTPException) as caught:
            await oauth.complete("foreign" if failure == "state" else "session-1",
                                 user, state, "sensitive-code", client)
    assert caught.value.status_code == (400 if failure == "state" else 503)
    assert oauth.current("session-1") is None
    if failure == "state":
        logger.error.assert_called_once_with("BFF OAuth callback rejected: category=state_binding")
    else:
        logger.error.assert_called_once_with("BFF OAuth callback rejected: category=%s", category)


def test_pending_grants_have_a_shared_capacity_limit(oauth: OAuthClient) -> None:
    from bff.identity.oauth_client import OAuthGrant

    now = int(time.time())
    oauth.grants = {f"session-{index}": OAuthGrant("synthetic-bearer", now + 300)
                    for index in range(999)}
    begin(oauth, "final-session")
    with pytest.raises(HTTPException) as caught:
        oauth.begin("overflow-session")
    assert caught.value.status_code == 503
    assert len(oauth.pending) == 1


@pytest.mark.parametrize("outcome", ["success", "outage", "timeout", "cancelled"])
async def test_in_flight_exchange_reserves_capacity_until_completion(
    oauth: OAuthClient, signing_key: Any, user: AuthenticatedUser,
    monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    from bff.identity.oauth_client import OAuthGrant

    now = int(time.time())
    monkeypatch.setattr("bff.identity.oauth_client.time.time", lambda: now)
    oauth.grants = {f"existing-{index}": OAuthGrant("synthetic-bearer", now + 3600)
                    for index in range(999)}
    state, _ = begin(oauth)
    token = token_for(signing_key, user)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        if outcome == "timeout":
            raise httpx.ReadTimeout("synthetic-timeout", request=request)
        if outcome == "outage":
            return httpx.Response(503)
        return httpx.Response(200, json={"access_token": token, "token_type": "Bearer",
                                        "scope": "mcp.access"})

    async with httpx.AsyncClient(base_url="https://identity.invalid",
                                transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(oauth.complete("session-1", user, state, "code", client))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            monkeypatch.setattr("bff.identity.oauth_client.time.time", lambda: now + 301)
            with pytest.raises(HTTPException) as overflow:
                oauth.begin("overflow-session")
            assert overflow.value.status_code == 503
            with pytest.raises(HTTPException) as replay:
                await oauth.complete("session-1", user, state, "code", client)
            assert replay.value.status_code == 400
            if outcome == "cancelled":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                if outcome == "success":
                    await task
                else:
                    with pytest.raises(HTTPException) as failed:
                        await task
                    assert failed.value.status_code == 503
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
    assert calls == 1
    if outcome == "success":
        assert oauth.current("session-1") is not None
        with pytest.raises(HTTPException):
            oauth.begin("overflow-session")
        oauth.grants.pop("session-1")
    else:
        assert oauth.current("session-1") is None
    begin(oauth, "replacement-session")
    assert len(oauth.pending) + len(oauth.grants) == 1000


def test_expired_state_and_grants_are_removed(
    oauth: OAuthClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bff.identity.oauth_client import OAuthGrant

    now = int(time.time())
    state, _ = begin(oauth)
    oauth.grants["session-1"] = OAuthGrant("synthetic-bearer", now + 300)
    monkeypatch.setattr("bff.identity.oauth_client.time.time", lambda: now + 300)
    assert oauth.current("session-1") is None
    assert state not in oauth.pending


@pytest.mark.parametrize("change", ["bearer", "endpoint", "user", "version", "locale"])
def test_binding_separates_identity_session_and_endpoint(
    user: AuthenticatedUser, change: str,
) -> None:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None, responses_upstream_mode="local")
    request = Request({"type": "http", "app": app,
                       "headers": [(b"authorization", b"Bearer synthetic-session")]})
    original = session_binding(request, user)
    if change == "bearer":
        request = Request({"type": "http", "app": app,
                           "headers": [(b"authorization", b"Bearer other-session")]})
    elif change == "endpoint":
        app.state.settings.responses_agent_endpoint = "http://other-agent:8088"
    else:
        fields = {"user": {"sub": "other"}, "version": {"identity_version": 2},
                  "locale": {"locale": "en"}}[change]
        user = user.model_copy(update=fields)
    assert session_binding(request, user) != original


@pytest.mark.parametrize("failure", ["outage", "timeout"])
async def test_uncertain_exchange_is_not_replayed(
    oauth: OAuthClient, user: AuthenticatedUser, failure: str,
) -> None:
    state, _ = begin(oauth)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-private-detail", request=request)
        return httpx.Response(500, text="synthetic-private-detail")

    async with httpx.AsyncClient(base_url="https://identity.invalid",
                                transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HTTPException) as caught:
            await oauth.complete("session-1", user, state, "code", client)
        assert caught.value.detail == {"code": "SERVICE_UNAVAILABLE"}
        with pytest.raises(HTTPException) as replay:
            await oauth.complete("session-1", user, state, "code", client)
    assert replay.value.status_code == 400
    assert calls == ["/oauth/token"]
    assert oauth.current("session-1") is None


@pytest.mark.parametrize("field,value", [
    ("oauth_redirect_uri", "http://localhost/oauth/callback"),
    ("oauth_issuer", "http://identity.invalid"), ("oauth_jwks", ""),
    ("oauth_client_id", ""), ("oauth_audience", ""),
])
def test_missing_or_insecure_configuration_cannot_start(
    oauth: OAuthClient, field: str, value: str,
) -> None:
    setattr(oauth.config, field, value)
    with pytest.raises(HTTPException) as caught:
        oauth.begin("session-1")
    assert caught.value.status_code == 503
    assert oauth.pending == {}


async def test_oauth_mode_missing_grant_does_not_fall_back_to_hmac(
    oauth: OAuthClient, user: AuthenticatedUser,
) -> None:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None, responses_upstream_mode="local",
                                  internal_identity_secret="synthetic-signer-at-least-32-bytes")
    app.state.oauth_client = oauth
    request = Request({"type": "http", "app": app, "headers": []})
    with pytest.raises(HTTPException) as caught:
        await _upstream_headers(request, user, False)
    assert caught.value.status_code == 403
    assert caught.value.detail == {"code": "ACCESS_DENIED"}
