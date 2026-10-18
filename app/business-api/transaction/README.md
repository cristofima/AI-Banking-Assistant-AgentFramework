# Transaction service

Transaction REST reads and customer dispute routes verify the browser application JWT.
MCP tools use the separate configured OAuth or HMAC agent bearer; neither authorizes
operator access. Business logic retains customer ownership checks.

## Package layout and local startup

The installed package is [banking_transaction](src/banking_transaction/):

- [models](src/banking_transaction/models/) defines transaction and operator DTOs.
- [routers](src/banking_transaction/routers/) separates transaction, customer-dispute
  and operator REST boundaries.
- [services](src/banking_transaction/services/) separates transaction reads, dispute
  intake, operator review and adjudication business logic.
- [consent](src/banking_transaction/consent/) owns signed proposal helpers.
- [projections](src/banking_transaction/projections/) builds transaction and case DTOs.
- [auth](src/banking_transaction/auth/) separates customer JWT, internal agent bearer
  and operator identity checks; [observability](src/banking_transaction/observability/)
  owns tracing and logging.
- [mcp_tools.py](src/banking_transaction/mcp_tools.py) remains the thin tool boundary;
  [main.py](src/banking_transaction/main.py) composes the application.

From repository root, after configuring this service's own `.env`:

```powershell
rtk proxy uv sync --directory app\business-api\transaction --frozen --group dev
$env:PROFILE = "dev"
rtk proxy uv run --directory app\business-api\transaction --env-file .env python -m banking_transaction.main
```

