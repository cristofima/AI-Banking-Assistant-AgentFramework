"""Paired synthetic dispute replay; baseline is offline, proposed requires explicit model settings."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import platform
import sys
from time import perf_counter
from typing import Any
import unicodedata
from xml.etree import ElementTree

from banking_evals.resources import repository_root

ROOT = repository_root()

from banking_evals.dispute_replay import (
    DATASET, display_value, expanded_fingerprint, fingerprint, load_cases, score_case,
)
from banking_evals.mcp_replay import ReplayReply, ReplayServer
from banking_evals.evidence import (
    CURRENT_RESULT, available_output, controlled_error, customer_identity,
    register_result, sanitize, unique_output,
)


def normalized(message: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFKD", message.casefold())
                   if not unicodedata.combining(character))


class DeterministicIntake:
    """Conservative finite-state intake using only customer turns and tool results."""

    def __init__(self, locale: str, session: Any) -> None:
        self.locale = locale
        self.session = session
        self.selected: dict[str, Any] | None = None
        self.case: dict[str, Any] | None = None
        self.preview: dict[str, Any] | None = None
        self.reason = ""
        self.stopped = False

    def text(self, key: str) -> str:
        messages = {
            "clarify": ("Which account, merchant and amount?", "¿Qué cuenta, comercio e importe?",
                        "Qual conta, estabelecimento e valor?"),
            "unsupported": ("That request is unavailable here.", "Esa solicitud no está disponible aquí.",
                            "Essa solicitação não está disponível aqui."),
            "unavailable": ("The requested resource or service is unavailable. No action was confirmed.",
                            "El recurso o servicio no está disponible. No se confirmó ninguna acción.",
                            "O recurso ou serviço está indisponível. Nenhuma ação foi confirmada."),
            "confirm": ("Is this the transaction?", "¿Es esta la transacción?", "Essa é a transação?"),
            "approval": ("Approve or decline investigation of this dispute?",
                         "¿Aprueba o rechaza la investigación de este reclamo?",
                         "Aprova ou recusa a investigação desta disputa?"),
            "proposal": (
                "May I create this dispute case and send it for review? This does not refund, credit or block a card.",
                "¿Autoriza crear este reclamo y enviarlo a revisión? Esto no reembolsa, acredita ni bloquea una tarjeta.",
                "Autoriza criar esta disputa e enviá-la para análise? Isso não reembolsa, credita nem bloqueia um cartão.",
            ),
            "declined": ("No case was created.", "No se creó ningún reclamo.", "Nenhuma disputa foi criada."),
            "recorded": ("Service-reported outcome", "Resultado informado por el servicio",
                         "Resultado informado pelo serviço"),
            "recommendation": ("Optional transaction alerts", "Alertas de transacciones opcionales",
                               "Alertas de transações opcionais"),
        }
        return messages[key][("en", "es", "pt").index(self.locale)]

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        reply = await self.session.call_tool(tool, arguments)
        if reply.is_error:
            self.stopped = True
            raise ValueError("Controlled synthetic tool denial or unavailability")
        return json.loads(reply.content[0].text)

    def outcome(self, record: dict[str, Any]) -> str:
        fields = [record.get(key) for key in ("caseId", "status", "triageOutcome", "resolutionOutcome")]
        translated = []
        for value in fields:
            if not value:
                continue
            text = display_value(value, self.locale)
            if value != record.get("caseId") and ("_" in value or "-" in value):
                text += f" ({value})"
            translated.append(text)
        fields = translated
        answer = self.text("recorded") + ": " + ", ".join(str(value) for value in fields if value)
        if record.get("recommendationType") and not record.get("recommendationOptedOut"):
            answer += ". " + self.text("recommendation") + ": " + display_value(
                record["recommendationType"], self.locale,
            )
        return answer

    async def run(self, message: str) -> str:
        if self.stopped:
            return self.text("unavailable")
        text = normalized(message)
        try:
            if self.case is not None:
                return await self.respond(text)
            if self.preview is not None:
                approved = bool(re.search(r"\b(approve|apruebo|aprovo|authorize|autorizo)\b", text))
                declined = bool(re.search(r"\b(no|cancel|decline|rechazo|recuso|nao)\b", text))
                if approved == declined:
                    return self.text("proposal")
                if declined:
                    self.preview = None
                    self.selected = None
                    self.stopped = True
                    return self.text("declined")
                self.case = await self.call("reportTransactionDispute", {
                    "preview_token": self.preview["previewToken"],
                })
                return self.outcome(self.case)
            if self.selected is not None:
                if not re.search(r"\b(yes|si|sim)\b", text) or not re.search(r"transa", text):
                    return self.text("confirm")
                self.preview = await self.call("previewTransactionDispute", {
                    "transaction_id": self.selected["id"], "reason": self.reason,
                })
                transaction = self.preview["transaction"]
                context = " | ".join(str(transaction[key]) for key in (
                    "recipientName", "amount", "currency", "timestamp",
                ))
                location = [transaction.get(key) for key in ("transactionCountry", "transactionCity")]
                context += " | " + ", ".join(value for value in location if value) if any(location) else ""
                return context + ". " + self.preview["reason"] + ". " + self.text("proposal")
            if any(phrase in text for phrase in ("credit limit", "admin mode", "all customers")):
                return self.text("unsupported")
            case_match = re.search(r"\b(?:case|caso)\s+([A-Z0-9]+(?:-[A-Z0-9]+)+)\b", message, re.IGNORECASE)
            if case_match:
                case_id = case_match.group(1).upper()
                record = await self.call("getSupportCase", {"case_id": case_id})
                events = await self.call("getSupportCaseTimeline", {"case_id": case_id})
                self.case = record
                answer = self.outcome(record) + "; " + ", ".join(event["eventType"] for event in events)
                if record["status"] == "WAITING_USER_APPROVAL":
                    answer += ". " + self.text("approval")
                return answer
            account = re.search(r"\b(?:account|cuenta|conta)\s+(\d+)\b", text)
            if not account:
                return self.text("clarify")
            transactions = await self.call("getLastTransactions", {"product_number": account.group(1)})
            amount = re.search(r"\$(\d+(?:\.\d{1,2})?)", text)
            candidates = [transaction for transaction in transactions if amount
                          and transaction.get("amount") == float(amount.group(1))
                          and transaction.get("recipientName")
                          and normalized(transaction.get("recipientName") or "") in text]
            if len(candidates) != 1:
                return self.text("clarify")
            self.selected = candidates[0]
            self.reason = message
            return " | ".join(str(self.selected[key]) for key in ("recipientName", "amount", "timestamp")) \
                + ". " + self.text("confirm")
        except ValueError:
            return self.text("unavailable")

    async def respond(self, message: str) -> str:
        assert self.case is not None
        if self.case["status"] != "WAITING_USER_APPROVAL":
            return self.outcome(self.case)
        approved = bool(re.search(r"\b(approve|apruebo|aprovo)\b", message))
        declined = bool(re.search(r"\b(no|cancel|decline|rechazo|recuso|nao)\b", message))
        if approved == declined:
            return self.text("approval")
        self.case = await self.call("respondToDisputeApproval", {
            "case_id": self.case["caseId"], "approved": approved,
        })
        return self.outcome(self.case)


async def run_baseline(case: dict[str, Any]) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []
    servers = {name: ReplayServer(name, [ReplayReply(**reply) for reply in case[name]], trace)
               for name in ("account", "transaction")}
    result: dict[str, Any] = {"case_id": case["id"], "locale": case["locale"], "turns": [],
                              "protocol_passed": False, "tool_calls": trace,
                              "identity_fixture": customer_identity(case["locale"])}
    register_result(result)
    try:
        async with AsyncExitStack() as stack:
            sessions = {name: await stack.enter_async_context(server.connect())
                        for name, server in servers.items()}
            comparator = DeterministicIntake(case["locale"], sessions["transaction"])
            for index, message in enumerate(case["turns"]):
                for server in servers.values():
                    server.turn = index
                started = perf_counter()
                entry = {"turn": index, "query": message, "completed": False, "final_answer": ""}
                result["turns"].append(entry)
                answer = await comparator.run(message)
                entry.update(final_answer=answer, response={"text": answer}, completed=True,
                             latency_seconds=perf_counter() - started)
            for server in servers.values():
                server.assert_complete()
            result["protocol_passed"] = True
    except Exception as error:
        result["error"] = {"type": type(error).__name__, "message": "Synthetic baseline execution failed"}
    result["replay_failures"] = {name: server.failures for name, server in servers.items()}
    result["unconsumed_replies"] = {name: len(server.replies) - server._position
                                    for name, server in servers.items()}
    return result
