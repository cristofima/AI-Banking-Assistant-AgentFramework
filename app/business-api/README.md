# Banking Assistant Business API - Python FastMCP Services

A collection of Python-based FastMCP servers used by the Banking Assistant. Account and Transaction read PostgreSQL-backed dataset rows through the shared SQLModel package and expose inquiry tools through the Model Context Protocol (MCP).

## 🏗️ Architecture

This business API layer contains **specialized MCP servers** for different banking domains:

- **Account Service**: Active workflow dependency for account details, payment methods, and beneficiaries
- **Transaction Service**: Active workflow dependency for transaction history, search and persisted support cases. Read-only dispute proposals require explicit consent before atomic intake/review; see the [Transaction consent contract](./transaction/README.md#customer-dispute-proposal-and-consent).

Each service runs as an independent FastMCP server exposing banking tools through HTTP endpoints that the copilot agents can consume.

## App Service Deployment

Identity is a separate HTTP identity service, not an MCP specialist. It owns
persisted users and JWT issuance; see the [Identity guide](./identity/README.md).
Identity, Account and Transaction deploy independently through the root
[azd manifest](../../azure.yaml) to Terraform-provisioned App Services.

| Service     | GitHub `Development` Variable | Terraform output / azd variable |
| ----------- | ----------------------------- | ------------------------------- |
| Identity    | `AZURE_IDENTITY_APP_NAME`     | `AZURE_IDENTITY_APP_NAME`       |
| Account     | `AZURE_ACCOUNT_APP_NAME`      | `AZURE_ACCOUNT_APP_NAME`        |
| Transaction | `AZURE_TRANSACTION_APP_NAME`  | `AZURE_TRANSACTION_APP_NAME`    |

Set GitHub Variables manually from the physical name outputs. Shared CD exports
those values under the same `_APP_NAME` azd names; `resourceName` and preflight target the
same app, with no tag-discovery fallback. Identity reuses the shared `database-url`
Key Vault secret; the BFF remains database-free. Packaging does not migrate the
database or create users, and resolved references do not prove runtime readiness.
See the [workflow guide](../../.github/workflows/README.md#required-github-environment-variables)
and [infrastructure guide](../../infra/README.md) for configuration and rollout.

### Python dependency artifacts

OAuth JWT cryptography uses Shared's `joserfc` dependency; Authlib continues to
provide Identity/BFF OAuth protocol handling. Declared Authlib dependencies are
pinned to `1.9.1`, and Identity pins `python-multipart` to `0.0.32` to avoid
unreviewed resolver upgrades.

Each Python App Service installs runtime dependencies from its own `requirements.txt`.
Keep the service's `pyproject.toml` and uv lock as the development source of truth;
regenerate artifacts rather than editing them manually. From repository root:

```powershell
rtk proxy uv pip compile app\business-api\account\pyproject.toml --no-emit-package banking-shared --python-version 3.11 --python-platform x86_64-unknown-linux-gnu -o app\business-api\account\requirements.txt
rtk proxy uv pip compile app\business-api\transaction\pyproject.toml --no-emit-package banking-shared --python-version 3.11 --python-platform x86_64-unknown-linux-gnu -o app\business-api\transaction\requirements.txt
rtk proxy uv pip compile app\business-api\identity\pyproject.toml --no-emit-package banking-shared --python-version 3.11 --python-platform x86_64-unknown-linux-gnu -o app\business-api\identity\requirements.txt
rtk proxy uv export --project app\responses-bff --no-dev --no-hashes --no-emit-project --no-emit-package banking-shared --output-file app\responses-bff\requirements.txt
```

`--no-emit-package banking-shared` excludes machine-local editable paths. Root azd
packaging hooks stage each service's installed-layout package and the shared package
from their `src` directories into isolated deployment zips, then clean up those copies. Packaging does not migrate PostgreSQL, provision users, or verify
hosted startup. See the [BFF guide](../responses-bff/README.md) and
[Identity CI/CD guide](./identity/README.md#cicd) for their deployment contracts.

## 🚀 Quick Start

### Prerequisites

- Python 3.11 or higher
- [uv](https://docs.astral.sh/uv/) package manager
- Git

> [!WARNING]
> Each service has its own `.venv`. Select the interpreter in the service directory you are running (for example, `transaction/.venv/Scripts/python.exe` on Windows).

> [!NOTE]
> Account and Transaction require `DATABASE_URL`, `INTERNAL_IDENTITY_SECRET` (MCP),
> and `JWT_SECRET_KEY` (browser REST) to start without a `503`. `CORS_ALLOWED_ORIGINS`
> defaults to `http://localhost:5170` if unset. Configure each service's own `.env`
> using its `.env.example`; see [Configuration](#configuration) below.

> [!NOTE]
> Docker deployment paths were removed along with the per-service Dockerfiles. Run the servers directly with Python or package them using the shared App Service workflow.

## 🔧 Service Setup

Each service follows the same setup pattern. Navigate to the specific service directory and follow below steps. Examples are shown for account service, make sure to adjust paths and environment variables for the transaction service:

### 1. Account Service Setup

Run from the repository root after configuring the service's own `.env`:

```powershell
rtk proxy uv sync --directory app\business-api\account --frozen --group dev
$env:PROFILE = "dev"
rtk proxy uv run --directory app\business-api\account --env-file .env python -m banking_account.main
```

The Account service will be available at: **http://localhost:8070**

---

### 2. Transaction Service Setup

Run from the repository root:

```powershell
rtk proxy uv sync --directory app\business-api\transaction --frozen --group dev
$env:PROFILE = "dev"
rtk proxy uv run --directory app\business-api\transaction --env-file .env python -m banking_transaction.main
```

The Transaction service will be available at: **http://localhost:8071**

## 🛠️ Available Banking Tools

Each service exposes two parallel surfaces that never share an auth mechanism:

- **MCP tools** (`mcp_tools.py`), mounted at `/mcp`, called only by the Responses agent.
  They authenticate with `internal_identity.py`'s short-lived HMAC bearer, minted
  fresh per request from the BFF-verified identity the agent receives in default HMAC
  mode. Explicit `MCP_IDENTITY_MODE=oauth` instead verifies Identity-issued RS256
  `mcp.access` access-token JWTs locally using `OAUTH_ISSUER`, `OAUTH_AUDIENCE`
  and public `OAUTH_JWKS` JSON. This mode never introspects opaque tokens or falls
  back to HMAC. Customer dispute operations retain ownership and explicit consent;
  legacy `banking.read` and staff grants are rejected. MCP revocation accepts the
  remaining 3600-second TTL delay; REST introspection is unchanged. See the
  [Identity OAuth contract](identity/README.md#optional-oauth-authorization-code-provider).
- **REST endpoints** in [Account routers](account/src/banking_account/routers/)
  and [Transaction routers](transaction/src/banking_transaction/routers/), mounted
  at `/api`, called directly by the browser. Their `auth` packages authenticate with `jwt_identity.py`'s
  `get_jwt_customer_id`, which validates the application JWT Identity issues through
  the BFF at login (HS256, `JWT_SECRET_KEY`/`JWT_ISSUER`/`JWT_AUDIENCE`) and verifies
  active customer status and the current identity version through protected Identity
  introspection. Operator review routes instead require `operator_identity.get_operator_principal`
  and current active-operator introspection; they do not require a customer claim.
  CORS is enabled via
  `CORS_ALLOWED_ORIGINS` (defaults to `http://localhost:5170`).

### Account Service (Port 8070)

MCP tools:

- **`getAccountsByUserName`** - Get all accounts for a specific user
- **`getAccountDetails`** - Get account details and available payment methods
- **`getRegisteredBeneficiary`** - Get registered beneficiaries for an account (unavailable for persisted products)
- **`getCreditCards`** - Validate the requested account's ownership, then list the customer's credit cards; this does not establish a card/account financial linkage
- **`getCardDetails`** - Get the details of a single credit card

REST endpoints (`/api` prefix):

- **`GET /accounts`** - List every bank account owned by the authenticated customer
- **`GET /cards`** - List every card owned by the authenticated customer
- **`GET /accounts/{product_number}/cards`**, **`GET /cards/{product_number}`**,
  **`POST /cards/{card_id}/recharge`**, **`POST /cards/{card_id}/pay`** - existing
  single-resource operations (recharge/pay are unavailable for persisted products)

### Transaction Service (Port 8071)

MCP tools:

- **`getTransactionsByRecipientName`** - Search transactions by recipient name
- **`getLastTransactions`** - Get recent transaction history for an account
- **`getCardTransactions`** - Get credit and debit card transactions
- **`previewTransactionDispute`**, **`reportTransactionDispute`**, **`recoverTransactionDispute`**, **`respondToDisputeApproval`**, **`listSupportCases`**,
  **`getSupportCase`**, **`getSupportCaseTimeline`** - transaction-dispute support-case
  workflow (intake/triage only; the agent never decides legitimacy)

REST endpoints (`/api/transactions` prefix):

- **`GET /{product_number}`** - Last 5 transactions, or filtered by `payment_type`/`transaction_type`/`card_product_number`
- **`GET /{product_number}/history`** - Owned bank-account history with inclusive `start_date`/`end_date`, `limit`, `offset`
- **`GET /products/{product_id}/history`** - Owned credit/debit-card history with the same date filters and pagination; the opaque product ID is not a card number

New disputes accept card transactions only; historical account cases remain readable.
No debit-card/account relationship is inferred from shared customer ownership.

Support-case REST endpoints at `/api/support-cases` (same JWT auth) expose read-only
`POST /preview`, root `POST` acceptance with `{previewToken}`, and read-only
`POST /recovery`. Preview and decline create no case/events; explicit acceptance
atomically persists intake, consent and routing. Existing pending cases retain
`POST /{case_id}/approval`; `POST /{case_id}/recommendation/dismiss` records the
customer's explicit opt-out. Customer `POST /{case_id}/resolve` is retired.
See the [Transaction consent contract](transaction/README.md#customer-dispute-proposal-and-consent)
for token expiry, revalidation and ambiguous-outcome recovery.

Accepted intake or legacy approval transitions every classification to `IN_REVIEW`. Low scores retain the
`fast_track` routing classification and emit `REVIEW_REQUIRED`, but no longer close
the case automatically or generate a resolution outcome/recommendation. High and
missing scores retain their escalation classifications. New cases do not assign a
simulated reviewer. The live ServiceAgent catalog is retired; archived assignments
remain historical evidence, never real operator ownership.

Operators use a separate current-operator JWT dependency for
`GET /api/operator/support-cases?view=available|assigned`, owner-only
`GET /api/operator/support-cases/{case_id}`, and bodyless
`POST /api/operator/support-cases/{case_id}/claim`. Available cases require customer
consent and unclaimed `IN_REVIEW` status; assigned cases belong only to the caller,
including resolved cases. Claim ownership, timestamp, version and audit commit
atomically. See the [Transaction guide](transaction/README.md) for pagination,
conflicts and authorization boundaries.

Consent and operator takeover authorize intake/review only, never a legitimacy
verdict, credit or card protection. Assigned-operator adjudication, recorded financial
effects and local card protection are separate versioned operations documented in the
[Transaction guide](transaction/README.md); intake does not execute them. Historical
resolved records remain unchanged. The 365-day intake window is unchanged and remains
independent of fraud-score triage.

### Port Configuration

Services use different ports based on the `PROFILE` environment variable:

| Service     | Development Port | Production Port |
| ----------- | ---------------- | --------------- |
| Account     | 8070             | 8080            |
| Transaction | 8071             | 8080            |

## Configuration

| Variable                                | Used by                        | Purpose / default                                                         |
| --------------------------------------- | ------------------------------ | ------------------------------------------------------------------------- |
| `DATABASE_URL`                          | Account, Transaction           | Shared PostgreSQL connection (via `banking-shared`).                      |
| `INTERNAL_IDENTITY_SECRET`              | Account, Transaction           | HMAC secret verifying the agent's short-lived MCP bearer.                 |
| `JWT_SECRET_KEY`                        | Account, Transaction           | Must match the BFF's signing key; verifies the browser's application JWT. |
| `JWT_ISSUER` / `JWT_AUDIENCE`           | Account, Transaction           | Default `home-banking-api` / `home-banking-web`, matching the BFF.        |
| `CORS_ALLOWED_ORIGINS`                  | Account, Transaction           | Comma-separated browser origins; defaults to `http://localhost:5170`.     |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Account, Transaction, Identity | Optional Azure Monitor trace and HTTP metric export.                      |

### Distributed Tracing

FastAPI instrumentation surrounds each service's REST routes and mounted `/mcp/`
application. It extracts W3C `traceparent` and `tracestate` from agent requests, or
starts a new trace for browser REST calls without context. Service resource names
are `banking-assistant-account` and `banking-assistant-transaction`. Export is optional;
propagation and server spans remain available without an Application Insights connection.
The setup does not enable body or authentication-header capture.
Explicit meter providers export HTTP server duration alongside server spans;
Identity uses the same setup with resource `banking-assistant-identity`.
Azure Monitor stores server spans as Requests and uses preaggregated HTTP metrics
for standard request charts. Logs in Traces are a separate signal: their presence
alone does not prove request ingestion. See the [Azure exporter guidance](https://learn.microsoft.com/en-us/python/api/overview/azure/monitor-opentelemetry-exporter-readme?view=azure-python).

Account and Transaction exclude only internal ASGI `send`/`receive` event spans,
retaining server request spans, HTTP status/error visibility and duration metrics.
In-memory tests verify MCP context reception, HTTP duration recording, and successful
and failed HTTP responses without ASGI event spans. The tracing tests temporarily
reenable the SDK for their injected in-memory providers even when the suite disables
telemetry. No external exporter is used by those tests. Correlation across the deployed
Foundry gateway and production ingestion-volume changes remain unverified. The App Service startup command must also trust
the controlled Azure proxy's forwarded scheme so `/mcp` redirects remain HTTPS;
the Terraform startup-command correction requires provisioning, not just zip deployment.

## 📁 Service Structure

Account and Transaction use installed packages, with responsibility-oriented
subpackages rather than flat application modules:

```text
service-name/
├── src/package_name/
│   ├── main.py             # FastAPI + FastMCP entry point
│   ├── mcp_tools.py        # Thin tool definitions
│   ├── models/             # API DTOs, separate from persistence tables
│   ├── routers/            # REST boundaries
│   ├── services/           # Business logic, ownership and orchestration
│   ├── projections/        # Runtime-to-DTO projections
│   ├── auth/               # Independent JWT and internal bearer contracts
│   └── observability/      # Tracing and logging
├── tests/
├── pyproject.toml
└── uv.lock
```

Transaction additionally owns `consent` proposal helpers and separate dispute,
operator and adjudication service modules. Shared owns canonical model families;
Identity owns provisioning services; Data owns ingestion, analysis and snapshot
packages. Keep package-qualified imports aligned with those responsibilities:
for example, `banking_account.services.products` and
`banking_transaction.services.disputes`. Install through each project's manifest;
do not add `sys.path` or `PYTHONPATH` workarounds. Checkout CLI adapters delegate to
the installed implementation. See the [architecture map](../../ARCHITECTURE.md) for
exact package paths and the [project convention](../../AGENTS.md#python-package-convention).

## 🔌 Integration with Copilot

The Banking Assistant Copilot connects to these services via MCP URLs configured in its environment:

```env
# MCP Server URLs (from app/agent/.env)
ACCOUNT_MCP_URL=http://localhost:8070
TRANSACTION_MCP_URL=http://localhost:8071
```

The copilot's active specialist agents use these tools to:

- **Account Agent**: Query account details and payment methods
- **Transaction Agent**: Search and retrieve transaction history

Account and Transaction MCP endpoints require a short-lived bearer created by the
Responses agent from BFF-verified identity. The [Account services](account/src/banking_account/services/)
and [Transaction services](transaction/src/banking_transaction/services/) enforce
`customer_id` ownership through persisted product relationships and transaction-row
filters before returning customer-owned resources. Keep those checks in the service layer;
tool descriptions and agent instructions are not authorization boundaries.

The browser calls Account and Transaction REST endpoints directly, authenticated with
the application JWT issued by [Identity](identity/README.md) through the
[Responses BFF](../responses-bff/README.md) at login. The DB-free BFF exposes
allowlisted identity/admin operations and customer Responses chat; it does not read
account, card, or transaction data from PostgreSQL.
Browser application JWTs are not substitutes for the internal MCP bearer, and the two
auth dependencies (customer/operator application-JWT dependencies for REST,
`internal_identity.get_customer_id` for MCP) are never interchangeable.

## 🐛 Development & Debugging

Launch the services individually or press `F5` with `DEV - Full Stack Ordered` to
start Identity, Account, Transaction, the local Responses agent, the BFF and Vite;
see the [root local-development guide](../../README.md#run-locally). F5 uses
`noDebug: true`: no debugger attaches and no browser opens automatically. Backend
output passes through the root local runner for colored severity labels and daily
UTF-8 files in each project's ignored `logs/<service>-YYYY-MM-DD.log`. Files append
across restarts and dates roll over while running; no automatic retention is configured.
The frontend runs npm directly without file logging. Credential-pattern redaction
does not remove arbitrary sensitive content; local agent message capture is enabled.

## Local inquiry verification

On 2026-09-30, user-supplied local browser evidence through the BFF confirmed an
owned-account answer and a foreign-account denial against the persisted-data path.
The foreign request called `getAccountDetails` with `product_number`; its tool
result was `ACCESS_DENIED`, followed by a visible assistant denial and a completed
Responses stream without foreign financial data. This is a tool-level denial,
not an HTTP 403 for the successful SSE transport.

Public lookup parameters use product numbers rather than internal product keys.
Bank numbers are displayed in full; card numbers are masked. Full Transaction chat,
missing/empty, multi-turn, and deployed authorization matrices remain pending.

Account and Transaction use loaded hackathon dataset rows, not live bank integrations.
The [data module guide](data/README.md) covers ingestion and demo identity seeding.
Production banking integration and deployment security controls remain outside this prototype.
