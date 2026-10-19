"""Offline OAuth grant, boundary and lifecycle regressions."""
from __future__ import annotations

from base64 import urlsafe_b64decode
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from azure.core.exceptions import ServiceRequestError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, utils
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from joserfc.jwk import RSAKey
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlmodel import Session, select

from banking_shared import User
from banking_shared.identity_models import utc_now
from banking_shared.oauth_models import OAuthAccessToken, OAuthAuthorizationCode, OAuthClient
from banking_shared.oauth_jwt import validate_access_token
from identity.services.oauth import CodeGrant, OAuthServer, is_expired
from identity.services.oauth_provisioning import provision_client
from identity.services.service import IdentityService
from identity.settings import Settings

SECRET = "synthetic-client-secret-" + "s" * 32
VERIFIER = "v" * 48
REDIRECT = "https://client.synthetic.invalid/callback"


@pytest.mark.parametrize("offset_hours", [-5, 0, 5])
@pytest.mark.parametrize("seconds, expected", [(-1, True), (0, True), (300, False)])
def test_expiry_preserves_aware_timestamp_instant(
    monkeypatch: pytest.MonkeyPatch, offset_hours: int, seconds: int, expected: bool,
) -> None:
    now = datetime(2026, 10, 18, 3, 18, tzinfo=timezone.utc)
    expiry = (now + timedelta(seconds=seconds)).astimezone(
        timezone(timedelta(hours=offset_hours)),
    )
    monkeypatch.setattr("identity.services.oauth.utc_now", lambda: now)

    assert is_expired(expiry) is expected


@pytest.mark.parametrize("seconds, expected", [(-1, True), (0, True), (300, False)])
def test_expiry_treats_naive_sqlite_timestamp_as_utc(
    monkeypatch: pytest.MonkeyPatch, seconds: int, expected: bool,
) -> None:
    now = datetime(2026, 10, 18, 3, 18, tzinfo=timezone.utc)
    expiry = (now + timedelta(seconds=seconds)).replace(tzinfo=None)
    monkeypatch.setattr("identity.services.oauth.utc_now", lambda: now)

    assert is_expired(expiry) is expected


@pytest.fixture
def configured(settings: Settings, engine: Engine) -> None:
    settings.oauth_issuer = "https://identity.synthetic.invalid"
    settings.oauth_frontend_authorize_url = "https://web.synthetic.invalid/oauth/consent"
    settings.oauth_audience = "banking-mcp"
    settings.oauth_signing_key_id = "synthetic-key"
    settings.oauth_signing_private_key = SecretStr(
        RSAKey.generate_key(2048, private=True).as_pem(private=True).decode(),
    )
    with Session(engine) as session:
        provision_client(session, IdentityService(settings), "synthetic-client", SECRET, [REDIRECT])


@pytest.fixture
def authorization() -> dict[str, object]:
    return {"client_id": "synthetic-client", "redirect_uri": REDIRECT, "response_type": "code",
            "scope": "mcp.access", "state": "synthetic-state",
            "code_challenge": create_s256_code_challenge(VERIFIER),
            "code_challenge_method": "S256", "approved": True}


def code(client: TestClient, tokens: dict[str, str], authorization: dict[str, object]) -> str:
    response = client.post("/oauth/authorize", json=authorization,
                           headers={"Authorization": "Bearer " + tokens["customer"]})
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["redirect_uri"]).query)["code"][0]


def exchange(client: TestClient, value: str, **changes: str) -> Response:
    data = {"grant_type": "authorization_code", "code": value, "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER}
    data.update(changes)
    return client.post("/oauth/token", data=data, auth=("synthetic-client", SECRET))


def test_oauth_disabled_preserves_login(client: TestClient, tokens: dict[str, str]) -> None:
    assert client.get("/.well-known/oauth-authorization-server").status_code == 404
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + tokens["customer"]}).status_code == 200


