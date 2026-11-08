"""Map one Responses request to a native workflow turn."""

from agent_framework_foundry_hosting import (
    HostedResponseRequest,
    WorkflowTurn,
    response_input_messages,
)

_REPLY_ITEM_TYPES = frozenset({"mcp_approval_response", "function_call_output"})


async def parse_response(request: HostedResponseRequest) -> WorkflowTurn:
    """Resume a pending approval from replies, otherwise start from this turn's messages."""
    items = await request.get_input_items()
    if items and all(item.get("type") in _REPLY_ITEM_TYPES for item in items):
        return WorkflowTurn(responses=await request.get_workflow_responses())
    return WorkflowTurn(input=await response_input_messages(request))
