# Banking Assistant Responses Agent

This project hosts the Account and Transaction handoff workflow through the Foundry Responses protocol. It is a separate deployable from the browser-facing BFF under [`app/responses-bff`](../responses-bff).

Transaction disputes are the workflow's persisted support-case use case, not a
third specialist. The agent identifies the transaction, gathers the customer's
reason, requests explicit approval, and explains tool-reported outcomes. The
Transaction service owns policy, triage, ownership checks, and case events; the
agent never determines dispute legitimacy or invents a missing fraud score.

Existing-case consultations are read-only. Triage routes case lists, status,
timelines, and follow-ups to Transaction. Ambiguous references require customer
selection; status follow-ups refresh case details and event questions fetch the
timeline. Missing and foreign cases are described equivalently as unavailable.
Customer consent and `IN_REVIEW` do not establish operator takeover or a verdict.
Manual-created cases use the same persisted follow-up tools; creating one does not
invoke a background model.

Discovery accepts merchant, approximate date, amount and masked-card clues, with
explicit selection among at most five readable tool-backed candidates. Full verified
product numbers remain necessary where the existing lookup contract requires them;
masked digits and database IDs are not lookup keys. Latest-movement lookup covers
only five movements, and MCP tools have no date/pagination arguments. No match is
reported within that limited searched scope, not as proof of exhaustive history.
Incorrect-amount complaints preserve the expected amount in the reason under the
existing full-original-amount policy, never a promised partial refund.

## Direct MCP OAuth delegation

The optional `MCP_IDENTITY_MODE=oauth` path delegates the customer's OAuth access
token to direct Account and Transaction MCP connections using only the
`Authorization` bearer header. It does not send a custom banking identity header
to either MCP. The services validate the Identity-issued RS256 JWT against trusted
public keys, issuer, audience, expiry and `mcp.access`; existing service-level
customer ownership checks remain mandatory. This is not an Entra login migration.

Configure `MCP_IDENTITY_MODE=oauth` consistently in the BFF, agent and MCP services.
For the agent, select `AGENT_RUNTIME=local` and configure `ACCOUNT_MCP_URL` and
`TRANSACTION_MCP_URL`: `local` selects direct HTTP transport, even for a hosted
agent, and does not mean offline model execution. OAuth mode rejects the experimental
Toolbox route because per-user bearer passthrough has not been demonstrated.
Missing, expired or malformed delegated grants fail closed without HMAC fallback.
Default `MCP_IDENTITY_MODE=hmac` preserves existing behavior.

The hosted manifest fixes `AGENT_RUNTIME=foundry` and `MCP_IDENTITY_MODE=oauth`
for the approved remote Toolbox target; neither requires an azd environment variable.
Local execution remains `local`/`oauth`, connecting directly to the two MCP services.
The adapter now supports native OAuth Toolbox consumption offline: it sends no banking
grant or HMAC, requires the platform request call ID and never fabricates one. A
consent challenge raised while connecting the Toolbox is deferred by
`ToolboxConsentMiddleware` and surfaced at run time as an `oauth_consent_request`
item, so the host ends the turn as `incomplete` with the link. In this mode the BFF
sends no local grant and `/oauth/status` reports `required: false`. Native
banking-user/caller/grant binding and two-user isolation remain unproven in hosted
runs, so the hosted target is not runtime readiness. Keep the BFF and both MCP services in the same identity mode.
No OAuth client secret, signing private key or JWKS is passed to the agent; those
belong to Identity, confidential clients or MCP validators.

The BFF keeps OAuth tokens server-side and includes the current grant in its signed
per-request identity envelope. That BFF-to-agent envelope remains necessary because
Foundry authentication occupies the upstream Authorization header. Tokens must not
enter model instructions, browser storage, responses or logs. Browser login and
direct financial REST authentication retain current Identity checks.

Access tokens last 3600 seconds (one hour). Authorization codes and BFF state
retain their 300-second expiry. MCP JWT validation does not perform per-call Identity
introspection, so revocation and profile changes can take up to that lifetime to
invalidate a token. `mcp.access` permits existing customer MCP reads and dispute
operations. It does not elevate roles, bypass resource ownership or replace explicit
case-creation consent. Old `banking.read` grants are rejected and require fresh consent.
See the [BFF guide](../responses-bff/README.md) for authorization-code consent and
grant custody, the [Identity guide](../business-api/identity/README.md) for signing
configuration, and [ADR 0012](../../docs/adr/0012-local-oauth-jwt-validation-for-mcp.md)
for the proposed revocation tradeoff. Offline tests verify protocol behavior, not
configured grants, hosted identity transport or end-to-end runtime acceptance.