def test_code_exchange_hashing_single_use_and_profile(configured: None, client: TestClient,
        tokens: dict[str, str], authorization: dict[str, object], engine: Engine,
        settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    issued_at = utc_now()
    monkeypatch.setattr("identity.services.oauth.utc_now", lambda: issued_at)
    value = code(client, tokens, authorization)
    response = exchange(client, value)
    assert response.status_code == 200, response.text
    assert response.json()["expires_in"] == 3600
    assert response.json()["scope"] == "mcp.access"
    assert "refresh_token" not in response.json()
    token = response.json()["access_token"]
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["jwks_uri"] == settings.oauth_issuer + "/oauth/jwks"
    keys = client.get("/oauth/jwks").json()
    assert set(keys["keys"][0]) == {"kty", "kid", "n", "e", "alg", "use"}
    claims = validate_access_token(token, keys, settings.oauth_issuer, settings.oauth_audience)
    assert claims["customer_id"] == "synthetic-customer"
    assert claims["client_id"] == "synthetic-client"
    assert claims["exp"] - claims["iat"] == 3600
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    with Session(engine) as session:
        stored_code = session.exec(select(OAuthAuthorizationCode)).one()
        stored_token = session.exec(select(OAuthAccessToken)).one()
        assert stored_code.code_hash != value and stored_code.consumed
        assert stored_code.expires_at.replace(tzinfo=timezone.utc) == (
            issued_at + timedelta(seconds=300)
        )
        assert stored_token.expires_at.replace(tzinfo=timezone.utc).timestamp() == claims["exp"]
        assert stored_token.token_hash != token
        assert session.get(OAuthClient, "synthetic-client").secret_hash != SECRET
    assert exchange(client, value).status_code == 400
    headers = {"Authorization": "Bearer " + settings.auth_internal_secret.get_secret_value()}
    profile = client.post("/internal/oauth/introspect", json={"token": token}, headers=headers)
    assert profile.status_code == 200
    assert profile.json()["customer_id"] == "synthetic-customer"
    assert profile.json()["role"] == "customer"
    assert client.post("/internal/oauth/introspect", json={"token": token}).status_code == 401
    with Session(engine) as session:
        user = session.get(User, "customer")
        user.identity_version += 1
        session.add(user)
        session.commit()
    assert client.post("/internal/oauth/introspect", json={"token": token}, headers=headers).status_code == 401
    # MCP accepts the signed snapshot until expiry, even after live identity revocation.
    assert validate_access_token(token, keys, settings.oauth_issuer,
                                 settings.oauth_audience)["identity_version"] == 1


@pytest.mark.parametrize("failure", [False, True])
def test_native_exchange_preserves_token_contract_and_rolls_back_signing_failure(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object], engine: Engine, settings: Settings,
    monkeypatch: pytest.MonkeyPatch, failure: bool,
) -> None:
    key = RSAKey.import_key(settings.oauth_signing_private_key.get_secret_value())
    public = key.as_dict(private=False)

    def raw(value: str) -> bytes:
        return urlsafe_b64decode(value + "=" * (-len(value) % 4))

    uri = "https://synthetic.vault.azure.net/keys/oauth-signing/synthetic-version"
    settings.oauth_signing_backend = "key_vault"
    settings.oauth_signing_key_uri = uri
    settings.oauth_signing_private_key = SecretStr("")
    remote = SimpleNamespace(
        id=uri, properties=SimpleNamespace(enabled=True),
        key=SimpleNamespace(kty="RSA", key_ops=["sign", "verify"],
                            n=raw(public["n"]), e=raw(public["e"])),
    )
    crypto = Mock()

    def sign(algorithm: object, digest: bytes) -> SimpleNamespace:
        return SimpleNamespace(key_id=uri, signature=key.private_key.sign(
            digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256())))

    crypto.sign.side_effect = sign
    monkeypatch.setattr("identity.services.oauth_signing.ManagedIdentityCredential", Mock())
    monkeypatch.setattr("identity.services.oauth_signing.KeyClient", Mock(
        return_value=Mock(get_key=Mock(return_value=remote))))
    monkeypatch.setattr("identity.services.oauth_signing.CryptographyClient", Mock(return_value=crypto))
    value = code(client, tokens, authorization)
    if failure:
        crypto.sign.side_effect = ServiceRequestError("sensitive upstream detail")
        response = exchange(client, value)
        assert response.status_code == 503
        assert response.json() == {"detail": {"code": "OAUTH_UNAVAILABLE"}}
        with Session(engine) as session:
            assert not session.exec(select(OAuthAuthorizationCode)).one().consumed
            assert not session.exec(select(OAuthAccessToken)).all()
        crypto.sign.side_effect = sign
    response = exchange(client, value)
    assert response.status_code == 200
    claims = validate_access_token(response.json()["access_token"],
        client.get("/oauth/jwks").json(), settings.oauth_issuer, settings.oauth_audience)
    with Session(engine) as session:
        stored = session.exec(select(OAuthAccessToken)).one()
        assert stored.expires_at.replace(tzinfo=timezone.utc).timestamp() == claims["exp"]
    assert claims["customer_id"] == "synthetic-customer"
    assert claims["exp"] - claims["iat"] == 3600
    assert exchange(client, value).status_code == 400


