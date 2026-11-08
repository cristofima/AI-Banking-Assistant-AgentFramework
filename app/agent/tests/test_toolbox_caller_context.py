"""Offline diagnostics of the installed Toolbox caller-header integration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx
import pytest
from agent_framework import MCPStreamableHTTPTool
from azure.ai.agentserver.core import (
    FoundryAgentRequestContext,
    get_request_context,
    reset_request_context,
    set_request_context,
)

from app.adapters import runtime_tools


@pytest.fixture
async def tools(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[MCPStreamableHTTPTool, MCPStreamableHTTPTool]]:
    clients: list[httpx.AsyncClient] = []
    client_class = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        instance = client_class(**kwargs)
        clients.append(instance)
        return instance

    monkeypatch.setenv("MCP_IDENTITY_MODE", "hmac")
    monkeypatch.setattr("agent_framework_foundry_hosting._toolbox.httpx.AsyncClient", client)
    monkeypatch.setattr(
        runtime_tools, "mcp_header_provider",
        lambda secret: lambda kwargs: {"Authorization": "synthetic-banking-bearer"},
    )
    try:
        yield runtime_tools.build_toolbox_tools(
            "foundry", "https://example.test/api/projects/bank", None, "synthetic-secret",
        )
    finally:
        for instance in clients:
            await instance.aclose()


def headers(tool: MCPStreamableHTTPTool) -> dict[str, str]:
    # Exercise the installed SDK's operation-boundary hook without opening an MCP connection.
    return tool._effective_headers({})


async def test_installed_toolbox_forwards_only_current_platform_call_id(
    tools: tuple[MCPStreamableHTTPTool, MCPStreamableHTTPTool],
) -> None:
    original = get_request_context()
    for call_id in ("synthetic-call-a", "synthetic-call-b", None):
        token = set_request_context(FoundryAgentRequestContext(
            call_id=call_id, user_id="synthetic-platform-user", session_id="synthetic-session",
        ))
        try:
            expected = {"x-banking-mcp-authorization": "synthetic-banking-bearer"}
            if call_id is not None:
                expected["x-agent-foundry-call-id"] = call_id
            for tool in tools:
                assert headers(tool) == expected
        finally:
            reset_request_context(token)
    assert get_request_context() is original


async def test_installed_toolbox_keeps_concurrent_caller_headers_isolated(
    tools: tuple[MCPStreamableHTTPTool, MCPStreamableHTTPTool],
) -> None:
    original = get_request_context()
    ready = [asyncio.Event(), asyncio.Event()]

    async def caller(index: int) -> list[dict[str, str]]:
        token = set_request_context(FoundryAgentRequestContext(
            call_id=f"synthetic-call-{index}", user_id=f"synthetic-user-{index}",
        ))
        try:
            ready[index].set()
            await ready[1 - index].wait()
            return [headers(tool) for tool in tools]
        finally:
            reset_request_context(token)

    results = await asyncio.gather(caller(0), caller(1))
    for index, result in enumerate(results):
        assert result == [{
            "x-banking-mcp-authorization": "synthetic-banking-bearer",
            "x-agent-foundry-call-id": f"synthetic-call-{index}",
        }] * 2
    assert get_request_context() is original
