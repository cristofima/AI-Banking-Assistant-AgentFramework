"""Bounded browser consent and standards-based OAuth protocol boundaries."""
import logging
from collections.abc import Callable, Iterator
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit

from authlib.oauth2.rfc6749.errors import OAuth2Error
from banking_shared.oauth_jwt import public_jwks
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from joserfc.errors import JoseError
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from identity.models.schemas import CustomerProfile, IntrospectionRequest
from identity.services.oauth import OAUTH_SCOPE, OAuthServer, ProtocolRequest
from identity.services.service import IdentityService
from identity.settings import Settings

logger = logging.getLogger("uvicorn.error.identity.oauth")

PROTOCOL_KEYS = {"client_id", "redirect_uri", "response_type", "scope", "state",
                 "code_challenge", "code_challenge_method"}
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class AuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=128)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    response_type: str = Field(min_length=1, max_length=32)
    scope: str = Field(default=OAUTH_SCOPE, max_length=128)
    state: str = Field(min_length=1, max_length=512)
    code_challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code_challenge_method: str = Field(pattern=r"^S256$")
    approved: bool = Field(strict=True)


def protocol_error(stage: str = "authorization") -> HTTPException:
    logger.error("Identity OAuth request rejected: stage=%s category=request", stage)
    return HTTPException(400, detail={"code": "OAUTH_INVALID_REQUEST"}, headers=NO_STORE)


def log_protocol_rejection(stage: str, error: object) -> None:
    allowed = {
        "invalid_client", "invalid_request", "invalid_scope", "unauthorized_client",
        "unsupported_response_type", "unsupported_grant_type", "access_denied", "invalid_grant",
    }
    code = error if isinstance(error, str) and error in allowed else "protocol_error"
    logger.error("Identity OAuth %s rejected: code=%s", stage, code)


