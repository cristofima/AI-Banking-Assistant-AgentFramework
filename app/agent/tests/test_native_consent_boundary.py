"""Characterize how the native workflow host reports a nested tool consent challenge."""

import json
from typing import Any
from unittest.mock import MagicMock

from agent_framework import Agent, Workflow
from agent_framework.exceptions import AgentFrameworkException
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import HandoffBuilder
from agent_framework_foundry_hosting import HostedResponseRequest, ResponsesHostServer
from mcp import MCPError
from starlette.testclient import TestClient

from app.adapters.responses_turn import parse_response

CONSENT_LINK = "https://consent.example.test/authorize?state=synthetic"


class ConsentRequiredAgent(Agent):
    """Agent whose MCP connection raises the Foundry gateway consent error."""

    async def __aenter__(self) -> "ConsentRequiredAgent":
        errors = {"errors": [{
            "name": "account-mcp", "type": "mcp",
            "error": {"code": "CONSENT_REQUIRED", "message": CONSENT_LINK},
        }]}
        raise AgentFrameworkException(
            "tools/list failed", MCPError(-32006, f"tools/list failed {json.dumps(errors)}"),
        )


def test_native_host_does_not_surface_nested_agent_consent_as_challenge() -> None:
    def create_workflow(_request: HostedResponseRequest) -> Workflow:
        client = FoundryChatClient(
            project_endpoint="https://example.services.ai.azure.com/api/projects/test",
            model="test-model", credential=MagicMock(),
        )
        triage = Agent(client=client, name="triage_agent", instructions="Route requests")
        account = ConsentRequiredAgent(client=client, name="AccountAgent", instructions="Find")
        return HandoffBuilder(
            participants=[triage, account], termination_condition=lambda _messages: True,
        ).with_start_agent(triage).add_handoff(triage, [account]).build()

    server = ResponsesHostServer(workflow=create_workflow, parse_response=parse_response)
    with TestClient(server) as http:
        reply = http.post("/responses", json={
            "model": "test-model", "input": "Show my balance", "store": True,
            "agent_session_id": "local-session",
        })

    payload: dict[str, Any] = reply.json()
    item_types = {item["type"] for item in payload.get("output", [])}
    # A generic failure, never a structured challenge and never an exposed link.
    assert payload["status"] == "failed"
    assert "oauth_consent_request" not in item_types
    assert CONSENT_LINK not in reply.text
