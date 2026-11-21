"""Shared test isolation for the agent suite."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_agentserver_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Responses host state out of ``~/.agentserver`` and away from concurrent runs."""
    monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(tmp_path / "agentserver"))
