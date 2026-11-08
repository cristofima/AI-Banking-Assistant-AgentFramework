"""Per-request staff introspection, revocation, strict roles and controlled failures."""
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
import pytest
from fastapi import FastAPI, HTTPException

from banking_shared import jwt_identity
from banking_transaction.auth import operator_identity
from banking_transaction.routers import operator as operator_routers

SECRET = "synthetic-operator-signing-secret-32-bytes"
pytestmark = pytest.mark.asyncio


def claims(role: str = "operator") -> dict[str, Any]:
    payload = {"sub": "operator-one", "email": "operator@synthetic.invalid", "locale": "en",
               "role": role, "identity_version": 1, "iss": "home-banking-api", "aud": "home-banking-web",
               "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    if role == "customer":
        payload["customer_id"] = "customer-one"
    return payload


def profile(payload: dict[str, Any]) -> dict[str, Any]:
    return {**{key: value for key, value in payload.items() if key not in ("iss", "aud", "exp")},
            "status": "active", "updated_at": "2026-01-01T00:00:00Z"}


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", SECRET)
    monkeypatch.setenv("AUTH_INTERNAL_SECRET", "synthetic-internal-secret")
    monkeypatch.setenv("JWT_ISSUER", "home-banking-api")
    monkeypatch.setenv("JWT_AUDIENCE", "home-banking-web")


def respond(monkeypatch: pytest.MonkeyPatch, status: int, body: Any) -> None:
    monkeypatch.setattr(jwt_identity, "_auth_client", lambda: httpx.AsyncClient(
        base_url="http://identity", transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)),
    ))


async def invoke(payload: dict[str, Any]) -> operator_identity.OperatorPrincipal:
    return await operator_identity.get_operator_principal("Bearer " + jwt.encode(payload, SECRET, algorithm="HS256"))


async def test_every_operator_endpoint_introspects_each_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/introspect"
        assert request.headers["Authorization"] == "Bearer synthetic-internal-secret"
        calls.append(request)
        return httpx.Response(200, json=profile(claims()))
    monkeypatch.setattr(jwt_identity, "_auth_client", lambda: httpx.AsyncClient(
        base_url="http://identity", transport=httpx.MockTransport(handler), timeout=3,
    ))
    app = FastAPI()
    app.include_router(operator_routers.router, prefix="/api/operator/support-cases")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as client:
        token = jwt.encode(claims(), SECRET, algorithm="HS256")
        # Validation still runs for all three routes before resource/service access.
        for path, method in [("?limit=0", "GET"), ("/missing", "GET"), ("/missing/claim", "POST")]:
            monkeypatch.setattr(operator_routers.service, "get_case", lambda *args: (_ for _ in ()).throw(LookupError()))
            from banking_transaction.services.operator import OperatorClaimConflict
            monkeypatch.setattr(operator_routers.service, "claim_case", lambda *args: (_ for _ in ()).throw(OperatorClaimConflict()))
            await client.request(method, "/api/operator/support-cases" + path, headers={"Authorization": "Bearer " + token})
    assert len(calls) == 3


@pytest.mark.parametrize("role", ["customer", "admin"])
async def test_valid_other_roles_forbidden(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    payload = claims(role)
    respond(monkeypatch, 200, profile(payload))
    with pytest.raises(HTTPException) as error:
        await invoke(payload)
    assert error.value.status_code == 403 and error.value.detail == {"code": "OPERATOR_REQUIRED"}


@pytest.mark.parametrize("field,value", [("role", "agent"), ("identity_version", True),
    ("identity_version", 0), ("sub", ""), ("locale", "unknown"), ("customer_id", None),
    ("aud", "foreign"), ("iss", "foreign"), ("exp", 1)])
async def test_invalid_claims(monkeypatch: pytest.MonkeyPatch, field: str, value: Any) -> None:
    payload = claims()
    payload[field] = value
    monkeypatch.setattr(jwt_identity, "_auth_client", lambda: pytest.fail("Invalid claims reached Identity"))
    with pytest.raises(HTTPException) as error:
        await invoke(payload)
    assert error.value.status_code == 401


@pytest.mark.parametrize("field,value", [("identity_version", 2), ("status", "inactive"),
    ("email", "changed@synthetic.invalid"), ("sub", "other"), ("locale", "es"), ("role", "admin")])
async def test_revoked_or_changed_profile(monkeypatch: pytest.MonkeyPatch, field: str, value: Any) -> None:
    body = profile(claims())
    body[field] = value
    respond(monkeypatch, 200, body)
    with pytest.raises(HTTPException) as error:
        await invoke(claims())
    assert error.value.status_code == 401


@pytest.mark.parametrize("status,body,expected", [(401, {}, 401), (500, {}, 503), (302, {}, 503),
    (200, [], 503), (200, {}, 503), (200, {"identity_version": True}, 503)])
async def test_controlled_identity_failure(monkeypatch: pytest.MonkeyPatch, status: int, body: Any, expected: int) -> None:
    respond(monkeypatch, status, body)
    with pytest.raises(HTTPException) as error:
        await invoke(claims())
    assert error.value.status_code == expected


async def test_network_failure_and_internal_bearer_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Synthetic timeout", request=request)
    monkeypatch.setattr(jwt_identity, "_auth_client", lambda: httpx.AsyncClient(
        base_url="http://identity", transport=httpx.MockTransport(handler),
    ))
    with pytest.raises(HTTPException) as error:
        await invoke(claims())
    assert error.value.status_code == 503
    with pytest.raises(HTTPException) as error:
        await operator_identity.get_operator_principal("Bearer synthetic-agent-only-bearer")
    assert error.value.status_code == 401
