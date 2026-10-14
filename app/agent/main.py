"""Foundry Hosted Agent entry point for the Responses protocol."""

import asyncio

from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer

from app.agents.azure_chat.hosted_workflow import build_hosted_workflow
from app.config.azure_credential import get_azure_credential
from app.config.settings import settings
from app.adapters.isolated_responses_host import IsolatedResponsesHostServer
from agent_framework import WorkflowAgent


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
    account_url = _required_setting(settings.ACCOUNT_MCP_URL, "ACCOUNT_MCP_URL")
    transaction_url = _required_setting(settings.TRANSACTION_MCP_URL, "TRANSACTION_MCP_URL")
    identity_secret = _required_setting(settings.INTERNAL_IDENTITY_SECRET, "INTERNAL_IDENTITY_SECRET")

    def create_agent() -> WorkflowAgent:
        return build_hosted_workflow(
            chat_client, account_url, transaction_url, identity_secret,
            account_chat_client=account_chat_client,
            transaction_chat_client=transaction_chat_client,
        ).as_agent(
            name="home_banking_agent",
            description="Answers authenticated account and transaction questions.",
        )

    return IsolatedResponsesHostServer(create_agent)


async def main() -> None:
    """Run the Responses host for local or Foundry-managed hosting."""
    await create_server().run_async()


if __name__ == "__main__":
    asyncio.run(main())