def oauth_router(config: Settings, identity: IdentityService,
                 session: Callable[[], Iterator[Session]], token_value: Callable,
                 internal: Callable) -> APIRouter:
    router = APIRouter()

    def enabled() -> None:
        if not config.oauth_issuer or not config.oauth_frontend_authorize_url:
            logger.error("Identity OAuth configuration invalid: category=disabled")
            raise HTTPException(404, detail={"code": "OAUTH_DISABLED"})
        for value in (config.oauth_issuer, config.oauth_frontend_authorize_url):
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.fragment):
                logger.error("Identity OAuth configuration invalid: category=https_url")
                raise HTTPException(503, detail={"code": "OAUTH_UNAVAILABLE"})
        category = "issuer"
        try:
            if urlsplit(config.oauth_issuer).query or config.oauth_issuer.endswith("/"):
                raise ValueError("OAuth issuer must be canonical")
            category = "audience"
            if not config.oauth_audience.strip():
                raise ValueError("OAuth audience is required")
            category = "signing_configuration"
            if (not config.oauth_signing_private_key.get_secret_value().strip()
                    or not config.oauth_signing_key_id.strip()
                    or len(config.oauth_signing_key_id) > 128):
                raise ValueError("OAuth signing configuration is required")
            category = "signing_key"
            public_jwks(config.oauth_signing_private_key.get_secret_value(),
                        config.oauth_signing_key_id)
        except (JoseError, ValueError, TypeError, KeyError):
            logger.error("Identity OAuth configuration invalid: category=%s", category)
            raise HTTPException(503, detail={"code": "OAUTH_UNAVAILABLE"}) from None

    @router.get("/oauth/jwks", dependencies=[Depends(enabled)])
    def jwks() -> JSONResponse:
        return JSONResponse(public_jwks(config.oauth_signing_private_key.get_secret_value(),
                                        config.oauth_signing_key_id), headers=NO_STORE)

    def protocol(data: dict[str, str], method: str = "GET",
                 headers: dict[str, str] | None = None) -> ProtocolRequest:
        return ProtocolRequest(method, config.oauth_issuer.rstrip("/") + "/oauth/authorize",
                               data, headers)

    @router.get("/.well-known/oauth-authorization-server", dependencies=[Depends(enabled)])
    @router.get("/oauth/.well-known/oauth-authorization-server", dependencies=[Depends(enabled)])
    def metadata() -> JSONResponse:
        issuer = config.oauth_issuer.rstrip("/")
        return JSONResponse({
            "issuer": issuer, "jwks_uri": issuer + "/oauth/jwks",
            "authorization_endpoint": issuer + "/oauth/authorize",
            "token_endpoint": issuer + "/oauth/token", "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"], "scopes_supported": [OAUTH_SCOPE],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "code_challenge_methods_supported": ["S256"],
        }, headers=NO_STORE)

    def validated(data: dict[str, str], server: OAuthServer) -> ProtocolRequest:
        if (data.get("code_challenge_method") != "S256" or not data.get("code_challenge")
                or not data.get("redirect_uri") or not data.get("state")):
            raise protocol_error()
        request = protocol(data)
        try:
            server.get_consent_grant(request)
        except OAuth2Error as exc:
            log_protocol_rejection("authorization", exc.error)
            raise HTTPException(400, detail={"code": "OAUTH_INVALID_REQUEST"},
                                headers=NO_STORE) from None
        return request

    @router.get("/oauth/authorize", dependencies=[Depends(enabled)])
    def landing(request: Request, db: Annotated[Session, Depends(session)]) -> RedirectResponse:
        values = request.query_params
        if (len(str(values)) > 4096 or any(key not in PROTOCOL_KEYS for key in values)
                or any(len(values.getlist(key)) != 1 for key in values)):
            raise protocol_error()
        try:
            body = AuthorizationRequest(**dict(values), approved=False)
        except ValueError as exc:
            raise protocol_error() from exc
        data = body.model_dump(exclude={"approved"})
        validated(data, OAuthServer(db, identity))
        separator = "&" if urlsplit(config.oauth_frontend_authorize_url).query else "?"
        return RedirectResponse(config.oauth_frontend_authorize_url + separator + urlencode(data),
                                status_code=302, headers=NO_STORE)

    @router.post("/oauth/authorize", dependencies=[Depends(enabled)])
    def authorize(body: AuthorizationRequest, db: Annotated[Session, Depends(session)],
                  token: Annotated[str, Depends(token_value)]) -> JSONResponse:
        try:
            user = identity.authenticate(db, token)
            identity.customer_profile(db, user)
        except HTTPException:
            logger.error("Identity OAuth authorization rejected: category=customer_identity")
            raise
        server = OAuthServer(db, identity)
        request = validated(body.model_dump(exclude={"approved"}), server)
        grant = server.get_authorization_grant(request)
        status, response, headers = server.create_authorization_response(
            request, grant_user=user if body.approved else None, grant=grant,
        )
        location = dict(headers).get("Location")
        if status != 302 or not location:
            db.rollback()
            log_protocol_rejection("authorization", response.get("error")
                                   if isinstance(response, dict) else None)
            raise protocol_error()
        db.commit()
        return JSONResponse({"redirect_uri": location}, headers=NO_STORE)

    def exchange(data: dict[str, str], headers: dict[str, str], db: Session) -> JSONResponse:
        server = OAuthServer(db, identity)
        request = protocol(data, "POST", headers)
        status, body, response_headers = server.create_token_response(request)
        if status == 200:
            db.commit()
        else:
            db.rollback()
            log_protocol_rejection("token", body.get("error"))
            body = {"error": body.get("error", "invalid_request")}
        return JSONResponse(body, status_code=status, headers={**dict(response_headers), **NO_STORE})

    @router.post("/oauth/token", dependencies=[Depends(enabled)])
    async def token(request: Request, db: Annotated[Session, Depends(session)]) -> JSONResponse:
        if (request.headers.get("content-type", "").split(";")[0]
                != "application/x-www-form-urlencoded"):
            raise protocol_error("token")
        raw = await request.body()
        if len(raw) > 8192:
            raise protocol_error("token")
        try:
            pairs = parse_qsl(raw.decode(), keep_blank_values=True, max_num_fields=12)
        except (ValueError, UnicodeDecodeError):
            raise protocol_error("token") from None
        data = dict(pairs)
        allowed = {"grant_type", "code", "redirect_uri", "client_id", "client_secret",
                   "code_verifier"}
        if (len(data) != len(pairs) or any(key not in allowed for key in data)
                or (request.headers.get("authorization") and "client_secret" in data)):
            raise protocol_error("token")
        return await run_in_threadpool(exchange, data,
                                      {"Authorization": request.headers.get("authorization", "")}, db)

    @router.post("/internal/oauth/introspect", response_model=CustomerProfile,
                 response_model_exclude_none=True,
                 dependencies=[Depends(enabled), Depends(internal)])
    def introspect(body: IntrospectionRequest,
                   db: Annotated[Session, Depends(session)]) -> CustomerProfile:
        try:
            return OAuthServer(db, identity).introspect(body.token.get_secret_value())
        except HTTPException:
            logger.error("Identity OAuth introspection rejected: category=grant_identity")
            raise

    return router
