# 🛠️ Troubleshooting

When provisioning Azure resources with `azd`/Terraform, you may hit errors that stop or
delay deployment. This lists the ones relevant to this repository's actual stack
(Terraform, Azure App Service, Azure Database for PostgreSQL Flexible Server, Azure
Monitor, and a Foundry account/project) — not generic Azure error documentation for
services this repository does not provision (no Container Apps, Azure Container
Registry, Cosmos DB, or Azure Search). Key Vault holds application secrets and the
native OAuth signing key; workflow diagnostics for those resources are included below.

> [!NOTE]
> This repository has two azd project roots. In the command examples below, run `azd`
> from the repository root for the App Service stack (`./azure.yaml`), and use
> `--cwd app/agent` for hosted-agent operations (`./app/agent/azure.yaml`).

## Manual database and OAuth workflow errors

Use the [manual workflow](../.github/workflows/manual-database-operations.yaml) and its
[operation guide](../.github/workflows/README.md#manual-database-and-oauth-operations) for dispatch
configuration. The following CLI defects were corrected in that workflow:

| Error                                                 | Cause                                                                 | Applied correction                                                                                                        |
| ----------------------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `uv sync` rejects `--no-env-file`                     | The flag belongs to `uv run`, not `uv sync`.                          | Removed it from both dependency sync commands; preserved `--frozen` and `uv run --no-env-file`.                           |
| `unrecognized arguments: --database-name banking_dev` | The runner's Azure CLI database lookup no longer accepts that option. | Changed `az postgres flexible-server db show` to use `--name` for the database, retaining `--server-name` for the server. |

Offline command checks cover syntax and operation dispatch, not Azure access. A successful
migration does not verify signing-key permissions, OAuth secret publication or client registration.

### Controlled remote-operation rejection

`Remote operation rejected; check authorized configuration, access and schema.` was the
original generic Python error. If ARM lookups completed before this message, the failure
was inside the Python operation, even though the workflow step is named Verify Azure Target.
The original message alone cannot identify the cause.

The updated [remote operation](../app/business-api/identity/src/identity/services/oauth_remote.py)
reports `stage=<stage>; code=<code>` without upstream messages, database credentials or
tracebacks. Publish/select that revision before re-running the authorized operation.

| Diagnostic                                                  | What to check                                                                                                                                                     |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AZURE_AUTHENTICATION_FAILED`                               | The workflow credential's authentication at the named stage.                                                                                                      |
| `AZURE_ACCESS_DENIED`                                       | Existing scoped Key Vault permissions and network policy for the operations principal; do not grant broad access as a workaround.                                 |
| `AZURE_RESOURCE_NOT_FOUND`                                  | The named stage's expected resource, including the pinned signing-key version. Missing optional OAuth secrets are handled normally, not reported as this failure. |
| `AZURE_CONFLICT`, `AZURE_THROTTLED`, `AZURE_REQUEST_FAILED` | Azure resource/request state at the named stage; the code does not identify the exact service-side cause.                                                         |
| `VALIDATION_FAILED`                                         | Configuration or existing state at the named stage, including exact vault/key metadata, database target, JWKS or client-secret compatibility.                     |
| `DATABASE_OPERATION_FAILED`                                 | PostgreSQL connectivity, schema and permissions at database-client registration or migration. Azure RBAC does not establish database privileges.                  |
| `MIGRATION_PROCESS_FAILED`, `IO_FAILED`                     | Migration subprocess prerequisites or local runner I/O at the named stage; raw subprocess output remains hidden.                                                  |

Initialization separates signing-key configuration/read/validation, JWKS
read/validation/publication, client-secret read/validation/publication and
`database-client-registration`. Nested diagnostics retain the innermost failing stage.
These codes do not authorize credential rotation, schema repair or permission expansion.

### Signing-key read denied despite an existing role assignment

The Development failure `stage=signing-key-read; code=AZURE_ACCESS_DENIED` was traced
to version-scoped signing-key role assignments. Terraform used the key's versioned
`resource_id` instead of its versionless key scope. The correction uses
`resource_versionless_id` (`/keys/oauth-signing`) for operations Key Vault Reader and
Identity Key Vault Crypto User; `OAUTH_SIGNING_KEY_URI` remains pinned to the exact
key version. Do not broaden access to the whole vault or rotate the key to bypass this.

The bounded Terraform repair was applied and both replacement assignments were
verified in Azure. The owner subsequently confirmed successful Development migration
and `init-oauth`. This does not verify consumer Key Vault reference resolution,
Foundry connection setup or hosted/two-user OAuth acceptance. See the
[infrastructure OAuth guide](../infra/README.md) for permission ownership.

### Missing OAuth secrets and an empty client table

This is valid before first initialization. `init-oauth` reads and validates `database-url`,
reads the existing version-pinned RSA signing key, publishes its public `oauth-jwks`,
creates `oauth-client-secret` only if absent, then registers/verifies the client's Argon2
secret hash and exact HTTPS callback in `oauth.clients`. It does not apply migrations,
create certificates or export the private signing key.

Missing both OAuth secrets and the client suggests a failure before publication, but
resource inventory alone is not an exact diagnosis. Initialization is not transactional
across Key Vault and PostgreSQL: a later failure can leave secrets published without a
client row. Identical reruns reconcile unchanged state; existing mismatches fail rather
than rotate secrets or reactivate clients. Do not insert a client manually or repeat
migrations to bypass an undiagnosed initialization failure.

## Deployment OAuth and federation blockers

The following deployment failures were investigated separately; their resolution is not
established by the manual workflow CLI fixes or offline diagnostic tests.

| Error                                                                                     | Diagnosis and next check                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Key Vault reference OAUTH_JWKS must be Resolved before deployment` with `SecretNotFound` | The referenced `oauth-jwks` is missing. Complete authorized OAuth initialization, then verify the App Service reference resolves under its own managed identity. Keep the preflight fail-closed; refreshing references requires an authorized rollout.                                                                                                                                                                                 |
| `AADSTS700213: No matching federated identity record`                                     | Compare the actual GitHub assertion's subject, issuer and audience with the configured Azure federation, including repository/environment identity changes. A later run passed login, but that does not prove every workflow principal is configured correctly.                                                                                                                                                                        |
| `TOOLBOX_BANKING_TOOLS_MCP_ENDPOINT is not set`                                           | Hosted-agent deployment lacks resolved Toolbox dependency metadata. For local deployment, check the selected agent azd environment and dependency deployment. Hosted CD requires a nonblank Toolbox endpoint in the Development GitHub Environment before exporting to azd; it does not enforce URL format or probe availability. Use the consumer endpoint for the published default or a version-specific endpoint for pinned tests. |

Toolbox provisioning is separate from banking-user OAuth binding. The runtime rejects
unverified Toolbox OAuth passthrough; deploying a Toolbox does not establish supported
callbacks, per-user grant isolation or hosted OAuth acceptance. Preserve the direct-MCP
OAuth boundary and do not fall back to HMAC on an OAuth failure.

## Python installed-package import errors

After the package migration, a missing `banking_account`, `banking_transaction`,
`identity`, `bff` or `app` import usually requires checking the selected service
environment and installation, not restoring flat modules or changing `sys.path`.

1. Select that project's `.venv` in VS Code and sync its frozen dependencies.
   For example, from repository root:

   ```powershell
   rtk proxy uv sync --directory app\business-api\account --frozen --group dev
   ```

2. Use the package-qualified entrypoint from the
   [Account](../app/business-api/account/README.md#package-layout-and-local-startup),
   [Transaction](../app/business-api/transaction/README.md#package-layout-and-local-startup),
   [Identity](../app/business-api/identity/README.md#configuration-and-local-tooling),
   [BFF](../app/responses-bff/README.md) or [Agent](../app/agent/README.md#local-setup)
   guide. Keep nested namespaces such as `banking_account.services.products`.
3. For ZIP deployment, check that the root
   [packaging hooks](../azure.yaml) staged the complete application package and
   shared source. The old `main:app` target is not the migrated service entrypoint;
   see [deployment startup targets](deployment-guide.md#python-package-staging-and-startup).

Do not use `PYTHONPATH`, copy compatibility modules into `src`, or flatten
responsibility subpackages to mask an installation error. Offline package checks
and local endpoint checks do not establish chatbot or hosted acceptance.

## ⚡ Most Frequently Encountered Errors

| Error Code                            | Common Cause                                    | Full Details                                            |
| ------------------------------------- | ----------------------------------------------- | ------------------------------------------------------- |
| **InsufficientQuota**                 | Not enough quota available in subscription      | [View Solution](#quota--capacity-limitations)           |
| **MissingSubscriptionRegistration**   | Required feature not registered in subscription | [View Solution](#subscription--access-issues)           |
| **ResourceGroupNotFound**             | RG doesn't exist or using old `.env` file       | [View Solution](#resource-group--deployment-management) |
| **DeploymentModelNotSupported**       | Foundry model not available in selected region  | [View Solution](#regional--location-issues)             |
| **ResourceNotFound**                  | Resource does not exist or cannot be found      | [View Solution](#resource-identification--references)   |
| **SpecialFeatureOrQuotaIdRequired**   | Subscription lacks access to a specific model   | [View Solution](#subscription--access-issues)           |
| **Conflict - No available instances** | App Service plan has insufficient capacity      | [View Solution](#quota--capacity-limitations)           |
| **AccountProvisioningStateInvalid**   | Resource used before provisioning completed     | [View Solution](#resource-state--provisioning)          |
| **RequestDisallowedByPolicy**         | Azure Policy blocking the requested operation   | [View Solution](#subscription--access-issues)           |

## 📖 Table of Contents

- [Manual database and OAuth workflow errors](#manual-database-and-oauth-workflow-errors)
- [Deployment OAuth and federation blockers](#deployment-oauth-and-federation-blockers)
- [Python installed-package import errors](#python-installed-package-import-errors)
- [Subscription & Access Issues](#subscription--access-issues)
- [Quota & Capacity Limitations](#quota--capacity-limitations)
- [Regional & Location Issues](#regional--location-issues)
- [Resource Naming & Validation](#resource-naming--validation)
- [Resource Identification & References](#resource-identification--references)
- [Resource Group & Deployment Management](#resource-group--deployment-management)
- [Resource State & Provisioning](#resource-state--provisioning)

## Subscription & Access Issues

| Issue/Error Code                                                          | Description                                                        | Steps to Resolve                                                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ReadOnlyDisabledSubscription**                                          | Subscription is disabled or in read-only state                     | Check if you have an active subscription before starting the deployment. Depending on the subscription type, it may need to be reactivated; see [Reactivate a disabled Azure subscription](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/subscription-disabled). |
| **Unauthorized - Operation cannot be completed without additional quota** | Insufficient quota for the requested operation                     | Check usage with `az vm list-usage --location "<Location>" -o table`; request more via [VM Quota Request](https://techcommunity.microsoft.com/blog/startupsatmicrosoftblog/how-to-increase-quota-for-specific-types-of-azure-virtual-machines/3792394).                                   |
| **CrossTenantDeploymentNotPermitted**                                     | Deployment across different Azure AD tenants not allowed           | Confirm your deployment identity and the target resource group are in the same tenant (`az account show`, `az group show --name <RG_NAME>`); for CI/CD, confirm the service principal belongs to the same tenant and has permissions on the resource group.                               |
| **RequestDisallowedByPolicy**                                             | Azure Policy blocking the requested operation                      | An Azure Policy is preventing the action; see [RequestDisallowedByPolicy](https://learn.microsoft.com/en-us/troubleshoot/azure/azure-kubernetes/create-upgrade-delete/error-code-requestdisallowedbypolicy).                                                                              |
| **SpecialFeatureOrQuotaIdRequired**                                       | Subscription lacks access to a specific Azure OpenAI/Foundry model | Submit a request via the [Azure OpenAI Model Access Request](https://customervoice.microsoft.com/Pages/ResponsePage.aspx?id=v4j5cvGGr0GRqy180BHbR7en2Ais5pxKtso_Pz4b1_xUQ1VGQUEzRlBIMVU2UFlHSFpSNkpOR0paRSQlQCN0PWcu) form for restricted models (for example `gpt-5`, `o3`).             |
| **ResourceProviderError**                                                 | Resource provider not registered in subscription                   | Register it; see [Register Resource Provider](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-register-resource-provider?tabs=azure-cli).                                                                                                            |

## Quota & Capacity Limitations

| Issue/Error Code                                              | Description                                               | Steps to Resolve                                                                                                                                                                                                                                                                         |
| ------------------------------------------------------------- | --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **InsufficientQuota**                                         | Not enough quota available in the subscription            | Check quota with `Get-AzVMUsage -Location "<region>"` (PowerShell) or `az vm list-usage`, then request more if needed.                                                                                                                                                                   |
| **Conflict - No available instances to satisfy this request** | Azure App Service has insufficient capacity in the region | Wait and retry `azd up` after 15-30 minutes, deploy to a different region (set `AZURE_LOCATION` and re-run `azd env new`/`azd up`), or move to a higher App Service SKU/tier. See [Azure App Service Plans](https://learn.microsoft.com/en-us/azure/app-service/overview-hosting-plans). |
| **SkuNotAvailable**                                           | Requested SKU not available in the selected location/zone | Check SKU availability with `az appservice list-locations --sku <sku-name>` and pick a supported region or SKU.                                                                                                                                                                          |

## Regional & Location Issues

| Issue/Error Code                                         | Description                                                                   | Steps to Resolve                                                                                                                                                                                                                                                                     |
| -------------------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **LocationNotAvailableForResourceType**                  | Resource type not supported in the selected region                            | Verify availability with `az provider show --namespace <provider-namespace> --query "resourceTypes[?resourceType=='<resource-type>'].locations" -o table`, or check [Azure Products by Region](https://azure.microsoft.com/en-us/explore/global-infrastructure/products-by-region/). |
| **InvalidResourceLocation**                              | Cannot change region for already-deployed resources                           | Azure resources cannot change region after creation. Either delete and redeploy (`azd down --force --purge` then `azd up`), or create a new environment in a different region (`azd env new <name>`, `azd env set AZURE_LOCATION <region>`, `azd up`). Back up critical data first.  |
| **DeploymentModelNotSupported / ServiceModelDeprecated** | Foundry/Azure OpenAI model not supported or deprecated in the selected region | Verify model/region availability in [Azure AI Foundry models](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/models).                                                                                                                                            |

## Resource Naming & Validation

| Issue/Error Code                      | Description                                      | Steps to Resolve                                                                                                                                                                                                  |
| ------------------------------------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ResourceNameInvalid**               | Resource name violates naming convention rules   | Confirm the name is within the allowed length/characters for that resource type; see [Resource Naming Convention](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules). |
| **Workspace Name - InvalidParameter** | Workspace name does not meet the required format | Must start/end with an alphanumeric character, only `a-z`/`0-9`/`-`, no spaces/underscores/periods, unique within region and subscription, 3-33 characters.                                                       |

## Resource Identification & References

| Issue/Error Code                                                                 | Description                                                              | Steps to Resolve                                                                                                                                                                                                                                                                                                   |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **ResourceNotFound / ParentResourceNotFound / DeploymentOutputEvaluationFailed** | Invalid or non-existent resource ID reference                            | Verify the resource ID format and that it exists, and that its provisioning state is `Succeeded` (`az resource show --ids <Resource ID> --query "properties.provisioningState"`). See [Resource Not Found errors](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-not-found). |
| **PrincipalNotFound**                                                            | Principal ID does not exist in the Azure AD tenant, or replication delay | Verify the principal exists in the same tenant (`az ad sp show --id <object-id>`); if just created, wait a few minutes and retry; explicitly set `principalType` in Terraform/ARM to avoid replication-delay issues.                                                                                               |

## Resource Group & Deployment Management

| Issue/Error Code              | Description                                                      | Steps to Resolve                                                                                                                                                                                                          |
| ----------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ResourceGroupNotFound**     | Specified resource group does not exist                          | Confirm the resource group exists in the Azure Portal, or create a new environment before redeploying: `azd env new <env-name>` (this error can also happen when reusing a stale `.env` file from a previous deployment). |
| **ResourceGroupBeingDeleted** | Resource group is currently being deleted                        | Wait for deletion to finish, then create a new one or use a different resource group.                                                                                                                                     |
| **DeploymentActive**          | Another deployment is already in progress in this resource group | Wait for the in-progress deployment to finish before starting a new one.                                                                                                                                                  |
| **DeploymentCanceled**        | Deployment was canceled before completion                        | Check the deployment history in the Azure Portal (Resource Group → Deployments) for the root cause, fix it, and retry.                                                                                                    |

## Resource State & Provisioning

| Issue/Error Code                              | Description                                                                         | Steps to Resolve                                                                                                                                                                                                                                                                                                                            |
| --------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **AccountProvisioningStateInvalid**           | Resource used before provisioning completed                                         | Wait until the resource's provisioning state is `Succeeded` before using it.                                                                                                                                                                                                                                                                |
| **FlagMustBeSetForRestore / NameUnavailable** | Soft-deleted Cognitive Services/Foundry resource requires a restore flag or a purge | Either restore (`"restore": true` in the resource definition) or purge the deleted resource first: `az cognitiveservices account purge --name <name> --resource-group <rg> --location <location>`. See [Soft delete and resource restore](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/delete-resource-group). |

---

💡 If you encounter an error not listed here, check the
[Common Deployment Errors](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/common-deployment-errors)
documentation, or [Terraform's azurerm provider troubleshooting](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs).
