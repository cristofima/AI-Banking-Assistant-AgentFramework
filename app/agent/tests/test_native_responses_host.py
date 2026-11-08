"""Exercise multi-turn handoffs through the native Responses workflow host."""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from agent_framework import (
    Agent, ChatResponse, ChatResponseUpdate, Content, ResponseStream, Workflow,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import HandoffBuilder
from agent_framework_foundry_hosting import HostedResponseRequest, ResponsesHostServer
from starlette.testclient import TestClient

from app.adapters.checkpointed_handoff import CheckpointedHandoffBuilder
from app.adapters.handoff_middleware import HandoffNarrationMiddleware
from app.adapters.no_history_provider import NoHistoryProvider
from app.adapters.responses_turn import parse_response
from app.agents.azure_chat.hosted_workflow import _has_completed_agent_response


@pytest.mark.parametrize("builder_class", [
    CheckpointedHandoffBuilder,
    pytest.param(HandoffBuilder, marks=pytest.mark.xfail(
        strict=True, reason="SDK 1.4.2 start executor drops the follow-up turn on continuation",
    )),
])
def test_native_handoff_delivers_followup_to_specialist(
    builder_class: type[HandoffBuilder],
) -> None:
    specialist_inputs: list[list[str]] = []
    account_inputs: list[list[str]] = []

    def model_response(_client: Any, *args: Any, **kwargs: Any) -> ResponseStream:
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

        triage = participant(
            "triage_agent", "Route requests", middleware=[HandoffNarrationMiddleware()],
        )
        specialist = participant("TransactionHistoryAgent", "Find charges")
        account = participant("AccountAgent", "Find balances")
        return builder_class(
            participants=[triage, specialist, account],
            termination_condition=_has_completed_agent_response,
        ).with_start_agent(triage).add_handoff(triage, [specialist, account]).build()

    server = ResponsesHostServer(workflow=create_workflow, parse_response=parse_response)

    def run_turn(
        http: TestClient, text: str, previous_id: str | None,
    ) -> tuple[str, str]:
        body: dict[str, Any] = {
            "model": "test-model", "input": text, "store": True, "agent_session_id": "local-session",
        }
        if previous_id is not None:
            body["previous_response_id"] = previous_id
        reply = http.post("/responses", json=body)
        assert reply.status_code == 200, reply.text
        payload = reply.json()
        assert payload["status"] == "completed", payload
        output = "".join(
            part["text"] for item in payload["output"] if item["type"] == "message"
            for part in item["content"] if part["type"] == "output_text"
        )
        return payload["id"], output

    with patch.object(FoundryChatClient, "_inner_get_response", model_response), TestClient(server) as http:
        first_id, first = run_turn(http, "I do not recognize a charge", None)
        second_id, second = run_turn(
            http, "2026-06-11, credit card ending 7036, USD 52.10", first_id,
        )
        third_id, third = run_turn(http, "Show my balance", second_id)
        _, fourth = run_turn(http, "Continue the charge lookup", third_id)
        _, isolated = run_turn(http, "I do not recognize a charge", None)
        assert isolated == "I will look up the charge on card ending 7036."
        assert "Continue the charge lookup" not in specialist_inputs[-1]

    assert first == "Which card and date?"
    assert second == "I will look up the charge on card ending 7036."
    assert third == "Your balance is available."
    assert fourth == "I will look up the charge on card ending 7036."
    assert len(specialist_inputs) == 4
    followup = "2026-06-11, credit card ending 7036, USD 52.10"
    assert specialist_inputs[1].count(followup) == 1
    assert account_inputs[0].count(followup) == 1
    assert account_inputs[0].count("Show my balance") == 1
    assert specialist_inputs[2].count("Continue the charge lookup") == 1
