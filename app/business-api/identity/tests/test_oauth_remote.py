"""Offline remote operation contracts; never connect to Azure or PostgreSQL."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock

from azure.core.exceptions import ResourceNotFoundError
from joserfc.jwk import RSAKey
import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from banking_shared.oauth_models import OAuthClient
from identity.services.oauth_provisioning import provision_client
from identity.services.oauth_remote import initialize_oauth, migrate_database, verified_database_uri
from identity.services.service import IdentityService
from identity.settings import Settings

VAULT = "https://synthetic.vault.azure.net"
KEY_URI = VAULT + "/keys/oauth-signing/synthetic-version"
URI = "postgresql+psycopg://synthetic:synthetic@synthetic.postgres.database.azure.com/banking?sslmode=require"


@pytest.fixture
def remote_clients() -> tuple[Mock, Mock]:
    public = RSAKey.generate_key(2048, private=True).as_dict(private=False)
    def decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    keys = Mock()
    keys.get_key.return_value = SimpleNamespace(
        id=KEY_URI, properties=SimpleNamespace(enabled=True),
        key=SimpleNamespace(kty="RSA", n=decode(public["n"]), e=decode(public["e"]),
                            key_ops=["sign", "verify"]),
    )
    stored: dict[str, str] = {}
    client = Mock(vault_url=VAULT)

    def get_secret(name: str) -> SimpleNamespace:
        if name not in stored:
            raise ResourceNotFoundError("synthetic missing secret")
        return SimpleNamespace(value=stored[name], properties=SimpleNamespace(enabled=True))

    def set_secret(name: str, value: str) -> None:
        stored[name] = value

    client.get_secret.side_effect = get_secret
    client.set_secret.side_effect = set_secret
    return client, keys


def initialize(client: Mock, keys: Mock) -> str:
    return initialize_oauth(client, keys, key_uri=KEY_URI, key_id="synthetic-key",
                            jwks_name="oauth-jwks", client_secret_name="oauth-client-secret")


def test_initialize_generates_once_and_verifies_identical_rerun(
    remote_clients: tuple[Mock, Mock], capsys: pytest.CaptureFixture[str],
) -> None:
    client, keys = remote_clients
    secret = initialize(client, keys)
    assert len(secret) == 43
    assert client.set_secret.call_count == 2
    jwks = json.loads(client.set_secret.call_args_list[0].args[1])
    assert set(jwks["keys"][0]) == {"kty", "kid", "n", "e", "alg", "use"}
    assert initialize(client, keys) == secret
    assert client.set_secret.call_count == 2
    assert secret not in capsys.readouterr().out
    keys.get_key.assert_called_with("oauth-signing", "synthetic-version")


def test_initialize_rejects_jwks_change_without_writes(remote_clients: tuple[Mock, Mock]) -> None:
    client, keys = remote_clients
    initialize(client, keys)
    client.set_secret.reset_mock()
    with pytest.raises(ValueError, match="Existing JWKS differs"):
        initialize_oauth(client, keys, key_uri=KEY_URI, key_id="different-key",
                         jwks_name="oauth-jwks", client_secret_name="oauth-client-secret")
    client.set_secret.assert_not_called()


@pytest.mark.parametrize("change", ["disabled", "version", "weak", "vault"])
def test_initialize_rejects_unsafe_key_before_writes(
    remote_clients: tuple[Mock, Mock], change: str,
) -> None:
    client, keys = remote_clients
    if change == "disabled":
        keys.get_key.return_value.properties.enabled = False
    elif change == "version":
        keys.get_key.return_value.id = KEY_URI + "different"
    elif change == "weak":
        keys.get_key.return_value.key.n = b"\x01" * 128
    else:
        client.vault_url = "https://foreign.vault.azure.net"
    with pytest.raises(ValueError):
        initialize(client, keys)
    client.set_secret.assert_not_called()


@pytest.mark.parametrize("change", ["secret", "redirect", "inactive"])
def test_registration_rerun_rejects_changed_or_inactive_client(
    settings: Settings, engine: Engine, change: str,
) -> None:
    with Session(engine) as session:
        service = IdentityService(settings)
        secret = "synthetic-secret" * 4
        redirects = ["https://synthetic.invalid/callback"]
        provision_client(session, service, "synthetic", secret, redirects)
        row = session.get(OAuthClient, "synthetic")
        original_hash = row.secret_hash
        if change == "inactive":
            row.active = False
            session.commit()
        with pytest.raises(ValueError, match="already exists"):
            provision_client(session, service, "synthetic",
                             "other-secret" * 4 if change == "secret" else secret,
                             ["https://foreign.invalid/callback"] if change == "redirect" else redirects)
        assert row.secret_hash == original_hash
        assert row.redirect_uris == redirects


def test_database_target_is_exact_and_tls_required() -> None:
    assert verified_database_uri(URI, "synthetic.postgres.database.azure.com", "banking") == URI


@pytest.mark.parametrize("uri", [
    URI.replace("synthetic.postgres", "foreign.postgres"),
    URI.replace("/banking?", "/foreign?"), URI.replace("require", "disable"),
    URI + "&host=foreign.invalid", URI + "&hostaddr=127.0.0.1", URI + "&service=other",
    URI + "&options=-csearch_path%3Dforeign", URI + "&user=other",
    URI.replace("postgresql+psycopg", "sqlite"),
])
def test_database_target_mismatch_is_rejected(uri: str) -> None:
    with pytest.raises(ValueError):
        verified_database_uri(uri, "synthetic.postgres.database.azure.com", "banking")


@pytest.mark.parametrize("revision", ["20261017_0013", "future_revision"])
def test_migration_automatically_upgrades_to_checkout_head(
    monkeypatch: pytest.MonkeyPatch, revision: str,
) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(command)
        assert kwargs["env"]["DATABASE_URL"] == URI
        assert kwargs["capture_output"] is True
        return SimpleNamespace(stdout=f"{revision} (head)\n")

    monkeypatch.setattr("identity.services.oauth_remote.subprocess.run", run)
    migrate_database(URI, ".")
    assert [command[-1] for command in calls] == ["heads", revision, "current"]
    assert calls[1][-2] == "upgrade"
    assert all("--no-env-file" in command and "--frozen" in command for command in calls)
    assert all(command[command.index("-c") + 1].endswith("alembic.ini") for command in calls)


@pytest.mark.parametrize("heads", [
    "other (head)\n20261017_0013 (head)\n", "", "not-a-head", "head (head)\n",
])
def test_migration_refuses_missing_invalid_or_multiple_heads(
    monkeypatch: pytest.MonkeyPatch, heads: str,
) -> None:
    run = Mock(return_value=SimpleNamespace(stdout=heads))
    monkeypatch.setattr("identity.services.oauth_remote.subprocess.run", run)
    with pytest.raises(ValueError, match="single checkout"):
        migrate_database(URI, ".")
    assert run.call_count == 1


def test_migration_passes_only_explicit_legacy_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(stdout="20261017_0013 (head)\n"))
    monkeypatch.setattr("identity.services.oauth_remote.subprocess.run", run)
    migrate_database(URI, ".", "inactive")
    assert all("legacy-user-status=inactive" in call.args[0] for call in run.call_args_list)
    with pytest.raises(ValueError, match="Legacy-user policy"):
        migrate_database(URI, ".", "automatic")
    assert run.call_count == 3


def test_migration_rejects_unconfirmed_applied_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(side_effect=[
        SimpleNamespace(stdout="20261017_0013 (head)\n"),
        SimpleNamespace(stdout=""),
        SimpleNamespace(stdout="20261005_0012\n"),
    ])
    monkeypatch.setattr("identity.services.oauth_remote.subprocess.run", run)
    with pytest.raises(ValueError, match="revision verification"):
        migrate_database(URI, ".")
    assert run.call_count == 3


def test_registration_identical_rerun_preserves_secret_hash(
    settings: Settings, engine: Engine,
) -> None:
    with Session(engine) as session:
        service = IdentityService(settings)
        secret = "synthetic-secret" * 4
        redirects = ["https://synthetic.invalid/callback"]
        provision_client(session, service, "synthetic", secret, redirects)
        original_hash = session.get(OAuthClient, "synthetic").secret_hash
        provision_client(session, service, "synthetic", secret, redirects)
        assert session.get(OAuthClient, "synthetic").secret_hash == original_hash


def test_remote_invalid_callback_rejected_before_azure_access(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from identity.services.oauth_remote import main

    credential = Mock()
    monkeypatch.setattr("identity.services.oauth_remote.ManagedIdentityCredential", credential)
    monkeypatch.setattr("sys.argv", [
        "oauth_remote", "init-oauth", "--vault-url", VAULT,
        "--expected-host", "synthetic.postgres.database.azure.com",
        "--expected-database", "banking", "--client-id", "synthetic",
        "--redirect-uri", "http://foreign.invalid/callback", "--key-uri", KEY_URI,
        "--key-id", "synthetic-key", "--confirm",
    ])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    credential.assert_not_called()
    assert "Remote operation rejected" in capsys.readouterr().err
