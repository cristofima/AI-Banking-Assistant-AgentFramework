"""Offline checks for per-agent deployment selection and client reuse."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.agents.azure_chat import hosted_workflow


entrypoint_path = Path(__file__).resolve().parents[1] / "main.py"
entrypoint_spec = importlib.util.spec_from_file_location("banking_agent_entrypoint", entrypoint_path)
assert entrypoint_spec is not None and entrypoint_spec.loader is not None
main_responses_host = importlib.util.module_from_spec(entrypoint_spec)
entrypoint_spec.loader.exec_module(main_responses_host)


@pytest.mark.parametrize(
    ("shared", "overrides", "expected"),
    [
        ("gpt-4.1-mini", (None, None, None), ("gpt-4.1-mini",) * 3),
        ("gpt-4.1-mini", ("", "", ""), ("gpt-4.1-mini",) * 3),
        (
            "gpt-4.1-mini", ("model-router", "gpt-5.4", "gpt-5.4"),
            ("model-router", "gpt-5.4", "gpt-5.4"),
        ),
        (
            "gpt-4.1-mini", ("router", "account", "transaction"),
            ("router", "account", "transaction"),
        ),
        ("gpt-4.1-mini", ("gpt-4.1-mini", None, "gpt-4.1-mini"), ("gpt-4.1-mini",) * 3),
        (
            "gpt-4.1-mini", (None, "gpt-5.4", None),
            ("gpt-4.1-mini", "gpt-5.4", "gpt-4.1-mini"),
        ),
        (
            "gpt-4.1-mini", (None, "", "transaction"),
            ("gpt-4.1-mini", "gpt-4.1-mini", "transaction"),
        ),
        (None, ("router", "account", "transaction"), ("router", "account", "transaction")),
        ("", ("router", "account", "transaction"), ("router", "account", "transaction")),
        (None, ("router", "specialist", "specialist"), ("router", "specialist", "specialist")),
        ("", ("router", "specialist", "specialist"), ("router", "specialist", "specialist")),
    ],
)
@pytest.mark.parametrize("runtime", ["local", "foundry"])
def test_host_selects_and_reuses_clients(
    runtime: str,
    shared: str | None,
    overrides: tuple[str | None, str | None, str | None],
    expected: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTSERVER_FLUSH_MODE", "sync")
    configured = SimpleNamespace(
        FOUNDRY_PROJECT_ENDPOINT="https://example.services.ai.azure.com/api/projects/example",
        AGENT_RUNTIME=runtime,
        MODEL_DEPLOYMENT_NAME=shared,
        TRIAGE_MODEL_DEPLOYMENT_NAME=overrides[0],
        ACCOUNT_MODEL_DEPLOYMENT_NAME=overrides[1],
        TRANSACTION_MODEL_DEPLOYMENT_NAME=overrides[2],
        ACCOUNT_MCP_URL="http://localhost:8070/mcp/" if runtime == "local" else None,
        TRANSACTION_MCP_URL="http://localhost:8071/mcp/" if runtime == "local" else None,
        INTERNAL_IDENTITY_SECRET="synthetic-test-secret-not-a-credential",
    )
    credential = object()
    with (
        patch.object(main_responses_host, "settings", configured),
        patch.object(main_responses_host, "get_azure_credential", return_value=credential),
        patch.object(main_responses_host, "FoundryChatClient") as factory,
        patch.object(main_responses_host, "build_hosted_workflow") as workflow,
        patch.object(main_responses_host, "build_toolbox_tools") as toolbox,
        patch.object(main_responses_host, "ResponsesHostServer") as server,
    ):
        factory.side_effect = lambda **kwargs: SimpleNamespace(**kwargs)
        tools = (MagicMock(), MagicMock())
        toolbox.return_value = tools

        def construct_host(*args: object, **kwargs: object) -> MagicMock:
            assert os.environ["AGENTSERVER_FLUSH_MODE"] == "async"
            return MagicMock()

        server.side_effect = construct_host
        host = main_responses_host.create_server()
        assert server.call_args.kwargs["options"].default_fetch_history_count == 100
        if runtime == "foundry":
            host.add_middleware.assert_called_once_with(main_responses_host.BankingIdentityMiddleware)
        else:
            host.add_middleware.assert_not_called()
        create_agent = server.call_args.args[0]
        create_agent()
        create_agent()

    call = workflow.call_args
    clients = (
        call.args[0], call.kwargs["account_chat_client"],
        call.kwargs["transaction_chat_client"],
    )
    assert tuple(client.model for client in clients) == expected
    assert factory.call_count == len(set(expected))
    assert all(client.credential is credential for client in clients)
    assert all(client.project_endpoint == configured.FOUNDRY_PROJECT_ENDPOINT for client in clients)
    assert call.args[1:] == (
        configured.ACCOUNT_MCP_URL if runtime == "local" else "",
        configured.TRANSACTION_MCP_URL if runtime == "local" else "",
        configured.INTERNAL_IDENTITY_SECRET,
    )
    if runtime == "foundry":
        assert toolbox.call_args.args == (
            runtime, configured.FOUNDRY_PROJECT_ENDPOINT, credential,
            configured.INTERNAL_IDENTITY_SECRET,
        )
        assert toolbox.call_count == 2
    else:
        toolbox.assert_not_called()
    assert call.kwargs["account_tool"] is (tools[0] if runtime == "foundry" else None)
    assert call.kwargs["transaction_tool"] is (tools[1] if runtime == "foundry" else None)
    for left in range(3):
        for right in range(3):
            assert (clients[left] is clients[right]) == (expected[left] == expected[right])


@pytest.mark.parametrize("missing_setting", ["ACCOUNT_MCP_URL", "TRANSACTION_MCP_URL"])
@pytest.mark.parametrize("missing_value", [None, ""])
def test_local_host_requires_direct_mcp_urls(
    missing_setting: str, missing_value: str | None,
) -> None:
    configured = SimpleNamespace(
        FOUNDRY_PROJECT_ENDPOINT="https://example.test/api/projects/bank",
        AGENT_RUNTIME="local",
        MODEL_DEPLOYMENT_NAME="model",
        TRIAGE_MODEL_DEPLOYMENT_NAME=None,
        ACCOUNT_MODEL_DEPLOYMENT_NAME=None,
        TRANSACTION_MODEL_DEPLOYMENT_NAME=None,
        ACCOUNT_MCP_URL="http://localhost:8070/mcp/",
        TRANSACTION_MCP_URL="http://localhost:8071/mcp/",
        INTERNAL_IDENTITY_SECRET="synthetic-test-secret-not-a-credential",
    )
    setattr(configured, missing_setting, missing_value)
    with (
        patch.object(main_responses_host, "settings", configured),
        patch.object(main_responses_host, "get_azure_credential", return_value=object()),
        patch.object(main_responses_host, "FoundryChatClient"),
        patch.object(main_responses_host, "build_toolbox_tools") as toolbox,
        patch.object(main_responses_host, "ResponsesHostServer") as server,
        pytest.raises(RuntimeError, match=missing_setting),
    ):
        main_responses_host.create_server()
    toolbox.assert_not_called()
    server.assert_not_called()


@pytest.mark.parametrize(
    "missing_setting",
    [
        "TRIAGE_MODEL_DEPLOYMENT_NAME",
        "ACCOUNT_MODEL_DEPLOYMENT_NAME",
        "TRANSACTION_MODEL_DEPLOYMENT_NAME",
    ],
)
@pytest.mark.parametrize("shared", [None, ""])
@pytest.mark.parametrize("missing_value", [None, ""])
def test_host_requires_specific_deployment_or_shared_fallback(
    missing_setting: str,
    shared: str | None,
    missing_value: str | None,
) -> None:
    configured = SimpleNamespace(
        FOUNDRY_PROJECT_ENDPOINT="https://example.services.ai.azure.com/api/projects/example",
        MODEL_DEPLOYMENT_NAME=shared,
        TRIAGE_MODEL_DEPLOYMENT_NAME="model-router",
        ACCOUNT_MODEL_DEPLOYMENT_NAME="gpt-5.4",
        TRANSACTION_MODEL_DEPLOYMENT_NAME="gpt-5.4",
    )
    setattr(configured, missing_setting, missing_value)
    with (
        patch.object(main_responses_host, "settings", configured),
        patch.object(main_responses_host, "get_azure_credential", return_value=object()),
        patch.object(main_responses_host, "FoundryChatClient"),
        patch.object(main_responses_host, "ResponsesHostServer") as server,
        pytest.raises(RuntimeError) as error,
    ):
        main_responses_host.create_server()

    assert f"{missing_setting} or MODEL_DEPLOYMENT_NAME" in str(error.value)
    server.assert_not_called()


@pytest.mark.parametrize("use_overrides", [False, True])
def test_workflow_passes_specialist_clients(use_overrides: bool) -> None:
    triage_client = MagicMock()
    account_client = MagicMock() if use_overrides else None
    transaction_client = MagicMock() if use_overrides else None
    account_tool = MagicMock() if use_overrides else None
    transaction_tool = MagicMock() if use_overrides else None
    with (
        patch.object(hosted_workflow, "Agent") as triage,
        patch.object(hosted_workflow, "AccountAgent") as account,
        patch.object(hosted_workflow, "TransactionHistoryAgent") as transaction,
        patch.object(hosted_workflow, "HandoffBuilder"),
    ):
        hosted_workflow.build_hosted_workflow(
            triage_client, "http://localhost:8070/mcp", "http://localhost:8071/mcp",
            "synthetic-test-secret-not-a-credential",
            account_chat_client=account_client,
            transaction_chat_client=transaction_client,
            account_tool=account_tool,
            transaction_tool=transaction_tool,
        )
    assert triage.call_args.kwargs["client"] is triage_client
    assert account.call_args.args[0] is (account_client if use_overrides else triage_client)
    assert transaction.call_args.args[0] is (transaction_client if use_overrides else triage_client)
    assert account.call_args.kwargs["account_tool"] is account_tool
    assert transaction.call_args.kwargs["account_tool"] is account_tool
    assert transaction.call_args.kwargs["transaction_tool"] is transaction_tool
