# Architecture of the Banking Assistant Prototype

## Bird's Eye View

This is an AI-first banking customer-service prototype built for the Factored AI &
Data Hackathon 2026, extending the public
`Azure-Samples/agent-openai-python-banking-assistant` sample. It implements one
coherent workflow: Account/Transaction inquiries through a conversational agent, with
a single persisted support-case layer on top for transaction disputes.

The system combines a React frontend, a database-free Responses BFF that owns the
browser trust boundary, dedicated Identity for credentials and lifecycle, a Responses
agent (Microsoft Agent Framework `HandoffBuilder`, served through Foundry Responses)
for triage and conversation, and two FastMCP business services (Account, Transaction)
for financial data and business rules. PostgreSQL is the only operational data store;
Terraform defines the five-site App Service stack separately from the hosted agent.

Banking login uses application JWTs; a separate OAuth authorization-code flow delegates
customer MCP access. Local execution uses direct MCP; the hosted OAuth Toolbox target
is explicitly blocked until native caller-to-customer grant binding is proven.

The one deliberate architectural split to internalize before changing anything: the
BFF is scoped to identity and chat-proxying only. The browser calls Account and
Transaction directly over REST for every financial read (accounts, cards,
transactions, support cases); it never goes through the BFF for those. The BFF is
only in the loop for allowlisted login/profile/admin facades and the customer
Responses chat stream. Dedicated Identity owns credentials, profiles and lifecycle;
the BFF has no database connection.

## Code Map

### `app/frontend/banking-web/`

React + Vite + shadcn-ui. `src/api/` holds `authClient.ts` (BFF login/profile),
`financialClient.ts` (Account/Transaction direct reads), `disputeClient.ts`
(customer support cases), and `operatorDisputeClient.ts` (operator queue/detail,
exclusive claim, versioned verdicts, effects retry and card protection). Transaction clients call its absolute `VITE_*_API_URL`
origin directly with the application JWT, never through a dev-server proxy.
`src/pages/` holds one page per screen, including `SupportCases.tsx`,
`SupportCaseDetail.tsx` and role-guarded `OperatorCases.tsx`. `src/locales/` holds the static `en`/`es`/`pt` UI catalogs
consumed by `UiLocaleProvider`. `src/components/DisputePreviewConsent.tsx` shares
proposal context, accept/decline and bounded recovery between `ReportDisputeDialog`
and the chat `DisputePreview` widget. Legacy `DisputeConsent` handles only persisted
pending-case decisions; generic MCP permission controls remain separate.
`src/api/oauthClient.ts`, `src/pages/OAuthConsent.tsx` and `OAuthCallback.tsx` own
explicit access consent and single-use callback completion. `OAuthReadinessContext`
shares session-bound status across chat controls; `required` is not the inverse of
`ready`. `src/components/chat/ResponsesChatProvider.tsx` owns streaming, welcome
prompts, continuation and uncertain-turn recovery. Browser storage never holds MCP grants.

### `app/responses-bff/src/bff/`

`main.py` wires FastAPI; `routers/auth.py` and `routers/admin.py` front allowlisted
Identity operations. `identity/authentication.py` checks current active identity/version
without caching. `routers/responses.py` proxies customer-only SSE; `identity/conversation.py`
and `identity/responses.py` bind continuation to the verified user and upstream.
`identity/internal_identity.py` signs the envelope the agent re-verifies;
`clients/credentials.py` obtains Azure credentials for hosted access.
`routers/oauth.py` and `identity/oauth_client.py` own single-use state, S256 exchange
and bounded process-memory grant custody, including capacity reservations during
in-flight token exchange. Restarts/replica changes require fresh consent;
there is no durable distributed grant store. The package never queries PostgreSQL or
verifies passwords. ADR 0005 documents direct frontend financial API calls.

### `app/business-api/identity/src/identity/`

