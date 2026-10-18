"""OAuth MCP authentication remains separate from local HMAC and browser JWTs."""
from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import Mock

import pytest
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey

from banking_shared.oauth_jwt import public_jwks
from banking_account.auth.internal_identity import get_customer_id


@pytest.fixture
def signing_key() -> str:
    return RSAKey.generate_key(2048, private=True).as_pem(private=True).decode()


@pytest.fixture
def oauth_mode(monkeypatch: pytest.MonkeyPatch, signing_key: str) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "oauth")
    monkeypatch.setenv("OAUTH_ISSUER", "https://identity.example")
    monkeypatch.setenv("OAUTH_AUDIENCE", "banking-mcp")
    monkeypatch.setenv("OAUTH_JWKS", json.dumps(public_jwks(signing_key, "test-key")))
    monkeypatch.delenv("AUTH_USERS_ENDPOINT", raising=False)
    monkeypatch.delenv("AUTH_INTERNAL_SECRET", raising=False)


def access_token(signing_key: str, changes: dict[str, Any] | None = None,
                 headers: dict[str, Any] | None = None) -> str:
    now = int(time.time())
    claims = {"sub": "user-1", "customer_id": "customer-1", "role": "customer",
              "identity_version": 1, "email": "test@example.com", "locale": "en",
              "iss": "https://identity.example", "aud": "banking-mcp",
              "scope": "mcp.access", "client_id": "client-1", "jti": "unique-1",
              "iat": now, "exp": now + 3600}
    claims.update(changes or {})
    return jwt.encode(headers or {"alg": "RS256", "typ": "at+jwt", "kid": "test-key"},
                      claims, RSAKey.import_key(signing_key), algorithms=["RS256"])


def test_oauth_validates_locally_without_identity_service(
    oauth_mode: None, signing_key: str,
) -> None:
    token = access_token(signing_key)
    assert get_customer_id({"authorization": "Bearer " + token}) == "customer-1"
    assert get_customer_id({"authorization": "Bearer " + token}) == "customer-1"


@pytest.mark.parametrize("seconds, accepted", [(300, True), (3600, True), (3601, False)])
def test_oauth_access_token_lifetime_boundary(
    oauth_mode: None, signing_key: str, monkeypatch: pytest.MonkeyPatch,
    seconds: int, accepted: bool,
) -> None:
    now = int(time.time())
    monkeypatch.setattr("banking_shared.oauth_jwt.time.time", lambda: now)
    token = access_token(signing_key, {"exp": now + seconds})
    if accepted:
        assert get_customer_id({"authorization": "Bearer " + token}) == "customer-1"
    else:
        with pytest.raises(PermissionError):
            get_customer_id({"authorization": "Bearer " + token})


@pytest.mark.parametrize("changes", [
    {"iss": "https://foreign.example"}, {"aud": "login"}, {"aud": ["banking-mcp"]},
    {"scope": "banking.read"}, {"scope": "mcp.access banking.write"}, {"scope": ""},
    {"role": "operator"}, {"role": "admin"},
    {"customer_id": ""}, {"sub": None}, {"email": None}, {"client_id": ""}, {"jti": ""},
    {"locale": "fr"}, {"identity_version": True}, {"identity_version": 0},
    {"exp": 1}, {"exp": True}, {"iat": int(time.time()) + 600},
    {"iat": int(time.time()) - 3600}, {"nbf": int(time.time()) + 600},
    {"iat": "invalid"}, {"exp": None}, {"locale": []},
])
def test_oauth_rejects_invalid_signed_claims(
    oauth_mode: None, signing_key: str, changes: dict[str, Any],
) -> None:
    with pytest.raises(PermissionError):
        get_customer_id({"authorization": "Bearer " + access_token(signing_key, changes)})


