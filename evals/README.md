# Offline Evaluation and Synthetic Confirmation

## Semantic replay evaluation

[semantic_cases.json](semantic_cases.json) contains eight development-exposed
synthetic cases across en/es/pt. [semantic_rubric.json](semantic_rubric.json)
records separate confidentiality, helpfulness, injection resistance, groundedness,
relevance and locale criteria, with explicit anchors. The rubric is **uncalibrated**:
a successful judgment is inconclusive for acceptance, while execution or technical
failures remain failures. No aggregate semantic score replaces individual criteria.

The [semantic package](src/banking_evals/semantic) validates strict contracts,
projects safe evidence, checks tool choice and task completion deterministically,
and emits additive private JSON, Markdown and JUnit reports. The
[Foundry adapter](src/banking_evals/semantic_provider.py) requests structured output
with no tools and no response storage. It uses the installed Agent Framework client;
no new evaluation SDK or dependency was added. Provider compatibility is tested with
SDK-backed mocks, not a verified remote deployment.

### Credential-free checks

Run from repository root:

```powershell
rtk proxy uv run --project evals --extra offline python -m pytest evals\tests\test_semantic_contracts.py evals\tests\test_semantic_dataset.py evals\tests\test_semantic_judge.py evals\tests\test_semantic_reports.py evals\tests\test_semantic_cli.py evals\tests\test_semantic_provider.py evals\tests\test_semantic_technical.py -q
rtk proxy uv run --project evals --extra offline python -m banking_evals.semantic.technical --evidence evals\semantic_technical_evidence.json --output evals\results\semantic-technical.json
```

The saved [technical fixture](semantic_technical_evidence.json) is authored positive
self-check evidence, **not agent output**. Its eight passing traces demonstrate
comparator behavior, not agent quality. Missing evidence, wrong calls/arguments or
results, incomplete answers and execution failures fail deterministic completion.
Intentional canned ownership denials can pass. Calls are ordered within each MCP
server; no arbitrary cross-server order is imposed. The scorer accepts the minimal
fixture envelope or a capture envelope with matching dataset fingerprint and exact
ordered requested case IDs. It does not judge response meaning or actual service
authorization. Output explicitly declares zero model calls and no Foundry submission.

[Hosted Agent CI](../.github/workflows/ci-hosted-agent.yml) configures a credential-free
semantic job for offline contracts/mocked judging and this fixture, with 14-day
artifacts. Configuration is not proof of a successful GitHub Actions run or a complete
`eval-quality`/`eval-authz` gate.

### Local validation

The installed Agent environment runs the full evaluation suite; the minimal Evals
environment skips tests that need optional frameworks, so SDK-backed coverage requires
the Agent/model dependencies. See the [test guide](tests/README.md) for coverage and
environment selection.

Run the local checks from repository root:

```powershell
$env:OTEL_SDK_DISABLED = 'true'
rtk proxy uv run --project app\agent --frozen --no-sync python -m pytest evals\tests -q --tb=short

rtk proxy uv run --project evals --frozen --no-sync python -W error::RuntimeWarning -m banking_evals.semantic.technical --evidence evals\semantic_technical_evidence.json --output evals\results\semantic-technical-validation.json
```

The technical command evaluates eight synthetic traces with zero model calls. Use a
fresh repository-local output path; do not reuse an existing output file. Mocked
verdicts verify forwarding, validation and reporting, not whether a real judge agrees
with independent reviewers. These checks make no model calls, remote Foundry submission
or remote semantic CI run; the rubric remains uncalibrated.

### Opt-in capture and saved-evidence judging

[run_semantic_eval.py](src/banking_evals/run_semantic_eval.py) separates capture from
judging. Capture reuses the production workflow with synthetic in-memory MCP sessions,
not production REST/MCP data or service authorization. Judge mode never reruns the
agent. Both modes require an explicit HTTPS endpoint, deployment, call budget and
`--authorize-model-calls`, which authorizes synthetic-data transmission as well as
billable calls. Obtain session approval before running either command. Examples use
owner-supplied endpoint/deployment environment variables, not credentials:

```powershell
rtk proxy uv run --project evals --extra model python -m banking_evals.run_semantic_eval capture --project-endpoint $env:PROJECT_ENDPOINT --model $env:AGENT_DEPLOYMENT --authorize-model-calls --max-calls 40 --output evals\results\semantic-capture.json
rtk proxy uv run --project evals --extra model python -m banking_evals.run_semantic_eval judge --project-endpoint $env:PROJECT_ENDPOINT --model $env:JUDGE_DEPLOYMENT --authorize-model-calls --max-calls 8 --evidence evals\results\semantic-capture.json --output evals\results\semantic-judged.json
```

CLI authentication uses the developer's existing Azure CLI credential server-side.
Do not print tokens or supply private instructions, real customer data or credentials
as evidence. Capture records raw dataset/rubric hashes, model/settings, actual request
count and requested IDs. Judge mode requires both matching hashes and exact requested
IDs, validates citations and rejects unsafe/oversized evidence instead of truncating.
Captured results retain completed answers and chronological tool traces only.

Default timeout is 120 seconds, bounded at 600; default judge retries are zero,
concurrency one and context limit 100000 characters. Retries cover transient transport
failures only and consume the shared budget. Capture is sequential with no retries.
Outputs are exclusive: choose a new path for each run. Judge emits JSON/Markdown/JUnit
sidecars without overwriting capture. Exit 0 means complete execution/judging without
technical failures, not calibrated semantic acceptance; exit 1 reports failed execution
or judging, and exit 2 reports controlled invalid input/configuration.

Human calibration, repeated judge consistency, thresholds, remote compatibility,
billable CI, real-model locale/grounding and browser/hosted/authz/financial acceptance
remain open. No real model run or remote Foundry evaluation is established by this
implementation. Frozen dispute workloads and their historical results are unchanged.

## Current dispute contract

[dispute_cases.json](dispute_cases.json) is the 25-case **dispute-replay-v2**
development-exposed synthetic confirmation set, not untouched held-out evidence.
[dispute_freeze.json](dispute_freeze.json) freezes exact dataset bytes, canonical
expanded inputs, case count, and source dependencies. Dependency hashing normalizes
CRLF to LF for portability; dataset hashing remains exact. The scoped
`.gitattributes` rule keeps the frozen dataset checkout in LF on Windows too.
Freeze revision 4 covers the `src` package layout and responsibility
splits, with transitive Agent/evaluation source membership enforced. It preserves both
the exact dataset hash and the `dispute-consent-v3` expanded-input hash, which covers
pre-intake consent, production preview/location models, and asynchronous SDK tool
invocation, plus the additive ADR 0009 projections and unexecuted defaults.
The 25 source scenarios, labels, dataset version, and exact dataset bytes remain
unchanged; canonical expanded inputs deliberately change to reflect the consent
contract. Workload or expansion changes require a new documented version/freeze.
Canonical expansion reads the dataset as UTF-8 before hashing compact, sorted-key
UTF-8 JSON; platform-default decoding is not equivalent for localized text.

Customer approval authorizes investigation only: every fraud-score band remains
IN_REVIEW. Low scores route to fast-track review, high scores escalate, and missing
scores require insufficient-signal review. Declining new intake creates no case;
declining a legacy pending case resolves it as withdrawn_by_customer.
Fixtures assert no posting, refund, credit, balance change, or product blocking.
Reviewer assignment is not a verdict. ADR 0009 approves production operator verdicts
and recorded financial effects; this investigation-only workload contains no simulated
assigned verdict and does not exercise those operations. Reassignment remains out of scope.

## Offline comparator and scoring

[run_dispute_replay.py](run_dispute_replay.py) implements finite-state intake from
customer turns and synthetic MCP replies, not expected labels. Separate transaction
confirmation precedes a read-only preview; explicit consent binds that preview to
acceptance directly into IN_REVIEW. Ambiguous consent creates no case. Legacy pending
cases retain their approval/decline path. Its narrow
en/es/pt grammar and dollar-prefixed amounts do not prove general language understanding.
[dataset.py](src/banking_evals/disputes/dataset.py) validates response models and
[scoring.py](src/banking_evals/disputes/scoring.py) checks complete
turns, ordered tool arguments/results, consent, selection, status grounding, and
unsupported actions. Financial-effect phrase checks are lexical, not semantic;
negation and mixed clauses still require human review.

`banking-evals` installs from `src/banking_evals`. Dataset, scoring, report and
baseline modules are separate from the opt-in model runner. The `offline` extra
installs test tools without the Agent SDK; `model` explicitly installs the Agent.
Legacy root scripts remain thin compatibility entrypoints, without path injection.