@pytest.mark.parametrize("changes", [{"redirect_uri": REDIRECT + "/foreign"},
    {"code_verifier": "x" * 48}, {"code_verifier": ""}, {"grant_type": "refresh_token"}])
def test_invalid_exchange_does_not_consume_code(configured: None, client: TestClient,
        tokens: dict[str, str], authorization: dict[str, object], changes: dict[str, str]) -> None:
    value = code(client, tokens, authorization)
    assert exchange(client, value, **changes).status_code == 400
    assert exchange(client, value).status_code == 200


@pytest.mark.parametrize("changes", [{"redirect_uri": "https://foreign.invalid/"},
    {"scope": "offline_access"}, {"scope": "banking.read"},
    {"response_type": "token"}, {"code_challenge_method": "plain"}])
def test_invalid_authorization_rejected(configured: None, client: TestClient,
        tokens: dict[str, str], authorization: dict[str, object], changes: dict[str, str]) -> None:
    authorization.update(changes)
    response = client.post("/oauth/authorize", json=authorization,
                           headers={"Authorization": "Bearer " + tokens["customer"]})
    assert response.status_code in {400, 422}


@pytest.mark.parametrize("changes, expected", [
    ({"client_id": "unregistered-synthetic-client"}, "invalid_client"),
    ({"scope": "offline_access"}, "invalid_scope"),
    ({"redirect_uri": "https://foreign.invalid/"}, "invalid_request"),
])
def test_authorization_rejection_logs_only_controlled_code(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object], monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, str], expected: str,
) -> None:
    logger = Mock()
    monkeypatch.setattr("identity.oauth_routes.logger", logger)
    authorization.update(changes)

    response = client.post("/oauth/authorize", json=authorization,
                           headers={"Authorization": "Bearer " + tokens["customer"]})

    assert response.status_code == 400
    assert response.json() == {"detail": {"code": "OAUTH_INVALID_REQUEST"}}
    logger.error.assert_called_once_with(
        "Identity OAuth %s rejected: code=%s", "authorization", expected,
    )


@pytest.mark.parametrize("category", ["unregistered", "inactive"])
def test_client_lookup_explains_missing_or_inactive_registration(
    configured: None, settings: Settings, engine: Engine,
    monkeypatch: pytest.MonkeyPatch, category: str,
) -> None:
    logger = Mock()
    monkeypatch.setattr("identity.services.oauth.logger", logger)
    with Session(engine) as session:
        row = session.get(OAuthClient, "synthetic-client")
        assert row is not None
        if category == "unregistered":
            session.delete(row)
        else:
            row.active = False
            session.add(row)
        session.commit()
        assert OAuthServer(session, IdentityService(settings)).query_client("synthetic-client") is None
    logger.error.assert_called_once_with("Identity OAuth client rejected: category=%s", category)


