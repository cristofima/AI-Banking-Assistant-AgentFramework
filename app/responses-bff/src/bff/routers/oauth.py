"""Customer-only OAuth consent facade; Identity owns client registration and grants."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from bff.clients.identity import auth_request, unavailable
from bff.identity.authentication import bearer_scheme, get_customer_user
from bff.identity.models import AuthenticatedUser
from bff.identity.oauth_client import OAuthClient, logger, session_binding

router = APIRouter(prefix="/oauth")


class CallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(min_length=1, max_length=512)
    code: str = Field(min_length=1, max_length=2048)


@router.get("/status")
async def grant_status(
    request: Request, user: Annotated[AuthenticatedUser, Depends(get_customer_user)],
) -> dict[str, bool]:
    oauth: OAuthClient = request.app.state.oauth_client
    if oauth.config.mcp_identity_mode not in {"hmac", "oauth"}:
        raise unavailable()
    return {"required": oauth.config.mcp_identity_mode == "oauth",
            "ready": oauth.current(session_binding(request, user)) is not None}


@router.post("/start")
async def start_grant(
    request: Request, user: Annotated[AuthenticatedUser, Depends(get_customer_user)],
) -> dict[str, str]:
    oauth: OAuthClient = request.app.state.oauth_client
    if oauth.config.mcp_identity_mode != "oauth":
        raise unavailable()
    return {"authorize_url": oauth.begin(session_binding(request, user))}


@router.post("/callback")
async def complete_grant(
    payload: CallbackRequest, request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_customer_user)],
) -> dict[str, bool]:
    oauth: OAuthClient = request.app.state.oauth_client
    if oauth.config.mcp_identity_mode != "oauth":
        raise unavailable()
    await oauth.complete(session_binding(request, user), user, payload.state, payload.code,
                         request.app.state.auth_client)
    return {"ready": True}


def validate_callback(value: str) -> str:
    """Reject non-HTTPS callbacks and ambiguous authority components."""
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https" or not url.hostname or url.username is not None
            or url.password is not None or url.fragment or "\\" in value
            or any(character.isspace() or ord(character) < 32 for character in value)
        ):
            raise ValueError("Invalid callback")
        _ = url.port
    except ValueError:
        raise ValueError("Invalid callback") from None
    return value


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$")
    redirect_uri: str = Field(min_length=1, max_length=2048)
    response_type: Literal["code"]
    scope: Literal["mcp.access"]
    state: str = Field(min_length=1, max_length=512, pattern=r"^[\x21-\x7E]+$")
    code_challenge: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")
    code_challenge_method: Literal["S256"]
    approved: bool = Field(strict=True)

    _callback = field_validator("redirect_uri")(validate_callback)


class ConsentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    redirect_uri: str = Field(min_length=1, max_length=4096)

    _callback = field_validator("redirect_uri")(validate_callback)


@router.post("/authorize", response_model=ConsentResponse)
async def authorize(
    payload: ConsentRequest,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_customer_user)],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> ConsentResponse:
    data = await auth_request(
        request.app.state.auth_client, "POST", "/oauth/authorize",
        token=credentials.credentials, payload=payload.model_dump(),
    )
    try:
        result = ConsentResponse.model_validate(data)
        expected, returned = urlsplit(payload.redirect_uri), urlsplit(result.redirect_uri)
        original = parse_qsl(expected.query, keep_blank_values=True, strict_parsing=True)
        actual = parse_qsl(returned.query, keep_blank_values=True, strict_parsing=True)
        if (expected.scheme, expected.netloc, expected.path) != (
            returned.scheme, returned.netloc, returned.path
        ) or actual[:len(original)] != original:
            raise ValueError("Callback changed")
        protocol = actual[len(original):]
        fields = dict(protocol)
        outcome = "code" if payload.approved else "error"
        allowed = {"state", outcome}
        if not payload.approved:
            allowed |= {"error_description", "error_uri"}
        if (
            len(fields) != len(protocol) or not fields.keys() <= allowed
            or fields.get("state") != payload.state or not fields.get(outcome)
            or (not payload.approved and fields[outcome] != "access_denied")
        ):
            raise ValueError("Invalid authorization response")
        return result
    except (ValidationError, ValueError):
        logger.error("BFF OAuth authorization rejected: category=callback_response")
        raise unavailable() from None