Run from repository root:

```powershell
rtk proxy uv run --project evals --extra offline python -m pytest evals\tests -q
rtk proxy uv run --project evals --extra offline python -m banking_evals.run_dispute_replay --system baseline --timeout-seconds 10 --output evals\results\dispute-alignment-baseline.json
rtk proxy uv run --project evals --extra offline python -m banking_evals.run_dispute_replay --rescore evals\results\dispute-alignment-baseline.json --output evals\results\dispute-alignment-rescored.json
```

Installed commands are `banking-dispute-replay`, `banking-mcp-replay` and
`banking-held-out-eval`. For use outside the checkout, set `BANKING_EVALS_ROOT` to
its absolute path; fixtures and production contracts deliberately remain repository
resources, not wheel data. The resolver rejects escaped or missing resources. Model
execution requires `--extra model` and separate authorization; installation alone
does not authorize billable runs.

For the complete agent suite and evaluation integration regressions, use both
extras from the repository root. The agent's deployment environment intentionally
does not install `banking-evals`; the dependency points from evaluations to the
agent so the hosted package remains self-contained.

```powershell
rtk proxy uv sync --project evals --extra offline --extra model --frozen
$env:OTEL_SDK_DISABLED = "true"
rtk proxy uv run --project evals --frozen --no-sync python -m pytest app\agent\tests evals\tests -q
```

The offline comparator, saved-evidence rescoring and replay tests produce synthetic
results only. The replay tests bind paired fixtures to the current scorer and frozen
expanded inputs, reject missing/stale fingerprints, and check controlled nested
errors without retaining arbitrary exception text. The combined offline regression
command covers the existing replay tests and the alignment tests:

```powershell
rtk proxy uv run --project app\agent --frozen --no-sync python -m pytest app\agent\tests\test_dispute_replay.py app\agent\tests\test_mcp_replay.py evals\tests -q --tb=short
```

The comparator's structured passes measure synthetic comparator completion, not
model-quality evidence. Validate the freeze before reusing saved results.

## Migration offline regression scope

The standalone `offline` environment skips tests that need the optional SDK; the
Agent-backed environment runs them. Overlapping suites are not additive coverage.
Baseline and saved-evidence rescoring run without model execution. These checks do not
establish semantic quality, locale correctness, service authorization or live
acceptance.

## Freeze alignment

The freeze pins reviewed transport dependency hashes: the
Agent entrypoint, banking identity middleware and hosted diagnostic.
Hosted requests separate the opaque platform partition from signed banking
claims in the forwarded `x-client-banking-identity` header; local identity,
signature validation, real platform call IDs and the native OAuth Toolbox gate
are unchanged by that separation. Exact dataset bytes, consent expansions and dependency coverage
are preserved. Reviewed changes are recorded in [dispute_freeze.json](dispute_freeze.json); offline validation
does not establish deployed header forwarding, impersonation permission, native
Toolbox grant binding, hosted readiness, model quality or live-data parity.

## Offline regression scope

The combined offline suites run from the repository root with `OTEL_SDK_DISABLED=true`:

```powershell
rtk proxy uv run --project app\agent --frozen --no-sync python -m pytest evals\tests app\agent\tests\test_dispute_replay.py app\agent\tests\test_mcp_replay.py app\agent\tests\test_replay_summary.py -q --tb=short
rtk proxy uv run --directory app\agent python -m pytest tests ..\..\evals\tests -q --tb=short
```

They include consent regressions and tamper/freeze tests, covering instruction
contracts, mocked workflow behavior and synthetic replay/freeze guards. This is offline
synthetic evidence, not real-model discovery, semantic quality, locale correctness,
live authorization, PostgreSQL, browser, financial settlement, hosted identity
transport or hosted acceptance. It runs no billable inference or cloud evaluation.

## Consultation safeguards

Existing-case inquiries are read-only. Triage routes case lists, status, timelines,
and follow-ups to Transaction. Use customer/tool-supplied IDs, request selection for
ambiguous references rather than choosing the newest case, refresh detail on status
follow-ups, and read timeline events for event questions. Empty lists are truthful;
missing and foreign cases remain equivalently unavailable. Consent or IN_REVIEW
never establishes operator takeover or a verdict. User-facing prose follows the
signed locale; underscore/hyphen machine codes remain unchanged.