## Remote OAuth Toolbox target and no-auth/HMAC limits

[azure.yaml](azure.yaml) and [toolbox.yaml](toolbox.yaml) reference `account-mcp`
and `transaction-mcp` project connections with the existing customer-tool allowlists.
Both connections must be configured as OAuth2 with `mcp.access`; references alone do
not change existing no-auth connections. Verify exact callbacks, PKCE
compatibility and separate banking-user grants before activation. Azure authentication
on the agent-to-Toolbox hop is separate from downstream customer OAuth.

The following describes the no-auth/HMAC route, not the
approved OAuth configuration or proof of OAuth acceptance.
`AGENT_RUNTIME=foundry` uses the project's experimental `banking-tools` Toolbox
endpoint, with separate Account and Transaction allowlists and no direct-MCP
fallback. `AGENT_RUNTIME=local`
requires `ACCOUNT_MCP_URL` and `TRANSACTION_MCP_URL` and connects
directly to those MCP services using the existing fresh customer-scoped HMAC bearer.
Local execution still uses Foundry models and its local Azure credential selection;
it is not an offline mode. Restart the local agent to pick up routing changes.
This routing does not prove runtime readiness or authorize automatic model calls.
The hosted manifest explicitly selects `foundry` and depends on `banking-tools`.
No additional Settings field is required. Direct synthetic MCP session injection
in isolated tests remains unchanged.
OAuth2 is excluded from the no-auth/HMAC route, not from the current remote
target. Static customer tokens and signer secrets in connections remain prohibited.

This routing is experimental, not a working authenticated Toolbox integration.
Azure authentication owns `Authorization` on the Toolbox hop. The adapter supplies
the banking HMAC separately as `x-banking-mcp-authorization`, but forwarding is not
verified and the existing banking MCP services accept it only in `Authorization`.
Consequently authenticated calls fail closed. Do not deploy this routing as a
functional replacement until downstream identity delivery is proven.

Toolbox does not follow the `/mcp` to `/mcp/` redirect that the services return, so
discovery over the non-canonical `/mcp` URLs fails with `HTTP_307`. Configure the
canonical trailing-slash URLs. With them, Toolbox discovery returns all 17
allowlisted tools. Read-only Account calls through Toolbox, both without banking
identity and with an invalid `x-banking-mcp-authorization`, return the
missing-identity rejection. This shows discovery and fail-closed behavior, not
arbitrary-header forwarding or valid HMAC acceptance. Transaction calls through
Toolbox return tool errors whose authentication cause is not established.

Deploying the experimental route does not establish successful authenticated tool
calls or end-to-end readiness. Canonical `/mcp/` endpoints answer MCP initialization
with HTTP 200, which is not a protected tool call and does not prove HMAC propagation
or financial access.

Microsoft's [hosted Toolbox guide](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/use-toolbox-hosted-agent)
separates Azure authentication to Toolbox from the downstream credential selected
by each tool's connection. `FoundryToolbox` forwards the hosted per-request call ID;
the guide does not promise forwarding arbitrary customer HMAC headers. With
connection authentication set to `none`, the current configuration therefore has
no documented authenticated path to the banking MCP services. Fixing discovery
alone does not remove that incompatibility. Do not replace the fresh customer HMAC
with a static connection token, expose the signer, or weaken MCP authorization.
A diagnostic resource or an alternative authentication/transport strategy requires
explicit authorization before further deployment.

### Native OAuth consent behavior

`ToolboxConsentMiddleware` surfaces the connect-time `CONSENT_REQUIRED` challenge as
`oauth_consent_request` output; the failed `tools/list` it logs before the user
consents is expected. After consent, Account and Transaction reads run under the
consenting user's grant, including the handoff to `TransactionHistoryAgent`.
Banking-user/caller/grant binding and two-user isolation are not established.

Stream behavior:

- The hosted workflow also projects the consent challenge as a `request_info`
  function call that never receives an output. The frontend ignores it so the chat
  shows one consent card per authorization link and no "tool result unavailable" task.
- Streamed function-call `arguments` can arrive duplicated (`{}{}`).
  `agent_framework_openai` emits `function_call_arguments.delta` with
  `merge_mode="append"` and `.done` with `merge_mode="replace"`, while
  `agent_framework_foundry_hosting` appends every update and ignores `merge_mode`.
  Tool execution is unaffected; the defect is upstream in the installed release.
- A missing `Agent365.Observability.OtelWrite` role makes the agent telemetry
  exporter fail with HTTP 403. That concerns the A365 exporter, not Application
  Insights ingestion or MCP consent.

To diagnose a hosted run from Application Insights and a captured Responses stream,
use the [foundry-telemetry-debug skill](../../.github/skills/foundry-telemetry-debug/SKILL.md).
A consent pass is proven by matching tool outputs in the stream, not by HTTP 200.

