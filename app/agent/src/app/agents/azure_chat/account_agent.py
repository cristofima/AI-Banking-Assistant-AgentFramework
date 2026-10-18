import logging

from agent_framework import Agent, BaseChatClient, MCPStreamableHTTPTool
from mcp import ClientSession

from app.agents.azure_chat.guardrails import BANKING_GUARDRAILS
from app.common.internal_identity import mcp_header_provider
from app.adapters.tool_error_middleware import OwnershipErrorMiddleware
from app.adapters.refusal_locale_middleware import RefusalLocaleMiddleware
from app.context.user_profile_provider import UserProfileProvider


logger = logging.getLogger(__name__)

class AccountAgent :
    instructions = BANKING_GUARDRAILS + """
    you are a personal financial advisor who help the user to retrieve information about their bank accounts.
    Always use markdown to format your response.
    Use the authenticated customer context for all account information.
    For a balance or detail inquiry with a full product number, call getAccountDetails
    directly with that number. Do not first call getAccountsByUserName: account listing
    is for an explicit list request or discovery when no full product number is supplied.
    Never infer ownership from the number's wording; rely on the tool's ownership check.
    Resource lookup tools accept product_number, never a database product id.
    Use the full product number supplied by the user for lookup. If only a masked number
    is available, ask for the full number; never reconstruct it or submit masked digits.
    In account lists and details, identify accounts using the tool's accountNumber exactly as returned.
    It is the full bank account number, not the internal id, so the value itself is never
    translated; the column or field label naming it is ordinary UI text and follows the
    response-language rule like any other heading.
    Never substitute ordinal labels such as "Account 1" or present id as an account number.
    If accountNumber is missing, state that the account number is unavailable; do not invent one.
    Bank account numbers may be shown in full. Credit and debit card numbers must remain masked.
    Never reconstruct or disclose masked card digits, including card numbers supplied by the user.
    If a tool reports that a requested account does not belong to the authenticated customer, stop.
    State that the account is unavailable without calling another tool, listing other accounts, or
    disclosing any account identifiers, balances, or details.
    """
    name = "AccountAgent"
    description = "This agent manages user accounts related information such as balance, credit cards."

    def __init__(
        self,
        azure_chat_client: BaseChatClient,
        account_mcp_server_url: str,
        internal_identity_secret: str,
        *,
        account_mcp_session: ClientSession | None = None,
        account_tool: MCPStreamableHTTPTool | None = None,
    ) -> None:
        self.azure_chat_client = azure_chat_client
        self.account_mcp_server_url = account_mcp_server_url
        self.internal_identity_secret = internal_identity_secret
        self.account_mcp_session = account_mcp_session
        self.account_tool = account_tool



    def build_af_agent(self) -> Agent:

      logger.info("Initializing Account Agent connection for account api ")

      logger.info("Initializing Account MCP server tools for AccountAgent ")
      account_mcp_server = self.account_tool if self.account_tool is not None else MCPStreamableHTTPTool(
                name="Account MCP server client",
                url=self.account_mcp_server_url,
                session=self.account_mcp_session,
                header_provider=(mcp_header_provider(self.internal_identity_secret)
                                 if self.account_mcp_session is None else None),
      )

      return Agent(
                client=self.azure_chat_client,
                instructions=AccountAgent.instructions.strip(),
                name=AccountAgent.name,
                require_per_service_call_history_persistence=True,
                tools=[account_mcp_server],
                middleware=[
                    OwnershipErrorMiddleware(),
                    RefusalLocaleMiddleware(self.internal_identity_secret),
                ],
                context_providers=[UserProfileProvider(self.internal_identity_secret)]
            )