Focused commands from repository root, with `OTEL_SDK_DISABLED=true`:

| Command                                                                                                       | Evidence boundary                                                |
| ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `rtk proxy uv run --directory app\responses-bff pytest tests\test_responses.py -q`                            | Mock Identity/upstream continuation and staff-denial regressions |
| `rtk proxy uv run --directory app\agent python -m pytest tests\test_hosted_workflow.py -q`                    | Instruction-contract and mocked workflow checks                  |
| `rtk proxy uv run --directory app\business-api\transaction python -m pytest tests\test_dispute_service.py -q` | Seeded SQLite lifecycle/ownership checks                         |

Continuation tests re-introspect Identity and stop revoked/version/role changes
or outages before a second agent request; operator/admin denial makes zero agent
upstream calls. Instruction assertions prove safeguards are present, not real-model
compliance. These tests are not live service/authz, PostgreSQL parity, browser,
locale-quality, or hosted-transport acceptance.

[Hosted Agent CI](../.github/workflows/ci-hosted-agent.yml) configures offline dispute
tests, comparator execution, separate rescoring artifacts, and 14-day retention.
Configuration is not proof of a successful CI run.

## Evidence and paired reporting

Default artifact names include timestamp/UUID. Occupied explicit paths are redirected
to unused names, including JSON/Markdown/JUnit sibling collisions. Use the printed
saved path rather than assuming overwrite. Complete redacted answers, SDK responses,
stream updates, ordered calls, pending turns, unused replies, timeout evidence, and
nested controlled failures are retained. Credential-like keys and token/secret
strings are removed; nonsecret text is not truncated. Only the fixed nonproduction
`SYNTHETIC-PREVIEW-TOKEN` marker is retained in `previewToken`/`preview_token` fields
so saved synthetic evidence preserves consent binding during rescoring. Arbitrary
preview capabilities remain redacted. Inspect redaction before sharing.

Saved proposed evidence supports offline --rescore and --compare-baseline. Pairing
requires identical frozen expanded inputs, dataset hash, case IDs/locales, and
current scorer fingerprint. Reports include model, prompt, comparator, contract,
and runtime metadata. Structured wins/ties/losses and failures are reported; a tie
can mean both failed. Only completed valid nonfailed timings enter percentiles.
Missing, negative, Boolean, NaN/infinite, incomplete, or failed timing is excluded,
not zero. No prices are assumed; cost and safe-resolution economics are unavailable.
Pairing does not establish semantics, locale quality, real authorization, persisted
effects, or improvement over human operations.

Proposed dispute replay calls a real model through in-memory MCP sessions and needs
explicit endpoint/model and billable-run authorization. No model run was executed
for this offline alignment.

## Identity fixture boundaries

Synthetic customer metadata carries role=customer and strict positive integer
identity_version=1. It is not a JWT. The signed agent envelope contains only sub,
customer_id, email, and locale; its v1 format is independent of JWT identity version.
Production JWTs additionally require role/version, issuer, audience, expiry, and
introspection checks. Offline fixtures prove no crypto, revocation/introspection,
role enforcement, service ownership, or BFF/hosted identity transport.

## Isolated MCP replay

[replay.py](src/banking_evals/mcp/replay.py), [run_mcp_replay.py](run_mcp_replay.py), and
[replay_cases.json](replay_cases.json) exercise three synthetic balance, canned-denial,
and empty-transaction protocol cases using the production workflow and real model.
They do not test disputes or real service authorization. Caller-supplied sessions
stay separate from production HTTP/header providers. Full evidence and controlled
failures are retained; behavioral review is separate from protocol passes.
The supplied-number cases expect direct resource lookups, not preliminary account
listing. The denial case is an ordinary balance request with a canned tool ownership
denial; it does not require a tool call for a prompt that can safely be refused outright.

The runner requires `--model` as the base deployment and accepts optional
`--triage-model`, `--account-model`, and `--transaction-model` overrides. Each omitted
or empty override independently falls back to the base deployment, matching production.
Clients are reused when participants share a deployment. Reports retain `model` and
record the effective deployments in `participant_models`.

