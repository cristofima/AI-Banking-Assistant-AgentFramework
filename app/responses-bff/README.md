# Responses BFF

FastAPI database-free browser trust boundary for the banking assistant. It fronts
[Identity](../business-api/identity/README.md) through allowlisted authentication and
admin-operator routes, verifies application JWTs against current identity state, and
fronts the Responses agent. Identity alone verifies passwords and issues tokens.
The browser never receives Azure credentials. Financial reads go directly to Account
and Transaction with the same application JWT; staff roles cannot use those reads
or customer chat.

## Package and ownership

The installed [bff package](src/bff/) resolves imports through installation, not
repository-root `PYTHONPATH`. [main.py](src/bff/main.py) preserves the `bff.main:app`
entrypoint and owns application/client/credential lifecycle.

- [routers](src/bff/routers/) contains the allowlisted auth/admin HTTP facades and
  Responses proxy composition.
- [clients](src/bff/clients/) owns Identity HTTP requests, Azure credential creation,
  and the single Responses send and upstream response cleanup.
- [identity](src/bff/identity/) owns validated identity/API contracts, JWT validation
  and uncached authentication dependencies, signed conversation binding, internal
  identity envelopes, and verified downstream headers.
- [config](src/bff/config/) owns settings and tracing configuration.

The Responses router forwards Responses events and appends an invisible signed
continuation control after a clean, completed stream. Effectful sends are never retried; pre-stream transport/credential failures
expose controlled 503 errors. Cancellation still propagates after streaming begins,
while cleanup closes the upstream response.

## Direct MCP OAuth delegation

The default `MCP_IDENTITY_MODE=hmac` retains existing chat behavior. Opt-in `oauth`
uses the BFF as a confidential authorization-code/S256 client, not as an additional
login provider. Identity still owns customer authentication, client registration and
RS256 token issuance. Browser REST authentication remains unchanged.

Customer-only routes in the [OAuth facade](src/bff/routers/oauth.py):

| Method | Route              | Behavior                                                                             |
| ------ | ------------------ | ------------------------------------------------------------------------------------ |
| GET    | `/oauth/status`    | Reports whether OAuth is required and a current session-bound grant exists.          |
| POST   | `/oauth/start`     | Returns a relative consent URL with server-generated state and S256 challenge.       |
| POST   | `/oauth/authorize` | Forwards explicit customer approval/denial, preserving the exact callback and state. |
| POST   | `/oauth/callback`  | Exchanges a matching, single-use code/state and returns readiness only.              |

All routes use uncached current-Identity checks and no-store responses. The
[confidential client](src/bff/identity/oauth_client.py) stores grants only in process
memory, bound to the browser bearer, customer/profile/version and agent endpoint.
It validates signature, issuer, audience and exact user/client claims before custody.
The browser never receives an MCP access token. Neither consent nor code exchange is
retried after uncertain failure. Pending states, stored grants and in-flight code
exchanges share a 1000-entry bound;
restart or another BFF replica loses readiness and requires authorization again.
There is no refresh or implicit shared grant.

Required externally supplied configuration: `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`,
`OAUTH_REDIRECT_URI`, `OAUTH_ISSUER`, `OAUTH_AUDIENCE`, `OAUTH_JWKS` (public JSON), plus
`MCP_IDENTITY_MODE=oauth`. The callback must be an exact registered HTTPS frontend URL,
including for local development; plain HTTP Vite is not a valid OAuth callback.
The issuer must be HTTPS. Exchange through `AUTH_USERS_ENDPOINT` requires HTTPS
except explicit HTTP loopback (`localhost`, `127.0.0.1`, `::1`). Supply these variables
in the process environment, for example through the documented `uv --env-file`
launch; the focused OAuth config does not independently read a dotenv file.
Keep the private signing key at Identity and the client secret at the BFF.

