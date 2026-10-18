"""Explicit fixed-client provisioning, never called at application startup."""
from __future__ import annotations

import argparse
import os
import re
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, create_engine

from banking_shared.oauth_models import OAuthClient
from identity.services.service import IdentityService
from identity.settings import Settings


def provision_client(session: Session, identity: IdentityService, client_id: str,
                     secret: str, redirects: list[str], *, allow_loopback: bool = False) -> None:
    if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id) or len(secret) < 32
            or len(secret) > 512 or not secret.strip() or not redirects or len(redirects) > 8
            or len(set(redirects)) != len(redirects)):
        raise ValueError("Invalid explicit OAuth client configuration")
    for uri in redirects:
        parsed = urlsplit(uri)
        loopback = (allow_loopback and parsed.scheme == "http"
                    and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
        if (len(uri) > 2048 or not parsed.hostname or parsed.fragment
                or parsed.username or parsed.password or (parsed.scheme != "https" and not loopback)):
            raise ValueError("Only exact HTTPS redirects or explicit offline loopback are allowed")
    if session.get(OAuthClient, client_id) is not None:
        raise ValueError("Client already exists; rotation requires separate authorization")
    session.add(OAuthClient(client_id=client_id, secret_hash=identity.passwords.hash(secret),
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
