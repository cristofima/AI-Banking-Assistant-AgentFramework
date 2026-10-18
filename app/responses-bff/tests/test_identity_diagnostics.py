"""Identity transport diagnostics never expose request or exception details."""

from __future__ import annotations

import ssl
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException

from bff.clients.identity import auth_request


@pytest.mark.parametrize("category", ["transport", "timeout", "tls_certificate_verification"])
async def test_identity_transport_failure_emits_only_controlled_category(
    monkeypatch: pytest.MonkeyPatch, category: str,
) -> None:
    logger = Mock()
    monkeypatch.setattr("bff.clients.identity.logger", logger)
    error = (
        httpx.ReadTimeout("sensitive timeout details")
        if category == "timeout" else httpx.ConnectError("sensitive connection details")
    )
    if category == "tls_certificate_verification":
        error.__cause__ = ssl.SSLCertVerificationError("sensitive certificate details")

    async def fail_request(request: httpx.Request) -> httpx.Response:
        raise error

    async with httpx.AsyncClient(
        base_url="https://identity.invalid", transport=httpx.MockTransport(fail_request),
    ) as client:
        with pytest.raises(HTTPException) as failure:
            await auth_request(client, "POST", "/auth/login", payload={"password": "synthetic"})

    assert failure.value.status_code == 503
    assert failure.value.detail == {"code": "SERVICE_UNAVAILABLE"}
    logger.error.assert_called_once_with("Identity connection failed: category=%s", category)
