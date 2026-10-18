"""Explicit remote operations using Azure credentials and in-memory secret custody."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import AzureCliCredential, ManagedIdentityCredential
from azure.keyvault.keys import KeyClient
from azure.keyvault.secrets import SecretClient
from joserfc.jwk import RSAKey
from pwdlib import PasswordHash
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, create_engine

from identity.services.oauth_provisioning import ensure_client, validate_client_metadata
from identity.services.oauth_signing import encoded, key_location


def read_secret(client: SecretClient, name: str) -> str | None:
    try:
        secret = client.get_secret(name)
    except ResourceNotFoundError:
        return None
    if secret.properties.enabled is not True or not secret.value:
        raise ValueError("Required Key Vault secret is disabled or empty")
    mask_secret(secret.value)
    return secret.value


def mask_secret(value: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::add-mask::{escaped}", flush=True)


def initialize_oauth(secret_client: SecretClient, key_client: KeyClient, *,
                     key_uri: str, key_id: str, jwks_name: str, client_secret_name: str) -> str:
    vault, name, version = key_location(key_uri)
    if vault != secret_client.vault_url.rstrip("/") or not key_id.strip() or len(key_id) > 128:
        raise ValueError("Key and secret vault must match; an explicit key ID is required")
    key = key_client.get_key(name, version)
    if (key.id != key_uri or key.properties.enabled is not True or key.key.kty != "RSA"
            or "sign" not in key.key.key_ops or not key.key.n or not key.key.e):
        raise ValueError("An enabled version-pinned RSA signing key is required")
    public = {"kty": "RSA", "kid": key_id, "n": encoded(bytes(key.key.n)),
              "e": encoded(bytes(key.key.e)), "alg": "RS256", "use": "sig"}
    if RSAKey.import_key(public).public_key.key_size < 2048:
        raise ValueError("RSA key must be at least 2048 bits")
    jwks = {"keys": [public]}
    previous = read_secret(secret_client, jwks_name)
    if previous is not None and json.loads(previous) != jwks:
        raise ValueError("Existing JWKS differs; rotation requires separate authorization")
    secret = read_secret(secret_client, client_secret_name)
    if secret is not None and (not secret.strip() or not 32 <= len(secret) <= 512):
        raise ValueError("Existing client secret is invalid; refusing replacement")
    if previous is None:
        secret_client.set_secret(jwks_name, json.dumps(jwks, separators=(",", ":")))
    if secret is None:
        secret = secrets.token_urlsafe(32)
        mask_secret(secret)
        secret_client.set_secret(client_secret_name, secret)
    return secret


def verified_database_uri(uri: str, expected_host: str, expected_database: str) -> str:
    url = make_url(uri)
    if url.password:
        mask_secret(url.password)
    allowed_query = {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout"}
    if (url.drivername != "postgresql+psycopg" or not url.username or not url.password
            or not expected_host.endswith(".postgres.database.azure.com")
            or url.host != expected_host or url.database != expected_database
            or not expected_database or url.port not in {None, 5432}
            or url.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}
            or set(url.query) - allowed_query):
        raise ValueError("Database URI must match the verified Azure database and require TLS")
    return uri


def register_client(uri: str, client_id: str, secret: str, redirects: list[str]) -> None:
    engine = create_engine(uri, hide_parameters=True)
    try:
        with Session(engine) as session:
            ensure_client(session, PasswordHash.recommended(), client_id, secret, redirects)
    finally:
        engine.dispose()


def migrate_database(uri: str, data_project: str,
                     legacy_user_status: str | None = None) -> None:
    project = Path(data_project).resolve(strict=True)
    if legacy_user_status not in {None, "active", "inactive"}:
        raise ValueError("Legacy-user policy must be explicitly active or inactive")
    command = ["uv", "run", "--frozen", "--no-env-file", "--directory", str(project),
               "python", "-m", "alembic", "-c", str(project / "alembic.ini")]
    if legacy_user_status is not None:
        command.extend(["-x", f"legacy-user-status={legacy_user_status}"])
    environment = {**os.environ, "DATABASE_URL": uri, "OTEL_SDK_DISABLED": "true"}
    heads = subprocess.run([*command, "heads"], env=environment, capture_output=True, text=True,
                           check=True).stdout.strip().splitlines()
    head = re.fullmatch(r"([A-Za-z0-9_]+) \(head\)", heads[0]) if len(heads) == 1 else None
    if head is None or head[1] in {"head", "heads", "base"}:
        raise ValueError("A single checkout migration head is required")
    revision = head[1]
    subprocess.run([*command, "upgrade", revision], env=environment, capture_output=True,
                   text=True, check=True)
    current = subprocess.run([*command, "current"], env=environment, capture_output=True,
                             text=True, check=True).stdout.strip().splitlines()
    if current != [f"{revision} (head)"]:
        raise ValueError("Database revision verification did not match the checkout head")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["init-oauth", "register-client", "migrate"])
    parser.add_argument("--credential", choices=["managed-identity", "azure-cli"],
                        default="managed-identity")
    parser.add_argument("--vault-url", required=True)
    parser.add_argument("--database-secret-name", default="database-url")
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--client-secret-name", default="oauth-client-secret")
    parser.add_argument("--jwks-secret-name", default="oauth-jwks")
    parser.add_argument("--client-id")
    parser.add_argument("--redirect-uri", action="append")
    parser.add_argument("--key-uri")
    parser.add_argument("--key-id")
    parser.add_argument("--data-project")
    parser.add_argument("--legacy-user-status", choices=["active", "inactive"])
    parser.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args()
    try:
        if args.operation == "migrate" and not args.data_project:
            raise ValueError("Migration project is required")
        if args.operation != "migrate" and (not args.client_id or not args.redirect_uri):
            raise ValueError("Explicit client ID and HTTPS redirect are required")
        if args.operation != "migrate":
            validate_client_metadata(args.client_id, args.redirect_uri)
        if args.operation == "init-oauth" and (not args.key_uri or not args.key_id):
            raise ValueError("Pinned key URI and key ID are required")
        credential = AzureCliCredential() if args.credential == "azure-cli" else ManagedIdentityCredential()
        with credential, SecretClient(args.vault_url, credential, logging_enable=False) as client:
            uri = read_secret(client, args.database_secret_name)
            if uri is None:
                raise ValueError("Database secret must already exist")
            verified_database_uri(uri, args.expected_host, args.expected_database)
            if args.operation == "migrate":
                migrate_database(uri, args.data_project, args.legacy_user_status)
            else:
                if args.operation == "init-oauth":
                    vault, _, _ = key_location(args.key_uri)
                    with KeyClient(vault, credential, logging_enable=False) as keys:
                        secret = initialize_oauth(client, keys, key_uri=args.key_uri,
                                                  key_id=args.key_id, jwks_name=args.jwks_secret_name,
                                                  client_secret_name=args.client_secret_name)
                else:
                    secret = read_secret(client, args.client_secret_name)
                    if secret is None:
                        raise ValueError("Client secret must already exist")
                register_client(uri, args.client_id, secret, args.redirect_uri)
    except (AzureError, ValueError, SQLAlchemyError, subprocess.SubprocessError, OSError):
        parser.exit(1, "Remote operation rejected; check authorized configuration, access and schema.\n")
    print("Remote operation completed and verified")


if __name__ == "__main__":
    main()
