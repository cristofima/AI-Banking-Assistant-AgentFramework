"""Confidential OAuth client with bounded, process-local customer grant custody."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from banking_shared.oauth_jwt import OAUTH_SCOPE, validate_access_token
from bff.identity.models import AuthenticatedUser

logger = logging.getLogger("uvicorn.error.bff.oauth")


class OAuthClientConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    mcp_identity_mode: str = "hmac"
    oauth_client_id: str = ""
    oauth_client_secret: SecretStr = SecretStr("")
    oauth_redirect_uri: str = ""
    oauth_issuer: str = ""
    oauth_audience: str = ""
    oauth_jwks: str = ""


@dataclass(frozen=True)
class OAuthGrant:
    access_token: str = field(repr=False)
    expires_at: int


@dataclass(frozen=True)
class PendingGrant:
    binding: str
    verifier: str = field(repr=False)
    expires_at: int


def session_binding(request: Request, user: AuthenticatedUser) -> str:
    material = [user.sub, user.customer_id, user.identity_version, user.email, user.locale,
                request.app.state.settings.responses_agent_endpoint,
                request.headers.get("authorization", "")]
    return hashlib.sha256(repr(material).encode()).hexdigest()


class OAuthClient:
    def __init__(self, config: OAuthClientConfig) -> None:
        self.config = config
        self.pending: dict[str, PendingGrant] = {}
        self.grants: dict[str, OAuthGrant] = {}
        self._in_flight = 0

    def _prune(self) -> None:
        now = int(time.time())
        self.pending = {key: value for key, value in self.pending.items()
                        if value.expires_at > now}
        self.grants = {key: value for key, value in self.grants.items()
                       if value.expires_at > now}

    def current(self, binding: str) -> OAuthGrant | None:
        self._prune()
        return self.grants.get(binding)

    def begin(self, binding: str) -> str:
        from bff.routers.oauth import validate_callback

        config = self.config
        try:
            validate_callback(config.oauth_redirect_uri)
            validate_callback(config.oauth_issuer)
            if (not config.oauth_client_id or not config.oauth_client_secret.get_secret_value()
                    or not config.oauth_audience or not config.oauth_jwks
                    or config.oauth_issuer.endswith("/") or "?" in config.oauth_issuer):
                raise ValueError("OAuth configuration missing")
        except ValueError:
            logger.error("BFF OAuth start rejected: category=configuration")
            raise HTTPException(503, detail={"code": "SERVICE_UNAVAILABLE"}) from None
        self._prune()
        if len(self.pending) + len(self.grants) + self._in_flight >= 1000:
            logger.error("BFF OAuth start rejected: category=capacity")
            raise HTTPException(503, detail={"code": "SERVICE_UNAVAILABLE"})
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        self.pending[state] = PendingGrant(binding, verifier, int(time.time()) + 300)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        return "/oauth/consent?" + urlencode({
            "client_id": config.oauth_client_id, "redirect_uri": config.oauth_redirect_uri,
            "response_type": "code", "scope": OAUTH_SCOPE, "state": state,
            "code_challenge": challenge.rstrip(b"=").decode(),
            "code_challenge_method": "S256",
        })

    async def complete(
        self, binding: str, user: AuthenticatedUser, state: str, code: str,
        client: httpx.AsyncClient,
    ) -> None:
        self._prune()
        pending = self.pending.get(state)
        if pending is None or pending.binding != binding:
            logger.error("BFF OAuth callback rejected: category=state_binding")
            raise HTTPException(400, detail={"code": "INVALID_REQUEST"})
        del self.pending[state]
        self._in_flight += 1
        config = self.config
        category = "endpoint"
        try:
            endpoint = client.base_url
            if (endpoint.scheme != "https" and not (
                endpoint.scheme == "http"
                and endpoint.host in {"localhost", "127.0.0.1", "::1"}
            )):
                raise ValueError("OAuth exchange requires HTTPS or loopback")
            category = "transport"
            response = await client.post(
                "/oauth/token", data={"grant_type": "authorization_code", "code": code,
                    "redirect_uri": config.oauth_redirect_uri, "code_verifier": pending.verifier},
                auth=httpx.BasicAuth(config.oauth_client_id,
                                    config.oauth_client_secret.get_secret_value()),
            )
            category = "upstream_status"
            response.raise_for_status()
            category = "token_response"
            body = response.json()
            if (body.get("token_type", "").lower() != "bearer"
                    or body.get("scope") != OAUTH_SCOPE or "refresh_token" in body):
                raise ValueError("Invalid grant")
            token = body["access_token"]
            category = "token_validation"
            claims = validate_access_token(token, json.loads(config.oauth_jwks),
                                           config.oauth_issuer, config.oauth_audience)
            category = "client_binding"
            if claims["client_id"] != config.oauth_client_id:
                raise ValueError("Wrong OAuth client")
            category = "customer_binding"
            if (claims["sub"] != user.sub or claims["customer_id"] != user.customer_id
                    or claims["identity_version"] != user.identity_version
                    or claims["email"] != user.email or claims["locale"] != user.locale):
                raise ValueError("Wrong grant identity")
            self.grants[binding] = OAuthGrant(token, claims["exp"])
        except (httpx.HTTPError, PermissionError, ValueError, KeyError, TypeError, AttributeError):
            logger.error("BFF OAuth callback rejected: category=%s", category)
            raise HTTPException(503, detail={"code": "SERVICE_UNAVAILABLE"}) from None
        finally:
            self._in_flight -= 1