def test_unregistered_client_leaves_oauth_tables_empty(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object], engine: Engine,
) -> None:
    with Session(engine) as session:
        session.delete(session.get(OAuthClient, "synthetic-client"))
        session.commit()
    response = client.post("/oauth/authorize", json=authorization,
                           headers={"Authorization": "Bearer " + tokens["customer"]})
    assert response.status_code == 400
    with Session(engine) as session:
        for model in (OAuthClient, OAuthAuthorizationCode, OAuthAccessToken):
            assert session.exec(select(model)).all() == []


def test_token_and_introspection_diagnostics_preserve_rejection(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object], settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr("identity.oauth_routes.logger", logger)
    value = code(client, tokens, authorization)
    assert exchange(client, value, code_verifier="x" * 48).status_code == 400
    logger.error.assert_called_once_with(
        "Identity OAuth %s rejected: code=%s", "token", "invalid_grant",
    )
    logger.reset_mock()
    response = client.post("/internal/oauth/introspect", json={"token": "sensitive-unknown"},
                           headers={"Authorization": "Bearer " +
                                    settings.auth_internal_secret.get_secret_value()})
    assert response.status_code == 401
    logger.error.assert_called_once_with(
        "Identity OAuth introspection rejected: category=grant_identity",
    )


@pytest.mark.parametrize("category", [
    "code_not_found_for_client", "code_consumed", "code_expired",
    "redirect_binding", "pkce_binding", "customer_snapshot",
])
def test_token_rejection_identifies_safe_stage(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object], engine: Engine,
    monkeypatch: pytest.MonkeyPatch, category: str,
) -> None:
    value = code(client, tokens, authorization)
    changes: dict[str, str] = {}
    with Session(engine) as session:
        row = session.exec(select(OAuthAuthorizationCode)).one()
        if category == "code_not_found_for_client":
            value = "unknown-synthetic-code"
        elif category == "code_consumed":
            row.consumed = True
        elif category == "code_expired":
            row.expires_at = utc_now() - timedelta(seconds=1)
        elif category == "redirect_binding":
            changes["redirect_uri"] = REDIRECT + "/different"
        elif category == "pkce_binding":
            changes["code_verifier"] = "x" * 48
        else:
            user = session.get(User, "customer")
            assert user is not None
            user.identity_version += 1
            session.add(user)
        session.add(row)
        session.commit()
    logger = Mock()
    monkeypatch.setattr("identity.services.oauth.logger", logger)

    response = exchange(client, value, **changes)

    assert response.status_code == 400
    logger.error.assert_called_once_with(
        "Identity OAuth token rejected: category=%s", category,
    )
    with Session(engine) as session:
        assert session.exec(select(OAuthAccessToken)).all() == []
        stored = session.exec(select(OAuthAuthorizationCode)).one()
        assert stored.consumed is (category == "code_consumed")


def test_consumption_conflict_rolls_back_token_and_logs_safe_category(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object], engine: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = code(client, tokens, authorization)
    original = CodeGrant.delete_authorization_code

    def expire_before_consumption(
        grant: CodeGrant, record: OAuthAuthorizationCode,
    ) -> None:
        record.expires_at = utc_now() - timedelta(seconds=1)
        grant.server.session.add(record)
        grant.server.session.flush()
        original(grant, record)

    logger = Mock()
    monkeypatch.setattr("identity.services.oauth.logger", logger)
    monkeypatch.setattr(CodeGrant, "delete_authorization_code", expire_before_consumption)

    assert exchange(client, value).status_code == 400
    logger.error.assert_called_once_with(
        "Identity OAuth token rejected: category=code_consumption_conflict",
    )
    with Session(engine) as session:
        assert session.exec(select(OAuthAccessToken)).all() == []
        assert not session.exec(select(OAuthAuthorizationCode)).one().consumed


