"""No-network regressions against the installed Responses telemetry lifecycle."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from azure.ai.agentserver.core import _tracing
from azure.ai.agentserver.responses.hosting import _endpoint_handler as endpoints
from starlette.requests import Request
from starlette.types import Message

from app.telemetry import configure_telemetry_flush


@pytest.fixture
def awaited_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTSERVER_FLUSH_MODE", "background")
    configure_telemetry_flush()
    assert endpoints._resolve_flush_mode("async") == "async"
    monkeypatch.setattr(endpoints, "schedule_flush_spans", Mock(side_effect=AssertionError))
    monkeypatch.setattr(endpoints, "flush_spans", Mock(side_effect=AssertionError))


@pytest.mark.parametrize("configured", [None, "background", "sync", "invalid", ""])
def test_startup_preserves_awaited_flush(
    monkeypatch: pytest.MonkeyPatch, configured: str | None,
) -> None:
    if configured is None:
        monkeypatch.delenv("AGENTSERVER_FLUSH_MODE", raising=False)
    else:
        monkeypatch.setenv("AGENTSERVER_FLUSH_MODE", configured)
    configure_telemetry_flush()
    assert os.environ["AGENTSERVER_FLUSH_MODE"] == "async"


@pytest.mark.parametrize("outcome", ["normal", "source_error", "send_error", "cancel"])
async def test_stream_finalizes_and_awaits_export(
    monkeypatch: pytest.MonkeyPatch, awaited_flush: None, outcome: str,
) -> None:
    events: list[str] = []
    flush_started = asyncio.Event()
    loop = asyncio.get_running_loop()
    flush_future: asyncio.Future[None] = loop.create_future()
    exporter = Mock()
    monkeypatch.setattr(
        _tracing.trace, "get_tracer_provider", lambda: SimpleNamespace(force_flush=exporter),
    )

    def queue_flush(executor: object, callback: object, timeout: int) -> asyncio.Future[None]:
        assert executor is None
        assert callback is _tracing.flush_spans
        assert timeout == 5000
        events.append("flush_started")
        flush_started.set()
        return flush_future

    monkeypatch.setattr(loop, "run_in_executor", queue_flush)

    async def source() -> AsyncGenerator[str, None]:
        try:
            yield "data: synthetic\n\n"
            if outcome == "source_error":
                raise RuntimeError("synthetic source failure")
        finally:
            events.append("source_closed")

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("more_body"):
            if outcome == "send_error":
                raise RuntimeError("synthetic send failure")
            if outcome == "cancel":
                raise asyncio.CancelledError
        if message["type"] == "http.response.body" and not message.get("more_body"):
            events.append("http_completed")

    response = endpoints._CreateStreamingResponse(source(), None, headers={})
    task = asyncio.create_task(response.stream_response(send))
    try:
        await flush_started.wait()
        assert events == ["source_closed", "flush_started"]
        assert not task.done()
        _tracing.flush_spans(5000)
        flush_future.set_result(None)
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome.endswith("error"):
            with pytest.raises(RuntimeError, match="synthetic"):
                await task
        else:
            await task
            assert events == ["source_closed", "flush_started", "http_completed"]
        exporter.assert_called_once_with(5000)
    finally:
        if not flush_future.done():
            flush_future.set_result(None)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_exporter_failure_does_not_break_stream(
    monkeypatch: pytest.MonkeyPatch, awaited_flush: None,
) -> None:
    exporter = Mock(side_effect=RuntimeError("synthetic exporter failure"))
    monkeypatch.setattr(
        _tracing.trace, "get_tracer_provider", lambda: SimpleNamespace(force_flush=exporter),
    )

    async def source() -> AsyncGenerator[str, None]:
        yield "data: synthetic\n\n"

    send = AsyncMock()
    await endpoints._CreateStreamingResponse(source(), None, headers={}).stream_response(send)
    exporter.assert_called_once_with(5000)
    assert send.call_args.args[0] == {"type": "http.response.body", "body": b"", "more_body": False}


async def test_cancellation_waits_for_queued_export(
    monkeypatch: pytest.MonkeyPatch, awaited_flush: None,
) -> None:
    loop = asyncio.get_running_loop()
    queued = asyncio.Event()
    flush_future: asyncio.Future[None] = loop.create_future()

    def queue_flush(executor: object, callback: object, timeout: int) -> asyncio.Future[None]:
        assert executor is None
        assert callback is _tracing.flush_spans
        assert timeout == 5000
        queued.set()
        return flush_future

    monkeypatch.setattr(loop, "run_in_executor", queue_flush)
    task = asyncio.create_task(endpoints._flush_spans_for_mode("async"))
    try:
        await queued.wait()
        task.cancel()
        checkpoint = asyncio.Event()
        loop.call_soon(checkpoint.set)
        await checkpoint.wait()
        assert not task.done()
        assert not flush_future.cancelled()
        flush_future.set_result(None)
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not flush_future.done():
            flush_future.set_result(None)
        await asyncio.gather(task, return_exceptions=True)


async def test_shutdown_finishes_active_stream_and_rejects_new_work(
    monkeypatch: pytest.MonkeyPatch, awaited_flush: None,
) -> None:
    shutdown = asyncio.Event()
    source_started = asyncio.Event()
    source_closed = asyncio.Event()
    flush_started = asyncio.Event()
    release_flush = asyncio.Event()

    async def flush() -> None:
        assert source_closed.is_set()
        flush_started.set()
        await release_flush.wait()

    monkeypatch.setattr(endpoints, "flush_spans_async", flush)

    async def source() -> AsyncGenerator[str, None]:
        try:
            source_started.set()
            yield "data: synthetic\n\n"
            await shutdown.wait()
        finally:
            source_closed.set()

    record = SimpleNamespace(
        response_context=SimpleNamespace(shutdown=shutdown),
        cancel_signal=asyncio.Event(), execution_task=None,
    )
    handler = SimpleNamespace(
        _is_draining=False, _shutdown_requested=asyncio.Event(),
        _runtime_options=SimpleNamespace(resilient_background=False, shutdown_grace_period_seconds=0),
        _runtime_state=SimpleNamespace(
            begin_draining=AsyncMock(return_value=[record]),
            list_records=AsyncMock(return_value=[]),
        ),
        _session_headers=lambda: {},
    )
    send = AsyncMock()
    task = asyncio.create_task(
        endpoints._CreateStreamingResponse(source(), None, headers={}).stream_response(send),
    )
    try:
        await source_started.wait()
        await endpoints._ResponseEndpointHandler.handle_shutdown(handler)
        await flush_started.wait()
        assert handler._is_draining
        assert handler._shutdown_requested.is_set()
        assert record.cancel_signal.is_set()
        assert not task.done()
        request = Request({"type": "http", "method": "POST", "path": "/responses", "headers": []})
        rejected = await endpoints._ResponseEndpointHandler.handle_create(handler, request)
        assert rejected.status_code == 503
        release_flush.set()
        await task
        assert send.call_args.args[0]["more_body"] is False
    finally:
        shutdown.set()
        release_flush.set()
        await asyncio.gather(task, return_exceptions=True)
