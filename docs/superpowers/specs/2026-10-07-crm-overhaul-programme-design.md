# CRM overhaul — programme design

Status: **proposed, design only — pending owner review.** No code is changed by this document or by
the four sub-project specs and plans that hang off it. Base: `main` @ `caa7f112` (the CRM paths are
identical at the working tree's `HEAD` `3bc7f6ca`). Alembic heads today: deal `e94d7a2c5f18`,
customer `e230c025337e`, lead `91dd3ca663fd`.

Reconciled on 2026-10-10 with the interface registry and the four specs. Where a spec departs from this
document, its deviations section (SP1 §10, SP2 §17, SP3 §17, SP4 §17) records why, and this document
carries the result.

Evidence base: two read-only discovery runs, 26 agents, each finding re-attacked by an independent
skeptic (46 claims corrected, none left unchecked): `wf_9a018e15-345` (the three register items
mapped end to end) and `wf_4cf65cd6-720` (whole-CRM audit: backend inventory, `product-crm` UI,
two cross-product integration sweeps, new-feature gaps, packaging). Where this document states a
fact it is one a skeptic confirmed or one re-verified by hand; inferences are labelled.

## 0. Document map

Specs and the registry are under `docs/superpowers/specs/`, plans under `docs/superpowers/plans/`.

| File | What it is |
|---|---|
| this file | The programme contract: goal, decisions, target picture, decomposition, ecosystem map, owner-decision register, release train |
| `2026-10-07-crm-overhaul-owner-review-guide.md` | The owner's route through the set, pointing to the sections of the other documents to read. The review is this guide plus the sections it points to, not every document end to end (§11) |
| `2026-10-07-crm-overhaul-interface-registry.md` | The binding source of names for every interface that crosses a sub-project boundary (platform-core API, schema, routes, events, permissions, settings, shared UI, tools, documents) and the wiring matrix; its §J records how each conflict between a spec and this file was resolved |
| `2026-10-07-crm-1-foundations-and-cleanup-design.md` + `plans/2026-10-07-crm-1-foundations-and-cleanup.md` | Sub-project 1 |
| `2026-10-07-crm-2-platform-backbone-design.md` + `plans/2026-10-07-crm-2-platform-backbone.md` | Sub-project 2 |
| `2026-10-07-crm-3-core-revamp-design.md` + `plans/2026-10-07-crm-3-core-revamp.md` | Sub-project 3 |
| `2026-10-07-crm-4-suite-connection-design.md` + `plans/2026-10-07-crm-4-suite-connection.md` | Sub-project 4 |

Precedence: the registry's names bind every spec and plan; where a spec's deviations section records a
departure from this file, this file now agrees with it; a D-id means the row in §8.

Plan status. SP1's plan is written to step level. SP2's is step-level for R2.1–R2.3 and task cards for
R2.4–R2.9. SP3's and SP4's are release outlines (task cards per release), expanded to steps when the release
starts, because earlier releases change the files a later card touches.

## 1. Goal and success criteria

The owner asked for one thing in six parts: **new features, revamped existing features, a revamped
UI, the unwanted things removed, a CRM that is clean and understandable, and its data connected to
every OpsGrids product.** Success means all of the following are true and checkable:

1. **Reps can work.** A rep in a CRM-only tenant (no HRMS) reads the shared book at ORG and edits exactly
   their own records through OWN scope, and a manager works a team. Today they cannot (§3 facts F1, F2).
   Reps keep reading at ORG on purpose (registry J19, D-38): narrowing reads would hide teammates' records
   from every rep's lists, boards and 360, a larger change than D-9 approves.
2. **One vocabulary.** A reader can learn the model from one glossary (§4.1) and the API, the UI,
   the events and the docs all use it. Nothing in the repo describes a CRM that no longer exists.
3. **Nothing dead is shipped.** Every item on the confirmed prune list is gone, and every removal
   that had a live user was replaced first (§3, SP1).
4. **The daily-use loop exists.** Follow-ups and reminders, lead capture with duplicate detection,
   assignment, win/loss reasons, saved views, search and sort, a contact page with the customer 360,
   configurable stages, a settings hub, reports.
5. **One customer identity across the suite.** Every product that holds a customer, contact or
   client record links it to the CRM company or contact by a plain UUID, and the CRM can show what
   every other product knows about that party (§6).
6. **No regression of the platform's guarantees.** Tenant isolation, append-only records, PII
   sealing and audited reveal, consent, server-side authorization, the one-box memory budget and
   the manual-deploy release process all hold at every release (§7).

## 2. Decisions (owner, 2026-10-07)

| # | Decision |
|---|---|
| M1 | The work is one programme of **four sub-projects**, each with its own spec and plan: (1) foundations and cleanup, (2) platform backbone, (3) core revamp (features and UI), (4) suite connection. Order 1 → 2 → {3, 4}. |
| M2 | **Independent releases.** Each release is reversible on its own and follows review → merge → manual deploy → verify, stopping on any failure. No bundled big-bang release. |
| M3 | **All four connection loops matter, and data must reach the whole ecosystem:** money (finance, orders, storefront), messaging (Engage, WhatsApp, consent), service and work (support, projects, PulseBoard), analytics (insights, dashboard), and every other product that holds a person or a company. §6 maps all eleven products and the platform services. |
| M4 | This pass produces **specs and plans only** — all four of each, for one review. Building starts after the owner approves them. |

Inherited decisions this programme does not reopen: ADR-0000 (schema per service, no cross-schema
FKs, RLS, outbox, money in integer minor units, append-only ledgers); ADR-0008 Q5 (CRM keys OWN on
the membership, via NEW-7 then C15); ADR-0017 (`lead` is a third process of the one `crm` product;
`pipeline` and `activity` services do not exist; configurable stages extend `deal`'s own schema);
ADR-0020 (insights projection); ADR-0021 (consent ledger); ADR-0022 (browser-side bulk import and
export); ADR-0007 (permission rollout to live tenants); ADR-0036 (the edge box is staging-like and
agents never touch production data).

### Known limits of M2 and the memory assumption

M2 holds for code, schema and configuration: every migration is additive, the previous image tolerates the
new columns, and each release's first lever is a mode or a setting that loses nothing. It cannot hold
everywhere. The owner accepts what follows, or refuses a release that needs it, by approving the specs.

**Six cases where a release is not reversible on its own** (SP4 §17 item 12; each rollback section names
the rule that replaces the guarantee, and ends with a table of what a rollback loses and its stop conditions):

| | Case | What replaces the guarantee |
|---|---|---|
| a | An erase is irreversible by design, and the previous customer image does not know `erased_at` | customer is not rolled back once any contact has been erased (SP4 §6.10) |
| b | A tax invoice the order-to-invoice chain issued, and a company the lifecycle setting promoted, survive a rollback (a legal document is corrected only by a credit note; a promotion keeps `lifecycle_promoted_at`) | The levers are each organization's own switches and the box mode; stop conditions watch the first organizations that opt in (SP4 §8.9) |
| c | A quote in `pending_approval` has no handler in the previous approval and deal images | Neither is rolled back while one exists; "Resend request" is the catch-up (SP4 §10.9) |
| d | Approval moves from the `hrms` profile to core: a 99 MB container on every box with `crm` and no `hrms` | Reverting the profile line stops the only consumer of the discount request for those tenants; ADR 0042 gates the deploy (D-18, SP4 §10.8) |
| e | Events a code rollback discards: a bound queue outlives its handler, the message is acknowledged and dropped, and a published outbox row cannot be replayed | Recoverable only where an owner catch-up tool exists (registry §H), "Resend request" or `:send-to-crm`; elsewhere the loss is stated as forward-only with its reason, or a stop rule forbids the rollback (SP4 §4.7) |
| f | An in-place edit of the unshipped insights migration | Made only after the owner's database check shows it has run nowhere; otherwise a forward revision ships (SP4 §13 step 10) |

The alternative to accepting (a) to (d) is not building the release that causes it.

**Three one-way points in SP3** (SP3 §14 K1 to K3; the owner acknowledges them in its step O1, §17 item 2):
**K1** R3.1 cannot be rolled back below R3.1a once a phone-only contact exists; **K2** R3.7 cannot be rolled
back below R3.7a once a captured lead exists, and a stored visitor's details can then only be removed by an
administrator's per-lead erase; **K3** the TEAM widening in R3.8 cannot be narrowed by any tool (the
reconcile only adds grants, ADR-0007), so it is a separate owner act after a soak, with the evidence written
down first.

**The "no memory increase" assumption behind D-13 does not hold.** The box holds
`EDGE_BUDGET_ACCEPTED_MB=3944`, exactly today's total for the ten profiles plus `admin` (the ten alone are
3747 MB of the box's 3862 MB), so every extra MB needs the owner's edit. Each consumer fold moves a container
from the `api` kind (98 MB) to `api_with_worker` (99 MB) in `infrastructure/edge/memory-budget.json`, and the
programme has five: deal (R2.4), lead (R3.8; TEAM scope cannot work without it) and SP4's support (R4.4),
marketing (R4.6) and storefront (R4.7). The ceiling rises 3945, 3946, 3947, 3948, 3949 in that order (ten
profiles 3752 MB at the end), each time to the total `make edge-preflight` prints, which depends on merge
order. On top of the folds: insights (+99 MB, R4.6-B; ten profiles 3851 MB, with `admin` 4048 MB); a PDF
library for quote and invoice PDFs resident in `deal` and `finance` (R4.5-B), not measured, 10 to 20 MB each
by inference, which with insights puts the ten profiles at 3871 to 3891 MB, above the box's 3862 MB; and two
profile moves that leave the ten-profile totals alone but load smaller boxes, the scheduler to core (R3.6,
+90 MB on a box with `crm` and no `finance`) and approval to core (R4.5, +99 MB on a box with `crm` and no
`hrms`). So D-13 is answered before R2.4; the owner raises the ceiling release by release or frees memory
(D-11 would free about 98 MB; registry L17 lists three fold alternatives that trade memory for
cross-service calls); insights and the PDF library ship only after a separate owner answer; and the
preflight, which refuses above 125% of RAM (4827 MB), is the stop condition.

## 3. What discovery established

Facts first, because several corrected what the register and ADRs say. Paths are relative to the
repo root.

**F1 — OWN scope is broken for CRM-only tenants, and the platform change it needs has not landed.**
`ScopeContext.apply` and `assert_can_touch` hard-wire `principal.subject_ref`
(`libs/platform-core/src/platform_core/auth.py:341,350,373,375`). `register_scope_resolver` writes a
module dict nothing reads (`auth.py:297-301`), so ADR-0008 Q5's "a resolver may declare a membership
subject" cannot work as worded; NEW-7 is larger than the register says. About 20 call sites read
`subject_ref` directly and bypass both methods (CRM 7–8, ITSM 5, projects 2, timesheet 1). The `mem`
claim and `X-Membership-Id` header already exist (`auth/tokens.py:108-109`, `gateway.py:665-672`), so
no token or gateway change is needed. `subject_ref` is only ever an HR employee id.

**F2 — ADR-0008's migration recipe does not match the data.** It says owner ids migrate "from
employee ids to membership ids through account's link". CRM owner ids are not employee ids: they come
from `customer.owners.id`, a random uuid4 the API cannot set
(`services/customer/app/services.py:212-249`), except deals and activities, which default `owner_id`
to the caller's `subject_ref`. So existing values are a mix. Today's behaviour is milder than the ADR
implies: default rep roles read at ORG and write, convert and reveal at OWN; with a null `sref` the
OWN actions are already refused 403, so a deploy does not empty a rep's list — C15 *newly enables*
OWN writes and reveal for reps, which is an access widening needing sign-off. The only evidence tying
a roster row to a person is email (roster email vs the member's verified email); that is a heuristic,
and ADR-0008 Q1 rejected email matching for the HRMS link. The mapping must be human-reviewed.

**F3 — NEW-9 as written is unsafe.** `lead.finalize_conversion` bumps `conversion_generation` only
for `owner_changed` aborts (`services/lead/app/services.py:1125-1132`); a `status_changed` abort
leaves the same derived ids live, and `deal`'s internal convert route treats a soft-deleted deal at
that id as a successful replay (`services/deal/app/api/internal.py:123-142`, pinned by
`services/deal/tests/test_lead_conversion_internal.py:97-121`). A cleanup followed by a retry can
therefore convert a lead into a dead deal, and a slow cleanup can destroy a successful retry. The
event names the ids customer/deal *returned*, which can be pre-existing records customer reused by
name or email — it carries no provenance. `fail_conversion` stages no event, so the
generation-bump leftovers ADR-0017 says the event covers have no hook. Marking the deal `closed_lost`
would distort win-rate and can fire tenant WhatsApp/email automations on `stage_changed`. Every
`crm.lead.conversion_aborted` emitted to date was dropped: a queue exists only once its consumer has
run, and an event with no bound queue is silently dropped while its outbox row is marked published
(`libs/platform-core/src/platform_core/events.py:250-262, 453-481`).

**F4 — Configurable stages is wider than `deal`.** The six keys (`lead`, `qualified`, `proposal`,
`negotiation`, `closed_won`, `closed_lost`) are a published contract: `deal.app.domain.STAGE_ORDER`
(`services/deal/app/domain.py:21`), customer-360's literal open-stage tuple and "hot = negotiation"
(`services/customer/app/c1_360.py:95,175-177`), three event contracts, the notification rule and
automation engine (tenant-authored `payload.to eq closed_won` rules), the SPA board, import/export
and seeds, about ten deal test files, and — on the unmerged insights branches — a CHECK constraint
on exactly these six values. Three base-state surprises: this tree has three deal migrations, not
four (the fourth, `a1c9e2f7b3d4`, exists only on `sprint/insights-q2` and re-parents on the same
head); the committed deal event schemas are "ahead of the producer" (required fields the producer
does not emit); `deal` has no consumer and no deployed worker.

**F5 — Nothing connects the CRM to the rest of the suite except messaging.** The live cross-product
CRM links are: notification ↔ customer (messaging target, phone → contact), customer-360 → deal and
notification, lead → customer and deal conversion. The finance/operations bridge (PR #88) is *not*
live: `operations/app/orders/services.py:871-892` hard-returns `None`, finance's
`customers:resolve-for-crm-contact` has no caller, finance has no `CUSTOMER_INTERNAL_URL` in compose,
`crm_contact_id` is never written, and nothing populates `Order.customer_id`. Eight customer-side
party records exist (`customer.companies/contacts`, `lead.leads`, `finance.customers`, operations
order name/phone snapshot, `storefront.enquiries`, `notification.contacts`, support requester
strings, project/projects `client` free text) and two vendor-side ones; Engage built the second
contact book its own spec forbade. The customer in OpsGrids billing is a tenant organization, which
is a different thing from the CRM company and must never be merged with it.

**F6 — The Customer 360 does not work in production and is not in the CRM app.**
`services/customer/app/c1_360.py:167` calls `{deal_internal_url}/deals`, but `create_app` mounts
every router under `/api/v1` (`libs/platform-core/src/platform_core/app.py:363`), so the deal leg
404s and the CRM section degrades; its test asserts the same wrong path
(`services/customer/tests/test_c1_customer360.py:154`). It lives only inside Engage's inbox, keyed on
a contact id; `product-crm` never calls it. Its orders, finance and support sections are always null.

**F7 — The box has no memory room, so nothing new gets a container.** With
`infrastructure/edge/lib/memory_budget_totals.py` against the current budget: ten sellable profiles
cost 3747 MB (97.0% of the 3862 MB reference), ten plus `admin` 3944 MB (102.1%); the box's
`.env` holds `EDGE_BUDGET_ACCEPTED_MB=3944`, exactly today's total, so even +1 MB needs an owner
edit. A standalone `deal-worker` would add ~90 MB. The repo's pattern since `d7dfc54a` is to fold a
consumer into its API process (`RUN_CONSUMERS_IN_API`, `compose.prod.yml:80-94`); `customer` is the
template, `deal` and `lead` have no fold wiring. ADR-0017's 94.4% figure is stale.

**F8 — The product surface is thin where users look.** No lead search or delete, no sort anywhere
(every "sort" is page-local), no company address or GSTIN, no contact page, no tasks or reminders
(activities are a past-only log enforced server-side), no stage history or `closed_at`, no
configurable pipeline, an assignment rule with endpoints and no UI, four placeholder screens
(Reports, Integrations, Settings, Help), silent caps (200-row directory, 200-card board). The
good parts to leave alone: the selectors/services/routes layering, RLS discipline, the sealed-phone
and audited-reveal path (the best implementation in the repo), the append-only timeline trigger,
idempotent lead conversion, the internal service-token pattern, the 360's bounded fan-out.

**F9 — Removal needs care.** About 48 dead-or-duplicate items were found across backend and UI.
Skeptics showed several "dead" calls had live users and must be replaced before they are removed:
the in-client fixtures mode (17 tests, seeds mirror it), `Login.tsx` (the dev sign-in and the
`/login` target), the page-only CSV export (the only selection-scoped export), the `/deals/summary`
fields (contract tests, visual fixtures, ADR-0020's parity test; the server fields stay and only the
SPA's trend scaffolding goes: D-23), the finance sample data (live resolvers share the file). The
programme's removal rule is **replace, then delete** (SP1 §5.4).

**F10 — Packaging has no gating mechanism.** CRM is one entitlement (`crm`, "OpsGrids Revenue"):
INR 3,000/month or 30,000/year (ADR-0035) plus a public per-seat `crm/growth` plan at INR 299/seat.
Plan `features` are free-form JSON only `trial_days`, `bundled_products`, `bundle_tier` and
`partner_of` ever read; seat classes are catalogued and enforced nowhere. "Core vs advanced" can
only be permissions and role templates inside `crm`. A new permission reaches live roles through
ADR-0007's reconcile; rename has no mechanism, and removal is specified but its tool does not exist
— so the 17 permission codes and the CRM event types are frozen (they are customer data in roles and
notification rules).

**F11 — Release and operations constraints.** Actions billing is blocked, so `make gate` locally is
the only full verification. Merges do not deploy; deploys are manual and name a ref. The owner
merges (the permission gate stops agents merging their own PRs). Box `.env` edits, production SQL and
production reads are owner-only. Production data operations are therefore `tools/` commands with a
dry-run default, counts-only output, a per-org transaction on the NOBYPASSRLS app role, idempotent
and re-runnable — modelled on `tools/reconcile_membership_links.py` and `tools/reconcile_permissions.py`.

## 4. Target picture

### 4.1 Vocabulary (the glossary is a deliverable of SP1)

| Term | Meaning | Notes |
|---|---|---|
| **tenant** | an OpsGrids customer — an `account.Organization` | Never "customer" in new copy |
| **lead** | pre-qualification intake record; converts into company + contact + deal | After the cleanup the *label* `lead` names only this record; the stored value `lead` stays on company status, contact status and the first stage key, because events and tenant rules carry it (SP1 deviation 3) |
| **company** | the **party**: a business or a one-person buyer | Public noun; URL today is `/customers`, canonical becomes `/companies` (alias kept) |
| **contact** | a **person** at a company; may be phone-only | The cross-service key most products already use (`customer_id` in operations, `contact_id` in notification) |
| **deal** | an opportunity with a stage | `deal.stage` is the only pipeline vocabulary |
| **stage** | an immutable key plus editable label, order, tone, default probability and an **outcome** `open \| won \| lost` | Consumers test the outcome, never the key |
| **activity** | a logged interaction on a lead, contact, company or deal | Past tense; `last_activity_at` is derived from it |
| **follow-up** | a dated future task for a rep | New; deliberately not an activity |
| **owner / rep** | the member responsible for a record, identified by **membership id** | Roster (`customer.owners`) becomes a thin CRM view of members |
| **customer** | reserved for the relationship status of a company (stored values `lead`, `prospect`, `customer`, `churned`; `dormant` is a contact status; all frozen, registry B17) | Not a noun for the table |
| **billed party** | finance's invoicing profile of a company | Finance owns the billing facts |

### 4.2 Identity model

- **Party = `customer.companies.id`. Person = `customer.contacts.id`. Rep = `account` membership id.**
  The customer service is the system of record for identity facts (legal name, tax ids, addresses,
  status, owner); every other product keeps only *role* facts about the party.
- Other products reference the party by plain, nullable, indexed UUIDs named **`crm_company_id`** and
  **`crm_contact_id`** — never `customer_id` (already ambiguous). No cross-schema FKs. A display
  snapshot stays beside the link so the product still works when CRM is not entitled.
- Links are resolved by idempotent **ensure/resolve internal routes on customer** (`parties:ensure`, which
  only operations may call, and `parties:resolve`; the `leads:convert` claim/call/record pattern), never
  by copying records, never inside an open transaction.
- Individuals remain one-person companies (the convention `lead` already uses).

### 4.3 Process topology and memory

`customer`, `deal` and `lead` stay three processes of one product (ADR-0017). **No new container for
any CRM feature**: consumers fold into the API process via `RUN_CONSUMERS_IN_API` (+1 MB each, five in
all: §2), per-party projections live in the existing customer consumer. The insights container
(ADR-0020, +99 MB) and two profile moves (scheduler and approval to core) are priced separately in §2.
Whether `lead` folds into `customer` is an open decision (D-11); it is costed in SP1 and not assumed.

### 4.4 Events and contracts

Contract-first and additive within `v1`: every payload change and its committed schema land in one
commit and are tested through the real write path (`platform_core.events.validate_event_schema`).
Commercial events carry **ids only** (`crm_company_id`, `crm_contact_id`, `owner_membership_id`) —
never PII, because deal's timeline is append-only by trigger and all services share one PII key (no
crypto-shredding). New consumers are deployed before their producers' behaviour changes, because an
event with no bound queue is dropped.

### 4.5 Packaging and permissions

One `crm` subscription; modules ship as permissions inside the existing three catalogues, admin-first,
reaching live tenants through ADR-0007's reconcile. Role templates consolidate by same-code merging
across the three catalogues into **`CRM_ADMIN`, `CRM_REP`, `CRM_MANAGER`, `CRM_VIEWER`** (the six
legacy codes frozen, not deleted). Plans stay commercial-only. The 17 permission codes and the CRM
event types are frozen; the UI relabels instead.

## 5. Decomposition

```
SP1 foundations & cleanup ──► SP2 platform backbone ──┬──► SP3 core revamp (features + UI)
        │                                             └──► SP4 suite connection
        └──────── (contracts, glossary, removals) ───────────────┘   SP3 ∥ SP4 after SP2, by readiness (§10)
```

| | Sub-project | In scope | Depends on |
|---|---|---|---|
| **SP1** | Foundations and cleanup | Decisions ADR; glossary and one CRM doc; the confirmed prune list with replace-then-delete classes; the 360 path fix; event contracts and producer-side validation; subscribable lead keys; ops-monitor coverage; stale-doc rewrite | — |
| **SP2** | Platform backbone | Lead hardening; NEW-7 (platform-core membership subject); configurable stages (expand, then authoring API and permission); NEW-9 consumers (log-only first); C15 (roster re-key, owner-run mapping, flip); role consolidation and permission rollout | SP1's ADR (R1.1); R2.3 also needs R1.2a (§10) |
| **SP3** | Core revamp | Data-model fit (company tax identity, phone-first contacts, deal number, win/loss, `closed_at`); API cleanup (search/sort/filters, canonical nouns); shared UI foundations; IA, settings hub and every list/detail screen rebuilt; contact page with the 360; follow-ups and reminders; lead capture, dedupe, assignment; saved views, tags, history; reports | SP2's rep and stage model; R3.1 and R3.3 can start after SP1 (R3.1's enforcement half once R1.2a has soaked), R3.2 needs R2.2 and R2.3 (§10) |
| **SP4** | Suite connection | Party-link convention and resolve route; id-bearing events; consent and erasure design; messaging loop; money loop; service and work loop; quotes-lite and discount approval; analytics; remaining products | SP2 and named SP3 releases (R3.1, R3.2, R3.3, R3.4, R3.6, R3.7, R3.8: §10); consent/erasure design before any fan-out |

**Out of scope by decision** (the scope traps): two-way email or calendar sync, telephony, AI
scoring or drafting, a second workflow engine, full CPQ, custom objects, tiering CRM modules by plan,
a new container per feature, a CRM-local BI service, deleting the `eventbridge` scaffold (a roadmap
decision for outbound webhooks, not hygiene).

## 6. Whole-ecosystem data map

Eleven products plus the platform services. "Direction" is who emits and who folds. Every link is a
nullable plain UUID plus a display snapshot; every event carries ids only.

| Product / service | Holds today | Connection (what the programme builds) | Release |
|---|---|---|---|
| **Finance** | `finance.customers` (billed party), `crm_contact_id` bridge (unwired, contact-keyed) | Billing profile keyed on `crm_company_id` (GSTIN-checked, terms, credit limit; several GSTIN profiles per company); company tax identity in CRM; invoice/payment events → CRM per-party projection (receivables, lifetime billed; the state sits in `party_documents` and `party_payments` and the rollup is derived from them); payments captured → finance payment; credit-position read for order accept; collecting an invoice online is staff-assisted only, with no customer-facing pay page (D-30) | R4.3, R4.5-C |
| **Operations / inventory** | order name/phone snapshot, `customer_id` (a contact id), suppliers, `inventory_items` | Order carries `crm_company_id`/`crm_contact_id`; order lifecycle events enriched; order → CRM timeline and lifecycle (first order → customer); `inventory_items` is the one item master for deal lines and quotes | R4.3, R4.5 |
| **Storefront** | `enquiries`, `products` (second item master) | Enquiry → lead (consent evidence kept); when CRM is entitled the status derived from the linked lead/deal is a second field (`crm_status`) beside the merchant's own `status`, never over it; `products.inventory_item_id` | R4.5, R4.7 |
| **Engage / notification** | `notification.contacts` (second contact book), consent ledger, conversations | Engage contacts become a projection of CRM contacts for CRM-entitled orgs (standalone kept; the projection writes no consent); email target; consent writer and visibility; CRM-originated sends (permission `engage.inbox.message.reply`, D-37); WhatsApp conversation → PII-free activity; assignment notifications | R4.1, R4.2 |
| **Support** | tickets with requester strings | `crm_contact_id`/`crm_company_id` on tickets (snapshot as fallback); ticket section and timeline on the 360; customer-facing ticket notifications | R4.4 |
| **Projects / PulseBoard** | `client` free text; two overlapping products | `crm_company_id` on projects (target product per D-17); won deal → draft project (org opt-in); delivery card on company page | R4.4 |
| **Marketing** | hand-typed leads/conversions/revenue | `marketing_campaign_id` and UTM on lead and deal (not `source_ref`, which already means a request key in approval, attendance and finance); attribution projection replaces typed numbers (CRM-attributed plus a typed adjustment); Engage sends, Marketing plans and reads | R4.6 |
| **HRMS** | employees, reporting lines | Offboarding deactivates the rep and queues a reassignment task (never auto-reassign); an HR exit does not revoke a membership, so the trigger set is `core.membership.revoked` plus `hrms.people.employee.transitioned`; availability for round-robin; manager line → TEAM scope | R3.8, R4.7 |
| **ITSM** | internal staff requesters | **Deliberately not connected** for customer data (it is internal IT); staff identity only, via the membership spine | — |
| **Insights / dashboard** | unmerged `sprint/insights-q1..q3` (not on `main`) | CRM facts and funnel metrics (stage by `outcome`, no names stored); dashboard tiles; global search entities; cross-product facts keyed by company are a design only (v2, D-32) | R4.6 |
| **Approval engine** | employee-keyed requesters, HRMS-only inbox | Discount approval (request type `crm.pipeline.discount`) needs a requester abstraction (membership id) and a platform inbox UI — supersedes ADR-0006 Q1's narrow stance; approval moves from the `hrms` profile to core | R4.5 |
| **Scheduler** | namespace rule rejects `crm.*`; runs under the finance profile only | Follow-up reminders and stale-deal alerts; cancel-by-key; a producer-to-prefix map for the namespace rule; moves to the core profile | R3.6 |
| **Document / private store** | none for CRM | Quote and invoice PDFs and notes attachments wait for R19 (private objects), which is outside the programme and not on `main`; attachments also wait for D-39 | R4.5-B |
| **Account / auth** | memberships, `manager_membership_id` | Membership is the rep spine; bulk membership read; enriched membership events | R2.2–R2.7 (subject, bulk read); R3.8 (enriched events) |
| **Payment / billing / subscription / metering** | the platform's own tenant billing | **Not the CRM's customer.** No link; vocabulary fix only | — |

## 7. Cross-cutting rules (apply to every release)

- **CLAUDE.md hard rules:** `organization_id` on every table and every query; tenant GUC
  transaction-local; consumers need a per-message GUC or `BYPASSRLS`; no cross-schema FKs; enqueue
  only after commit via the outbox; money in integer minor units; append-only ledgers; PII sealed and
  masked by default, reveal audited; authorization server-side with a **negative-authz test per
  endpoint**; the `import app` ambiguity means tests and mypy loop per service.
- **Migrations are expand-only and rollback-tolerant.** One Alembic head per service; whichever of
  two stacks merges second re-parents, except that the insights migration `a1c9e2f7b3d4` is adopted
  unchanged and byte-pinned by R2.3, so every later deal revision chains after R2.3's. Contractive
  steps ship in a later release than the code that stops using the old shape.
- **Permissions:** catalogue → `make openapi` → deploy the enforcing service →
  `make edge-reconcile PRODUCTS=crm` (dry run first, then `APPLY=1`, off-peak, paced; from R2.5 `ORG=<uuid>`
  reaches one designated tenant before the fleet) → only then the UI. Every new route needs an explicit
  floor so a customer-granted OWN rung cannot widen it; the one exception is the bare `GET /deals/stages`,
  which is tenant configuration (SP2 §17 item 10).
- **Production data is owner-run** (§F11): dry-run output is reviewed by the owner before any apply.
- **Consent and erasure** are designed before any PII-adjacent fan-out (R4.1): consent stays in
  notification and CRM never decides it; CRM contact erasure anonymises in place and emits
  `crm.book.contact.erased`; deal activities stay PII-free.
- **Frontend:** shared components live in `@opsgrids/ui` / `@opsgrids/client` and are never forked
  into `product-crm`; the token baseline forbids raw values; every screen has loading, empty, error
  and forbidden states; SPA gating hides controls and protects nothing.
- **Verification per release:** per-service `pytest` + `mypy` loop, `make check`, `make gate` locally
  (Actions billing is blocked), `make openapi` and the contract tests, the migration round-trip, the
  visual and a11y suites for any UI release, and the owner's dry-run evidence for data steps.
- **Git:** no AI attribution in commit messages or PR bodies; short-lived branches, PR, squash merge
  by the owner.

## 8. Owner-decision register

Forty-one decisions, each with a recommended default so review can be "approve or amend". "Blocks" names the
first release that cannot start without it. Every row is open until the owner answers; the defaults are what
the specs and the registry (§K) build on, and no plan task that depends on an open row starts. D-1 to D-24
are the original rows, amended where a spec found the first text wrong; D-25 and above were raised later.
The first run numbered those independently and reused ids, so this table is the one numbering: a D-id above
24 in an older text means the row here. The registry's open questions L1, L9, L13, L18, L19, L20, L21 and
L22 (SP3's) are now D-35, D-36, D-37, D-38, D-39, D-25, D-40 and D-41; L23 is folded into D-27. The detail of
each decision lives in one spec: SP1 for D-11; SP2 for D-1 to D-10, D-12, D-13, D-20, D-21, D-24 to D-26 and
D-38; SP3 for D-14, D-22, D-23 and D-41; SP4 for D-15 to D-19, D-27 to D-37, D-39 and D-40.

| ID | Decision | Recommendation | Blocks |
|---|---|---|---|
| D-1 | NEW-9: what happens to the orphan **deal** | Soft-delete it with an audit row naming the consumer; emit no `stage_changed` event (closing it as lost would distort win rate and fire tenant automations) | R2.4 |
| D-2 | NEW-9: how far customer cleanup may go | Only records provably created by the aborted attempt (id equals the id re-derived from lead id and generation, and the entity's own creation audit row has `acting_as='lead'` and is its only audit row), with no other contacts and no live deal; never a reused record. `CRM_CONVERSION_CLEANUP_MODE` ships as `log_only`; the owner flips it to `on` after reading the log lines | R2.4 |
| D-3 | Orphans from past aborts already exist? | The owner runs a read-only count of `lead.conversion_aborted` audit rows (`lead.audit_logs`) before R2.4, as hand-run SQL; the historical sweep (registry H3) is built only if the count is non-zero | R2.4 |
| D-4 | Lead bumps `conversion_generation` on every abort and announces the deleted-record bump | Yes to both: bump on every abort, carry the generation in the payload and schema, and emit an event for the deleted-record bump (registry EV13, reason `record_deleted`) | R2.1 |
| D-5 | Stage model | One pipeline per org (tables shaped so more can be added); immutable stage key; editable label, order, tone and probability; outcome `open\|won\|lost`; at least one won and one lost (several allowed); archive, never delete; closed deals immutable (the no-move rule; reopening is a separate decision) | R2.3 |
| D-6 | Who edits stages | New permission `crm.pipeline.stage.write` with a route floor of ORG; reading stays under `crm.pipeline.deal.read`; no new admin template | R2.5 |
| D-7 | What a rep is after C15 | `customer.owners` gains a nullable `membership_id` column (a column, not a primary-key rekey); the Team screen lists organization members and activates them as reps; free-form rep creation retires after cutover (R2.7 keeps the first-run self-claim, R2.9 removes the button) | R2.6 |
| D-8 | Owner-id mapping rule | A human-reviewed plan: employee-link evidence for employee-valued rows; email match from roster to member only as a proposal the owner accepts; unmapped rows untouched and listed (visible at ORG, invisible at OWN); never guessed; owner-run dry run per organization with a reversible journal. Historical `deal.activities.owner_id` stays untouched (append-only; registry L7) | R2.6 |
| D-9 | Sign-off for the access widening C15 causes | Approve (reps gain OWN write, convert and reveal on their own rows), with a signed security review of every OWN route (SP2 checklist S-1 to S-12) before the R2.7 flip | R2.7 |
| D-10 | Production CRM census | The owner runs a read-only count: organizations with CRM data, roster sizes, employee links and the `lead.outbox` backlog | R2.6 |
| D-11 | Fold `lead` into `customer`? | Keep three processes for this programme; ADR-0038 costs the fold (about 98 MB freed, supersedes ADR-0017 decision 1, needs a cross-schema data move) and it is revisited after R2 | R2.4 (only if D-13 holds the ceiling; R1.1 merges with D-11 open, per SP1 deviation 6, which overrides the first draft's R1.1) |
| D-12 | Insights vs stages landing order (event contract) | Stages land first; the insights migration `a1c9e2f7b3d4` is adopted unchanged (byte-pinned); insights rebases onto `outcome`; the event contract (`outcome`, frozen stage keys, `closed_at`) is recorded in ADR-0038. Insights' `deal_facts` stage CHECK and `closed_won`/`closed_lost` predicates must move to `outcome` before custom stages are enabled in any org with insights (registry L10) | R2.3 |
| D-13 | Memory ceiling (amended by registry L17) | The first draft's "no increase" assumption is already broken by the first consumer fold (+1 MB each). The owner raises `EDGE_BUDGET_ACCEPTED_MB` by one per fold, release by release, in the box `.env` before each deploy (3945 at R2.4 deal, 3946 R3.8 lead, 3947 R4.4 support, 3948 R4.6 marketing, 3949 R4.7 storefront; ten profiles plus `admin`), or frees memory (D-11). Insights (+99 MB) and the PDF library (+10 to +20 MB each in deal and finance, not measured) ship only after a separate owner answer on the ceiling (§2) | R2.4 (the first draft said R4.6; SP1 deviation 1 and SP2 deviation 5 move it to the first fold's deploy) |
| D-14 | Party identity | Company is the party, contact the person, phone-only contacts allowed (`contacts.email` nullable) | R3.1 (also gates R4.1) |
| D-15 | Engage contact book | A projection of CRM contacts for CRM-entitled organizations; standalone Engage mode kept; the projection writes no consent and never opts anyone in | R4.2 |
| D-16 | Marketing vs Engage | Engage owns sending and delivery analytics; Marketing owns planning and attribution and reads Engage | R4.6 |
| D-17 | Target for the client link and won-deal kickoff: `project` (PulseBoard) or `projects` | Both products get the same link columns and event fields (one shared contract); the kickoff is built in PulseBoard (`project`) first because ADR-0000 section 11 already says the PulseBoard client id becomes the CRM customer id; `projects` adopts the same route later. The owner picks | R4.4 (kickoff only) |
| D-18 | Discount approvals | The approval engine is the one mechanism (no third local state machine), with a membership requester abstraction and a platform inbox UI; supersedes ADR-0006 Q1's narrow stance through ADR `approvals-for-tenants-without-hrms` (expected 0042, filed proposed, accepted before the approval deploy). Approval moves from the `hrms` profile to core (a 99 MB container on boxes with `crm` and no `hrms`, SP4 §10.8) | R4.5 |
| D-19 | Quote home and item master | Quotes live in `deal`'s own schema (never the `quotation` scaffold, which is deleted with the quote work); `inventory_items` is the one item master; `storefront.products` gains `inventory_item_id`; recorded in ADR `crm-quotes-in-deal` (expected 0043) | R4.5 |
| D-20 | Role consolidation | Four templates `CRM_ADMIN`, `CRM_REP`, `CRM_MANAGER`, `CRM_VIEWER`; the six legacy role codes and all 17 permission codes frozen; the four later codes (E19-E22) are catalogued admin-first in R2.8 | R2.8 |
| D-21 | Plan packaging | Stay flat (one `crm` entitlement, no per-module tiering); fix or retire the INR 299 per-seat `crm/growth` plan by a staff action in the admin console (ADR-0035's open question); no code in this programme | R2.8 |
| D-22 | API nouns | `/companies` canonical (`/customers` aliased), the 360 under `/contacts/{id}/360`, `/activities` aliased | R3.2 |
| D-23 | Removals that had live users | Replace, then delete: fixtures mode, `Login.tsx`, the CSV export and the per-card select. The `/deals/summary` server fields are **not** deleted (registry C9 freezes all nine; the five unrendered ones get a renderer in R3.4 and only the SPA's trend scaffolding goes): SP1 deviation 2 and SP3 §8.6.5 correct this document's first list | R3.4 |
| D-24 | TEAM scope for managers | Include, sourced from `manager_membership_id` through a per-service `owner_team` projection; for a membership subject TEAM also admits the caller's own rows (registry J10, L6); wired in R3.8; the widening is a separate owner-run reconcile after a soak and cannot be narrowed by any tool (SP3 K3) | R2.2 (membership-TEAM semantics tasks only; they flip as a unit on a no); R3.8 for wiring and the widening |
| D-25 | May managers reveal the sealed phone or email of their whole team (raised by registry L20) | No: `CRM_MANAGER` holds `crm.book.contact.read_sensitive` and `crm.lead.lead.read_sensitive` at OWN, the rep-equivalent. On a yes those cells become TEAM through the R3.8 widening and D-9's security review covers them | R2.8 |
| D-26 | Seal `contacts.email` at rest (raised by SP2; registry B56) | No: R2.9 Part C is built only on an explicit yes and when its gates hold (R3.1 shipped with nullable email and the `email_index` backfill run; the plaintext readers C45, C47 and C53 sealed-aware or consciously left on plaintext; reveal route negative-authz set green). A yes reopens R4.1 and R4.2 | R2.9 Part C only (optional release) |
| D-27 | What each store keeps after an erase, including what a lead erase keeps (raised by SP4, shared with SP3; absorbs SP3 question L23) | SP4 §6.5 coverage table: identifiers are overwritten wherever a store keys a row by the contact id; legal documents (invoices), consent ledger rows, suppressions, free-text bodies and append-only deal text are retained and listed as residue; the owner and legal confirm each exception before ADR `crm-consent-and-erasure` (expected 0039) is accepted. Leads: an administrator erases one lead at a time, in place, on E22 behind a step-up (`POST /leads/{lead_id}:erase`), clearing name, company, email, phone, notes, visitor messages, GSTIN and duplicate pointers and rewriting audit copies, and keeping source, status, owner, attribution, consent evidence and converted ids; no bulk erase and no dedicated `crm.lead.lead.erase` code (the alternatives L23 puts to the owner); recorded in ADR `crm-public-lead-capture` (expected 0041) | R3.7 (R3.7b does not ship without the lead answer); R4.1 (ADR acceptance, contacts); R4.3. Whichever of R3.7b and R4.1 is scheduled first |
| D-28 | Accounting for an online refund of a captured invoice or order payment | Record only: finance stores the refund fact on `online_captures`, writes an audit row and logs it; it creates no credit note and no negative allocation; a person issues the credit note | R4.3 |
| D-29 | Fulfilment billing settings surface (R4.3-S) | R4.3 includes a minimal slice: `GET` and `PUT /operations/fulfilment/settings` for the organization-default row (`invoice_timing`, `default_tax_rate_pct`, `create_crm_party_on_order`, `order_invoice_chain_enabled` default false), one new permission (E27), one card on the existing payment-settings screen, used by the organization's own administrator, never the platform owner. Alternative: ship the chain dark and set rows by owner SQL | R4.3 (chain enablement only) |
| D-30 | Customer-facing pay page (payment link) for invoices | Defer: no public payment surface exists and the provider seam has hosted-checkout orders only; R4.5-C ships staff-assisted collection (finance staff open the hosted checkout) | none (nothing waits on it; the default defers) |
| D-31 | Marketing opt-in captured on a public form (storefront buyer form and lead capture forms) | Single opt-in recorded with evidence: the ledger row names the form text and the enquiry, an unticked box writes nothing, every marketing message carries the unsubscribe link; confirmed (double) opt-in is deferred | R4.7 (storefront consent only) |
| D-32 | Insights v2: company-keyed cross-product facts | Do not build until a dashboard needs a company-ranked board that the R4.3 party projection cannot serve; keep the design (`docs/insights/v2-company-facts.md`) so the decision costs a document, not a producer change | none (design only) |
| D-33 | One stated rule for STOP and unsubscribe (first drafted as D-26 in SP4) | Confirm the code's rule: STOP and the unsubscribe link both opt out of marketing only; a transactional opt-out is a separate explicit act (an admin, or the person's own request). R4.1 corrects the two stale texts (`consent.py` docstring, ADR-0021) and `docs/whatsapp/architecture.md` in the ADR; no behaviour changes | R4.1 (ADR acceptance) |
| D-34 | Lifecycle automation: may CRM promote a company to customer on a commercial event? | A per-organization setting `customer.customer_settings.lifecycle_promotion`, default off. `first_order` promotes on the first accepted order only (the one trigger §6 names); `first_commercial_event` also promotes on the first won deal and first paid invoice (the owner's explicit extension). No organization changes until its administrator turns it on; backfills and replays never promote | R4.3 (the setting; no promotion happens while it is off) |
| D-35 | Who owns a party created by `parties:ensure` (registry L1) | The caller's `owner_membership_id` when given and activatable, else the organization's single active `is_owner` membership (activated as a rep with an audit row), else 422 `owner_required` | R4.1 |
| D-36 | First-touch or last-touch campaign attribution (registry L9) | First touch, write-once: a lead's campaign id is set at capture and the deal copies it at conversion; neither changes afterwards (409 `attribution_locked`); campaign figures are CRM-attributed plus the typed adjustment, money added only in the campaign's own currency; the schema carries one `marketing_campaign_id` and one UTM set per lead and deal either way | R4.6 (R4.6-A attribution) |
| D-37 | Permission for CRM-originated sends (registry L13) | `engage.inbox.message.reply`, so the rep also needs an Engage role (a CRM role template cannot hold a cross-product code); no new CRM code | R4.2 |
| D-38 | Should a rep read only their own records? (registry L18, J19) | No: reps read at ORG and write at OWN, which is today's behaviour; success criterion 1 (§1) is worded to match. Narrowing reads would hide teammates' records from every rep's lists, boards and 360 and is a larger change than D-9 approves | R2.8 |
| D-39 | Notes attachments and erasure (registry L19) | Until the consent and erasure ADR chooses, R4.5 ships the PDF rows but not attachments (B57, C81-C83); deal binds `crm.book.contact.erased` unconditionally and has document delete the private objects of the erased contact's activities (the attachment row stays as a fact); the ADR also decides whether `original_name` is stored at all | R4.5 (attachments only, R4.5-B) |
| D-40 | Who may hand a quote off to finance, operations or projects (registry L21) | No new permission code: the ORG floor on `crm.pipeline.quote.write` for order and invoice targets plus the callee's entitlement re-check. Alternative: a dedicated dangerous `crm.pipeline.quote.handoff` (default OWN, max ORG) | R4.5 |
| D-41 | May `PATCH` still edit a closed deal's value, currency, probability and close date? (SP3 question L22) | Yes, no server change in this programme: D-5's "closed deals immutable" is the no-move rule; the deal page renders closed deals read-only; bulk update and import keep working. Win and loss figures can change retroactively if someone edits a closed deal through the API | R3.5 |

### ADR numbers the programme allocates

Numbers are allocated in order and never reused (`docs/decisions.md`). `docs/adr` ends at 0037, the next free
number is 0038, and 0040 (`faster-ci`) is already taken on another ref. The slug is binding and the number is
the expected one: R1.1 reserves 0039 and 0041 to 0043 by a note in `docs/decisions.md` (SP1 DD-1), and each
release confirms its number against that file when it files the ADR. If a parallel branch lands above a
number first, the number moves and the slug does not.

| ADR | Slug | Filed by | Records | Status |
|---|---|---|---|---|
| 0038 | `crm-programme-decisions` | R1.1 | M1 to M4 and D-1 to D-41 (§8) as the owner answers them; D-11's costing; D-12's event contract; the correction to ADR-0008 Q5. R1.1 also adds dated amendment notes to ADR-0008, ADR-0017 and ADR-0020 (no new numbers) | proposed; each decision's state cell is edited as it is answered |
| 0039 | `crm-consent-and-erasure` | R4.1 | Consent stays in notification; erase in place; the per-store coverage table and its exceptions (D-27); the STOP and unsubscribe rule (D-33); draft in SP4 §6.5 | filed proposed; accepted before R4.1 is called done |
| 0041 | `crm-public-lead-capture` | R3.7b | Form token and webhook header shapes, replay rule, entitlement check, the capture secret, what the per-lead erase clears and keeps (D-27 for leads) | accepted before the R3.7b deploy |
| 0042 | `approvals-for-tenants-without-hrms` | R4.5 | D-18; supersedes ADR-0006 Q1's narrow stance; draft in SP4 §10.4 | filed proposed; accepted before the approval deploy |
| 0043 | `crm-quotes-in-deal` | R4.5 | D-19; deleting the `quotation` scaffold | filed with R4.5 |

## 9. Top risks

| Risk | Mitigation |
|---|---|
| A wrong owner mapping hands a rep another rep's pipeline and OWN-scope audited reveal of sealed contact data | Owner-run dry-run, human review per org, unmapped rows untouched, data-before-code ordering, a stored reversible mapping, security review of every OWN route (D-8/D-9) |
| NEW-9 destroys real data by trusting an event that can name reused records | Lead hardening first (R2.1); provenance from customer's own audit; log-only first deploy; soft-delete for deals |
| NEW-7 silently widens or empties scope for a product | Subject declared at registration with the employee link as default, so every other product is byte-for-byte unchanged; static test banning raw `subject_ref` in membership-subject services; exhaustive negative tests |
| Event-contract drift breaks tenant automations and the unmerged insights consumer | Stage keys never change meaning; additive `outcome`; contracts and producers change in one commit; insights rebased on the decided contract |
| The "no memory increase" assumption behind D-13 is broken (§2): five consumer folds (+1 MB each), insights (+99 MB), an unmeasured PDF library and two profile moves on a box already at 102% of RAM with `admin` | D-13 answered before R2.4; the owner raises `EDGE_BUDGET_ACCEPTED_MB` release by release to the total `make edge-preflight` prints, or frees memory (D-11, the registry L17 alternatives); every release prices itself with `memory_budget_totals.py`; insights and the PDF library ship only after a separate owner answer; the preflight is the stop condition |
| Cross-product fan-out leaks PII or defeats erasure | Ids-only events; consent stays in notification; erasure design precedes R4.2; deal activities PII-free |
| Two Alembic heads in deal (stages vs insights) | Land order decided in D-12: R2.3 adopts `a1c9e2f7b3d4` unchanged and byte-pinned, every later deal revision chains after it, and the later stack re-parents with a `RE-PARENT HERE` marker |
| Scope creep from "connect everything" | The §6 map is the contract; a connection not in it is a new decision; the scope-trap list is binding |
| Three one-way points (SP3 K1 to K3) and six exceptions to M2 (§2): a phone-only contact, a captured lead and the TEAM widening cannot be undone; an erase, an issued tax invoice, a pending quote, approval's profile move, discarded events and an edited insights migration limit a rollback | Stated per release with the rule that replaces the guarantee; the owner acknowledges K1 to K3 (SP3 step O1) and accepts or refuses the six by approving SP4; the TEAM widening is a separate owner act after a soak, with the evidence written down first |
| R19 (the private object store) is outside the programme and not on `main`: quote and invoice PDFs and notes attachments cannot ship, and if R19 lands in the unmerged branch's shape (public routes, employee-keyed permission) rather than ADR-0009 Q1's, authorization is wrong for CRM-only tenants | R4.5-B is its own stage and waits; before it starts the owner confirms which R19 landed (SP4 §13 step 8) |
| The unmerged insights stack (four branches, three Alembic heads): a failed deploy, a wrong figure, or an in-place edit of a migration that already ran somewhere | Fixed merge order and one head; the owner's read-only database check before any in-place edit, else a forward revision (SP4 §13 step 10); insights consumes `outcome` and stores no names (D-12) |
| Legal and data-subject answers are not engineering's to give (D-27, D-31, D-33): CRM stores a stranger's details (R3.7b) or erases less than the owner believes (R4.1) | R3.7b and R4.1 do not ship before their ADRs are accepted and D-27 is answered; the erase residue is listed in the ADR; no bulk lead erase |
| Owner capacity: about 60 owner-run steps (SP1 §6, SP2 §13, SP3 §13, SP4 §13), among them every merge, deploy, box `.env` edit, production read and data tool; a skipped read-only check removes a stop rule | Each step carries its command, expected result and stop rule; releases are independent, so the order follows the owner's time; an agent never runs one |
| Findings recorded for the owner and not fixed (registry L3, L8, L12): notification's `ACCOUNT_INTERNAL_URL` has no compose line; Engage's own scheduled sends may fail the scheduler's namespace rule; product-service's internal entitlement route has no caller allow-list and public lead capture will call it | Listed so the owner decides; none blocks a release; hardening that route would touch auth's sign-in path and is outside this programme |
| The documents disagree after parallel authoring: a plan built on a stale id, name or prerequisite | Precedence in §0; registry §J lists every resolution; D-ids are those of §8; the owner review guide names the sections to read |

## 10. Release train

Independent releases; each is reversible within the limits of §2, owner-merged and manually deployed.
"Needs" lists the hard prerequisites as the specs record them (SP2 §0.1 and §17, SP3 §4.2 and §17, SP4 §17
items 1 and 2 and each release's needs paragraph; registry J18 and J30), which are wider than this document's
first draft listed, then the owner decisions of §8 that block the release. SP3 and SP4 releases interleave
by readiness, not by number.

| Release | Content | Needs |
|---|---|---|
| R1.1 | Decisions ADR 0038, glossary and CRM doc, stale-doc rewrite, register updates; documents plus three service READMEs, merged and not deployed | — (D-11 stays open: SP1 deviation 6) |
| R1.2a | 360 path fix with a real-path test, the guard tests for its class, the `org` claim on notification's service tokens, a production-smoke step | R1.1 |
| R1.2b | Confirmed removals, subscribable lead keys, ops-monitor coverage of the lead relay, missing event contracts with producer-side tests, Engage recipe repair | R1.2a deployed and verified |
| R2.1 | Lead hardening: generation bump on every abort, generation and claim in the payload and schema, an event for the deleted-record bump | R1.1; D-4 |
| R2.2 | NEW-7: platform-core membership subject, default unchanged, plus an `own_ref` accessor; rebuilds every image | R1.1; D-24 (membership-TEAM semantics tasks only) |
| R2.3 | Stages expand: stage tables, `closed_at`, `outcome` on events, read API; the one behaviour change is a 422 for a closing move off the stage's pinned probability | R1.1, R1.2a (the 360 fix it rewrites); D-5, D-12 |
| R2.4 | NEW-9: deal consumer folded into the API and customer consumer, log-only, then enabled by the owner; rebuilds every image; the first memory increase | R2.1; D-1, D-2, D-3 (the count), D-13 (ceiling raised first), D-11 only if D-13 holds the ceiling |
| R2.5 | Stages authoring API and `crm.pipeline.stage.write`; a hardened SPA published first; reconcile on one tenant, then the fleet | R2.3; D-6 |
| R2.6 | C15 expand: `owners.membership_id`, account bulk read, owner-run mapping tool `tools/map_crm_owners.py`, owner display in the SPA | R2.2, R2.3 (deal's migration chain); D-7, D-8, D-10 |
| R2.7 | C15 flip: CRM scope on the membership, SPA subject, data applied first | R2.6 with the mapping applied; D-9 (signed review of every OWN route) |
| R2.8 | Role consolidation into four templates, the four later permission codes catalogued, reconcile; four images (`account` included) | R2.5, R2.7; D-20, D-21, D-25, D-38 |
| R2.9 | Optional: retire the one-click owner claim and the naming residue (the write-side fallbacks already retire in R2.7); optional sealing of `contacts.email` (Part C) | R2.7 soaked (Part A also R3.4); D-26 for Part C |
| R3.1 (two deploys) | Data-model fit, expand only: company tax ids and addresses, phone-only contacts, deal number, win/loss reasons, lead campaign fields; (a) schema and tolerant readers, (b) phone-only creation | R1.2a soaked (for the enforcement half), R2.3 (the deal side); D-14 |
| R3.2 | API cleanup: search, sort, filters, `/companies`, generated types; rebuilds every image | R1.2a, R2.2, R2.3, R3.1; D-22 |
| R3.3 | Shared UI foundations, no CRM behaviour change | R1.1 |
| R3.4 | CRM shell, information architecture, settings hub, list rebuilds and the first deletions | R2.5, R2.7, R2.8, R3.2, R3.3; D-23 |
| R3.5 | Contact, company and deal pages with the customer 360, close reasons, deal history | R2.3, R3.1, R3.2, R3.4; D-41 |
| R3.6 | Follow-ups and reminders, stale and overdue alerts, scheduler cancel and its move to the core profile, rebuilt Overview | R2.4, R2.7, R2.8, R3.4 |
| R3.7 (two deploys) | Lead capture, duplicate detection, SLA, assignment, lead page, an administrator's per-lead erase, the Sources page; R3.7a everything that creates no new kind of lead, plus the erase, R3.7b public capture | R2.8, R3.1 with the owner's email-index backfill, R3.4; R3.7b also its ADR accepted, the capture secret set on the box and D-27 answered for leads |
| R3.8 (two deploys plus an owner tool) | Saved views, tags, history, reports, TEAM scope and lead's consumer fold; the TEAM widening is a separate owner act | R2.4, R2.6, R2.7, R2.8, R3.6, R3.7a; D-13 (+1 MB), D-24, D-25 |
| R4.1 | Party identity and consent/erasure foundation: party routes, ids on every CRM event, erase, consent writer, ADR 0039 | R2.3, R2.7, **R3.1** with C77's soak and the email-index backfill for every organization; D-14, D-27, D-33, D-35 |
| R4.2 | Messaging loop: Engage contact book as a projection, consent standing, CRM-originated sends, assignment alerts | R4.1, R2.4, R2.7, R3.4, R3.6, R3.7; D-15, D-37 |
| R4.3 | Money loop: billing profile per company, orders wired to customer and finance, party projection, online capture to payment, lifecycle setting; includes R4.3-S, the fulfilment settings slice | R4.1, R2.7, R3.1, R3.2, R3.3; D-14, D-22, D-27, D-28, D-29, D-34 |
| R4.4 | Service and work loop: tickets and projects link to the company; a won deal drafts a project; an Engage automation task | R4.1, R4.3, R2.4, R3.2, R3.3, R3.6; D-13 (+1 MB), D-17 |
| R4.5 (three stages) | Quotes-lite, discount approval through the approval engine, handoffs, item-master link: A quotes and approval, B PDFs (and attachments), C staff-assisted invoice collection | R4.1, R4.3, R4.4, R2.7, R3.1, R3.4, R3.6; B waits for R19, outside the programme; D-18, D-19, D-40 (D-39 for attachments); ADR 0042 accepted before the approval deploy |
| R4.6 (five stages) | Analytics and attribution: A attribution, B insights adoption, C dashboard tiles, D global search, E the v2 design | R2.3, R2.7, R3.1, R3.3, R3.7, R4.1 (R4.5 for quote facts only); D-12, D-13, D-16, D-36; B waits for the owner's answer on its +99 MB |
| R4.7 (four parts) | Remaining products and coverage audit: A HRMS hooks, B storefront, C orphan check, D records and audit | R3.7, R3.8, R4.1, R4.4, R4.5; D-13 (+1 MB), D-27, D-31 |

**Deploy shape.** One deploy per release unless the row says otherwise. R1.2 is two (R1.2a before R1.2b: the
box's only rollback is the whole ref, so the one defect fix is kept apart from the hygiene); R3.1 is two (the
schema and tolerant readers soak, then phone-only creation); R3.7 is two (R3.7a, then public capture in
R3.7b); R3.8 is two deploys plus an owner-run tool, then the widening. R4.5, R4.6 and R4.7's HR half ship in
stages that deploy and roll back on their own. R2.2, R2.4 and R3.2 rebuild every image. R2.5 publishes its
SPA first; R2.6 deploys four images, then its SPA; R2.7 deploys three images and publishes its SPA last.

## 11. Definition of done for the design pass

The owner can approve or amend the programme in one review. **The review is the owner review guide plus
the sections it points to, not this spec, the registry, the four specs and the four plans read end to
end**: those are the build contract, read by whoever builds a release. Each sub-project spec states scope,
contracts, data model, API, UI, tests, rollout and rollback; the registry fixes every name that crosses a
sub-project boundary; each plan breaks its releases into tasks with exact files, interfaces, tests and
commands (step-level or as cards, §0). By approving, the owner accepts the six exceptions to M2 and the
three one-way points (§2) and takes on the memory steps. Open decisions in §8 stay open until the owner
answers them; no plan task that depends on one proceeds before it is answered.

## Appendix — evidence index (local to the build machine)

- Register-debt map: workflow run `wf_9a018e15-345` (7 mappers, 7 skeptics).
- Overhaul audit: workflow run `wf_4cf65cd6-720` (6 auditors, 6 skeptics).
- Re-verified by hand while writing this document: F6 (`c1_360.py:167` calls `/deals`;
  `app.py:363` mounts routers under `/api/v1`). Every other fact is a mapper finding that an
  independent skeptic confirmed; the sub-project specs re-verify the ones they depend on. The next
  free ADR number (0038) was re-checked on 2026-10-10 against `docs/decisions.md` and `git log --all`
  (`docs/adr` ends at 0037; `0040-faster-ci` exists on another ref) — confirm at merge.
- Reconcile of 2026-10-10: registry §J (J25 to J33) and §K, and the deviations sections of the four specs.
