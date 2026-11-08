"""Authenticated proxy for a local or Foundry Responses agent."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse

from bff.clients.continuation import completed_token, stream_with_continuation
from bff.clients.responses import read_response, send_response
from bff.config.settings import Settings
from bff.identity.authentication import get_customer_user
from bff.identity.conversation import _previous_response
from bff.identity.models import AuthenticatedUser
from bff.identity.responses import _upstream_headers


router = APIRouter(prefix="/responses")


@router.post("")
async def create_response(
    payload: dict[str, Any],
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_customer_user)],
) -> Response:
    """Forward a Responses request to the configured trusted upstream."""
    if "previous_response_id" in payload or "agent_session_id" in payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST"},
        )

    requested_conversation = payload.get("conversation")
    if requested_conversation is not None and not isinstance(requested_conversation, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST"},
        )

    settings: Settings = request.app.state.settings
    upstream_payload = dict(payload)
    upstream_payload.pop("conversation", None)
    if requested_conversation is not None:
        upstream_payload["previous_response_id"] = _previous_response(
            requested_conversation, user.sub, settings
        )

    if settings.responses_upstream_mode == "local":
        # The local host scopes workflow checkpoints by session; without a stable one,
        # each turn derives a different session and cannot restore the previous checkpoint.
        upstream_payload["agent_session_id"] = hashlib.sha256(
            f"local-session:{user.sub}".encode()
        ).hexdigest()

    client: httpx.AsyncClient = request.app.state.http_client
    upstream_request = client.build_request(
        "POST",
        settings.responses_agent_endpoint,
        json=upstream_payload,
        headers=await _upstream_headers(request, user, bool(payload.get("stream"))),
    )
    upstream = await send_response(client, upstream_request)

    response_headers: dict[str, str] = {}
    content_type = upstream.headers.get("content-type", "application/json")
    if content_type.startswith("text/event-stream"):
        return StreamingResponse(
            stream_with_continuation(upstream, user.sub, settings),
            status_code=upstream.status_code,
            media_type="text/event-stream",
            headers=response_headers,
        )

    content = await read_response(upstream)
    if upstream.is_success:
        token = completed_token(content, user.sub, settings)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "SERVICE_UNAVAILABLE"},
            )
        response_headers["X-Conversation-Id"] = token
    return Response(
        content=content,
        status_code=upstream.status_code,
        media_type=content_type,
        headers=response_headers,
    )