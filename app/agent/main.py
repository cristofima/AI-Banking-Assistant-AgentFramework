"""Foundry Hosted Agent entry point for the Responses protocol."""

import asyncio

from agent_framework import WorkflowAgent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.responses import ResponsesServerOptions

from app.agents.azure_chat.hosted_workflow import build_hosted_workflow
from app.adapters.runtime_tools import build_toolbox_tools
from app.common.internal_identity import BankingIdentityMiddleware
from app.config.azure_credential import get_azure_credential
from app.config.settings import settings
from app.telemetry import configure_telemetry_flush


def _required_setting(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} must be configured for the Responses host")
    return value


def create_server() -> ResponsesHostServer:
    """Create the Responses host without connecting to external services."""
    credential = get_azure_credential()
    project_endpoint = _required_setting(
        settings.FOUNDRY_PROJECT_ENDPOINT, "FOUNDRY_PROJECT_ENDPOINT",
    )
    clients: dict[str, FoundryChatClient] = {}

    def client_for(deployment: str | None, setting_name: str) -> FoundryChatClient:
        model = _required_setting(
            deployment or settings.MODEL_DEPLOYMENT_NAME,
            f"{setting_name} or MODEL_DEPLOYMENT_NAME",
        )
        if model not in clients:
            clients[model] = FoundryChatClient(
                project_endpoint=project_endpoint,
                model=model,
                credential=credential,
            )
        return clients[model]

    chat_client = client_for(
        settings.TRIAGE_MODEL_DEPLOYMENT_NAME, "TRIAGE_MODEL_DEPLOYMENT_NAME",
    )
    account_chat_client = client_for(
        settings.ACCOUNT_MODEL_DEPLOYMENT_NAME, "ACCOUNT_MODEL_DEPLOYMENT_NAME",
    )
    transaction_chat_client = client_for(
        settings.TRANSACTION_MODEL_DEPLOYMENT_NAME, "TRANSACTION_MODEL_DEPLOYMENT_NAME",
    )
    runtime = settings.AGENT_RUNTIME
    if runtime not in {"local", "foundry"}:
        raise ValueError("AGENT_RUNTIME must be local or foundry")
    account_url = (
        _required_setting(settings.ACCOUNT_MCP_URL, "ACCOUNT_MCP_URL")
        if runtime == "local" else ""
    )
    transaction_url = (
        _required_setting(settings.TRANSACTION_MCP_URL, "TRANSACTION_MCP_URL")
        if runtime == "local" else ""
    )
    identity_secret = _required_setting(settings.INTERNAL_IDENTITY_SECRET, "INTERNAL_IDENTITY_SECRET")

    def create_agent() -> WorkflowAgent:
        toolbox_tools = (
            build_toolbox_tools(runtime, project_endpoint, credential, identity_secret)
            if runtime == "foundry" else (None, None)
        )
        return build_hosted_workflow(
            chat_client, account_url, transaction_url, identity_secret,
            account_chat_client=account_chat_client,
            transaction_chat_client=transaction_chat_client,
            account_tool=toolbox_tools[0],
            transaction_tool=toolbox_tools[1],
        ).as_agent(
            name="home_banking_agent",
            description="Answers authenticated account and transaction questions.",
        )

    configure_telemetry_flush()
    server = ResponsesHostServer(
        create_agent, options=ResponsesServerOptions(default_fetch_history_count=100),
    )
    if runtime == "foundry":
        server.add_middleware(BankingIdentityMiddleware)
    return server


async def main() -> None:
    """Run the Responses host for local or Foundry-managed hosting."""
    await create_server().run_async()


if __name__ == "__main__":
    asyncio.run(main())