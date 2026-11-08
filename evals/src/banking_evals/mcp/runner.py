"""Run the production workflow with a real model and synthetic MCP sessions."""

from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

from banking_evals.resources import repository_root

ROOT = repository_root()

from agent_framework import BaseChatClient
from agent_framework.foundry import FoundryChatClient
from azure.ai.agentserver.core import (
    FoundryAgentRequestContext, reset_request_context, set_request_context,
)
from azure.identity.aio import AzureCliCredential

from app.agents.azure_chat.hosted_workflow import build_hosted_workflow
from banking_evals.mcp_replay import ReplayReply, ReplayServer
from banking_evals.evidence import (
    available_output, controlled_error, customer_identity, register_result, sanitize, unique_output,
)


def evaluation_names(agent_name: str, lane: str = "mcp-replay") -> tuple[str, str]:
    name = f"{agent_name}-{lane}-eval"
    return name, f"{name} run"


def error_details(error: BaseException) -> dict[str, Any]:
    return controlled_error(error)


async def run_case(
    client: BaseChatClient, case: dict[str, Any], timeout_seconds: float = 120,
    *, account_chat_client: BaseChatClient | None = None,
    transaction_chat_client: BaseChatClient | None = None,
) -> dict[str, Any]:
    secret = "synthetic-replay-only-not-a-production-secret"
    identity = customer_identity(case.get("locale", "en"))
    claims = {key: identity[key] for key in ("sub", "customer_id", "email", "locale")}
    payload = base64.urlsafe_b64encode(json.dumps(
        claims, sort_keys=True, separators=(",", ":"),
    ).encode()).rstrip(b"=").decode()
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = set_request_context(FoundryAgentRequestContext(user_id=f"v1.{payload}.{signature}"))
    trace: list[dict[str, Any]] = []
    servers = {
        name: ReplayServer(name, [ReplayReply(**reply) for reply in case.get(name, [])], trace)
        for name in ("account", "transaction")
    }
    result: dict[str, Any] = {
        "case_id": case["id"], "offline_or_simulated": True, "lane": "mcp-replay",
        "model_execution": "caller-supplied", "expected_behavior": case["expected_behavior"],
        "query": case["query"], "locale": claims["locale"],
        "protocol_passed": False,
        "turns": [], "tool_calls": trace, "identity_fixture": identity,
        "identity_acceptance": "synthetic envelope only; JWT/introspection not exercised",
    }
    register_result(result)
    try:
        async with asyncio.timeout(timeout_seconds), AsyncExitStack() as stack:
            sessions = {
                name: await stack.enter_async_context(server.connect())
                for name, server in servers.items()
            }
            workflow = build_hosted_workflow(
                client, "in-memory://account", "in-memory://transaction", secret,
                account_mcp_client=sessions["account"],
                transaction_mcp_client=sessions["transaction"],
                account_chat_client=account_chat_client,
                transaction_chat_client=transaction_chat_client,
            )
            agent = workflow.as_agent(name="home_banking_agent")
            conversation = agent.create_session() if "turns" in case else None
            for turn, message in enumerate(case.get("turns", [case["query"]])):
                for server in servers.values():
                    server.turn = turn
                started = perf_counter()
                options = {"session": conversation} if conversation is not None else {}
                entry = {"turn": turn, "query": message, "completed": False,
                         "final_answer": "", "stream_updates": []}
                result["turns"].append(entry)
                stream = agent.run(message, stream=True, **options)
                async for update in stream:
                    entry["stream_updates"].append(sanitize(update.to_dict()))
                    entry["final_answer"] += sanitize(update.text or "")
                response = await stream.get_final_response()
                result["response"] = sanitize(response.to_dict())
                result["final_answer"] = sanitize(response.text)
                entry.update(response=sanitize(response.to_dict()), final_answer=sanitize(response.text),
                             completed=True, latency_seconds=perf_counter() - started)
                if not response.text.strip():
                    raise AssertionError("Workflow returned an empty final answer")
            for server in servers.values():
                server.assert_complete()
            if not response.text.strip():
                raise AssertionError("Workflow returned an empty final answer")
            result["protocol_passed"] = True
    except Exception as error:
        result["error"] = error_details(error)
    finally:
        reset_request_context(token)
        result["replay_failures"] = {name: server.failures for name, server in servers.items()}
        result["unconsumed_replies"] = {
            name: len(server.replies) - server._position for name, server in servers.items()
        }
        result["behavior_review"] = "pending"
        result["turns"] = sanitize(result["turns"])
        result["tool_calls"] = sanitize(trace)
    return sanitize(result)


async def main_async(args: argparse.Namespace) -> int:
    cases = json.loads(args.dataset.read_text(encoding="utf-8"))
    if args.case:
        cases = [case for case in cases if case["id"] in args.case]
        if {case["id"] for case in cases} != set(args.case):
            raise ValueError("Unknown replay case ID")
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Replay requires a nonempty dataset with unique case IDs")
    models = {
        "triage": args.triage_model or args.model,
        "account": args.account_model or args.model,
        "transaction": args.transaction_model or args.model,
    }
    async with AzureCliCredential() as credential:
        clients = {
            model: FoundryChatClient(
                project_endpoint=args.project_endpoint, model=model, credential=credential,
            )
            for model in set(models.values())
        }
        results = [await run_case(
            clients[models["triage"]], case, args.timeout_seconds,
            account_chat_client=clients[models["account"]],
            transaction_chat_client=clients[models["transaction"]],
        ) for case in cases]
    evaluation_name, run_name = evaluation_names(args.agent_name)
    report = {
        "evaluation_name": evaluation_name, "run_name": run_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model, "participant_models": models,
        "model_execution": "real", "sample_size": len(results),
        "offline_or_simulated": True, "results": results,
        "foundry_submission": "not_submitted", "behavior_review": "pending",
    }
    output = available_output(args.output or unique_output(ROOT / "evals" / "results", "mcp-replay"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(sanitize(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(results)} synthetic replay transcripts to {output}")
    passed = all(result["protocol_passed"] for result in results)
    return 0 if results and passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--triage-model", help="Triage deployment; defaults to --model")
    parser.add_argument("--account-model", help="Account deployment; defaults to --model")
    parser.add_argument("--transaction-model", help="Transaction deployment; defaults to --model")
    parser.add_argument("--agent-name", default="home-banking-agent")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals" / "replay_cases.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", action="append")
    parser.add_argument("--timeout-seconds", type=float, default=120)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())