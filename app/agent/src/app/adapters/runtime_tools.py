"""Build experimental Foundry-only Toolbox tools."""

from __future__ import annotations

import os
from typing import Any

from agent_framework import MCPStreamableHTTPTool
from agent_framework.foundry import FoundryToolbox
from azure.core.credentials import TokenCredential
from azure.core.credentials_async import AsyncTokenCredential

from app.common.internal_identity import mcp_header_provider


ACCOUNT_TOOLS = (
    "getAccountsByUserName", "getAccountDetails", "getRegisteredBeneficiary",
    "getCreditCards", "discoverCardsBySuffix", "getCardDetails",
)
TRANSACTION_TOOLS = (
    "getTransactionsByRecipientName", "getCardTransactions", "getLastTransactions",
    "getTransactionRecognitionContext", "previewTransactionDispute",
    "reportTransactionDispute", "recoverTransactionDispute", "respondToDisputeApproval",
    "listSupportCases", "getSupportCase", "getSupportCaseTimeline",
)


def build_toolbox_tools(
    runtime: str,
    project_endpoint: str,
    credential: TokenCredential | AsyncTokenCredential,
    identity_secret: str,
) -> tuple[MCPStreamableHTTPTool, MCPStreamableHTTPTool]:
    """Build hosted Toolbox tools; local execution uses direct MCP instead."""
    if os.environ.get("MCP_IDENTITY_MODE", "hmac") == "oauth":
        raise ValueError("OAuth MCP requires direct HTTP tools; Toolbox passthrough is unverified")
    if runtime != "foundry":
        raise ValueError("AGENT_RUNTIME must be foundry for Toolbox")
    if not project_endpoint.strip():
        raise ValueError("FOUNDRY_PROJECT_ENDPOINT is required for Toolbox")
    endpoint = (
        f"{project_endpoint.rstrip('/')}/toolboxes/banking-tools/mcp?api-version=v1"
    )
    identity_headers = mcp_header_provider(identity_secret)

    def header_provider(runtime_kwargs: dict[str, Any]) -> dict[str, str]:
        # Azure owns Authorization on this hop. Forwarding this separate header
        # downstream is experimental; banking MCP authentication stays fail-closed.
        headers = identity_headers(runtime_kwargs)
        return {"x-banking-mcp-authorization": headers["Authorization"]}

    def toolbox(service: str, names: tuple[str, ...]) -> FoundryToolbox:
        return FoundryToolbox(
            credential,
            url=endpoint,
            name=f"{service} Toolbox",
            allowed_tools=[f"{service}___{name}" for name in names],
            header_provider=header_provider,
        )

    return toolbox("account", ACCOUNT_TOOLS), toolbox("transaction", TRANSACTION_TOOLS)