The existing signed BFF-to-agent envelope carries the current server-owned grant;
Foundry's Authorization header remains reserved for Azure credentials. Agent-to-MCP
calls use only standard OAuth Authorization, with no custom HMAC identity header.
Missing/expired grants fail closed; OAuth never falls back to HMAC. RS256 MCP
validation has a bounded revocation delay up to 3600 seconds (one hour).
The 1000-entry custody bound includes pending states, stored grants and in-flight
exchanges. Single-use states remain consumed after exchange failure or cancellation;
capacity reservations are released when the exchange finishes.
Authorization state still expires after 300 seconds. The exact `mcp.access`
scope permits existing customer MCP reads and dispute mutation tools only after
strict customer JWT verification. Legacy `banking.read` and operator/admin grants
are rejected. Service ownership, explicit dispute consent and uncertain-mutation
recovery remain required; OAuth consent never authorizes automatic replay.
See [ADR 0012](../../docs/adr/0012-local-oauth-jwt-validation-for-mcp.md)
for the local validation tradeoff and key distribution requirements.

This path uses direct MCP (`AGENT_RUNTIME=local`), even if the agent itself is hosted.
OAuth Toolbox routing remains blocked until native per-customer grant binding is
proven. Offline tests are not browser, deployed or real-model acceptance.

## Request Flow

```mermaid
flowchart LR
    Browser[Banking web] -->|Application JWT| BFF[Responses BFF]
    Browser -->|Application JWT| Account[Account API]
    Browser -->|Application JWT| Transaction[Transaction API]
    BFF -->|Allowlisted HTTP and current-state checks| Identity[Identity REST]
    Identity -->|SQLModel| DB[(PostgreSQL identity)]
    BFF -->|Signed verified identity| Agent[Responses agent]
    Agent -->|Short-lived bearer| MCP[Account and Transaction MCP]
```

Account, card, Dashboard, and Analytics reads call the
[Account and Transaction REST APIs](../business-api/README.md) directly over CORS,
each verifying the same JWT independently in its own `jwt_identity.py`. Conversational
inquiries use the agent and MCP services instead, authenticated through a separate
short-lived internal bearer; neither path replaces the other's service-layer
ownership checks.

## HTTP Contract

| Method | Route         | Behavior                                                                                         |
| ------ | ------------- | ------------------------------------------------------------------------------------------------ |
| POST   | `/auth/login` | Verify persisted email and Argon2 password hash; return bearer token and user profile.           |
| GET    | `/auth/me`    | Return verified identity and persisted customer name. Requires bearer JWT.                       |
| POST   | `/responses`  | Proxy Responses events with verified identity and user-bound conversations. Requires bearer JWT. |

JWT identity includes `sub`, `email`, `locale`, fixed `role`, and `identity_version`,
with `iss`, `aud`, `iat`, and `exp`; only customers have `customer_id`. Profile-only
`name`, `status`, and ISO `updated_at` are not JWT claims. Missing names return `null`.
Admin-only GET/POST `/admin/operators` and POST lifecycle routes (`activate`,
`deactivate`, `reset-password`) forward to Identity; the BFF never mutates a database.
Operator creation requires trimmed `first_name` and `last_name` (1–50 characters
each), email up to 120 characters, and a 12–256-character password. The legacy
creation `name` field is rejected. Password resets use the same password bounds.
The operator list preserves optional structured names for legacy profiles.
Admin-only GET `/admin/customers` and POST `/admin/customers/{user_id}/activate`
and `/deactivate` are explicit, body-free Identity forwards, checked against
uncached administrator identity on every request. They list existing customer users
and change sign-in access only, not banking customer status or financial data.
Customer creation, password reset, deletion, and generic Identity proxying are not
exposed. All browser authentication and administration stays behind this BFF.
An uncached, three-second Identity client validates active status and token version
for protected requests. Inactive/stale tokens return 401, wrong roles 403, and
Identity outages 503. Already admitted Responses streams are not cancelled, and
use a separate streaming client without that identity-request timeout. Account/card/transaction response shapes,
pagination, masking, and ownership rules are documented in the
[business-api README](../business-api/README.md) now that those reads live there
instead of in the BFF.

### Controlled Errors

Application-raised failures use `{"detail":{"code":"SERVICE_UNAVAILABLE"}}`
with an allow-listed code: `AUTH_REQUIRED`, `INVALID_CREDENTIALS`,
`SERVICE_UNAVAILABLE`, or `INVALID_REQUEST`. HTTP statuses and bearer challenges are
preserved. Framework validation responses can retain their standard shape; the
frontend treats unknown codes, raw details and non-JSON responses as safe local
fallbacks rather than displaying server text. Successful Responses streams are
unchanged.