Local development uses port 8071. The ASGI target is `banking_transaction.main:app`;
production uses port 8080. Imports use the nested package namespaces without runtime
path injection. See the [shared packaging guide](../README.md#python-dependency-artifacts).
F5 runs the root ordered stack without debugging or automatic browser launch. Its
local runner adds severity colors and daily UTF-8 `logs/transaction-YYYY-MM-DD.log`
files; the manual command above does not capture files. For that HTTPS Identity
stack, configure `AUTH_USERS_ENDPOINT=https://localhost:8091` and trust the prepared CA.
Logs are ignored and have no automatic retention cleanup; see the
[root startup guide](../../../README.md#run-locally).

## MCP OAuth access tokens

The [.env.example](.env.example) selects local OAuth and `PROFILE=dev`, keeps
Identity at `https://localhost:8091` and browser CORS at `https://localhost:5170`,
and leaves public JWKS and database credentials unset. Supply `OAUTH_JWKS` as
single-line JSON in single quotes, matching Identity's signing key ID. Empty JWKS
rejects MCP calls; no automatic fetch occurs. For HMAC compatibility, explicitly
set `MCP_IDENTITY_MODE=hmac` on this service and the agent and configure the separate
`INTERNAL_IDENTITY_SECRET` to match BFF/agent transport. Login JWT and introspection
keys remain required and distinct. Optional telemetry is disabled by leaving
`APPLICATIONINSIGHTS_CONNECTION_STRING` empty.

Set `MCP_IDENTITY_MODE=oauth`, `OAUTH_ISSUER`, `OAUTH_AUDIENCE` and `OAUTH_JWKS`
(public JWKS JSON from Identity's `/oauth/jwks`, never its private signing key).
The issuer must exactly match the HTTPS issuer without a trailing slash; use an
audience distinct from browser login. RS256, `typ=at+jwt`, known `kid`, strict
customer claims, exact `mcp.access` and a maximum 3600-second lifetime are checked
locally. No introspection, network or HMAC fallback occurs in OAuth mode.
Verified customer grants permit existing MCP reads and customer dispute creation
and approval tools. Legacy `banking.read` and operator/admin grants are rejected.
Service-layer ownership, explicit consent and uncertain-mutation recovery remain
unchanged; the grant does not bypass these checks or authorize automatic replay.

Identity/client revocation and profile changes take effect at expiry, up to 3600 seconds
later. REST login JWTs still require uncached current-identity introspection.
Default `hmac` mode and operator authorization remain separate and unchanged.
Public JWKS rotation is explicit, not fetched at runtime: publish/configure new keys
before signing with them, and retain old public keys until issued tokens expire.
See [Identity OAuth configuration](../identity/README.md#optional-oauth-authorization-code-provider).

## Optional charge-recognition assistance

The read-only MCP tool `getTransactionRecognitionContext(transaction_id)` checks the
selected transaction and card against the authenticated customer. It returns up to
three earlier matching records from the same owned card: canonical `Purchase` and
`Approved`, exact persisted amount and currency, and a trimmed, case-insensitive
exact merchant match. The window starts inclusively 180 days before the selected
charge and ends exclusively at that charge's timestamp. Results are newest first,
with transaction ID as the deterministic tie-breaker; the full matching count and
`matchesTruncated` distinguish the three-record display limit from query coverage.

The response declares comparison boundaries, available fields, missing-field count
and a masked card reference. `queryComplete` describes the bounded query only;
`historyCoverage: INSUFFICIENT_HISTORY` explicitly retains uncertainty about the
loaded source window. No matches means no matching available record in that window,
not a first-ever purchase. Matching uses database trim/lower semantics; synthetic
SQLite tests do not establish PostgreSQL normalization parity.

Assistance is optional and must never delay a requested report. Similar purchases
are clues, not proof of authorization, legitimacy, fraud or a subscription. Intake
stops only when the customer explicitly recognizes the charge **and** chooses not
to report; otherwise existing eligibility and consent continue without rewriting
the customer reason. English tool/instruction metadata preserves profile-driven
final-response localization.

Recognition alone creates no case, events, consent, financial effects or protection.
Provider conversations and traces remain separate records. The approved conversation
snapshot extension below preserves visible recognition assistance only when the
customer explicitly accepts intake with browser-provided history.

## Customer-provided case conversation

REST intake accepts optional `conversationHistory` containing at most 100 visible
user/assistant messages and 100,000 total characters. Malformed or oversized history
is rejected, not silently truncated. The snapshot is saved atomically with case,
consent and routing; retries cannot overwrite it. It is customer-provided evidence,
not an authoritative Foundry trace, and excludes hidden reasoning and tool state.

`GET /api/support-cases/{case_id}/conversation` requires customer ownership.
`GET /api/operator/support-cases/{case_id}/conversation` requires the exact assigned
operator. Both return `source: CUSTOMER_PROVIDED` and `messages`; authorized legacy
or direct cases without a snapshot return an empty list. No public queue history
or administrator bypass is introduced.

The owning customer can also select these read-only snapshots in the frontend Help
history, independently of temporary session chats. A snapshot survives logout but is
bounded to visible messages submitted at accepted intake, not a complete conversation:
older omitted messages and post-intake exchanges are not appended. It does not restore
a Foundry checkpoint or enable chat continuation. General chats without accepted case
intake have no durable archive here. No Cosmos export or BFF database access is used.

Revision 0012 moves the four existing support-domain tables into `support` and adds
`support.case_conversations`, registered in Shared SQLModel metadata. Banking and
Identity tables remain in the default schema. The BFF stays DB-free. See
[Data migration prerequisites](../data/README.md#migration-prerequisites). Schema
migration, browser validation and hosted acceptance are separate gates.

Focused offline validation:

```powershell
$env:OTEL_SDK_DISABLED = "true"
rtk proxy uv run --directory app\business-api\transaction python -m pytest tests\test_recognition.py tests\test_authorization.py tests\test_dispute_replay_contract.py -q
rtk proxy uv run --directory app\agent python -m pytest tests\test_hosted_workflow.py tests\test_settings.py -q
rtk proxy uv run --project evals --extra offline --frozen python -m pytest evals\tests -q --tb=short
```

Historical focused results: **46 Transaction tests**, **74 agent tests** and
**71 combined offline replay/eval tests passed**. Evals now runs in its independent
offline environment; the latest full Evals run passed **52 tests** using
`--extra offline --frozen --no-sync`, including package-aware DTO loading.
Replay freeze revision 7 reviews the new tool, instructions and package-aware
DTO dependency graph;
scenario bytes and expanded inputs remain unchanged. The owner confirmed browser
recognition demonstrations with similar purchases and without matches, plus case
history display, customer isolation and session switching. These are limited owner
observations, not an exhaustive recognition, operator-negative or locale matrix.
Real-model clue/locale quality, PostgreSQL read parity and hosted acceptance remain
open. These checks do not prove reduced operator workload.

## Customer dispute proposal and consent

New intake is a read-only proposal followed by explicit consent to create a case
and request review. Identifying a charge is not consent. Both the direct action
and customer chat use the same service contract:

| Route                              | Request                   | Result                                                                                                                  |
| ---------------------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `POST /api/support-cases/preview`  | `transactionId`, `reason` | HTTP 200 proposal with `previewToken`, `transactionId`, `reason`, `expiresAt`, `transaction`; no case or events written |
| `POST /api/support-cases`          | `previewToken`            | HTTP 201 persisted case with consent and routing recorded atomically; new case enters `IN_REVIEW`                       |
| `POST /api/support-cases/recovery` | `previewToken`            | HTTP 200 accepted case or null; read-only recovery                                                                      |

MCP equivalents are `previewTransactionDispute(transaction_id, reason)`,
`reportTransactionDispute(preview_token)` and `recoverTransactionDispute(preview_token)`.
The retired root request body containing only transaction/reason returns HTTP 422.
Every REST request still checks current Identity; services enforce customer ownership.

The stateless signed proposal binds customer, transaction, reason and persisted
evidence. Acceptance expires after ten minutes; recovery is bounded to 24 hours
from issuance. Tokens are transport values, never visible assistant prose, and
integrity alone is not proof of human consent. No new configuration or migration
is required: signing uses the existing JWT key with a dedicated audience, and the
proposal nonce determines the accepted case ID. Acceptance rechecks ownership,
card eligibility, the real-clock 365-day window and evidence under locks. Stale
proposals must be replaced and consent obtained again.

Repeated acceptance recovers the same case without reopening a terminal case.
Ambiguous transport failure uses read-only recovery, not blind resubmission; a null
recovery result is not proof that an in-flight write failed. Declining a proposal
writes no SupportCase or event. Existing persisted `WAITING_USER_APPROVAL` cases
retain their approval/decline path and historical audit; new cases require no
second review consent. Review is not adjudication, compensation or card protection.

Customer transaction context includes optional country/city only from owned
persisted records, never staff fraud signals or invented location. Visible labels
use profile-driven en/es/pt catalogs while canonical transport values stay unchanged.
The preview's `Card` label is `Card`/`Tarjeta`/`Cartão`. Spanish display and generated
agent wording consistently use `reclamo`/`reclamos` with masculine grammar. Canonical
English tool names, keys and codes, Portuguese dispute terminology, customer-entered
reasons and original audit text remain unchanged. See the
[frontend localization guide](../../frontend/banking-web/README.md#localization).
After application-recorded acceptance, chat reads the supplied case with
`getSupportCase` and acknowledges the confirmed receipt instead of creating again.
Only confirmed persistence/readback permits a successful case receipt.

The owner subsequently reported the feature working. The localization follow-up
passed 53 focused frontend tests and 80 focused agent tests; exact commands and
limitations are in the linked frontend and [agent guide](../../agent/README.md#pre-intake-consent-and-localized-context).
This observation does not complete the browser or real-model acceptance matrix.

Offline validation of this slice: Transaction **278 passed, 7 skipped** (one existing
warning); focused agent suites **80 passed** (24 SDK deprecation warnings); nine
frontend suites **183 passed**, lint **0 errors/14 existing warnings**, build passed
with its existing bundle-size warning. Scripted-model tests exercise actual SDK
session/tool continuation, not real-model consent semantics. Vite build does not
prove clean TypeScript diagnostics. Browser, hosted transport, real-model locale
quality, approved-data parity and PostgreSQL acceptance contention remain separate
unexecuted gates for this change.

## Operator queue and takeover

Every operator request validates the HS256 application JWT and calls Identity's
`POST /internal/introspect` to verify current active status, role and identity version.
The existing `JWT_SECRET_KEY`, `JWT_ISSUER`, `JWT_AUDIENCE`, `AUTH_USERS_ENDPOINT` and
`AUTH_INTERNAL_SECRET` configuration applies. Only `operator` identities are allowed;
customers, administrators and agent identities cannot use these routes.

| Route                                                              | Result                                                                          |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| `GET /api/operator/support-cases?view=available&offset=0&limit=50` | Unclaimed, customer-consented `IN_REVIEW` queue (default view)                  |
| `GET /api/operator/support-cases?view=assigned&offset=0&limit=50`  | Cases assigned strictly to the authenticated operator, including resolved cases |
| `GET /api/operator/support-cases/{case_id}`                        | Assigned operator's own case detail only                                        |
| `POST /api/operator/support-cases/{case_id}/claim`                 | Exclusive claim; empty request body; returns owned detail                       |

Both views permit nonnegative offsets and limits from 1 to 100. Omitting `view`
selects `available`; `assigned` filters only by `assigned_operator_sub` matching the
verified principal's `sub`, without status or consent restrictions. Claiming removes
a case from `available` and makes it discoverable in its owner's `assigned` view.
Results are ordered by opening time and case ID, not by an invented risk-priority policy.
The page contains `items`, `total`, `offset`, `limit`. Each item contains `caseId`,
`status`, `openedAt`, `updatedAt`, `triageOutcome`, `claimVersion`; customer IDs,
reasons, product/transaction IDs and financial values are omitted.

Owned detail adds `assignedOperatorSub`, `claimedAt`, `reason`, `transactionId`,
`productId`, `events`, and nullable `customerName`. The name is read from the case's
current persisted Customer record: trimmed nonempty first/last names are joined with
one space; missing customers or blank names return `null`. It is current metadata,
not immutable transaction evidence, and never appears in queue summaries. The same
owned-detail contract applies to claim and subsequent action responses; name changes
do not modify evidence snapshots or case/evidence versions.

The operator UI displays localized transaction-status badges (Approved green,
Declined red, Pending amber, Reversed gray) using canonical values for styling.
Stored fraud scores use an accessible continuous green–amber–red scale from 0 to 100,
with numeric text; missing or invalid values remain unavailable. Colors introduce no
risk bands or probability interpretation and do not change routing or verdict rules.

Events contain `eventId`, `eventType`, `actor`, `message`,
`createdAt`, `operatorSub`, `operatorIdentityVersion`, `claimVersion`.
Event messages preserve original audit text, including historical simulated wording;
that wording is not evidence of human investigation, settlement or product protection.
Owned historical detail remains accessible after a status change.

Errors use `detail.code`: `401 AUTH_REQUIRED` (with `WWW-Authenticate: Bearer`),
`403 OPERATOR_REQUIRED`, `503 SERVICE_UNAVAILABLE`, `404 CASE_NOT_FOUND` for missing,
unclaimed or foreign detail, and `409 OPERATOR_CLAIM_CONFLICT` for unavailable claims.
Repeating a successful claim also returns `409`; no reassignment is supported.
Invalid `view` values or pagination parameters return HTTP `422` validation errors.

A conditional SQLAlchemy update performs eligibility checking and ownership assignment
atomically. The claim timestamp/version and `OPERATOR_CLAIMED` event commit together;
audit failure rolls back the claim. Consent requires a customer `APPROVAL_GRANTED`
event. Archived `legacy_assigned_agent_id` neither grants nor blocks operator authority.
New reviews no longer assign synthetic ServiceAgent reviewers. Stored fraud-score
routing is retained; missing scores still escalate without estimating a score.
Taking over does not resolve a case or change balances. Assigned operators can then
adjudicate the consented case through the separate versioned endpoints below.
Reassignment, new login and conversational specialists remain out of scope.

## Adjudication and recorded financial effects

All paths below use `/api/operator/support-cases/{case_id}` and return owned detail.

| Method and suffix       | JSON request fields                                                                                                                             |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /adjudicate`      | `verdict` (`valid` or `invalid`), nonblank `rationale`, `expected_case_version`, `expected_evidence_version`, optional `destination_product_id` |
| `POST /effects/retry`   | `expected_case_version`, optional `destination_product_id`                                                                                      |
| `POST /card-protection` | `expected_case_version`, nonblank `rationale`, `blocked`                                                                                        |

Only the persisted assigned operator can act. A verdict is accepted exactly once on
`IN_REVIEW`. Invalid closes as `RESOLVED_INVALID` without posting. Valid first commits
`PENDING_EFFECTS`; execution then atomically records the full positive original amount
and currency, actual-time linked refund movement, runtime balance delta, audit and
`RESOLVED_VALID` closure. Failed effects remain pending and retry never creates another
verdict. Lifetime entitlement is unique per original transaction across cases; active
intake is also protected by a database unique index. Legacy `RESOLVED` does not retrocredit.

Debit-card restitution targets an owned active same-currency Savings Account, with
Checking Account fallback only when no eligible Savings exists. Exactly one preferred
choice can be selected automatically; multiple choices require an explicit destination
from that tier. No card/account relationship is inferred and no extra consent is added.
Credit-card restitution uses a negative delta against positive outstanding-debt balances.
Source balances and original movements remain ingestion anchors: separate runtime
postings preserve deltas when source data refreshes, and generated refunds appear in
normal Transaction reads without entering source-load verification or monthly snapshots.

Owned detail adds `caseVersion`, `evidenceVersion`, `evidence`, `verdict`, `rationale`,
`eligibleDestinations`, `effects`, `effectCode`, and `cardProtection`. Effects expose
`movementId`, `destinationProductId`, `amount`, `currency`, `balanceDelta`, `executedAt`.
Pending codes include `DESTINATION_REQUIRED`, `DESTINATION_UNAVAILABLE`,
`DESTINATION_NOT_ELIGIBLE`, `EVIDENCE_CHANGED`, `EVIDENCE_UNAVAILABLE`,
`CREDIT_ALREADY_APPLIED`, and `EFFECT_EXECUTION_FAILED`. Conflicts return HTTP 409 with
controlled `detail.code`; stale clients must reload before sending new versions.

Card protection is an independent, rationale-audited local product overlay preserving
prior source status. Verdicts neither block nor unblock cards. It is not external
payment-processor enforcement; bank-account-only cases cannot use this command.
Operator detail also exposes nullable `productProtectionStatus`: the actual source
product status with `Blocked` overlaid only for recorded local protection, never the
transaction evidence status or an assumed Active state.

Customer owned list/detail retains `financialEffectsStatus` and `cardProtectionStatus`
and adds `caseVersion`, nullable `verdict`, `rationale`, `effectCode`, `effects`, and
`cardProtection`. The effects object uses the same six fields as operator detail;
amount and signed balance delta are Decimal strings. Protection exposes `blocked`,
nullable `priorStatus`, `rationale`, cause `caseId`, ISO `updatedAt`, and fixed
`scope: LOCAL_PRODUCT_ONLY`. The current product-scoped protection record can originate
from another case on the same card. Missing records remain null; legacy/invalid status
never invents financial effects. Customer reasons and stored audit text remain unchanged.
Customer `/resolve` is retired and always
returns HTTP 403 `OPERATOR_ADJUDICATION_REQUIRED` after customer authentication.

## Schema prerequisite

Apply the data module's Alembic migrations through revision `20261004_0011` before
running this version. Revision 0009 adds claim/time/version/audit fields; revision
0010 binds nullable real ownership to `operators.user_id` with deletion restricted.
Claim requires that persisted Operator as well as current Identity introspection;
missing local persistence returns `503 SERVICE_UNAVAILABLE` without assigning ownership.

Revision 0010 archives the catalog as `legacy_service_agents`, copies former optional
operator mappings to `legacy_operator_service_agents`, removes that live mapping,
and renames historical case assignment to `legacy_assigned_agent_id`. Original audit
text and genuine ownership are preserved; simulated IDs are never converted to real
ownership. Orphan real owners fail migration preflight. Downgrade is deliberately
refused to preserve history.

Revision 0011 adds versioned evidence/verdicts, runtime postings, generated-movement
linkage and local card protection. Upgrade refuses existing duplicate active disputes;
downgrade refuses discarding recorded financial evidence. Migration 0011 requires
separate reviewed local execution; source and synthetic tests do not prove rollout.

## Validation

From the repository root:

```powershell
rtk proxy uv run --directory app\business-api\transaction python -m pytest tests -q
rtk proxy uv run --directory app\business-api\data python -m pytest tests\test_operator_claim_migration.py tests\test_identity_migrations.py tests\test_service_agent_retirement.py tests\test_legacy_manifest_verification.py -q
```

`test_operator_postgres.py` is an opt-in PostgreSQL concurrency check. It requires both
`OPERATOR_TEST_ALLOW_WRITES=1` and an externally supplied `OPERATOR_TEST_DATABASE_URL`
pointing at an explicitly authorized disposable test database, never production.
It creates a uniquely named schema with synthetic records and drops that schema on
completion. Two competing sessions must produce exactly one winner, one controlled
conflict and one claim audit, with no resolution. Repeated claims by either operator
must conflict without changing timestamps, version or audit identity. Failure injection
after audit flush and before commit checks rollback of ownership, timestamps, version
and audit, preservation of customer consent, and a subsequent successful claim by the
other operator. Listeners are attached only to the failing session. It does not load
credential files.

```powershell
rtk proxy uv run --directory app\business-api\transaction python -m pytest tests\test_operator_postgres.py -q
```

Without explicit configuration these tests skip. SQLite regressions do not prove
PostgreSQL concurrency or deployed end-to-end identity transport. A subsequent

### Accepted-consent PostgreSQL checks

[`test_consent_postgres.py`](tests/test_consent_postgres.py) requires both
`CONSENT_TEST_ALLOW_WRITES=1` and an externally supplied
`CONSENT_TEST_DATABASE_URL`. Obtain explicit local write authorization first.
The fixture rejects non-PostgreSQL and non-loopback targets before connecting;
it never loads credential files itself. Each test creates and drops only its
uniquely named synthetic schema, with bounded connection, lock and statement waits.

With those variables supplied in the terminal, run from the repository root:

```powershell
rtk proxy uv run --directory app\business-api\transaction python -m pytest tests\test_consent_postgres.py -q --tb=short
```

The ten cases cover coordinated independent connections through service, REST and
MCP acceptance, same/distinct proposals, ownership-protected recovery, lost responses
after commit, two precommit rollback points and resolved-transaction reclaim.
Durable assertions check one case/consent/review transition and no posting, protection
or balance change. Without both variables, these tests skip.

Authorized local execution passed **10 tests** on PostgreSQL **13.0** at
**READ COMMITTED**; a repeated run passed again and verified no newly created test
schemas remained. The target reported Alembic revision `20261005_0012`, but fixture
tables are created from SQLModel metadata: this is not migration-execution evidence.
REST/MCP tests substitute synthetic identity extraction, not business persistence.
These results do not prove live Identity/BFF authentication, browser/model behavior,
hosted transport, financial settlement or approved customer-data parity. Existing
focused offline dispute/consent regressions passed **82 tests**, with these ten
PostgreSQL cases skipped when authorization variables were absent.

Transaction CI explicitly excludes this local-only module through `PYTEST_ADDOPTS`,
even if database authorization variables are present in the job environment.

The operator-effects implementation was validated with the following suites:

```powershell
rtk proxy uv run --directory app\business-api\transaction python -m pytest tests -q --tb=short
rtk proxy uv run --directory app\business-api\account python -m pytest tests -q --tb=short
rtk proxy uv run --directory app\business-api\data python -m pytest tests -q --tb=short
```

Results: Transaction **230 passed, 7 skipped**; Account **85 passed, 1 skipped**;
Data **118 passed**. A separately authorized loopback PostgreSQL run passed all
**6** isolated concurrency tests, including effects retry, lifetime entitlement
and concurrent active intake. These results do not prove migration 0011 rollout,
browser acceptance or hosted identity transport.

A read-only local persisted-data check found 100,102 Credit Card product balances:
97,939 positive, 2,163 zero, none negative or missing. Credit Card restitution
subtracts the original amount from the stored balance through the runtime ledger.
This confirms the sign representation, not external settlement or issuer enforcement. A subsequent
user-authorized localhost PostgreSQL run of `test_operator_cases.py` and
`test_operator_postgres.py` passed **18 tests**, including all three PostgreSQL
cases. Connection configuration was loaded internally and verified as loopback;
only uniquely named synthetic schemas were written. Read-only cleanup verification
found **0 remaining operator test schemas**. This verifies service-level contention,
repeat conflicts and rollback/recovery, not runtime Identity checks, browser
acceptance, claim-versus-customer-resolution races or final-verdict authority.

Separately authorized local PostgreSQL execution on 2026-10-04 upgraded revision
`20261003_0008` to `20261004_0010`. Backup integrity and read-only preservation checks
passed: original business rows, five cases, 24 events, legacy catalog/mappings and
Operator ownership constraints were retained. One concurrent login added an identity
audit; all 62 original audit records were verified unchanged. Restore rehearsal and
remote rollout were not performed. See the [data guide](../data/README.md#schema-and-ownership).

After assigned-list integration, the focused `tests\test_operator_cases.py` run
passed 13 tests. This is synthetic service coverage, not authenticated browser
acceptance or operator-verdict authority.

The extended rollback regressions were verified locally from the repository root:

```powershell
$env:OPERATOR_TEST_ALLOW_WRITES = "0"
Set-Location app\business-api\transaction
& .\.venv\Scripts\python.exe -m pytest tests\test_operator_cases.py tests\test_operator_postgres.py -q
```

Result: **15 passed, 3 skipped**. The PostgreSQL cases were intentionally disabled;
this verifies the offline rollback/recovery checks and collection, not PostgreSQL
execution. The existing FastAPI/Starlette test-client deprecation warning remains.
