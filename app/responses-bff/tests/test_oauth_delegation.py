"""Authenticated BFF OAuth routes keep downstream grants out of browser responses."""
from __future__ import annotations

import base64
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from asgi_lifespan import LifespanManager
from joserfc import jwt as oauth_jwt
from joserfc.jwk import RSAKey

from bff.config.settings import Settings
from bff.main import create_app
from bff.identity.models import AuthenticatedUser
from bff.identity.oauth_client import OAuthClientConfig
from bff.identity.responses import _upstream_headers
from fastapi import Request


async def test_customer_callback_and_signed_delegation_remain_server_owned() -> None:
    secret = "synthetic-application-key-at-least-32-bytes"
    config = Settings(_env_file=None, responses_upstream_mode="local", jwt_secret_key=secret,
                      auth_internal_secret="synthetic-internal-key",
                      internal_identity_secret="synthetic-envelope-key-at-least-32-bytes")
    profile: dict[str, Any] = {"sub": "user-1", "customer_id": "customer-1",
        "email": "demo@example.invalid", "locale": "es", "role": "customer",
        "identity_version": 1}
    now = int(time.time())
    browser = jwt.encode({**profile, "iss": config.jwt_issuer, "aud": config.jwt_audience,
                          "exp": now + 600}, secret, algorithm="HS256")
    key = RSAKey.generate_key(2048, private=True, parameters={"kid": "test"})
    public = {**key.as_dict(private=False), "alg": "RS256", "use": "sig"}
    oauth_config = OAuthClientConfig(mcp_identity_mode="oauth", oauth_client_id="bff-client",
        oauth_client_secret="synthetic-client-key", oauth_redirect_uri="https://localhost/oauth/callback",
        oauth_issuer="https://identity.invalid", oauth_audience="mcp",
        oauth_jwks=json.dumps({"keys": [public]}))
    access = oauth_jwt.encode({"alg": "RS256", "typ": "at+jwt", "kid": "test"},
        {**profile, "iss": oauth_config.oauth_issuer, "aud": "mcp", "iat": now,
         "exp": now + 300, "scope": "mcp.access", "client_id": "bff-client", "jti": "grant"},
        key, algorithms=["RS256"])
    exchanges: list[str] = []

    def identity(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/introspect":
            return httpx.Response(200, json=profile)
        assert request.url.path == "/oauth/token"
        exchanges.append(request.url.path)
        return httpx.Response(200, json={"access_token": access, "token_type": "bearer",
                                        "scope": "mcp.access"})

    app = create_app(config, auth_transport=httpx.MockTransport(identity), oauth_config=oauth_config)
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bff",
                                    headers={"Authorization": "Bearer " + browser}) as client:
            status = await client.get("/oauth/status")
            assert status.json() == {"required": True, "ready": False}
            start = await client.post("/oauth/start")
            assert start.status_code == 200 and start.headers["cache-control"] == "no-store"
            state = parse_qs(urlsplit(start.json()["authorize_url"]).query)["state"][0]
            callback = await client.post("/oauth/callback", json={"code": "code", "state": state})
            assert callback.json() == {"ready": True}
            assert access not in callback.text and access not in start.text
            assert (await client.get("/oauth/status")).json() == {"required": True, "ready": True}
            replay = await client.post("/oauth/callback", json={"code": "code", "state": state})
            assert replay.status_code == 400 and exchanges == ["/oauth/token"]
            anonymous = await client.get("/oauth/status", headers={"Authorization": ""})
            assert anonymous.status_code == 401
            request = Request({"type": "http", "app": app,
                               "headers": [(b"authorization", ("Bearer " + browser).encode())]})
            headers = await _upstream_headers(request, AuthenticatedUser(**profile), False)
            encoded = headers["x-agent-user-id"].split(".")[1]
            envelope = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            assert envelope["oauth_access_token"] == access
            assert envelope["oauth_expires_at"] == now + 300
            assert "Authorization" not in headers
