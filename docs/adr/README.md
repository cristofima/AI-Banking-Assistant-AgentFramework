# Architecture Decision Records

One ADR per significant, hard-to-reverse decision — the problem, the alternatives
weighed, the choice made, and its consequences. See
[ARCHITECTURE.md](../../ARCHITECTURE.md) for the current state of the system these
decisions produced; read these records for _why_, not _how it works today_.

| ADR                                                                 | Decision                                                                                              |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| [0001](0001-single-workflow-scope-with-dispute-support-case.md)     | Single workflow scope: Account/Transaction inquiries with one transaction-dispute support case layer  |
| [0002](0002-deterministic-fraud-score-triage-over-trained-model.md) | Deterministic `fraud_score` threshold triage instead of training a new fraud-detection model          |
| [0003](0003-postgresql-as-the-operational-data-store.md)            | PostgreSQL as the operational data store for the live tool-calling path                               |
| [0004](0004-custom-jwt-authentication-over-entra-id.md)             | Custom email/password JWT authentication instead of Microsoft Entra ID                                |
| [0005](0005-frontend-calls-account-and-transaction-directly.md)     | Frontend calls Account and Transaction directly; BFF trimmed to identity and agent proxy              |
| [0006](0006-dedicated-auth-users-and-staff-identities.md)           | Dedicated Identity, DB-free BFF, independent staff identities and per-request revocation              |
| [0007](0007-real-operator-exclusive-dispute-takeover.md)            | Real operator exclusive dispute takeover without verdict or financial authority                       |
| [0008](0008-operator-ownership-without-service-agent-catalog.md)    | Real operator ownership, retiring the simulated catalog while preserving historical evidence          |
| [0009](0009-operator-verdicts-with-recorded-financial-effects.md)   | Assigned-operator verdicts with truthful terminal states and atomic, ingestion-safe financial effects |
| [0010](0010-bounded-customer-provided-case-conversation.md)         | Immutable bounded customer-provided conversation evidence at accepted intake                          |
| [0011](0011-support-domain-postgresql-schema.md)                    | One PostgreSQL support-domain schema with record-preserving table migration                           |
| [0012](0012-local-oauth-jwt-validation-for-mcp.md)                  | Proposed local OAuth JWT validation at direct MCP boundaries with a one-hour revocation bound         |

Once a record's `Status` is `Accepted`, its Context/Decision/Consequences are
immutable. A changed decision gets a new ADR that supersedes the old one; the old
record's `Status` is updated to `Superseded`, never edited in place.
