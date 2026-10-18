"""Account and transaction workflow for the Foundry Responses host."""

from collections.abc import Sequence

from agent_framework import Agent, BaseChatClient, Message, Workflow, MCPStreamableHTTPTool
from mcp import ClientSession

from app.agents.azure_chat.account_agent import AccountAgent
from app.agents.azure_chat.guardrails import BANKING_GUARDRAILS
from app.agents.azure_chat.transaction_agent import TransactionHistoryAgent
from app.adapters.checkpointed_handoff import CheckpointedHandoffBuilder as HandoffBuilder
from app.adapters.handoff_middleware import HandoffNarrationMiddleware
from app.adapters.no_history_provider import NoHistoryProvider
from app.adapters.refusal_locale_middleware import RefusalLocaleMiddleware
from app.context.user_profile_provider import UserProfileProvider


TRIAGE_INSTRUCTIONS = BANKING_GUARDRAILS + """
You are a banking customer support agent triaging requests about bank accounts and transactions.
Evaluate the whole conversation and hand off to AccountAgent or TransactionHistoryAgent when the
request belongs to one of those areas.

# Triage rules
- For account information such as balances, cards, and beneficiaries, call
  handoff_to_AccountAgent.
- For banking movements, transaction history, transaction disputes, support-case lists,
  case status, timelines, and follow-up questions about a dispute, call
  handoff_to_TransactionHistoryAgent.
- When routing, call the handoff tool without a transfer announcement or other text.
- For payment initiation, invoice processing, or any unrelated request, explain that this
  assistant cannot help with that request.
"""


def _has_completed_agent_response(conversation: Sequence[Message]) -> bool:
    if not conversation:
        return False

    message = conversation[-1]
    return (
        message.role == "assistant"
        and any(
            content.type == "text" and content.text and content.text.strip()
            for content in message.contents
        )
        and not any(content.type == "function_call" for content in message.contents)
    )


def build_hosted_workflow(
    chat_client: BaseChatClient,
    account_mcp_server_url: str,
    transaction_mcp_server_url: str,
    internal_identity_secret: str,
    *,
    account_chat_client: BaseChatClient | None = None,
    transaction_chat_client: BaseChatClient | None = None,
    account_mcp_session: ClientSession | None = None,
    transaction_mcp_session: ClientSession | None = None,
    account_tool: MCPStreamableHTTPTool | None = None,
    transaction_tool: MCPStreamableHTTPTool | None = None,
) -> Workflow:
    """Build the workflow with checkpoints owned by the Responses hosting runtime."""
    triage_agent = Agent(
        client=chat_client,
        instructions=TRIAGE_INSTRUCTIONS.strip(),
        name="triage_agent",
        description="Routes banking requests to the account or transaction specialist.",
        require_per_service_call_history_persistence=True,
        context_providers=[NoHistoryProvider(), UserProfileProvider(internal_identity_secret)],
        middleware=[
            HandoffNarrationMiddleware(),
            RefusalLocaleMiddleware(internal_identity_secret),
        ],
    )
    account_agent = AccountAgent(
        account_chat_client if account_chat_client is not None else chat_client,
        account_mcp_server_url,
        internal_identity_secret,
        account_mcp_session=account_mcp_session,
        account_tool=account_tool,
    ).build_af_agent()
    transaction_agent = TransactionHistoryAgent(
        transaction_chat_client if transaction_chat_client is not None else chat_client,
        account_mcp_server_url,
        transaction_mcp_server_url,
        internal_identity_secret,
        account_mcp_session=account_mcp_session,
        transaction_mcp_session=transaction_mcp_session,
        account_tool=account_tool,
        transaction_tool=transaction_tool,
    ).build_af_agent()

    return (
        HandoffBuilder(
            name="banking_assistant_handoff",
            description="Banking account and transaction support workflow.",
            participants=[triage_agent, account_agent, transaction_agent],
            termination_condition=_has_completed_agent_response,
        )
        .with_start_agent(triage_agent)
        .add_handoff(triage_agent, [account_agent, transaction_agent])
        .build()
    )