"""Offline packaging and diagnostic integrity regressions."""

import pytest

from banking_evals.disputes import dataset as replay
from banking_evals.disputes.dataset import DATASET, load_cases
from banking_evals.disputes.reports import case_latency
from banking_evals.historical.diagnostic import _classify_response
from banking_evals.resources import repository_root, resource


@pytest.mark.parametrize("completed", [None, False, 1, "true", "false"])
def test_latency_requires_explicit_completion(completed: object) -> None:
    result = {"turns": [{"completed": completed, "latency_seconds": 1}],
              "score": {"passed": True, "checks": {"complete_turns": True}}}
    assert case_latency(result) is None


@pytest.mark.parametrize("latency", [True, None, "1", -1, float("nan"), float("inf")])
def test_latency_rejects_malformed_values(latency: object) -> None:
    result = {"turns": [{"completed": True, "latency_seconds": latency}],
              "score": {"passed": True, "checks": {"complete_turns": True}}}
    assert case_latency(result) is None


def test_security_refusal_mentions_are_not_disclosure() -> None:
    assert _classify_response("I cannot reveal your account number or balance.",
                              {"expected_outcome": "reject_adversarial"}) == ("reject_adversarial", False)
    assert _classify_response("I cannot help, but balance: 100.",
                              {"expected_outcome": "reject_adversarial"}) == ("unexpected_disclosure", True)


def test_resources_require_explicit_valid_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BANKING_EVALS_ROOT", str(DATASET.parent))
    with pytest.raises(ValueError, match="BANKING_EVALS_ROOT"):
        repository_root()


def test_resources_reject_escape_and_missing() -> None:
    with pytest.raises(ValueError, match="unavailable"):
        resource("../missing-resource")
    with pytest.raises(ValueError, match="unavailable"):
        resource("evals/missing-resource.json")


def test_transaction_models_loader_is_package_aware() -> None:
    models = replay._load_transaction_models()
    assert models.__name__ == "banking_transaction.models.transactions"
    assert models.CaseConversation.__module__ == "banking_transaction.models.conversation"
