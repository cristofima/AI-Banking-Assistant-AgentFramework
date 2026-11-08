import pytest
from openai.types.responses import Response, ResponseCreatedEvent, ResponseInProgressEvent

from app.adapters.foundry_chat import WorkflowFoundryChatClient


def _response() -> Response:
    return Response(
        id="resp_1", created_at=1.0, model="m", object="response", output=[],
        parallel_tool_calls=False, tool_choice="auto", tools=[], status="in_progress",
    )


@pytest.fixture
def client() -> WorkflowFoundryChatClient:
    return WorkflowFoundryChatClient(
        project_endpoint="https://example.services.ai.azure.com/api/projects/p",
        model="m",
        credential=object(),
    )


@pytest.mark.parametrize(
    ("event_type", "type_name"),
    [(ResponseCreatedEvent, "response.created"), (ResponseInProgressEvent, "response.in_progress")],
)
def test_streamed_updates_drop_continuation_token_unless_background(
    client: WorkflowFoundryChatClient, event_type: type, type_name: str,
) -> None:
    event = event_type(type=type_name, response=_response(), sequence_number=0)
    assert client._parse_chunk_from_openai(event, {}, {}).continuation_token is None
    assert client._parse_chunk_from_openai(event, {"background": True}, {}).continuation_token is not None
