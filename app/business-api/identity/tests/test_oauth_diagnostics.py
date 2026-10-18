"""OAuth configuration diagnostics expose categories, never configuration values."""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from identity.oauth_routes import log_protocol_rejection
from identity.settings import Settings


@pytest.mark.parametrize("error", ["sensitive-error-description", None, ["invalid_client"]])
def test_unknown_protocol_errors_are_not_logged(
    monkeypatch: pytest.MonkeyPatch, error: object,
) -> None:
    logger = Mock()
    monkeypatch.setattr("identity.oauth_routes.logger", logger)
    log_protocol_rejection("token", error)
    logger.error.assert_called_once_with(
        "Identity OAuth %s rejected: code=%s", "token", "protocol_error",
    )


@pytest.mark.parametrize("category", [
    "https_url", "issuer", "audience", "signing_configuration", "signing_key",
])
def test_invalid_oauth_configuration_preserves_safe_response(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch,
    category: str,
) -> None:
    settings.oauth_issuer = "https://identity.synthetic.invalid"
    settings.oauth_frontend_authorize_url = "https://web.synthetic.invalid/oauth/consent"
    settings.oauth_audience = "synthetic-audience"
    settings.oauth_signing_key_id = "synthetic-key"
    settings.oauth_signing_private_key = SecretStr("sensitive-invalid-key")
    if category == "https_url":
        settings.oauth_issuer = "http://identity.synthetic.invalid"
    elif category == "issuer":
        settings.oauth_issuer += "/"
    elif category == "audience":
        settings.oauth_audience = " "
    elif category == "signing_configuration":
        settings.oauth_signing_key_id = " "
    logger = Mock()
    monkeypatch.setattr("identity.oauth_routes.logger", logger)

    response = client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "OAUTH_UNAVAILABLE"}}
    if category == "https_url":
        logger.error.assert_called_once_with(
            "Identity OAuth configuration invalid: category=https_url",
        )
    else:
        logger.error.assert_called_once_with(
            "Identity OAuth configuration invalid: category=%s", category,
        )
