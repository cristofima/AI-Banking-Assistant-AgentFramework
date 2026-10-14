"""Offline checks for per-agent deployment selection and client reuse."""

import importlib.util
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
def test_host_selects_and_reuses_clients(
    shared: str | None,
    overrides: tuple[str | None, str | None, str | None],
    expected: tuple[str, str, str],
) -> None:
    configured = SimpleNamespace(
        FOUNDRY_PROJECT_ENDPOINT="https://example.services.ai.azure.com/api/projects/example",
        MODEL_DEPLOYMENT_NAME=shared,
        TRIAGE_MODEL_DEPLOYMENT_NAME=overrides[0],
        ACCOUNT_MODEL_DEPLOYMENT_NAME=overrides[1],
        TRANSACTION_MODEL_DEPLOYMENT_NAME=overrides[2],
        ACCOUNT_MCP_URL="http://localhost:8070/mcp",
        TRANSACTION_MCP_URL="http://localhost:8071/mcp",
        INTERNAL_IDENTITY_SECRET="synthetic-test-secret-not-a-credential",
    )
    credential = object()
    with (
        patch.object(main_responses_host, "settings", configured),
        patch.object(main_responses_host, "get_azure_credential", return_value=credential),
        patch.object(main_responses_host, "FoundryChatClient") as factory,
        patch.object(main_responses_host, "build_hosted_workflow") as workflow,
        patch.object(main_responses_host, "IsolatedResponsesHostServer") as server,
    ):
        factory.side_effect = lambda **kwargs: SimpleNamespace(**kwargs)
        main_responses_host.create_server()
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
        configured.ACCOUNT_MCP_URL, configured.TRANSACTION_MCP_URL,
        configured.INTERNAL_IDENTITY_SECRET,
    )
    for left in range(3):
        for right in range(3):
            assert (clients[left] is clients[right]) == (expected[left] == expected[right])


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
        patch.object(main_responses_host, "IsolatedResponsesHostServer") as server,
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
        )
    assert triage.call_args.kwargs["client"] is triage_client
    assert account.call_args.args[0] is (account_client if use_overrides else triage_client)
    assert transaction.call_args.args[0] is (transaction_client if use_overrides else triage_client)