## Local Development

Run from the repository root using the ignored service `.env`:

```powershell
rtk proxy uv sync --project "app\responses-bff" --frozen
rtk proxy uv run --project "app\responses-bff" --env-file "app\responses-bff\.env" uvicorn bff.main:app --port 8080
```

Keep `JWT_SECRET_KEY`, `JWT_ISSUER`, `JWT_AUDIENCE`, and
`AUTH_INTERNAL_SECRET` synchronized with Identity, Account, and Transaction.
Do not place `DATABASE_URL` or legacy `AUTH_USERS` credentials in the BFF environment.

Set `PROFILE=dev`, `RESPONSES_UPSTREAM_MODE=local`, and
`RESPONSES_AGENT_ENDPOINT=http://127.0.0.1:8088/responses` in that environment.
The root F5 `DEV - Full Stack Ordered` launch uses `noDebug: true` and ordered tasks
to start Identity HTTPS, both MCP services, agent, BFF and frontend without opening a
browser. Configure `AUTH_USERS_ENDPOINT=https://localhost:8091` for this stack and
trust the prepared localhost CA. The task output runner adds severity colors and
appends plain UTF-8 `logs/bff-YYYY-MM-DD.log` files; no automatic retention is configured.
The manual command above does not use that runner. See the
[root startup guide](../../README.md#run-locally). Restart the BFF after Python edits
when running without `--reload`.

## Configuration

| Variable                                | Purpose / default                                                                                 |
| --------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `AUTH_USERS_ENDPOINT`                   | Compatibility name for Identity endpoint; default `http://127.0.0.1:8090`.                        |
| `AUTH_INTERNAL_SECRET`                  | Protected Identity introspection key; distinct from downstream HMAC secret.                       |
| `JWT_SECRET_KEY`                        | Application HS256 signing key, at least 32 characters.                                            |
| `JWT_ISSUER`                            | `home-banking-api`                                                                                |
| `JWT_AUDIENCE`                          | `home-banking-web`                                                                                |
| `INTERNAL_IDENTITY_SECRET`              | Shared downstream identity signing secret, at least 32 characters.                                |
| `RESPONSES_UPSTREAM_MODE`               | `foundry` by default; use `local` for local browser validation.                                   |
| `RESPONSES_AGENT_ENDPOINT`              | Local default `http://127.0.0.1:8088/responses`; configure the approved upstream for hosted mode. |
| `RESPONSES_TOKEN_SCOPE`                 | `https://ai.azure.com/.default`                                                                   |
| `AZURE_CLIENT_ID`                       | Optional client ID for server-side Azure credentials.                                             |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Optional Azure Monitor trace and HTTP metric export; propagation works without it.                |
| `ALLOWED_ORIGINS`                       | JSON array; defaults to `["http://localhost:5170"]`.                                              |

Keep secrets in ignored environment files or protected deployment settings. Never log
passwords, password hashes, JWTs, or Azure credentials. `AUTH_USERS` is not a login
source. Identity owns credentials, persisted profiles, associations and audit; the BFF
has no ORM repository, database configuration or password hashing. Explicit first-admin
bootstrap requires separate database-write authority; see the [Identity guide](../business-api/identity/README.md).

The legacy [real-data verifier](scripts/verify_real_data.py) returns exit code 2 with
`LEGACY_BFF_VERIFIER_RETIRED`, without database access. Pure expectation helpers remain
tested, but do not establish live parity. Separately authorized Identity HTTP and
direct Account/Transaction REST checks remain required.

## Distributed Tracing

The BFF originates W3C trace context when the browser sends no tracing headers.
FastAPI creates the server span and the BFF's instrumented HTTPX clients inject
`traceparent` into Identity and agent requests. Valid incoming context keeps its trace ID and
`tracestate`; malformed context starts a new trace. A new trace normally has no
`tracestate`. No frontend instrumentation or extra CORS headers are required.

Trace and meter providers are reused per service without replacing global providers.
FastAPI records HTTP server duration; both HTTPX clients record dependency duration.
`APPLICATIONINSIGHTS_CONNECTION_STRING` enables asynchronous Azure Monitor export;
missing or invalid export configuration does not disable local propagation.
The service resource is named `banking-assistant-responses-bff`. Instrumentation does
not enable request-body, response-body, or authentication-header capture. Do not
enable header capture for Authorization or the signed internal identity headers.

Deterministic tests exercise outbound HTTPX instrumentation, concurrent trace isolation,
optional export, and Account/Transaction MCP context reception. The agent uses its hosting
SDK's instrumentation; context preservation through the deployed Foundry gateway and
correlated Application Insights spans still need a separate hosted validation.

## Validation and Limits

```powershell
rtk proxy uv run --directory app\responses-bff python -m pytest tests -q
```

Tests cover the database-free boundary, allowlisted Identity adapters, current-state
revocation and role isolation, authentication (`/auth/login`, `/auth/me`), and Responses
proxy behavior. Account,
card, and transaction contract tests moved to the
[business-api suite](../business-api/README.md) along with the endpoints themselves.
The suite also covers W3C propagation and export configuration.

Local and hosted modes use the same completed-response checkpoint chain. The BFF
signs the response ID, binding it to verified `sub`, upstream mode and endpoint.
The frontend retains this token per thread in tab-scoped session storage and sends it as `conversation`;
the BFF verifies it and sends only the decoded `previous_response_id` upstream.
Browser-supplied raw `previous_response_id` and `agent_session_id` fields are rejected.
The token is signed, not encrypted. JSON replies expose it through `X-Conversation-Id`;
SSE replies append an invisible `bff.continuation` event only after clean EOF.

Failures, cancellation or missing completed checkpoints block further sends on the
uncertain thread instead of silently resetting context or replaying effectful input.
A new thread starts a fresh chain. This transport safety rule is not business chat
closure. Same-login reload restores temporary frontend history and completed
continuation tokens; explicit logout/new login clears them. Help separately retrieves
owning-customer, read-only PostgreSQL intake snapshots through Transaction REST.
Those snapshots are associated with cases, not a general transcript archive, and
cannot resume provider execution. The BFF remains database-free. Legacy conversation
tokens are not accepted; failed-turn recovery and safe retry are not implemented.

The hosting SDK restores workflow state using `previous_response_id`; see the
[agent state guide](../agent/README.md#conversation-state). This follows Microsoft's
[local and hosted multi-turn guidance](https://learn.microsoft.com/azure/foundry/how-to/develop/framework-hosted-agents#multi-turn-conversations).
Deterministic tests cover local/hosted payload translation, JSON/SSE chaining,
customer/endpoint isolation and failure precedence. Browser and real-model hosted
checkpoint acceptance remain separate, unexecuted validations.

Hosted identity transport and deployed parity still require separate validation.
Public registration, self-service password reset, MFA, production database grants,
and real reviewer permissions remain unavailable. Admin operator password reset,
active/inactive status and token-version revocation are implemented in Identity;
synthetic coverage does not establish deployed acceptance.

## App Service Deployment

[BFF CD](../../.github/workflows/cd-responses-bff.yaml) reads the GitHub
`Development` Variable `AZURE_RESPONSES_BFF_APP_NAME`. Set it manually to the
physical App Service name from Terraform output `AZURE_RESPONSES_BFF_APP_NAME`.
Shared setup exports that value as azd `AZURE_RESPONSES_BFF_APP_NAME`, which the root
[manifest](../../azure.yaml) binds through `resourceName`. Preflight checks the
same named app; tags do not select it.

The BFF uses Identity endpoint settings and protected introspection, never
`DATABASE_URL`. Service URLs come from actual hostname outputs, not app-name
reconstruction. The hosted agent deploys separately from the agent project root.
See [workflow configuration](../../.github/workflows/README.md#required-github-environment-variables)
and the [infrastructure guide](../../infra/README.md). Configuration preflight does
not establish login, startup or hosted identity acceptance.

For zip packaging and dependency export, follow the
[root deployment artifact guide](../../README.md#python-dependency-artifact-for-app-service-zip-deploy).
