"""Credential-free replay identity and redacted, non-overwriting evidence helpers."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

CURRENT_RESULT: ContextVar[dict[str, Any] | None] = ContextVar("replay_result", default=None)


def customer_identity(locale: str) -> dict[str, Any]:
    if locale not in {"en", "es", "pt"}:
        raise ValueError("Unsupported replay locale")
    return {"sub": "synthetic-replay-user", "customer_id": "SYNTHETIC-CUSTOMER",
            "email": "replay@example.invalid", "locale": locale,
            "role": "customer", "identity_version": 1}


def register_result(result: dict[str, Any]) -> None:
    CURRENT_RESULT.set(result)


def controlled_error(error: BaseException) -> dict[str, Any]:
    details: dict[str, Any] = {"type": type(error).__name__, "message": "Replay execution failed"}
    if isinstance(error, BaseExceptionGroup):
        details["causes"] = [controlled_error(cause) for cause in error.exceptions]
    return details


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()
                if not any(word in key.lower() for word in (
                    "authorization", "secret", "password", "token", "api_key", "credential"))
                or key in {"input_token_count", "output_token_count", "total_token_count"}
                or (key in {"previewToken", "preview_token"}
                    and item == "SYNTHETIC-PREVIEW-TOKEN")}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)bearer\s+[^\s\"']+", "[REDACTED BEARER]", value)
        value = re.sub(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b", "[REDACTED JWT]", value)
        value = re.sub(r"v1\.[\w-]+\.[a-f0-9]{64}", "[REDACTED IDENTITY]", value)
        value = re.sub(
            r"(?i)\b(password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)"
            r"\s*[=:]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
            r"\1=[REDACTED]", value,
        )
        value = value.replace("synthetic-replay-only-not-a-production-secret", "[REDACTED]")
    return value


def financial_claim(answer: str) -> bool:
    """Conservative phrase gate, not a semantic safety or translation evaluator."""
    effects = r"refund|reimburse|card.{0,12}block|reembols|cr[eé]dit|cart[aã]o.{0,12}bloque|tarjeta.{0,12}bloque"
    positive = r"issued|posted|applied|credited|refunded|blocked|emitido|aplicado|reembolsado|bloquead[ao]|creditado"
    for sentence in re.split(r"[.!?\n]", answer.lower()):
        if re.search(r"\b(no|not|never|cannot|can't|without|n[oã]o|sin|sem|ning[uú]n)\b", sentence):
            continue
        if re.search(effects, sentence) and re.search(positive, sentence):
            return True
    return False


def unique_output(directory: Path, prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return directory / f"{prefix}-{stamp}-{uuid4().hex[:8]}.json"


def available_output(output: Path) -> Path:
    if any(output.with_suffix(suffix).exists() for suffix in (".json", ".xml", ".md")):
        return unique_output(output.parent, output.stem)
    return output
