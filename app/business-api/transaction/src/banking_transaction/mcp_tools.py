from typing import Any

from starlette.concurrency import run_in_threadpool
from fastmcp import FastMCP
from fastmcp.server.dependencies import CurrentHeaders
import logging
from banking_transaction.services.disputes import support_case_service_singleton as dispute_service
from banking_transaction.auth.internal_identity import get_customer_id
from banking_transaction.services.transactions import transaction_service_singleton as service

logger = logging.getLogger(__name__)
mcp = FastMCP("Transaction MCP Server")


@mcp.tool(name="getTransactionsByRecipientName", description="Get transactions by recipient name")
def get_transactions_by_recipient_name(
    product_number: str,
    recipientName: str,
    headers: dict[str, str] = CurrentHeaders(),
):
    return service.get_transactions_by_recipient_name(
        product_number,
        recipientName,
        get_customer_id(headers),
    )

@mcp.tool(name="getCardTransactions", description="Get credit and debit card transactions")
def get_card_transactions(
    product_number: str,
    card_product_number: str,
    headers: dict[str, str] = CurrentHeaders(),
):
    return service.get_transactions_by_type(
        account_id=product_number,
        customer_id=get_customer_id(headers),
        card_id=card_product_number,
    )


@mcp.tool(name="getLastTransactions", description="Get the last transactions for an account")
def get_last_transactions(
    product_number: str,
    headers: dict[str, str] = CurrentHeaders(),
):
    return service.get_transactions(product_number, get_customer_id(headers))


@mcp.tool(
    name="getTransactionRecognitionContext",
    description=(
        "Read up to three strictly earlier matching Approved purchases on the same owned "
        "card and currency within 180 days of the selected charge. Merchant matches are "
        "trimmed case-insensitive exact matches and amounts match exactly. Loaded history "
        "coverage is unverified. No matches never means first-ever purchase. This creates "
        "no case or events; continue reporting immediately if the customer wishes."
    ),
)
async def get_transaction_recognition_context(
    transaction_id: str,
    headers: dict[str, str] = CurrentHeaders(),
) -> dict[str, Any]:
    return await run_in_threadpool(
        service.get_transaction_recognition_context,
        transaction_id, await run_in_threadpool(get_customer_id, headers),
    )


@mcp.tool(
    name="previewTransactionDispute",
    description=(
        "Read eligible owned transaction context and prepare a ten-minute consent proposal. "
        "This creates no case or audit event. Present the transaction and reason, then ask "
        "explicit permission to create a case and send it for review. Never show previewToken."
    ),
)
async def preview_transaction_dispute(
    transaction_id: str,
    reason: str,
    headers: dict[str, str] = CurrentHeaders(),
) -> dict[str, Any]:
    preview = await run_in_threadpool(
        dispute_service.preview_transaction_dispute,
        transaction_id, await run_in_threadpool(get_customer_id, headers), reason,
    )
    return preview.model_dump(mode="json")


@mcp.tool(
    name="reportTransactionDispute",
    description=(
        "Accept the exact previewToken only after explicit customer consent to create that "
        "case and send it for review. Atomically persists the case and consent in IN_REVIEW. "
        "Retry only the same token after an ambiguous result, within 24 hours. Never decide "
        "legitimacy or claim approval itself refunds, credits, changes balances or blocks cards."
    ),
)
async def report_transaction_dispute(
    preview_token: str,
    headers: dict[str, str] = CurrentHeaders(),
) -> dict[str, Any]:
    case = await run_in_threadpool(
        dispute_service.accept_transaction_dispute, preview_token,
        await run_in_threadpool(get_customer_id, headers, mutation=True),
    )
    return case.model_dump(mode="json")


@mcp.tool(
    name="recoverTransactionDispute",
    description=(
        "Read the confirmed case for the same previewToken after an ambiguous acceptance. "
        "Creates no case. Returns null if not accepted; recovery expires after 24 hours. "
        "Never show the token or replace it with a fresh proposal to retry an uncertain result."
    ),
)
async def recover_transaction_dispute(
    preview_token: str,
    headers: dict[str, str] = CurrentHeaders(),
) -> dict[str, Any] | None:
    case = await run_in_threadpool(
        dispute_service.recover_transaction_dispute, preview_token,
        await run_in_threadpool(get_customer_id, headers),
    )
    return case.model_dump(mode="json") if case is not None else None


@mcp.tool(
    name="respondToDisputeApproval",
    description=(
        "Record the customer's approval or decline for a dispute case awaiting approval. "
        "Only call this after the customer has explicitly confirmed or declined. Approval "
        "continues persisted case processing, not a human investigation or verdict. Legacy "
        "outcome codes do not establish credit, refund, balance, or card-protection effects."
    ),
)
def respond_to_dispute_approval(
    case_id: str,
    approved: bool,
    headers: dict[str, str] = CurrentHeaders(),
):
    return dispute_service.respond_to_approval(
        case_id, get_customer_id(headers, mutation=True), approved,
    )


@mcp.tool(name="listSupportCases", description="List the customer's transaction-dispute support cases")
def list_support_cases(
    headers: dict[str, str] = CurrentHeaders(),
):
    return dispute_service.list_cases(get_customer_id(headers))


@mcp.tool(name="getSupportCase", description="Get a single transaction-dispute support case by ID")
def get_support_case(
    case_id: str,
    headers: dict[str, str] = CurrentHeaders(),
):
    return dispute_service.get_case(case_id, get_customer_id(headers))


@mcp.tool(
    name="getSupportCaseTimeline",
    description=(
        "Get the event timeline for a persisted transaction-dispute support case. Prefer "
        "displayMessage for user-facing text; message preserves original audit text. "
        "Historical outcome words do not prove financial effects or a human verdict."
    ),
)
def get_support_case_timeline(
    case_id: str,
    headers: dict[str, str] = CurrentHeaders(),
):
    return dispute_service.get_case_timeline(case_id, get_customer_id(headers))
