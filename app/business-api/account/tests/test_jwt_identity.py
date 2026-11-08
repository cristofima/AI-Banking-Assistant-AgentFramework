"""Synthetic application JWT revocation and customer-role boundary regressions."""
from collections.abc import Callable
from typing import Any

from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from fastapi import HTTPException

from banking_shared import jwt_identity
from banking_shared.testing import JWT_SECRET, customer_claims, set_jwt_environment

pytestmark = pytest.mark.asyncio

SECRET = JWT_SECRET
claims = customer_claims


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    set_jwt_environment(monkeypatch)


def transport(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    monkeypatch.setattr(jwt_identity, "_auth_client", lambda: httpx.AsyncClient(
        base_url="http://auth", timeout=3, transport=httpx.MockTransport(handler)))


async def invoke(payload: dict[str, Any], secret: str = SECRET) -> str:
    return await jwt_identity.get_jwt_customer_id("Bearer " + jwt.encode(payload, secret, algorithm="HS256"))


async def test_customer_introspected_each_time(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    profile = {k: v for k, v in claims().items() if k not in ("iss", "aud", "exp")}
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer synthetic-internal-secret"
        assert request.url.path == "/internal/introspect"
        assert request.extensions["timeout"]["read"] == 3
        assert set(__import__("json").loads(request.content)) == {"token"}
        calls.append(request)
        return httpx.Response(200, json=profile)
    transport(monkeypatch, handler)
    assert await invoke(claims()) == "customer-1"
    assert await invoke(claims()) == "customer-1"
    assert len(calls) == 2


@pytest.mark.parametrize("field,value", [("role", "operator"), ("role", "admin"),
    ("identity_version", True), ("identity_version", "1"), ("identity_version", 0),
    ("customer_id", ""), ("customer_id", None), ("email", " "),
    ("iss", "foreign"), ("aud", "foreign"), ("exp", 1)])
async def test_invalid_claims_rejected_before_http(monkeypatch: pytest.MonkeyPatch, field: str, value: Any) -> None:
    transport(monkeypatch, lambda request: pytest.fail("Invalid JWT must not reach Auth"))
    payload = claims()
    payload[field] = value
    with pytest.raises(HTTPException) as error:
        await invoke(payload)
    assert error.value.status_code == 401
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize("field", jwt_identity.REQUIRED_CLAIMS)
async def test_missing_claim(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    transport(monkeypatch, lambda request: pytest.fail("Missing claim reached Auth"))
    payload = claims()
    del payload[field]
    with pytest.raises(HTTPException) as error:
        await invoke(payload)
    assert error.value.status_code == 401


async def test_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    transport(monkeypatch, lambda request: pytest.fail("Bad signature reached Auth"))
    with pytest.raises(HTTPException) as error:
        await invoke(claims(), "another-synthetic-secret-32-bytes-long")
    assert error.value.status_code == 401


@pytest.mark.parametrize("status", [401, 500, 403, 302])
async def test_introspection_status(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    transport(monkeypatch, lambda request: httpx.Response(status))
    with pytest.raises(HTTPException) as error:
        await invoke(claims())
    assert error.value.status_code == (401 if status == 401 else 503)


@pytest.mark.parametrize("field,value", [("identity_version", 2), ("customer_id", "foreign"),
    ("sub", "foreign"), ("locale", "pt"), ("email", "other@example.com")])
async def test_identity_mismatch(monkeypatch: pytest.MonkeyPatch, field: str, value: Any) -> None:
    profile = {k: v for k, v in claims().items() if k not in ("iss", "aud", "exp")}
    profile[field] = value
    transport(monkeypatch, lambda request: httpx.Response(200, json=profile))
    with pytest.raises(HTTPException) as error:
        await invoke(claims())
    assert error.value.status_code == 401


@pytest.mark.parametrize("body", [{}, [], {"identity_version": True}])
async def test_malformed_profile(monkeypatch: pytest.MonkeyPatch, body: Any) -> None:
    transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(HTTPException) as error:
        await invoke(claims())
    assert error.value.status_code == 503


async def test_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic", request=request)
    transport(monkeypatch, handler)
    with pytest.raises(HTTPException) as error:
        await invoke(claims())
    assert error.value.status_code == 503


@pytest.mark.parametrize("header", [None, "Basic no", "Bearer ", "garbage"])
async def test_missing_bearer(header: str | None) -> None:
    with pytest.raises(HTTPException) as error:
        await jwt_identity.get_jwt_customer_id(header)
    assert error.value.status_code == 401


@pytest.mark.parametrize("key", ["JWT_SECRET_KEY", "AUTH_INTERNAL_SECRET"])
async def test_unconfigured(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.delenv(key)
    with pytest.raises(HTTPException) as error:
        await invoke(claims())
    assert error.value.status_code == 503


async def test_revocation_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    profile = {k: v for k, v in claims().items() if k not in ("iss", "aud", "exp")}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=profile) if calls == 1 else httpx.Response(401)

    transport(monkeypatch, handler)
    assert await invoke(claims()) == "customer-1"
    with pytest.raises(HTTPException) as error:
        await invoke(claims())
    assert error.value.status_code == 401
    assert calls == 2


async def test_invalid_json_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    transport(monkeypatch, lambda request: httpx.Response(200, content=b"not-json"))
    with pytest.raises(HTTPException) as error:
        await invoke(claims())
    assert error.value.status_code == 503
