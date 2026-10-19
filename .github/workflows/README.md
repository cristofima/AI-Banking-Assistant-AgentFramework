# CI/CD Workflows

This directory contains the GitHub Actions workflows for CI and CD. Infrastructure is provisioned separately, and deployments here only run `azd deploy` for already-provisioned services.

Reusable building blocks live in [../actions](../actions):

- [../actions/ci-python/action.yml](../actions/ci-python/action.yml): shared Python CI implementation used by all `ci-*` workflows.
- [../actions/ci-node/action.yml](../actions/ci-node/action.yml): shared Node/React CI implementation used by frontend `ci-*` workflows.
- [../actions/cd-azure/action.yml](../actions/cd-azure/action.yml): shared Azure login and `azd` environment setup used by all `cd-*` workflows.

## Naming convention

- `ci-*.yml`: validation workflows for each service (Python and frontend).
- `cd-*.yaml`: deployment workflows for each deployable service.

## Deployment model

- Target branch: `main`
- Target GitHub Environment: `Development`
- Authentication: GitHub OIDC with `azure/login@v2`
- Provisioning in CI: not allowed (`azd provision` is intentionally not used here)
- Deploy command pattern: `azd deploy <service> --no-prompt`
- CD triggers: push to `main` on matching path filters; manual dispatch where enabled.

## CI model

- CI branches: `main`, `develop`, `feature/*`, `refactor/*`.
- PR targets: `main`, `develop`.
- Common checks per project: dependency sync with `uv`, compile check, test discovery, pytest when tests exist, and artifact upload (`junit.xml`, optional `coverage.xml`).
- Frontend checks: `npm ci`, `lint`, tests with Vitest coverage summary/artifact, and production `vite build` via shared `ci-node`.
- Frontend Node runtime: shared `ci-node` defaults to Node `22`.

## Workflow inventory

| Workflow file           | Purpose                                                   | Trigger path                           | azd project root | Main command                                             |
| ----------------------- | --------------------------------------------------------- | -------------------------------------- | ---------------- | -------------------------------------------------------- |
| `ci-identity.yml`       | Validate Identity build/tests and deployment requirements | `app/business-api/identity/**`         | n/a              | Shared action (`ci-python`)                              |
| `cd-identity.yaml`      | Test and deploy existing Identity App Service             | `app/business-api/identity/**`         | repository root  | `azd deploy identity --no-prompt`                        |
| `ci-account.yml`        | Validate Account API Python build/tests                   | `app/business-api/account/**`          | n/a              | Shared action (`ci-python`)                              |
| `ci-transaction.yml`    | Validate Transaction API build/tests                      | `app/business-api/transaction/**`      | n/a              | Shared action (`ci-python`)                              |
| `ci-responses-bff.yml`  | Validate Responses BFF build/tests                        | `app/responses-bff/**`                 | n/a              | Shared action (`ci-python`)                              |
| `ci-hosted-agent.yml`   | Validate hosted-agent build/tests                         | `app/agent/**`                         | n/a              | Shared action (`ci-python`)                              |
| `ci-data.yml`           | Validate data module build/tests                          | `app/business-api/data/**`             | n/a              | Shared action (`ci-python`)                              |
| `ci-frontend.yml`       | Validate frontend lint/tests/build                        | `app/frontend/banking-web/**`          | n/a              | Shared action (`ci-node`)                                |
| `cd-account.yaml`       | Deploy Account API App Service                            | `app/business-api/account/**`          | repository root  | `azd deploy account --no-prompt`                         |
| `cd-transaction.yaml`   | Deploy Transaction API App Service                        | `app/business-api/transaction/**`      | repository root  | `azd deploy transaction --no-prompt`                     |
| `cd-responses-bff.yaml` | Deploy JWT-protected Responses BFF                        | `app/responses-bff/**`                 | repository root  | `azd deploy responses-bff --no-prompt`                   |
| `cd-frontend.yaml`      | Deploy frontend Web App Service                           | `app/frontend/banking-web/**`          | repository root  | `azd deploy web --no-prompt`                             |
| `cd-hosted-agent.yaml`  | Deploy Foundry hosted agent                               | `app/agent/**` and `workflow_dispatch` | `app/agent`      | `azd -C app/agent deploy home-banking-agent --no-prompt` |

