# Terraform provisioning

The root `azure.yaml` runs `azd provision` against this directory and `azd deploy` for five Linux App Services: Identity, Account, Transaction, the Responses BFF, and the web frontend. The hosted agent has its own `app/agent/azure.yaml` and is not an App Service or a Terraform resource in this stack. Terraform uses an existing resource group and provisions one shared Linux plan, five web sites, Log Analytics, Application Insights, Blob storage, PostgreSQL, and a dedicated Foundry account and project. The separate Foundry azd project deploys the hosted agent; it consumes the project endpoint provisioned here.

The BFF binds its opaque conversation tokens to the authenticated user, but only forwards them upstream in local mode. Hosted mode does not yet link turns using platform conversation IDs or `previous_response_id`; see the [BFF conversation guide](../app/responses-bff/README.md#validation-and-limits). Before production use, verify the hosted agent and BFF identities have only the required Foundry permissions. Do not import role assignments targeting the removed legacy backend App Service identity for a different principal. Blob storage is still provisioned for existing business-service compatibility and remains publicly reachable unless a private endpoint or VNet path is designed separately.

## Prerequisites

Select a single subscription, resource group, and azd environment. The resource group and the remote-state storage account/container must already exist before the first provision; do not put application data in the state container. Grant the Terraform identity Storage Blob Data Contributor on the state container and appropriate permissions to provision resources in the resource group. Authenticate with Azure CLI as well as azd (`azd config set auth.useAzCliAuth true`), then set these azd values: `AZURE_SUBSCRIPTION_ID`, `AZURE_LOCATION`, `AZURE_RESOURCE_GROUP`, `RS_STORAGE_ACCOUNT`, and `RS_CONTAINER_NAME`. `AZURE_ENV_NAME` comes from the selected azd environment. Keep the state storage in the selected resource group. The remote state key is `banking-assistant.tfstate`; use one state for the one environment. The Terraform state backend uses Microsoft Entra authentication and Azure Blob lease locking.

The root Terraform files use `main.tfvars.json` for azd interpolation and `provider.conf.json` for remote state; do not put credentials in either file. For direct Terraform commands, pass equivalent variables in an untracked `.tfvars` file and initialize with `-backend-config` using real state account/container values instead of the `${...}` azd placeholders. Never commit state, plans, or secret values. The checked-in lock file pins resolved provider versions. The new Foundry account is created with system-assigned identity and project management enabled from its first request; the project-management setting cannot be enabled retroactively on an existing account.

## Existing resources

The checked-in `main.tfvars.json` sets `postgres_location = "centralus"` because
the East US 2 PostgreSQL capacity request was denied, and this provisioning path was
validated successfully in Central US. This override applies only to PostgreSQL.
Storage, monitoring, and Foundry retain the existing resource group region.
The separate `app_service_location` variable scopes a regional override to the shared
Linux plan and all five sites; it does not move PostgreSQL, storage, Key Vault,
monitoring, Foundry, or the resource group. Its default is null, preserving existing
deployments when invoking Terraform directly. The root azd path passes
`AZURE_APP_SERVICE_LOCATION` through `main.tfvars.json`; configure it explicitly in
the selected root environment before provisioning:

```powershell
azd env set AZURE_APP_SERVICE_LOCATION centralus
```

To retain the current App Service region through azd, set this value to that region
instead. Do not change `AZURE_LOCATION` to move only App Services. The SKU remains
controlled independently by `plan_sku` (default B2).

Changing an existing App Service region is not an in-place operation. All five sites
use `replace_triggered_by` on the plan's `location`: Terraform destroys attached sites
before replacing the plan, then recreates the sites and their managed-identity grants.
A SKU-only update does not trigger site replacement. Same-name regional migration
requires downtime and redeployment of all five application artifacts; provisioning
alone does not restore application code. Review `azd provision --preview` first:
existing sites must show replacement, not an in-place location update. After a partial
failure, refresh and review the plan again before retrying; do not manually delete the
plan while sites remain attached.

Consume the new hostname outputs, update the separate agent's MCP URLs, and rebuild
the frontend with the new service URLs before validating the application. A parallel
migration instead requires distinct plan/site names and preservation of the original
apps until cutover validation. Moving App Services does not establish a fix for
Foundry hosted-agent readiness errors.

A null or omitted PostgreSQL override uses the resource group region. Direct Terraform runs must
also pass `postgres_location = "centralus"` in their local variable file.

PostgreSQL server and database names are resolved from azd environment variables in
`main.tfvars.json` (`${POSTGRES_SERVER_NAME}` and `${POSTGRES_DATABASE_NAME}`) rather
than hardcoded values. The current low-cost baseline is Burstable
`B_Standard_B1ms` with `postgres_storage_mb = 32768` (32 GiB), which is the minimum
supported storage size returned by the Central US Burstable SKU catalog for this
subscription at provisioning time. If latency or throughput becomes a bottleneck,
increase `postgres_sku_name` (for example to `B_Standard_B2s` or `B_Standard_B2ms`)
and reprovision.

Cross-region database access adds latency and may incur data-transfer charges.
Catalog support and `terraform validate` do not guarantee capacity at creation time.
Changing the region of an existing server requires replacement and a separate data
migration.

PostgreSQL Flexible Server has `publicNetworkAccess = "Enabled"`, but the only firewall
rule created by default (`AllowAllAzureServicesAndResourcesWithinAzureIps`, the special
`0.0.0.0`/`0.0.0.0` range) permits Azure-hosted resources only, not an arbitrary public
IP. To reach the server directly from a local client such as pgAdmin, set the azd
environment value `POSTGRES_ALLOWED_CLIENT_IP` to that single public IP
(`azd env set POSTGRES_ALLOWED_CLIENT_IP <your-ip>`) before provisioning; `main.tfvars.json`
forwards it to `postgres_allowed_client_ip`, which creates one extra `AllowLocalClientIp`
firewall rule. Leave the value unset to keep the server reachable only from Azure. Rotate
or remove this value (and reprovision) whenever the allowed IP changes.

Before enabling `azd provision` against an existing Bicep deployment, inspect the actual resource IDs and names. Set `app_names` and the storage name override for existing resources; set `hostname_scope = null` when importing sites created without a unique hostname. Azure only accepts the unique hostname scope on site creation; changing it on an existing site requires replacing that site. Back up the existing deployment and Terraform state before any cutover. Import each managed resource (the plan, existing sites, monitoring, storage account, and container) with `terraform import -var-file=<local.tfvars> <address> <resource-id>` after initializing remote state. Example Terraform addresses: `azurerm_service_plan.main`, `azapi_resource.app["account"]`, `azurerm_log_analytics_workspace.main`, `azurerm_application_insights.main`, `azurerm_storage_account.content`, and `azurerm_storage_container.content`. The Responses BFF is a new dedicated resource at `azapi_resource.responses_bff`; do not import an unrelated backend site at that address. Terraform does not automatically import Bicep-managed resources. Review `terraform plan` for **zero unexpected deletes or replacements** before running `azd provision`. Do not run Bicep and Terraform provisioning concurrently on the same resources.

The app names, derived web URLs, and infrastructure settings are emitted as Terraform outputs for azd. Do not derive a web URL from the site name when unique hostnames are enabled; consume the `defaultHostName` output. The root environment also receives `AZURE_OPENAI_ENDPOINT`, `FOUNDRY_PROJECT_ENDPOINT`, `AZURE_RESPONSES_BFF_APP_NAME`, `RESPONSES_BFF_URI`, `AZURE_IDENTITY_APP_NAME`, and `IDENTITY_URI`. Root azd binds every service through `resourceName` to its corresponding `_APP_NAME` Terraform output. Standalone CD exports that same name into its azd environment; tags remain metadata, not deployment selectors. Identity uses dedicated address `azapi_resource.identity` and defaults to `app-identity-<environment>`; adopt/import an existing site deliberately before provisioning. `azd deploy --cwd app/agent` is the separate hosted-agent deploy path; plain root `azd deploy` targets only the five App Services.

Terraform deliberately does **not** set `VITE_*` App Service application settings for the
web frontend: Vite bakes those values into the bundle at build time, before `azd deploy web`
ever uploads it, so settings applied to the running App Service can never reach the already-built
bundle. `.github/workflows/cd-frontend.yaml` injects `VITE_ACCOUNT_API_URL`,
`VITE_TRANSACTION_API_URL`, `VITE_RESPONSES_BFF_URL`, and `VITE_RESPONSES_API_URL` with
`azd env set` immediately before that build step instead; see
[../.github/workflows/README.md](../.github/workflows/README.md#frontend-variables-cd-frontendyaml).

Terraform provisions a shared RBAC-authorized, purge-protected Key Vault and uses
versionless `@Microsoft.KeyVault(SecretUri=...)` app settings. System-assigned
identities receive `Key Vault Secrets User` at individual secret scopes:

| Service               | Permitted secrets                                                                                         |
| --------------------- | --------------------------------------------------------------------------------------------------------- |
| Identity              | `database-url`, `jwt-secret-key`, `auth-internal-secret`; separate key-scoped native signing grant        |
| Account / Transaction | `database-url`, `jwt-secret-key`, `auth-internal-secret`, `internal-identity-secret`, `oauth-jwks`        |
| Responses BFF         | `jwt-secret-key`, `auth-internal-secret`, `internal-identity-secret`, `oauth-client-secret`, `oauth-jwks` |

Supply the three authentication secrets through approved private secret-entry
operations, never command arguments, source, logs or Terraform secret-value data sources.
Identity reuses the same Terraform-managed `database-url` reference as Account and
Transaction; no second database secret or credential copy is required. Neither this
infrastructure nor deployment bootstraps users or runs migrations. Identity does not
consume the agent-only `internal-identity-secret`.

Terraform manages `database-url` for Identity, Account and Transaction using the PostgreSQL
administrator credential. Sharing that administrator principal is an explicitly approved
prototype tradeoff, not least-privilege database isolation. That URL/password resides in sensitive state; restrict backend
and saved-plan access. Sensitive marking does not remove data from state. The BFF has no
`DATABASE_URL` or database-secret assignment. Applying this change replaces existing
vault-wide consumer grants with secret-scoped grants; inspect the approved preview for
unexpected changes and audit inherited/manual RBAC and PostgreSQL grants separately.
These declarations alone do not prove deployed isolation or least-privilege DB access.

All three consumers receive HTTPS `AUTH_USERS_ENDPOINT` from Identity's actual hostname
and the same explicit `JWT_ISSUER`/`JWT_AUDIENCE` (defaults `home-banking-api` and
`home-banking-web`), JWT key and introspection secret. Identity's
`ACCESS_TOKEN_MINUTES` is explicitly set from Terraform's `access_token_minutes`
(default 60, matching Identity's application default) and must be an integer from
1–60. Expiry is absolute from issuance, not an inactivity timeout. This application
JWT lifetime is independent from MCP OAuth lifetimes; OAuth access tokens use a
3600-second bound independently. JWT and
introspection secrets must each satisfy the runtime's 32-character minimum. Account
and Transaction continue verifying browser JWTs independently of agent-only transport. Terraform sets
`CORS_ALLOWED_ORIGINS` for Account and Transaction to the deployed web app's origin
directly (no secret involved). Native App Service CORS is explicitly cleared on Account,
Transaction, and the BFF; their Python `CORSMiddleware` implementations are the sole CORS
owners. The BFF receives its allowed web origin through `ALLOWED_ORIGINS`.
Foundry hosted agents have no native Key Vault app-setting reference, so
`cd-hosted-agent.yaml` resolves `internal-identity-secret` itself with `az keyvault secret
show` before passing it to `azd -C app/agent env set`; see
[../.github/workflows/README.md](../.github/workflows/README.md). The BFF identity
receives `Foundry Agent Consumer` and a custom project-scoped role containing
`Microsoft.CognitiveServices/accounts/agents/UserIdentityImpersonation/action`. Do not send
Azure credentials or the delegated identity header from the browser.

Terraform owns the complete Account/Transaction and BFF app settings arrays through
`azapi_resource.app` and `azapi_resource.responses_bff`. Identity's full settings map
is owned exclusively by `azapi_resource.identity_app_settings`, a child `appsettings`
resource: the site must exist before its actual public hostname can become its OAuth
issuer without a self-reference cycle. The Identity site resource does not also write
an app settings array. There is no separate `api_cors` patch or `ignore_changes` rule.
Provisioning applies settings to both new and existing sites; declare persistent
changes in Terraform rather than patching settings manually, because a later
provision can overwrite those manual changes.

### MCP OAuth deployment configuration

Set the new nonsecret inputs in the selected **root** azd environment before an
explicitly authorized provision. `main.tfvars.json` maps all three values explicitly;
direct Terraform defaults `oauth_audience` to `banking-mcp` but requires
`oauth_client_id` and `oauth_signing_key_id`. The signing key ID is public metadata,
passed directly to Identity's `OAUTH_SIGNING_KEY_ID`, not a Key Vault secret. It must
be nonblank, at most 128 characters, and match the native key's published JWKS key ID:

```powershell
rtk proxy azd env set OAUTH_AUDIENCE banking-mcp
rtk proxy azd env set OAUTH_CLIENT_ID <registered-confidential-bff-client-id>
rtk proxy azd env set OAUTH_SIGNING_KEY_ID <public-signing-key-id>
```

Terraform derives `OAUTH_ISSUER` from Identity's actual public HTTPS hostname,
`OAUTH_FRONTEND_AUTHORIZE_URL` from the web hostname plus `/oauth/consent`, and
`OAUTH_REDIRECT_URI` from the web hostname plus `/oauth/callback`. These names and
`OAUTH_AUDIENCE`/`OAUTH_CLIENT_ID` are nonsecret outputs for subsequent configuration;
Terraform does not register an OAuth client or copy outputs into GitHub Variables.
The owner must register that exact HTTPS callback and the exact `mcp.access` scope
with Identity through separately authorized provisioning, not this Terraform stack.

Remote Identity now uses native Key Vault signing. Terraform creates the nonexportable
RSA-2048 `oauth-signing` key with `sign`/`verify` operations, pins its version in
`OAUTH_SIGNING_KEY_URI`, selects `OAUTH_SIGNING_BACKEND=key_vault`, and grants Identity
key-scoped Crypto User access. Both Identity Crypto User and operations Key Vault Reader
assignments use `resource_versionless_id` (`/keys/oauth-signing`), not the versioned
`resource_id` (`/keys/oauth-signing/versions/...`). The signing URI remains pinned to
its exact version independently of the RBAC scope. The private key never reaches Terraform state or Identity.
The provisioning principal needs Crypto Officer access (in addition to the existing
Secrets Officer grant); `deployer_principal_id` optionally creates both assignments.
No automatic key rotation is configured; changing the key requires coordinated public
JWKS publication and redeployment/restart, not an implicit rollover.

Manual operations select a federated principal through dedicated configuration;
Development currently reuses the regular CD principal. A separate operations principal
requires an explicitly approved isolation change.
Set root azd `AZURE_OPERATIONS_PRINCIPAL_ID` to its object ID for Terraform grants;
set protected GitHub Development `AZURE_OPERATIONS_CLIENT_ID` to its application ID
for OIDC login. Terraform grants resource-group Reader, key-scoped Key Vault Reader
(public key only, no signing), database-url Secrets User, and Secrets Officer only
on `oauth-jwks` and `oauth-client-secret`. Existing/inherited permissions must be
audited separately. Configure environment approval and federated credentials outside
this stack; runner connectivity to the verified PostgreSQL target is a prerequisite.

Local Identity retains default `OAUTH_SIGNING_BACKEND=pem` and its existing private PEM;
no local configuration or database restore is required. Initialize the remaining entries
with the explicit manual OAuth database operation before consumer deployment. Terraform
creates versionless references and secret-scoped grants, never reads or stores these
OAuth secret values, and exposes no secret outputs:

| Key Vault entry       | App setting           | Consumers                 | Required value                                                                             |
| --------------------- | --------------------- | ------------------------- | ------------------------------------------------------------------------------------------ |
| `oauth-client-secret` | `OAUTH_CLIENT_SECRET` | BFF only                  | Secret matching the registered confidential client                                         |
| `oauth-jwks`          | `OAUTH_JWKS`          | BFF, Account, Transaction | Public-only JWKS JSON matching Identity's signing key and key ID, not a URL or private key |

Identity consumes no `MCP_IDENTITY_MODE` setting. BFF, Account and Transaction are
explicitly configured with `MCP_IDENTITY_MODE=oauth`; BFF retains
`RESPONSES_UPSTREAM_MODE=foundry`. Existing HMAC references and application JWT and
introspection settings remain independent. Invalid OAuth never falls back to HMAC.
No private signing key or confidential client secret is sent to MCP services or the
agent, and no agent configuration is managed here. Keep public JWKS synchronized
with signing-key changes; versionless references do not prove immediate runtime
refresh or rotation readiness.

The hosted/native Toolbox OAuth path has offline consent transport, but
banking-user/native-caller/grant binding, callback/S256 compatibility and two-user
isolation remain unproven. These deployment declarations do not establish hosted
readiness; do not route the path through a shared grant or an HMAC fallback.
The operations identity reuses the existing GitHub CD principal, including scoped
OAuth-secret write access. GitHub variables, code rollout and runtime verification
remain separate gates. Workflow success for migration and `init-oauth` is not hosted
OAuth acceptance. App Service can cache a `SecretNotFound` status for a Key Vault
reference after the secret exists; refresh the references and read their status to
confirm `Resolved`. Resolved references do not prove token exchange or hosted OAuth
readiness. Terraform's scoped operations grants are not the complete effective
permission inventory: the CD principal can also hold inherited vault-wide Secrets User
access. Confirm the actual runner identity and inherited access before adding roles.

### Foundry MCP connections and Toolbox

Root Terraform provisions the Foundry account/project, not the OAuth project connections
or Toolbox. The separate [agent manifest](../app/agent/azure.yaml) declares Account and
Transaction MCP tools in `banking-tools`, referencing `account-mcp` and `transaction-mcp`.
For a separately authorized configuration/deployment:

1. Create those OAuth2 connections in the selected Foundry project using the public
   Account/Transaction HTTPS `/mcp` endpoints and exact `mcp.access` scope.
2. Obtain each connection's actual platform callback and register compatible native
   confidential clients in Identity. Do not guess callbacks or automatically reuse the
   BFF client/secret, whose callback belongs to the frontend.
3. Deploy and explicitly publish `banking-tools`, then verify its resolved dependency
   metadata in the selected agent azd environment. Configuration-only Toolbox deployment
   can precede client registration, but does not make OAuth usable. Before a separately
   authorized `home-banking-agent` rollout, the deployment-only GitHub runner must also
   receive that metadata; local azd configuration is not copied into CI.
4. Verify native callback/S256 compatibility, banking-user/caller/grant binding and
   two-user isolation before enabling the currently rejected Toolbox OAuth runtime path.

The [hosted-agent workflow guide](../.github/workflows/README.md#hosted-agent-variables-cd-hosted-agentyaml)
describes the implemented endpoint handoff and remaining runner/runtime verification. Creating connections or deploying the Toolbox does not
remove the runtime gate or authorize shared grants, fabricated call IDs or HMAC fallback.

#### Native OAuth compatibility gate

- The BFF system-assigned identity needs project-scoped `Foundry Agent Consumer` and
  the explicit `UserIdentityImpersonation/action` permission. These assignments do
  not establish individual customer/native OAuth grant binding.
- A no-auth Toolbox experiment with inline MCP URLs, no `project_connection_id` and
  trailing `/mcp/` paths is not the OAuth Toolbox declared by the agent manifest. Do not
  supply its endpoint as OAuth dependency metadata.

Both connections are `RemoteTool`/`OAuth2` with exact `mcp.access` scope and
independent credentials held in Key Vault and Foundry, never exposed:

| Connection        | Identity client ID        | Key Vault secret                       | Actual platform callback                                                          |
| ----------------- | ------------------------- | -------------------------------------- | --------------------------------------------------------------------------------- |
| `account-mcp`     | `foundry-account-mcp`     | `oauth-client-foundry-account-mcp`     | `https://global.consent.azure-apim.net/redirect/ae31090f0320449ebac04370824883b7` |
| `transaction-mcp` | `foundry-transaction-mcp` | `oauth-client-foundry-transaction-mcp` | `https://global.consent.azure-apim.net/redirect/af3efbb2af8444ee90ad67cfabb0c889` |

These callbacks come from the actual connections, not inference. Both clients require
protected `register-client` execution in Identity; connection creation and enabled
secrets do not prove registration or callback/S256 interoperability.

`azd deploy banking-tools --no-prompt --cwd app/agent` creates a Toolbox version from the
agent manifest, with `project_connection_id` references and the existing tool allowlists.
Deployment does not promote the version: `azd ai toolbox publish banking-tools <version>
--cwd app/agent` makes it the default, and republishing an earlier version rolls back.
These commands affect only the Toolbox, not the hosted agent or model deployments.
Deployment preview is unsupported for the `azure.ai.toolbox` host.

The agent azd environment stores `TOOLBOX_BANKING_TOOLS_MCP_ENDPOINT`. Hosted CD requires
a nonblank endpoint from the Development GitHub Environment and exports it to its agent
azd environment without enforcing URL format or probing availability. Local azd
configuration does not populate that GitHub variable; set it explicitly. For
default-version consumption use
`https://aifdevelopment58b174.services.ai.azure.com/api/projects/foundry-development/toolboxes/banking-tools/mcp?api-version=v1`;
use the `.../banking-tools/versions/<version>/mcp?api-version=v1` form for pinned tests.
Publishing or rolling back the Toolbox default retargets default-version consumers
without changing that variable. A version-pinned consumer needs its endpoint updated
on rollback. The mapping is covered by offline regressions, not a successful hosted
CD run, native runtime identity support or per-user OAuth compatibility proof.
Do not rotate or replace the native credentials on retry. Rotation, revocation,
connection replacement and Identity client changes require separately approved,
coordinated operations; no automatic rollback across these systems is implemented.

[Foundry session delegation](https://learn.microsoft.com/azure/foundry/agents/how-to/isolate-sessions-per-user#isolate-sessions-for-your-own-users)
accepts an opaque stable end-user identifier of 1–256 characters with a restricted
character set in `x-ms-user-identity`. The current BFF instead sends its signed
banking envelope, including the local OAuth grant in OAuth mode. That envelope is
not a proven native delegation identifier. Changing it alone would also remove the
agent's existing authenticated banking context; do not replace it without a reviewed
separate transport and banking-user/native-caller/grant binding contract.

[Toolbox authentication](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/tool-authentication#walkthrough-oauth-identity-passthrough)
requires the real hosted `x-agent-foundry-call-id` and documents same-tenant Entra
consumer requirements for per-user OAuth. Custom-user session delegation alone does
not establish compatibility with that OAuth grant resolution. Keep foundry/oauth
rejected until this distinction and actual callback/S256/client authentication are
proven. Connection and Toolbox configuration do not register Identity clients or
perform a hosted-agent deployment, consent flow, MCP call or model invocation.
Successful `register-client` runs do not establish native OAuth compatibility;
two-user hosted isolation remains open
in [#81](https://github.com/cristofima/AI-Banking-Assistant-AgentFramework/issues/81).

Account starts with `python -m uvicorn banking_account.main:app --host 0.0.0.0 --port 8080 --proxy-headers`;
Transaction uses `banking_transaction.main:app` with the same options.
Uvicorn reads `FORWARDED_ALLOW_IPS=*` from the App Service environment
to trust the forwarded HTTPS scheme. Keeping the wildcard out of the startup command
avoids shell expansion by Oryx. This trust setting is scoped to the controlled App Service
proxy environment. These commands import the qualified package's `main:app`; they do not execute the
`if __name__ == "__main__"` block used by `python -m banking_account.main`
or `python -m banking_transaction.main` locally.

Changes to these startup commands or app settings require provisioning, not a service
code deploy. Code or dependency changes still require deployment. After provisioning,
verify service startup, HTTPS redirects, and browser CORS preflight responses; successful
Terraform validation alone does not prove runtime health.

By convention in this repository, the frontend App Service name is `app-banking-web-<env>` (for example, `app-banking-web-development`). Keep this explicit naming when adding environments so the web workload is distinguishable from account/transaction services.

Identity starts with `python -m uvicorn identity.main:create_app --factory --host 0.0.0.0 --port 8000`
on Python 3.11 with Oryx enabled. Apply forward Identity migrations through the approved
head with a separate authorized principal before admitting traffic; never migrate at
startup. Provisioning changes consumers' settings, so use an approved maintenance window:
preview first, migrate, provision, deploy Identity, verify resolved references and bounded
DB/schema readiness, then deploy compatible consumers and require fresh login. CD does
not probe OpenAPI; endpoint exposure and a readiness contract are deferred. Metadata
preflight and deployment success do not prove startup, database access or login.

All root services deploy by explicit App Service name in the configured resource
group. Set GitHub `Development` Variables manually from the identically named
Terraform outputs: `AZURE_IDENTITY_APP_NAME`, `AZURE_ACCOUNT_APP_NAME`,
`AZURE_TRANSACTION_APP_NAME`, `AZURE_RESPONSES_BFF_APP_NAME`, and
`AZURE_WEB_APP_NAME`. Terraform does not publish these GitHub Variables automatically.
The root manifest uses those same `_APP_NAME` azd variables in `resourceName`;
shared CD validates each GitHub value and exports it under that same name before
deployment. Python-service preflight reads that exact app with `az webapp show`,
never tag-based discovery.

The existing secret-scoped Identity `database-url` grant is reconciled with
`azurerm_role_assignment.identity_key_vault_secrets_user["database-url"]` in the
approved remote Terraform state. If provisioning reports
`409 RoleAssignmentExists` for a pre-existing grant, verify its principal, role and
secret scope, then import its full role-assignment resource ID at the matching
Terraform address in the correct backend and workspace. Review the plan before
provisioning; do not delete or recreate the existing permission to resolve a state
mismatch. Resolved references and successful provisioning do not prove runtime readiness.

Retain compatible artifacts/configuration references before rollout. Rollback must preserve
the forward schema, current JWT contracts and introspection; never restore BFF database
access or bypass Identity. If Identity is unavailable, keep consumers fail-closed and repair
it. A database restore or schema downgrade requires separate approval.

## Checks

Run `terraform fmt -check -recursive` and `terraform validate` after initializing the providers. `terraform plan` and `azd provision` require Azure credentials, a pre-created backend, and the selected subscription; validation alone does not create or import any Azure resources. `azd deploy` requires successfully provisioned App Services and a working zip/build pipeline for the frontend and each service. Do not run `azd down` on the existing resource group as a rollback strategy.

Sources: [azd Terraform setup](https://learn.microsoft.com/en-us/azure/developer/azure-developer-cli/use-terraform-for-azd) and [Azure App Service unique default hostnames](https://techcommunity.microsoft.com/blog/appsonazureblog/public-preview-creating-web-app-with-a-unique-default-hostname/4156353).