def test_successful_exchange_has_no_rejection_diagnostics(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = code(client, tokens, authorization)
    logger = Mock()
    monkeypatch.setattr("identity.services.oauth.logger", logger)

    assert exchange(client, value).status_code == 200
    logger.error.assert_not_called()


def test_consent_denial_staff_and_landing(configured: None, client: TestClient,
        tokens: dict[str, str], authorization: dict[str, object], engine: Engine) -> None:
    authorization["approved"] = False
    response = client.post("/oauth/authorize", json=authorization,
                           headers={"Authorization": "Bearer " + tokens["customer"]})
    assert response.status_code == 200
    assert parse_qs(urlsplit(response.json()["redirect_uri"]).query)["error"] == ["access_denied"]
    with Session(engine) as session:
        assert session.exec(select(OAuthAuthorizationCode)).first() is None
    for role in ("operator", "admin"):
        assert client.post("/oauth/authorize", json={**authorization, "approved": True},
            headers={"Authorization": "Bearer " + tokens[role]}).status_code == 401
    with Session(engine) as session:
        assert session.exec(select(OAuthAuthorizationCode)).first() is None
    params = {key: value for key, value in authorization.items() if key != "approved"}
    landing = client.get("/oauth/authorize", params=params, follow_redirects=False)
    assert landing.status_code == 302
    assert landing.headers["location"].startswith("https://web.synthetic.invalid/oauth/consent?")
    assert client.get("/oauth/authorize", params={**params, "token": "never-forward"}).status_code == 400


def test_expired_code_and_post_client_auth(configured: None, client: TestClient,
        tokens: dict[str, str], authorization: dict[str, object], engine: Engine) -> None:
    value = code(client, tokens, authorization)
    with Session(engine) as session:
        row = session.exec(select(OAuthAuthorizationCode)).one()
        row.expires_at = utc_now() - timedelta(seconds=1)
        session.add(row)
        session.commit()
    assert exchange(client, value).status_code == 400
    value = code(client, tokens, authorization)
    response = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": value,
        "redirect_uri": REDIRECT, "code_verifier": VERIFIER,
        "client_id": "synthetic-client", "client_secret": SECRET})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("invalidated", ["expired", "inactive", "client_disabled", "profile"])
def test_access_introspection_rechecks_live_bindings(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object], engine: Engine, settings: Settings, invalidated: str,
) -> None:
    token = exchange(client, code(client, tokens, authorization)).json()["access_token"]
    with Session(engine) as session:
        if invalidated == "expired":
            row = session.exec(select(OAuthAccessToken)).one()
            row.expires_at = utc_now() - timedelta(seconds=1)
        elif invalidated == "client_disabled":
            row = session.get(OAuthClient, "synthetic-client")
            row.active = False
        else:
            row = session.get(User, "customer")
            if invalidated == "inactive":
                row.status = "inactive"
            else:
                row.locale = "en"
        session.add(row)
        session.commit()
    response = client.post(
        "/internal/oauth/introspect", json={"token": token},
        headers={"Authorization": "Bearer " + settings.auth_internal_secret.get_secret_value()},
    )
    assert response.status_code == 401


