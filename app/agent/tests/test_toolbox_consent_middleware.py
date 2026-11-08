"""Foundry Toolbox consent challenges become native consent output instead of failures."""

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from agent_framework import (
    Agent, AgentContext, AgentResponse, AgentResponseUpdate, ChatResponse, ChatResponseUpdate,
    Content, MCPStreamableHTTPTool, Message, ResponseStream, Workflow,
)
from agent_framework.exceptions import AgentFrameworkException, ToolException
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import HostedResponseRequest, ResponsesHostServer
from mcp import MCPError
from starlette.testclient import TestClient

from app.adapters.checkpointed_handoff import CheckpointedHandoffBuilder
from app.adapters.handoff_middleware import HandoffNarrationMiddleware
from app.adapters.no_history_provider import NoHistoryProvider
from app.adapters.responses_turn import parse_response
from app.adapters.toolbox_consent_middleware import ToolboxConsentMiddleware
from app.agents.azure_chat.hosted_workflow import _has_completed_agent_response

CONSENT_URL = "https://consent.example.test/oauth/start?state=synthetic"


def consent_exception(url: str = CONSENT_URL, name: str = "account") -> AgentFrameworkException:
    details = {"errors": [{
        "name": name, "type": "mcp",
        "error": {"code": "CONSENT_REQUIRED", "message": url},
    }]}
    mcp_error = MCPError(
        -32006, f"tools/list failed for 1 tool source(s), succeeded for 0 tool source(s) {json.dumps(details)}",
    )
    return ToolException("Failed to connect to MCP server.", mcp_error)


def consent_links(contents: list[Content]) -> list[str]:
    return [content.consent_link for content in contents if content.type == "oauth_consent_request"]


@pytest.mark.parametrize("stream", [False, True])
async def test_connect_failure_becomes_consent_content(stream: bool) -> None:
    context = AgentContext(agent=MagicMock(), messages=[], stream=stream)

    async def run() -> None:
        raise consent_exception()

    await ToolboxConsentMiddleware().process(context, run)
    if stream:
        updates = [update async for update in context.result]
        assert [link for update in updates for link in consent_links(update.contents)] == [CONSENT_URL]
        result = await context.result.get_final_response()
    else:
        result = context.result
    contents = [content for message in result.messages for content in message.contents]
    assert consent_links(contents) == [CONSENT_URL]
    assert contents[0].additional_properties["server_label"] == "account"
    assert _has_completed_agent_response(result.messages)


async def test_lazy_stream_failure_becomes_consent_content() -> None:
    context = AgentContext(agent=MagicMock(), messages=[], stream=True)

    async def failing_updates() -> AsyncIterator[AgentResponseUpdate]:
        raise consent_exception()
        yield  # pragma: no cover

    async def finalizer(_updates: object) -> AgentResponse:
        raise consent_exception()

    async def run() -> None:
        context.result = ResponseStream(failing_updates(), finalizer=finalizer)

    await ToolboxConsentMiddleware().process(context, run)
    updates = [update async for update in context.result]
    assert [link for update in updates for link in consent_links(update.contents)] == [CONSENT_URL]
    result = await context.result.get_final_response()
    assert consent_links([c for m in result.messages for c in m.contents]) == [CONSENT_URL]


async def test_bare_mcp_error_from_toolbox_connect_becomes_consent() -> None:
    # The Toolbox discovery failure is the bare MCPError, with one entry per tool source.
    details = {"errors": [
        {"name": name, "type": "mcp", "error": {"code": "CONSENT_REQUIRED", "message": f"{CONSENT_URL}&n={name}"}}
        for name in ("account", "transaction")
    ]}

    class FakeToolbox:
        async def connect(self) -> None:
            raise MCPError(-32006, f"tools/list failed for 2 tool source(s) {json.dumps(details)}")

    toolbox = FakeToolbox()
    middleware = ToolboxConsentMiddleware([toolbox])
    await toolbox.connect()
    context = AgentContext(agent=MagicMock(), messages=[], stream=False)

    async def run() -> None:
        raise AssertionError("the deferred challenge must replace the run")

    await middleware.process(context, run)
    contents = [content for message in context.result.messages for content in message.contents]
    assert consent_links(contents) == [f"{CONSENT_URL}&n=account", f"{CONSENT_URL}&n=transaction"]


async def test_shared_tool_challenge_reaches_the_second_agent() -> None:
    class FakeToolbox:
        async def connect(self) -> None:
            raise consent_exception()

    shared = FakeToolbox()
    first = ToolboxConsentMiddleware([shared])
    second = ToolboxConsentMiddleware([shared, FakeToolbox()])
    await shared.connect()
    context = AgentContext(agent=MagicMock(), messages=[], stream=False)

    async def run() -> None:
        raise AssertionError("the deferred challenge must replace the run")

    await second.process(context, run)
    contents = [content for message in context.result.messages for content in message.contents]
    assert consent_links(contents) == [CONSENT_URL]

    # The challenge is consumed once, so the other agent runs normally afterwards.
    ran = False

    async def next_run() -> None:
        nonlocal ran
        ran = True

    await first.process(AgentContext(agent=MagicMock(), messages=[], stream=False), next_run)
    assert ran