CI reads `MODEL_DEPLOYMENT_NAME` and the optional Development environment variables
`TRIAGE_MODEL_DEPLOYMENT_NAME`, `ACCOUNT_MODEL_DEPLOYMENT_NAME`, and
`TRANSACTION_MODEL_DEPLOYMENT_NAME`, forwarding them to those flags. These are
existing model deployment names, not agent names; replay does not provision models.

Warnings about missing outgoing handoffs for AccountAgent and
TransactionHistoryAgent reflect the intentional terminal-specialist topology. They
are not themselves a protocol failure; unexpected calls or unused replies still fail
replay and report validation. Prompt contracts and offline tests do not guarantee
real-model compliance; a separately authorized CI replay confirms runtime behavior.

## Evaluation methods

MCP smoke validates protocol contracts using real-model responses and synthetic MCP
sessions. Dispute replay uses a deterministic comparator for structured consent,
tool and outcome checks. Neither evaluates semantic quality through an LLM judge.

The separate [azd-native evaluation recipe](../app/agent/eval.yaml) configures
`eval_model: gpt-5.4-mini` and a locally generated semantic rubric. The rubric is
ignored and is not distributed with the checkout. This is judge-model configuration,
not evidence of a successful remote evaluation or a judge used by Hosted Agent CI.
The custom identity-header limitation described above still applies to azd invoke/eval.

### Offline checks and open acceptance

Run the focused offline regressions from the repository root:

```powershell
$env:OTEL_SDK_DISABLED = 'true'
rtk proxy uv run --project app\agent --frozen --no-sync python -m pytest app\agent\tests\test_refusal_locale_middleware.py app\agent\tests\test_hosted_workflow.py app\agent\tests\test_internal_identity.py app\agent\tests\test_mcp_replay.py evals\tests -q --tb=short
```

These tests cover policy wiring, SDK-marked refusal localization, mocked MCP runner
behavior and existing offline evaluation contracts. They are not semantic guardrail
acceptance or a new proposed-system dispute replay result. The historical
proposed-system **0/25** is not a reproduced current failure or a result superseded
by these tests.

Browser guardrail refusals and chat case creation work in the positive path.
Browser refusal localization remains broken: the stream carries English ordinary
`output_text`, outside the marked-refusal middleware
contract. See the [agent limitation and research findings](../app/agent/README.md#grounding-and-confidentiality).
Semantic guardrail evaluation, an authorized proposed-system dispute replay, and
browser/hosted locale acceptance remain open.

## PR smoke check

[replay_summary.py](replay_summary.py) rejects missing/partial reports, duplicate
cases, protocol errors, unused replies, and missing transcript/answer evidence.
CI uses Development OIDC for same-repository PRs to main/develop, retains 14-day
artifacts, and publishes controlled statuses in a persistent PR comment. Fork model
replay is explicitly not executed. Missing evidence or failed protocol checks fail
the job. The existing Dispute Replay job remains offline; it does not add real-model
dispute execution. This is not a semantic guardrail or dispute quality/authz gate.
No runner creates a remote Foundry evaluation; local display names are not remote IDs.
The existing Dispute Replay job runs offline alignment regressions, the deterministic
comparator and saved-evidence rescoring. These offline checks do not establish
real-model dispute quality, semantics, real ownership, business approval, persisted
financial effects or hosted identity transport.

## Historical diagnostics

[scenarios.json](scenarios.json) preserves 18 **historical obsolete-policy** diagnostic
definitions. [run_held_out_eval.py](run_held_out_eval.py) keeps its label-derived
baseline and keyword classifier for historical diagnostics only. Obsolete automatic
resolution labels are not current acceptance expectations. Full redacted per-turn
responses are retained, and execution continues after controlled failures.
Local live mode requires an explicitly supplied password and an existing BFF stack;
hosted direct mode is diagnostic, not browser/BFF topology acceptance. Historical
numbers are not v2 results.

## Remaining acceptance gates

- Assigned-operator adjudication and financial effects are approved by ADR 0009,
  but the frozen investigation-only replay does not exercise or establish their acceptance.
- Real-model paired disputes, consultation behavior, semantic/locale review, and variability.
- Live JWT role/version/introspection and real ownership/authz acceptance, separate
  from the mocked/seeded regression results above.
- Persisted case/event parity; financial effects are not implemented by these fixtures.
- Browser/BFF and hosted identity/continuation evidence.
- Untouched external confirmation workload and priced usage/cost evidence.
