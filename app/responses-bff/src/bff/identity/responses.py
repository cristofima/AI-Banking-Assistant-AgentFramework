"""Authenticated downstream identity and credential policy."""

from typing import Any, Protocol
import httpx
from azure.core.exceptions import AzureError
from fastapi import HTTPException, Request, status
from bff.identity.models import AuthenticatedUser
from bff.config.settings import Settings
from bff.identity.internal_identity import create_internal_identity, create_platform_user_id
from bff.identity.oauth_client import OAuthClient, session_binding

class AsyncCredential(Protocol):
    """Credential operations used by the BFF."""

    async def get_token(self, *scopes: str) -> Any: ...

    async def close(self) -> None: ...


async def _upstream_headers(
    request: Request,
    user: AuthenticatedUser,
    stream: bool,
) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    headers = {"Accept": "text/event-stream" if stream else "application/json"}
    if not settings.internal_identity_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "SERVICE_UNAVAILABLE"},
        )
    oauth: OAuthClient = request.app.state.oauth_client
    grant = None
    if oauth.config.mcp_identity_mode == "oauth":
        grant = oauth.current(session_binding(request, user))
        if grant is None:
            raise HTTPException(403, detail={"code": "ACCESS_DENIED"})
    elif oauth.config.mcp_identity_mode != "hmac":
        raise HTTPException(503, detail={"code": "SERVICE_UNAVAILABLE"})
    internal_identity = create_internal_identity(user, settings.internal_identity_secret, grant)
    if settings.responses_upstream_mode == "foundry":
        credential: AsyncCredential = request.app.state.azure_credential
        try:
            token = await credential.get_token(settings.responses_token_scope)
        except (AzureError, httpx.RequestError) as error:
            raise HTTPException(status_code=503, detail={"code": "SERVICE_UNAVAILABLE"}) from error
        headers["Authorization"] = f"Bearer {token.token}"
        headers["x-ms-user-identity"] = create_platform_user_id(
            user, settings.internal_identity_secret
        )
        headers["x-client-banking-identity"] = internal_identity
    else:
        headers["x-agent-user-id"] = internal_identity
    return headers
