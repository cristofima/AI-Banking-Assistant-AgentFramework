# Banking Web Frontend

React and Vite frontend for the Banking Assistant prototype. The assistant uses the local or deployed Responses BFF and does not call Foundry directly.

## Local Development

```powershell
cd app/frontend/banking-web
npm install
npm run dev
```

Node runtime baseline is `>=22` (aligned with `package.json` engines and frontend CI defaults).

Vite normally listens at `http://localhost:5170`. The root `DEV - Full Stack Ordered` VS Code launch starts Identity (`8090`), Account MCP (`8070`), Transaction MCP (`8071`), the local Responses agent (`8088`), the BFF (`8080`), and this frontend in separate terminals.

Account, Transaction, and the BFF are separate origins in every environment, local
and deployed, so the frontend calls each one by its absolute URL instead of relying
on a dev-server proxy. Locally those absolute URLs just happen to share the
`localhost` host with a different port per service:

| Service       | Local URL               |
| ------------- | ----------------------- |
| Account       | `http://localhost:8070` |
| Transaction   | `http://localhost:8071` |
| Responses BFF | `http://localhost:8080` |

## CI coverage and SonarQube

[Frontend CI](../../../.github/workflows/ci-frontend.yml) runs Vitest once through
[ci-node](../../../.github/actions/ci-node/action.yml), producing summary, HTML and
LCOV coverage in `coverage/`. After build and tests succeed, it calls the reusable
[SonarQube frontend workflow](../../../.github/workflows/sonarqube-frontend.yml),
which downloads `frontend-coverage-report` from the same workflow run, requires
an LCOV report and normalizes its source paths relative to the repository before
scanning. Tests are not rerun for SonarQube.

Repository variables `SONAR_ORGANIZATION` and `SONAR_PROJECT_KEY_TYPESCRIPT`, plus
the repository secret `SONAR_TOKEN`, configure the scan. Fork pull requests run CI
but skip SonarQube because they cannot access that secret. Python coverage is separate.

To generate the same coverage reports locally from this directory:

```powershell
rtk proxy npm run test -- --coverage.enabled=true --coverage.reporter=text-summary --coverage.reporter=json-summary --coverage.reporter=html --coverage.reporter=lcov
```

## Environment

| Variable                   | Purpose                                                     | Local default                     |
| -------------------------- | ----------------------------------------------------------- | --------------------------------- |
| `VITE_ACCOUNT_API_URL`     | Account API base URL (including `/api`)                     | `http://localhost:8070/api`       |
| `VITE_TRANSACTION_API_URL` | Transaction API base URL (including `/api`)                 | `http://localhost:8071/api`       |
| `VITE_RESPONSES_API_URL`   | Responses BFF chat/stream endpoint                          | `http://localhost:8080/responses` |
| `VITE_RESPONSES_BFF_URL`   | BFF base URL for authentication (`/auth/login`, `/auth/me`) | `http://localhost:8080`           |

Active browser reads call Account and Transaction directly, authenticated with the
same application JWT Identity issues through the BFF at login. Legacy REST client modules remain in
the source tree, but are not used by the in-scope financial screens.

The frontend signs in through the [Responses BFF](../../responses-bff/README.md) at `/auth/login`, keeps only the short-lived application JWT in browser storage, and restores verified identity through `/auth/me`. Profiles require a supported role and positive `identity_version`; only customers carry `customer_id`. Navigation uses the persisted name, with an email fallback. The frontend does not create fixed bearer tokens or synthetic profiles.

## Session lifetime

Access JWTs expire 60 minutes after issuance by default; navigation does not extend
their lifetime. Identity configuration can override this within 1–60 minutes.
Restart Identity and sign in again after changing the TTL: existing JWTs keep their
original expiry. Refresh tokens and renewal dialogs are deferred; expiry requires
a new login. Current-identity checks still fail closed.

## Chat recovery and case evidence

Help separates two kinds of history:

- **Chats from this session:** all chat types use bounded, tab-scoped `sessionStorage`,
  tied to the authenticated subject, identity version and server. Navigation and hard
  reload during the same login preserve signed completed-response continuation tokens
  and visible history.
  Explicit logout or a new login clears them. They are not a durable cross-login,
  cross-tab or cross-device archive. Interrupted or uncertain threads remain read-only;
  there is no automatic resend.
- **Saved case conversations:** owning customers can select their cases in Help and
  read the immutable PostgreSQL intake snapshot. It can omit older messages and does
  not include later exchanges. Legacy/direct cases can have no snapshot. This is not
  a general-chat archive and does not restore provider state or enable continuation.

Failed, incomplete or cancelled turns show an interrupted-response notice and offer
New conversation without deleting the old messages or case receipts. The new thread
does not reuse an uncertain continuation or resend the failed turn. Error retry hints
never unlock an uncertain thread.

