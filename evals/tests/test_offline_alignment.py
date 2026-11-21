"""Exposed synthetic confirmation checks; no model, services, or credentials."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from banking_evals.disputes import dataset as replay
from banking_evals.disputes.scoring import score_case
from banking_evals import run_dispute_replay as runner
from banking_evals.evidence import (
    available_output, controlled_error, customer_identity, financial_claim,
    register_result, sanitize, unique_output,
)


@pytest.fixture
def cases() -> list[dict[str, Any]]:
    return replay.load_cases()[1]


def report(results: list[dict[str, Any]], system: str = "baseline") -> dict[str, Any]:
    return {"schema_version": 1, "system": system, "model": "synthetic-test-only",
            "dataset_sha256": replay.fingerprint(replay.DATASET),
            "scorer_sha256": replay.fingerprint(replay.ROOT / "evals" / "src" / "banking_evals" / "disputes" / "scoring.py"),
            "expanded_inputs_sha256": replay.expanded_fingerprint(replay.load_cases()[1]),
            "results": results}


@pytest.mark.asyncio
async def test_all_25_cases_are_investigation_only(cases: list[dict[str, Any]]) -> None:
    assert len(cases) == 25
    approved = []
    for case in cases:
        result = await runner.run_baseline(case)
        assert score_case(case, result)["passed"], case["id"]
        for call in result["tool_calls"]:
            if call["tool"] == "reportTransactionDispute":
                approved.append(call["result"])
                assert call["result"]["status"] == "IN_REVIEW"
                assert call["result"].get("resolutionOutcome") is None
                assert not call["result"].get("financialEffectsStatus")
                assert not call["result"].get("cardProtectionStatus")
    assert approved
    assert {value["triageOutcome"] for value in approved} >= {"fast_track", "escalated"}


@pytest.mark.asyncio
async def test_saved_baseline_retains_all_25_scores(
    cases: list[dict[str, Any]], tmp_path: Path,
) -> None:
    results = [await runner.run_baseline(case) for case in cases]
    raw = report(results)
    assert runner.rescore(raw, cases, raw["dataset_sha256"])
    path = tmp_path / "baseline.json"
    assert runner.write_artifacts(raw, path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert sanitize(saved) == saved
    assert runner.rescore(saved, cases, saved["dataset_sha256"])
    assert len(saved["results"]) == 25
    for result in saved["results"]:
        assert result["score"]["passed"], result["case_id"]


@pytest.mark.parametrize("key", ["previewToken", "preview_token"])
def test_sanitize_preserves_only_fixed_synthetic_preview_marker(key: str) -> None:
    assert sanitize({key: "SYNTHETIC-PREVIEW-TOKEN"}) == {
        key: "SYNTHETIC-PREVIEW-TOKEN",
    }
    for value in ["real-capability", "SYNTHETIC-PREVIEW-TOKEN-suffix", None, 42]:
        assert sanitize({key: value}) == {}
    assert sanitize({"token": "SYNTHETIC-PREVIEW-TOKEN"}) == {}
    assert sanitize({"password": "SYNTHETIC-PREVIEW-TOKEN"}) == {}




def test_identity_and_full_redaction() -> None:
    identity = customer_identity("pt")
    assert identity["role"] == "customer"
    assert type(identity["identity_version"]) is int and identity["identity_version"] > 0
    assert identity["locale"] == "pt"
    text = "Complete answer " * 300 + " Bearer eyJabc.def.ghi password=do-not-save"
    clean = sanitize({"answer": text, "password": "hidden", "output_token_count": 4})
    assert len(clean["answer"]) > 4000
    assert "do-not-save" not in str(clean) and "eyJabc" not in str(clean)
    assert clean["output_token_count"] == 4
    error = controlled_error(ExceptionGroup("secret", [RuntimeError("password=secret")]))
    assert error["type"] == "ExceptionGroup"
    assert "secret" not in str(error)


@pytest.mark.parametrize("text,expected", [
    ("Your account has been credited.", True),
    ("Your card has been blocked.", True),
    ("No refund has been posted.", False),
    ("Investigação em análise; nenhum crédito foi lançado.", False),
])
def test_financial_phrase_gate(text: str, expected: bool) -> None:
    assert financial_claim(text) is expected


@pytest.mark.asyncio
async def test_timeout_retains_pending_answer(cases: list[dict[str, Any]]) -> None:
    async def timeout(case: dict[str, Any]) -> dict[str, Any]:
        partial = {"case_id": case["id"], "locale": case["locale"],
                   "turns": [{"turn": 0, "completed": False,
                              "final_answer": "partial " * 400}], "tool_calls": []}
        register_result(partial)
        await asyncio.Event().wait()
        return partial

    result = await runner.execute_case(timeout, cases[0], 0.01)
    assert result["error"]["type"] == "TimeoutError"
    assert not result["turns"][0]["completed"]
    assert len(result["turns"][0]["final_answer"]) > 2000
    assert not score_case(cases[0], result)["passed"]


@pytest.mark.asyncio
async def test_pair_counts_and_failure_latency(cases: list[dict[str, Any]]) -> None:
    baseline = report([await runner.run_baseline(case) for case in cases])
    proposed = deepcopy(baseline)
    proposed["system"] = "proposed"
    proposed["results"][0]["error"] = controlled_error(RuntimeError("private"))
    proposed["results"][0]["turns"][0]["final_answer"] = "RESOLVED"
    comparison = runner.compare_reports(baseline, proposed, cases, replay.fingerprint(replay.DATASET))
    assert comparison["wins"] == 0
    assert comparison["losses"] == 1
    assert comparison["ties"] == 24
    assert comparison["baseline_failures"] == 0
    assert comparison["proposed_failures"] == 1
    assert comparison["cases"][0]["proposed_seconds"] is None
    for field in ("scorer_sha256", "expanded_inputs_sha256"):
        corrupted = deepcopy(proposed)
        corrupted.pop(field)
        with pytest.raises(ValueError):
            runner.compare_reports(baseline, corrupted, cases, replay.fingerprint(replay.DATASET))


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), None])
@pytest.mark.asyncio
async def test_invalid_latency_excluded(value: object, cases: list[dict[str, Any]]) -> None:
    result = await runner.run_baseline(cases[0])
    result["score"] = score_case(cases[0], result)
    result["turns"][0]["latency_seconds"] = value
    assert runner.case_latency(result) is None


def test_collision_safe_output(monkeypatch: pytest.MonkeyPatch) -> None:
    requested = replay.ROOT / "evals" / "results" / "collision.json"
    monkeypatch.setattr(Path, "exists", lambda path: path == requested.with_suffix(".xml"))
    alternative = available_output(requested)
    assert alternative != requested and alternative.parent == requested.parent
    assert unique_output(requested.parent, "run") != unique_output(requested.parent, "run")


@pytest.mark.asyncio
async def test_stream_failure_keeps_partial_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("agent_framework")
    from banking_evals.mcp import runner as mcp

    class Stream:
        async def __aiter__(self) -> Any:
            yield SimpleNamespace(text="partial " * 400,
                                  to_dict=lambda: {"text": "partial " * 400})
            raise RuntimeError("password=private")

    class Agent:
        def run(self, message: str, **kwargs: Any) -> Stream:
            return Stream()

    monkeypatch.setattr(mcp, "build_hosted_workflow", lambda *args, **kwargs:
                        SimpleNamespace(as_agent=lambda **options: Agent()))
    result = await mcp.run_case(None, {"id": "partial", "query": "test",
                                       "expected_behavior": "failure", "locale": "en"})
    assert not result["protocol_passed"]
    assert result["error"]["type"] == "ExceptionGroup"
    cause = result["error"]
    while cause.get("causes"):
        cause = cause["causes"][0]
    assert cause["type"] == "RuntimeError"
    assert "private" not in str(result)
    assert len(result["turns"][0]["final_answer"]) > 2000
    assert not result["turns"][0]["completed"]
    assert result["unconsumed_replies"] == {"account": 0, "transaction": 0}


@pytest.mark.asyncio
async def test_completed_stream_uses_four_field_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    import base64
    pytest.importorskip("agent_framework")
    from banking_evals.mcp import runner as mcp

    captured: list[dict[str, Any]] = []

    def capture(context: Any) -> None:
        payload = context.user_id.split(".")[1]
        captured.append(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))))

    answer = "Complete response " * 300

    class Stream:
        async def __aiter__(self) -> Any:
            yield SimpleNamespace(text=answer, to_dict=lambda: {"text": answer})

        async def get_final_response(self) -> Any:
            return SimpleNamespace(text=answer, to_dict=lambda: {"text": answer})

    class Agent:
        def run(self, message: str, **kwargs: Any) -> Stream:
            return Stream()

    monkeypatch.setattr(mcp, "set_request_context", capture)
    monkeypatch.setattr(mcp, "reset_request_context", lambda token: None)
    monkeypatch.setattr(mcp, "build_hosted_workflow", lambda *args, **kwargs:
                        SimpleNamespace(as_agent=lambda **options: Agent()))
    result = await mcp.run_case(None, {"id": "complete", "query": "test",
                                       "expected_behavior": "completion", "locale": "es"})
    assert result["protocol_passed"]
    assert result["turns"][0]["final_answer"] == answer
    assert set(captured[0]) == {"sub", "customer_id", "email", "locale"}
    assert captured[0]["locale"] == "es"
    assert result["identity_fixture"]["role"] == "customer"
    assert result["identity_fixture"]["identity_version"] == 1


@pytest.mark.asyncio
async def test_scorer_rejects_unsupported_effects(cases: list[dict[str, Any]]) -> None:
    result = await runner.run_baseline(cases[0])
    result["turns"][0]["final_answer"] += " Your card has been blocked."
    assert not score_case(cases[0], result)["passed"]


@pytest.mark.asyncio
async def test_rescore_requires_expansion_and_records_current_scorer(
    cases: list[dict[str, Any]],
) -> None:
    evidence = report([await runner.run_baseline(case) for case in cases])
    evidence["scorer_sha256"] = "older-scorer"
    assert runner.rescore(evidence, cases, replay.fingerprint(replay.DATASET))
    assert evidence["scorer_sha256"] == replay.fingerprint(replay.ROOT / "evals" / "src" / "banking_evals" / "disputes" / "scoring.py")
    evidence.pop("expanded_inputs_sha256")
    with pytest.raises(ValueError, match="Expanded"):
        runner.rescore(evidence, cases, replay.fingerprint(replay.DATASET))


@pytest.mark.asyncio
async def test_failed_or_nested_error_timing_excluded(cases: list[dict[str, Any]]) -> None:
    result = await runner.run_baseline(cases[0])
    result["score"] = score_case(cases[0], result)
    result["turns"][0]["error"] = controlled_error(RuntimeError("private"))
    assert runner.case_latency(result) is None
    result["turns"][0].pop("error")
    result["score"]["passed"] = False
    assert runner.case_latency(result) is None


def test_historical_runner_retains_full_answer_and_continues_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from banking_evals.historical import diagnostic as historical

    answer = "Complete historical evidence " * 300
    calls: list[str] = []

    class Client:
        def __init__(self, base_url: str) -> None:
            pass

        def login(self, email: str, password: str) -> str:
            return "synthetic"

        def send_turn(self, token: str, turn: str, conversation: str | None) -> Any:
            calls.append(turn)
            if turn == "fail":
                raise RuntimeError("private error detail")
            return {"output": [{"type": "message", "content": [
                {"type": "output_text", "text": answer}]}]}, "conversation"

        def close(self) -> None:
            calls.append("closed")

    scenarios = [{"id": str(index), "customer": "synthetic", "category": "diagnostic",
                  "expected_outcome": "clarify", "turns": [turn]}
                 for index, turn in enumerate(("fail", "continue"))]
    monkeypatch.setattr(historical, "BffClient", Client)
    result = historical.run_proposed(scenarios, {"synthetic": {"email": "test@example.invalid"}},
                                     "unused", "synthetic")
    assert calls == ["fail", "continue", "closed"]
    assert result.results[0].actual_outcome == "execution_failed"
    assert result.results[0].latency_seconds is None
    assert not result.results[0].turns[0]["completed"]
    assert "private" not in str(result.results[0].error)
    assert result.results[1].detail == answer
    assert result.results[1].turns[0]["final_answer"] == answer
