"""Offline regressions for the local task output adapter."""
from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
from datetime import datetime as RealDatetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture
def runner() -> ModuleType:
    path = Path(__file__).with_name("run-local-service.py")
    spec = importlib.util.spec_from_file_location("local_service_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_console_colors_severity_only(runner: ModuleType, level: str) -> None:
    assert runner.console_line(f"{level}: message\n", color=True) == (
        f"\x1b[{runner.COLORS[level]}m{level}\x1b[0m: message\n"
    )


def test_console_plain_output_removes_ansi(runner: ModuleType) -> None:
    assert runner.console_line("\x1b[32mINFO\x1b[0m: ready\n", color=False) == "INFO: ready\n"


def test_files_append_and_change_at_midnight(runner: ModuleType, tmp_path: Path) -> None:
    class Clock:
        current = RealDatetime(2026, 10, 18, 23, 59)

        @classmethod
        def now(cls) -> RealDatetime:
            return cls.current

    runner.datetime = Clock
    runner.write_line(tmp_path, "identity", "\x1b[32mINFO\x1b[0m first\n")
    runner.write_line(tmp_path, "identity", "second\n")
    Clock.current = RealDatetime(2026, 10, 19)
    runner.write_line(tmp_path, "identity", "third\n")
    assert (tmp_path / "logs" / "identity-2026-10-18.log").read_text() == "INFO first\nsecond\n"
    assert (tmp_path / "logs" / "identity-2026-10-19.log").read_text() == "third\n"


def test_runner_captures_output_and_nonzero_exit(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner.ROOT = tmp_path
    runner.SERVICES = {"test": (".", ["-c", "import sys; print('INFO: ready'); sys.exit(7)"])}
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert runner.run_service("test") == 7
    assert capsys.readouterr().out == "\x1b[32mINFO\x1b[0m: ready\n"
    files = list((tmp_path / "logs").glob("test-*.log"))
    assert len(files) == 1
    assert files[0].read_text() == "INFO: ready\n"


def test_runner_handles_unicode_with_legacy_windows_stdout(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner.ROOT = tmp_path
    runner.SERVICES = {
        "test": (".", ["-c", "print('  \\u279c Local: https://localhost:5170/')"]),
    }
    monkeypatch.setenv("NO_COLOR", "1")
    with io.BytesIO() as buffer:
        with io.TextIOWrapper(buffer, encoding="cp1252") as stdout:
            monkeypatch.setattr(runner.sys, "stdout", stdout)
            assert runner.run_service("test") == 0
            stdout.flush()
            expected = "  ➜ Local: https://localhost:5170/\n"
            assert buffer.getvalue().decode("utf-8").replace("\r\n", "\n") == expected
    filename = next((tmp_path / "logs").glob("test-*.log"))
    assert filename.read_text(encoding="utf-8") == expected


def test_cancellation_terminates_child_after_grace_period(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Child:
        stdout: Any = None
        terminated = False

        def __enter__(self) -> Child:
            self.stdout = self
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self) -> Child:
            return self

        def __next__(self) -> str:
            raise KeyboardInterrupt

        def wait(self, timeout: int | None = None) -> int:
            if timeout is not None:
                raise subprocess.TimeoutExpired("test", timeout)
            return 1

        def terminate(self) -> None:
            self.terminated = True

        def poll(self) -> int | None:
            return 1 if self.terminated else None

    child = Child()
    runner.ROOT = tmp_path
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: child)
    assert runner.run_service("account") == 1
    assert child.terminated


def test_credentials_are_redacted_from_console_and_file(
    runner: ModuleType, tmp_path: Path,
) -> None:
    line = 'GET /callback?code=secret&state=private Bearer abc.def.ghi eyJabc.eyJdef.signature\n'
    expected = 'GET /callback?code=[REDACTED]&state=[REDACTED] Bearer [REDACTED] [REDACTED]\n'
    assert runner.console_line(line, color=False) == expected
    runner.write_line(tmp_path, "identity", line)
    assert next((tmp_path / "logs").glob("*.log")).read_text() == expected


def test_backend_tasks_load_project_environment_with_local_topology_overrides() -> None:
    directory = Path(__file__).parent
    tasks = json.loads((directory / "tasks.json").read_text())["tasks"]
    backend_labels = {
        "run identity HTTPS dev",
        "run account mcp dev",
        "run transaction mcp dev",
        "run responses agent dev",
        "run responses bff dev",
    }
    backends = [task for task in tasks if task["label"] in backend_labels]
    assert len(backends) == 5
    for task in backends:
        assert "uv run --env-file .env python -u" in task["command"]
        environment = task["options"].get("env", {})
        expected = {} if task["label"] == "run identity HTTPS dev" else {
            "SSL_CERT_FILE": "${env:USERPROFILE}\\.banking-oauth-local\\localhost-ca-bundle.pem",
        }
        if task["label"] in {"run account mcp dev", "run transaction mcp dev"}:
            expected["PROFILE"] = "dev"
        if task["label"] == "run responses bff dev":
            expected.update({
                "PROFILE": "dev",
                "RESPONSES_UPSTREAM_MODE": "local",
                "RESPONSES_AGENT_ENDPOINT": "http://127.0.0.1:8088/responses",
            })
        if task["label"] == "run responses agent dev":
            expected.update({
                "AGENT_RUNTIME": "local",
                "ENABLE_SENSITIVE_DATA": "true",
                "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "true",
            })
        assert environment == expected
        assert task["problemMatcher"]["background"]["endsPattern"]


@pytest.mark.parametrize("service,port", [("account", 8070), ("transaction", 8071)])
def test_mcp_local_profile_matches_ordered_task_readiness(service: str, port: int) -> None:
    directory = Path(__file__).parent
    tasks = json.loads((directory / "tasks.json").read_text())["tasks"]
    task = next(task for task in tasks if task["label"] == f"run {service} mcp dev")
    profile = task["options"]["env"]["PROFILE"]
    assert profile == "dev"
    background = task["problemMatcher"]["background"]
    assert re.search(
        background["beginsPattern"],
        f"Starting {service} service server with profile: {profile}, port: {port}",
    )
    assert re.search(background["endsPattern"], f"Uvicorn running on http://0.0.0.0:{port}")
    assert not re.search(background["endsPattern"], "Uvicorn running on http://0.0.0.0:8080")


def test_task_only_startup_keeps_six_ordered_services() -> None:
    directory = Path(__file__).parent
    tasks = json.loads((directory / "tasks.json").read_text())
    startup = next(task for task in tasks["tasks"] if task["label"] == "prepare full stack dev")
    assert startup["group"] == {"kind": "build", "isDefault": True}
    assert startup["dependsOrder"] == "sequence"
    assert len(startup["dependsOn"]) == 6
    frontend = next(task for task in tasks["tasks"] if task["label"] == "run frontend dev")
    assert frontend["command"] == "rtk proxy npm run dev -- --mode oauth-local"
    launch = (directory / "launch.json").read_text()
    assert '"name": "DEV - Full Stack Ordered"' in launch
    assert '"noDebug": true' in launch
    assert '"preLaunchTask": "prepare full stack dev"' in launch
    assert '"internalConsoleOptions": "neverOpen"' in launch
    assert "pwa-chrome" not in launch
