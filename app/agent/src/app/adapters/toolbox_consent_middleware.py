"""Surface Foundry Toolbox OAuth consent challenges as native consent output."""

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any
from uuid import uuid4

from agent_framework import (
    AgentContext, AgentMiddleware, AgentResponse, AgentResponseUpdate, Content, Message,
    ResponseStream,
)
from agent_framework.exceptions import AgentFrameworkException
from mcp.shared.exceptions import MCPError
# The hosting package does not export this parser; the single-agent host uses the same one.
from agent_framework_foundry_hosting._responses import consent_url_from_error


_TOOLBOX_ERRORS = (AgentFrameworkException, MCPError)
_PENDING_ATTR = "_toolbox_consent_pending"


def _consent_contents(exc: BaseException) -> list[Content] | None:
    # Toolbox discovery raises the bare MCPError, while the host parser reads a wrapped one.
    if isinstance(exc, MCPError):
        exc = AgentFrameworkException("Toolbox request failed", exc)
    errors = consent_url_from_error(exc)
    if not errors:
        return None
    contents = [
        Content.from_oauth_consent_request(
            error.consent_url, additional_properties={"server_label": error.name},
        )
        for error in errors
    ]
    # The workflow executor registers consent as a user input request and needs an ID.
    for content in contents:
        content.id = f"consent_{uuid4().hex}"
    return contents


def _consent_response(contents: list[Content]) -> AgentResponse:
    return AgentResponse(messages=[Message(role="assistant", contents=contents)])


def _consent_stream(contents: list[Content]) -> ResponseStream:
    async def updates() -> AsyncIterator[AgentResponseUpdate]:
        yield AgentResponseUpdate(role="assistant", contents=contents)

    return ResponseStream(updates(), finalizer=lambda _: _consent_response(contents))


def _guard_stream(stream: ResponseStream) -> ResponseStream:
    # Tool discovery runs lazily, so the challenge surfaces while the stream is consumed.
    async def updates() -> AsyncIterator[AgentResponseUpdate]:
        try:
            async for update in stream:
                yield update
        except _TOOLBOX_ERRORS as exc:
            contents = _consent_contents(exc)
            if contents is None:
                raise
            yield AgentResponseUpdate(role="assistant", contents=contents)

    async def finalize(_buffered: Sequence[AgentResponseUpdate]) -> AgentResponse:
        try:
            return await stream.get_final_response()
        except _TOOLBOX_ERRORS as exc:
            contents = _consent_contents(exc)
            if contents is None:
                raise
            return _consent_response(contents)

    return ResponseStream(updates(), finalizer=finalize)


class ToolboxConsentMiddleware(AgentMiddleware):
    """Return the platform consent link instead of failing the run.

    The link goes to the client only; it is never given to the model, and the
    failed run is not replayed after consent.

    The workflow host opens every agent, and so connects its MCP tools, before the
    run starts. Tools passed here have connect failures deferred to the next run,
    where the challenge can be returned as output instead of failing preparation.
    """

    def __init__(self, tools: Sequence[Any] = ()) -> None:
        self._tools = list(tools)
        for tool in self._tools:
            self._defer_connect_failure(tool)

    @staticmethod
    def _defer_connect_failure(tool: Any) -> None:
        # A tool shared by several agents is wrapped once, and its pending challenge
        # lives on the tool so whichever agent runs first can return it.
        if hasattr(tool, _PENDING_ATTR):
            return
        setattr(tool, _PENDING_ATTR, None)
        connect = tool.connect

        async def deferred_connect(*args: Any, **kwargs: Any) -> None:
            try:
                await connect(*args, **kwargs)
            except _TOOLBOX_ERRORS as exc:
                if _consent_contents(exc) is None:
                    raise
                setattr(tool, _PENDING_ATTR, exc)

        tool.connect = deferred_connect

    def _take_pending(self) -> BaseException | None:
        pending = None
        for tool in self._tools:
            pending = pending or getattr(tool, _PENDING_ATTR, None)
            setattr(tool, _PENDING_ATTR, None)
        return pending

    async def process(
        self,
        context: AgentContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        pending = self._take_pending()
        try:
            if pending is not None:
                raise pending
            await call_next()
        except _TOOLBOX_ERRORS as exc:
            contents = _consent_contents(exc)
            if contents is None:
                raise
            context.result = _consent_stream(contents) if context.stream else _consent_response(contents)
            return
        if isinstance(context.result, ResponseStream):
            context.result = _guard_stream(context.result)
