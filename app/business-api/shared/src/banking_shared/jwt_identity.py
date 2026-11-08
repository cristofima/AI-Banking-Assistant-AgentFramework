"""Customer-only application JWT boundary, independent of agent MCP identity."""

from __future__ import annotations

import os
from typing import Annotated

import httpx
import jwt
from fastapi import Header, HTTPException
from jwt import InvalidTokenError

REQUIRED_CLAIMS = ["exp", "sub", "customer_id", "email", "locale", "role", "identity_version"]


def _invalid() -> HTTPException:
    return HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED"},
                         headers={"WWW-Authenticate": "Bearer"})


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail={"code": "SERVICE_UNAVAILABLE"})


def _auth_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=os.environ.get("AUTH_USERS_ENDPOINT", "http://127.0.0.1:8090"),
        timeout=3.0, follow_redirects=False,
    )


async def get_jwt_customer_id(authorization: Annotated[str | None, Header()] = None) -> str:
    """Verify signature, customer role, and current revocation state on every call."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _invalid()
    secret = os.environ.get("JWT_SECRET_KEY")
    internal_secret = os.environ.get("AUTH_INTERNAL_SECRET")
    if not secret or not internal_secret:
        raise _unavailable()
    try:
        claims = jwt.decode(
            token, secret, algorithms=["HS256"],
            audience=os.environ.get("JWT_AUDIENCE", "home-banking-web"),
            issuer=os.environ.get("JWT_ISSUER", "home-banking-api"),
            options={"require": REQUIRED_CLAIMS},
        )
    except InvalidTokenError:
        raise _invalid() from None
    if (claims.get("role") != "customer"
        or type(claims.get("identity_version")) is not int
        or claims["identity_version"] < 1
        or any(not isinstance(claims.get(key), str) or not claims[key].strip()
               for key in ("sub", "customer_id", "email", "locale"))):
        raise _invalid()
    try:
        async with _auth_client() as client:
            response = await client.post(
                "/internal/introspect", json={"token": token},
                headers={"Authorization": f"Bearer {internal_secret}"},
            )
    except httpx.RequestError:
        raise _unavailable() from None
    if response.status_code == 401:
        raise _invalid()
    if response.status_code != 200:
        raise _unavailable()
    try:
        profile = response.json()
    except ValueError:
        raise _unavailable() from None
    keys = ("sub", "customer_id", "email", "locale", "role", "identity_version")
    if (not isinstance(profile, dict) or any(key not in profile for key in keys)
        or type(profile["identity_version"]) is not int
        or profile["identity_version"] < 1
        or any(not isinstance(profile[key], str) or not profile[key].strip()
               for key in keys if key != "identity_version")
        or profile["role"] not in ("customer", "operator", "admin")):
        raise _unavailable()
    if any(profile[key] != claims[key] for key in keys):
        raise _invalid()
    return claims["customer_id"]