Before chat consent dispatch, the browser must save the attempted proposal's bounded
recovery evidence; storage failure prevents creation. After same-login reload, that
proposal permits only read-only recovery, including when the composer is locked.
Absent or mismatched recovery results remain uncertain. Confirmed receipts retain
case status and View case without retaining proposal tokens. Recover acknowledgement
first reads the owned existing case, then starts a fresh BFF chat identifying that
case, never replaying intake or requesting creation consent again. Recovery is
single-flight and aborts on thread selection, a new conversation or identity change.

Recovery orchestration lives in
[ResponsesChatProvider](src/components/chat/ResponsesChatProvider.tsx), bounded
session persistence in [sessionHistory](src/components/chat/sessionHistory.ts), and
consent/receipt controls in [DisputePreviewConsent](src/components/DisputePreviewConsent.tsx)
and [DisputePreview](src/components/chat/widgets/common/DisputePreview.tsx).
The composer and recovery controls use the authenticated UI locale (en/es/pt).
Explicitly closed threads and saved case snapshots remain read-only; recovery does
not reopen them or enable stale consent or generic MCP approval cards.

The session's seven focused frontend regression files passed (218 tests); lint
passed with zero errors and 14 warnings, and build passed with the existing
chunk-size warning. The owner additionally confirmed recognizing a charge and
choosing not to report it stops intake without creating a case or requesting more
consent, and multi-turn intake preserves charge/context through acceptance and
subsequent existing-case consultation without recreation or second consent.
These are reported positive-path observations, not assistant-executed browser tests.
Retry was not exercised because no error occurred. Browser interruption/recovery,
reload, accessibility, real-model recovery acknowledgement and hosted acceptance
remain separate validation gates. Same-provider-operation replay and resilient
background execution are not implemented.

The browser sends signed `conversation` continuation tokens; the BFF translates them
internally to `previous_response_id` in both local and hosted modes. PostgreSQL snapshots
do not replace these tokens or workflow checkpoints. No Cosmos conversation export is used.

After recorded case acceptance and a successful assistant continuation containing
prose, Continue/Close offers an explicit choice once streaming ends. The controls
remain hidden during creation or failed continuation; transport completion is not
proof of the model's confirmation wording. Continue uses the primary style and Close
the destructive style, with responsive spacing. Close blocks further sends and
approvals in that thread, not the authenticated session; starting a new thread
remains available. An ordinary answer of “no” does not heuristically close the conversation.

