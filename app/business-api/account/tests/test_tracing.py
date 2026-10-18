"""W3C context reaches the mounted MCP HTTP boundary."""

from __future__ import annotations

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from banking_account.main import create_app


@pytest.fixture(autouse=True)
def enable_in_memory_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")


def test_mcp_request_preserves_incoming_trace_context(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr("banking_account.main.create_meter_provider", lambda *args: meter_provider)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    monkeypatch.setattr("banking_account.main.create_tracer_provider", lambda *args: provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace_id = "1234567890abcdef1234567890abcdef"
    try:
        with TestClient(create_app()) as client:
            client.get(
                "/mcp/",
                headers={
                    "traceparent": f"00-{trace_id}-1234567890abcdef-01",
                    "tracestate": "bank=opaque",
                },
            )
        spans = [span for span in exporter.get_finished_spans() if span.parent is not None]
        assert spans
        server_span = next(span for span in spans if span.parent.is_remote)
        assert server_span.context.trace_id == int(trace_id, 16)
        assert server_span.context.trace_state.get("bank") == "opaque"
        assert server_span.kind == SpanKind.SERVER
        assert not any(span.kind == SpanKind.INTERNAL for span in spans)
        data = reader.get_metrics_data()
        assert data is not None
        metrics = [
            metric
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        ]
        duration = next(
            metric for metric in metrics
            if metric.name in ("http.server.duration", "http.server.request.duration")
        )
        assert sum(point.count for point in duration.data.data_points) == 1
    finally:
        provider.shutdown()
        meter_provider.shutdown()


@pytest.mark.parametrize("status_code", [200, 500])
def test_http_status_remains_visible_without_asgi_event_spans(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    meter_provider = MeterProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("banking_account.main.create_tracer_provider", lambda *args: provider)
    monkeypatch.setattr("banking_account.main.create_meter_provider", lambda *args: meter_provider)
    app = create_app()

    @app.get("/trace-probe")
    async def trace_probe() -> Response:
        return Response(status_code=status_code)

    try:
        with TestClient(app) as client:
            response = client.get("/trace-probe")
        assert response.status_code == status_code
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.kind == SpanKind.SERVER
        assert span.attributes is not None
        recorded_status = span.attributes.get(
            "http.response.status_code", span.attributes.get("http.status_code")
        )
        assert recorded_status == status_code
        assert span.status.status_code == (
            StatusCode.ERROR if status_code == 500 else StatusCode.UNSET
        )
    finally:
        provider.shutdown()
        meter_provider.shutdown()