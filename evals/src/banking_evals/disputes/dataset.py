"""Frozen synthetic service replies and independent structured dispute checks."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from banking_evals.resources import repository_root

ROOT = repository_root()
DATASET = ROOT / "evals/dispute_cases.json"
EXPANSION_VERSION = "dispute-consent-v3"
TRANSACTION_PACKAGE_NAME = "banking_transaction"
TRANSACTION_MODULE_NAME = f"{TRANSACTION_PACKAGE_NAME}.models.transactions"
TRANSACTION_PACKAGE_ROOT = ROOT / "app/business-api/transaction/src/banking_transaction"


DISPLAY_VALUES = {
    "WAITING_USER_APPROVAL": ("awaiting approval", "pendiente de aprobación", "aguardando aprovação"),
    "IN_REVIEW": ("in review", "en revisión", "em análise"),
    "RESOLVED": ("resolved", "resuelto", "resolvido"),
    "fast_track": ("fast-track review", "revisión prioritaria", "análise prioritária"),
    "escalated": ("escalated", "derivado a revisión", "encaminhado para análise"),
    "insufficient_signal": ("insufficient signal", "información insuficiente", "informações insuficientes"),
    "withdrawn_by_customer": ("withdrawn by customer", "retirado por el cliente", "retirado pelo cliente"),
    "transaction_alerts": ("transaction alerts", "alertas de transacciones", "alertas de transações"),
}


def display_value(value: str, locale: str) -> str:
    """Deterministic comparator wording, not a semantic or locale evaluator."""
    return DISPLAY_VALUES.get(value, (value, value, value))[("en", "es", "pt").index(locale)]


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dependency_fingerprint(path: Path) -> str:
    """Hash source consistently across Windows checkout and Linux CI line endings."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _load_transaction_models() -> ModuleType:
    try:
        return importlib.import_module(TRANSACTION_MODULE_NAME)
    except ModuleNotFoundError as exc:
        if exc.name != TRANSACTION_PACKAGE_NAME:
            raise
    package_spec = importlib.util.spec_from_file_location(
        TRANSACTION_PACKAGE_NAME,
        TRANSACTION_PACKAGE_ROOT / "__init__.py",
        submodule_search_locations=[str(TRANSACTION_PACKAGE_ROOT)],
    )
    if package_spec is None or package_spec.loader is None:
        raise ValueError("Missing response models")
    package = importlib.util.module_from_spec(package_spec)
    sys.modules[TRANSACTION_PACKAGE_NAME] = package
    package_spec.loader.exec_module(package)
    return importlib.import_module(TRANSACTION_MODULE_NAME)


def load_cases(path: Path = DATASET) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata = json.loads(path.read_text(encoding="utf-8"))
    cases = metadata["cases"]
    if metadata["version"] != "dispute-replay-v2" or not cases:
        raise ValueError("Unsupported or empty dispute dataset")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Duplicate dispute IDs")
    models = _load_transaction_models()
    expanded = []
    for source in cases:
        if source["locale"] not in {"en", "es", "pt"} or not source["turns"]:
            raise ValueError("Invalid locale or missing turns")
        case = expand_case(source)
        _validate_replies(case["transaction"], models)
        expanded.append(case)
    freeze_path = path.with_name("dispute_freeze.json")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if (freeze.get("expansion_version") != EXPANSION_VERSION
            or freeze["dataset_version"] != metadata["version"]
            or freeze["dataset_sha256"] != fingerprint(path)
            or freeze["expanded_inputs_sha256"] != expanded_fingerprint(expanded)):
        raise ValueError("Frozen dataset hash differs; version inputs explicitly before running")
    if freeze["case_count"] != len(expanded):
        raise ValueError("Frozen case count differs")
    if not required_dependencies().issubset(freeze["dependencies"]):
        raise ValueError("Frozen dependency coverage is incomplete; review and version the freeze")
    for dependency, expected_hash in freeze["dependencies"].items():
        dependency_path = (ROOT / dependency).resolve()
        if (not dependency_path.is_relative_to(ROOT.resolve()) or not dependency_path.is_file()
                or dependency_fingerprint(dependency_path) != expected_hash):
            raise ValueError("Frozen dependency hash differs; review and version the freeze")
    return metadata, expanded


def required_dependencies() -> set[str]:
    """Cover replay and workflow sources conservatively, including local import seams."""
    paths = {
        path.relative_to(ROOT).as_posix()
        for directory in (ROOT / "evals/src/banking_evals", ROOT / "app/agent/src/app")
        for path in directory.rglob("*.py")
    }
    paths.update({
        "app/agent/main.py",
        "app/business-api/transaction/src/banking_transaction/__init__.py",
        "app/business-api/transaction/src/banking_transaction/models/__init__.py",
        "app/business-api/transaction/src/banking_transaction/models/conversation.py",
        "app/business-api/transaction/src/banking_transaction/models/transactions.py",
        "app/business-api/transaction/src/banking_transaction/mcp_tools.py",
        "app/business-api/account/src/banking_account/mcp_tools.py",
    })
    return paths


