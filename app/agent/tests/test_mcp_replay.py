"""Offline protocol checks, not model-quality or live authorization evidence."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from agent_framework import MCPStreamableHTTPTool

from app.agents.azure_chat.account_agent import AccountAgent
from app.agents.azure_chat.transaction_agent import TransactionHistoryAgent
from app.agents.azure_chat.hosted_workflow import build_hosted_workflow
from banking_evals.mcp_replay import ReplayReply, ReplayServer, load_contracts
from banking_evals.mcp.runner import evaluation_names, run_case


async def test_replay_discovers_contracts_and_executes_without_http() -> None:
    replay = ReplayServer("account", [ReplayReply(
        "getAccountDetails", {"product_number": "SYNTHETIC-001"},
        {"accountNumber": "SYNTHETIC-001", "balance": 12, "currency": "USD"},
    )])
    async with replay.connect() as session:
        tools = await session.list_tools()
        contract = next(item for item in tools.tools if item.name == "getAccountDetails")
        assert contract.description == "Get account details and available payment methods"
        assert contract.input_schema["required"] == ["product_number"]
        assert "headers" not in contract.input_schema["properties"]
        async with MCPStreamableHTTPTool(
            name="Account MCP server client", url=None, mcp_client=session,
            load_prompts=False,
        ) as client:
            assert any(item.name == "getAccountDetails" for item in client.functions)
            result = await session.call_tool("getAccountDetails", {"product_number": "SYNTHETIC-001"})
            assert not result.is_error
            assert json.loads(result.content[0].text)["balance"] == 12
    replay.assert_complete()


async def test_replay_propagates_errors_and_rejects_unexpected_calls() -> None:
    replay = ReplayServer("account", [ReplayReply(
        "getAccountDetails", {"product_number": "FOREIGN"},
        error="Account does not belong to the authenticated customer",
    )])
    async with replay.connect() as session:
        denied = await session.call_tool("getAccountDetails", {"product_number": "FOREIGN"})
        assert denied.is_error
        replay.assert_complete()
        unexpected = await session.call_tool("getAccountsByUserName", {"userName": "other"})
        assert unexpected.is_error
    with pytest.raises(AssertionError, match="Unexpected replay call"):
        replay.assert_complete()


async def test_replay_instances_and_server_contracts_are_isolated() -> None:
    first = ReplayServer("transaction", [ReplayReply("listSupportCases", {}, [])])
    second = ReplayServer("transaction", [ReplayReply("listSupportCases", {}, [{"id": "CASE"}])])
    async with first.connect() as first_session, second.connect() as second_session:
        assert (await first_session.call_tool("listSupportCases", {})).content[0].text == "[]"
        assert (await second_session.call_tool("listSupportCases", {})).content[0].text != "[]"
    first.assert_complete()
    second.assert_complete()
    assert "getAccountDetails" not in {item.name for item in load_contracts("transaction")}


async def test_workflow_passes_injected_sessions_to_all_mcp_clients() -> None:
    account = ReplayServer("account", [])
    transaction = ReplayServer("transaction", [])
    async with account.connect() as account_session, transaction.connect() as transaction_session:
        with patch("app.agents.azure_chat.account_agent.MCPStreamableHTTPTool") as account_tool:
            with patch("app.agents.azure_chat.transaction_agent.MCPStreamableHTTPTool") as transaction_tool:
                account_tool.return_value = MCPStreamableHTTPTool(
                    name="account", url=None, mcp_client=account_session,
                )
                transaction_tool.return_value = MCPStreamableHTTPTool(
                    name="transaction", url=None, mcp_client=transaction_session,
                )
                build_hosted_workflow(
                    MagicMock(), "in-memory://account", "in-memory://transaction",
                    "test-secret-key-with-at-least-32-bytes",
                    account_mcp_client=account_session, transaction_mcp_client=transaction_session,
                )
        assert account_tool.call_args.kwargs["mcp_client"] is account_session
        assert transaction_tool.call_args_list[0].kwargs["mcp_client"] is account_session
        assert transaction_tool.call_args_list[1].kwargs["mcp_client"] is transaction_session
        assert account_tool.call_args.kwargs["header_provider"] is None
        assert all(call.kwargs["header_provider"] is None
                   for call in transaction_tool.call_args_list)


@pytest.mark.parametrize("specialist", ["account", "transaction"])
async def test_specialist_invokes_injected_mcp_tools_without_http_identity(
    specialist: str,
) -> None:
    account = ReplayServer("account", [ReplayReply(
        "getAccountDetails", {"product_number": "TEST"}, {"balance": 12},
    )])
    transaction = ReplayServer("transaction", [ReplayReply("listSupportCases", {}, [])])
    async with account.connect() as account_session, transaction.connect() as transaction_session:
        module = f"app.agents.azure_chat.{specialist}_agent.MCPStreamableHTTPTool"
        with patch(module, wraps=MCPStreamableHTTPTool) as constructor, patch(
            f"app.agents.azure_chat.{specialist}_agent.mcp_header_provider",
            return_value=lambda _: {"Authorization": "synthetic-replay-only"},
        ):
            if specialist == "account":
                AccountAgent(
                    MagicMock(), "in-memory://account", "test-secret",
                    account_mcp_client=account_session,
                ).build_af_agent()
            else:
                TransactionHistoryAgent(
                    MagicMock(), "in-memory://account", "in-memory://transaction", "test-secret",
                    account_mcp_client=account_session, transaction_mcp_client=transaction_session,
                ).build_af_agent()
        for call in constructor.call_args_list:
            async with MCPStreamableHTTPTool(**call.kwargs) as client:
                tool_name = "getAccountDetails" if call.kwargs["mcp_client"] is account_session else "listSupportCases"
                function = next(item for item in client.functions if item.name == tool_name)
                arguments = {"product_number": "TEST"} if tool_name == "getAccountDetails" else {}
                await function.invoke(arguments=arguments)
    account.assert_complete()
    if specialist == "transaction":
        transaction.assert_complete()


def test_production_mcp_clients_retain_signed_header_providers() -> None:
    with patch("app.agents.azure_chat.account_agent.MCPStreamableHTTPTool") as account_tool:
        with patch("app.agents.azure_chat.transaction_agent.MCPStreamableHTTPTool") as transaction_tool:
            account_tool.return_value = MCPStreamableHTTPTool(name="account", url="https://example.invalid")
            transaction_tool.return_value = MCPStreamableHTTPTool(name="transaction", url="https://example.invalid")
            build_hosted_workflow(MagicMock(), "https://account.invalid", "https://transaction.invalid",
                                  "test-secret-key-with-at-least-32-bytes")
    calls = [account_tool.call_args, *transaction_tool.call_args_list]
    assert len(calls) == 3
    assert all(call.kwargs["mcp_client"] is None for call in calls)
    assert all(callable(call.kwargs["header_provider"]) for call in calls)


def test_evaluation_names_follow_agent_first_reference_convention() -> None:
    assert evaluation_names("home-banking-agent") == (
        "home-banking-agent-mcp-replay-eval", "home-banking-agent-mcp-replay-eval run",
    )


async def test_runner_preserves_signed_identity_and_records_failures() -> None:
    from azure.ai.agentserver.core import get_request_context
    from app.common.internal_identity import get_internal_principal

    def fail_workflow(*args: object, **kwargs: object) -> None:
        principal = get_internal_principal("synthetic-replay-only-not-a-production-secret")
        assert principal.locale == "es"
        assert principal.customer_id == "SYNTHETIC-CUSTOMER"
        raise RuntimeError("controlled model failure")

    original_context = get_request_context()
    with patch("banking_evals.mcp.runner.build_hosted_workflow", side_effect=fail_workflow):
        result = await run_case(MagicMock(), {
            "id": "failure", "locale": "es", "query": "test", "expected_behavior": "test",
        })
    assert not result["protocol_passed"]
    assert result["error"]["type"] == "ExceptionGroup"
    cause = result["error"]
    while "causes" in cause:
        assert cause["message"] == "Replay execution failed"
        assert len(cause["causes"]) == 1
        cause = cause["causes"][0]
    assert cause == {"type": "RuntimeError", "message": "Replay execution failed"}
    assert "controlled model failure" not in json.dumps(result)
    assert result["behavior_review"] == "pending"
    assert get_request_context() is original_context


async def test_runner_records_full_transcript_and_interleaved_tools() -> None:
    from unittest.mock import AsyncMock

    text = "Complete synthetic answer. " * 30
    response = MagicMock(text=text)
    response.to_dict.return_value = {"messages": [{"text": text}]}

    async def final_response() -> object:
        await transaction_session.call_tool("getLastTransactions", {"product_number": "TEST"})
        await account_session.call_tool("getAccountDetails", {"product_number": "TEST"})
        return response

    triage_client, account_client, transaction_client = MagicMock(), MagicMock(), MagicMock()

    def build_workflow(*args: object, **kwargs: object) -> MagicMock:
        nonlocal account_session, transaction_session
        assert args[0] is triage_client
        assert kwargs["account_chat_client"] is account_client
        assert kwargs["transaction_chat_client"] is transaction_client
        account_session = kwargs["account_mcp_client"]
        transaction_session = kwargs["transaction_mcp_client"]
        workflow = MagicMock()
        workflow.as_agent.return_value.run.return_value.get_final_response = AsyncMock(
            side_effect=final_response,
        )
        return workflow

    account_session = None
    transaction_session = None
    with patch("banking_evals.mcp.runner.build_hosted_workflow", side_effect=build_workflow):
        result = await run_case(triage_client, {
            "id": "success", "query": "test", "expected_behavior": "test",
            "account": [{"tool": "getAccountDetails", "arguments": {"product_number": "TEST"}, "result": {}}],
            "transaction": [{"tool": "getLastTransactions", "arguments": {"product_number": "TEST"}, "result": []}],
        }, account_chat_client=account_client, transaction_chat_client=transaction_client)
    assert result["protocol_passed"]
    assert result["final_answer"] == text
    assert result["response"]["messages"][0]["text"] == text
    assert [call["server"] for call in result["tool_calls"]] == ["transaction", "account"]
    assert [call["sequence"] for call in result["tool_calls"]] == [0, 1]


def test_replay_fixtures_match_public_tool_arguments() -> None:
    import json
    from pathlib import Path

    cases = json.loads((Path(__file__).resolve().parents[3] / "evals" / "replay_cases.json").read_text())
    for case in cases:
        for server in ("account", "transaction"):
            contracts = {tool.name: tool.input_schema for tool in load_contracts(server)}
            for reply in case.get(server, []):
                assert set(reply["arguments"]) == set(contracts[reply["tool"]]["properties"])


async def test_multi_turn_runner_reuses_session_and_isolates_cases() -> None:
    from unittest.mock import AsyncMock

    sessions = []
    agents = []

    def build_workflow(*args: object, **kwargs: object) -> MagicMock:
        workflow = MagicMock()
        agent = workflow.as_agent.return_value
        session = object()
        sessions.append(session)
        agents.append(agent)
        agent.create_session.return_value = session
        response = MagicMock(text="Synthetic response")
        response.to_dict.return_value = {"messages": [{"text": response.text}]}
        agent.run.return_value.get_final_response = AsyncMock(return_value=response)
        return workflow

    case = {"id": "continuation", "query": "first", "turns": ["first", "approve"],
            "expected_behavior": "test"}
    with patch("banking_evals.mcp.runner.build_hosted_workflow", side_effect=build_workflow):
        first = await run_case(MagicMock(), case)
        second = await run_case(MagicMock(), case)
    assert first["protocol_passed"] and second["protocol_passed"]
    assert sessions[0] is not sessions[1]
    for agent, session in zip(agents, sessions, strict=True):
        assert [call.args[0] for call in agent.run.call_args_list] == case["turns"]
        assert all(call.kwargs["session"] is session for call in agent.run.call_args_list)
    assert [turn["turn"] for turn in first["turns"]] == [0, 1]
    assert all(turn["latency_seconds"] >= 0 for turn in first["turns"])


@pytest.mark.parametrize("overrides, expected", [
    ([], {"triage": "base", "account": "base", "transaction": "base"}),
    (["--triage-model", "router", "--account-model", "account", "--transaction-model", "movements"],
     {"triage": "router", "account": "account", "transaction": "movements"}),
    (["--triage-model", "", "--account-model", "specialist", "--transaction-model", "specialist"],
     {"triage": "base", "account": "specialist", "transaction": "specialist"}),
])
async def test_replay_cli_resolves_and_records_participant_models(
    tmp_path: Path, overrides: list[str], expected: dict[str, str],
) -> None:
    from unittest.mock import AsyncMock

    from banking_evals.mcp.runner import main, main_async

    dataset = tmp_path / "cases.json"
    dataset.write_text(json.dumps([{"id": "model-routing"}]), encoding="utf-8")
    output = tmp_path / "report.json"
    argv = ["run_mcp_replay", "--project-endpoint", "https://example.invalid",
            "--model", "base", "--dataset", str(dataset), "--output", str(output), *overrides]
    with patch("sys.argv", argv), patch("banking_evals.mcp.runner.asyncio.run", return_value=0) as run:
        with patch("banking_evals.mcp.runner.main_async", new=MagicMock()) as parse:
            assert main() == 0
        args = parse.call_args.args[0]
        run.assert_called_once()

    credential_context = MagicMock()
    credential_context.__aenter__ = AsyncMock(return_value=MagicMock())
    credential_context.__aexit__ = AsyncMock(return_value=False)
    clients = {model: MagicMock() for model in set(expected.values())}
    with patch("banking_evals.mcp.runner.AzureCliCredential", return_value=credential_context), \
         patch("banking_evals.mcp.runner.FoundryChatClient",
               side_effect=lambda **kwargs: clients[kwargs["model"]]) as factory, \
         patch("banking_evals.mcp.runner.run_case", new=AsyncMock(
             return_value={"protocol_passed": True},
         )) as replay:
        assert await main_async(args) == 0

    assert factory.call_count == len(clients)
    assert {call.kwargs["model"] for call in factory.call_args_list} == set(expected.values())
    for call in factory.call_args_list:
        assert call.kwargs["project_endpoint"] == args.project_endpoint
        assert call.kwargs["credential"] is credential_context.__aenter__.return_value
    replay.assert_awaited_once_with(
        clients[expected["triage"]], {"id": "model-routing"}, args.timeout_seconds,
        account_chat_client=clients[expected["account"]],
        transaction_chat_client=clients[expected["transaction"]],
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["model"] == "base"
    assert report["participant_models"] == expected
    assert report["model_execution"] == "real"