Dedicated FastAPI identity service: Argon2 login, HS256 issuance, customer/operator/
admin profiles, fixed-role operator management, active/inactive lifecycle, identity
version and atomic audit. Access JWTs default to 60 minutes; refresh sessions and
renewal modals are deferred. An explicit seed service bootstraps an admin from external
credentials; it never runs at startup. Protected introspection uses a separate
`AUTH_INTERNAL_SECRET`. Staff have no `customer_id` and cannot use customer
financial REST or customer chat; operator-role review endpoints are a separate
boundary. ADR 0006 documents dedicated staff/customer identities.
`oauth_routes.py` exposes the Authlib provider implemented in `services/oauth.py`:
fixed confidential clients, exact HTTPS callbacks, mandatory S256, explicit Allow/Deny
and RS256 MCP access tokens signed with `joserfc`. Authorization codes and BFF state
expire after 300 seconds; MCP access tokens last 3600 seconds. No refresh tokens
are implemented. `services/oauth_signing.py` preserves local PEM signing and provides
remote signing through a version-pinned, non-exported RSA key in Azure Key Vault.
`services/oauth_provisioning.py` explicitly registers or verifies identical active
clients, never at startup. `services/oauth_remote.py` owns approved migration,
OAuth initialization and client-registration commands using Key Vault credentials
in memory; initialization never applies migrations. OAuth issuance checks current
customer profile/version; it does not replace banking login or authorize dispute creation.

### `app/agent/src/app/`

Root `app/agent/main.py` serves native `ResponsesHostServer(create_agent)` locally
and hosted, with a per-request workflow factory and a 100-item history boundary.
`agents/azure_chat/hosted_workflow.py` builds the single `HandoffBuilder` workflow;
`account_agent.py` and `transaction_agent.py` own specialist definitions. The host
reuses `FoundryChatClient` instances by model deployment, not mutable workflow state.
`tools/` holds direct MCP client wiring; `adapters/runtime_tools.py` selects Foundry
Toolbox allowlists and explicitly rejects its unverified OAuth passthrough.
`common/internal_identity.py` verifies signed BFF identity and supplies the selected
MCP bearer. `context/user_profile_provider.py` injects authenticated profile/locale.
Transaction intake requires separate preview/consent; existing cases use readback.
The ZIP installs `src/app` noneditably and starts root `main.py`; package `routers/`
is not the Responses host entrypoint. History, checkpoints and mutation recovery
have separate contracts.

### `app/business-api/account/src/banking_account/` and `app/business-api/transaction/src/banking_transaction/`

Both expose `mcp_tools.py` (agent-only tools via `auth/internal_identity.py`'s
explicit OAuth/HMAC mode), `routers/` (browser REST via `auth/jwt_identity.py`),
`services/` (business logic and resource ownership), `projections/` (read DTO
assembly), and `models/` (Pydantic DTOs, not canonical tables).
Transaction's `services/disputes.py`, `consent/preview.py` and `routers/disputes.py`
own customer proposals, consent, routing and `/api/support-cases`.
`services/operator.py` coordinates claims, verdicts and recorded effects;
`services/adjudication.py` applies effects without owning commits.
`routers/operator.py`, `auth/operator_identity.py` and `models/operator.py`
define the separate operator-role REST boundary and DTOs.
Both REST boundaries validate application JWTs and check current Identity; customer
ownership and operator assignment/version rules remain in service code. Read-only preview precedes explicit consent; signed-token
acceptance atomically creates the case, consent and routing in `IN_REVIEW`, while
preview/decline create no case/events. Acceptance lasts ten minutes; read-only recovery
lasts 24 hours from issuance and never treats null as proof of a failed write.
Legacy `WAITING_USER_APPROVAL` cases retain their decision path. See the
[Transaction consent contract](app/business-api/transaction/README.md).
A score never resolves a case. [ADR 0008: Operator ownership without the service-agent catalog](docs/adr/0008-operator-ownership-without-service-agent-catalog.md)
defines exclusive claim as responsibility, not adjudication or financial authority.
[ADR 0009: Operator verdicts with recorded financial effects](docs/adr/0009-operator-verdicts-with-recorded-financial-effects.md)
defines the separate assigned-operator verdict and recorded-compensation boundary:
valid cases stay pending until actual financial effects succeed; debit compensation
uses owned savings/checking allocation, not an inferred card-account relationship.
Card protection is separately audited and does not imply processor enforcement.
Both services enable `CORSMiddleware` via `CORS_ALLOWED_ORIGINS`.