@pytest.mark.parametrize("stream", [False, True])
async def test_other_failures_are_not_swallowed(stream: bool) -> None:
    context = AgentContext(agent=MagicMock(), messages=[], stream=stream)

    async def run() -> None:
        raise ToolException("Failed to connect.", MCPError(-32000, "connection refused"))

    with pytest.raises(ToolException):
        await ToolboxConsentMiddleware().process(context, run)


async def test_lazy_non_consent_failure_is_not_swallowed() -> None:
    context = AgentContext(agent=MagicMock(), messages=[], stream=True)

    async def failing_updates() -> AsyncIterator[AgentResponseUpdate]:
        raise ToolException("Failed to connect.", MCPError(-32000, "connection refused"))
        yield  # pragma: no cover

    async def run() -> None:
        context.result = ResponseStream(failing_updates(), finalizer=lambda _: None)

    await ToolboxConsentMiddleware().process(context, run)
    with pytest.raises(ToolException):
        [update async for update in context.result]


async def test_successful_stream_is_unchanged() -> None:
    context = AgentContext(agent=MagicMock(), messages=[], stream=True)
    response = ChatResponse.from_updates([
        ChatResponseUpdate(role="assistant", contents=[Content.from_text("Done.")]),
    ])

    async def updates() -> AsyncIterator[ChatResponseUpdate]:
        yield ChatResponseUpdate(role="assistant", contents=[Content.from_text("Done.")])

    async def run() -> None:
        context.result = ResponseStream(updates(), finalizer=lambda _: response)

    await ToolboxConsentMiddleware().process(context, run)
    assert [update.text async for update in context.result] == ["Done."]
    assert (await context.result.get_final_response()).text == "Done."


def test_consent_marks_the_turn_complete() -> None:
    assert _has_completed_agent_response([]) is False
    consent = Message(role="assistant", contents=[Content.from_oauth_consent_request(CONSENT_URL)])
    assert _has_completed_agent_response([consent]) is True


def test_middleware_runs_outside_other_middleware_in_agents() -> None:
    from app.agents.azure_chat.account_agent import AccountAgent

    agent = AccountAgent(MagicMock(), "https://account.invalid", "synthetic-secret-synthetic-secret").build_af_agent()
    assert isinstance(agent, Agent)
    assert isinstance(agent.middleware[0], ToolboxConsentMiddleware)


@pytest.mark.parametrize("stream", [False, True])
def test_native_host_returns_consent_and_ends_turn(stream: bool) -> None:
    def model_response(_client: Any, *args: Any, **kwargs: Any) -> ResponseStream:
        async def updates() -> AsyncIterator[ChatResponseUpdate]:
            if "Route requests" in kwargs.get("options", {}).get("instructions", ""):
                yield ChatResponseUpdate(role="assistant", contents=[Content.from_function_call(
                    "handoff", "handoff_to_AccountAgent", arguments="{}",
                )])
            else:
                raise AssertionError("The specialist must not reach the model without its tools")

        return ResponseStream(updates(), finalizer=ChatResponse.from_updates)

    def create_workflow(_request: HostedResponseRequest) -> Workflow:
        client = FoundryChatClient(
            project_endpoint="https://example.services.ai.azure.com/api/projects/test",
            model="test-model", credential=MagicMock(),
        )

        def participant(name: str, instructions: str, **extra: Any) -> Agent:
            return Agent(
                client=client, name=name, instructions=instructions,
                context_providers=[NoHistoryProvider()],
                require_per_service_call_history_persistence=True, **extra,
            )

        triage = participant("triage_agent", "Route requests", middleware=[HandoffNarrationMiddleware()])
        account_tool = MCPStreamableHTTPTool(name="account", url="https://account.invalid/mcp/")
        account = participant(
            "AccountAgent", "Find balances",
            tools=[account_tool],
            middleware=[ToolboxConsentMiddleware([account_tool])],
        )
        return CheckpointedHandoffBuilder(
            participants=[triage, account], termination_condition=_has_completed_agent_response,
        ).with_start_agent(triage).add_handoff(triage, [account]).build()

    server = ResponsesHostServer(workflow=create_workflow, parse_response=parse_response)

    async def failing_connect(*_args: Any, **_kwargs: Any) -> None:
        raise consent_exception()

    body = {"model": "test-model", "input": "Show my balance", "store": True,
            "agent_session_id": "local-session", "stream": stream}
    with (
        patch.object(FoundryChatClient, "_inner_get_response", model_response),
        patch.object(MCPStreamableHTTPTool, "connect", failing_connect),
        TestClient(server) as http,
    ):
        reply = http.post("/responses", json=body)
    assert reply.status_code == 200, reply.text
    if stream:
        assert "response.incomplete" in reply.text
        assert "response.failed" not in reply.text
        assert '"type": "oauth_consent_request"' in reply.text or '"type":"oauth_consent_request"' in reply.text
        assert CONSENT_URL in reply.text
    else:
        payload = reply.json()
        assert payload["status"] == "incomplete", payload
        items = [item for item in payload["output"] if item["type"] == "oauth_consent_request"]
        assert [item["consent_link"] for item in items] == [CONSENT_URL]
        assert items[0]["server_label"] == "account"
