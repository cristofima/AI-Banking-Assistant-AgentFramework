"""Tests for local and Foundry Responses upstream modes."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
import re
from types import SimpleNamespace
from typing import Any
from fastapi import FastAPI

import httpx
import httpcore
import jwt
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from bff.identity.models import AuthenticatedUser
from bff.identity.internal_identity import create_internal_identity, create_platform_user_id
from bff.main import create_app as _create_app
from bff.config.settings import Settings


def create_app(settings: Settings, **kwargs: Any) -> FastAPI:
    def introspect(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/introspect"
        assert request.headers["Authorization"] == "Bearer synthetic-internal-secret"
        claims = jwt.decode(json.loads(request.content)["token"], TEST_SECRET,
                            algorithms=["HS256"], audience=settings.jwt_audience,
                            issuer=settings.jwt_issuer)
        return httpx.Response(200, json={key: claims[key] for key in
            ("sub", "customer_id", "email", "locale", "role", "identity_version")})
    return _create_app(settings, auth_transport=httpx.MockTransport(introspect), **kwargs)


TEST_SECRET = "test-secret-key-with-at-least-32-bytes"


@pytest.mark.parametrize("locale", ["es", "pt", "en", "en-US", "fr"])
def test_internal_identity_signs_verified_email_without_browser_credentials(locale: str) -> None:
    user = _authenticated_user("user-a")
    user = AuthenticatedUser(sub=user.sub, customer_id=user.customer_id,
                             email=user.email, locale=locale, role="customer", identity_version=1)

    identity = create_internal_identity(user, TEST_SECRET)

    version, encoded, signature = identity.split(".")
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert version == "v1"
    assert payload == {
        "sub": user.sub,
        "customer_id": user.customer_id,
        "email": user.email,
        "locale": user.locale,
    }
    assert hmac.compare_digest(
        signature,
        hmac.new(TEST_SECRET.encode(), encoded.encode(), hashlib.sha256).hexdigest(),
    )


def test_platform_identity_is_stable_private_and_user_scoped() -> None:
    user = _authenticated_user("user-a")
    first = create_platform_user_id(user, TEST_SECRET)
    assert first == create_platform_user_id(user, TEST_SECRET)
    assert first != create_platform_user_id(_authenticated_user("user-b"), TEST_SECRET)
    assert first != create_platform_user_id(user, TEST_SECRET + "different")
    assert user.sub not in first and user.email not in first and user.customer_id not in first
    assert re.fullmatch(r"[A-Za-z0-9._:@-]{1,256}", first)


class FakeCredential:
    def __init__(self) -> None:
        self.scopes: list[str] = []
        self.closed = False

    async def get_token(self, *scopes: str) -> SimpleNamespace:
        self.scopes.extend(scopes)
        return SimpleNamespace(token="foundry-access-token")

    async def close(self) -> None:
        self.closed = True


class SseStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_first","status":"completed"}}\n\n'


def _settings(mode: str) -> Settings:
    return Settings(
        _env_file=None, auth_internal_secret="synthetic-internal-secret",
        responses_upstream_mode=mode,
        responses_agent_endpoint="http://agent.test/responses",
        jwt_secret_key=TEST_SECRET,
        internal_identity_secret=TEST_SECRET,
    )


def _token(user_id: str, settings: Settings) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "role": "customer", "identity_version": 1,
            "customer_id": f"customer-{user_id}",
            "email": f"{user_id}@example.com",
            "locale": "en-US",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        TEST_SECRET,
        algorithm="HS256",
    )


async def _post(app, token: str, payload: dict[str, object]) -> httpx.Response:
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                "/responses",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )


async def test_local_mode_uses_signed_internal_identity_without_azure_credential() -> None:
    settings = _settings("local")
    credential_factory_called = False

    def fail_if_called(_: Settings) -> FakeCredential:
        nonlocal credential_factory_called
        credential_factory_called = True
        raise AssertionError("Local mode must not create an Azure credential")

    async def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://agent.test/responses")
        assert "Authorization" not in request.headers
        assert "x-ms-user-identity" not in request.headers
        assert request.headers["x-agent-user-id"] == create_internal_identity(
            user=_authenticated_user("user-a"),
            secret=TEST_SECRET,
        )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=SseStream(),
        )

    app = create_app(
        settings,
        transport=httpx.MockTransport(upstream),
        credential_factory=fail_if_called,
    )
    response = await _post(app, _token("user-a", settings), {"input": "Accounts", "stream": True})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "x-conversation-id" not in response.headers
    assert '"type": "bff.continuation"' in response.text
    assert '"id":"resp_first"' in response.text
    assert credential_factory_called is False


async def test_foundry_mode_uses_managed_credential_and_delegated_identity() -> None:
    settings = _settings("foundry")
    credential = FakeCredential()

    async def upstream(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer foundry-access-token"
        assert request.headers["x-ms-user-identity"] == create_platform_user_id(
            _authenticated_user("user-a"), TEST_SECRET,
        )
        assert re.fullmatch(r"[A-Za-z0-9._:@-]{1,256}", request.headers["x-ms-user-identity"])
        assert request.headers["x-client-banking-identity"] == create_internal_identity(
            user=_authenticated_user("user-a"),
            secret=TEST_SECRET,
        )
        assert "user_isolation_key" not in request.headers
        assert "chat_isolation_key" not in request.headers
        return httpx.Response(200, json={"id": "resp_first", "status": "completed"})

    app = create_app(
        settings,
        transport=httpx.MockTransport(upstream),
        credential_factory=lambda _: credential,
    )
    response = await _post(app, _token("user-a", settings), {"input": "Accounts"})

    assert response.status_code == 200
    assert credential.scopes == ["https://ai.azure.com/.default"]
    assert credential.closed is True


def _authenticated_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        sub=user_id, role="customer", identity_version=1,
        customer_id=f"customer-{user_id}",
        email=f"{user_id}@example.com",
        locale="en-US",
    )


@pytest.mark.parametrize(
    ("identity_change", "expected_status", "expected_code"),
    [
        ("revoked", 401, "AUTH_REQUIRED"),
        ("version", 401, "AUTH_REQUIRED"),
        ("role", 401, "AUTH_REQUIRED"),
        ("unavailable", 503, "SERVICE_UNAVAILABLE"),
    ],
)
async def test_continuation_revalidates_identity_before_upstream(
    identity_change: str, expected_status: int, expected_code: str,
) -> None:
    settings = _settings("local")
    introspection_calls = 0
    upstream_calls = 0

    def introspect(request: httpx.Request) -> httpx.Response:
        nonlocal introspection_calls
        introspection_calls += 1
        claims = jwt.decode(
            json.loads(request.content)["token"], TEST_SECRET, algorithms=["HS256"],
            audience=settings.jwt_audience, issuer=settings.jwt_issuer,
        )
        profile = {key: claims[key] for key in
                   ("sub", "customer_id", "email", "locale", "role", "identity_version")}
        if introspection_calls > 1:
            if identity_change == "revoked":
                return httpx.Response(401)
            if identity_change == "unavailable":
                raise httpx.ConnectError("Synthetic identity outage", request=request)
            if identity_change == "version":
                profile["identity_version"] = 2
            if identity_change == "role":
                profile["role"] = "operator"
                profile.pop("customer_id")
        return httpx.Response(200, json=profile)

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json={"id": "resp_first", "status": "completed"})

    app = _create_app(
        settings, auth_transport=httpx.MockTransport(introspect),
        transport=httpx.MockTransport(upstream),
    )
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://bff") as client:
            headers = {"Authorization": "Bearer " + _token("user-a", settings)}
            first = await client.post("/responses", headers=headers, json={"input": "My cases"})
            assert first.status_code == 200
            second = await client.post("/responses", headers=headers, json={
                "input": "Their current status", "conversation": first.headers["x-conversation-id"],
            })
    assert second.status_code == expected_status
    assert second.json() == {"detail": {"code": expected_code}}
    assert introspection_calls == 2
    assert upstream_calls == 1


@pytest.mark.parametrize("role", ["operator", "admin"])
async def test_staff_responses_never_reaches_agent(role: str) -> None:
    settings = _settings("local")
    claims = jwt.decode(_token("staff", settings), TEST_SECRET, algorithms=["HS256"],
                        audience=settings.jwt_audience, issuer=settings.jwt_issuer)
    claims["role"] = role
    claims.pop("customer_id")
    profile = {key: claims[key] for key in
               ("sub", "email", "locale", "role", "identity_version")}
    upstream_calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json={"id": "resp_first", "status": "completed"})

    app = _create_app(
        settings, auth_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=profile)),
        transport=httpx.MockTransport(upstream),
    )
    response = await _post(app, jwt.encode(claims, TEST_SECRET, algorithm="HS256"), {"input": "Cases"})
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "ACCESS_DENIED"}}
    assert upstream_calls == 0


async def test_conversation_cannot_cross_users() -> None:
    settings = _settings("local")

    async def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "resp_first", "status": "completed"})

    app = create_app(settings, transport=httpx.MockTransport(upstream))
    first = await _post(app, _token("user-a", settings), {"input": "First turn"})
    second = await _post(
        app,
        _token("user-b", settings),
        {"input": "Second turn", "conversation": first.headers["x-conversation-id"]},
    )

    assert first.status_code == 200
    assert second.status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {"input": "Continue", "previous_response_id": "caresp_foreign"},
        {"input": "Continue", "agent_session_id": "foreign-session"},
        {"input": "Continue", "previous_response_id": None},
        {"input": "Continue", "previous_response_id": ""},
        {"input": "Continue", "agent_session_id": None},
        {"input": "Continue", "agent_session_id": ""},
        {"input": "Continue", "conversation": 123},
    ],
)
async def test_invalid_continuation_is_rejected(payload: dict[str, object]) -> None:
    settings = _settings("local")
    app = create_app(settings)

    response = await _post(app, _token("user-a", settings), payload)

    assert response.status_code == 400


@pytest.mark.parametrize("incoming", [None, "valid", "invalid"])
@pytest.mark.parametrize("stream", [False, True])
async def test_w3c_context_propagates_to_agent(
    monkeypatch: pytest.MonkeyPatch, incoming: str | None, stream: bool
) -> None:
    captured: list[httpx.Headers] = []

    async def upstream(
        pool: httpcore.AsyncConnectionPool, request: httpcore.Request
    ) -> httpcore.Response:
        captured.append(httpx.Headers(request.headers))
        content = (
            b'event: response.completed\ndata: {"type":"response.completed",'
            b'"response":{"id":"resp_trace","status":"completed"}}\n\n'
            if stream else b'{"id":"resp_trace","status":"completed"}'
        )
        return httpcore.Response(
            200, content=content,
            headers=[(b"content-type", b"text/event-stream" if stream else b"application/json")],
        )

    monkeypatch.setattr(httpcore.AsyncConnectionPool, "handle_async_request", upstream)
    settings = _settings("local")
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {_token('user-a', settings)}"}
    trace_id = "1234567890abcdef1234567890abcdef"
    parent_id = "1234567890abcdef"
    if incoming == "valid":
        headers.update(traceparent=f"00-{trace_id}-{parent_id}-01", tracestate="bank=opaque")
    elif incoming == "invalid":
        headers.update(traceparent="invalid", tracestate="bank=opaque")

    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/responses", json={"input": "Accounts", "stream": stream}, headers=headers
            )

    assert response.status_code == 200
    assert len(captured) == 1
    outbound = captured[0]["traceparent"]
    assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", outbound)
    assert outbound.split("-")[1] != "0" * 32
    assert outbound.split("-")[2] != parent_id
    if incoming == "valid":
        assert outbound.split("-")[1] == trace_id
        assert captured[0]["tracestate"] == "bank=opaque"
    else:
        assert outbound.split("-")[1] != trace_id
        assert "tracestate" not in captured[0]


async def test_concurrent_bff_requests_have_independent_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    both_requests = asyncio.Event()

    async def upstream(
        pool: httpcore.AsyncConnectionPool, request: httpcore.Request
    ) -> httpcore.Response:
        captured.append(httpx.Headers(request.headers)["traceparent"])
        if len(captured) == 2:
            both_requests.set()
        await asyncio.wait_for(both_requests.wait(), timeout=5)
        return httpcore.Response(200, content=b'{"id":"resp_trace","status":"completed"}')

    monkeypatch.setattr(httpcore.AsyncConnectionPool, "handle_async_request", upstream)
    settings = _settings("local")
    app = create_app(settings)
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            responses = await asyncio.gather(*(
                client.post(
                    "/responses", json={"input": "Accounts"},
                    headers={"Authorization": f"Bearer {_token(user, settings)}"},
                )
                for user in ("user-a", "user-b")
            ))

    assert [response.status_code for response in responses] == [200, 200]
    assert len({value.split("-")[1] for value in captured}) == 2


@pytest.mark.parametrize("mode", ["local", "foundry"])
@pytest.mark.parametrize("stream", [False, True])
async def test_two_turns_translate_customer_token_to_previous_response(
    mode: str, stream: bool,
) -> None:
    settings = _settings(mode)
    captured: list[dict[str, object]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if stream:
            return httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, stream=SseStream()
            )
        return httpx.Response(200, json={"id": "resp_first", "status": "completed"})

    app = create_app(
        settings, transport=httpx.MockTransport(upstream),
        credential_factory=lambda _: FakeCredential(),
    )
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://bff") as client:
            headers = {"Authorization": "Bearer " + _token("user-a", settings)}
            first = await client.post(
                "/responses", headers=headers, json={"input": "First", "stream": stream}
            )
            if stream:
                controls = [json.loads(line[6:]) for line in first.text.splitlines()
                            if line.startswith('data: {"type": "bff.continuation"')]
                token = controls[0]["token"]
            else:
                token = first.headers["x-conversation-id"]
            second = await client.post(
                "/responses", headers=headers,
                json={"input": "Next", "conversation": token, "stream": stream},
            )
    assert first.status_code == second.status_code == 200
    assert captured == [
        {"input": "First", "stream": stream},
        {"input": "Next", "stream": stream, "previous_response_id": "resp_first"},
    ]


async def test_json_without_checkpoint_returns_controlled_failure() -> None:
    settings = _settings("local")
    app = create_app(settings, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"status": "completed"})
    ))
    response = await _post(app, _token("user-a", settings), {"input": "First"})
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "SERVICE_UNAVAILABLE"}}
    assert "x-conversation-id" not in response.headers