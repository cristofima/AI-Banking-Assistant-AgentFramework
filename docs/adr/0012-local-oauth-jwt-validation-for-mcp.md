# ADR 0012: Locally Validate Short-Lived OAuth JWTs at MCP Boundaries

| Status   | Proposed by |
| -------- | ----------- |
| Proposed | cristofima  |

---

## Context

The owner requested standard OAuth bearer delegation without custom HMAC headers
on Agent-to-MCP calls. Existing Account and Transaction services already enforce
customer ownership, independently of the authentication transport. Opaque OAuth
tokens require a network introspection call for each tool request; signed tokens
can remove that dependency but delay revocation until expiry.

Custom browser authentication and the signed BFF-to-Foundry identity envelope are
separate boundaries. Foundry uses Azure authentication on its Authorization header,
and native Toolbox per-user OAuth grant binding remains unverified.

## Decision

Implement an opt-in direct-HTTP MCP path using Identity-issued RS256 access tokens.
MCP services validate trusted public keys, issuer, audience, expiry, customer claims
and exact `mcp.access` scope locally, then preserve existing customer-resource ownership
checks. The scope authorizes the existing customer MCP surface, including dispute
operations; verified customer role, resource ownership and explicit business consent
still determine whether each operation is allowed. Operator/admin agent access is excluded.
Previously issued `banking.read` grants are rejected, not promoted to mutation authority.
Only the standard Authorization bearer reaches MCP; invalid OAuth requests never
fall back to HMAC. Preserve HMAC as the default compatibility path.

The confidential BFF owns authorization-code S256 exchange and server-side token
custody. It delegates the current grant through its existing signed identity
envelope, never through browser token storage or model instructions. OAuth Toolbox
routing fails closed until real per-user delegation is proven.

Use a 3600-second (one-hour) access-token lifetime, requested on 2026-10-18,
with no implicit refresh. Authorization codes and BFF state retain 300 seconds.
Keep browser login and REST current-identity checks unchanged. OAuth consent to
`mcp.access` does not replace case-creation consent or operator adjudication. Enabling the local JWT path in a runtime
requires owner acceptance of its bounded revocation delay; this record remains
Proposed until that security tradeoff is explicitly accepted.

## Alternatives considered

- Opaque tokens with uncached introspection: provides immediate Identity-state
  enforcement but retains the network dependency the owner requested removing.
- HMAC-only MCP: preserves compatibility but does not meet standard OAuth delegation.
- Remove the BFF-to-agent envelope as well: cannot convey banking identity through
  the Azure-owned upstream Authorization header without a proven replacement.
- Native Toolbox OAuth: promising hosted transport, but current discovery and
  deployment evidence does not establish per-user grant binding.
- JWTs longer than the requested one-hour bound or browser-managed grants: further
  increases revocation exposure or leaks downstream credentials beyond the BFF trust boundary.

## Consequences

Protected MCP reads no longer depend on per-call Identity availability. Standard
bearer transport works with direct local MCP URLs without requiring Toolbox.
Ownership, issuer trust and scope checks remain independent requirements.

Revoked users, changed profiles and rotated identity versions can retain MCP access
until an issued JWT expires, up to 3600 seconds (one hour). The longer requested
lifetime expands this exposure; changing the TTL does not prove security acceptance.
Operators must distribute trusted public keys and manage signing-key rotation explicitly. BFF restart loses in-memory
grants and requires authorization again. Existing clients restricted to `banking.read`
need explicit reconfiguration and fresh consent; persisted grants are not silently
expanded. A broad MCP scope reduces scope complexity but makes strict role and
per-resource service authorization essential.

The owner confirmed local OAuth consent and an account inquiry through frontend,
BFF, local agent and direct MCP. This is limited positive-path evidence, not two-user
isolation or acceptance of the revocation tradeoff. Offline regressions are implementation
evidence, not complete live authorization, browser, hosted-model or deployed acceptance. Runtime configuration and those checks
remain separate gates. See the [Agent guide](../../app/agent/README.md),
[BFF guide](../../app/responses-bff/README.md) and
[Identity guide](../../app/business-api/identity/README.md) for setup details.