### `app/business-api/shared/src/banking_shared/`

Owns canonical SQLModel tables such as `Product`, `TransactionRecord`,
`RuntimePosting`, `CardProtection`, `SupportCase` and `SupportCaseEvent`.
Services and ingestion import these persisted types. Service `Account`, `Card`,
`Transaction` and `DisputeCase` models are separate Pydantic DTOs. Stored-field
changes start here and require data-module migrations; API-shape changes belong
in service DTOs. `models/` groups catalog, products, transactions, cases, effects
and historical tables; its initializer registers canonical metadata.
`database.py` owns PostgreSQL engine/session wiring, and `runtime.py` batches
runtime balance/protection projections without changing persisted anchors.
`identity_models.py` defines public Identity tables; `oauth_models.py` defines
clients, hashed authorization codes and hashed token records in schema `oauth`.
Support tables use schema `support`; banking/Identity tables retain public ownership.
OAuth foreign keys restrict deletion across clients and users; expiry columns are
timezone-aware. `oauth_jwt.py` and `oauth_identity.py` share pinned RS256/JWKS
verification with exact `mcp.access` scope, issuer and audience checks.

### `app/business-api/data/`

The CSV-to-PostgreSQL ingestion pipeline. `scripts/run_pipeline.py` is the only
normal entry point (EDA, scope approval, loading, verification, in that order).
`scripts/evaluate_fraud_threshold.py` is the offline baseline evaluation for the
dispute triage threshold. This module owns Alembic migrations and historical
assignment archives; canonical tables live in `shared/src/banking_shared`. Source-data
refresh must preserve runtime financial effects. The
[manual database operations workflow](.github/workflows/manual-database-operations.yaml)
invokes Identity's remote CLI to upgrade the selected branch checkout's single latest
Alembic head with this module's configuration. Development environment policy controls
allowed branches; automatic CD never migrates, seeds or registers clients.

### `infra/`

Terraform only (no Bicep in this repo). One Linux App Service plan, five App
Services (`identity`, `account`, `transaction`, Responses BFF, `web`), Log Analytics,
Application Insights, PostgreSQL Flexible Server, Key Vault, Blob Storage and a
Foundry account/project. Identity has a dedicated resource
and database-secret reference; consumers use its computed HTTPS hostname. Managed
identities receive secret-scoped access; BFF has no database setting/grant. Root azd
uses explicit service `resourceName` bindings, while standalone Identity CD uses
its name output. Terraform also owns the native OAuth RSA key, version-pinned Identity
signing settings and scoped operations-principal grants. The manual workflow authenticates
through Azure OIDC; Development currently shares its CD and operations principal, so
those grants do not establish a separate administrative identity. Key Vault access does
not establish PostgreSQL network reachability or SQL migration privileges.
Provisioning, remote DB grants, migrations and coordinated rollout remain separate
acceptance gates; Terraform declarations are not deployed-isolation evidence.

### `app/agent/azure.yaml` vs root `azure.yaml`

Two independent `azd` project roots: the root manifest provisions the App Service
stack via Terraform; `app/agent/azure.yaml` provisions the hosted Foundry agent
separately. Commands against the agent stack need `--cwd app/agent`.

### `evals/`, `.github/` and `.vscode/`

`evals/src/banking_evals/` is an independently installed evaluation package, with
held-out scenarios and a separate development-exposed dispute replay. Dataset/dependency
freeze contracts protect evidence integrity; offline checks do not prove real-model,
customer-authorization or hosted behavior. `.github/workflows/` owns CI/CD; root
`.vscode/` owns six-service local launch orchestration. F5 runs
`DEV - Full Stack Ordered` with `noDebug: true`, using the ordered full-stack task
without automatically opening a browser. `run-local-service.py` centralizes backend
console severity colors and per-project daily UTF-8 logs; frontend npm runs directly
without file logging. `AGENTS.md` owns shared rules
and focused checks; local Copilot notes complement it, not a second policy source.

## Cross-Cutting Concerns

