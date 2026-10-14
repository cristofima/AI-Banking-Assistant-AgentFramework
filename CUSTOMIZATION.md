# Agent Customization

The active workflow contains a triage agent plus Account and Transaction specialists. It is built in [hosted_workflow.py](app/agent/src/app/agents/azure_chat/hosted_workflow.py) and served through Foundry Responses by [main.py](app/agent/main.py).

## Supported Changes

- Refine triage, Account, or Transaction instructions.
- Adjust handoff descriptions between those agents.
- Add or refine tools on the existing Account and Transaction MCP servers.
- Improve generic MCP approval rendering and submission in the Responses frontend.

Adding another specialist or restoring ChatKit changes the locked architecture and requires explicit approval first.

## Agent Changes

1. Update the owning agent or workflow module under [azure_chat](app/agent/src/app/agents/azure_chat).
2. Keep agent instructions and MCP tool names and descriptions in English.
3. Preserve `FoundryChatClient`, `HandoffBuilder`, and the Responses host boundary.
4. Keep MCP calls async and avoid opening connections during module import or workflow construction.
5. Add focused coverage to `app/agent/tests/test_hosted_workflow.py` when behavior changes.

Run:

```powershell
rtk proxy uv run --directory app\agent python -m pytest tests\test_hosted_workflow.py tests\test_settings.py -q
```

## Tool Changes

Keep `mcp_tools.py` as a thin schema and delegation layer. Put business rules, data access, and customer-resource authorization in the owning [Account services](app/business-api/account/src/banking_account/services) or [Transaction services](app/business-api/transaction/src/banking_transaction/services) module. Keep transport adapters in `routers/`, DTOs in `models/`, and display conversion in `projections/`; preserve service-owned authorization and transaction boundaries.

## Browser Contract

The frontend signs in through the existing BFF login and sends messages and generic approval results to `/responses` through the BFF. The BFF validates the application JWT, binds the conversation to that user, and calls the local or hosted agent with server-side Azure credentials. Do not add a second or fake login path, direct browser-to-agent calls, ChatKit event envelopes, attachment upload flows, or mock session tokens.

Validate frontend changes with:

```powershell
rtk proxy npm --prefix app\frontend\banking-web run lint
rtk proxy npm --prefix app\frontend\banking-web run build
```
