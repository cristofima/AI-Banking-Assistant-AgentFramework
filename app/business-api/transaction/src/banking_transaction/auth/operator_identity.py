"""Active Identity operator boundary; no customer or MCP identity substitution."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Header, HTTPException

from banking_shared.jwt_identity import _invalid, _unavailable, introspect_token


@dataclass(frozen=True)
class OperatorPrincipal:
    sub: str
    identity_version: int


async def get_operator_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> OperatorPrincipal:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _invalid()
    secret = os.environ.get("JWT_SECRET_KEY")
    internal_secret = os.environ.get("AUTH_INTERNAL_SECRET")
    if not secret or not internal_secret:
        raise _unavailable()
    keys = ("sub", "email", "locale", "role", "identity_version")
    try:
        claims = jwt.decode(
            token, secret, algorithms=["HS256"],
            audience=os.environ.get("JWT_AUDIENCE", "home-banking-web"),
            issuer=os.environ.get("JWT_ISSUER", "home-banking-api"),
            options={"require": ["exp", *keys]},
        )
    except jwt.InvalidTokenError:
        raise _invalid() from None
    if (type(claims["identity_version"]) is not int or claims["identity_version"] < 1
        or any(not isinstance(claims[key], str) or not claims[key].strip()
               for key in keys if key != "identity_version")
        or claims["role"] not in ("customer", "operator", "admin")
        or claims["locale"] not in ("en", "es", "pt")
        or (claims["role"] != "customer" and "customer_id" in claims)):
        raise _invalid()
    profile = await introspect_token(token, internal_secret)
    if (not isinstance(profile, dict) or any(key not in profile for key in (*keys, "status"))
        or type(profile["identity_version"]) is not int or profile["identity_version"] < 1
        or any(not isinstance(profile[key], str) or not profile[key].strip()
               for key in keys if key != "identity_version")
        or profile["role"] not in ("customer", "operator", "admin")
        or profile["locale"] not in ("en", "es", "pt")
        or profile["status"] not in ("active", "inactive")
        or (profile["role"] != "customer" and "customer_id" in profile)):
        raise _unavailable()
    if profile["status"] != "active" or any(profile[key] != claims[key] for key in keys):
        raise _invalid()
    if claims["role"] != "operator":
        raise HTTPException(403, detail={"code": "OPERATOR_REQUIRED"})
    return OperatorPrincipal(sub=claims["sub"], identity_version=claims["identity_version"])