Before enabling the main banking agent through BFF, verify:

1. Application JWT validation and current-Identity checks, signed BFF-to-agent
   banking context, actual native caller and individual OAuth grant all correspond
   to the intended banking customer. Playground consent does not establish this
   mapping for the BFF's authenticated Azure caller.
2. Both Account and Transaction support two independently consented users, owned
   reads, controlled foreign-resource denials and concurrent-session isolation.
   Tool arguments, including user names, must never select authentication authority.
3. Native consent requests reach the browser safely through BFF, remain bound to
   the correct session/customer, and handle Allow/Deny, expiry, logout and recovery
   without shared grants, token exposure or automatic replay of uncertain turns.
4. Authorized telemetry inspection discovers the agent's actual Application
   Insights/workspace linkage and correlates request/dependency operations with
   Identity and Account/Transaction evidence. Start from the run's timestamps
   and broaden only if ingestion or clock evidence requires it. Absent traces do
   not prove absent execution.
   Do not publish consent URLs, tokens, customer identifiers or financial payloads.
5. Only after binding proof, review the production adapter, BFF/native-consent
   transport and deployment metadata changes, preserve fail-closed behavior, and
   run focused regressions plus separately authorized end-to-end checks. Changing
   the BFF endpoint alone does not integrate native OAuth.

The production gate remains intact. These observations do not close issue #81;
new invocations, telemetry configuration changes and deployments require separate
execution authorization. No credentials or private log links belong in this guide.

### Offline caller-context diagnostic

Run the installed-SDK header diagnostic and the native OAuth gate regression without
Azure credentials, MCP connections or model calls:

```powershell
rtk proxy uv run --directory app\agent --frozen --no-sync python -m pytest tests\test_toolbox_caller_context.py tests\test_runtime_tools.py -q
```

The diagnostic exercises the real `FoundryToolbox` operation-boundary header hook
with synthetic request contexts. It checks consecutive and concurrent callers,
missing call IDs, context restoration and exclusion of platform user/session IDs
from outbound headers. A missing call ID is not replaced with a banking subject or
session ID. The gate regression verifies that native OAuth is rejected before
constructing tools or resolving banking grants.

