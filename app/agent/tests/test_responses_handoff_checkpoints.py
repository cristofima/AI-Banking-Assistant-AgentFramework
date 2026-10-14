"""Exercise multi-turn handoffs through the real Responses checkpoint handler."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent_framework import (
    Agent, ChatResponse, ChatResponseUpdate, Content, InMemoryCheckpointStorage, ResponseStream,
)
from agent_framework.foundry import FoundryChatClient

from app.agents.azure_chat.hosted_workflow import _has_completed_agent_response
from app.adapters.checkpointed_handoff import CheckpointedHandoffBuilder as HandoffBuilder
from app.adapters.handoff_middleware import HandoffNarrationMiddleware
from agent_framework_foundry_hosting import ResponsesHostServer
from app.adapters.no_history_provider import NoHistoryProvider


@pytest.mark.parametrize("conversation_id", [None, "conversation-test"])
async def test_checkpointed_handoff_delivers_followup_to_specialist(
    conversation_id: str | None,
) -> None:
    client = FoundryChatClient(
        project_endpoint="https://example.services.ai.azure.com/api/projects/test",
        model="test-model", credential=MagicMock(),
    )
    specialist_inputs: list[list[str]] = []
    account_inputs: list[list[str]] = []

    def model_response(*args: Any, **kwargs: Any) -> ResponseStream:
        instructions = kwargs.get("options", {}).get("instructions", "")
        is_triage = "Route requests" in instructions

        async def updates() -> AsyncIterator[ChatResponseUpdate]:
            texts = [message.text for message in kwargs["messages"]]
            if is_triage:
                target = "AccountAgent" if texts[-1] == "Show my balance" else "TransactionHistoryAgent"
                contents = [Content.from_function_call(
                    "handoff", f"handoff_to_{target}", arguments="{}",
                )]
            elif "Find balances" in instructions:
                account_inputs.append(texts)
                contents = [Content.from_text("Your balance is available.")]
            else:
                specialist_inputs.append(texts)
                contents = [Content.from_text(
                    "Which card and date?" if len(specialist_inputs) == 1
                    else "I will look up the charge on card ending 7036.",
                )]
            yield ChatResponseUpdate(role="assistant", contents=contents)

        return ResponseStream(updates(), finalizer=ChatResponse.from_updates)

    def create_agent() -> Any:
        triage = Agent(
            client=client, name="triage_agent", instructions="Route requests",
            context_providers=[NoHistoryProvider()],
            middleware=[HandoffNarrationMiddleware()],
            require_per_service_call_history_persistence=True,
        )
        specialist = Agent(
            client=client, name="TransactionHistoryAgent", instructions="Find charges",
            context_providers=[NoHistoryProvider()],
            require_per_service_call_history_persistence=True,
        )
        account = Agent(
            client=client, name="AccountAgent", instructions="Find balances",
            context_providers=[NoHistoryProvider()],
            require_per_service_call_history_persistence=True,
        )
        return HandoffBuilder(
            participants=[triage, specialist, account],
            termination_condition=_has_completed_agent_response,
        ).with_start_agent(triage).add_handoff(triage, [specialist, account]).build().as_agent()

    server = ResponsesHostServer(create_agent)
    stores: dict[str, InMemoryCheckpointStorage] = {}
    server._checkpoint_storage_provider = MagicMock()
    server._checkpoint_storage_provider.get_store.side_effect = lambda **kwargs: stores.setdefault(
        kwargs["context_id"], InMemoryCheckpointStorage(),
    )
    server._function_approval_storage_provider = MagicMock()

    async def run_turn(text: str, response_id: str, previous_id: str | None) -> list[str]:
        context = MagicMock()
        context.get_input_items = AsyncMock(return_value=[{
            "type": "message", "role": "user", "content": text,
        }])
        context.response_id = response_id
        context.conversation_id = conversation_id
        context.is_recovery = False
        context.shutdown = asyncio.Event()
        outputs: list[str] = []
        tracker = MagicMock()

        async def handle_update(update: Any, **kwargs: Any) -> AsyncIterator[Any]:
            for content in update.contents:
                if content.type == "text":
                    outputs.append(content.text or "")
            if False:
                yield None

        tracker.handle_update = handle_update
        request: dict[str, Any] = {"model": "test-model", "input": text}
        if previous_id and conversation_id is None:
            request["previous_response_id"] = previous_id
        async def run_prepared(
            host: ResponsesHostServer, request: Any, context: Any,
            cancellation_signal: Any, response_event_stream: Any, agent: Any,
            *args: Any,
        ) -> AsyncIterator[Any]:
            async for event in host._handle_inner_workflow(
                request, context, response_event_stream, tracker, cancellation_signal, agent,
            ):
                yield event

        with patch(
            "agent_framework_foundry_hosting._responses.get_request_context",
            return_value=MagicMock(session_id=None, user_id=None, call_id=None),
        ), patch.object(ResponsesHostServer, "_handle_prepared_response", run_prepared):
            async for _ in server._handle_response(request, context, asyncio.Event()):
                pass
        return outputs

    with patch.object(client, "_inner_get_response", model_response):
        first = await run_turn("I do not recognize a charge", "response-first", None)
        second = await run_turn(
            "2026-06-11, credit card ending 7036, USD 52.10", "response-second", "response-first",
        )
        third = await run_turn("Show my balance", "response-third", "response-second")
        fourth = await run_turn("Continue the charge lookup", "response-fourth", "response-third")
        if conversation_id is None:
            isolated = await run_turn("I do not recognize a charge", "response-isolated", None)
            assert isolated == ["I will look up the charge on card ending 7036."]
            assert "Continue the charge lookup" not in specialist_inputs[-1]

    assert first == ["Which card and date?"]
    assert second == ["I will look up the charge on card ending 7036."]
    assert third == ["Your balance is available."]
    assert fourth == ["I will look up the charge on card ending 7036."]
    assert len(specialist_inputs) == (4 if conversation_id is None else 3)
    followup = "2026-06-11, credit card ending 7036, USD 52.10"
    assert specialist_inputs[1].count(followup) == 1
    assert account_inputs[0].count(followup) == 1
    assert account_inputs[0].count("Show my balance") == 1
    assert specialist_inputs[2].count("Continue the charge lookup") == 1
