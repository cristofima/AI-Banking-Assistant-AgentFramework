# Zip Deploy Strategy for Python Agent Stack

- Overview
- Hosted agent placement
- MCP API deployment
- Frontend deployment
- When to revisit containers
- References

## Overview

Zip deploy on App Service removes the Azure Container Registry dependency for the Python MCP APIs, Responses BFF, and web frontend. The root azd project uses the Terraform stack under [`infra`](../infra/README.md); the hosted agent has a separate azd project.

## Hosted agent placement

The Account/Transaction orchestrator runs through a Foundry Responses host. Its azd manifest lives at [app/agent/azure.yaml](../app/agent/azure.yaml). For an agent-only rollout from repository root, use `rtk proxy azd deploy home-banking-agent --cwd app\agent`; full `azd up` also handles dependencies and requires separate provisioning authorization. Local browser validation uses the local agent on port `8088` through the Responses BFF.

Hosted source packaging is not App Service ZIP deployment. The manifest uses Python
3.13 and `dependencyResolution: remote_build`; the frozen requirements export retains
`.` to install `src/app` noneditably. Keep `main.py`, `src/`, `pyproject.toml` and the
required package resources in the source ZIP. Follow the
[agent packaging contract](../app/agent/README.md#hosted-source-dependency-installation),
not the App Service staging procedure below.

Hosted CD exports a nonblank `TOOLBOX_BANKING_TOOLS_MCP_ENDPOINT` from the Development
GitHub Environment without format or availability checks. The consumer endpoint
follows the published default; a version-specific endpoint pins experiments. CD
neither publishes the Toolbox nor registers clients. The manifest's `foundry/oauth`
runtime remains fail-closed pending native identity/grant binding proof; packaging
and metadata do not establish hosted OAuth support. See the
[deployment gate](deployment-guide.md#hosted-oauth-prerequisites-and-deployment-gate).

## MCP API deployment

The Account and Transaction MCP servers are FastAPI projects that rely only on Python packages. App Service build automation (`SCM_DO_BUILD_DURING_DEPLOYMENT=true`) restores dependencies during zip deploy. Select an App Service plan SKU (for example B1, B2, or P1v3) that aligns with expected concurrent tool calls.

For zip deploy, each Python API directory must include a `requirements.txt` file. In this repository, `pyproject.toml` remains the local development source of truth, and `requirements.txt` is the deployment artifact consumed by Oryx on App Service.

Generate or refresh the deployment artifact from the repository root:

```powershell
uv pip compile app\business-api\account\pyproject.toml --no-emit-package banking-shared --python-version 3.11 --python-platform x86_64-unknown-linux-gnu -o app\business-api\account\requirements.txt
uv pip compile app\business-api\transaction\pyproject.toml --no-emit-package banking-shared --python-version 3.11 --python-platform x86_64-unknown-linux-gnu -o app\business-api\transaction\requirements.txt
```

These artifacts target the deployed Linux/Python version rather than the developer's
Windows interpreter. Identity and BFF also require deployment artifacts; use their
commands in the [dependency-artifact guide](../app/business-api/README.md#python-dependency-artifacts).
Do not apply the agent's `.` installation rule to these staged App Service packages.

### Importable source packages

The Python distributions use `src/` layouts. The root [azd manifest](../azure.yaml)
stages each service's package (`identity`, `banking_account`, `banking_transaction`,
or `bff`) and `shared/src/banking_shared` at the ZIP root before packaging, then
removes those temporary copies afterward. Hooks reject existing staging paths rather
than overwrite source. Do not deploy a ZIP containing only `src/` with the existing
startup commands: Oryx installs third-party requirements, not these local packages.

Account and Transaction startup targets are `banking_account.main:app` and
`banking_transaction.main:app`; Identity and BFF retain their qualified targets.
Local packaging checks do not certify Oryx or hosted identity transport. Staging or
startup changes need separate deployed validation before claiming hosted parity.

## Frontend deployment

The banking web deployment workflow builds the Vite production assets, packages its App Service server, and injects the deployed Account, Transaction, and Responses BFF URLs. The browser calls the BFF `/responses` endpoint and never receives a Foundry credential.

## When to revisit containers

Keep the current App Service ZIP strategy while its build and runtime requirements
are satisfied. Multiple instances alone do not require containers. Reassess the hosting
decision, rather than automatically switching to Container Apps/ACR, if these needs arise:

- Native libraries that require system packages not present in the App Service Python image
- Autoscaling past the limits of the selected plan or needing custom horizontal rules
- Long running background jobs or sidecars that must share a Container Apps environment

## References

- [Quickstart: Deploy a Python web app to Azure App Service](https://learn.microsoft.com/azure/app-service/quickstart-python#deploy-your-application-code-to-azure)
- [Configure a Linux Python app for Azure App Service](https://learn.microsoft.com/azure/app-service/configure-language-python)
- [Run your app from a ZIP package in Azure App Service](https://learn.microsoft.com/azure/app-service/deploy-run-package)
- [Deploy a Python Flask app with azd](https://learn.microsoft.com/azure/app-service/tutorial-python-postgresql-app-flask#modify-sample-code-and-redeploy)