def test_stale_code_and_invalid_client_auth_do_not_issue_tokens(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object], engine: Engine,
) -> None:
    value = code(client, tokens, authorization)
    data = {"grant_type": "authorization_code", "code": value, "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER}
    assert client.post("/oauth/token", data=data).status_code in {400, 401}
    assert client.post("/oauth/token", data=data,
                       auth=("synthetic-client", "incorrect-secret")).status_code == 401
    assert client.post("/oauth/token", data={**data, "client_secret": SECRET},
                       auth=("synthetic-client", SECRET)).status_code == 400
    with Session(engine) as session:
        user = session.get(User, "customer")
        user.identity_version += 1
        session.add(user)
        session.commit()
    assert exchange(client, value).status_code == 400
    with Session(engine) as session:
        assert session.exec(select(OAuthAccessToken)).first() is None


def test_consumption_conflict_rolls_back_generated_token(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object], engine: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from authlib.oauth2.rfc6749.errors import InvalidGrantError
    from identity.services.oauth import CodeGrant

    value = code(client, tokens, authorization)

    def conflict(self: CodeGrant, record: OAuthAuthorizationCode) -> None:
        raise InvalidGrantError()

    with monkeypatch.context() as patch:
        patch.setattr(CodeGrant, "delete_authorization_code", conflict)
        assert exchange(client, value).status_code == 400
    with Session(engine) as session:
        assert session.exec(select(OAuthAccessToken)).first() is None
    assert exchange(client, value).status_code == 200


def test_provisioning_is_explicit_and_refuses_overwrite(
    settings: Settings, engine: Engine,
) -> None:
    with Session(engine) as session:
        service = IdentityService(settings)
        assert session.exec(select(OAuthClient)).first() is None
        callback = "http://127.0.0.1:9000/callback"
        with pytest.raises(ValueError):
            provision_client(session, service, "offline", SECRET, [callback])
        provision_client(session, service, "offline", SECRET, [callback], allow_loopback=True)
        existing_hash = session.get(OAuthClient, "offline").secret_hash
        provision_client(session, service, "offline", SECRET, [callback], allow_loopback=True)
        assert session.get(OAuthClient, "offline").secret_hash == existing_hash
        with pytest.raises(ValueError, match="already exists"):
            provision_client(session, service, "offline", "different" * 8, [callback],
                             allow_loopback=True)


def test_token_issuance_does_not_log_access_token(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object], caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG", logger="authlib")
    response = exchange(client, code(client, tokens, authorization))
    assert response.status_code == 200
    assert response.json()["access_token"] not in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize("setting,value", [
    ("oauth_audience", ""), ("oauth_signing_key_id", ""),
    ("oauth_signing_private_key", SecretStr("")),
    ("oauth_signing_private_key", SecretStr("invalid-signing-material")),
    ("oauth_issuer", "https://identity.synthetic.invalid/?query=yes"),
    ("oauth_issuer", "https://identity.synthetic.invalid/"),
])
def test_oauth_signing_configuration_fails_closed_without_breaking_login(
    configured: None, settings: Settings, client: TestClient, tokens: dict[str, str],
    setting: str, value: str | SecretStr,
) -> None:
    setattr(settings, setting, value)
    assert client.get("/.well-known/oauth-authorization-server").status_code == 503
    assert client.get("/oauth/jwks").status_code == 503
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + tokens["customer"]}).status_code == 200


@pytest.mark.parametrize("private,bits", [(False, 2048), (True, 1024)])
def test_oauth_rejects_public_only_and_weak_signing_keys(
    configured: None, settings: Settings, client: TestClient, private: bool, bits: int,
) -> None:
    key = RSAKey.generate_key(bits, private=True)
    settings.oauth_signing_private_key = SecretStr(key.as_pem(private=private).decode())
    assert client.get("/oauth/jwks").status_code == 503


def test_duplicate_protocol_fields_are_rejected(
    configured: None, client: TestClient, tokens: dict[str, str],
    authorization: dict[str, object],
) -> None:
    value = code(client, tokens, authorization)
    response = client.post(
        "/oauth/token", content="grant_type=authorization_code&code=" + value + "&code=other",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=("synthetic-client", SECRET),
    )
    assert response.status_code == 400
    assert exchange(client, value).status_code == 200
    params = [(key, str(value)) for key, value in authorization.items() if key != "approved"]
    params.append(("state", "other"))
    assert client.get("/oauth/authorize", params=params).status_code == 400


@pytest.mark.parametrize("redirect", ["http://remote.invalid/callback", "https://client.invalid/#fragment",
                                      "https://user:password@client.invalid/callback"])
def test_provisioning_rejects_unsafe_redirects(settings: Settings, engine: Engine,
                                              redirect: str) -> None:
    with Session(engine) as session, pytest.raises(ValueError):
        provision_client(session, IdentityService(settings), "unsafe", SECRET, [redirect])
