# Evaluation Tests

These tests exercise the installed `banking_evals` package with synthetic evidence.
They require no model calls, Azure credentials, running services or database writes.
For capture and judging commands, see the [evaluation guide](../README.md).

## Run from repository root

Use the Evals environment for credential-free checks:

```powershell
rtk proxy uv run --project evals --extra offline python -m pytest evals\tests -q --tb=short
```

SDK-backed provider and production-workflow integration tests need the optional
Agent Framework dependencies. Use the existing configured Agent environment for
complete local coverage:

```powershell
$env:OTEL_SDK_DISABLED = 'true'
rtk proxy uv run --project app\agent --frozen --no-sync python -m pytest evals\tests -q --tb=short
```

`--no-sync` assumes dependencies are already installed. Missing optional framework
dependencies cause intentional skips in the minimal Evals environment; a skipped
test is not verified SDK coverage. Test doubles prevent model calls in either lane.

## Coverage map

| Tests                                                      | Contract exercised                                                                                |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| [Semantic contracts](test_semantic_contracts.py)           | Strict evidence/judgment schemas, safe data, finite JSON and valid citations.                     |
| [Semantic dataset](test_semantic_dataset.py)               | Eight synthetic cases, rubric consistency and fingerprints.                                       |
| [Semantic judge](test_semantic_judge.py)                   | Evidence preparation, injected verdicts, bounded concurrency and controlled failures.             |
| [Semantic reports](test_semantic_reports.py)               | Separate criteria, technical failure precedence and uncalibrated acceptance.                      |
| [Semantic CLI](test_semantic_cli.py)                       | Capture/judge separation, authorization/configuration validation, provenance and output behavior. |
| [Semantic provider](test_semantic_provider.py)             | Mocked SDK transport, structured output, retry classification and shared call budgets.            |
| [Semantic technical evaluator](test_semantic_technical.py) | Calls, arguments, ordered traces, complete answers, malformed evidence and zero-call reports.     |
| [Offline alignment](test_offline_alignment.py)             | Frozen dispute inputs, source integrity, comparator and saved-report scoring.                     |
| [Consent replay](test_consent_replay.py)                   | Synthetic multi-turn preview, explicit consent and legacy approval behavior.                      |
| [Migration contracts](test_migration_contracts.py)         | Package and compatibility contracts supporting replay.                                            |

## Scope and limits

- The minimal Evals environment skips tests that need optional frameworks.
- The [technical fixture](../semantic_technical_evidence.json) is evaluated with
  RuntimeWarning treated as an error and zero model calls.
- [Hosted Agent CI](../../.github/workflows/ci-hosted-agent.yml) configures seven
  semantic test modules and the deterministic fixture on Python 3.11. Local results
  do not establish a successful remote run or Python 3.11 certification.

Injected judge verdicts test code behavior, not semantic accuracy. Authored positive
traces are comparator self-checks, not captured agent responses. This suite does not
establish human calibration, real-model consistency, hosted identity transport,
service authorization, persisted financial effects or browser/data parity. Keep
those acceptance gates separate; the rubric remains uncalibrated.
