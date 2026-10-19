# Identity service

Dedicated credential and principal-lifecycle REST service. The Responses BFF remains
its browser facade; it has no database access. Identity owns Argon2 verification,
HS256 token issuance, persisted profiles, operator and customer-user lifecycle, and identity audit.
It does not own financial data, dispute verdicts, or a real reviewer queue.

## Optional OAuth authorization-code provider

OAuth is disabled until `OAUTH_ISSUER` (the public HTTPS Identity origin) and
`OAUTH_FRONTEND_AUTHORIZE_URL` (the HTTPS frontend consent page) are configured.
Local PEM OAuth also requires `OAUTH_AUDIENCE`, `OAUTH_SIGNING_KEY_ID` and externally
supplied `OAUTH_SIGNING_PRIVATE_KEY` (an unencrypted RSA PEM, at least 2048 bits).
Missing or invalid signing configuration fails closed with controlled HTTP 503.
No signing material is generated, read from a credential file, or persisted by Identity.
The issuer must have no query or trailing slash. Configure the same issuer/audience
on both MCP consumers and an audience distinct from `JWT_AUDIENCE`.
Credential-free configuration outline (supply private PEM externally, not in source):

```dotenv
OAUTH_ISSUER=https://localhost:8091
OAUTH_FRONTEND_AUTHORIZE_URL=https://localhost:5170/oauth/consent
OAUTH_AUDIENCE=banking-mcp
OAUTH_SIGNING_KEY_ID=
OAUTH_SIGNING_PRIVATE_KEY=
```

The [.env.example](.env.example) targets the local HTTPS stack but contains no
working credentials or signing material. Supply a nonempty signing key ID and private
RSA PEM externally; an ignored dotenv can store the PEM as a quoted multiline value
with actual line breaks. Clear both OAuth URL assignments for HMAC-only compatibility.
`OAUTH_CLIENT_SECRET` is only for the explicitly authorized provisioning CLI below,
not startup configuration. Local TLS preparation remains required; these values do
not configure certificates or prove client registration or OAuth readiness.

For Account and Transaction, configure `MCP_IDENTITY_MODE=oauth`, the same
`OAUTH_ISSUER`/`OAUTH_AUDIENCE`, and `OAUTH_JWKS` containing public JWKS JSON.
They never receive `OAUTH_SIGNING_PRIVATE_KEY`. Existing `AUTH_INTERNAL_SECRET`
and login JWT settings remain required for REST's separate introspection path.
Application login, HS256 tokens, roles and uncached identity checks remain unchanged.
OAuth uses Authlib authorization-code validation and S256 PKCE, not OIDC. Only
confidential, explicitly provisioned clients and the fixed `mcp.access` scope are
supported. There is no refresh token, offline access, password grant, dynamic client
registration or staff authorization.

