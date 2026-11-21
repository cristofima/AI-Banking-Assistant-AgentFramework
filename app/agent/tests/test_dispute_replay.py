"""Offline comparator and evaluator regressions, not measured model quality."""

from copy import deepcopy

import pytest

pytest.importorskip("banking_evals", reason="requires the evals environment used by CI")

from banking_evals.dispute_replay import (  # noqa: E402
    DATASET, ROOT, expanded_fingerprint, fingerprint, load_cases, score_case,
)
from banking_evals.run_dispute_replay import (  # noqa: E402
    case_latency, compare_reports, execute_case, rescore, run_baseline, sanitize,
)


async def test_baseline_executes_all_cases_and_maps_original_ids() -> None:
    _, cases = load_cases()
    assert len(cases) == 25
    assert len({source for case in cases for source in case["source_ids"]}) == 18
    for case in cases:
        result = await run_baseline(case)
        assert score_case(case, result)["passed"], case["id"]


async def test_baseline_does_not_consume_labels_or_expected_outcomes() -> None:
    _, cases = load_cases()
    case = cases[0]
    changed = deepcopy(case)
    for key in ("outcome", "expected_behavior", "family", "consent", "source_ids"):
        changed.pop(key, None)
    original = await run_baseline(case)
    altered = await run_baseline(changed)
    assert altered["tool_calls"] == original["tool_calls"]
    assert [turn["final_answer"] for turn in altered["turns"]] == [
        turn["final_answer"] for turn in original["turns"]]


async def test_early_approval_and_decline_mismatch_fail_scoring() -> None:
    _, cases = load_cases()
    case = cases[0]
    result = await run_baseline(case)
    result["tool_calls"][-1]["turn"] = 0
    assert "consent" in score_case(case, result)["failed_checks"]
    result["tool_calls"][-1]["turn"] = 2
    result["tool_calls"][-1]["arguments"]["preview_token"] = "OTHER-PREVIEW"
    assert not score_case(case, result)["passed"]


async def test_missing_turn_and_ungrounded_status_fail_scoring() -> None:
    _, cases = load_cases()
    case = cases[0]
    result = await run_baseline(case)
    result["turns"][0]["final_answer"] = "RESOLVED"
    assert "status_grounding_0" in score_case(case, result)["failed_checks"]
    result["turns"].pop()
    assert "complete_turns" in score_case(case, result)["failed_checks"]


async def test_rescore_rejects_incomplete_duplicate_and_changed_dataset() -> None:
    _, cases = load_cases()
    report = {"schema_version": 1, "system": "baseline",
              "dataset_sha256": fingerprint(DATASET), "results": [await run_baseline(cases[0])]}
    with pytest.raises(ValueError, match="Missing"):
        rescore(report, cases, fingerprint(DATASET))
    with pytest.raises(ValueError, match="hash"):
        rescore(report, cases, "changed")


def test_sanitizer_removes_nested_credentials_but_preserves_usage() -> None:
    result = sanitize({"authorization": "secret", "nested": [
        {"password": "secret", "text": "Bearer secret eyJabc.def.ghi v1.abc." + "a" * 64,
         "input_token_count": 12, "output_token_count": 3}],
    })
    assert "secret" not in str(result)
    assert "eyJabc" not in str(result)
    assert result["nested"][0]["input_token_count"] == 12


@pytest.mark.parametrize("field,value", [
    ("server", "account"), ("result", {"status": "RESOLVED"}),
    ("error", "invented denial"), ("turn", -1),
])
async def test_scorer_rejects_corrupted_tool_evidence(field: str, value: object) -> None:
    _, cases = load_cases()
    result = await run_baseline(cases[0])
    result["tool_calls"][0][field] = value
    assert not score_case(cases[0], result)["passed"]


@pytest.mark.parametrize("case_id,translation", [
    ("D-HR-1", "derivado a revisión"), ("D-WP-2", "encaminhado para análise"),
])
async def test_escalation_relay_translates_human_text_and_preserves_codes(
    case_id: str, translation: str,
) -> None:
    _, cases = load_cases()
    case = next(case for case in cases if case["id"] == case_id)
    result = await run_baseline(case)
    answer = result["turns"][-1]["final_answer"]
    assert translation in answer
    assert "escalated" not in answer
    assert "IN_REVIEW" in answer
    assert case["transaction"][-1]["result"]["status"] == "IN_REVIEW"
    assert score_case(case, result)["passed"]


async def test_rescore_rejects_duplicate_cases_and_locale_tampering() -> None:
    _, cases = load_cases()
    results = [await run_baseline(case) for case in cases]
    report = {"schema_version": 1, "system": "baseline",
              "dataset_sha256": fingerprint(DATASET), "results": results}
    results[-1] = deepcopy(results[0])
    with pytest.raises(ValueError, match="duplicated"):
        rescore(report, cases, fingerprint(DATASET))
    results[-1] = await run_baseline(cases[-1])
    results[0]["locale"] = "en"
    with pytest.raises(ValueError, match="locale"):
        rescore(report, cases, fingerprint(DATASET))


async def test_execution_failure_preserves_case_identity_without_raw_error() -> None:
    async def fail(case: dict) -> dict:
        raise RuntimeError("Bearer sensitive-value")

    _, cases = load_cases()
    result = await execute_case(fail, cases[0], 1)
    assert result["case_id"] == cases[0]["id"]
    assert result["error"]["type"] == "RuntimeError"
    assert "sensitive-value" not in str(result)
    assert not score_case(cases[0], result)["passed"]


async def test_paired_comparison_rescores_evidence_and_rejects_wrong_system() -> None:
    _, cases = load_cases()
    baseline = {
        "schema_version": 1, "system": "baseline", "dataset_sha256": fingerprint(DATASET),
        "scorer_sha256": fingerprint(ROOT / "evals" / "src" / "banking_evals" / "disputes" / "scoring.py"),
        "expanded_inputs_sha256": expanded_fingerprint(cases),
        "results": [await run_baseline(case) for case in cases],
    }
    proposed = deepcopy(baseline)
    proposed.update(system="proposed", model="synthetic-test-only")
    proposed["results"][0]["turns"][0]["final_answer"] = "RESOLVED"
    comparison = compare_reports(baseline, proposed, cases, fingerprint(DATASET))
    assert comparison["regressions"] == 1
    assert comparison["improvements"] == 0
    assert len(comparison["cases"]) == 25
    for field, message in (
        ("scorer_sha256", "current identical scorer fingerprint"),
        ("expanded_inputs_sha256", "identical frozen expanded inputs"),
    ):
        for value in (None, "outdated"):
            incompatible = deepcopy(proposed)
            if value is None:
                incompatible.pop(field)
            else:
                incompatible[field] = value
            with pytest.raises(ValueError, match=message):
                compare_reports(baseline, incompatible, cases, fingerprint(DATASET))
    with pytest.raises(ValueError, match="requires"):
        compare_reports(baseline, baseline, cases, fingerprint(DATASET))


@pytest.mark.parametrize("timing", [None, -1, float("nan"), float("inf"), True])
async def test_case_latency_excludes_missing_and_invalid_timing(timing: object) -> None:
    _, cases = load_cases()
    result = await run_baseline(cases[0])
    result["score"] = score_case(cases[0], result)
    assert case_latency(result) is not None
    result["turns"][0]["latency_seconds"] = timing
    assert case_latency(result) is None
    result["turns"] = []
    result["score"] = score_case(cases[0], result)
    assert case_latency(result) is None