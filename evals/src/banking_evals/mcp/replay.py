"""Synthetic, in-memory MCP transport for isolated workflow evaluations."""

from __future__ import annotations

import ast
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from mcp import Client, types
from mcp.server import Server


from banking_evals.resources import repository_root

ROOT = repository_root()


from banking_evals.mcp.contracts import load_contracts

@dataclass(frozen=True)
class ReplayReply:
    tool: str
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None


@dataclass
class ReplayServer:
    name: str
    replies: list[ReplayReply]
    trace: list[dict[str, Any]] | None = None
    turn: int | None = None
    calls: list[dict[str, Any]] = field(default_factory=list, init=False)
    failures: list[str] = field(default_factory=list, init=False)
    _position: int = field(default=0, init=False)

    def build(self) -> Server:
        contracts = load_contracts(self.name)

        async def list_tools(
            ctx: Any, params: types.PaginatedRequestParams | None
        ) -> types.ListToolsResult:
            return types.ListToolsResult(tools=contracts)

        async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
            name, arguments = params.name, params.arguments or {}
            call = {"server": self.name, "tool": name, "arguments": arguments}
            if self.turn is not None:
                call["turn"] = self.turn
            self.calls.append(call)
            if self.trace is not None:
                call["sequence"] = len(self.trace)
                self.trace.append(call)
            if self._position >= len(self.replies):
                error = f"Unexpected replay call: {self.name}/{name}"
            else:
                reply = self.replies[self._position]
                if name != reply.tool or arguments != reply.arguments:
                    error = f"Replay call does not match step {self._position}: {self.name}/{name}"
                else:
                    self._position += 1
                    call.update(result=reply.result, error=reply.error)
                    return types.CallToolResult(
                        content=[types.TextContent(type="text", text=(
                            reply.error if reply.error else json.dumps(reply.result, ensure_ascii=False)
                        ))],
                        isError=reply.error is not None,
                    )
            self.failures.append(error)
            call["error"] = error
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=error)], isError=True,
            )

        return Server(
            f"banking-replay-{self.name}", on_list_tools=list_tools, on_call_tool=call_tool,
        )

    def assert_complete(self) -> None:
        if self.failures or self._position != len(self.replies):
            raise AssertionError(
                f"{self.name}: consumed {self._position}/{len(self.replies)} replies; "
                f"failures={self.failures}"
            )

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[Client]:
        async with Client(self.build()) as client:
            yield client