| Method | Route                                     | Contract                                                                                                                                                                                |
| ------ | ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/.well-known/oauth-authorization-server` | RFC 8414 metadata; `/oauth/.well-known/oauth-authorization-server` is an alias                                                                                                          |
| GET    | `/oauth/authorize`                        | Validates request then redirects to configured frontend consent page with bounded allowlisted protocol parameters only                                                                  |
| POST   | `/oauth/authorize`                        | Existing customer application bearer plus JSON request and explicit `approved` boolean; returns `{redirect_uri}` containing code/state or access_denied/state, never automatic redirect |
| POST   | `/oauth/token`                            | RFC form exchange with `client_secret_basic` or `client_secret_post`, exact redirect URI and S256 verifier; issues an RS256 access-token JWT                                            |
| GET    | `/oauth/jwks`                             | Public RSA JWKS only; advertised by metadata `jwks_uri`                                                                                                                                 |
| POST   | `/internal/oauth/introspect`              | `AUTH_INTERNAL_SECRET` bearer and `{token}` JSON; returns the existing customer profile or controlled 401                                                                               |

Authorization JSON contains `client_id`, `redirect_uri`, `response_type=code`,
`scope=mcp.access`, nonempty bounded `state`, `code_challenge`,
`code_challenge_method=S256`, and `approved`. The frontend/BFF owns login and consent
presentation; the application bearer must never appear in a browser URL.
Codes expire after 300 seconds and are consumed once using a conditional ORM update
in the same transaction as token persistence. RS256 access tokens expire after
3600 seconds (one hour), with `typ=at+jwt` and an explicit `kid`. Claims bind issuer, audience,
customer profile, `client_id`, unique `jti`, `iat`, `exp` and exact `mcp.access` scope.
This customer-only grant permits existing MCP reads and customer dispute mutation
tools; service ownership, explicit dispute consent and recovery checks remain required.
Legacy `banking.read` scopes and operator/admin grants are rejected.
Only SHA-256 code/token digests and Argon2 client-secret hashes are persisted.
Every exchange and compatibility introspection rechecks the active customer, current
identity version and profile bindings; client deactivation also rejects introspection.
Account/Transaction MCP OAuth mode instead validates the signed snapshot locally
against configured public JWKS, with no per-call introspection or HMAC fallback.
Deactivation, password/version/profile changes and client revocation therefore take
up to the remaining 3600-second token lifetime to affect MCP access. This configured
TTL delay does not apply to login JWTs or REST's uncached introspection;
[ADR 0012](../../../docs/adr/0012-local-oauth-jwt-validation-for-mcp.md) remains Proposed.
Legacy opaque tokens can still be introspected but cannot authenticate MCP OAuth.
OAuth tables belong to PostgreSQL schema `oauth`, with migration `20261017_0013`
after `20261005_0012`. Applying migrations and deployment are separately authorized
operations; offline tests do not prove PostgreSQL contention or Foundry integration.

Supply `OAUTH_CLIENT_SECRET` externally (32–512 characters), then explicitly provision:

```powershell
rtk proxy uv run --directory app\business-api\identity banking-provision-oauth-client --client-id foundry-banking --redirect-uri https://approved-client.example/callback --confirm-provision
```

The CLI never prints the secret or implicitly seeds clients. Explicit identical reruns
verify the active client's secret and redirects without replacing it; mismatches fail.
HTTPS exact redirects are mandatory; `--allow-loopback-http` permits only
explicit offline localhost/127.0.0.1/::1 HTTP callbacks. Rotation requires separate
approval. Token responses are no-store and omit raw protocol exception descriptions.

### Native Azure signing and remote client registration

Remote Terraform selects `OAUTH_SIGNING_BACKEND=key_vault` and supplies a version-pinned
`OAUTH_SIGNING_KEY_URI`. Identity's managed identity reads the public RSA key and signs
RS256 digests in Key Vault; the private key is never exported. Local
`OAUTH_SIGNING_BACKEND=pem` remains the default and uses the existing externally supplied
PEM. Do not configure both native and PEM signing. Native/public-JWKS rotation requires
coordinated approval, not an automatic key-version change.

Use the [manual database operations workflow](../../../.github/workflows/manual-database-operations.yaml)
and its [configuration guide](../../../.github/workflows/README.md#manual-database-and-oauth-operations)
instead of restoring a local database remotely:

1. Apply the separately authorized Terraform configuration and operations-principal grants.
   Development provisioning completed on 2026-10-18 using the existing GitHub CD OIDC
   principal for operations as well. GitHub environment variables still require separate
   configuration; successful provisioning does not verify runner connectivity or SQL grants.
2. Review migration lineage, backup/restore readiness and the selected branch checkout; dispatch
   `migrate` with `migrate:Development`. The CLI resolves the latest single checkout head
   automatically, without a target-revision input. Manual dispatch permits any branch
   allowed by the Development environment policy once the workflow exists on the default
   branch. OAuth initialization never applies migrations.
3. Dispatch `init-oauth` with `init-oauth:Development`. It reads the public native key,
   publishes matching `oauth-jwks`, generates a random 32-byte BFF secret only if absent,
   and stores only its Argon2 hash with the exact client ID and HTTPS callback in PostgreSQL.
4. Deploy Identity and consumers after resolved Key Vault references and separately
   authorized runtime checks. No secret is copied into GitHub variables or browser storage.

Identical reruns verify existing secrets/client metadata without replacing them. A
mismatch or inactive client fails; rotation and reactivation require separate approval.
`register-client` registers/verifies a client against an existing Key Vault secret and
creates no key or secret. Native Foundry clients must use their own approved IDs, callbacks
and separate secrets, not the BFF secret or a shared customer grant. The CLI supports
`--client-secret-name` for explicitly authorized additional clients; the workflow targets
only the configured BFF client. This is administrative provisioning, not a public dynamic
client-registration endpoint, and does not remove the Toolbox fail-closed gate.

The installed module `identity.services.oauth_remote` exposes `init-oauth`,
`register-client` and `migrate`, all requiring `--confirm`, `--vault-url`,
`--expected-host` and `--expected-database`. Standalone execution defaults to managed
identity; the OIDC workflow uses `--credential azure-cli`. Database credentials are
retrieved in memory, never passed as CLI arguments or read from dotenv. The workflow
first verifies the vault/server/database through Azure ARM in its selected subscription;
the CLI requires the exact PostgreSQL hostname/database and TLS. Explicit operations
must be serialized; the workflow provides shared concurrency, not cross-system locking.

Client registration is not automatic at startup, login or consent. An empty `oauth`
schema is consistent with `invalid_client`: authorization cannot create a code without
an existing active client whose ID matches the request. Persistence proceeds through
explicit client registration, accepted consent (authorization code), then successful
code exchange (access token). Diagnostics do not provision clients or apply migrations.

OAuth failures emit controlled diagnostics across Identity authorization, token exchange
and introspection, BFF authorization/callback and upstream transport, and shared MCP
JWT verification. Client lookup distinguishes `unregistered` from `inactive`; protocol
errors use allowlisted codes, and other failures use static categories or HTTP statuses.
Token exchange distinguishes `code_not_found_for_client`, `code_consumed`, `code_expired`,
`redirect_binding`, `pkce_binding`, `customer_snapshot` and `code_consumption_conflict`.
Expiry checks compare instants in UTC, preserving timezone-aware PostgreSQL offsets;
naive SQLite timestamps are treated as UTC. Authorization codes retain their
300-second lifetime; access tokens last 3600 seconds.
These categories preserve native Authlib validation and do not identify the exact
profile field that changed. Persisted unconsumed codes prove authorization succeeded,
not that the callback's code, client, redirect and verifier bindings passed validation.
Successful BFF status polling adds no error diagnostics. Logs omit request contents,
customer claims, secrets, tokens, callback URLs and raw exception descriptions. Share
only controlled categories/codes when troubleshooting. Offline regressions verify the
empty-table rejection behavior, not the configured PostgreSQL state.

Focused offline check:

```powershell
$env:OTEL_SDK_DISABLED = "true"
rtk proxy uv run --directory app\business-api\identity python -m pytest tests\test_oauth.py -q
```

## Contracts

- Each principal has exactly one controlled role: `customer`, `operator`, or `admin`.
  Roles use integer primary keys and unique closed-catalog names; `UserRole.role_id`
  stores the association, while JWT/profile `role` remains the canonical name.
  Administrators are Users with the admin role, not a separate Admin table.
- New operator requests require trimmed, nonempty `first_name` and `last_name`,
  each at most 50 characters. Display names are derived from those fields; legacy
  nullable components are not guessed or split and retain the User.name fallback.
  User email is limited to 120 characters and User.name to 101 characters.
  `CustomerUser` maps customer principals to real customers; staff have no invented
  customer association. Operators use the stable `user_id` key; revision
  `20261004_0010` removes the optional simulated reviewer mapping and archives
  existing mappings in `legacy_operator_service_agents`, without live foreign keys.
  Support-case real ownership references `operators.user_id`; catalog snapshots
  never grant operator authority.
- Users have database-constrained `active`/`inactive` status, UTC `created_at` and
  `updated_at`, and positive `identity_version`. New operators start inactive.
- JWTs include `sub`, `email`, `locale`, `role`, `identity_version`, `iss`, `aud`,
  `iat`, and `exp`; only customer JWTs include `customer_id`. Profiles additionally
  expose `name`, `status`, and ISO `updated_at`; these are not JWT claims.
- BFF, Account, and Transaction check current persisted identity state without
  caching. Inactive or stale-version tokens fail with 401; forbidden roles with
  403; unavailable Identity with 503. Existing admitted streams are not cancelled.
  Legacy role-less tokens require a coordinated fresh login.
- Status/password changes increment the identity version and atomically update
  timestamps and audit. Passwords, hashes, and tokens must never enter logs.

| Method     | Route                                       | Access                                                        |
| ---------- | ------------------------------------------- | ------------------------------------------------------------- |
| POST       | `/auth/login`                               | Email/password through BFF                                    |
| GET        | `/auth/me`                                  | Application bearer through BFF                                |
| POST       | `/internal/introspect`                      | `AUTH_INTERNAL_SECRET` bearer; application token in JSON body |
| GET / POST | `/admin/operators`                          | Current admin bearer through BFF                              |
| POST       | `/admin/operators/{user_id}/activate`       | Current admin bearer                                          |
| POST       | `/admin/operators/{user_id}/deactivate`     | Current admin bearer                                          |
| POST       | `/admin/operators/{user_id}/reset-password` | Current admin bearer                                          |
| GET        | `/admin/customers`                          | Current admin bearer through BFF                              |
| POST       | `/admin/customers/{user_id}/activate`       | Current admin bearer through BFF                              |
| POST       | `/admin/customers/{user_id}/deactivate`     | Current admin bearer through BFF                              |

Customer administration lists existing customer-role Users, including inactive
ones, with validated `CustomerUser` membership to a persisted Customer. Staff,
missing targets, and malformed customer memberships cannot be changed. There is no
customer creation, deletion, or password-reset endpoint. Each accepted activation
or deactivation, including a repeated same-status request, increments the version
and atomically records `customer_activate` or `customer_deactivate` audit with its
actor and target. Older sessions stay revoked after reactivation; a fresh login is
required. These operations never change banking Customer status or financial data.
The forward-only `20261003_0008` migration widens the audit action constraint and
must be applied separately through the approved database migration workflow before
using these endpoints; applying it is not part of offline tests.

The introspection secret is distinct from `JWT_SECRET_KEY` and the agent-only
`INTERNAL_IDENTITY_SECRET` HMAC envelope. Staff cannot use customer chat or financial
REST routes. Current operators have a separate direct Transaction review surface:
consented available cases, exclusively assigned cases and owner-only detail.
Identity authenticates and revalidates those operators; Transaction owns claim
policy and audit. See the [Transaction guide](../transaction/README.md).
Assigned-operator verdicts and recorded effects are implemented in Transaction;
reassignment remains out of scope. Implementation does not establish hosted or
financial end-to-end acceptance.

## Configuration and local tooling

Supply environment values explicitly; the service never discovers credential files.

| Variable                                | Purpose                                             |
| --------------------------------------- | --------------------------------------------------- |
| `DATABASE_URL`                          | Approved PostgreSQL connection                      |
| `JWT_SECRET_KEY`                        | HS256 key, at least 32 characters                   |
| `JWT_ISSUER` / `JWT_AUDIENCE`           | Shared application JWT issuer/audience              |
| `AUTH_INTERNAL_SECRET`                  | Protected introspection key, at least 32 characters |
| `ACCESS_TOKEN_MINUTES`                  | 60 by default, range 1–60                           |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Optional Azure Monitor trace and HTTP metric export |

Access expiry is absolute from issuance, not an inactivity timeout. Explicit environment
values override the default. Restart Identity after changing the lifetime;
already-issued JWTs retain their original expiry.

From repository root, only after authorizing stack startup:

```powershell
rtk proxy uv sync --directory app\business-api\identity --frozen --group dev
rtk proxy uv run --directory app\business-api\identity --env-file .env python -u ..\..\..\.vscode\run-local-service.py identity
```

The root F5 `DEV - Full Stack Ordered` launch uses `noDebug: true` and starts
Identity HTTPS on `https://localhost:8091`, using certificates prepared outside the
checkout. It neither attaches a debugger nor opens a browser. The local output runner
adds severity colors and appends plain UTF-8 output to `logs/identity-YYYY-MM-DD.log`;
files have no automatic retention cleanup. See the [root startup guide](../../../README.md#run-locally)
for TLS preparation and the ordered six-service task.

The local tasks load each consumer's ignored `.env`: Identity, Account,
Transaction, and Responses BFF. Keep `JWT_SECRET_KEY`, `JWT_ISSUER`,
`JWT_AUDIENCE`, and `AUTH_INTERNAL_SECRET` identical across those four services.
The introspection key must differ from `INTERNAL_IDENTITY_SECRET`; the agent and
normal data ingestion do not need the three issuer/audience/introspection settings.
Identity, Account, and Transaction need `DATABASE_URL`; the BFF must not receive it.
Each service owns `.env` and a credential-free `.env.example`, including the agent.
There is no root dotenv configuration; service tasks load each service's `.env`.

BFF/Account/Transaction retain the compatibility setting `AUTH_USERS_ENDPOINT`
(compatibility default `http://127.0.0.1:8090`) for this service despite the Identity rename.
For the root HTTPS stack, explicitly configure each consumer with
`AUTH_USERS_ENDPOINT=https://localhost:8091` and trust the prepared localhost CA.
PostgreSQL connection and statement timeouts are bounded to three seconds.

## Observability

FastAPI creates server spans and HTTP duration metrics using explicit, reused
providers with the `banking-assistant-identity` service resource. Incoming W3C
trace context correlates Identity requests with the BFF. Export is optional;
missing or invalid exporter configuration does not disable HTTP serving.
Instrumentation does not enable body or authentication-header capture.
In-memory regression tests do not establish deployed Application Insights ingestion.

## Explicit administrator bootstrap

Bootstrap is never invoked at application startup. After separately approving the
specific database target and write operation, run the CLI with environment supplied
by the operator:

```powershell
uv run --directory app\business-api\identity banking-bootstrap-admin --email <approved-email> --locale en --confirm-bootstrap
```

The default mode requires `--email`, `--locale`, and `--confirm-bootstrap`; `--name`
is optional. It prompts for a hidden password and matching confirmation, even when
administrator environment credentials are present.

For explicitly opted-in noninteractive provisioning, securely supply
`ADMIN_BOOTSTRAP_EMAIL` and `ADMIN_BOOTSTRAP_PASSWORD` in the operator's environment,
then run against the separately approved target:

```powershell
rtk proxy node -e 'const p=require("path"),s=require("child_process");const data=p.resolve("app","business-api","data",".env").split(String.fromCharCode(92)).join(String.fromCharCode(92,92));const r=s.spawnSync("uv",["run","--no-sync","--env-file",".env","--env-file",data,"banking-bootstrap-admin","--credentials-from-env","--locale","en","--confirm-bootstrap"],{cwd:p.resolve("app","business-api","identity"),stdio:"inherit"});process.exit(r.status ?? 1);'
```

This local example loads Identity runtime settings and the existing Data bootstrap
credentials without relocating seed passwords. Both files must target the same
approved database; later files and existing process variables can override values.
Run from the repository root in PowerShell after syncing Identity dependencies.
This verified Node wrapper preserves the Windows Data dotenv path through RTK/uv
argument parsing. The bootstrap is one-time provisioning, not a login/reset command.

Environment mode rejects missing/blank credentials and a simultaneous `--email`;
it never falls back to a prompt. Administrator passwords are preserved exactly and
must satisfy the 12–256-character input contract. `DEMO_USER_PASSWORD` remains the
separate customer-seeding credential (outer whitespace is trimmed by that existing
contract). When both are configured, either provisioning path rejects equal
passwords before connecting to the database. Keep both variables configured during
provisioning to enforce that comparison; this is not a password-history comparison.

The CLI refuses an existing administrator or email; it does not reset, promote, or
seed an administrator automatically. First-admin creation is serialized on the admin
role row. Success output identifies no email and contains no credentials. The admin
identity is the operator-supplied bootstrap email, not a default or a seeded customer.
Never put passwords in source, command arguments, or a provisioning manifest.

Provisioning order is explicit: approve target/backups and the legacy activation
policy, apply [data migrations](../data/README.md#migration-prerequisites), run
the existing `run_pipeline.py` only if the approved customer data needs loading,
then separately seed selected customer users and bootstrap the first administrator.
Ingestion never provisions users or an administrator automatically.

## Package responsibilities

- [services](src/identity/services/) contains identity operations, explicit administrator
  bootstrap and customer demo provisioning.
- [models](src/identity/models/) contains API and provisioning DTO schemas; persisted
  tables remain owned by the shared package.
- [main.py](src/identity/main.py) remains the application launch entrypoint.
  [bootstrap.py](src/identity/bootstrap.py) retains the compatible module CLI;
  the console command targets `identity.services.bootstrap:main`.

## Explicit customer demo provisioning

[provisioning.py](src/identity/services/provisioning.py) owns customer identity creation and
refresh, role and association checks, Argon2 hashing, profile normalization and
audits. Each explicit call commits the selected cohort once. Refresh rotates the
credential and identity version; it is not a name-only repair.

The compatible [Data CLI](../data/README.md#explicit-demo-identity-seeding) supplies
environment inputs and writes a credential-free manifest. Normal ingestion and
service startup never invoke provisioning. Database writes and seeding require
separate explicit authorization.

## CI/CD

[Identity CI](../../../.github/workflows/ci-identity.yml) runs Python 3.11 dependency
sync, syntax checks and synthetic tests on matching pushes and pull requests. It
uploads JUnit evidence and rejects stale deployment requirements through the shared
`ci-python` action's optional `deployment-requirements: requirements.txt` input.
[Identity CD](../../../.github/workflows/cd-identity.yaml) repeats validation before
OIDC-authenticated deployment to an existing App Service in `Development`. It only
deploys `main`; OpenAPI exposure and runtime readiness checks are deferred.

The root [azd manifest](../../../azure.yaml) targets Identity through the
explicit `resourceName: ${AZURE_IDENTITY_APP_NAME}`, matching the other services, and copies
`banking_shared` into its deployment package. [Terraform](../../../infra/main.tf)
defines the fifth App Service and exports `AZURE_IDENTITY_APP_NAME`
plus `IDENTITY_URI` from its actual hostname. CI/CD requires the app-name
output as a GitHub Development variable: deployment and preflight target that
exact name, just like the other services. Identity reuses Terraform's existing `database-url` secret with
secret-scoped managed-identity access and coordinated consumer settings; the BFF
has no database setting. This shared PostgreSQL administrator credential is an
approved prototype tradeoff, not least-privilege database isolation. See the
[infrastructure guide](../../../infra/README.md) for authorized rollout and rollback.
Provisioning, approved migrations, network/database grants, secret configuration
and deployed acceptance remain separate gates; neither workflow creates users or
bootstraps administrators.
See [workflow configuration](../../../.github/workflows/README.md#identity-deployment-cd-identityyaml)
for all GitHub variables and App Service settings, including the factory startup command.

Regenerate the Oryx runtime artifact from the dependency manifest for Linux Python 3.11
when dependencies change, using the repository's `uv pip compile` convention:

```powershell
rtk proxy uv pip compile app\business-api\identity\pyproject.toml --no-emit-package banking-shared --python-version 3.11 --python-platform x86_64-unknown-linux-gnu -o app\business-api\identity\requirements.txt
```

The excluded shared distribution is supplied as source by the packaging hooks;
its transitive runtime dependencies remain in the compiled requirements. Successful
CI, metadata preflight or deployment does not establish startup, hosted login or
PostgreSQL acceptance.

## Verification boundaries

```powershell
rtk proxy uv run --directory app\business-api\identity --offline python -m pytest -q --tb=short
```

Synthetic tests cover roles, profiles, token version/revocation, fail-closed
associations, lifecycle audit, explicit bootstrap and
[customer provisioning](tests/test_provisioning.py), including cohort commit and
pre-session validation. Shared-schema migration tests
live in [data tests](../data/tests/test_identity_migrations.py).

Synthetic SQLite success is not PostgreSQL migration/backfill acceptance. Live
migration rehearsal, bootstrap, HTTP/browser checks, least-privilege database grants,
and deployment remain separate approval and evidence gates. No production reviewer
or financial effect is enabled by this service.