## Replay smoke on PRs

[ci-hosted-agent.yml](ci-hosted-agent.yml) also validates replay harness changes
and Account/Transaction MCP tool contracts. After offline tests, same-repository
PRs run the three synthetic cases with a real model using `azure/login@v2`, OIDC,
and the `Development` environment. Required variables are `AZURE_CLIENT_ID`,
`AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `FOUNDRY_PROJECT_ENDPOINT`, and
`MODEL_DEPLOYMENT_NAME`. The federated identity must be authorized to call the
model. No Key Vault secret resolution, deployment, or provisioning is performed.

Missing configuration or incomplete/failed evidence fails the smoke job. A sticky
comment and job summary show protocol statuses; full synthetic evidence remains
in 14-day artifacts. Fork PRs only receive a non-execution notice. This job is not
a dispute quality or real authorization gate. Actual PR execution, OIDC access,
and required-check settings are unverified. See the [evaluation guide](../../evals/README.md#pr-smoke-check).

## Why two azd project roots exist

This repository uses two independent `azure.yaml` manifests:

1. Root [azure.yaml](../../azure.yaml): App Service deployments (`identity`, `account`, `transaction`, `responses-bff`, `web`), provisioned through root Terraform. Each CD workflow requires its explicit App Service name variable, exported into azd and bound by the manifest’s `resourceName`. Python preflight looks up that exact name; tags do not select deployment targets.
2. Agent [azure.yaml](../../app/agent/azure.yaml): Foundry hosted-agent stack (`home-banking-agent`).

For that reason, `cd-hosted-agent.yaml` always uses `azd -C app/agent ...` so it resolves the agent manifest explicitly and never the root manifest.

## Required GitHub Environment variables

Set these in **Settings > Environments > Development > Variables**.

### Shared variables (all workflows)

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`

### Deployment regions

- `AZURE_APP_SERVICE_LOCATION=centralus`: required by the five root App Service CD
  workflows (Identity, Account, Transaction, Responses BFF and Web).
- `AZURE_LOCATION`: required by the separate hosted-agent CD workflow; retain its
  existing Foundry region.

These GitHub Environment variables are independent from local azd configuration.
The shared CD action uses the selected region to initialize its deployment-only
azd environment; it does not provision or relocate resources.

Root CD also requires a GitHub `Development` variable whose value matches its
service’s Terraform name output. Terraform outputs, azd variables, root manifest
bindings and GitHub Variables all use the same `_APP_NAME` names:

| Service       | Required GitHub variable       | Terraform output / azd variable |
| ------------- | ------------------------------ | ------------------------------- |
| Identity      | `AZURE_IDENTITY_APP_NAME`      | `AZURE_IDENTITY_APP_NAME`       |
| Account       | `AZURE_ACCOUNT_APP_NAME`       | `AZURE_ACCOUNT_APP_NAME`        |
| Transaction   | `AZURE_TRANSACTION_APP_NAME`   | `AZURE_TRANSACTION_APP_NAME`    |
| Responses BFF | `AZURE_RESPONSES_BFF_APP_NAME` | `AZURE_RESPONSES_BFF_APP_NAME`  |
| Web           | `AZURE_WEB_APP_NAME`           | `AZURE_WEB_APP_NAME`            |

Shared Azure setup validates the GitHub value and exports it under the same
azd variable with `azd env set`. Terraform outputs are not automatically published
to GitHub Variables.
Missing/invalid names fail before authentication. The separate Foundry agent
project omits both App Service name inputs.

### Shared secrets Key Vault (`cd-responses-bff.yaml`, `cd-account.yaml`, `cd-transaction.yaml`, `cd-hosted-agent.yaml`)

`JWT_SECRET_KEY` (application JWT), `AUTH_INTERNAL_SECRET` (Identity introspection),
and `INTERNAL_IDENTITY_SECRET` (agent transport) are distinct secrets, each at least
32 characters, stored in the shared Key Vault Terraform provisions
(`azurerm_key_vault.secrets` in `infra/main.tf`), not as GitHub Environment Secrets.
Preflights inspect reference syntax and App Service `Resolved` metadata only;
they never retrieve resolved secret values or prove their length.