These checks establish only installed-SDK header composition, not HTTP delivery,
MCP session reuse, native consent or downstream grant binding. Foundry owns the
OAuth credentials selected by each `project_connection_id`; the BFF's existing grant
is not evidence of a native grant. Keep the native OAuth gate until actual callback,
S256/client authentication, Consumer/tenant compatibility and two-user binding are
proven. See [Toolbox authentication](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/tool-authentication)
and [MCP OAuth](https://learn.microsoft.com/azure/foundry/agents/how-to/mcp-authentication).

Run the following from the repository root only after authorizing cloud operations.
Use the existing agent azd environment, with `FOUNDRY_PROJECT_ENDPOINT`,
`AZURE_SUBSCRIPTION_ID`, `ACCOUNT_MCP_URL` and `TRANSACTION_MCP_URL` already configured.
The MCP URLs must be cloud-reachable HTTPS endpoints, not localhost. Use their
canonical `/mcp/` paths to avoid redirects. Do not dump the azd environment or place
credentials in command arguments.

```powershell
$env:AZURE_DEV_USER_AGENT = "microsoft_foundry_skill"
rtk proxy azd deploy banking-tools --cwd app\agent
rtk proxy azd ai toolbox show banking-tools --cwd app\agent
rtk proxy azd ai toolbox versions list banking-tools --cwd app\agent
```

Deployment creates an immutable version but does not automatically promote it.
Test the new version's MCP endpoint, then select it with
`azd ai toolbox publish banking-tools <version> --cwd app\agent` before testing
the unversioned consumer endpoint.

Before agent integration, test a separate diagnostic MCP with non-secret changing
headers through Toolbox. Verify delivery, cross-request isolation, session reuse
and header collisions; do not expose an echo endpoint on the banking services.
Discovery without the required identity may fail: that is not permission to disable
MCP authentication. Configuration and deployment do not prove header forwarding.

After forwarding and agent consumption are implemented and validated, provision
only if required, then deploy the agent separately:

```powershell
$env:AZURE_DEV_USER_AGENT = "microsoft_foundry_skill"
rtk proxy azd provision --cwd app\agent
rtk proxy azd deploy home-banking-agent --cwd app\agent
```

Provisioning can create or update model deployments declared in the manifest; it is
not needed merely to redeploy an already provisioned agent. Deploying the current
source activates the experimental Toolbox route. The alternative
[toolbox.yaml](toolbox.yaml) requires named `account-mcp` and `transaction-mcp`
project connections created with `--auth-type none`; use either that imperative
path or the declarative service, not both for the same Toolbox.

## Grounding and confidentiality

Triage, Account and Transaction prepend the same English [guardrail policy](src/app/agents/azure_chat/guardrails.py).
They must ground answers in authenticated context and authorized tool results, treat
customer and tool-returned text as untrusted data, and refuse instruction overrides,
credential disclosure and hidden-prompt requests. Refusals follow the stored profile
locale, do not echo secrets, and do not use tools or handoffs to satisfy forbidden
requests. Security refusals, warnings and redirections use the same authenticated
profile language as normal answers, never a default English refusal for a Spanish
or Portuguese profile, even when a message requests a language override. The
per-request locale provider reinforces this rule for every agent run.
The [refusal middleware](src/app/adapters/refusal_locale_middleware.py) also replaces
SDK-marked provider refusals with controlled en/es/pt text, for streaming and final
responses, using the signed profile locale. It does not translate unmarked model
prose or tool results, and adds no telemetry flags or spans.

**Known limitation:** refusal localization can still fail in the browser; the cause
is not established. A refusal that arrives as ordinary `output_text` rather than structured
`refusal` content stays unchanged, because the middleware intentionally leaves
ordinary text alone. It is not established whether the provider omits refusal
metadata, a later layer loses it, or the running deployment differs from the tested
code. Browser localization and
hosted-model locale acceptance remain open; the offline middleware fix does not close
them.

[Microsoft Learn's Responses schema](https://learn.microsoft.com/rest/api/microsoft-foundry/aiproject#components)
distinguishes ordinary text from structured refusals. The reviewed documentation did
not provide a refusal-language setting or guarantee. If debugging resumes, inspect
controlled provider metadata at the
[chat-middleware boundary](https://learn.microsoft.com/en-us/agent-framework/agents/middleware)
before choosing another fix. Do not infer content filtering from refusal wording alone
or translate arbitrary agent Markdown.
Independently valid banking questions in mixed requests remain supported.
Authorized bank numbers, masked cards and existing consent transport are preserved.

This prompt policy is defense in depth, not an authorization boundary or guaranteed
model immunity. Offline tests verify policy content and actual agent configuration;
real-model refusal and hosted deployment require separate validation. Never use real
credentials in adversarial test messages.

## Pre-intake consent and localized context

Selected-charge confirmation is separate from explicit case consent. The Transaction
agent first calls `previewTransactionDispute` with the selected transaction and reason.
The preview is read-only and contains no persisted case or case ID. Explicit consent
to create the case and request review is required before `reportTransactionDispute`
accepts the signed preview token. Decline creates no case or event; unclear or unrelated
affirmation requires clarification. Acceptance records intake, consent and routing
atomically in `IN_REVIEW`, without a second review-consent step.

The shared frontend proposal displays tool-backed amount/currency, date, masked card,
merchant and optional owned country/city. Proposal tokens stay in transport, never
assistant prose. Acceptance lasts ten minutes; read-only `recoverTransactionDispute`
recovery lasts 24 hours from issuance. Ambiguous failures require recovery, not blind
resubmission, and a null recovery result remains uncertain. Successful intake responses
produce factual receipts only after confirmed persistence/readback. Sequential duplicate
intake recovers an owned active case through list/detail readback. Eligibility and
ownership remain service decisions. Existing `WAITING_USER_APPROVAL` cases retain their
legacy consent path. See the [Transaction contract](../business-api/transaction/README.md#customer-dispute-proposal-and-consent).

The authenticated [locale provider](src/app/context/user_profile_provider.py) injects the
stored en/es/pt response language once per request. Generated prose and human-readable
status labels use that language; canonical structured keys, status codes and tool names
remain unchanged. Spanish generated labels consistently use `reclamo`/`reclamos` with
masculine grammar, not `disputa` or `reclamación`. Quoted customer reasons and original
audit text remain verbatim. Agent instructions and tool descriptions remain authored
in English; frontend catalogs do not translate agent Markdown.

After REST-recorded acceptance, chat calls `getSupportCase` for the supplied case
and acknowledges confirmed readback, without recreation or second consent.

Focused localization checks, from the repository root:

```powershell
$env:OTEL_SDK_DISABLED = 'true'
rtk proxy uv run --directory app\agent python -m pytest tests\test_internal_identity.py tests\test_hosted_workflow.py tests\test_settings.py -q
```

Scripted SDK continuations cover accept,
decline, unclear consent and REST-accepted readback; they prove session/tool plumbing,
not real-model judgment. Full browser locale,
real-model consent, PostgreSQL contention and hosted acceptance remain separate gates.

Assigned-operator verdicts and recorded financial effects are implemented in the
Transaction service, not executed by this agent. An invalid verdict has no compensation;
a valid pending verdict is not a completed effect. Completion claims require returned
financial-effect status and movement evidence. Card protection requires independent
returned evidence and is application-local, not external processor enforcement.
Stored synthetic fraud scores are routing signals, not an investigation or proof.
Deterministic instruction-contract tests verify these requirements are present,
not that a real model follows them; persisted and browser validation remain separate.

## Package and responsibility seams

The installed `app` package lives under `src/app`. Authenticated context providers
live in `context`; protected SDK compatibility lives in `adapters`. Composition
roots retain deployment-keyed client reuse and independent model overrides. Sync
and async credential consumers remain supported. Logging configuration is packaged
and invalid configuration falls back once to a bounded console configuration.
Handoff buffering, output suppression and Responses streaming are unchanged.

## Runtime Flow

```mermaid
flowchart LR
    Browser --> BFF[Responses BFF]
    BFF --> Agent[Responses agent]
    Agent --> Account[Account MCP]
    Agent --> Transaction[Transaction MCP]
```

The browser never sends Azure credentials to Foundry. The BFF validates the application JWT and signs the verified `sub` and `customer_id` for this agent. The agent verifies that envelope and creates a fresh 60-second bearer for Account and Transaction MCP calls. In hosted mode, the BFF obtains its Azure token server-side before forwarding the request.

## Local Setup

Requirements:

- Python 3.11 or newer
- `uv`
- Azure OpenAI access configured in `.env` (copy `.env.example`)
- Account MCP on port `8070`
- Transaction MCP on port `8071`

Install dependencies and run the local Responses host:

```powershell
cd app/agent
uv sync --dev --frozen
$env:PROFILE="dev"
uv run python main.py
```

The agent listens on port `8088`. Browser traffic should go through the BFF on port
`8080`, not directly to this process. F5 runs the root `DEV - Full Stack Ordered`
launch with `noDebug: true`, ordered service tasks and no automatic browser launch.
That task loads the agent's `.env`, sets `AGENT_RUNTIME=local` and uses the root
output runner for colored severity labels and daily `logs/agent-YYYY-MM-DD.log` files.
Both `ENABLE_SENSITIVE_DATA` and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`
are explicitly `true` in the local task. Keep logs private: common credential redaction
does not remove conversation content, and no automatic retention cleanup is configured.
The manual command above does not apply those task overrides or daily output capture.
See the [root startup guide](../../README.md#run-locally).

The hosted code package contains only this project. Its manifest and lockfile must
not depend on repository-root evaluation packages or other external local paths.
The evaluation project owns the optional dependency on the agent, not the reverse.
[.agentignore](.agentignore) controls the hosted source ZIP exclusions using gitignore
syntax. It excludes local environments, secrets, caches and development-only tests
and evaluation assets, while retaining `main.py`, `src/`, `pyproject.toml`, `uv.lock`, `README.md`
and runtime logging YAML. This is the hosted-agent packaging ignore file, not
`.azdignore`. Exclusions do not install the `app` package or establish hosted readiness.
Run the complete agent suite, including evaluation integration tests, from the
repository root using the combined evaluation environment:

```powershell
rtk proxy uv sync --project evals --extra offline --extra model --frozen
$env:OTEL_SDK_DISABLED = "true"
rtk proxy uv run --project evals --frozen --no-sync python -m pytest app\agent\tests -q
```

Hosted-agent CI also restores an isolated copy of this project's dependencies on
Python 3.13, without the repository-root evaluation directory, before running tests.
This check verifies packaging isolation, not hosted activation or model behavior.

## Configuration

This directory is a separate `azd` project root because it has its own `azure.yaml` at `app/agent/azure.yaml`. The repository root `azure.yaml` (App Service stack) and this agent `azure.yaml` (hosted agent stack) do not share `azd` environment state automatically.

When you run commands with `--cwd app/agent` (or directly from this folder), `azd` may prompt for:

- An agent environment name
- Azure subscription
- Azure region/location

Using the same environment name as root is fine for consistency, but state is still independent per project root.

Configure the Foundry project endpoint and the name of a model deployment in that project:

```env
FOUNDRY_PROJECT_ENDPOINT=https://your-resource.services.ai.azure.com/api/projects/your-project
AZURE_AI_PROJECT_ID=/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<account-name>/projects/<project-name>
MODEL_DEPLOYMENT_NAME=gpt-5.4
ACCOUNT_MCP_URL=http://localhost:8070/mcp/
TRANSACTION_MCP_URL=http://localhost:8071/mcp/
INTERNAL_IDENTITY_SECRET=<shared-secret-at-least-32-characters>
```

For hosted provisioning with `azd`, make sure the agent environment includes at least:

- `FOUNDRY_PROJECT_ENDPOINT`
- `AZURE_AI_PROJECT_ID`
- `MODEL_DEPLOYMENT_NAME`
- `ACCOUNT_MCP_URL`
- `TRANSACTION_MCP_URL`
- `INTERNAL_IDENTITY_SECRET`: the existing BFF transport signer, at least 32 characters.

Local tasks load the agent's own `.env` and use Azure CLI credentials when
`AGENT_RUNTIME=local`; hosted `foundry` runtime uses managed identity, with optional
`AZURE_CLIENT_ID` for a user-assigned identity. The credential-free
[template](.env.example) selects local direct MCP and `MCP_IDENTITY_MODE=oauth`;
keep that identity mode consistent with the BFF and both MCP services. Supply
`MCP_IDENTITY_MODE` in the process environment through the root tasks or
`uv --env-file`, because the MCP identity helper reads it from the environment,
not from the `Settings` object. The BFF transport signer is required in either mode.
OAuth client secrets and grants do not belong in this template.

Keep MCP URLs, Foundry settings and the shared transport identity secret in the
agent's local configuration. The template leaves the optional Application Insights
connection string unset. `ENABLE_OTEL` currently has no consumer in the Responses
entrypoint, so it is not a supported telemetry on/off switch.

Hosted deployment is owned by the [agent manifest](azure.yaml) and uses managed identity. Its `ai-project` service declares `model-router` and `gpt-5.4` deployments for provisioning; the hosted agent references that project. The manifest explicitly maps `FOUNDRY_PROJECT_ENDPOINT` and `INTERNAL_IDENTITY_SECRET` from the selected agent azd environment into the container. The signer must match the existing BFF signer; the agent environment does not inherit the BFF's local dotenv settings. An empty signer prevents startup through `Settings` validation and can surface as HTTP 424 / `session_not_ready`. Synchronize the existing value only with explicit authorization; never print it, rotate it as a workaround, or weaken validation. `AUTH_INTERNAL_SECRET` is a separate Identity introspection secret. Shared and per-agent environment settings select runtime deployments; manifest declarations and local tests do not verify available capacity or hosted end-to-end behavior.

### Hosted source dependency installation

The source deployment explicitly uses `dependencyResolution: remote_build`. Keep
[requirements.txt](requirements.txt) synchronized with [uv.lock](uv.lock):

```powershell
rtk proxy uv export --project app\agent --frozen --no-dev --no-editable --no-hashes --no-header --output-file app\agent\requirements.txt
```

Run this command from the repository root. The deliberate `.` entry installs the
local distribution noneditably, including `src/app` and its logging configuration.
Do not add `--no-emit-project`: dependencies alone do not make this src-layout
package importable. The source ZIP must retain `main.py`, `src/`, `pyproject.toml`,
`README.md` and `requirements.txt`; moving the entrypoint alone does not install
`app`. A reference project with a package at its root can import it without this
installation, so that layout is not evidence of src-layout support.

Hosted-agent CI checks the frozen export and installs the requirements into an
isolated Python 3.13 environment. CD rejects a stale requirements artifact before
deployment. These checks do not establish that the remote builder successfully
installs the application: verify hosted build logs and `/readiness` returning HTTP
200 after deployment before claiming the HTTP 424 startup failure is resolved.

### Optional per-agent model deployments

Each agent can use a distinct deployment in the same Foundry project. Unset or
empty overrides use the configured `MODEL_DEPLOYMENT_NAME` shared fallback.
The shared setting is required only for agents without a nonempty override; it
can be unset when all three overrides are configured. There is no automatic
runtime deployment default:

```env
TRIAGE_MODEL_DEPLOYMENT_NAME=model-router
ACCOUNT_MODEL_DEPLOYMENT_NAME=gpt-5.4
TRANSACTION_MODEL_DEPLOYMENT_NAME=gpt-5.4
```

These values are deployment names, not automatic model provisioning. The Responses
host creates one client per distinct effective deployment, reusing it when names
match. Direct workflow callers can also pass `account_chat_client` and
`transaction_chat_client`; omitted clients reuse the triage client. Identity,
locale, tools, and the Account/Transaction handoff topology remain unchanged.

Microsoft Learn documents [model-router agentic tool support](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router)
and [Foundry Responses inference through a router deployment](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router#test-model-router-with-foundry-responses-and-chat-completions).
The [GPT-5.4 catalog](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure#gpt-54)
lists Responses, Chat Completions, and function/tool calling support. Router model
eligibility and deployment availability depend on region, access, and deployment
configuration. Configure an eligible tool-capable routing pool; no project
availability or live end-to-end compatibility is established by offline tests.
The [hosted manifest](azure.yaml) forwards the shared setting and all three optional
overrides; configure them in the agent azd environment to select the deployed host's
models.

## Validation

```powershell
uv run python -m pytest tests/test_hosted_workflow.py tests/test_internal_identity.py tests/test_settings.py tests/test_model_configuration.py tests/test_mcp_replay.py -q
```

The focused suite covers handoff completion, safe ownership
denials, signed identity, settings, and concurrent workflow-request isolation. SDK
deprecation warnings and a telemetry-exporter connection failure are separate from
the test assertions.

Through the BFF, an owned account returns details and masked cards; a foreign account
reaches `getAccountDetails(product_number=...)`, returns `ACCESS_DENIED`, and produces a
visible assistant denial before `response.completed`, with no foreign financial data.
Missing/empty account cases, multi-turn checkpoint restoration, approval
continuation, and hosted identity transport are not verified end to end.

### Dispute Evaluation Coverage

The [evaluation guide](../../evals/README.md) separates evidence levels:

- [Service regression tests](../business-api/transaction/tests/test_dispute_service.py)
  exercise seeded business rules, ownership, case events, and recommendations.
  They do not evaluate the model.
- [Isolated replay](../../evals/run_mcp_replay.py) injects Account and Transaction
  SDK memory sessions into the existing workflow. A model run uses a real Foundry
  model and synthetic signed identity, without the BFF, database, real business
  APIs, or application JWT. Production HTTP/authentication defaults stay intact.
- [Dispute replay v2](../../evals/dispute_cases.json) contains 25 development-exposed
  synthetic confirmation cases with frozen expanded inputs. Approval leaves every
  score band IN_REVIEW; score routes review, not legitimacy or financial effects.
- [Historical scenarios](../../evals/scenarios.json) preserve 18 obsolete-policy
  diagnostics. Their label-derived baseline and keyword classifier are not current
  acceptance or untouched held-out evidence.

The isolated MCP replay's three cases cover balance lookup, canned denial, and empty
transactions, not disputes. The Hosted Agent CI workflow runs agent build/tests, the
real-model MCP protocol smoke and offline Dispute Replay
alignment, comparator and saved-evidence rescoring checks, with evidence uploads and PR reporting.
Offline dispute checks are not real-model dispute evaluation. `protocol_passed`
checks calls and completion, not grounding, locale, approval correctness or dispute
success; live authorization, persisted effects and hosted end-to-end acceptance
remain separate gates. See the [replay guide](../../evals/README.md#pr-smoke-check).

Offline alignment, comparator and saved-evidence rescoring tests, complete redacted
per-turn evidence, controlled failures and fingerprint-checked pairing are
implemented; they are not a paired real-model comparison. Instruction assertions and
mocked Identity/upstream or SQLite checks do not establish
model compliance, locale quality, live authorization, or PostgreSQL parity.
Exact commands and limitations are in the evaluation guide.

Next coverage must prioritize dispute identification, clarification, approval and
decline across turns, low/high/missing-score tool outcomes, safe refusals, and
status explanations grounded in returned cases/timelines. Simulated MCP replies
prove agent behavior only; real authorization and persistence require independent
service/integration checks. Generic MCP consent is not the business approval gate.

Replay reports use `home-banking-agent-mcp-replay-eval` and
`home-banking-agent-mcp-replay-eval run` as display names. Reports are currently
local/CI artifacts and `foundry_submission: not_submitted`; they do not create remote runs.
The azd-native hosted evaluation path cannot supply the required signed banking
identity header, even for prompts that need no tools. The separately authorized
historical hosted diagnostic can send that header; this is not native evaluation
or evidence of a successful hosted run. Hosted deployment success does not close
that gate.

## Hosted banking identity transport

The BFF sends a stable non-PII identifier in Foundry's reserved
`x-ms-user-identity` header and the signed banking envelope separately in
`x-client-banking-identity`. Foundry forwards `x-client-*` headers to the container
([contract](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-contract#forward-custom-request-headers-to-your-container)).
The hosted entrypoint installs pure-ASGI request middleware so signature validation,
locale and grant lookup use that envelope throughout streaming and concurrent
requests, without overwriting platform user IDs, call IDs or history partitions.
Missing, duplicate or invalid envelopes fail closed. Local transport still reads
the signed `x-agent-user-id` through the SDK's request context.

Deploy the BFF and agent changes together. Delegated opaque identity additionally
requires the caller to hold Foundry's
[`UserIdentityImpersonation` data action](https://learn.microsoft.com/azure/foundry/agents/how-to/isolate-sessions-per-user#isolate-sessions-for-your-own-users)
through an explicitly assigned custom role; built-in roles do not grant it.
Format compatibility does not establish that permission. The native OAuth Toolbox adapter remains gated:
header forwarding alone is not proof of customer/native-caller/grant binding or
two-user isolation. Do not capture these headers in logs or telemetry.

## Conversation State

The browser-facing BFF uses `previous_response_id` for both local and hosted
Responses turns. It exposes only a signed customer/endpoint-bound continuation
token to the browser, then verifies and translates that token on the next request.
See the [BFF continuity contract](../responses-bff/README.md) for completion,
cancellation and temporary versus saved case-history limits. This follows the
[Microsoft multi-turn guidance](https://learn.microsoft.com/azure/foundry/how-to/develop/framework-hosted-agents#multi-turn-conversations);
offline checkpoint tests do not establish live hosted acceptance.

[The entrypoint](main.py) passes a request-aware factory and
[a turn parser](src/app/adapters/responses_turn.py) to the native
`ResponsesHostServer(workflow=..., parse_response=...)`. The factory builds a fresh
workflow and fresh chat clients and Toolbox tools for every request, because the
hosting resolver rejects any resource shared across requests; clients are deduplicated
by deployment only inside one request. The runtime owns checkpoints and restores the
matching one before new input. The parser forwards approval and function-output
items as workflow replies and any other input as new messages.
[Native-route tests](tests/test_native_responses_host.py) and
[concurrency tests](tests/test_hosted_workflow.py) drive the real `/responses` route.

The entrypoint explicitly retains a 100-item transport history limit because
Responses 2.3.0 otherwise defaults to unlimited history. Workflow continuation uses
checkpoints, not that transport history; fresh input and immutable intake snapshots
are not truncated by this limit. [History tests](tests/test_responses_history.py)
cover the 99/100/101 boundaries and prefetched history IDs.

[Telemetry startup](src/app/telemetry.py) selects `AGENTSERVER_FLUSH_MODE=async`
before host construction. Export runs off the event loop but remains awaited;
cancellation/shutdown must not abandon an active flush.
[Mocked lifecycle tests](tests/test_telemetry_lifecycle.py) cover success, failures,
cancellation and shutdown admission without network exporters. This is telemetry
lifecycle protection, not durable business execution. Windows offline tests and
noneditable Python 3.13 installation do not prove Linux or hosted readiness.

[The checkpoint-aware handoff builder](src/app/adapters/checkpointed_handoff.py)
also synchronizes each fresh restored turn to the specialists before routing.
The installed orchestration SDK otherwise broadcasts only the initial input,
allowing a specialist's stale final answer to terminate a follow-up without an
answer. The adapter preserves normal completion, function approvals and
`request_info` continuations. It uses a narrow protected builder hook; revalidate
it when upgrading the SDK and remove it when upstream fixes fresh-turn broadcast.
[Actual-host regression tests](tests/test_native_responses_host.py) cover
previous-response-ID restoration, repeated turns, cross-specialist routing,
exactly-once input delivery and separate response chains; the stock SDK
`HandoffBuilder` is pinned there as a strict expected failure, so the test tells us
when the adapter can be removed. A
[consent boundary test](tests/test_native_consent_boundary.py) records that the stock
SDK turns a connect-time MCP consent error from a nested agent into a generic failed
response (no `oauth_consent_request` item, no link). The
[Toolbox consent middleware](src/app/adapters/toolbox_consent_middleware.py) defers
that error at connect, including the bare `MCPError` (-32006) that carries both
Account and Transaction sources, and re-raises it inside the run as native consent
output; a tool shared by two specialists reaches both. Binding and two-user isolation
remain unproven hosted, so OAuth Toolbox acceptance stays gated.
These deterministic tests do not establish hosted or real-model acceptance.

The default checkpoint provider uses `FoundryStateStore`. Outside Foundry hosting,
the installed SDK writes JSON files under `~/.agentserver/state_stores`, or under
`AGENTSERVER_STATE_ROOT/state_stores` when configured. Execution state is in RAM,
but checkpoints can survive process restarts. They are not stored in PostgreSQL.
Treat these files as sensitive conversation data; a browser reload neither deletes
them nor resumes them automatically.

Completed-response continuation and workflow checkpoints are not failed-turn retry
or exactly-once guarantees. The current browser locks an uncertain failed,
incomplete or disconnected thread to prevent blind mutation replay. Resilient
background execution, correlated operation recovery and safe chatbot retry are not
implemented. PostgreSQL case snapshots contain only submitted visible intake
messages; they cannot restore provider execution state or a complete chat archive.