- **Three trust paths, never conflated.** Application HS256 JWTs authorize REST
  and BFF requests with uncached, fail-closed current-Identity checks. Signed BFF-to-agent
  envelopes carry verified customer context and, in OAuth mode, the delegated grant.
  MCP verifies RS256 using pinned public JWKS without Identity introspection, or a fresh
  60-second HMAC bearer in explicit compatibility mode; invalid OAuth never falls back.
  MCP revocation/profile changes can lag until one-hour access-token expiry. Hosted Azure access
  is separate from customer identity; native OAuth Toolbox binding remains unverified.
- **Authorization lives in `services/`, not in prompts.** Every method that reads
  or acts on an `accountId`/`productId`/case id re-derives the owner from the
  verified identity and checks it against the resource before doing anything;
  no caller-supplied `customer_id` is ever trusted.
- **Tool schemas and agent instructions stay English-only.** Only the final
  response text is locale-driven, from the authenticated user's stored `locale`
  (`es`/`pt`/`en`, `en` fallback), injected once per request by a context provider,
  never inferred from the current message's language. Human-facing status labels
  are translated without changing canonical transport codes. Frontend catalogs map
  Card to Card/Tarjeta/Cartão; Spanish generated/display terminology uses
  `reclamo`/`reclamos` with masculine grammar. Customer reasons and original audit
  text remain verbatim; frontend catalogs never translate agent Markdown.
- **Dispute legitimacy is never an LLM decision.** `services/disputes.py`'s triage
  is a deterministic routing check against the dataset's precomputed `fraud_score`;
  missing scores stay in review without estimation. The agent's only job is intake
  (identify and confirm the transaction), read-only case consultation and routing.
  `services/operator.py` separately enforces available/assigned lists, owner-only
  detail, atomic exclusive claims, assigned/versioned verdicts, recorded effects
  and case-specific protection. Claim is not a verdict; valid classification stays
  `PENDING_EFFECTS` until compensation is recorded. Real ownership references
  `operators.user_id`; retired simulated assignments remain archives, not verdicts.
- **Testing is per-service, not repo-wide.** `pytest` (`pytest-asyncio`,
  `asyncio_mode = "auto"`) per Python service under its own `tests/`; `vitest` for
  the frontend. There is no single top-level test runner.
- **`uv` is the supported Python tool runner.** Use each service's project context
  with `uv run`/`uv sync`, either from its directory or with explicit
  `--project`/`--directory` options from the repository root.
- **Controlled diagnostics, not credential dumps.** BFF/Identity OAuth logs expose
  rejection categories, never codes, state, PKCE material, tokens or raw exceptions.
  Agent telemetry and BFF `config/tracing.py` support tracing; configured telemetry
  is not proof of deployed monitoring or successful execution. Local agent tasks explicitly
  enable sensitive GenAI message capture for debugging; ignored backend logs can contain
  conversation content. The local output adapter redacts common credential patterns,
  not arbitrary personal or sensitive content.

## Architectural Invariants

- OAuth MCP access consent never substitutes for business dispute consent and never
  permits replay of an uncertain mutation. Callback exchanges are single-use.
- No shared customer grant, browser-held MCP token, automatic OAuth refresh or native
  OAuth Toolbox passthrough is implemented. Proposed ADRs are not deployment evidence.
- The BFF never queries Account, Transaction, or PostgreSQL financial tables
  directly. If a future change adds that back, it reopens a closed migration
  ([ADR 0005: Direct frontend financial API calls](docs/adr/0005-frontend-calls-account-and-transaction-directly.md)) and needs
  the same explicit sign-off that closed it.
- `plan/` is gitignored and local-only. Nothing under it is ever staged, committed,
  or pushed, regardless of how much planning content accumulates there.
- Account and Transaction are the only conversational specialists.
  The transaction-dispute support case is a tracking/workflow layer over those two
  existing MCP tool surfaces, not a third `HandoffBuilder` participant.
- MCP tools and customer/operator routers stay thin: authenticate and role-gate
  through dependencies, validate input, delegate and shape responses. Services own
  customer-resource ownership, assigned-operator checks and business rules.
- The conversational model never invents an eligibility, fraud, or dispute-outcome
  rule; those are deterministic policy/threshold checks, with the model only
  explaining results already computed by code.