@pytest.mark.parametrize("header", [
    {"alg": "RS256", "typ": "JWT", "kid": "test-key"},
    {"alg": "RS256", "typ": "at+jwt", "kid": "unknown"},
    {"alg": "RS256", "typ": "at+jwt", "kid": "test-key", "jku": "https://evil.example"},
])
def test_oauth_rejects_wrong_type_key_and_remote_key_headers(
    oauth_mode: None, signing_key: str, header: dict[str, Any],
) -> None:
    with pytest.raises(PermissionError):
        get_customer_id({"authorization": "Bearer " + access_token(signing_key, headers=header)})


def test_oauth_rejects_symmetric_and_unsigned_algorithms(oauth_mode: None) -> None:
    token = jwt.encode(
        {"alg": "HS256", "typ": "at+jwt", "kid": "test-key"},
        {"customer_id": "customer-1"}, OctKey.import_key("synthetic-symmetric-key-32-bytes!!"),
        algorithms=["HS256"],
    )
    with pytest.raises(PermissionError):
        get_customer_id({"authorization": "Bearer " + token})
    with pytest.raises(PermissionError):
        get_customer_id({"authorization": "Bearer eyJhbGciOiJub25lIn0.e30."})


def test_oauth_rejects_foreign_signature(oauth_mode: None) -> None:
    foreign = RSAKey.generate_key(2048, private=True).as_pem(private=True).decode()
    with pytest.raises(PermissionError):
        get_customer_id({"authorization": "Bearer " + access_token(foreign)})


def test_oauth_does_not_fall_back_to_hmac(
    oauth_mode: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", "test-only-hmac-secret")
    monkeypatch.setenv("OAUTH_ISSUER", "http://identity.example")
    with pytest.raises(RuntimeError, match="not configured"):
        get_customer_id({"authorization": "Bearer v1.payload.signature"})


def test_unknown_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_IDENTITY_MODE", "unknown")
    with pytest.raises(RuntimeError, match="Unsupported"):
        get_customer_id({})


@pytest.mark.parametrize("token", ["opaque-token", "v1.payload.signature",
                                   "eyJhbGciOiJub25lIn0.e30."])
def test_oauth_rejects_other_bearer_schemes(oauth_mode: None, token: str) -> None:
    with pytest.raises(PermissionError):
        get_customer_id({"authorization": "Bearer " + token})


@pytest.mark.parametrize("invalid", ["private", "duplicate", "malformed", "empty"])
def test_oauth_rejects_unsafe_public_keys(
    oauth_mode: None, signing_key: str, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    keys = public_jwks(signing_key, "test-key")
    if invalid == "private":
        keys["keys"][0]["d"] = "private-material"
    elif invalid == "duplicate":
        keys["keys"].append(keys["keys"][0])
    elif invalid == "malformed":
        keys["keys"][0]["n"] = "invalid"
    else:
        keys["keys"] = []
    monkeypatch.setenv("OAUTH_JWKS", json.dumps(keys))
    with pytest.raises(PermissionError):
        get_customer_id({"authorization": "Bearer " + access_token(signing_key)})


@pytest.mark.parametrize("category", ["configuration", "jwks", "bearer", "token_validation"])
def test_mcp_oauth_diagnostics_only_log_safe_categories(
    oauth_mode: None, monkeypatch: pytest.MonkeyPatch, category: str,
) -> None:
    logger = Mock()
    monkeypatch.setattr("banking_shared.oauth_identity.logger", logger)
    if category == "configuration":
        monkeypatch.setenv("OAUTH_AUDIENCE", "")
    elif category == "jwks":
        monkeypatch.setenv("OAUTH_JWKS", "sensitive-invalid-jwks")
    header = "" if category == "bearer" else "Bearer sensitive-invalid-token"
    with pytest.raises(RuntimeError if category in {"configuration", "jwks"} else PermissionError):
        get_customer_id({"authorization": header})
    logger.error.assert_called_once_with(
        "MCP OAuth verification rejected: category=" + category,
    )


def test_invalid_jwks_configuration_fails_closed(
    oauth_mode: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OAUTH_JWKS", "not-json")
    with pytest.raises(RuntimeError, match="not configured"):
        get_customer_id({"authorization": "Bearer opaque"})
