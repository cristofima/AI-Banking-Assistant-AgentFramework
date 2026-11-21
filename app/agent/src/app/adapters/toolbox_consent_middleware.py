"""Surface Foundry Toolbox OAuth consent challenges as native consent output."""

import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any
from uuid import uuid4

from agent_framework import (
    AgentContext, AgentMiddleware, AgentResponse, AgentResponseUpdate, Content,
    FunctionInvocationContext, FunctionMiddleware, Message, ResponseStream,
)
from agent_framework._middleware import MiddlewareFailure
from agent_framework.exceptions import AgentFrameworkException
from mcp.shared.exceptions import MCPError
# The hosting package does not export this parser; the single-agent host uses the same one.
from agent_framework_foundry_hosting._responses import consent_url_from_error


_TOOLBOX_ERRORS = (AgentFrameworkException, MCPError)
_PENDING_ATTR = "_toolbox_consent_pending"
_CONSENT_ERROR_CODE = -32006
_URL_PATTERN = re.compile(r"https://[^\s'\"\\)]+")


class ToolboxCallConsentRequired(MiddlewareFailure):
    """Consent challenge raised by a Toolbox tool call, carried to the agent middleware."""

    def __init__(self, consent_url: str, server_label: str) -> None:
        super().__init__("Toolbox tool call requires user consent.")
        self.consent_url = consent_url
        self.server_label = server_label


def _call_consent_error(exc: BaseException) -> str | None:
    # A tool call reports consent as plain text, without the JSON that tool discovery uses.
    for arg in exc.args:
        if isinstance(arg, MCPError) and arg.error.code == _CONSENT_ERROR_CODE:
            match = _URL_PATTERN.search(arg.error.message)
            return match.group(0) if match else None
    return None


class ToolboxCallConsentMiddleware(FunctionMiddleware):
    """Abort the run with the consent challenge when a Toolbox tool call requires consent.

    Without this, the function loop turns the error into a generic tool failure that
    the model apologizes for; the challenge reaches the agent middleware instead.
    """

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        try:
            await call_next()
        except AgentFrameworkException as exc:
            consent_url = _call_consent_error(exc)
            if consent_url is None:
                raise
            # Toolbox tools are named "<connection>___<tool>" after the connection's server label.
            server_label = context.function.name.partition("___")[0]
            raise ToolboxCallConsentRequired(consent_url, server_label) from exc


def _consent_contents(exc: BaseException) -> list[Content] | None:
    if isinstance(exc, ToolboxCallConsentRequired):
        content = Content.from_oauth_consent_request(
            exc.consent_url, additional_properties={"server_label": exc.server_label},
        )
        content.id = f"consent_{uuid4().hex}"
        return [content]
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
    challenge: list[Content] = []

    async def updates() -> AsyncIterator[AgentResponseUpdate]:
        try:
            async for update in stream:
                yield update
        except _TOOLBOX_ERRORS as exc:
            contents = _consent_contents(exc)
            if contents is None:
                raise
            challenge.extend(contents)
            yield AgentResponseUpdate(role="assistant", contents=contents)

    async def finalize(_buffered: Sequence[AgentResponseUpdate]) -> AgentResponse:
        if challenge:
            return _consent_response(challenge)
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
