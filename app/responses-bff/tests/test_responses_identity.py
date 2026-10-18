"""Credential outages remain controlled and do not disclose upstream details."""

from unittest.mock import AsyncMock

from azure.core.exceptions import ClientAuthenticationError
from fastapi import FastAPI, HTTPException, Request
import pytest

from bff.identity.models import AuthenticatedUser
from bff.identity.oauth_client import OAuthClient, OAuthClientConfig
from bff.identity.responses import _upstream_headers
from bff.config.settings import Settings


async def test_foundry_credential_failure_is_controlled() -> None:
    app = FastAPI()
    app.state.settings = Settings(
        responses_upstream_mode="foundry",
        internal_identity_secret="synthetic-signing-key-with-32-bytes",
    )
    app.state.oauth_client = OAuthClient(OAuthClientConfig(mcp_identity_mode="hmac"))
    credential = AsyncMock()
    credential.get_token.side_effect = ClientAuthenticationError("private synthetic detail")
    app.state.azure_credential = credential
    request = Request({"type": "http", "app": app})
    user = AuthenticatedUser(sub="synthetic-user", customer_id="synthetic-customer",
                             email="synthetic@example.invalid", locale="en",
                             role="customer", identity_version=1)

    with pytest.raises(HTTPException) as caught:
        await _upstream_headers(request, user, False)

    assert caught.value.status_code == 503
    assert caught.value.detail == {"code": "SERVICE_UNAVAILABLE"}
    credential.get_token.assert_awaited_once_with(app.state.settings.responses_token_scope)
