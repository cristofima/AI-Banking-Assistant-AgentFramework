"""Offline contract checks for the gated banking Toolbox candidate."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def toolbox_definition() -> dict[str, Any]:
    definition_path = REPOSITORY_ROOT / "app" / "agent" / "toolbox.yaml"
    return yaml.safe_load(definition_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("service", ["account", "transaction"])
def test_toolbox_allowlist_matches_active_mcp_tools(
    toolbox_definition: dict[str, Any], service: str,
) -> None:
    source_path = (
        REPOSITORY_ROOT / "app" / "business-api" / service / "src"
        / f"banking_{service}" / "mcp_tools.py"
    )
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    active_tools: set[str] = set()
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "mcp"
                and decorator.func.attr == "tool"
            ):
                exported_name = next(
                    (keyword.value for keyword in decorator.keywords if keyword.arg == "name"),
                    None,
                )
                if exported_name is None:
                    active_tools.add(node.name)
                else:
                    assert isinstance(exported_name, ast.Constant)
                    assert isinstance(exported_name.value, str)
                    active_tools.add(exported_name.value)
    server = next(
        tool for tool in toolbox_definition["tools"] if tool["server_label"] == service
    )
    assert active_tools
    assert set(server["allowed_tools"]) == active_tools
    assert len(server["allowed_tools"]) == len(active_tools)


@pytest.mark.parametrize("service", ["account", "transaction"])
def test_declarative_toolbox_uses_no_auth_urls_and_matching_allowlists(
    toolbox_definition: dict[str, Any], service: str,
) -> None:
    manifest_path = REPOSITORY_ROOT / "app" / "agent" / "azure.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    toolbox = manifest["services"]["banking-tools"]
    assert toolbox["host"] == "azure.ai.toolbox"
    assert len(toolbox["tools"]) == 2
    server = next(tool for tool in toolbox["tools"] if tool["server_label"] == service)
    candidate = next(
        tool for tool in toolbox_definition["tools"] if tool["server_label"] == service
    )
    assert server["server_url"] == f"${{{service.upper()}_MCP_URL}}"
    assert server["allowed_tools"] == candidate["allowed_tools"]
    assert server["type"] == "mcp"
    assert set(server) == {"type", "server_label", "server_url", "allowed_tools"}
    assert "banking-tools" in manifest["services"]["home-banking-agent"]["uses"]
    assert manifest["services"]["home-banking-agent"]["env"]["AGENT_RUNTIME"] == "foundry"


def test_toolbox_contains_only_connection_backed_banking_servers(
    toolbox_definition: dict[str, Any],
) -> None:
    assert toolbox_definition["name"] == "banking-tools"
    servers = toolbox_definition["tools"]
    assert len(servers) == 2
    assert {server["server_label"] for server in servers} == {"account", "transaction"}
    for server in servers:
        assert server["type"] == "mcp"
        assert server["project_connection_id"] == f"{server['server_label']}-mcp"
        assert set(server) == {
            "type", "server_label", "project_connection_id", "allowed_tools",
        }
