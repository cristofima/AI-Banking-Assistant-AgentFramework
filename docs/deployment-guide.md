# Deployment Guide

> [!WARNING]
> Provisioning and hosted identity transport have not been verified end to end for the
> Development environment. Review the Terraform plan and existing resource ownership before
> running these commands. The root project targets App Service through Terraform; the
> hosted agent uses a separate azd project at `app/agent/azure.yaml`.

## **🚀 Quick Start**

Clone [this fork](https://github.com/cristofima/AI-Banking-Assistant-AgentFramework)
and work from its repository root. Initializing the upstream sample does not reproduce
this fork's Identity, BFF, PostgreSQL or operator workflow.

First complete the [infrastructure prerequisites](../infra/README.md), including
remote Terraform state, environment configuration and existing-resource ownership.
A fresh checkout is not sufficient for an automatic deployment. Review the plan,
database migration/grant requirements and coordinated rollout before authorizing
provisioning; the commands below describe that authorized deployment path.

1. Run

   ```powershell
   rtk proxy azd auth login
   ```

2. Run

   ```powershell
   rtk proxy azd up
   ```

For the hosted-agent stack, run from repository root:

```powershell
rtk proxy azd up --cwd app\agent
```

- Root `azd up` provisions the Terraform App Service stack and deploys its services.
- Agent `azd up --cwd app/agent` provisions the `model-router` and `gpt-5.4`
  deployments declared in the [agent manifest](../app/agent/azure.yaml) for the
  configured Foundry project, then deploys the hosted workflow. Model access and
  regional capacity remain deployment prerequisites. This command includes dependency
  provisioning; do not use it for an agent-only code rollout.

### Hosted OAuth prerequisites and deployment gate

The [agent manifest](../app/agent/azure.yaml) selects `foundry/oauth`, but the runtime
adapter still rejects that combination. A deployment command or a resolved Toolbox
endpoint does not make the hosted banking path usable. Keep experiments separately
authorized until callback/S256/client-auth compatibility, Consumer prerequisites,
actual platform call IDs and banking-user/native-caller/individual-grant binding
are proven. Two-user hosted isolation and consent remain separate acceptance checks.

OAuth connections, Identity client registrations and Toolbox publication are separate
operations, not implicit consequences of agent deployment. The owner reported
successful protected registrations for both native clients; this is not independent
runtime verification. See the [configuration record](../infra/README.md#native-oauth-compatibility-gate)
and [issue #81](https://github.com/cristofima/AI-Banking-Assistant-AgentFramework/issues/81).

For hosted CD, configure `TOOLBOX_BANKING_TOOLS_MCP_ENDPOINT` in the Development
GitHub Environment. CD requires a nonblank value and exports it to its agent azd
environment; it neither enforces URL format nor probes availability. Prefer the
consumer endpoint that follows the published default; use a version-specific endpoint
for pinned experiments. Local azd metadata does not configure GitHub runners. CD
only deploys `home-banking-agent`, not connections, clients or the Toolbox. See the
[workflow variable contract](../.github/workflows/README.md#hosted-agent-variables-cd-hosted-agentyaml).

### Post-deployment checks

After deployment, use the printed web URL for an authorized smoke test. A printed
URL or successful deploy command does not prove Identity availability, database
migrations, hosted identity transport or customer/operator end-to-end behavior.

## Python package staging and startup

Development installs application packages from each project's `src` directory.
The root [azd manifest](../azure.yaml) stages those packages and
`banking_shared` into the App Service ZIP roots before packaging, then removes the
staging copies. Existing staging destinations are rejected rather than overwritten.
Do not deploy only the old flat module names or add import-path workarounds.

| Service       | ASGI entrypoint                             |
| ------------- | ------------------------------------------- |
| Identity      | `identity.main:create_app` with `--factory` |
| Account       | `banking_account.main:app`                  |
| Transaction   | `banking_transaction.main:app`              |
| Responses BFF | `bff.main:app`                              |

Responsibility subpackages remain intact inside each staged package. Runtime
requirements omit the local `banking-shared` distribution path because its source
is staged separately; its dependencies are still required. See the
[dependency artifact contract](../app/business-api/README.md#python-dependency-artifacts),
[Identity deployment contract](../app/business-api/identity/README.md#cicd) and
[BFF guide](../app/responses-bff/README.md). The
[agent project](../app/agent/README.md) packages separately from its own azd root.
Package/import validation does not prove hosted startup, chatbot behavior or
PostgreSQL migration acceptance.

### **Important: Note for PowerShell Users**

If you encounter issues running PowerShell scripts due to the policy of not being digitally signed, you can temporarily adjust the `ExecutionPolicy` by running the following command in an elevated PowerShell session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

This will allow the scripts to run for the current session without permanently changing your system's policy.

## 🛠️ Troubleshooting & Common Issues

**Before starting deployment**, be aware of these common issues and solutions:

| **Common Issue**                      | **Quick Solution**                            | **Full Guide Link**                                                                 |
| ------------------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------- |
| **ReadOnlyDisabledSubscription**      | Check subscription status and access          | [Troubleshooting Guide](./troubleshooting.md#subscription--access-issues)           |
| **InsufficientQuota**                 | Check capacity for the affected resource      | [Troubleshooting Guide](./troubleshooting.md#quota--capacity-limitations)           |
| **ResourceGroupNotFound**             | Check environment, subscription and ownership | [Troubleshooting Guide](./troubleshooting.md#resource-group--deployment-management) |
| **InvalidParameter (Workspace Name)** | Check the affected resource's naming rules    | [Troubleshooting Guide](./troubleshooting.md#resource-naming--validation)           |
| **ResourceNameInvalid**               | Follow Azure naming conventions               | [Troubleshooting Guide](./troubleshooting.md#resource-naming--validation)           |

> **If you encounter deployment errors:** Refer to the [complete troubleshooting guide](./troubleshooting.md) with comprehensive error solutions.

## Redeploying Infra or App Code Changes

If you've only changed the App Service backend services (business APIs or Responses BFF) or frontend code in the `app` folder, without changing infrastructure requirements, you don't need to re-provision the Azure resources. Deploy the root App Service stack with:

```powershell
rtk proxy azd deploy
```

For hosted-agent code changes, use:

```powershell
rtk proxy azd deploy home-banking-agent --cwd app\agent
```

Select the intended agent azd environment and resolve its project/Toolbox dependency
metadata first. Preserve the existing BFF-to-agent signer mapping. This agent-only
command does not register OAuth clients or publish a Toolbox version, and the native
runtime gate above still applies.

If you changed the root infrastructure files (`infra` or the root `azure.yaml`), review
the Terraform plan and then reprovision the App Service stack:

```powershell
rtk proxy azd up
```

Do not use `azd down` as a rollback against an existing shared resource group. Inspect
the Terraform state and plan first.

## Model Configuration

The [agent manifest](../app/agent/azure.yaml) declares `model-router` and `gpt-5.4`
deployments; the root Terraform stack does not provision those model deployments.
Set the shared `MODEL_DEPLOYMENT_NAME` and optional per-agent overrides in the agent
azd environment to select runtime deployments. Changing an environment setting
selects a deployment; it does not provision or resize one. See the
[per-agent model configuration](../app/agent/README.md#optional-per-agent-model-deployments)
for fallback behavior. Manifest declarations alone do not verify model access,
capacity or hosted end-to-end compatibility.

## Running Agents locally

The supported local topology runs independently of App Service deployment. Use the root
VS Code launch `DEV - Full Stack Ordered`; for component details, see:

- the [agent README](../app/agent/README.md) for the Responses workflow
- the [frontend README](../app/frontend/banking-web/README.md) for the browser application
- the [business API README](../app/business-api/README.md) for PostgreSQL-backed
  REST/MCP services using persisted synthetic banking data
- the [dependency-artifact guide](../app/business-api/README.md#python-dependency-artifacts)
  for requirements regeneration and zip packaging.
