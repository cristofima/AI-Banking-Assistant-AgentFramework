"""Constructed consent protocol regressions, independent of freeze approval."""

import json
from copy import deepcopy
from typing import Any

import pytest

from banking_evals.disputes import dataset as replay
from banking_evals.dispute_replay import DATASET, _validate_replies, expand_case, score_case
from banking_evals.evidence import sanitize
from banking_evals.mcp_replay import ReplayReply, ReplayServer, load_contracts
from banking_evals.run_dispute_replay import run_baseline


def test_account_discovery_contract_preserves_optional_nullable_username() -> None:
    contracts = {tool.name: tool.input_schema for tool in load_contracts("account")}
    schema = contracts["getAccountsByUserName"]
    assert "userName" not in schema.get("required", [])
    assert schema["properties"]["userName"]["default"] is None
    assert schema["properties"]["userName"]["anyOf"] == [
        {"type": "string"}, {"type": "null"},
    ]
    assert "product_number" in contracts["getAccountDetails"]["required"]


def expanded_sources() -> list[dict[str, Any]]:
    return [expand_case(source) for source in json.loads(DATASET.read_text(encoding="utf-8"))["cases"]]


@pytest.mark.asyncio
async def test_expanded_sources_follow_consent_contract_without_freeze_override() -> None:
    cases = expanded_sources()
    assert len(cases) == 25
    for case in cases:
        result = await run_baseline(case)
        assert score_case(case, result)["passed"], case["id"]
        tools = [call["tool"] for call in result["tool_calls"]]
        assert "respondToDisputeApproval" not in tools
        if case.get("consent") is True:
            assert tools == ["getLastTransactions", "previewTransactionDispute", "reportTransactionDispute"]
            preview, accepted = result["tool_calls"][1:]
            assert preview["turn"] == 1 and accepted["turn"] == 2
            assert accepted["arguments"] == {"preview_token": preview["result"]["previewToken"]}
            assert accepted["result"]["status"] == "IN_REVIEW"
            assert "SYNTHETIC-PREVIEW-TOKEN" not in " ".join(turn["final_answer"] for turn in result["turns"])
        if case.get("consent") is False:
            assert tools == ["getLastTransactions", "previewTransactionDispute"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Yes, that is the transaction.", "Maybe", "I approve, but no"])
async def test_selection_or_unclear_turn_does_not_create_case(message: str) -> None:
    case = next(case for case in expanded_sources() if case.get("consent") is True)
    case = deepcopy(case)
    case["turns"][2] = message
    case["transaction"] = case["transaction"][:2]
    result = await run_baseline(case)
    assert result["protocol_passed"]
    assert [call["tool"] for call in result["tool_calls"]] == [
        "getLastTransactions", "previewTransactionDispute",
    ]
    assert "SYNTHETIC-PREVIEW-TOKEN" not in result["turns"][2]["final_answer"]


@pytest.mark.asyncio
async def test_scorer_rejects_token_disclosure_and_wrong_preview_binding() -> None:
    case = next(case for case in expanded_sources() if case.get("consent") is True)
    result = await run_baseline(case)
    result["turns"][1]["final_answer"] += " SYNTHETIC-PREVIEW-TOKEN"
    assert "no_preview_token_1" in score_case(case, sanitize(result))["failed_checks"]
    result = await run_baseline(case)
    result["tool_calls"][-1]["arguments"]["preview_token"] = "OTHER-TOKEN"
    assert "consent" in score_case(case, result)["failed_checks"]
    assert "consent" in score_case(case, sanitize(result))["failed_checks"]


@pytest.mark.asyncio
async def test_existing_pending_case_retains_legacy_approval_without_preview() -> None:
    record = {
        "caseId": "CASE-LEGACY", "transactionId": "TX-LEGACY", "reason": "Existing reason",
        "status": "WAITING_USER_APPROVAL", "openedAt": "2026-10-01T12:00:00Z",
        "updatedAt": "2026-10-01T12:00:00Z",
    }
    accepted = {**record, "status": "IN_REVIEW", "triageOutcome": "escalated"}
    case = {
        "id": "constructed-legacy", "locale": "en", "consent": True,
        "turns": ["Show case CASE-LEGACY", "I approve"],
        "transaction": [
            {"tool": "getSupportCase", "arguments": {"case_id": "CASE-LEGACY"}, "result": record, "error": None},
            {"tool": "getSupportCaseTimeline", "arguments": {"case_id": "CASE-LEGACY"}, "result": [], "error": None},
            {"tool": "respondToDisputeApproval", "arguments": {"case_id": "CASE-LEGACY", "approved": True}, "result": accepted, "error": None},
        ], "account": [],
    }
    result = await run_baseline(case)
    assert score_case(case, result)["passed"]
    assert result["tool_calls"][-1]["tool"] == "respondToDisputeApproval"
    result["tool_calls"][0]["result"]["status"] = "IN_REVIEW"
    assert "consent" in score_case(case, result)["failed_checks"]


@pytest.mark.asyncio
async def test_async_consent_tools_are_discovered_and_invoked_through_sdk() -> None:
    MCPStreamableHTTPTool = pytest.importorskip("agent_framework").MCPStreamableHTTPTool
    expected = {
        "previewTransactionDispute": {"transaction_id": "TX", "reason": "Reason"},
        "reportTransactionDispute": {"preview_token": "TOKEN"},
        "recoverTransactionDispute": {"preview_token": "TOKEN"},
    }
    contracts = {tool.name: tool.input_schema for tool in load_contracts("transaction")}
    replay = ReplayServer("transaction", [
        ReplayReply(name, arguments, None if name == "recoverTransactionDispute" else {"synthetic": name})
        for name, arguments in expected.items()
    ])
    async with replay.connect() as mcp_client:
        async with MCPStreamableHTTPTool(
            name="Consent replay", url=None, mcp_client=mcp_client,
            load_prompts=False,
        ) as client:
            for name, arguments in expected.items():
                assert set(contracts[name]["properties"]) == set(arguments)
                assert set(contracts[name]["required"]) == set(arguments)
                function = next(function for function in client.functions if function.name == name)
                await function.invoke(arguments=arguments)
    replay.assert_complete()


def test_preview_and_null_recovery_validate_against_production_models() -> None:
    models = replay._load_transaction_models()
    for case in expanded_sources():
        _validate_replies(case["transaction"], models)
    _validate_replies([{"tool": "recoverTransactionDispute", "result": None}], models)
    preview = next(case for case in expanded_sources() if case.get("consent") is True)["transaction"][1]
    invalid = deepcopy(preview)
    invalid["result"].pop("previewToken")
    with pytest.raises(ValueError):
        _validate_replies([invalid], models)
