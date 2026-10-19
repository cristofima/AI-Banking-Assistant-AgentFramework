"""Explicit fixed-client provisioning, never called at application startup."""
from __future__ import annotations

import argparse
import os
import re
from urllib.parse import urlsplit

from pwdlib import PasswordHash
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, create_engine

from banking_shared.oauth_models import OAuthClient
from identity.services.service import IdentityService
from identity.settings import Settings


def provision_client(session: Session, identity: IdentityService, client_id: str,
                     secret: str, redirects: list[str], *, allow_loopback: bool = False) -> None:
    ensure_client(session, identity.passwords, client_id, secret, redirects,
                  allow_loopback=allow_loopback)


def validate_client_metadata(client_id: str, redirects: list[str], *,
                             allow_loopback: bool = False) -> None:
    if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id)
            or not redirects or len(redirects) > 8
            or len(set(redirects)) != len(redirects)):
        raise ValueError("Invalid explicit OAuth client configuration")
    for uri in redirects:
        parsed = urlsplit(uri)
        loopback = (allow_loopback and parsed.scheme == "http"
                    and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
        if (len(uri) > 2048 or not parsed.hostname or parsed.fragment
                or parsed.username or parsed.password or (parsed.scheme != "https" and not loopback)):
            raise ValueError("Only exact HTTPS redirects or explicit offline loopback are allowed")


def ensure_client(session: Session, passwords: PasswordHash, client_id: str,
                  secret: str, redirects: list[str], *, allow_loopback: bool = False) -> None:
    validate_client_metadata(client_id, redirects, allow_loopback=allow_loopback)
    if not 32 <= len(secret) <= 512 or not secret.strip():
        raise ValueError("Invalid explicit OAuth client secret")
    existing = session.get(OAuthClient, client_id)
    if existing is not None:
        if (not existing.active or set(existing.redirect_uris) != set(redirects)
                or not passwords.verify(secret, existing.secret_hash)):
            raise ValueError("Client already exists with different credentials/metadata or is inactive")
        return
    session.add(OAuthClient(client_id=client_id, secret_hash=passwords.hash(secret),
                            redirect_uris=redirects))
    session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Explicit fixed OAuth client provisioning")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--redirect-uri", action="append", required=True)
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--confirm-provision", action="store_true", required=True)
    args = parser.parse_args()
    engine = None
    try:
        config = Settings()
        secret = os.environ.get("OAUTH_CLIENT_SECRET", "")
        engine = create_engine(config.database_url.get_secret_value())
        with Session(engine) as session:
            provision_client(session, IdentityService(config), args.client_id, secret,
                             args.redirect_uri, allow_loopback=args.allow_loopback_http)
    except (ValidationError, ValueError, SQLAlchemyError):
        parser.exit(1, "OAuth client provisioning rejected; check explicit input and migrations\n")
    finally:
        if engine is not None:
            engine.dispose()
    print("OAuth client provisioned")


if __name__ == "__main__":
    main()
