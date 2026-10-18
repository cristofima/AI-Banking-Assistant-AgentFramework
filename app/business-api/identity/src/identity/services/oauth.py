"""Authlib authorization-code grant backed by ORM-only, atomic persistence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import logging
from secrets import token_urlsafe
from typing import Any

from authlib.oauth2.rfc6749 import AuthorizationServer, ClientMixin, OAuth2Request
from authlib.oauth2.rfc6749.errors import InvalidGrantError
from authlib.oauth2.rfc6749.grants import AuthorizationCodeGrant
from authlib.oauth2.rfc6749.requests import BasicOAuth2Payload
from authlib.oauth2.rfc7636 import CodeChallenge
from fastapi import HTTPException
from joserfc import jwt
from joserfc.jwk import RSAKey
from pwdlib.exceptions import UnknownHashError
from sqlmodel import Session, select, update

from banking_shared.identity_models import User, utc_now
from banking_shared.oauth_models import OAuthAccessToken, OAuthAuthorizationCode, OAuthClient
from banking_shared.oauth_jwt import (
    OAUTH_CODE_TTL_SECONDS, OAUTH_SCOPE, OAUTH_TTL_SECONDS, public_jwks,
)
from identity.models.schemas import CustomerProfile
from identity.services.service import IdentityService, unauthorized

logger = logging.getLogger("uvicorn.error.identity.oauth")


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def is_expired(value: datetime) -> bool:
    # SQLite returns naive UTC; PostgreSQL may return an aware non-UTC timestamp.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc) <= utc_now()


class Client(ClientMixin):
    def __init__(self, row: OAuthClient, identity: IdentityService) -> None:
        self.row = row
        self.identity = identity
        self.client_id = row.client_id

    def get_client_id(self) -> str:
        return self.client_id

    def get_default_redirect_uri(self) -> None:
        return None

    def get_allowed_scope(self, scope: str | None) -> str:
        return OAUTH_SCOPE if scope is None or scope == OAUTH_SCOPE else ""

    def check_redirect_uri(self, redirect_uri: str) -> bool:
        return redirect_uri in self.row.redirect_uris

    def check_client_secret(self, client_secret: str) -> bool:
        try:
            return self.identity.passwords.verify(client_secret, self.row.secret_hash)
        except (ValueError, TypeError, UnknownHashError):
            return False

    def check_endpoint_auth_method(self, method: str, endpoint: str) -> bool:
        return endpoint == "token" and method in {"client_secret_basic", "client_secret_post"}

    def check_response_type(self, response_type: str) -> bool:
        return response_type == "code"

    def check_grant_type(self, grant_type: str) -> bool:
        return grant_type == "authorization_code"


class ProtocolRequest(OAuth2Request):
    def __init__(self, method: str, uri: str, data: dict[str, str],
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(method, uri, headers=headers)
        self.payload = BasicOAuth2Payload(data)

    @property
    def form(self) -> dict[str, str]:
        return self.payload.data

    @property
    def args(self) -> dict[str, str]:
        return self.payload.data


class CodeGrant(AuthorizationCodeGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = ["client_secret_basic", "client_secret_post"]

    def validate_token_request(self) -> None:
        try:
            super().validate_token_request()
        except InvalidGrantError as exc:
            categories = {
                "Invalid 'redirect_uri' in request.": "redirect_binding",
                "Code challenge failed.": "pkce_binding",
            }
            category = categories.get(exc.description)
            if category is not None:
                logger.error("Identity OAuth token rejected: category=%s", category)
            raise

    def create_token_response(self) -> tuple[int, dict[str, object], list[tuple[str, str]]]:
        # Authlib's default response logs the raw access token at debug level.
        user = self.authenticate_user(self.request.authorization_code)
        if user is None:
            logger.error("Identity OAuth token rejected: category=%s", "customer_snapshot")
            raise InvalidGrantError()
        self.request.user = user
        token = self.generate_token(user=user, scope=self.request.authorization_code.get_scope(),
                                    include_refresh_token=False)
        self.save_token(token)
        self.delete_authorization_code(self.request.authorization_code)
        return 200, token, self.TOKEN_RESPONSE_HEADER

    def save_authorization_code(self, code: str, request: ProtocolRequest) -> None:
        server: OAuthServer = self.server
        profile = server.identity.customer_profile(server.session, request.user)
        server.session.add(OAuthAuthorizationCode(
            code_hash=digest(code), client_id=request.client.client_id, user_id=request.user.id,
            identity_version=request.user.identity_version,
            profile_snapshot=server.snapshot(profile), redirect_uri=request.payload.redirect_uri,
            scope=request.scope, code_challenge=request.payload.data["code_challenge"],
            expires_at=utc_now() + timedelta(seconds=OAUTH_CODE_TTL_SECONDS),
        ))

    def query_authorization_code(self, code: str, client: Client) -> OAuthAuthorizationCode | None:
        server: OAuthServer = self.server
        row = server.session.exec(select(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.code_hash == digest(code),
            OAuthAuthorizationCode.client_id == client.client_id,
        ).with_for_update()).first()
        category = None
        if row is None:
            category = "code_not_found_for_client"
        elif row.consumed:
            category = "code_consumed"
        elif is_expired(row.expires_at):
            category = "code_expired"
        if category is not None:
            logger.error("Identity OAuth token rejected: category=%s", category)
            return None
        return row

    def authenticate_user(self, authorization_code: OAuthAuthorizationCode) -> User | None:
        server: OAuthServer = self.server
        return server.current_user(authorization_code)

    def delete_authorization_code(self, authorization_code: OAuthAuthorizationCode) -> None:
        server: OAuthServer = self.server
        # Conditional consumption also protects databases where FOR UPDATE is unavailable.
        result = server.session.exec(update(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.code_hash == authorization_code.code_hash,
            OAuthAuthorizationCode.consumed.is_(False),
            OAuthAuthorizationCode.expires_at > utc_now(),
        ).values(consumed=True).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            logger.error("Identity OAuth token rejected: category=code_consumption_conflict")
            raise InvalidGrantError()


class OAuthServer(AuthorizationServer):
    def __init__(self, session: Session, identity: IdentityService) -> None:
        super().__init__(scopes_supported=[OAUTH_SCOPE])
        self.session = session
        self.identity = identity
        self.register_grant(CodeGrant, [CodeChallenge(required=True)])
        self.register_token_generator("default", self.generate_token)

    @staticmethod
    def snapshot(profile: CustomerProfile) -> dict[str, object]:
        return profile.model_dump(mode="json", exclude={"updated_at", "name", "status"})

    def current_user(self, record: OAuthAuthorizationCode | OAuthAccessToken) -> User | None:
        user = self.session.exec(select(User).where(User.id == record.user_id)
                                 .execution_options(populate_existing=True)).first()
        if user is None or user.status != "active" or user.identity_version != record.identity_version:
            return None
        try:
            profile = self.identity.customer_profile(self.session, user)
        except HTTPException as exc:
            if exc.status_code == 401:
                return None
            raise
        return user if self.snapshot(profile) == record.profile_snapshot else None

    def query_client(self, client_id: str) -> Client | None:
        row = self.session.get(OAuthClient, client_id)
        if row is None or not row.active:
            logger.error("Identity OAuth client rejected: category=%s",
                         "unregistered" if row is None else "inactive")
            return None
        return Client(row, self.identity)

    def create_oauth2_request(self, request: ProtocolRequest) -> ProtocolRequest:
        return request

    def handle_response(self, status_code: int, body: Any,
                        headers: list[tuple[str, str]]) -> tuple[int, Any, list[tuple[str, str]]]:
        return status_code, body, headers

    def send_signal(self, name: str, **kwargs: Any) -> None:
        pass

    def generate_token(self, **kwargs: Any) -> dict[str, object]:
        settings = self.identity.settings
        material = settings.oauth_signing_private_key.get_secret_value()
        public_jwks(material, settings.oauth_signing_key_id)
        profile = self.identity.customer_profile(self.session, kwargs["user"])
        issued = int(utc_now().timestamp())
        claims = {**self.snapshot(profile), "iss": settings.oauth_issuer.rstrip("/"),
                  "aud": settings.oauth_audience, "iat": issued,
                  "exp": issued + OAUTH_TTL_SECONDS, "jti": token_urlsafe(24),
                  "client_id": kwargs["client"].client_id, "scope": OAUTH_SCOPE}
        token = jwt.encode({"alg": "RS256", "typ": "at+jwt",
                            "kid": settings.oauth_signing_key_id}, claims,
                           RSAKey.import_key(material), algorithms=["RS256"])
        return {"access_token": token, "token_type": "Bearer",
                "expires_in": OAUTH_TTL_SECONDS, "scope": OAUTH_SCOPE}

    def save_token(self, token: dict[str, object], request: ProtocolRequest) -> None:
        profile = self.identity.customer_profile(self.session, request.user)
        self.session.add(OAuthAccessToken(
            token_hash=digest(str(token["access_token"])), client_id=request.client.client_id,
            user_id=request.user.id, identity_version=request.user.identity_version,
            profile_snapshot=self.snapshot(profile), scope=OAUTH_SCOPE,
            expires_at=datetime.fromtimestamp(
                jwt.decode(str(token["access_token"]), RSAKey.import_key(
                    self.identity.settings.oauth_signing_private_key.get_secret_value(),
                ), algorithms=["RS256"]).claims["exp"],
                tz=timezone.utc,
            ),
        ))

    def introspect(self, token: str) -> CustomerProfile:
        row = self.session.get(OAuthAccessToken, digest(token))
        if (row is None or is_expired(row.expires_at) or row.scope != OAUTH_SCOPE
                or self.query_client(row.client_id) is None):
            raise unauthorized()
        user = self.current_user(row)
        if user is None:
            raise unauthorized()
        return self.identity.customer_profile(self.session, user)
