"""Strict RS256 OAuth access tokens, separate from application login JWTs."""
from __future__ import annotations

import time
from typing import Any

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import GuestProtocol, RSAKey

OAUTH_SCOPE = "mcp.access"
OAUTH_TTL_SECONDS = 3600
OAUTH_CODE_TTL_SECONDS = 300


def public_jwks(signing_material: str, key_id: str) -> dict[str, Any]:
    """Publish only the public half of an externally supplied RSA signing key."""
    if not signing_material.strip() or not key_id.strip() or len(key_id) > 128:
        raise ValueError("OAuth signing configuration is required")
    key = RSAKey.import_key(signing_material, {"kid": key_id})
    if not key.is_private:
        raise ValueError("OAuth requires a private RSA signing key")
    if key.public_key.key_size < 2048:
        raise ValueError("OAuth RSA signing key must be at least 2048 bits")
    return {"keys": [{**key.as_dict(private=False), "alg": "RS256", "use": "sig"}]}


def validate_access_token(
    token: str, jwks: dict[str, Any], issuer: str, audience: str,
) -> dict[str, Any]:
    """Verify signature and bounded customer/read-only claims without introspection."""
    def load_key(guest: GuestProtocol) -> RSAKey:
        header = guest.headers()
        if (header.get("alg") != "RS256" or header.get("typ") != "at+jwt"
                or not isinstance(header.get("kid"), str)
                or set(header) != {"alg", "typ", "kid"}):
            raise ValueError("Invalid OAuth header")
        keys = jwks.get("keys")
        if not isinstance(keys, list) or not 1 <= len(keys) <= 16:
            raise ValueError("Invalid OAuth JWKS")
        matches = [key for key in keys if isinstance(key, dict)
                   and key.get("kid") == header["kid"]]
        if len(matches) != 1:
            raise ValueError("Unknown OAuth signing key")
        key = matches[0]
        if (key.get("kty") != "RSA" or key.get("alg") != "RS256"
                or key.get("use") != "sig" or set(key) - {"kty", "kid", "alg", "use", "n", "e"}):
            raise ValueError("Invalid OAuth public key")
        imported = RSAKey.import_key(key)
        if imported.public_key.key_size < 2048:
            raise ValueError("Invalid OAuth public key")
        return imported

    try:
        claims = jwt.decode(token, load_key, algorithms=["RS256"]).claims
        now = int(time.time())
        jwt.JWTClaimsRegistry(now=now, leeway=0).validate(claims)
        if (claims.get("iss") != issuer or claims.get("aud") != audience
                or claims.get("scope") != OAUTH_SCOPE or claims.get("role") != "customer"
                or claims.get("locale") not in {"en", "es", "pt"}
                or type(claims.get("identity_version")) is not int
                or claims["identity_version"] < 1
                or any(not isinstance(claims.get(key), str) or not claims[key].strip()
                       for key in ("sub", "customer_id", "email", "client_id", "jti"))
                or any(type(claims.get(key)) is not int for key in ("iat", "exp"))
                or not 0 <= claims["iat"] <= now < claims["exp"]
                or not 0 < claims["exp"] - claims["iat"] <= OAUTH_TTL_SECONDS):
            raise ValueError("Invalid OAuth claims")
        return dict(claims)
    except (JoseError, ValueError, TypeError, KeyError, OverflowError):
        raise PermissionError("Invalid customer identity") from None