- Python App Services use versionless `@Microsoft.KeyVault(SecretUri=...)` references,
  resolved by their system-assigned managed identities (`Key Vault Secrets User`).
  The shared `appservice-preflight` action requires `Resolved` reference status for
  JWT/introspection secrets and database URLs; consumers also require the agent
  transport secret. Account/Transaction require `CORS_ALLOWED_ORIGINS`.
- Consumers require remote HTTPS `AUTH_USERS_ENDPOINT` without loopback/IP literals,
  credentials, query or fragment, and explicit nonempty `JWT_ISSUER`/`JWT_AUDIENCE`.
  The DB-free Responses BFF rejects any `DATABASE_URL` app setting, including an empty one.
  These are configuration gates, not connectivity, login or matching-secret evidence.
  OIDC needs App Service configuration/reference-metadata read and deployment permissions,
  not permission to retrieve vault values for these preflights.
- Foundry hosted agents have no native Key Vault app-setting reference, so
  `cd-hosted-agent.yaml` resolves `internal-identity-secret` itself with
  `az keyvault secret show`, masks it with `::add-mask::`, and exports it through
  `$GITHUB_ENV` before passing it to `azd -C app/agent env set`.
- Set `AZURE_KEY_VAULT_NAME` under **Settings > Environments > Development > Variables**
  to the Key Vault name from Terraform's `AZURE_KEY_VAULT_NAME` output
  (`azd env get-value AZURE_KEY_VAULT_NAME`).
- The GitHub Actions federated identity needs `Key Vault Secrets User` on the vault to
  run the `cd-hosted-agent.yaml` resolve step; grant it via Terraform's
  `github_actions_principal_id` variable (the service principal's object ID, not its
  client/app ID) or with `az role assignment create` directly.
- Authentication secret values are supplied separately, not managed as Terraform
  secret resources. Terraform does manage `database-url` using its generated
  PostgreSQL administrator credential, shared by Identity, Account and Transaction.
  That URL/password enters sensitive Terraform state; restrict backend and saved-plan
  access. Sensitive marking does not remove values from state.

### Identity deployment (`cd-identity.yaml`)

Identity uses the five shared Azure variables plus `AZURE_IDENTITY_APP_NAME` in
`Development`, like the other App Service workflows. Root azd deployment
and preflight target that exact physical name. The OIDC identity needs
permission to read app configuration and deploy that app. No Identity passwords or
JWT/introspection secrets belong in GitHub variables or workflow logs.

Identity CI uses Python 3.11, synthetic pytest tests and JUnit artifacts. Both CI and
CD opt into the shared `ci-python` action's `deployment-requirements: requirements.txt`
check to run `uv pip compile` against `pyproject.toml`
for Linux (`x86_64-unknown-linux-gnu`), Python 3.11, excluding `banking-shared`.
The artifact generator uses:

```bash
uv pip compile app/business-api/identity/pyproject.toml --no-emit-package banking-shared --python-version 3.11 --python-platform x86_64-unknown-linux-gnu -o app/business-api/identity/requirements.txt
```

Existing `requirements.txt` pins constrain recompilation to avoid upstream version drift;
comparison ignores comments/blank lines and rejects local/editable package paths.
The App Service action also runs synthetic configuration/target-selection regressions
(`node --test .github/actions/appservice-preflight/preflight.test.cjs`) before preflight.
The frozen lockfile still governs CI installation/tests, not the Oryx runtime artifact.
CD repeats checks before Azure authentication; manual Identity deployments are restricted
to `main`. Deployments are serialized and never cancel an in-progress deployment.

Provision the root Terraform stack and configure approved secrets before running CD.
Preflight requires the existing app named by `AZURE_IDENTITY_APP_NAME` in
`AZURE_RESOURCE_GROUP`, independently of tags. Required
App Service configuration:

- Linux runtime `PYTHON|3.11`.
- Startup command `python -m uvicorn identity.main:create_app --factory --host 0.0.0.0 --port 8000`.
- Nonempty `DATABASE_URL`, `JWT_SECRET_KEY`, `JWT_ISSUER`, `JWT_AUDIENCE`, and
  `AUTH_INTERNAL_SECRET`; database/JWT/introspection settings must be versionless
  Key Vault references with `Resolved` metadata, and issuer/audience explicit values.
