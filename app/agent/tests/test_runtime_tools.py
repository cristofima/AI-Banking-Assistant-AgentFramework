"""Offline runtime routing checks without Azure or banking calls."""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters import runtime_tools


@pytest.mark.parametrize("runtime", ["local", "invalid", "", "FOUNDRY"])
def test_invalid_runtime_is_rejected(runtime: str) -> None:
    with pytest.raises(ValueError, match="AGENT_RUNTIME"):
        runtime_tools.build_toolbox_tools(runtime, "https://example.test", None, "secret")


@pytest.mark.parametrize("runtime", ["foundry"])
def test_runtime_requires_project_endpoint(runtime: str) -> None:
    with pytest.raises(ValueError, match="FOUNDRY_PROJECT_ENDPOINT"):
        runtime_tools.build_toolbox_tools(runtime, "", None, "secret")


@pytest.mark.parametrize("runtime", ["foundry"])
def test_runtime_uses_only_scoped_toolbox_endpoints(
    runtime: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def toolbox(credential: object, **kwargs: Any) -> object:
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(runtime_tools, "FoundryToolbox", toolbox)
    monkeypatch.setattr(
        runtime_tools, "mcp_header_provider",
        lambda secret: lambda kwargs: {"Authorization": "synthetic-bearer"},
    )
    tools = runtime_tools.build_toolbox_tools(
        runtime, "https://example.test/api/projects/bank/", None, "secret",
    )
    assert tools is not None
    assert len(calls) == 2
    for call, service in zip(calls, ["account", "transaction"], strict=True):
        assert call["url"] == (
            "https://example.test/api/projects/bank/toolboxes/banking-tools/mcp?api-version=v1"
        )
        assert all(name.startswith(f"{service}___") for name in call["allowed_tools"])
        assert call["header_provider"]({}) == {
            "x-banking-mcp-authorization": "synthetic-bearer",
        }
