"""Transaction dispute routes enforce the shared customer JWT boundary on every call.

Generic JWT verification is covered in Account's test_jwt_identity.py."""
from typing import Any
from unittest.mock import Mock

import httpx
import jwt
import pytest
from fastapi import FastAPI, HTTPException

from banking_transaction.routers import disputes as dispute_routers
from banking_shared import jwt_identity
from banking_shared.testing import (  # noqa: F401
    JWT_SECRET as SECRET,
    customer_claims as claims,
    jwt_environment,
    mock_introspection as transport,
)

pytestmark = pytest.mark.asyncio


DISPUTE_REQUESTS = [
    ("GET", "", None, "list_cases"),
    ("POST", "", {"previewToken": "synthetic-preview"}, "accept_transaction_dispute"),
    (
        "POST", "/preview", {"transactionId": "tx-1", "reason": "Unrecognized purchase"},
        "preview_transaction_dispute",
    ),
    ("POST", "/recovery", {"previewToken": "synthetic-preview"}, "recover_transaction_dispute"),
    ("GET", "/case-1", None, "get_case"),
    ("GET", "/case-1/timeline", None, "get_case_timeline"),
    ("POST", "/case-1/approval", {"approved": True}, "respond_to_approval"),
    ("POST", "/case-1/resolve", {"resolutionOutcome": "DECLINED"}, "resolve_case"),
    ("POST", "/case-1/recommendation/dismiss", None, "dismiss_recommendation"),
]


def dispute_app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Mock]:
    app = FastAPI()
    app.include_router(dispute_routers.router, prefix="/api/support-cases")
    service = Mock()
    monkeypatch.setattr(dispute_routers, "service", service)
    return app, service


@pytest.mark.parametrize("method,path,body,service_method", DISPUTE_REQUESTS)
@pytest.mark.parametrize("role", ["operator", "admin"])
@pytest.mark.parametrize("customer_claim", [False, True])
async def test_staff_cannot_enter_customer_dispute_routes(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    service_method: str,
    role: str,
    customer_claim: bool,
) -> None:
    app, service = dispute_app(monkeypatch)
    transport(monkeypatch, lambda request: pytest.fail("Staff JWT reached introspection"))
    payload = claims()
    payload["role"] = role
    if not customer_claim:
        del payload["customer_id"]
    token = jwt.encode(payload, SECRET, algorithm="HS256")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://transaction"
    ) as client:
        response = await client.request(
            method, "/api/support-cases" + path, json=body,
            headers={"Authorization": "Bearer " + token},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "AUTH_REQUIRED"}}
    assert response.headers["www-authenticate"] == "Bearer"
    getattr(service, service_method).assert_not_called()
    assert service.mock_calls == []


@pytest.mark.parametrize("method,path,body,service_method", DISPUTE_REQUESTS)
@pytest.mark.parametrize("change", ["operator", "admin", "version", "revoked", "outage"])
async def test_dispute_routes_recheck_identity_before_every_service_call(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    service_method: str,
    change: str,
) -> None:
    app, service = dispute_app(monkeypatch)
    getattr(service, service_method).return_value = {
        "caseId": "case-1", "transactionId": "tx-1", "reason": "Unrecognized purchase",
        "status": "WAITING_USER_APPROVAL", "openedAt": "2026-10-01T00:00:00Z",
        "updatedAt": "2026-10-01T00:00:00Z",
    }
    if service_method == "preview_transaction_dispute":
        service.preview_transaction_dispute.return_value = {
            "previewToken": "synthetic-preview", "transactionId": "tx-1",
            "reason": "Unrecognized purchase", "expiresAt": "2026-10-01T00:10:00Z",
            "transaction": {"id": "tx-1"},
        }
    payload = claims()
    profile = {key: value for key, value in payload.items() if key not in ("iss", "aud", "exp")}
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=profile)
        if change == "revoked":
            return httpx.Response(401)
        if change == "outage":
            raise httpx.ReadTimeout("Synthetic Identity outage", request=request)
        current = dict(profile)
        if change == "version":
            current["identity_version"] = 2
        else:
            current["role"] = change
        return httpx.Response(200, json=current)

    transport(monkeypatch, handler)
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://transaction"
    ) as client:
        first = await client.request(
            method, "/api/support-cases" + path, json=body,
            headers={"Authorization": "Bearer " + token},
        )
        expected_status = 403 if service_method == "resolve_case" else (201 if method == "POST" and not path else 200)
        assert first.status_code == expected_status
        if service_method == "resolve_case":
            assert first.json() == {"detail": {"code": "OPERATOR_ADJUDICATION_REQUIRED"}}
            service.resolve_case.assert_not_called()
        else:
            getattr(service, service_method).assert_called_once()
        service.reset_mock()
        second = await client.request(
            method, "/api/support-cases" + path, json=body,
            headers={"Authorization": "Bearer " + token},
        )

    assert second.status_code == (503 if change == "outage" else 401)
    code = "SERVICE_UNAVAILABLE" if change == "outage" else "AUTH_REQUIRED"
    assert second.json() == {"detail": {"code": code}}
    if change != "outage":
        assert second.headers["www-authenticate"] == "Bearer"
    assert calls == 2
    assert service.mock_calls == []