- `SCM_DO_BUILD_DURING_DEPLOYMENT=true` for Oryx dependency installation.
- Optional `ACCESS_TOKEN_MINUTES` (default 60, valid range 1–60; absolute expiry from issuance).

Use approved secret storage/Key Vault references and database network/grant configuration.
`AUTH_INTERNAL_SECRET` is the shared introspection secret, not the agent transport
secret `INTERNAL_IDENTITY_SECRET`. Configure BFF, Account and Transaction with the
Identity URL in `AUTH_USERS_ENDPOINT` and matching JWT/introspection settings through
root Terraform. Rollout remains gated: provision/configure references, deploy Identity,
perform separately approved Identity acceptance checks, then deploy consumers. Independent
workflows do not enforce cross-workflow ordering or prove acceptance.

CD does not probe `/openapi.json`; endpoint exposure and a safe readiness contract
are deferred. Configuration preflight checks reference metadata before deployment,
not secret contents, startup, PostgreSQL connectivity, login or end-to-end authorization.
Automatic CI/CD never applies migrations, seeds users, bootstraps administrators or
provisions infrastructure. The separate manually confirmed database operations workflow
can upgrade reviewed migrations or initialize OAuth; it is not part of deployment. See the [Identity guide](../../app/business-api/identity/README.md#cicd).

### OAuth App Service configuration

Root Terraform owns OAuth app settings; CD checks the existing settings before deploying
Identity, Account, Transaction or BFF. The runner does not copy a local azd environment
or apply Terraform. Configure root azd `OAUTH_AUDIENCE`, `OAUTH_CLIENT_ID` and
`OAUTH_SIGNING_KEY_ID` before the separately authorized infrastructure update.
See [infrastructure configuration](../../infra/README.md) for exact inputs and outputs.

| Service               | Required OAuth app settings                                                                                                                                         |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Identity              | `OAUTH_ISSUER`, `OAUTH_AUDIENCE`, `OAUTH_FRONTEND_AUTHORIZE_URL`, `OAUTH_SIGNING_KEY_ID`, `OAUTH_SIGNING_BACKEND=key_vault`, version-pinned `OAUTH_SIGNING_KEY_URI` |
| Account / Transaction | `MCP_IDENTITY_MODE=oauth`, `OAUTH_ISSUER`, `OAUTH_AUDIENCE`, `OAUTH_JWKS`                                                                                           |
| Responses BFF         | MCP consumer settings above plus `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_REDIRECT_URI`                                                                     |

The issuer is the public Identity HTTPS origin, without a trailing slash, matching
consumers' `AUTH_USERS_ENDPOINT`. Identity consent targets the public frontend
`/oauth/consent`; BFF's exact registered redirect targets frontend `/oauth/callback`,
with that origin in `ALLOWED_ORIGINS`. Callback routes do not bypass the BFF.

Terraform creates the native RSA key; Identity signs remotely using its managed
identity and never receives `OAUTH_SIGNING_PRIVATE_KEY`. Local PEM signing remains
unchanged. Explicit OAuth initialization generates the BFF client secret only when
missing, publishes matching public `oauth-jwks`, and registers/verifies the client in
the target database; reruns never silently rotate existing secrets or client metadata.
Only BFF receives its confidential client secret. Trusted public JWKS reaches BFF and
both MCP services. Manual initialization needs narrowly scoped Key Vault public-key
read and write access to the two OAuth entries. Development currently shares the
operations and CD principal; those write permissions therefore also belong to CD.
Preflight requires versionless `Resolved` references, but cannot establish key-pair
alignment, client registration, scope, signature validity or runtime acceptance.
Missing OAuth settings block CD; HMAC is not an automatic deployment fallback.
Application JWTs and `INTERNAL_IDENTITY_SECRET` remain independent.

### Manual database and OAuth operations

[manual-database-operations.yaml](manual-database-operations.yaml) is dispatch-only and
can run from any selected branch in the protected `Development` environment. The
workflow must first exist on the default branch for GitHub to expose manual dispatch.
Configure required reviewers, environment branch policies permitting approved source
branches, and Azure OIDC federation for that environment before use.
It is independent from CD and shares `database-operations-development` concurrency
without canceling an in-progress writer. Any other manual workflow writing this database
must use the same group; direct external writers are not locked by GitHub concurrency.

Configure these **Development environment variables**, separately from local azd:

| Variable                                                     | Purpose                                                                                                                           |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| `AZURE_OPERATIONS_CLIENT_ID`                                 | Operations principal's application/client ID for OIDC login; Development currently reuses the existing CD principal               |
| `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`                   | Explicit Azure login target                                                                                                       |
| `AZURE_RESOURCE_GROUP`                                       | Resource group for ARM target verification                                                                                        |
| `AZURE_KEY_VAULT_NAME`                                       | Existing Terraform vault holding `database-url` and OAuth entries                                                                 |
| `AZURE_POSTGRES_SERVER_NAME`, `AZURE_POSTGRES_DATABASE_NAME` | Existing server/database, checked through ARM                                                                                     |
| `OAUTH_SIGNING_KEY_URI`, `OAUTH_SIGNING_KEY_ID`              | Terraform public outputs; required for `init-oauth`                                                                               |
| `OAUTH_CLIENT_ID`, `OAUTH_REDIRECT_URI`                      | Default BFF client and exact frontend HTTPS callback; required for client operations without complete additional-client overrides |
| `AZURE_DB_OPERATIONS_RUNNER`                                 | Optional JSON runner label/list; defaults to `"ubuntu-latest"`; select an approved network-connected runner if necessary          |

Root azd `AZURE_OPERATIONS_PRINCIPAL_ID` is the principal's **object ID**, not the
workflow application's client ID. Terraform grants resource-group Reader, key-scoped
public read, `database-url` read and Secrets Officer only on `oauth-jwks` and
`oauth-client-secret`. Development provisioning on 2026-10-18 reused the existing CD
principal, so that identity now also has these scoped write permissions. A distinct
operations principal is an optional future isolation change, not the current setup.
azd provisioning does not populate GitHub environment variables. Wait for RBAC
propagation and ensure runner network/database access; this workflow grants no firewall,
role-assignment or infrastructure-provisioning permission.

Dispatch one operation at a time:

- `migrate`: confirmation `migrate:Development`. Automatically resolve the single latest
  Alembic head in the selected branch checkout, upgrade to it and verify the applied
  revision. There is no target-revision input; missing/multiple heads fail before upgrade.
  Select an approved legacy-user policy only if required by migration preflight; leaving
  it `unspecified` does not activate users. Review lineage and backup/restore readiness first.
- `init-oauth`: confirmation `init-oauth:Development`, after migrations. Publish the native
  public JWKS, generate a random BFF client secret only if missing and register/verify its
  Argon2 hash and callback. Existing mismatches fail instead of rotating credentials.
- `register-client`: confirmation `register-client:Development`. Register/verify the
  selected client using an already present secret, without key/secret creation.

Leave `client_id`, `redirect_uri` and `client_secret_name` blank to preserve the configured
BFF behavior. For an approved additional client, supply all three together. Use a distinct
client ID, the exact HTTPS callback returned by the actual Foundry connection, and a
separate lowercase Key Vault name beginning `oauth-client-` (for example
`oauth-client-foundry-account-mcp` or `oauth-client-foundry-transaction-mcp`). The input is a secret
**name**, never its value. Partial overrides, BFF ID/secret reuse, unrelated secret names
and client inputs on migrations fail before Azure login. The Identity CLI additionally
validates callback metadata before reading credentials or writing records.

`register-client` requires that separate secret to exist already. `init-oauth` can initialize
it only with an approved real callback and the existing pinned signing key; it is not a
secret-only preparation command. Never register a placeholder callback to work around
Foundry's credential-before-callback creation order. Connection creation remains a separate
Foundry CLI/API operation, not an operation of this database workflow. When a CLI requires
secrets in command arguments, use an approved API request with credentials held only in
memory instead; never put a client secret in shell history or process arguments.

Terraform declares OAuth-secret writes only for `oauth-jwks` and `oauth-client-secret`.
Additional operations require approved effective Key Vault access; these inputs do not
grant access or provision connections. The Development Azure inspection also found an
inherited vault-wide Secrets User assignment on the inspected CD principal. Declared
secret-scoped grants alone therefore do not prove an effective access denial. Confirm
the actual workflow principal and inherited assignments before diagnosing or requesting
permissions; protected GitHub variable reads returned HTTP 403 during this session.
Any new grant requires separate authorization and should be secret-scoped. Keep Account
and Transaction client IDs and secrets distinct and preserve the BFF environment variables.
Publishing this workflow and dispatching it are separate owner actions. Registration does
not remove the native adapter gate or prove individual-grant binding.

Credentials stay in memory and masked runner channels, never workflow inputs, command
arguments, artifact files or local database backups. Migration subprocess output is
captured; failures expose only controlled stage/code diagnostics, never upstream
exception messages or tracebacks. See [workflow troubleshooting](../../docs/troubleshooting.md#manual-database-and-oauth-workflow-errors)
for corrected CLI errors and diagnostic interpretation. Initialization is not transactional
across Key Vault/PostgreSQL; mismatches require investigation rather than replacement. Restart/refresh
App Service secret references through a separately authorized rollout when necessary.
No workflow run, migration or runtime OAuth acceptance is implied by this configuration.

#### Federation is not network authorization

The operations workflow already uses GitHub OIDC for Azure login and reads secrets
through Key Vault SDK clients with that credential. No OAuth secret is copied into
GitHub, and the native signing private key is never exported. PostgreSQL currently uses
password authentication from `database-url`; Azure RBAC does not grant database DDL.

[PostgreSQL Entra authentication](https://learn.microsoft.com/azure/postgresql/security/security-entra-configure)
can also use the federated principal, but requires enabling server Entra authentication,
a separately authorized Entra administrator, a mapped PostgreSQL principal, and reviewed
schema/object ownership and migration privileges. None is provisioned by this workflow.
The banking email/password login and local PEM signing are independent and unchanged.

OIDC does not bypass TCP/DNS/firewall restrictions. The current Terraform server has
public networking, Entra disabled, and an existing broad `0.0.0.0` Azure-services rule;
that rule is not repository-scoped authorization or verified GitHub-runner access.
No dynamic GitHub IP rule is added. For a no-GitHub-IP route, select an approved runner
with actual network access: a self-hosted Azure runner or
[GitHub-hosted Azure VNet runner](https://docs.github.com/en/organizations/managing-organization-settings/configuring-private-networking-for-github-hosted-runners-in-your-organization)
with the applicable organization/plan, plus PostgreSQL private endpoint/DNS connectivity.
Runner hosting, private networking, Entra database grants and replacement of the existing
Azure-services rule require a separate approved infrastructure change; default
`ubuntu-latest` is not evidence of connectivity. Key Vault access also needs a reachable
endpoint. Do not dispatch remote operations until those prerequisites are verified.

### Hosted-agent variables (`cd-hosted-agent.yaml`)

`app/agent/azure.yaml` substitutes `${ACCOUNT_MCP_URL}`/`${TRANSACTION_MCP_URL}` at deploy
time, so set both under **Settings > Environments > Development > Variables** to the
deployed Account/Transaction App Service MCP endpoints
(`https://<app-hostname>/mcp`), alongside the other required variables below. The workflow
validates and injects them with `azd -C app/agent env set` the same way as the rest of this
list.

Required:

- `FOUNDRY_PROJECT_ENDPOINT`
- `AZURE_AI_PROJECT_ID`
- `MODEL_DEPLOYMENT_NAME`
- `ACCOUNT_MCP_URL`
- `TRANSACTION_MCP_URL`

The agent manifest fixes `AGENT_RUNTIME=foundry` and `MCP_IDENTITY_MODE=oauth`;
no OAuth private key, browser-client secret or public JWKS is consumed by the hosted
adapter. Do not copy these App Service settings into the agent or browser.
`account-mcp` and `transaction-mcp` must already be owner-managed OAuth2 project
connections in the selected Foundry project, using exactly `mcp.access` and separately
registered native confidential clients/platform callbacks. Local azd values alone do
not configure these connections, and CD does not register clients or replace connections.

The manifest also declares `banking-tools` as an agent dependency. Before agent-only
CD, a separately authorized setup must deploy that Toolbox and resolve its dependency
metadata. The current workflow neither deploys `banking-tools` nor injects
`TOOLBOX_BANKING_TOOLS_MCP_ENDPOINT` into its runner's agent azd environment. The five
required variables above therefore do not complete Toolbox dependency setup. Supply
verified resolved metadata through an explicit workflow change; do not invent an
endpoint or treat a local azd environment as CI configuration. This deployment metadata
is distinct from runtime OAuth support: setting it does not remove the adapter's gate.
See [deployment troubleshooting](../../docs/troubleshooting.md#deployment-oauth-and-federation-blockers)
for the missing-endpoint failure.

Development configuration on 2026-10-19 created both OAuth2 connections and published
`banking-tools` version `3` as default, retaining version `2` for rollback. Its verified
version-pinned endpoint is stored in the selected local agent azd environment only.
See the [infrastructure configuration record](../../infra/README.md#native-oauth-compatibility-gate)
for the exact callbacks, client IDs, secret names and endpoint. The native clients
remain unregistered in Identity, and the workflow's additional-client inputs must be
published before dispatching their protected registrations. This record does not prove
GitHub environment variable presence, CD metadata injection or hosted OAuth acceptance.
No hosted-agent deployment or model invocation occurred in that configuration step.

The workflow validates public HTTPS project/MCP URLs (MCP path `/mcp`) and records
the deployment boundary in its summary. The runtime adapter still **fails closed** for
OAuth Toolbox passthrough pending native banking-user/caller/grant binding and two-user
isolation proof. Artifact deployment or `agent show` is not hosted OAuth readiness.
Do not replace this gate with HMAC fallback, a shared grant or a fabricated call ID.

Optional (per-agent model deployments):

- `TRIAGE_MODEL_DEPLOYMENT_NAME`
- `ACCOUNT_MODEL_DEPLOYMENT_NAME`
- `TRANSACTION_MODEL_DEPLOYMENT_NAME`

The workflow forwards these existing GitHub Environment variables with
`azd -C app/agent env set`, including empty values when unset. They are not part of
its required-variable validation; `MODEL_DEPLOYMENT_NAME` remains required.

Optional (observability and tracing behavior):

- `ENABLE_INSTRUMENTATION`
- `ENABLE_SENSITIVE_DATA`
- `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING`
- `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`

### Frontend variables (`cd-frontend.yaml`)

Vite bakes `VITE_*` values into the bundle at build time, not at runtime, and `azd deploy web`
builds the bundle in the runner before upload. Two approaches were tried and don't work: Terraform
App Service application settings (the build never reads them, since the bundle is already built
before the zip reaches Azure; no such `VITE_*` app settings exist in `infra/main.tf` anymore) and a
root `azure.yaml` `postprovision` hook deriving them from Terraform outputs for local `azd deploy`
(removed, since this workflow is the only validated deploy path and never provisions). Set these
four directly under **Settings > Environments > Development > Variables**, using the deployed App
Service hostnames:

- `VITE_ACCOUNT_API_URL` (`https://<account-app-hostname>/api`)
- `VITE_TRANSACTION_API_URL` (`https://<transaction-app-hostname>/api`)
- `VITE_RESPONSES_BFF_URL` (`https://<responses-bff-app-hostname>`)
- `VITE_RESPONSES_API_URL` (`https://<responses-bff-app-hostname>/responses`)

OAuth uses the existing `VITE_RESPONSES_BFF_URL`; no new `VITE_OAUTH_*` variable,
Identity URL, client secret or direct agent endpoint belongs in the bundle. Build
preflight requires HTTPS URLs with the paths above, no trailing slash on the BFF
base, and the same BFF origin for OAuth/auth and `/responses` chat.

The workflow validates and injects them with `azd env set` before `azd deploy web --no-prompt`,
the same pattern `cd-hosted-agent.yaml` uses for `ACCOUNT_MCP_URL`/`TRANSACTION_MCP_URL`. Update
these four if the backing App Services are ever recreated (their default hostnames include a
non-deterministic suffix). This relies on `azd` >=1.23.7, pinned in the root `azure.yaml`'s
`requiredVersions`: before that release, `azd env set` values reached Docker builds and hooks
only, never non-Docker framework subprocesses like `npm run build`
(see [Azure/azure-dev#6903](https://github.com/Azure/azure-dev/issues/6903)), so Vite would
silently build with blank values instead of failing.

A second, independent problem produced the same symptom (the login page kept pointing at
`http://localhost:8080`) even after the above was fixed: the `web` service's `azure.yaml` entry
had no `dist` property, so `azd deploy web` packaged the entire project (`package.json`, `src/`,
`.env.local`, the already-built `dist/`) instead of only the built output. Azure's Kudu pipeline
auto-detects `package.json` in that zip and runs its own remote Oryx build
(`SCM_DO_BUILD_DURING_DEPLOYMENT=true` + `POST_BUILD_COMMAND=npm run build`), which re-bakes
`VITE_*` from whatever `.env.local` is sitting in the uploaded source, with no overriding
Application Settings on the server, and silently overwrites azd's already-correct local/CI build.
Confirmed directly from the App Service's own deployment log: `Running oryx build...` followed by
`Deployment successful ... Remote build.`. Fixed by setting `dist: dist` on the `web` service in
the root `azure.yaml`, so azd deploys only the built `dist/` output, with no `package.json` for
Oryx to detect; `infra/main.tf` was updated to match (`appCommandLine` serves
`/home/site/wwwroot` directly instead of a nested `dist/` subfolder, and the now-dead
`SCM_DO_BUILD_DURING_DEPLOYMENT`/`POST_BUILD_COMMAND` settings were removed for `web`).

## Hosted-agent workflow details

`cd-hosted-agent.yaml` performs these steps:

1. Installs Foundry `azd` extension (`microsoft.foundry`) in the runner.
2. Authenticates with Azure via OIDC.
3. Resolves `internal-identity-secret` from the shared Key Vault and masks it.
4. Creates/selects `development` `azd` environment in `app/agent`.
5. Validates required Foundry variables before deployment.
6. Sets environment variables with `azd -C app/agent env set ...`.
7. Deploys only `home-banking-agent`.
8. Prints agent status with `azd -C app/agent ai agent show --no-prompt`.

## Responses BFF workflow details

`cd-responses-bff.yaml` performs these steps:

1. Authenticates with Azure via OIDC and selects the root `development` azd environment.
2. Looks up the explicitly named app and validates resolved JWT/introspection/agent
   Key Vault references, explicit issuer/audience, a remote HTTPS Identity endpoint,
   and absence of `DATABASE_URL`, without printing setting values.
3. Deploys only `responses-bff`.

The BFF App Service uses its system-assigned managed identity to call Foundry. Terraform grants that identity the built-in `Foundry Agent Consumer` role and the project-scoped delegated user identity action required by the Responses endpoint.

Terraform configures the shared agent-transport secret, and consumer deployment preflights
check its Key Vault reference metadata; successful local signed identity propagation does
not prove that hosted delegated-user headers reach the agent.
Treat hosted identity transport as unverified until an end-to-end deployment test passes.
Hosted Toolbox OAuth compatibility remains unverified. Business-specific HITL is
implemented through the persisted dispute/customer-consent/assigned-operator workflow;
it is not a pending policy decision or generic Toolbox approval. Neither source
implementation nor deployment settings prove hosted or end-to-end acceptance.

## Operational notes

- These workflows are intentionally independent by path filter to avoid unrelated deploys.
- If no files in a workflow path change, that workflow does not run on push.
- Manual runs are enabled for `cd-hosted-agent.yaml` and `cd-responses-bff.yaml` (`workflow_dispatch`).
- All workflows are non-interactive (`--no-prompt`) and fail fast when required values are missing.

## Data CI workflow details

`ci-data.yml` validates the Python data module under `app/business-api/data` without acting as a style gate.

- Blocking checks:
  - Dependency install (`uv sync --dev --frozen --no-build`)
  - Python syntax/bytecode compile check (`python -m compileall -q .`)
  - Tests (`pytest -q tests --junitxml=junit.xml`)
- Non-blocking signals:
  - Coverage summary (only when `coverage.xml` exists)
  - Test and coverage artifact upload (`junit.xml`, optional `coverage.xml`)
- Not included by design:
  - Strict lint/style gating (for example max line length)
  - SonarQube scan

## Troubleshooting

- If deployment fails with auth/authorization errors, verify OIDC federation and role assignments for the `Development` environment identity.
- If hosted-agent deployment fails early, verify the five required Foundry variables and the separately resolved Toolbox dependency metadata described above.
- If `azd` cannot resolve a service name, check that the command is using the correct `azure.yaml` root (`-C app/agent` for hosted agent).