def expanded_fingerprint(cases: list[dict[str, Any]]) -> str:
    encoded = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_replies(replies: list[dict[str, Any]], models: ModuleType) -> None:
    validators = {
        "previewTransactionDispute": models.DisputePreview,
        "recoverTransactionDispute": models.DisputeCase,
        "reportTransactionDispute": models.DisputeCase,
        "respondToDisputeApproval": models.DisputeCase,
        "getSupportCase": models.DisputeCase,
        "getSupportCaseTimeline": models.DisputeCaseEvent,
        "getLastTransactions": models.Transaction,
    }
    for reply in replies:
        if reply.get("error"):
            continue
        result = reply["result"]
        if reply["tool"] == "recoverTransactionDispute" and result is None:
            continue
        records = result if isinstance(result, list) else [result]
        for record in records:
            validators[reply["tool"]].model_validate(record)


def expand_case(source: dict[str, Any]) -> dict[str, Any]:
    return {**source, "query": source["turns"][0], "expected_behavior": source["outcome"],
            "account": [], "transaction": _case_replies(source)}


def _case_replies(source: dict[str, Any]) -> list[dict[str, Any]]:
    family = source["family"]
    record = {"caseId": "SYNTHETIC-CASE", "transactionId": "SYNTHETIC-TX",
              "productNumber": "100001", "reason": source["turns"][0],
              "status": "WAITING_USER_APPROVAL", "openedAt": "2026-10-01T12:00:00Z",
              "updatedAt": "2026-10-01T12:00:00Z", "recommendationOptedOut": False}
    if family in {"ambiguous", "unsupported", "injection"}:
        return []
    if family == "foreign-case":
        return [_reply("getSupportCase", {"case_id": "SYNTHETIC-FOREIGN"},
                       error="Support case is unavailable for the authenticated customer")]
    if family in {"status", "optout"}:
        return _status_replies(source, record)
    if family in {"foreign", "failure"}:
        return [_reply(
            "getLastTransactions", {"product_number": "999999" if family == "foreign" else "100001"},
            error="Account is unavailable for the authenticated customer" if family == "foreign"
            else "Transaction service temporarily unavailable",
        )]
    return _intake_replies(source, record)


def _reply(tool: str, arguments: dict[str, Any], result: Any = None,
           error: str | None = None) -> dict[str, Any]:
    return {"tool": tool, "arguments": arguments, "result": result, "error": error}


def _status_replies(source: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    record.update(status=source["outcome"], triageOutcome="escalated")
    events = [
        {"eventType": "CASE_OPENED", "actor": "customer", "createdAt": record["openedAt"]},
        {"eventType": "APPROVAL_REQUESTED", "actor": "system", "createdAt": record["openedAt"]},
        {"eventType": "APPROVAL_GRANTED", "actor": "customer", "createdAt": record["updatedAt"]},
        {"eventType": "ESCALATED_TO_REVIEW", "actor": "system", "createdAt": record["updatedAt"]},
    ]
    if source["family"] == "optout":
        record.update(resolutionOutcome="withdrawn_by_customer",
                      recommendationType="transaction_alerts", recommendationOptedOut=True,
                      resolvedAt=record["updatedAt"])
        events = events[:2]
        record["triageOutcome"] = None
        events.extend([
            {"eventType": "CUSTOMER_DECLINED", "actor": "customer",
             "createdAt": record["updatedAt"]},
            {"eventType": "RESOLVED", "actor": "system", "createdAt": record["updatedAt"]},
            {"eventType": "RECOMMENDATION_DISMISSED", "actor": "customer",
             "createdAt": record["updatedAt"]},
        ])
    return [_reply("getSupportCase", {"case_id": record["caseId"]}, record),
            _reply("getSupportCaseTimeline", {"case_id": record["caseId"]}, events)]


def _intake_replies(source: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    family = source["family"]
    replies = [_reply("getLastTransactions", {"product_number": "100001"}, [
        {"id": "SYNTHETIC-TX", "recipientName": "Mercado Azul", "amount": 120.0,
         "currency": "USD", "timestamp": "2026-09-30T12:00:00Z", "product_number": "100001",
         "status": "Declined" if family == "non-approved" else "Approved"},
        {"id": "SYNTHETIC-DISTRACTOR", "recipientName": "Other Store", "amount": 120.0,
         "currency": "USD", "timestamp": "2026-09-29T12:00:00Z", "product_number": "100001",
         "status": "Approved"},
    ])]
    errors = {"blocked": "Disputes can only be opened on an Active product",
              "expired": "Transaction falls outside the dispute window",
              "non-approved": "Only Approved transactions can be disputed",
              "duplicate": "An open dispute already exists for this transaction"}
    preview = {
        "previewToken": "SYNTHETIC-PREVIEW-TOKEN", "transactionId": record["transactionId"],
        "reason": record["reason"], "expiresAt": "2026-10-01T12:10:00Z",
        "transaction": replies[0]["result"][0],
    }
    replies.append(_reply(
        "previewTransactionDispute", {"transaction_id": "SYNTHETIC-TX", "reason": source["turns"][0]},
        preview if family not in errors else None, errors.get(family),
    ))
    if source.get("consent") is True:
        triage = {"low": "fast_track", "high": "escalated", "missing": "insufficient_signal"}[family]
        final = {**record, "status": "IN_REVIEW", "triageOutcome": triage}
        replies.append(_reply(
            "reportTransactionDispute", {"preview_token": preview["previewToken"]}, final,
        ))
    return replies