Explicit intake acceptance forwards a bounded visible user/assistant snapshot.
Customer and assigned-operator case details display it with the shared safe Markdown
renderer in an initially collapsed section; customer history follows the timeline.
Stored original text remains unchanged, with loading, empty and retry states. It is
not provider trace evidence; legacy/direct cases can have no snapshot. Missing or
inaccessible customer cases show the same localized unavailable state and navigation
to support cases within the customer layout. See the
[Transaction history contract](../../business-api/transaction/README.md#customer-provided-case-conversation).
The owner confirmed history display, customer isolation and session change in the
browser. Reload, closure, the revised controls and exact-assigned-operator negative
checks remain user-owned validations.

## App Service Deployment

[Frontend CD](../../../.github/workflows/cd-frontend.yaml) requires GitHub
`Development` Variable `AZURE_WEB_APP_NAME`, manually populated from Terraform
output `AZURE_WEB_APP_NAME`. Shared setup exports it as azd `AZURE_WEB_APP_NAME`; the root
[manifest](../../../azure.yaml) targets that app through `resourceName`, not tags.
The provisioned naming convention remains `app-banking-web-<env>`.

The `VITE_*` settings above are build-time configuration, separate from the
App Service name. Use actual service hostname outputs for those URLs. Browser
authentication and chat stay behind the BFF; financial reads go directly to
Account and Transaction. No direct Identity or Foundry browser URL is needed.
See the [workflow guide](../../../.github/workflows/README.md#frontend-variables-cd-frontendyaml)
and [infrastructure guide](../../../infra/README.md). A successful build or deploy
alone does not establish browser or deployed financial acceptance.

## Identity Workspaces

- Customers retain the banking routes, direct Account/Transaction reads and BFF chat.
- Administrators use `/admin/operators` to list operators, activate or deactivate them, and reset their passwords through the BFF's `/admin/operators` endpoints. The protected `/admin/operators/create` page reuses the shared creation form and returns to the list after successful creation or cancellation. Operators/Customers navigation uses primary styling for the active tab and a bordered card background for the inactive tab. Persisted status must be `active` or `inactive`; the required ISO `updated_at` response field is displayed in the administrator's profile locale and browser time zone, with the original timestamp available on hover. Lifecycle fields come from identity responses, not JWT claims. The relocated `app/business-api/identity` service remains behind the BFF; browser endpoints are unchanged. Passwords remain transient and are cleared on submission or dismissal. Operator and customer action confirmations reuse the shared AlertDialog: a portal-backed centered modal with a dark overlay, z-index layering, focus trapping and cancellation autofocus. Errors stay inside the modal; operator creation remains a separate page. Pending operations disable dismissal until the list is refreshed; session unmount aborts pending work.
  Operator creation uses required, trimmed first/last names (50 characters each),
  email up to 120 characters and passwords of 12–256 characters. Labels are localized
  in en/es/pt; invalid payloads are rejected before transport. Existing nullable staff
  names remain readable without inventing names or a customer association.

- Administrators also use `/admin/customers` to list existing customer users and
  confirm activation/deactivation of sign-in access through explicit BFF endpoints.
  The localized list shows customer membership, persisted status and update time,
  including inactive users. Each change revokes prior sessions; activation requires
  a fresh sign-in. Banking customer status and financial data remain unchanged.
  Customer creation, deletion and password reset are unavailable. Loading, empty,
  retry, safe errors, revoked-session logout and pending-operation cancellation follow
  the operator workspace patterns. No browser Identity URL is introduced.
- Operators use `/operator/support-cases` for available and assigned case lists;
  `/operator` redirects there. Available cases are consented, unclaimed `IN_REVIEW`
  cases. Assigned cases include resolved cases and belong only to the authenticated
  operator, so they can be rediscovered after refresh. Owner-only detail uses
  `/operator/support-cases/:caseId`. Requests go directly to Transaction with the
  application JWT; Transaction revalidates the operator with Identity. The bodyless
  claim atomically assigns review responsibility and navigates to persisted detail;
  a controlled conflict refreshes the queue. Unclaimed cases have no detail link.
  Each view has isolated pagination, resets on switching views, shows the total and
  recovers after shrinking. Detail displays claim time and version, without the
  redundant operator subject, and shares timeline markup with customer detail while
  using operator-specific localized wording: consent and withdrawals remain customer
  actions, and assignment refers to the viewing operator's review responsibility.
  Cards separate the case title and status badge from labeled metadata and actions.
  Detail gives the unchanged customer reason its own full-width bordered block;
  assignment labels and values align in two columns on wider screens and stack on
  mobile. Typography, spacing and grouping distinguish fields without relying on
  color alone. Case timestamps use day, localized abbreviated month, year and a
  24-hour clock in the browser's local timezone (for example, `3 oct 2026, 23:35`
  in Spanish); timeline entries also include seconds. Movement tables retain
  `YYYY-MM-DD`. The shared `formatDateTime(value, locale, format)` helper accepts
  `YYYY-MM-DD`, `date-time` (default), or `date-time-seconds`; date-only formatting
  preserves the source calendar date without timezone conversion. Stored timestamps,
  audit text and custom resolution notes remain unchanged.
  Pending requests are canceled on route/session teardown. Claim records responsibility
  only. Assigned operators review versioned evidence and confirm valid/invalid verdicts
  with a mandatory rationale in the shared confirmation dialog. Pending restitution
  supports retry and explicit selection when multiple eligible destinations exist.
  Completed effects show the persisted movement, original currency amount and actual
  execution date; pending effects never imply credit. Card protection is a separate
  reason-required audited application-only action, not processor enforcement.
  Reassignment remains unavailable.
  Evidence is stored transaction data captured when the operator claims a case,
  not an external investigation or proof that a dispute is valid. Legacy assigned
  cases without a snapshot show an explicit unavailable warning, hide destination
  controls and disable adjudication; reading detail does not create evidence.
  Protection labels distinguish the current card state from the recorded prior state.
  Customer balance reload only repeats read requests; it never executes restitution
  or modifies balances. Destination unavailability alone does not imply that a valid
  verdict or pending restitution has already been recorded.
  Neither staff shell mounts financial screens or chat providers.

Direct and nested routes are role-guarded; unknown staff paths return to their workspace. Login destinations cannot redirect to another role's workspace or an external URL. Role guards are UI isolation, not a replacement for server authorization.

Login, logout and cross-tab token changes cancel pending identity work, clear query caches and notifications, and remount the authorized shell to discard local conversation state. The shell key includes session epoch, user, role and identity version; loading or failed profile restoration never mounts customer chat. Staff and administrator UI uses the profile's en/es/pt locale; logout resets it to English.

Verified sessions also schedule teardown at JWT expiry and recheck expiry on window focus. JWT payload parsing is only a UI timer, never an identity or authorization source. Customer Responses `AUTH_REQUIRED` or `ACCESS_DENIED` errors invoke the same logout boundary. Canceled streams and streams whose stored token changed cannot publish late text, approval widgets, conversation IDs, errors or completion callbacks into another session. Revocation is observed when the BFF rejects a request; this is not proactive revocation polling.

Focused Node tests cover customer launcher/routes, staff exclusion, failed/loading profiles, expiry/focus, logout and replacement-token stream isolation. Operator tests cover validated queue/detail/claim payloads, exclusive-claim controls, conflict refresh and request cancellation. Browser evidence remains unverified: customer launcher interaction, refresh/deep links, real expiry/revocation, cross-user/role transitions with open or minimized chat, and retained composer/widget/conversation cleanup require an authorized browser run through the BFF. Operator queue/detail/claim requires its own browser run using direct JWT-authenticated Transaction requests. Unit tests and builds do not close the live acceptance gates in issues #35, #42, #55 or #56; the operator claim and versioned final-verdict controls are implemented; live adjudication, posting and protection acceptance remain unverified.

Run `npm test`, `npm run lint` and `npm run build` for frontend checks. Administrator client tests resolve request paths against a synthetic base URL so they work both with a configured BFF URL and without local Vite environment files, as in CI. This test base does not change production transport configuration. Identity tests cover contracts, route mount isolation, session reset orchestration and administrator form handlers using the existing Node test environment. These are not browser or live-backend acceptance evidence. Manual checks still include three-role deep links and refresh, lifecycle persistence, expired/revoked sessions, locale display and cross-user logout isolation.

## Session validation (2026-10-04)

Frontend checks after adding versioned adjudication, pending-effects controls,
application-only protection and customer balance refresh:

```powershell
rtk npm --prefix app\frontend\banking-web test
rtk npm --prefix app\frontend\banking-web run lint
rtk npm --prefix app\frontend\banking-web run build
```

Results: 493 tests across 30 files passed; lint had zero errors and 14 existing
warnings; build passed with the existing large-chunk warning. These checks cover
shared posting/protection contracts, operator controls using actual product state,
nullable historical protection state, linked financial movements and session-scoped
balance refresh, not authenticated browser acceptance.
Historical suite counts elsewhere describe earlier runs, not the current total.

### Dispute presentation and recorded receipts

Operator context uses fixed source-evidence and stored-routing groups, with chronology
before the review controls and a separate missing-information summary. Missing snapshot
fields are shown even when omitted from the payload; zero scores and false flags remain
visible. The stored synthetic fraud signal supports routing only, not investigation or
proof of legitimacy. Customer statements, operator rationales and original audit text
remain unchanged.

Customer detail reads back the persisted case ID, transaction, reason and status. Consent
comes from the latest recorded approval event, never from intake acceptance or case
status. Financial receipts show recorded movement/destination references, original
currency, balance adjustment and execution time. Pending effects and invalid verdicts
never imply completed restitution. Recorded application movements do not confirm external
settlement; application-local protection remains a separately recorded action.

Dispute product, transaction-status, source and protection labels use controlled en/es/pt
lookups with unavailable fallbacks. Amounts use the shared precision-safe locale formatter.
Operator transaction statuses use canonical values for badge colors: Approved green,
Declined red, Pending amber and Reversed gray; unknown values use a neutral badge.
Labels are localized without changing stored statuses or financial calculations.

Stored fraud scores display localized numeric text out of 100 and an accessible meter
on a continuous green–amber–red scale. Only finite decimal strings in the inclusive
0–100 range, with at most four decimal places, are displayed; missing or invalid
scores remain unavailable, never zero. Scores retain their decimal precision without
a currency or probability suffix. Colors do not introduce risk bands, determine
legitimacy or change the existing routing threshold.

Owned operator detail also displays nullable `customerName` from Transaction's current
persisted Customer record, with a localized unavailable fallback. It is current customer
metadata, separate from immutable claim evidence; it is not included in queue summaries.
Older payloads without the field remain supported by the
[operator client](src/api/operatorDisputeClient.ts).
Multiple debit destinations still require explicit selection. No allocation or default
policy has been added; that policy remains unratified.

Focused validation from this directory:

```powershell
rtk npm test -- src\common\disputePresentation.test.ts src\components\DisputePresentation.test.tsx src\components\SupportCaseIntakeReceipt.test.tsx src\components\OperatorCaseActions.test.tsx src\components\SupportCaseFinancialDetails.test.tsx src\components\SupportCaseTimeline.test.tsx
rtk npm run lint
rtk npm run build
```

Results: 67 tests across six files passed; lint passed with zero errors and 14 existing
warnings; build passed with the large-chunk warning. These are synthetic frontend
regressions, not browser, live posting/protection, hosted or end-to-end acceptance.

### Pre-intake proposal and consent

Direct reporting through [ReportDisputeDialog](src/components/ReportDisputeDialog.tsx)
and the chat [DisputePreview widget](src/components/chat/widgets/common/DisputePreview.tsx)
share [DisputePreviewConsent](src/components/DisputePreviewConsent.tsx). The owned
Transaction preview displays amount/currency, localized date, masked card, merchant,
optional persisted country/city and the customer's verbatim reason. Before acceptance
it is a proposal, not a persisted case or successful receipt.

Accept explicitly authorizes case creation and review. The Transaction service records
case, consent and routing atomically in `IN_REVIEW`; new intake has no second review
approval. Decline/cancel creates no case or event. The [REST client](src/api/disputeClient.ts)
uses preview, token-based acceptance and read-only recovery. Acceptance expires after
ten minutes and recovery after 24 hours from issuance. An ambiguous response uses
recovery; null remains uncertain, not a successful receipt or permission for blind retry.
Chat continues a REST-accepted case with `getSupportCase`, not recreation. Existing
`WAITING_USER_APPROVAL` cases keep the legacy consent widget, separate from generic MCP
permission controls. Intake/review does not itself refund, adjudicate or protect a card.
See the [Transaction contract](../../business-api/transaction/README.md#customer-dispute-proposal-and-consent).

The owner reported the feature working. This is a limited user observation, not
completion of the en/es/pt browser, expired-session, cross-user, PostgreSQL contention
or hosted acceptance matrix.

### Chat streaming and approval parity

The [Responses provider](src/components/chat/ResponsesChatProvider.tsx) keeps POST SSE
through the BFF. Account, Transaction and support-case calls show correlated processing
tasks only while executing. Successful results remove their correlated activity instead
of leaving generic completion rows; failed or unfinished calls retain neutral failure
feedback without a loading shimmer. Concurrent pending calls remain independent.
Metadata does not prematurely hide the initial loading indicator. A call
declaration is not an execution receipt. The [stream reader](src/components/chat/useThreadStream.ts)
handles fragmented UTF-8, a final event without a newline and `[DONE]`, while rejecting
malformed events with controlled feedback and suppressing stale-session callbacks.
Text deltas are accumulated separately by output item or message ID; deltas without
an ID retain the legacy accumulator. Completed, incomplete and EOF turns without
meaningful text or a current-turn approval/validated consent card show one controlled,
localized failure instead of silently ending blank. Tool progress and handoff receipts
do not count as an answer; cancellation does not add this fallback.

Generic MCP approval cards remain separate from persisted dispute consent. Cards await
the resumed request's outcome: success disables duplicate submissions; failure or
cancellation permits retry. A transport failure can occur after server processing, so
an enabled retry is not proof that the server never consumed the approval.

For an already-persisted legacy case, a complete validated tool result inserts an inline
[dispute consent card](src/components/chat/widgets/common/DisputeConsent.tsx). The card
New proposals use the shared pre-intake consent component instead. The card
reloads the owned case through the existing authenticated Transaction REST client,
only offers approve/decline while `WAITING_USER_APPROVAL`, and refreshes persisted state
after an ambiguous POST failure. Consent does not itself grant tool permission, issue
a refund or protect a card. Progress, controls and feedback use en/es/pt catalogs.

Outputless-turn validation, run from the repository root:

| Command                                                                             | Result                                    |
| ----------------------------------------------------------------------------------- | ----------------------------------------- |
| `rtk proxy npm --prefix app\frontend\banking-web test -- --run src\components\chat` | 111 passed across five files              |
| `rtk proxy npm --prefix app\frontend\banking-web run lint`                          | Passed: zero errors, 14 existing warnings |
| `rtk proxy npm --prefix app\frontend\banking-web run build`                         | Passed with existing large-chunk warning  |

These provider regressions exercise visible error items, en/es/pt failure text,
interleaved output IDs, legacy deltas, approval retries and consent-only turns.
Earlier parity validation recorded 619 full-suite tests across 37 files and 13 existing
diagnostics from `tsc --noEmit -p tsconfig.app.json` outside the parity changes; neither
check was rerun for this update. Editor diagnostics report no errors in the three
changed chatbot source/test files. Browser, backend-producer and hosted end-to-end
behavior are not verified by these synthetic checks.

Owner browser checklist using the normal full-stack launch:

- Ask Account and Transaction questions: observe processing feedback followed by streamed text.
- Accept and reject a generic tool approval; simulate a failed continuation and retry.
- Preview a dispute in chat: no case exists before explicit acceptance; acceptance creates one `IN_REVIEW` case and detail link, without a second consent step.
- Decline/cancel a new proposal: no case or event is created. Verify legacy pending-case approve/decline separately.
- Accept a proposal manually, then retrieve that same case in chat; confirm readback without duplicate intake or second consent.
- Switch threads, cancel, logout and change customer: no stale text, cards or actions may leak.
- Repeat with en/es/pt profiles; compare inline case status with the persisted detail page.

## Localization

The [locale provider](src/context/UiLocaleProvider.tsx) uses the authenticated BFF
profile's exact `es`, `pt`, or `en` locale. Missing, regional or unsupported values fall
back to English; clearing the profile returns the UI to English. Login is always English.
There is no browser-language detection, language selector or separately stored UI locale.

The independent JSON catalogs for [English](src/locales/en.json),
[Spanish](src/locales/es.json) and [Portuguese](src/locales/pt.json) have matching flat
UI keys and nested transaction groups. [TypeScript configuration](src/i18n.ts) imports them statically; JSON separation
does not introduce lazy loading or network requests. The catalogs cover navigation, financial screens, loading,
empty and retry states, chat controls and generic approval controls. Product labels are
translated for display; stored values, identifiers, precision and conversation messages
are not rewritten. The chat welcome is static localized UI and does not send an automatic
request to the agent. Actual agent responses use the separately verified signed backend
locale context, not frontend translation.

The `transactions.types`, `transactions.categories`, and `transactions.statuses`
groups translate six types (including Payment), six categories, and four statuses
in the financial table. Unknown values retain their original label; nulls show the
localized unavailable state. Backend values still drive filters and calculations.
Agent-generated Markdown, merchant names and channels are not translated by these groups.
Nested lookups explicitly enable the dot separator without changing flat-key lookup.

Controlled [BFF error codes](../../responses-bff/README.md#controlled-errors) are mapped
to local UI messages. Raw backend details and unexpected exception messages are not
displayed; login errors remain English.

The consent-label audit added the flat `Card` key explicitly to all three catalogs:
English `Card`, Spanish `Tarjeta`, Portuguese `Cartão`. Spanish customer-facing dispute
and claim labels consistently use `reclamo`/`reclamos` with masculine grammar. Canonical
English keys, statuses and payload values remain unchanged, as do Portuguese dispute
terminology, customer-entered reasons and original audit text. The agent's authenticated
locale directive separately requires this terminology for generated Spanish prose.

Latest focused localization validation from the repository root:

| Command                                                                                                                  | Result                                    |
| ------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------- |
| `rtk proxy npm --prefix app\frontend\banking-web test -- src\i18n.test.ts src\components\DisputePreviewConsent.test.tsx` | 53 passed across two files                |
| `rtk proxy npm --prefix app\frontend\banking-web run lint`                                                               | Passed: zero errors, 14 existing warnings |
| `rtk proxy npm --prefix app\frontend\banking-web run build`                                                              | Passed with existing large-chunk warning  |

Catalog regressions verify consent-surface key completeness, `Card` translations,
Spanish terminology/grammar and canonical payload preservation. These results supersede
the earlier 16-test focused i18n snapshot, not unrelated full-suite evidence. Full
TypeScript checking previously reported existing errors in chat rendering/provider
types and legacy BFF mocks; Vite build does not prove those resolved. The owner reported
the feature working, but the complete authenticated browser localization, responsive
layout and live multilingual model matrix was not rerun for this follow-up.

## Account Page

The Account page calls `GET /accounts` on the Account API directly with the application
JWT (not through the BFF). Account scopes the result to savings and checking products
owned by the verified user/customer association. One account is displayed directly;
multiple accounts expose a selector with the type and masked number (product ID
fallback). Loading, retryable errors, and no-account states are explicit.

The holder comes from the authenticated profile. Type, status, opening date, currency,
and account number come from PostgreSQL; absent values are not fabricated. The Account
Codes section appears only when a stored number exists. SWIFT, IBAN, and routing numbers
are omitted because the schema does not provide them. Agreements and Privacy & Security
Policy are unchanged inherited placeholders.

Account uses a stable two-column desktop layout, compact panels, and aligned
label/value rows. Account Codes keeps its position when present; missing codes
do not cause the agreements and policy panels to change columns.

## My products and product movements

Dashboard is an owned account/card catalog with All, Accounts, and Cards category
filters; it does not fetch movement history or invent loans/investments. Product
tiles navigate to `/product/:productId`.
The account overview also links its selected account to that route. Standalone
Analytics navigation is removed; legacy `/analytics` redirects home.

[FinancialOverview](src/components/FinancialOverview.tsx) is reused exclusively by
product detail. The route ID must resolve uniquely against both owned catalogs
(`GET /accounts` and `GET /cards`). Missing, foreign, and ambiguous IDs show a
generic unavailable state without a history lookup. Missing or ambiguous bank
numbers also fail closed. Bank history uses the owned catalog bank number at
`GET /transactions/{product_number}/history`; card history uses only its opaque
ID at `GET /transactions/products/{product_id}/history`. There is no arbitrary
ID/number/PAN fallback or inferred card-to-account relationship. Bank numbers are
shown in full; card numbers remain masked even when their last four digits match.

Product detail uses
[ProductSummaryCard](src/components/ProductSummaryCard.tsx): translated type/status,
full bank number or masked card number, recorded balance, and opening date. Card
summaries also show expiry; only credit cards show a credit limit. The detail page
places this summary above a bordered movement-filter panel, responsive currency
totals, and the movement table. Totals methodology is available in an expandable
section. Labels and exact monetary display follow the authenticated en/es/pt locale;
display formatting does not change calculation precision or canonical filters.
Standalone Cards and Investments pages and navigation entries are removed. Legacy
`/credit-cards` and `/portfolio` URLs redirect home. Card numbers show the first
and last four digits (`4111 **** **** 1234`); legacy last-four-only masks are
preserved without inventing a prefix. The Account API masks card numbers before
returning them to the browser.

The default inclusive date window covers today and the preceding 29 calendar days.
Date query parameters carry only `start` and `end`, not product selection.
The [financial client](src/api/financialClient.ts) fetches every transport page in
batches of 100 before exposing records. It rejects changing totals, duplicate IDs,
foreign account records, and incomplete pagination. The detail table then pages
those complete records in groups of 25; summaries always use the full window.
Product/window and session changes abort old requests and suppress stale rows and
report controls immediately. There is no first-product fallback.

Balances are stored current balances, not balances reconstructed for the selected
window. Transaction dates display the calendar date returned by the service without
browser timezone conversion. Missing fields remain unavailable. Loading, empty,
invalid-window, failure, retry, and rejected-session messages are explicit; a
financial `401` message does not itself clear the authentication context.

Product detail derives movement totals with exact four-decimal arithmetic, separately
for each currency. Only `Approved` records qualify: `Deposit` is inflow, while
`Payment`, `Purchase`, `Transfer`, and `Withdrawal` are outflow. Adjustments, unknown
types, other statuses, and negative amounts are excluded and counted. These are
dataset-policy movements, not income/spending classifications or historical balances.
No mixed-currency total or estimated monthly snapshot is displayed.

Dashboard and product detail have no mock financial fallback. Payments, investments,
beneficiaries, and card mutations remain unavailable rather than simulating data
or operations.

## Transaction Disputes

The [support-cases list](src/pages/SupportCases.tsx) and
[case detail/timeline](src/pages/SupportCaseDetail.tsx) pages, at `/support-cases` and
`/support-cases/:caseId`, read and act on Transaction's `/api/support-cases` REST
surface directly through [disputeClient.ts](src/api/disputeClient.ts), authenticated
with the same application JWT as the rest of the direct reads above, never through the
BFF. A [`ReportDisputeDialog`](src/components/ReportDisputeDialog.tsx) is wired into
the selected-card product movement table for `Approved` rows and opens a new case from a
customer-entered reason.

Reporting is hidden for transactions older than the approved 365-day window,
using the real clock, and uses a light-blue outline trigger. The selected-card detail fetches the
complete customer-scoped support-case list once, not per transaction row. Every
non-`RESOLVED` case replaces the report action with an existing-case link. Reporting
fails closed while that list loads or fails, with a retry action. Requests abort and
eligibility resets on identity/session changes. Submission rechecks the complete list,
redirects to an existing active case, and refreshes eligibility after creation.
Failed submissions retain controlled error feedback; retry rechecks the complete list
before any creation request. A controlled `DISPUTE_ALREADY_ACTIVE` conflict reloads the
complete list and opens the existing active case. If that reload fails or finds no active
case, localized conflict feedback remains visible and retry revalidates eligibility.
The case contract accepts workflow/effects metadata without adding an evidence UI.
Timeline fallback prefers the backend's optional `displayMessage` projection over the
unchanged original `message`; arbitrary resolution notes remain visible. Known legacy
generated messages retain truthful localized display templates.
The backend remains authoritative; frontend suppression is not a database concurrency
or duplicate-credit guarantee.
Resolved transactions can currently be reported again. Eligibility also accepts
approved card deposits; restricting transaction types requires an explicit policy decision.

Under [ADR 0009](../../../docs/adr/0009-operator-verdicts-with-recorded-financial-effects.md),
assigned operators issue versioned final verdicts. Valid verdicts stay `PENDING_EFFECTS`
until compensation is recorded; completed application effects have separate linked
movements and balance adjustments without rewriting the original transaction. Card
protection is a separate audited application action, never an automatic consequence
of consent or verdict and never proof of external processor enforcement. Historical
records do not trigger financial backfills. Live posting/protection acceptance remains
unverified.

Recommendation and timeline projections are localized in en/es/pt. Customer reasons,
operator rationales and original audit text remain unchanged; arbitrary resolution
notes remain visible. Display uses recorded state, not inferred human review or
settlement. Earlier localization checks passed, but synthetic tests do not establish
authenticated three-language browser parity.

The detail page labels the product number as Card Number, localized in en/es/pt.
The approve/decline gate appears while `WAITING_USER_APPROVAL`. Approval starts
review, not closure: low, high, and missing fraud scores all remain `IN_REVIEW`
until an assigned operator's explicit verdict and any required recorded effects.
Low-score cases show the localized `REVIEW_REQUIRED` event; a low score neither
determines legitimacy nor authorizes automatic closure. Historical catalog assignments
are not real operator claims. Current takeover and verdict controls use the authenticated
assigned operator; customer final-resolution authority is retired. No automatic review
timeout is implemented. Previously resolved cases remain unchanged.

The detail page also shows the full event timeline and the single post-resolution
recommendation card with an explicit dismiss action once a case resolves favorably. All
status, resolution, and event values are machine-readable codes translated for display
through a dedicated `support-cases.*` i18n namespace in all three locale catalogs.

The backend's `DISPUTE_WINDOW_DAYS` demo policy (365 days, evaluated against the real
system clock) rejects opening a _new_ dispute once every loaded transaction falls
outside that window; this is a dataset-staleness constraint, not a frontend bug.

The customer list masks full card numbers and shows a localized unavailable label
when no number exists. Case links encode their identifiers. Detail headers and legacy
consent actions wrap on narrow screens; financial references, intake receipts and
timeline content wrap without truncating stored text. Manual timeline notes preserve
literal line breaks.

Customer-page regressions cover rendering, loading/empty/refresh states, duplicate-action
suppression, persisted-state readback, cancellation, authentication failures and
stale-session isolation. These synthetic checks do not establish responsive browser
acceptance or live service authorization.

## UI-readiness validation

Checks run from the repository root after the customer layout/privacy repairs and
operator customer-name, badge and score-meter changes:

| Command                                                                                                                                                                                     | Result                                       |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| `rtk proxy npm --prefix app\frontend\banking-web run test -- src\components\DisputePresentation.test.tsx src\api\operatorDisputeClient.test.ts src\components\OperatorCaseActions.test.tsx` | 155 passed across three files                |
| `rtk proxy npm --prefix app\frontend\banking-web run test`                                                                                                                                  | 776 passed across 41 files                   |
| `rtk proxy npm --prefix app\frontend\banking-web run lint`                                                                                                                                  | Zero errors; 14 existing warnings            |
| `rtk proxy npm --prefix app\frontend\banking-web run build`                                                                                                                                 | Passed with the existing large-chunk warning |

Related Transaction operator/adjudication tests passed 45 synthetic cases; see the
[Transaction guide](../../business-api/transaction/README.md). No browser, stack,
database or cloud run was performed for these changes. Authenticated en/es/pt browser
checks, narrow-screen layouts, score-meter endpoints and live/hosted data parity remain
separate acceptance gates. Earlier suite counts in this README are historical snapshots.

## Validation

```powershell
npm test
npm run test -- --coverage.enabled=true --coverage.reporter=text-summary --coverage.reporter=json-summary --coverage.reporter=html
npm run lint
npm run build
```

Frontend CI coverage uses Vitest's V8 provider and requires `@vitest/coverage-v8` in `devDependencies`.

Historical financial arithmetic and pagination validation passed 19 tests, the build
and focused financial lint. Historical global lint errors described below were resolved;
the latest full run passes with 14 warnings (see chat streaming validation above).

The card page compiled successfully, and the user confirmed local credit/debit card
display. The Account API's JWT-authenticated contract suite passed, including masking
and ownership cases (see the [business-api guide](../../business-api/README.md)). Card-specific browser recovery and complete two-user parity
have not yet been recorded. The user supplied desktop screenshots of the card panels
and Dashboard. Subsequent card-grid, Account, and Dashboard presentation changes
passed build and focused lint; the 19 frontend tests passed again. The latest layout
has not been independently inspected in an authenticated browser session. Account
retains inherited placeholder-link accessibility diagnostics; the latest global lint
has warnings but no errors.

Desktop browser balances and transaction rows matched direct, JWT-authenticated Account
and Transaction reads for
two approved users; PostgreSQL comparisons were performed separately. Transaction
`503` and `401` UI states were simulated. Natural session expiration, normal pointer
interaction, and the complete signed agent/MCP matrix remain open. Mobile responsive
validation is deferred.

The assistant uses the verified email from signed BFF identity for Account and
Transaction inquiries, with generic approval-event support. User-supplied local
browser evidence on 2026-09-30 shows an owned-account answer with a full bank number
and masked card number, and a foreign-account denial rendered after `ACCESS_DENIED`.
The reported blank chat response is resolved for these cases; Transaction chat,
missing/empty results, multi-turn and approval continuation remain separate checks.

Temporary threads, visible messages and signed completed-response continuation
identifiers survive same-login reloads through tab-scoped session storage. Explicit
logout/new login clears them; a JWT alone is not a durable archive of general chats.
Help separately lists owning-customer, read-only PostgreSQL case-intake snapshots,
which survive logout but cannot resume a thread or include later exchanges. Failed or
uncertain turns remain conservatively locked; automatic recovery is not implemented.
Hosted identity transport and deployed financial parity are not verified; the
separately tracked real-data verification checklist lives outside this public
repository. Payment submission and attachment upload are not active features.
