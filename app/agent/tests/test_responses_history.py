"""Offline transport-history bounds remain separate from fresh request input."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from azure.ai.agentserver.responses import ResponseContext
from azure.ai.agentserver.responses.models import ItemMessage, MessageContentInputTextContent
from azure.ai.agentserver.responses.models.runtime import ResponseModeFlags


@pytest.mark.parametrize("history_count", [99, 100, 101])
@pytest.mark.parametrize("prefetched", [False, True])
async def test_history_bound_preserves_input_and_coalesces_reads(
    history_count: int, prefetched: bool,
) -> None:
    ids = [f"message-{index}" for index in range(history_count)][-100:]
    history = [ItemMessage(role="assistant", content=[MessageContentInputTextContent(text=item)]) for item in ids]
    provider = AsyncMock()
    provider.get_history_item_ids.return_value = ids
    provider.get_items.return_value = history
    # Business evidence is validated separately; transport history must not trim fresh input.
    evidence = "x" * 100_000
    fresh = ItemMessage(role="user", content=[MessageContentInputTextContent(text=evidence)])
    context = ResponseContext(
        response_id="synthetic-response",
        mode_flags=ResponseModeFlags(stream=True, store=True, background=False),
        provider=provider, previous_response_id="synthetic-previous", input_items=[fresh],
        history_limit=100, prefetched_history_ids=ids if prefetched else None,
    )

    first, second = await asyncio.gather(context.get_history(), context.get_history())
    inputs = await context.get_input_items()

    assert tuple(history) == first == second
    assert len(first) == min(history_count, 100)
    assert inputs == (fresh,)
    assert inputs[0]["content"][0]["text"] == evidence
    provider.get_items.assert_awaited_once()
    if prefetched:
        provider.get_history_item_ids.assert_not_awaited()
    else:
        provider.get_history_item_ids.assert_awaited_once()
        assert provider.get_history_item_ids.call_args.args == (
            "synthetic-previous", None, 100,
        )
