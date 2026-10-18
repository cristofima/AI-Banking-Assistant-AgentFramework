"""Independent local FastMCP discovery, without database or identity access."""

from __future__ import annotations

import ast
from pathlib import Path
import runpy
from typing import Annotated, Any
from unittest.mock import AsyncMock, MagicMock, call

from fastmcp import Client, FastMCP
from fastmcp.server.dependencies import CurrentHeaders
import pytest
from starlette.concurrency import run_in_threadpool

from banking_transaction.models.transactions import DisputeCase, DisputeCaseEvent

from banking_evals.mcp_replay import load_contracts

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.asyncio
async def test_replay_schemas_match_independent_fastmcp_discovery() -> None:
    for name in ("account", "transaction"):
        source = ROOT / "app" / "business-api" / name / "src" / f"banking_{name}" / "mcp_tools.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        tree.body = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        server = FastMCP("isolated-contract-discovery")
        namespace = {
            "mcp": server, "CurrentHeaders": CurrentHeaders,
            "service": MagicMock(), "dispute_service": MagicMock(),
            "Annotated": Annotated, "Any": Any,
        }
        model_name = "products" if name == "account" else "transactions"
        namespace.update(runpy.run_path(str(source.parent / "models" / f"{model_name}.py")))
        exec(compile(tree, str(source), "exec"), namespace)
        async with Client(server) as client:
            discovered = {tool.name: tool for tool in await client.list_tools()}
        contracts = {tool.name: tool for tool in load_contracts(name)}
        assert contracts.keys() == discovered.keys()
        for tool_name, contract in contracts.items():
            actual = discovered[tool_name]
            assert contract.description == actual.description
            assert contract.inputSchema == actual.inputSchema
    assert "resolveCase" not in contracts
    assert "dismissRecommendation" not in contracts


@pytest.mark.asyncio
async def test_recognition_tool_delegates_verified_customer_in_threadpool() -> None:
    source = ROOT / "app/business-api/transaction/src/banking_transaction/mcp_tools.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "get_transaction_recognition_context"]
    tree.body[0].decorator_list = []
    headers = {"synthetic-identity": "not-a-token"}
    identity = MagicMock(return_value="verified-customer")
    service = MagicMock()
    expected = {"matches": [], "persistedInSupportCase": False}
    service.get_transaction_recognition_context.return_value = expected
    threadpool = AsyncMock(side_effect=run_in_threadpool)
    namespace = {
        "CurrentHeaders": CurrentHeaders, "Any": Any, "service": service,
        "get_customer_id": identity, "run_in_threadpool": threadpool,
    }
    exec(compile(tree, str(source), "exec"), namespace)

    result = await namespace["get_transaction_recognition_context"]("selected", headers)

    assert result == expected
    identity.assert_called_once_with(headers)
    assert threadpool.await_args_list == [
        call(identity, headers),
        call(service.get_transaction_recognition_context, "selected", "verified-customer"),
    ]
    service.get_transaction_recognition_context.assert_called_once_with(
        "selected", "verified-customer",
    )
    identity.side_effect = PermissionError("unavailable")
    threadpool.reset_mock()
    service.reset_mock()
    with pytest.raises(PermissionError, match="unavailable"):
        await namespace["get_transaction_recognition_context"]("selected", headers)
    threadpool.assert_awaited_once_with(identity, headers)
    service.get_transaction_recognition_context.assert_not_called()


def test_dispute_response_models_reject_incomplete_fixture_outputs() -> None:
    with pytest.raises(ValueError):
        DisputeCase.model_validate({"caseId": "SYNTHETIC-CASE", "status": "RESOLVED"})
    with pytest.raises(ValueError):
        DisputeCaseEvent.model_validate({"eventType": "RESOLVED"})