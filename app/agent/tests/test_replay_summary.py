"""Offline checks for the PR replay evidence gate."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("banking_evals", reason="requires the evals environment used by CI")

from banking_evals.replay_summary import main, render_summary  # noqa: E402


@pytest.fixture
def report() -> dict[str, Any]:
    return {
        "sample_size": 1, "model_execution": "real", "foundry_submission": "not_submitted",
        "results": [{
            "case_id": "SMOKE-1", "protocol_passed": True, "final_answer": "synthetic answer",
            "response": {"messages": [{"text": "synthetic answer"}]},
            "replay_failures": {"account": [], "transaction": []},
            "unconsumed_replies": {"account": 0, "transaction": 0},
        }],
    }


def test_complete_report_passes_without_exposing_answer(report: dict[str, Any]) -> None:
    summary, passed = render_summary(report, [{"id": "SMOKE-1"}])
    assert passed
    assert "Behavior review is pending" in summary
    assert "synthetic answer" not in summary


@pytest.mark.parametrize("defect", [
    "empty", "duplicate", "missing", "failure", "error", "fake", "transcript", "unused",
])
def test_incomplete_or_failed_reports_fail(report: dict[str, Any], defect: str) -> None:
    if defect == "empty":
        report["results"] = []
    elif defect == "duplicate":
        report["results"].append(deepcopy(report["results"][0]))
    elif defect == "missing":
        report["results"][0]["case_id"] = "OTHER"
    elif defect == "failure":
        report["results"][0]["protocol_passed"] = False
    elif defect == "error":
        report["results"][0]["error"] = {"message": "must not appear in PR"}
    elif defect == "transcript":
        report["results"][0]["response"] = {}
    elif defect == "unused":
        report["results"][0]["unconsumed_replies"]["account"] = 1
    else:
        report["model_execution"] = "mock"
    summary, passed = render_summary(report, [{"id": "SMOKE-1"}])
    assert not passed
    assert "**FAILED**" in summary
    assert "must not appear in PR" not in summary


def test_empty_dataset_fails(report: dict[str, Any]) -> None:
    assert not render_summary(report, [])[1]


def test_missing_report_writes_failure_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "summary.md"
    monkeypatch.setattr("sys.argv", [
        "replay_summary", "--report", str(tmp_path / "missing.json"),
        "--dataset", str(tmp_path / "cases.json"), "--output", str(output),
    ])
    assert main() == 1
    assert "missing or invalid" in output.read_text()


async def test_empty_dataset_fails_before_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argparse import Namespace
    from banking_evals.mcp.runner import main_async

    dataset = tmp_path / "cases.json"
    dataset.write_text("[]")

    def unexpected_credentials() -> None:
        pytest.fail("An empty dataset must not create Azure credentials")

    monkeypatch.setattr("banking_evals.mcp.runner.AzureCliCredential", unexpected_credentials)
    with pytest.raises(ValueError, match="nonempty"):
        await main_async(Namespace(dataset=dataset, case=None))