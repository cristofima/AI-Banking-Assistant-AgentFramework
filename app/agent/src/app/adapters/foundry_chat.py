"""Foundry chat client compatible with native workflow hosting."""

from typing import Any

from agent_framework import ChatResponseUpdate
from agent_framework.foundry import FoundryChatClient


class WorkflowFoundryChatClient(FoundryChatClient):
    """Drops provider continuation tokens from streamed updates unless background mode is requested.

    The OpenAI Responses parser marks every in-progress streamed update with a continuation token,
    and native workflow hosting rejects any workflow output carrying one as unfinished
    background output. This workflow never requests background execution.
    """

    def _parse_chunk_from_openai(
        self,
        event: Any,
        options: dict[str, Any],
        function_call_ids: dict[int, tuple[str, str]],
        seen_reasoning_delta_item_ids: set[str] | None = None,
    ) -> ChatResponseUpdate:
        update = super()._parse_chunk_from_openai(
            event, options, function_call_ids, seen_reasoning_delta_item_ids,
        )
        if not options.get("background"):
            update.continuation_token = None
        return update
