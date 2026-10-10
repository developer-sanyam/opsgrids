# CRM overhaul — sub-project 4: suite connection — design

Status: **proposed, design only — pending owner review.** No code, test, migration, configuration or
ADR is changed by this document. Sub-project 4 (SP4) of the CRM overhaul programme
(`2026-10-07-crm-overhaul-programme-design.md`, cited as P§n; its owner decisions as D-n, its facts as
P§3 F1–F11), bound by the interface registry (`2026-10-07-crm-overhaul-interface-registry.md`, cited by
row handle: `B15`, `C51`, `EV33`, `W27`, `H2`). Base: `main` @ `caa7f112`; every path:line below was
re-read at working-tree `HEAD` `3bc7f6ca`. Alembic heads at `3bc7f6ca` (each is the single head of its
service): customer `e230c025337e`, deal `e94d7a2c5f18`, lead `91dd3ca663fd`, finance `faf3ff81d039`,
notification `f1c8a2d5b7e4`, operations `d7a3c9e5b184`, support `d4e5f6a7b8c9`, project `c92e07b13fd5`,
projects `c7b52e0a9f43`, storefront `ea0b8b84b84d`, marketing `96e4780b8df1`, approval `435fc681c9e5`,
leave `e5b7c9d1a2f4`. The companion plan is `plans/2026-10-07-crm-4-suite-connection.md`.

**How to read this document.** *Fact* means a skeptic-confirmed discovery finding that was re-read in
code for this document. *Inference* is labelled. `(pending D-n)` marks a default that moves only when
the owner answers programme decision D-n. Where the programme and the registry were silent and this
document had to choose, the choice is in §2.2 with the alternative it rejected. What this document
added to the registry (new rows, amended rows, new decision ids) has been folded into it, row by row
(registry §J.2), so every handle written here is the registry's **final** one: that
includes the handles SP3 first proposed under an `SP3-` prefix (for example B86 for the lead audit scrub and
EV54 for the system in-app rules) and the ones this document moved to leave SP3 its numbers (B79 to B82, C104,
G24, G25, H14). A decision id above D-24 is the consolidated register's: D-26 is SP2's, so this document's
STOP and unsubscribe rule is D-33, and D-35 to D-40 are the registry questions promoted in §2.1.
Paths are relative to the repo root; a path that starts with a service name is under `services/`.
**A rule that holds for every table in this document and is not repeated per table:** it carries
`organization_id` (as a column or the leading key column), is `ENABLE`d and `FORCE`d under the
`tenant_isolation` policy on its schema's `current_org()`, and every selector and service query on it filters
on `organization_id` as well (RLS is the net, never the permission to skip the filter); no table holds a
foreign key into another schema; money is an integer in minor units beside an ISO currency; and a migration is
expand-only, takes `SET LOCAL lock_timeout = '5s'`, and refuses a downgrade that would destroy data unless
`OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`.
Effort uses the ADR-0020 scale: S ≤ 1 day, M 2–3 days, L 4–6 days, XL > 1 week, one engineer, tests
included. Event envelopes are cited with the key the code uses, `organization_id`
(`libs/platform-core/src/platform_core/events.py:173-189`); ADR-0000 §7 spells it `org_id`, which is the
pre-implementation name (registry §D intro and J33 agree: the wire carries `organization_id`).

## 1. Goal and non-goals

### 1.1 Goal

P§1 criterion 5 and decision M3 say one thing: every OpsGrids product that holds a customer, a contact
or a company must be linked to the CRM party, and the CRM must be able to show what every other product
knows about that party. SP4 turns that into six properties a reviewer can check mechanically:

1. **One link convention.** Every customer-side party record in every product carries
   `crm_company_id` and `crm_contact_id` as plain, nullable, indexed UUIDs beside its display snapshot
   (P§4.2). The link is resolved through idempotent internal routes on `customer`, never by copying a
   record, and the product keeps working with the link `NULL`.
2. **The CRM shows the connected picture.** The company and contact pages show money (invoices,
   payments, orders), service (tickets), delivery (projects) and messaging (consent, conversations),
   each section failure-isolated and gated by the scope rule of §4.4.
3. **Events carry ids only.** Every enriched commercial event carries `crm_company_id`,
   `crm_contact_id` and `owner_membership_id` and never a name, address, email, phone or free text
   (registry §D rules); each change lands with its committed schema and a producer-side test.
4. **Erasure and consent are designed before any fan-out** and enforced by the release that creates
   each link: consent stays in `notification`, CRM never decides it, a contact erase anonymises in
   place and emits `crm.book.contact.erased`.
5. **The platform engines are reused, not copied.** Scheduler (quote expiry), approval (discount),
   the in-app feed (assignment alerts), the outbox and the private store are used as they are
   extended; no third approval state machine, no second contact book, no new container beyond ADR-0020's `insights`.
6. **The box stays inside its budget without a silent overrun.** SP4 builds **no container of its own** beyond
   the one ADR-0020 already decided: each consumer fold is +1 MB and is priced in the rollout of its release
   (§9.8, §11.9, §12.10); that container, `insights`, is one `planned.insights-api` already prices, and it
   ships only after the owner answers D-13 (§11.9). Two other changes move memory without a new container:
   the approval engine moves to the core profile, so a box with `crm` and no `hrms` starts one existing 99 MB
   container (§10.8, §17 item 12), and the PDF library in `deal` and `finance` is priced in its own kind
   once measured (§10.4). Anything that needs an owner decision says so.

### 1.2 Non-goals

Everything in P§5's "out of scope by decision" list stands: two-way email or calendar sync, telephony,
AI scoring or drafting, a second workflow engine, full CPQ (price books, tiered or volume discounts,
multi-currency tax, e-sign), custom objects, tiering CRM modules by plan, a new container per feature,
a CRM-local BI service, deleting the `eventbridge` scaffold. SP4 adds these specific non-goals:

- **The OpsGrids tenant is not a CRM party.** Platform billing, subscription, metering, catalog and
  the platform side of payment have no link to CRM companies (§5.4).
- **ITSM customer data does not exist and is not invented.** ITSM requesters are staff (§5.4).
- **No customer-facing pay page.** Online collection on invoices is staff-assisted in R4.5; a public
  payment link is decision D-30 (pending D-30).
- **No automatic accounting for online refunds, no automatic credit notes** (pending D-28).
- **No vendor party in the CRM.** `operations.suppliers` and purchase documents stay a separate vendor
  master; R4.7 records the decision (§12).
- **No time-and-materials invoicing** from timesheets, **no inbound email or calendar connector**,
  **no bulk "merge duplicates" tool** (out of scope in SP3 §15; the programme names no merge).
- **No change to ADR-0021's decision function.** `consent.decide` is untouched; SP4 adds a writer
  route and states one rule for STOP and unsubscribe (D-33), nothing else.

## 2. Decisions

### 2.1 Owner decisions this sub-project needs

Each row has a recommended default so review can be "approve or amend". "Blocks" names the first SP4
release that cannot start without it. Rows D-12 to D-19 are the programme's (P§8); D-27 to D-34 are
raised here and recorded in registry §K (**D-26 is SP2's** "seal `contacts.email` at rest", so this
document's STOP and unsubscribe rule is D-33 and D-27 keeps the number SP3 already cites); D-35, D-36,
D-37, D-39 and D-40 are open questions of the registry's section L, promoted to decisions because each needs
an owner answer (the registry keeps the interface question and its registered default under the old handle,
which the Marker column names as provenance). D-38 and D-41 belong to the registry and to SP3 and add no
condition of their own to an SP4 release. Decisions of SP1 to SP3 that SP4 inherits follow the table.

| ID | Decision | Recommended default | Marker | Blocks |
|---|---|---|---|---|
| D-12 | Insights vs stages landing order (the event contract insights consumes) | Stages land first; insights is rebased onto `stage_outcome` | (pending D-12) | R4.6 |
| D-13 | Memory ceiling (amended by registry L17, which holds the five-fold arithmetic). SP4 adds consumer folds in support (R4.4), marketing (R4.6) and storefront (R4.7), +1 MB each, the insights container (+99 MB, R4.6) and, in R4.5-B, the PDF library resident in deal and finance (+10 to +20 MB each, **not measured**: inference from the library's size) | Accept the three folds and raise `EDGE_BUDGET_ACCEPTED_MB` by one per release; ship insights only after the owner answers. Running totals in each release's rollout, assuming R2.4 and R3.8 have shipped: R4.4 3750 MB ten profiles (97.1%) and 3947 with `admin` (102.2%); R4.6 3751 and 3948; R4.7 3752 and 3949 (102.3%); insights adds 99: 3851 (99.7%) and 4048 (104.8%). **Worst case with the PDF library in both processes: 3871 to 3891 MB (100.2% to 100.8%) and, with `admin`, 4068 to 4088 MB (105.3% to 105.9%)**: with insights the ten-profile total crosses the box's 3862 MB MemTotal at +12 MB, which only a raised `EDGE_BUDGET_ACCEPTED_MB` covers (the preflight cap is 125%, 4827 MB). R4.5-B therefore ships only after the measured delta is priced into its own kind (§10.8) | (pending D-13) | R4.4, R4.5-B, R4.6 |
| D-14 | Party identity: company is the party, contact the person, phone-only contacts allowed | As stated | (pending D-14) | R4.1 |
| D-15 | Engage contact book is a projection of CRM contacts for CRM-entitled organizations; standalone Engage kept | As stated; the projection never opts anyone in | (pending D-15) | R4.2 |
| D-16 | Marketing vs Engage | Engage owns sending and delivery analytics; Marketing owns planning and attribution and reads Engage | (pending D-16) | R4.6 |
| D-17 | Target for the client link and the won-deal kickoff: `project` (PulseBoard) or `projects` | Both products get the same link columns and event fields in R4.4; the **kickoff** is built in PulseBoard (`project`) first, because ADR-0000 §11 already says "PulseBoard client id becomes the CRM customer id" (`docs/adr/0000-phase-0-decisions.md:157-165`) and it holds clients, budget, billable and timesheets; `projects` adopts the same route name later without redesign | (pending D-17) | R4.4 (kickoff only) |
| D-18 | Discount approvals use the approval engine, with a membership requester abstraction and a platform inbox UI | As stated; supersedes ADR-0006 Q1's narrow stance in the ADR `approvals-for-tenants-without-hrms` (expected 0042), whose draft is in §10.4, which R4.5 files `Status: proposed` and which gates the approval deploy (§10.8) | (pending D-18) | R4.5 |
| D-19 | Quotes live in `deal`'s own schema; `inventory_items` is the one item master; `storefront.products` gains `inventory_item_id`; the `quotation` scaffold is deleted | As stated | (pending D-19) | R4.5 |
| D-27 | Erase retention exceptions: what each store keeps after `crm.book.contact.erased` (finance documents, ticket and message bodies, consent evidence, audit rows, deal text), **and what the erase of a lead keeps** | The coverage table in §6.5: identifiers are overwritten everywhere a store keys a row by the contact id; legal documents (invoices), consent ledger rows, suppressions, free-text bodies and append-only deal text are retained and listed. **A lead that never converted** has no contact id for the event to carry, so SP3's administrator erase of one lead (`POST /leads/{lead_id}:erase`, SP3 §11.3.3, R3.7a; its D-27 row lists what it clears and keeps) is that route, and SP4's lead handler applies the same function to a converted lead (§12.4.6). The owner and legal confirm each exception; no legal claim is made here | (pending D-27) | R4.1 (ADR acceptance), R4.3 |
| D-28 | Accounting for an online refund of a captured invoice or order payment | **Record only.** Finance stores the refund fact on `online_captures`, writes an audit row and logs it; it creates no credit note and no negative allocation. A person issues the credit note | (pending D-28) | R4.3 |
| D-29 | Fulfilment billing settings surface. No code writes `fulfilment_settings` (`services/operations/app/orders/selectors.py:355-363`), so the order-to-invoice chain has no tax rate, timing or party-creation setting an organization can set | R4.3 includes a minimal slice, R4.3-S: `GET` and `PUT /operations/fulfilment/settings` for the organization-default row only (`invoice_timing`, `default_tax_rate_pct`, `create_crm_party_on_order` and the per-organization opt-in `order_invoice_chain_enabled`, default false), one new permission, one card on the existing operations payment-settings screen, **used by the organization's own administrator**: the tax rate is a tenant's business setting, never the platform owner's. The alternative is to ship the chain dark and set rows by owner SQL | (pending D-29) | R4.3 (chain enablement) |
| D-30 | Customer-facing pay page ("payment link") for invoices | **Defer.** No public payment surface exists and the provider seam has hosted-checkout orders only (`services/payment/app/providers/base.py:394-480, 629-643`). R4.5 ships staff-assisted collection (§10.3, §17 deviation 3) | (pending D-30) | none |
| D-31 | Marketing opt-in captured on a public form (the storefront buyer form, and by the same rule a lead capture form): **single opt-in recorded with evidence**, or **confirmed opt-in** (a confirmation e-mail whose link writes the `opted_in` event) | Single opt-in: the ledger row names the form text and the enquiry, every marketing message carries the unsubscribe link, and anyone who types another person's address with the box ticked is the known weakness (§12.4.6). Confirmed opt-in needs a public confirmation route and token and is deferred | (pending D-31) | R4.7 (storefront consent only) |
| D-32 | Insights v2: company-keyed cross-product facts (§11.5) | Do not build until a dashboard needs a company-ranked board that the party projection of R4.3 cannot serve; the design is kept so the decision costs a document, not a producer change | (pending D-32) | none |
| D-33 | **One stated rule for STOP and unsubscribe.** Two stale texts disagree with each other and with the code: the `consent.py` module docstring says an unsubscribe link opts out of both purposes (`services/notification/app/consent.py:15-22`), ADR-0021 says a STOP does (`docs/adr/0021-consent-ledger.md:62-65`); the code opts **both** out of marketing only (STOP: `services/notification/app/services.py:2102-2110`; unsubscribe: `:4208-4216, 4314-4322, 4377-4386`, with the comment "an invoice or receipt is not [marketing]"). (Raised first as D-26; renumbered because SP2's D-26 is a different question.) | **Confirm the code's rule: STOP and the unsubscribe link both opt out of marketing only**; a transactional opt-out is a separate, explicit act (an admin, or the person's own request). R4.1 corrects the two texts and `docs/whatsapp/architecture.md` in the ADR; **no behaviour changes**, so R4.2 to R4.4's transactional e-mail (invoice follow-up, ticket notifications) is not silently suppressed by a newsletter unsubscribe | (pending D-33) | R4.1 (ADR acceptance) |
| D-34 | **Lifecycle automation.** May CRM promote a company from `prospect` or `lead` to `customer` on a commercial event? It changes the dashboard's "Total Customers", which counts `status = customer` (`frontend/opsgrids-dashboard/src/api/index.ts:382`). The programme names one trigger, "first order → customer" (P§6); the registry's EV2 text adds the first won deal and the first paid invoice | **A per-organization setting, default `off`** (`customer.customer_settings.lifecycle_promotion`, B83, C106, §8.5). `first_order` promotes on the first accepted order only, which is all P§6 names; `first_commercial_event` also promotes on the first won deal and the first paid invoice and is the owner's explicit extension. No organization changes until an administrator turns it on; H4 and every replay never promote | (pending D-34) | R4.3 (the setting; no promotion happens while it is `off`) |
| D-35 | Who owns a party created by `parties:ensure` (`owner_id` is NOT NULL: `services/customer/app/models.py:118, 216`) | The registered default: the caller's `owner_membership_id` when activatable, else the organization's single active `is_owner` membership (activated as a rep with an audit row), else 422 `owner_required` | (pending D-35); registry L1 | R4.1 |
| D-36 | First touch or last touch for campaign attribution (R4.6) | **First touch, write-once**: a lead's campaign is set at capture and the deal copies it at conversion; neither changes afterwards (409 `attribution_locked`, SD-21, §11.4.1). The schema carries one `marketing_campaign_id` and one set of UTM columns per lead and per deal either way, so the answer moves a rule and not a column | (pending D-36); registry L9 | R4.6-A |
| D-37 | Permission for CRM-originated sends | `engage.inbox.message.reply` (the rep also needs an Engage role; a CRM template cannot hold a `cross_product` code: `tools/export_permissions.py:191-221`) | (pending D-37); registry L13 | R4.2 |
| D-39 | Notes attachments and erasure | Until the ADR chooses, R4.5 ships PDFs but not B57, C81–C83. SP4 adds one default: deal binds `crm.book.contact.erased` unconditionally (§6.5), so the attachment purge joins an existing handler | (pending D-39); registry L19 | R4.5 (attachments only) |
| D-40 | Who may hand a quote off to finance, operations or projects | No new code; the ORG floor on `crm.pipeline.quote.write` for `order` and `invoice`, plus the callee's entitlement re-check (C20) | (pending D-40); registry L21 | R4.5 |

**Inherited gating decisions.** SP4 also rests on decisions that belong to the programme, SP2 and SP3.
Each is open until the owner answers; the table gives the recommended default, the marker this document
uses for it, and the first SP4 release it blocks, so a reader can see what R4.1 waits for beyond D-14 and
D-27 to D-35 (and SP2's D-26, the last row). "C15" in the D-7 and D-9 rows is the programme's change C15
(the roster re-key and its flip, P§5), not the registry's route row C15.

| ID | Decision | Recommended default | Marker | First SP4 release it blocks, and why |
|---|---|---|---|---|
| D-7 | What a rep is after C15 | `customer.owners` gains `membership_id`; the Team screen lists organization members and activates them as reps; free-form rep creation retires after cutover | (pending D-7) | R4.1: `owner_membership_id` on every enriched event, and D-35's activation of a roster row (`owner.activated`), assume the roster is a view of members |
| D-8 | Owner-id mapping rule | Email match roster to member, employee link for employee-valued rows; unmapped rows untouched and listed; owner-run dry run per organization; never guess | (pending D-8) | R4.1: until a row is mapped its events carry `owner_membership_id: null` and the row is visible at ORG only; C94 and the reassignment task count both id spaces (§12.3, §12.4.2) |
| D-9 | Sign-off for the access widening C15 causes (reps gain OWN write, convert and reveal on their rows) | Approve, with a security review of every OWN route before the flip | (pending D-9) | R4.1, through R2.7: R4.1 needs the flip, and every OWN rep in R4.2 to R4.7 depends on it |
| D-11 | Fold `lead` into `customer`? | Keep three processes for this programme | (pending D-11) | R4.7 (and R4.1's lead event fields): the lead consumer and its queue `lead.events`, `lead.scrub_lead_audit`, C94 and C104 on lead, and EV45 all assume lead stays its own process; a yes moves them into customer and rewrites §12 |
| D-20 | Role consolidation: `CRM_ADMIN`, `CRM_REP`, `CRM_MANAGER`, `CRM_VIEWER` | As stated; the six legacy codes and all 17 permission codes frozen | (pending D-20) | R4.3 and R4.5: E23, E25 and E26 reach the non-admin templates only through R2.8's templates, `CRM_MANAGER` at TEAM (†) and `CRM_REP` at OWN; `CRM_VIEWER` holds none of them (registry E.2: "no money"). Until then they reach `CRM_ADMIN` and organization owners only |
| D-22 | API nouns | `/companies` canonical (`/customers` aliased); the 360 under `/contacts/{id}/360` | (pending D-22) | R4.3: C24 `GET /companies/{id}/360` and `useCompanyLookup` (G13) sit under the `/companies` prefix and the `ids` filter R3.2 delivers |
| D-24 | TEAM scope for managers | Include, sourced from `manager_membership_id` through a per-service `owner_team` | (pending D-24) | R4.7: the HR-exit mapping reads `owner_team.employee_id`, a column of that table (§12.2); the TEAM cells of E23, E25 and E26 for `CRM_MANAGER` follow R3.8's reconcile |
| SP2 D-26 | Seal `contacts.email` at rest (SP2 §2.1, registry B56) | **No**: R2.9 Part C is built only on a yes | (pending SP2 D-26) | None by default. The plaintext `contacts.email` readers C45 (messaging target), C47 (finance contact summary) and C53 (export) are **consciously left on the plaintext column**; on a yes each must be sealed-aware before R2.9 Part C ships (SP2 §12.4 rule 3), which reopens R4.1 and R4.2 |

The other programme decisions (D-1 to D-6, D-10, D-21, D-23) gate SP2 and SP3 releases that SP4 needs only
through its "Needs" lines; they add no condition of their own here.

### 2.2 Design decisions made here

Each decision was a real fork. The rejected alternative is stated so a reviewer can overturn it.

| # | Decision | Rejected alternative and why |
|---|---|---|
| SD-1 | **Resolve reads, ensure writes.** A product calls `parties:resolve` on a read path and `parties:ensure` only on a deliberate create path (accepting an order for a new buyer with the organization's opt-in, converting a lead). Never both in one flow | "Ensure everywhere" (get-or-create on every read): looking at a ticket would create CRM records as a side effect, and an unentitled or unauthorized viewer would write master data |
| SD-2 | **Validate on write, never trust a plain UUID.** A product that stores `crm_company_id` or `crm_contact_id` proves it through C50 (company summary) or C52 (resolve) in a claim/call/record step before the write transaction opens, and stores the link only when the answer is a live record. `deal.company_id` today is never validated (`services/deal/app/services.py:99-186`) | Trust the caller's UUID (today's deal behaviour): a typo or a cross-tenant guess becomes a permanent dangling link, and the nightly orphan check would be the only defence |
| SD-3 | **A snapshot beside every link; no placeholder rows.** Each product keeps the display snapshot it has today (order name and phone, ticket requester, project `client`, enquiry name) so it works with CRM absent, and never invents a CRM record or a placeholder party to satisfy a NOT NULL | Replace snapshots with links: breaks every non-CRM organization |
| SD-4 | **One handler per projected event, one idempotency marker.** The customer consumer handles each finance, operations, support, project, quote and conversation event in a single handler that writes the document, payment, rollup, timeline and lifecycle rows in one transaction and marks `uuid5(event_id, "party-projection")`. `processed_events` is keyed on `event_id` alone (`libs/platform-core/src/platform_core/events.py:72-81`), so two handlers on one event with the same marker would make the second skip silently; the notification consumer already avoids this with a derived id (`services/notification/app/consumers.py:217`) | Separate handlers for rollup, timeline and lifecycle: the first marks the event and the others never run |
| SD-5 | **Party facts are state, not counters.** `customer.party_documents` and `customer.party_payments` hold per-document and per-allocation state with a version guard (`source_occurred_at`); `party_rollup` is derived from them and recomputed for the touched `(company, currency)` in the same transaction **and under a per-company advisory lock** (`pg_advisory_xact_lock(hashtextextended('customer.party:' \|\| organization_id \|\| ':' \|\| company_id, 0))`, taken before the fact upsert: the handler, the nightly sweep, H4 and the second consumer that a rollback to `split-workers` briefly runs each write the same rows, and without the lock two READ COMMITTED transactions each aggregate without the other's uncommitted fact and the later commit overwrites with a partial total), and `overdue_minor` is recomputed nightly because it depends on the date. This follows ADR-0020 §2 | Delta counters on the rollup: cannot produce `overdue_minor`, `latest_invoice` or `count_90d`, drift on a redelivery after a manual fix, and lose a payment that arrives before its invoice event (the two events come from different producers through one queue, and a failed message is requeued behind newer ones: `libs/platform-core/src/platform_core/events.py:489-507`). An unlocked recompute: one consumer handles one message at a time (`events.py:489-495`), so the racing writers are the sweep, the backfill tools and a second consumer, and the nightly sweep recomputes only companies with an open invoice, so a paid-up company would keep the partial figure |
| SD-6 | **Section gating has two tiers (§4.4).** A section read live from another product is gated by the viewer's own grant in that product, evaluated by that product on the forwarded identity, never widened by CRM scope. A section served from CRM's own projection of that product's events (money, orders, timeline) is CRM data and is gated by `crm.book.company.read_commercial` plus CRM scope on the company, because finance and operations are ORG-only (`services/finance/app/scope.py:1-14`) and cannot be forwarded to for an OWN rep | Forward the rep's identity to finance for the money section: it can only 403 or force an ORG finance grant on every rep |
| SD-7 | **Who may cause a CRM party to be created.** Storefront enquiries create leads (always, through C56); operations orders create parties only when `fulfilment_settings.create_crm_party_on_order` is on (default off); finance, support, projects and PulseBoard never create a party and only link to an existing one | Auto-create from every product: an order clerk without any CRM permission would create master data under a service token |
| SD-8 | **The order-to-invoice chain ships dark.** `ORDER_INVOICE_CHAIN_MODE` (F19) is `off`, `log_only` (default) or `on`; `log_only` runs the resolution, logs the request it would send with ids and amounts, and keeps today's visibly-logged skip. **In `on` mode the chain emits only for an organization that has a settings row and has opted in** (`fulfilment_settings.order_invoice_chain_enabled`, default false, written only through R4.3-S), so flipping the box-wide variable never turns it on for an organization that has not set a tax rate: readiness is per organization (S50). Finance derives place of supply, supply type and due date from the billing profile when the event omits them, and lines carry `hsn_sac` | Wire the chain unconditionally: the first production order invoices would have a 0% tax rate (the only source is the unwritable `default_tax_rate_pct`), due date equal to the issue date, no HSN and no place of supply (`services/finance/app/services.py:1196-1224`), and would enter e-invoice auto-filing. A box-wide switch alone: an organization with no settings row resolves to the hard-coded default (rate 0, source `default`) and would be invoiced at 0% the moment the variable flips, on legal tax documents that only a credit note corrects |
| SD-9 | **Online captures are parked and applied under a per-order advisory lock.** `finance.online_captures` records every verified capture; it is applied to the order's invoice when both exist, under `pg_advisory_xact_lock` keyed on the order id in both the capture handler and the invoice creation. Refunds are recorded, not accounted (D-28) | Allocate at capture time only: a prepaid order is captured before its invoice exists (the invoice is requested at READY), so the payment would be lost or rejected (`services/finance/app/services.py:1293-1380` requires an open invoice) |
| SD-10 | **Erase = anonymise in place, plus a narrow audit scrub.** The audit copies of a contact's data are rewritten by a `SECURITY DEFINER` function, because the application role holds no `UPDATE` on `audit_logs` (`libs/platform-core/src/platform_core/db.py:140-146`), so B12's first-draft "the erase rewrites the audit rows in the same transaction" could not run as the app role (B12 now registers the function). The definer's power is bound twice: the key helpers act only on JSON objects (an absent snapshot is stored as JSON `null`, S49), and the function refuses an organization other than the session's own tenant (§6.2) | Give the app role `UPDATE` on `audit_logs`: breaks the platform's append-only rule for every row; or leave old audit rows to an owner-run tool: the erase would not be complete when it returns; or trust the `p_organization_id` argument: any code running as the app role could then rewrite another tenant's audit rows, which defeats RLS as the net |
| SD-11 | **The system that owns a capture surface writes the consent ledger.** Storefront for its buyer form, lead for its capture endpoints, support never (no consent surface). CRM stores no consent and the Engage projection writes none (ADR-0021: silence is not consent) | CRM stores a consent flag: two decision sources, and a bulk import would attest opt-ins |
| SD-12 | **The WhatsApp touch is emitted when the contact is known, not at the inbound fold.** The inbound event carries no contact (`services/notification/app/services.py:1706-1720`); the contact is resolved later by `fold_inbound_contact` (`services/notification/app/consumers.py:190-269`), so `engage.inbox.conversation.touched` is emitted there and from reply and CRM-send paths | Emit at the webhook fold: no `crm_contact_id` yet, so every stranger and every known contact looks the same |
| SD-13 | **Approval's membership requester is additive and rollback-tolerant.** New nullable `*_membership_id` columns beside the employee columns; for a membership-keyed row the NOT NULL employee columns carry a surrogate id, `uuid5(NAMESPACE_MEMBERSHIP, membership_id)`, used for nothing but to keep the previous image able to read the row. The approver is resolved at open time from at most three direct-manager reads of account (C57), and the step is unassigned (any holder of `core.approval.request.decide` at ORG) when there is no active manager. **The three guard triggers are replaced in the same revision** so the new columns are frozen exactly as their employee twins (`requester_membership_id` with the request identity, `decided_by_membership_id` after the decision, `approver_membership_id` on a step), because each trigger compares an explicit column tuple and a column outside it stays mutable (S44) | Relax the NOT NULLs and store only membership ids: the previous image would 500 on every approvals list for the whole organization after the first CRM request, which breaks "each release is reversible" (M2). Leave the triggers alone: the decider of a decided request and the approver of a step would remain editable by one `UPDATE`, defeating the decide-once guarantee this decision relies on |
| SD-14 | **Quotes-lite shape.** One quote chain per deal; each version is its own row with its own number `"{deal_number_prefix}-{deal.number}/Q{version}"`; quote and line totals are derived on the server and never accepted from the client; tax is one amount (no CGST/SGST/IGST split: the seller's state lives in finance, which CRM cannot read) and finance computes the split on the invoice. **Rounding follows the code that already owns each rule:** quantity × price is half-to-even once per line (ADR-0014 §2, `quantity.value_minor`), and every percentage of money, the line discount and the GST, is half-up through `Decimal`, exactly finance's `gst_amount_minor` (`services/finance/app/domain.py:573-581`), operations' `expected_gst_amount_minor` that mirrors it (`services/operations/app/domain.py:351-370`) and marketing's `_round_half_up` | Several parallel quotes per deal and client-supplied totals: alternatives and a refused tax mismatch at invoice time. Half-even tax "as operations order lines do": that is true only of the line amount (`services/operations/app/domain.py:338-342`); GST is half-up everywhere it exists, so a half-even quote would disagree with finance on every exact-half case and make `quote_total_delta_minor` the normal path |
| SD-15 | **Payment collection on invoices is staff-assisted.** `reference_type = finance.invoice`, finance calls payment's collections API and the finance SPA opens the hosted checkout, exactly as operations does for orders; there is no customer-facing URL (D-30) | A tokenized public pay page in R4.5: a new anonymous money surface with its own CSP and rate-limit design, and the first merchant invoice collections (risk HIGH in the discovery) in the same release |
| SD-16 | **Attribution is facts, not counters.** `marketing.attribution_leads` and `marketing.attribution_deals` hold per-lead and per-deal state, version-guarded; `campaign_attribution` is derived and keyed by currency. The typed campaign numbers become adjustments by meaning, with **no column rename** in this programme | Rename `leads`, `revenue_minor` and the others to `adjustment_*`: the previous release would read the old names and break |
| SD-17 | **HR offboarding binds the HRMS exit event as well as the membership revoke.** At HEAD an HR exit does not revoke the membership: the account consumer binds only product-activation events (`services/account/app/consumers.py:192-194`), `services/employee/app/services.py` has no call into account, and `core.membership.revoked` is staged only by `revoke_member` (`services/account/app/services.py:920-970`). The registry's first-draft EV25 note ("an HR exit arrives as `core.membership.revoked`") was therefore not true at HEAD, and EV25 is corrected (registry J33) | Consume only `core.membership.revoked`: an employee who exits HR but keeps a membership would keep an active CRM rep and a round-robin slot |
| SD-18 | **The nightly orphan check runs inside `customer`.** Customer already folds a consumer; a sweep there (the `RUN_SWEEPS_IN_API` pattern, `services/operations/app/config.py:32`) calls each owning service's `crm-references` export under a service token and checks the ids against its own tables. It reports and never deletes (H5, ADR-0000 §10) | A new job container: no budget; per-service validation on write only: leaves existing and later-deleted references undetected |
| SD-19 | **Global search matches names and numbers only.** Cross-product groups register per entitlement and grant exactly as `useGlobalSearch` does (`frontend/opsgrids-client/src/platform/useGlobalSearch.ts:1-30`); an identifier lookup is a POST body (C38, C86), because a URL is retained by gateway and proxy logs | Searching email or phone through `?q=`: PII in access logs |
| SD-20 | **A company with commercial history cannot be deleted.** `delete_company` already refuses while contacts exist (`services/customer/app/services.py:562-577`); R4.3 also refuses (409 `has_commercial_history`) while `party_documents` rows exist. Status `churned` is the supported way to retire it | Allow deletion and rely on the orphan check: finance and operations would keep links to a record that no longer resolves |
| SD-21 | **Attribution is first touch and write-once, and campaign figures are CRM-attributed plus a typed adjustment.** A lead's campaign id is set at capture and the deal copies it at conversion; neither can change afterwards (409 `attribution_locked`). Marketing's typed `leads`, `conversions`, `revenue_minor` and `pipeline_minor` keep their names and become adjustments added to the attributed figures; money is added only in the campaign's own currency (pending D-36) | Last touch or free re-attribution: a rep's edit silently rewrites history and every ROAS already shown. Replacing the typed columns: breaks the published `CampaignOut` contract (`services/marketing/app/schemas.py:146-167`) |
| SD-22 | **Insights is adopted without names, and its unshipped migrations are edited in place only when the owner has confirmed that no shared database has run them.** The merge drops the `name` columns and the stage vocabulary CHECK, adds the outcome and makes the owner a nullable membership id; it chains the three divergent heads. Whether that is an in-place edit of `ad6f07c940d8` or a forward revision on top is decided by one owner-run, read-only check recorded in §13 step 10: **absent on every shared database means in place; anything else, or no recorded answer, means the forward revision** (default). The three export routes it re-applies are re-cut name-free (§11.4.2). CRM OWN uses the membership id | An unconditional in-place edit: "never run on a shared database" is asserted, not evidenced (the branches are local worktrees, `ui/sprint-integration` carries them and the edge box is staging-like), and an environment that did run `ad6f07c940d8` would then carry a stage CHECK that was wrong from the start and a `deal_facts.name` already holding a lead's full name (S13), which no erase could reach (`docs/adr/0020-insights-projection.md:682`). A forward revision as the only path: on a database that never ran the branch it adds a revision whose only job is to undo another |
| SD-23 | **The storefront's displayed status is derived beside the merchant's own, never over it.** `status` stays the desk workflow and keeps its `status_changed` contract; `crm_status` is a second read-only field shown as the headline | Overwriting `status` from CRM: a merchant's manual `contacted` would be reversed silently and the committed `status_changed` event would describe changes nobody made |
| SD-24 | **One writer per consent surface, single opt-in recorded with evidence.** Storefront writes for its buyer form, lead for its own endpoints, nobody for a lead that came from elsewhere; an unticked box writes nothing (pending D-31) | Lead forwarding every lead's evidence: two ledger rows for one tick. CRM recording the box: a second decision source (SD-11) |
| SD-25 | **Round-robin availability never leaves a lead unowned.** A rep on approved leave is skipped; when every active rep is away the rotation ignores availability; a half-day counts as a day; "today" is the organization's date (`lead_settings.timezone`) | Leaving the lead unassigned: it sits where no one looks |
| SD-26 | **An HR exit deactivates the roster entry and queues one task; it reassigns nothing and revokes no login.** One system follow-up per departed rep per day, anchored on their oldest open deal, addressed to the manager or the organization owner | One follow-up per deal (hundreds for a large book), or automatic reassignment (a book would change hands silently: `services/customer/app/services.py:295-300` states the principle) |
| SD-27 | **The coverage audit is a tested document.** `docs/crm-ecosystem-coverage.md` is checked by `tests/infra/test_crm_ecosystem_coverage_doc.py`: every product key, every cited proof file, no CRM link column in the non-connected products | A prose audit: true on the day it is written and silently false after the next release |
| SD-28 | **A phone number suggests, it never links.** `accept_order` records a unique phone match as a suggestion (ids only, in `settings_snapshot`) and writes no link; the link comes from a company or contact chosen at order entry, from an operator's confirmation, or from the C51 create that the organization opted into (§8.4.2). A suggestion feeds neither the credit check nor the invoice | Link on one unambiguous phone match: a shared or reused number would select the legal buyer, GSTIN and place of supply of a tax invoice (and its e-invoice filing) for an operator who holds no CRM permission and sees no confirmation |
| SD-29 | **The purpose of an e-mail send is derived by the server.** An Engage e-mail template carries a category (`marketing` or `transactional`; unclassified counts as marketing) that only an administrator sets, audited; C60 refuses a `transactional` purpose on any other template (§7.4, S54) | Trust the caller's `purpose` and mitigate in the UI: a rep holding `engage.inbox.message.reply` could send marketing to a contact with no opt-in by labelling it transactional, and a hidden option is not a server control |
| SD-30 | **Promotion to `customer` is an organization setting, default off, stored in customer.** `customer.customer_settings` (B83) holds `lifecycle_promotion`; the projection handler reads it in its own transaction (D-34, §8.5) | Promote every organization on deploy (a headline number changes with no one choosing it); read the setting from `deal.pipeline_settings` (a cross-service read inside a handler); a box-wide variable (promotion is per organization) |
| SD-31 | **A rollback is stated per consumer, with what it loses.** Every release's rollback section carries a table of its consumers, the events a code rollback discards, and a catch-up or "forward-only" for each (§4.7) | Say "code rolls back to the previous images" and leave the lost events unsaid: a published outbox row cannot be replayed (`tools/outbox.py`), so a rollback that discards events is a data change the owner must approve knowingly |

## 3. Current state

All of the following was re-read at `HEAD` `3bc7f6ca`. "S" ids are used by later sections.

| # | Fact | Evidence |
|---|---|---|
| S1 | Customer's internal routes: `messaging-target` and `contacts:resolve` carry **no** caller allow-list and no org-claim check; `contacts/{id}` (finance), `leads:convert` (lead) and `owners:validate` (lead) carry both | `services/customer/app/api/internal.py:138-241` against `:95-121, 244-287, 290-348, 351-380`; `_require_caller` is called only at `:271, 318, 373` |
| S2 | `messaging-target` answers phone channels only (`whatsapp`, `sms`) and refuses any other with 422; `MessagingTargetOut` has `phone` and no `email` | `services/customer/app/api/internal.py:83, 163-166`; `services/customer/app/schemas.py:278-288` |
| S3 | `contacts:resolve` is phone-only (blind index, no decrypt, no audit) and returns only `contact_id` | `services/customer/app/api/internal.py:197-241`; `services/customer/app/schemas.py:291-309` |
| S4 | `Contact.email` is NOT NULL and unique among live rows; `company_id` is a NOT NULL same-schema FK; the phone is sealed (`phone_enc`) with a blind index (`phone_index`); `Company` has no GSTIN, address or tax identity and its name is unique case-sensitively | `services/customer/app/models.py:171, 188, 206, 208-212, 222-228, 95-154, 132-138` |
| S5 | A contact's personal data is copied into audit rows: `contact.created` stores `{full_name, email, status}`, `contact.updated` stores `before` and `after` of every changed field; `record_audit` redacts by key name only (`pan`, `bank_account`, …, not `email` or `full_name`) | `services/customer/app/services.py:676-686, 744-753`; `libs/platform-core/src/platform_core/audit.py:29-55, 106-115` |
| S6 | `UPDATE` and `DELETE` on `audit_logs` are revoked from every service's app role by `apply_schema_grants` | `libs/platform-core/src/platform_core/db.py:140-146` |
| S7 | `delete_company` is refused while the company has contacts; deal-service cannot see the delete, so deals keep a dangling id | `services/customer/app/services.py:562-577` |
| S8 | Customer has one consumer handler, `crm.pipeline.activity.logged` → `last_activity_at`, already folded into the API process; `processed_events` is keyed on `event_id` only | `services/customer/app/consumers.py:54-97`; `services/customer/app/config.py:17-23`; `libs/platform-core/src/platform_core/events.py:72-81` |
| S9 | The 360 is keyed on a contact id; `orders`, `finance` and `support` are always `null` (`MISSING_UPSTREAMS`); the deal leg calls `{deal_internal_url}/deals` while routers mount under `/api/v1`, so it never reaches deal (P§3 F6); section shapes `OrdersSection`, `FinanceSection`, `SupportSection` are pre-drawn, each with a single `Money` | `services/customer/app/c1_360.py:65-73, 157-179, 324-326`; `services/customer/app/c1_schemas.py:17, 52-80, 105-113`; `libs/platform-core/src/platform_core/app.py:363` |
| S10 | There is no committed schema for `crm.book.contact.*`, `crm.pipeline.activity.logged`, `finance.billing.customer.*`, any `support.*`, `core.membership.*`, `pulseboard.*`, `projects.portfolio.*`, `marketing.*`, or any order lifecycle event except `invoice_requested`; 26 of 77 directories carry the "Ahead of the producer" comment | `ls libs/contracts/events` (77 directories); `grep -l "Ahead of the producer" libs/contracts/events/*/v1.schema.json` |
| S11 | `deal.activities` is append-only by trigger; the activity audit row stores the free-text `subject`; the `activity.logged` event carries `type, deal_id, contact_id, occurred_at` and no company or direction | `services/deal/migrations/versions/e94d7a2c5f18_enable_row_level_security.py:90-107`; `services/deal/app/services.py:406-437`; `services/deal/app/models.py:99-138` |
| S12 | Deal has no consumer handler and no outbound service URL setting; lead has no `consumers.py`, no `bin/worker` and no fold setting | `services/deal/app/consumers.py:15-29`; `services/deal/app/config.py:1-19`; `ls services/lead/app services/lead/bin` |
| S13 | Lead conversion names a deal `"{company} — {full_name} · {lead id prefix}"`: the lead's personal name is inside `deals.name` and in deal audit rows | `services/lead/app/domain.py:159-177` |
| S14 | Finance's `Customer` is a free-standing billed party with a nullable `crm_contact_id` (unique among live rows) and no company link, GSTIN profile uniqueness, payment terms or credit limit; the model docstring still says "Distinct from any CRM account by design" | `services/finance/app/models.py:83-148, 86-89, 138-146` |
| S15 | The identity bridge is built and uncalled: `ensure_customer_for_crm_contact` (claim, call, record, savepoint race handling), the internal route restricted to caller `operations`, and finance's client; finance's compose block sets no `CUSTOMER_INTERNAL_URL` (it inherits `*service-env`) and operations has no customer or finance URL setting | `services/finance/app/services.py:506-640`; `services/finance/app/api/internal.py:366-411`; `infrastructure/edge/compose.prod.yml:1567-1577`; `services/operations/app/config.py:59` (only `payment_service_url`) |
| S16 | Operations' `_resolve_finance_customer_id` always returns `None`; `request_invoice_for_order` is a logged skip with audit row `order.invoice_request_skipped`; `check_credit_before_accept` is a documented no-op and every accept records `credit_check = not_implemented` | `services/operations/app/orders/services.py:871-892, 957-1032, 423-444, 582` |
| S17 | With the bridge wired, an order invoice would carry `due_date = issued_date`, no `place_of_supply` (inter-state is assumed), `supply_type` from the payload (always b2b), no HSN on lines, and tax from `default_tax_rate_pct`, which defaults to 0 and has **no writer**; auto-filing then starts | `services/finance/app/services.py:1178-1209` (`due_date=issued_on` at `:1187`, `buyer_gstin` at `:1198`), `:1226-1262` (auto-filing); `services/operations/app/orders/services.py:905-955`; `services/operations/app/orders/selectors.py:355-363`; no `FulfilmentSettings(` constructor in `services/operations/app` |
| S18 | Default handover refusal: the hard-coded default `invoice_timing = on_accept` makes `check_billing_gate` refuse a handover until `invoice_id` is set, and nothing sets it | `services/operations/app/orders/selectors.py:354-363`; `services/operations/app/orders/services.py:1073-1102` |
| S19 | Operations orders carry `customer_id` (a CRM contact id, plain UUID) and a **plaintext** `customer_phone`; no `crm_*`, `source_*` or `finance_customer_id` columns exist | `services/operations/app/models.py:1647-1650` |
| S20 | Finance does not consume online captures. The captured event's `reference_type` enum is `["operations.order"]`; payment's collections routes are callable only by `operations`; the captured event carries no payer | `libs/contracts/events/payment.collections.payment.captured/v1.schema.json`; `services/payment/app/api/collections_internal.py:51` (`COLLECTION_CALLERS`); `services/operations/app/consumers.py:280-313` |
| S21 | Finance `Payment` has no currency column; `reference` is unique per organization and `String(40)`; `record_payment` needs open invoices of the paying customer and refuses an allocation above the outstanding amount, but never checks that the allocated invoices share one currency (S48) | `services/finance/app/models.py:298-328`; `services/finance/app/services.py:1293-1380` (the checks are `:1356-1366`) |
| S22 | Finance's invoice and payment events are "ahead of the producer": the committed schemas require `issued_date`, `due_date`, `subtotal_minor`, `tax_minor` (issued), `currency`, `paid_date` (paid) and `received_date`, `method`, `currency` (payment), none of which the producer emits; `payment.received.method` is a closed enum without an online value | `services/finance/app/services.py:840-860, 1413-1465`; `libs/contracts/events/finance.billing.invoice.{issued,paid}`, `finance.billing.payment.received` |
| S23 | An issued invoice snapshots only the two GSTINs and the place of supply; the buyer's name, address, email and state are read from `finance.customers`, which `update_customer` edits in place | `services/finance/app/models.py:151-253, 205-207`; `services/finance/app/services.py:369-450` (`update_customer`) |
| S24 | Notification's contact book is sealed, hard-deleted and unique on the email blind index; conversations carry `contact_id` (a CRM contact id) and `assignee_membership_id`; the consent ledger stores a sealed subject with a blind index, `contact_id` naming an Engage contact (not a CRM one) | `services/notification/app/models.py:815-841, 501-569, 539, 551, 874-976, 932` |
| S25 | The prose about STOP and unsubscribe contradicts itself and the code: `consent.py`'s module docstring says an unsubscribe link opts out of both purposes, ADR-0021 says a STOP does; the code opts **both** out of marketing only | `services/notification/app/consent.py:15-22`; `docs/adr/0021-consent-ledger.md:62-65`; code: `services/notification/app/services.py:2102-2110, 4208-4216, 4314-4322, 4377-4386` |
| S26 | The subscribable allow-list holds five CRM keys, three finance keys (not `invoice.paid`) and three support keys (not `status_changed`); the in-app feed is addressed by membership id and `NotificationOut` has no entity reference | `services/notification/app/domain.py:98-227, 146-151, 223-226, 310-325`; `services/notification/app/inbox.py:47-56` |
| S27 | Notification's two outbound calls into customer mint tokens with **no claims** | `services/notification/app/targets.py:78-80`; `services/notification/app/contacts.py:79-81` |
| S28 | The inbound WhatsApp event carries `conversation_id` and `inbound_message_id` only; the contact is resolved later and the conversation row is updated | `services/notification/app/services.py:1706-1720, 2269-2306`; `services/notification/app/consumers.py:190-269` |
| S29 | The messaging summary is keyed on `Conversation.contact_id` and callable by `customer` only | `services/notification/app/c1_messaging_summary.py:60-140`; `services/notification/app/api/c1_internal.py:36-49, 81-114` |
| S30 | Support tickets carry only a requester snapshot (`requester_name`, `requester_email`, `requester_company`, `requester_kind`) and `channel` in `email, chat, web, phone, api`; its five event constants exist, payloads carry no requester or CRM id; its consumer is a handler-less scaffold; both ticket permissions floor at ORG | `services/support/app/models.py:87-135`; `services/support/app/services.py:40-52, 388-403, 495-506, 550-561, 631-643, 683-693`; `services/support/app/consumers.py:15`; `services/support/app/api/routes.py:63-70` |
| S31 | PulseBoard and Projects keep `client` as free text (`String(200)`); `project.currency` defaults to USD, `projects.currency` to INR; the project-created payloads carry no client | `services/project/app/models.py:107, 120-122`; `services/projects/app/models.py:102, 119`; `services/project/app/services.py:332-345`; `services/projects/app/services.py:345-360` |
| S32 | Marketing's campaign numbers (`leads`, `conversions`, `pipeline_minor`, `revenue_minor`) are typed by hand; its consumer is a handler-less scaffold | `services/marketing/app/models.py:131-138`; `services/marketing/app/consumers.py:15` |
| S33 | Storefront enquiries are created anonymously, with a sealed email (always present) and optional sealed phone and GSTIN, no consent field and no CRM link; `storefront.enquiry.created` carries `enquiry_id, store_id, company, line_count`; a status change writes an audit row and **no event**; its consumer is a handler-less scaffold; `products` has no item-master link | `services/storefront/app/api/public.py:371-402`; `services/storefront/app/services.py:1817-1939`; `services/storefront/app/models.py:780-830, 671-742`; `services/storefront/app/consumers.py:15` |
| S34 | The approval engine is employee-keyed throughout: `requester_employee_id` NOT NULL, steps by `approver_employee_id`, delegations by employee, one hierarchy projection by employee id; two request types are known; escalation skips an unassigned step and walks the employee hierarchy; approval runs under the `hrms` profile | `services/approval/app/models.py:238-330, 71-131`; `services/approval/app/services.py:75, 1330-1470`; `infrastructure/edge/memory-budget.json` (`containers.approval.profile = hrms`) |
| S35 | Scheduler accepts a scheduled target only when it starts with the producer's service name; there is no cancel; it runs under the `finance` profile | `services/scheduler/app/consumers.py:83-111`; `services/scheduler/app/models.py:25-40`; `infrastructure/edge/memory-budget.json` (`containers.scheduler.profile = finance`) |
| S36 | The private object store is decided in ADR-0009 Q1 (the owning product authorizes, `document` only presigns through an internal `:presign` route, uploads come from the product server-side, "no browser upload path") and **is not on `main`**. An unmerged, **divergent** implementation exists: commit `45a7f8bf` (branch `sprint/private-uploads`, also contained in `ui/sprint-integration`, `ui/rc-backend` and `ui/release-candidate`) adds public routes `POST /documents/private-objects/initiate`, `POST …/{id}/complete`, `GET` and `DELETE …/{id}` with a browser presigned POST, migration `d7c4a1f0e3b6_private_objects_table.py`, and permissions `core.content.private_document.read` and `.write` at **OWN keyed on the employee link** (its message says it inherits "the no-HRMS gap"), so the document service, not the owning product, authorizes. `document` runs only under the `storefront` profile; no PDF library is used anywhere | `docs/adr/0009-uploaded-images-and-object-storage.md:909-945`; `git show 45a7f8bf:services/document/app/api/routes.py:478-598`, `…/app/catalogue.py:228-229`, `git show 45a7f8bf --stat`; `git merge-base --is-ancestor 45a7f8bf main` fails; `infrastructure/edge/memory-budget.json` (`containers.document.profile = storefront`); `grep` for PDF libraries in `services/` and `frontend/` finds none |
| S37 | `account.memberships` carries `employee_id`, `manager_membership_id` and `status`; `core.membership.revoked` is staged only by the admin revoke (payload `organization_id, membership_id, user_id`); account's internal membership read is notification-only; an HR exit does not revoke a membership | `services/account/app/models.py:309-345`; `services/account/app/services.py:920-970`; `services/account/app/api/internal.py:304-346`; `services/account/app/consumers.py:192-194` |
| S38 | `hrms.time.leave.applied` is ahead of its producer: the schema requires `status, employee_id, leave_type_id, start_date, end_date, total_half_days`, `_publish_leave_applied` emits none of them | `libs/contracts/events/hrms.time.leave.applied/v1.schema.json`; `services/leave/app/services.py:636-661` |
| S39 | ITSM requesters are staff directory ids used for OWN scope; its resolver offers OWN and ORG only | `services/itsm/app/models.py:108-125, 225-245`; `services/itsm/app/scope.py:1-42` |
| S40 | Memory: ten sellable profiles cost 3747 MB (97.0% of the 3862 MB reference), ten plus `admin` 3944 MB (102.1%); `kinds.api` 98, `kinds.api_with_worker` 99; customer, finance, notification and approval already fold a consumer; deal, lead, support, marketing, storefront and operations are budgeted as `api` | `python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles …` (run read-only at HEAD); `infrastructure/edge/memory-budget.json` |
| S41 | Ops-monitor watches `customer.events` for the `crm` profile only; its `EDGE_RELAY_SCHEMAS` default omits `lead` while edge-relay's includes it | `libs/platform-core/src/platform_core/ops_monitor/config.py:62-109`; `infrastructure/edge/compose.prod.yml:791` against `:851` |
| S42 | The cross-product frontend is thin: finance's nav says "Customers" for billed parties, finance money helpers are an INR-only copy, the dashboard's "Invoices" page is OpsGrids billing, `useGlobalSearch` has no caller, the Engage 360 panel is the only 360 consumer | `frontend/product-finance/src/ui/useNavGroups.ts:28`; `frontend/product-finance/src/lib/money.ts:1-47`; `frontend/opsgrids-dashboard/src/routes/Invoices.tsx:178`; `frontend/opsgrids-client/src/platform/useGlobalSearch.ts:1-30`; `frontend/product-engage/src/routes/inbox/Customer360Panel.tsx:1-30` |
| S43 | The insights service is four unmerged lines of work: `sprint/insights` (ADR-0020 slice I1: scaffold and edge wiring `35e0ecd3`), `-q1` (finance facts `d543211a`), `-q2` (deal, lead, company facts `a4b248f1`) and `-q3` (operations, projects, storefront facts `93265864`); q2 is not an ancestor of q3, and their three migrations all have `down_revision = '5cead06f3d54'` (three heads once merged). q2's CRM facts test the literal `closed_won` and `closed_lost` keys and store deal and company names; its default `lead_service_url` is `:8110`, the auth service's port; the compose block on the branch sets only `ACCOUNT_SERVICE_URL`; no branch holds any frontend code for it; ADR-0020 prices it at +99 MB in the core profile | `git log main..<branch>` for each; `git show d543211a:services/insights/migrations/versions/048dbce2f957_add_finance_fact_tables.py`, `a4b248f1:…/ad6f07c940d8_deal_lead_company_facts.py`, `93265864:…/a7c4e1f9b3d2_i5_i6_i7_facts.py`; `a4b248f1:services/insights/app/models.py:164-169, 210` and `app/config.py`; `services/app-gateway/routes.json:52-53, 81`; `docs/insights/metrics.md` §4.2; `docs/adr/0020-insights-projection.md:85-100, 682` |
| S44 | Approval's three guard triggers compare **explicit column tuples**: the step guard freezes `approver_employee_id` and the rest while allowing only the closing `UPDATE`; the request guard freezes `requester_employee_id` and the rest while pending, and after a decision allows only the write-once `application_*` trio but compares `decided_by_employee_id`. A column outside a tuple stays mutable by the one `UPDATE` each guard permits | `services/approval/migrations/versions/c8d3e6f4a1b2_guard_triggers.py:39-64, 97-146, 149-186` |
| S45 | `operations.events` is a live queue (the in-API consumers are on by default, `RUN_CONSUMERS_IN_API` true in `x-service-env`) but **unwatched**: `PROFILE_DLQ_QUEUES` names `crm`, `hrms`, `finance` and `admin` only, no `*-worker` rollback container exists for operations, and its budget kind is `api`; `test_ops_monitor_dlq_queue_sync.py` derives its workers from compose in both directions, so a queue cannot be listed without its worker (registry EV55 records the queue: R4.3 gives operations the fold set of F5, F7 and F8 together with the erase handler of EV18) | `libs/platform-core/src/platform_core/ops_monitor/config.py:101-109`; `infrastructure/edge/compose.prod.yml:94, 923-1590` (the `*-worker` services run `account-worker` to `finance-worker`, none for operations); `services/operations/app/config.py:41`; `services/operations/app/consumers.py:81`; `infrastructure/edge/memory-budget.json` (`containers.operations`); `tests/infra/test_ops_monitor_dlq_queue_sync.py:1-12` |
| S46 | A queue binding is durable and outlives the image that declared it, but a handler does not: `EventConsumer.run` binds the keys of **its own** handlers, and `_handle` loops over `self._handlers.get(event_type, [])`, which is empty for a key the running image never knew, then acknowledges. After a code rollback every event of a newer key is silently discarded, and a published outbox row cannot be replayed | `libs/platform-core/src/platform_core/events.py:470-471, 492-494`; `tools/outbox.py` |
| S47 | Cross-tenant discovery by a sweep reads on the narrow outbox role, and its column grant is maintained per service in `migrations/env.py`: finance `_SWEEP_READER_COLUMNS` and operations `RECONCILE_DISCOVERY_TABLES` (`REVOKE ALL`, then `GRANT SELECT (columns)` for each table that exists, guarded on `pg_roles`, on every run). Operations' comment records the 2026-10-01 incident of two sweeps that failed every tick for want of a grant; customer has no such grant | `services/finance/migrations/env.py:36-76, 78-130`; `services/operations/migrations/env.py:96-115, 126-157`; `services/customer/migrations/env.py:1-80` (no grant) |
| S48 | Finance's payment ledger is "append-only" by convention in the model docstring; no trigger guards `payments` (the append-only triggers are on the GST evidence tables), and `record_payment` does not require the allocated invoices to share a currency | `services/finance/app/models.py:11-20`; `services/finance/migrations/versions/9c41d0b7e5a2_gst_tables_rls_and_append_only.py`; `services/finance/app/services.py:1295-1430` |
| S49 | `audit_logs.before` and `.after` are `JSONB` columns whose Python `None` persists as **JSON `null`, not SQL NULL** (`JSONB.none_as_null` is false, so `should_evaluate_none` is true: checked offline with the repo's SQLAlchemy 2.0.52), and `record_audit` passes an explicit `None` for an absent snapshot. A `contact.created` row therefore stores `before = 'null'::jsonb` beside an `after` that holds the e-mail and name | `libs/platform-core/src/platform_core/audit.py:75-78, 177-178`; `services/customer/app/services.py:681-687` |
| S50 | `resolve_settings` answers `source` `warehouse`, `organization` or `default`; with no `fulfilment_settings` row it returns the hard-coded default (`default_tax_rate_pct` 0, `invoice_timing` `on_accept`, `source` `default`), which is what an organization that never set a rate gets | `services/operations/app/orders/selectors.py:310-363, 366-411` |
| S51 | Customer's internal routes carry a caller allow-list and an organization-claim check but **no entitlement check** (the module has none); product-service's entitlement read has no caller allow-list and answers for any organization on the service token alone. The customer process is profile-wide, so a box that sells `crm` to one organization serves every organization's call | `services/customer/app/api/internal.py:95-121` and the whole module; `services/product/app/api/internal.py:57-79`; `infrastructure/edge/compose.prod.yml:1424-1448` |
| S52 | `SUBSCRIBABLE_EVENTS` holds no storefront key and not `finance.orders.invoice.issued`, although their committed schemas exist; the Engage recipe `enquiry-follow-up` already triggers on `storefront.enquiry.created`, so the builder flags it today and it stays broken | `services/notification/app/domain.py:98-227`; `libs/contracts/events/storefront.enquiry.created`, `storefront.enquiry.status_changed`, `finance.orders.invoice.issued`; `frontend/product-engage/src/routes/automations/recipes.ts:51-55` |
| S53 | Support's `ticket.replied` payload is `{ticket_id, number, message_ref, author_type, internal}`: **no contact id**, so a tenant rule cannot address the customer from it today. Rules have no conditions, and `recipient_contact_path` is a free dotted path into any payload, so any key that gains a contact id becomes a way to message that contact | `services/support/app/services.py:620-646`; `services/notification/app/services.py:682-760` (`_recipient_for`) |
| S54 | Engage e-mail templates have no category (`code`, `channel`, `subject`, `body`); a purpose is derived from a provider template's registration for WhatsApp only (`purpose_for_send`), so for e-mail nothing on the server says what a message is | `services/notification/app/models.py:72-100`; `services/notification/app/consent.py:337-355` |
| S55 | The unmerged insights q2 export routes serialize **names and closed vocabularies**: `DealExportRowOut` has `name` and `stage: DealStage` (the six-value literal), `CompanyExportRowOut` has `name`, `LeadExportRowOut` types `source` as the three-value `LeadSource` (a custom stage, or a lead from R3.7's new sources, would fail to serialize) | `git show a4b248f1:services/deal/app/schemas.py:37, 297-317`; `git show a4b248f1:services/customer/app/schemas.py:341-354`; `git show a4b248f1:services/lead/app/schemas.py:155-171` |

## 4. The connection model shared by every release

### 4.1 The party-link convention

- **Party = `customer.companies.id`, person = `customer.contacts.id`, rep = an `account` membership id**
  (P§4.2). An individual stays a one-person company, the convention `lead` already uses
  (`services/lead/app/domain.py:159-177` names the company after the person when none is given).
- **Columns.** In another product's schema the link is `crm_company_id` and `crm_contact_id`, both
  `UUID NULL`, each with an index that leads with `organization_id` (`ix_<table>_org_crm_company`,
  `ix_<table>_org_crm_contact`), no foreign key (ADR-0000 §10), the name `customer_id` never reused for
  a new column. A product that links a lead uses `crm_lead_id` the same way (B51).
- **Written** at creation, or by one explicit link action, after the id has been proved through C50 or
  C52 (SD-2). **Never rewritten** to a different party on a frozen or legal record (an invoice, an accepted
  order) except by the owner-run link tool (H2); there is no merge tool that could rewrite one (a
  duplicate merge is out of scope in SP3 §15). A mutable working record (a support
  ticket) may be re-linked by an audited, re-proved write (§9.3). **Cleared** only by the erase handler of
  §6.5 or by the owner's remedy after an orphan report.
- **A display snapshot stays beside it** (SD-3). The snapshot is what an issued invoice, an accepted
  order or a ticket shows when the CRM record is renamed, erased or absent.
- **No placeholder.** A link that cannot be resolved stays `NULL` and the product records why in its own
  audit row (`reason = crm_unavailable`, `crm_not_found`, `crm_ambiguous`); it never invents a party.
- **Events** carry the same names as fields (`crm_company_id`, `crm_contact_id`, `owner_membership_id`)
  and nothing else about the party (§4.3).

### 4.2 Resolution routes and who may call them

Every route below is internal: mounted at `/internal/v1/organizations/{organization_id}/...`,
`include_in_schema=False`, service token addressed to the callee, a per-route caller allow-list
(`_require_caller`, `services/customer/app/api/internal.py:95-107`) and the org-claim check
(`_require_org_claim`, `:110-121`), a foreign or deleted id answered 404, and every call made in the
claim/call/record shape with no transaction open (`docs/io-outside-transactions.md`,
`tests/infra/test_io_outside_transaction.py`). Each route ships three negative tests (no token 401,
wrong caller 403, wrong organization claim 403) and one cross-tenant test.

| Handle | Route | Callers | Answers | Release |
|---|---|---|---|---|
| C45 | customer `GET .../contacts/{contact_id}/messaging-target?channel=` | notification | address on `whatsapp`, `sms`, and from R4.1 `email` (`MessagingTargetOut` gains `email`) | R4.1 |
| C46 | customer `POST .../contacts:resolve` | notification, lead | by `phone` or `email`: `contact_id`; from R4.2 also `company_id`, `owner_membership_id` | R4.2 |
| C47 | customer `GET .../contacts/{contact_id}` | finance | name, email (nullable from R3.1), company | exists |
| C50 | customer `GET .../companies/{company_id}` | finance, operations, project, projects, support, storefront, deal | business facts only: name, legal name, status, owner membership, GSTINs, billing address | R4.1 |
| C89 | customer `GET .../contacts/{contact_id}/identity` (**SP4 addition**) | support, operations, deal, notification | ids only: `{contact_id, company_id, owner_membership_id, erased}`; 404 for foreign, deleted or erased | R4.1 |
| C51 | customer `POST .../parties:ensure` | **operations only** (SD-7: no other product creates a party; the first-draft registry also listed finance, storefront and notification, which registry C51 and J29 drop) | `{company_id, contact_id, company_created, contact_created}` | R4.1 |
| C52 | customer `POST .../parties:resolve` | finance, operations, storefront, notification, support, project, projects, lead | `{contact_id, company_id, owner_membership_id, ambiguous}` | R4.1 |
| C53 | customer `GET .../contacts:export` | **notification** for full rows; finance, support, lead, operations, storefront and deal **only with `erased_only=true`** (else 403 `erased_only_required`) for the one-off erase catch-up | full rows (notification): keyset rows with `email` (null when phone-only) and `erased_at`, no phone. With `erased_only=true` (every other caller): `{contact_id, company_id, erased_at, updated_at}` and nothing else, no name and no e-mail. **SP4 adds** the query parameters `ids` (comma list, at most 100, notification only) and `erased_only` (registry C53) | R4.1 |
| C58 | notification `POST .../consent-events` | **storefront and lead only** (the first-draft registry also listed customer and support, which registry C58 and J29 drop: only those two own a capture surface, SD-11); `source` **`web_form` only** (§6.3) | `{recorded, effective}`; reuses `record_consent_event` | R4.1 (contract) |
| C59 | notification `POST /internal/v1/contacts:summarize-messaging` | customer | per contact `ChannelsOut` or `null` | R4.2 |
| C61 | finance `POST .../customers:resolve-for-crm-company` | operations, customer | `{customer_id, created}` | R4.3 |
| C62 | finance `GET .../customers/credit-position?crm_company_id=` | operations | per-currency limit, outstanding, overdue | R4.3 |
| C63 | finance `GET .../invoices:export`, `.../payments:export`; operations `GET .../orders:export` | customer | keyset state rows for the projection backfill | R4.3 |
| C64 | operations `orders:create-from-quote`; finance `invoices:create-from-quote`; project and projects `projects:create-from-deal` | deal | `{id}` of the created record, idempotent on the quote or deal id | R4.4, R4.5 |
| C78 | deal `GET .../quotes/{quote_id}/approval-summary` | approval | numbers and the rep's `discount_reason` | R4.5 |
| C79 | support `GET .../tickets/{ticket_id}/messages/{message_ref}` | notification | an agent's non-internal reply body, read at send time | R4.4 |
| C80 | operations `GET .../inventory-items:search` | deal, and **SP4 adds** storefront (with an `ids` parameter) | trimmed item list, no stock, no cost | R4.5 |
| C104 | **SP4 addition.** `GET .../crm-references?kind=&after=&limit=` on finance, operations, notification, support, project, projects, storefront, deal, lead | customer | keyset page of `{kind, id, source}` for the nightly orphan check | R4.7 (built), design in R4.1 |
| C105 | **SP4 addition.** payment `GET .../collections:export-captured?after_updated_at=&after_id=&limit=&since=` | finance | keyset rows `{charge_id, reference_type, reference_id, amount_minor, currency, provider, captured_at, refunded_total_minor, updated_at}` for merchant charges in state `captured`; no payer and no provider payment id. The catch-up for lost capture events (§8.4.4, §4.7) | R4.3 |

**Entitlement re-check on customer's party routes.** An internal route sits behind no gateway entitlement
gate, the customer process is profile-wide (S51) and the caller allow-lists above prove only which
*service* called. So C50, C51, C52, C53 and C89 also re-check that the organization is entitled to `crm`
through product-service's entitlement read (the shared `PRODUCT_SERVICE_URL`, new settings
`product_service_url` and `product_timeout_seconds: float = 5.0` on customer, a 60-second in-process
cache, the pattern of lead's capture check, registry L12 and W36) and answer **403 `crm_not_active`**
otherwise (503 `entitlement_unavailable` when product-service cannot answer: it fails closed, and the caller
records `crm_unavailable`). Without it an organization that never bought CRM could create master data through
C51 (`create_crm_party_on_order`) and read through C50 and C52. Each of the five routes ships an
unentitled-organization test beside its three negative tests; W36 lists `customer` as a caller (registry W36).

### 4.3 Event enrichment: one rule, one table

**The rule** (registry §D): additive within `v1`; the payload change and its committed schema
(`additionalProperties: false`, `$id opsgrids://contracts/<type>/v1`) land in one commit; a
producer-side test builds the payload through the real write path and calls
`platform_core.events.validate_event_schema` (`libs/platform-core/src/platform_core/events.py:203-224`);
payloads carry ids and enumerations only; `owner_membership_id` is `null` until the owner is mapped and
equals `owner_id` after R2.7; a consumer deploys **before** the behaviour change of its producer, because
an event with no bound queue is confirmed by the broker and dropped
(`events.py:250-262`; `tools/outbox.py` cannot replay a published row).

**The table** lists only what SP4 changes. "Gap" means the committed schema already requires fields the
producer does not emit, so the producer-side test cannot pass until the same commit closes the gap.

| Event (producer) | Release | Change | Notes |
|---|---|---|---|
| EV1 to EV3 `crm.book.company.*` (customer) | R4.1 | `created_at` (gap), state after the patch on `.updated`, then `crm_company_id`, `owner_membership_id` | The legacy `company_id`, `owner_id` stay (frozen) |
| EV4 `crm.book.contact.*` (customer) | R1.2b schemas (EV4 and EV5 contracts), R4.1 ids | `crm_contact_id`, `crm_company_id`, `owner_membership_id`; `.deleted` gets `crm_contact_id` | `full_name` on `.created` is legacy and frozen (J12); an erased contact emits `"Erased contact"` |
| EV5 `crm.pipeline.activity.logged` (deal) | R1.2b schema, R4.1 ids | `crm_company_id`, `crm_contact_id`, `direction`, `owner_membership_id` | Never the subject or body |
| EV6 to EV9 `crm.pipeline.deal.*` (deal) | R2.3 full state; R4.1 ids | `crm_company_id`, `crm_contact_id` (primary contact), `owner_membership_id`; `.deleted` gets `crm_company_id` | `name` is frozen legacy and, for a lead-converted deal, embeds a person's name (S13): new consumers must not read it |
| EV10 to EV12 `crm.lead.*` (lead) | R3.7 gap; R4.1 ids | `owner_membership_id`; `.converted` gets `crm_company_id`, `crm_contact_id` | |
| EV18 `crm.book.contact.erased` (customer) | R4.1 | new | not subscribable |
| EV32 `finance.billing.customer.created/updated/archived` | R4.1 schemas, R4.3 fields | schemas committed from what is emitted (`.updated` carries `before` and `after` objects: `services/finance/app/services.py:434-448`); R4.3 adds `crm_company_id`, `crm_contact_id`, `gstin_present` | the `before`/`after` blobs carry `contact_name` and `email` today (`services/finance/app/services.py:384-448`). No consumer binds `.updated` and it is not subscribable, so R4.1 **reduces** it to `{customer_id, changed[]}` and commits that shape; `.created` keeps its `name` as a legacy, subscribable, frozen field (like `full_name` in J12) that new consumers must not read |
| EV33 `finance.billing.invoice.issued/paid`, `payment.received` | **R4.1 closes the gap**; R4.3 adds `crm_company_id` | R4.1 emits `issued_date`, `due_date`, `subtotal_minor`, `tax_minor` (issued), `currency`, `paid_date` (paid), `received_date`, `method`, `currency` (payment), plus `crm_contact_id` from the existing finance column and `crm_company_id: null`; R4.3 fills `crm_company_id` | The registry's §D intro assigns the gap closure of deal, customer and lead events and, with it, finance's (EV33): the producer-side test cannot pass without it, so R4.1 owns it. The committed `payment.received` carries one `currency` and `record_payment` does not check that the allocated invoices share one (S48), so R4.1 also makes it refuse an allocation set that spans currencies (422 `mixed_currency_payment`) |
| EV34 `operations.orders.order.*` | R4.3 | `crm_company_id`, `crm_contact_id`, `channel`, `finance_customer_id`; `source_type`, `source_id` from R4.5 | No new `delivered` event: `handed_over` is delivery (ADR-0025 §1) |
| EV38 `payment.collections.payment.captured/refunded` | R4.3, R4.5 | `crm_company_id`, `crm_contact_id` once the reference carries them; `reference_type` enum gains `finance.invoice` in R4.5 | |
| EV42 `finance.orders.invoice.issued` | R4.3 | joins `SUBSCRIBABLE_EVENTS` (one line in `services/notification/app/domain.py`, S52); its committed schema is unchanged | the payload is ids, an invoice number and a total (`libs/contracts/events/finance.orders.invoice.issued/v1.schema.json`); notification deploys before the chain flips (§8.8) |
| EV30 `engage.inbox.conversation.touched` (notification) | R4.2 | new: `crm_contact_id`, direction, channel, occurred_at; **SP4 adds** `crm_company_id`, `owner_membership_id` (nullable) so deal can write the activity without a read | `deal.events` binds it in R4.2 (§7.5); `customer.events` binds it in R4.3 for the `conversation_touched` timeline kind (§8.5), so a touch published before R4.3 never reaches the timeline |
| EV35 `support.desk.ticket.*` | R4.4 | `requester_kind` and `assignee_ref` on all five; **`crm_contact_id` and `crm_company_id` only on `.created`, `.status_changed` and `.agent_reply_sent`**; `.status_changed` gets `solved_at`, `sla_breached`; new `.agent_reply_sent` | the reply text never rides the event (C79). `.replied`, `.assigned` and `.updated` carry **no** CRM id: rules have no conditions and `recipient_contact_path` is a free path into any payload, so an id there would let a tenant rule e-mail or WhatsApp the customer about an agent's internal note, an assignment or a tag edit (S53, §9.4) |
| EV36 project created/updated (both products) | R4.4 | `crm_company_id`, `crm_deal_id`, `client` (a business label) | same field names in both products |
| EV37 `storefront.enquiry.*` | R4.7 | `status` and `lines` (gap), `.status_changed` produced; **both keys join `SUBSCRIBABLE_EVENTS`** (EV42) in the same commits as their producer tests | no buyer email or phone; links live on the enquiry row (B51); notification deploys before storefront's producer change (§12.10) |
| EV25 `crm.book.owner.deactivated` (customer) | R4.7 | `reason` enum is `membership_revoked`, `employee_exited`, `manual` (**SP4 adds `employee_exited`**, SD-17) | |

### 4.4 The scope rule for a cross-product section

A rep with OWN scope on companies must not read another product's data through the CRM that the rep could
not read in that product, and CRM scope must not narrow what a partner-product grant allows. The rule has
two tiers (SD-6), and every section of the 360 and of the company page falls in exactly one.

| Section | Source | Gate | Absent or denied |
|---|---|---|---|
| `crm` (open deals) | deal list **as the viewer** (forwarded identity headers, `/api/v1` mount) | `crm.pipeline.deal.read` at the viewer's own scope, evaluated by deal | `null`, listed in `unavailable` |
| `support` (tickets) | support list `GET /support/tickets?crm_contact_id=`/`crm_company_id=` **as the viewer** | `support.desk.ticket.read` (ORG floor) evaluated by support | `null`; the partner product refuses, CRM never substitutes its own scope |
| `delivery` (projects) | PulseBoard and Projects lists `?crm_company_id=` **as the viewer** | `pulseboard.engagements.project.read` / `projects.portfolio.project.read` at the viewer's scope | `null` per product |
| `channels` (consent standing) | notification messaging summary, service token with `org` and `sub` claims, **facts only, never an address** (exists: `services/customer/app/c1_360.py:182-206`) | `crm.book.contact.read` plus the `engage` entitlement; unchanged | `null` |
| `orders`, `finance`, money tiles, company receivables | `party_documents`, `party_payments`, `party_rollup` (CRM-held projection of finance and operations events) | `crm.book.company.read_commercial` (E23) plus `assert_can_touch` on the company's owner | `null`, "Not available" |
| merged timeline entries that come from another product (`ticket_*`, `conversation_touched`, `project_started`, `quote_*`, `invoice_*`, `payment_*`, `order_*`) | `party_timeline` (PII-free, B16) | `crm.book.company.read_commercial` | the viewer sees CRM-native activity only |

The projection tier is the one place CRM scope decides the visibility of another product's facts. It is
safe because the projection holds numbers, dates, ids and document numbers only, is labelled "as of" (the
`as_of` column of B15), and is parity-tested against finance's own receivables (§8.7); it exists because
finance's permissions top out at ORG (`services/finance/app/scope.py:1-14`) and a rep can never be given
them.

### 4.5 Entitlement-aware degradation

Entitlement is per product and the `customer` process runs only under the `crm` profile
(`infrastructure/edge/compose.prod.yml:1186-1188`). Every connection is nullable, so each absent product
degrades to "unavailable", never to an error.

| Absent | What the others do |
|---|---|
| CRM (no `crm` entitlement or no customer process) | Products keep their snapshots and null links; `parties:*` and the other customer routes answer **403 `crm_not_active`** for an organization without the entitlement on a box where the customer process runs (§4.2) and 503 or time out where it does not, and the caller records `crm_unavailable` and carries on (an order is still accepted with a null `finance_customer_id`); no CRM consumer exists, so no projection; UI controls that need CRM are hidden by `useEntitlement('crm')` (a hide, never the enforcement); the server refuses a CRM id from an unentitled organization (SD-2) |
| Finance | CRM money sections `null` and named in `unavailable`; the billing-profile tab hidden; quote handoff to invoice refused (C20 entitlement re-check); no capture consumer |
| Operations | Orders section `null`; quote lines are free text (`inventory_item_id` NULL, B29, C87 answers 404); handoff to order refused |
| Support | Support section `null`; "Create ticket" hidden and refused |
| Engage | Send affordances hidden and C60 answers 402; the channels section `null`; consent is still writable through C58 (notification is a platform service); assignment alerts are in-app and unaffected |
| Projects, PulseBoard | Delivery section `null`; the won-deal kickoff refused (`create_project_on_won` has nothing to call) |
| Marketing | Attribution events are dropped for want of a bound queue (consumer-first rule); lead and deal still carry `marketing_campaign_id` unvalidated |
| HRMS | No availability data, so round-robin treats every rep as available; offboarding runs from `core.membership.revoked` only |
| Approval | Moved to the core profile in R4.5 (SD-13), so it is always present after that release; before it, `PUT /deals/settings` refuses a non-null discount threshold with 422 `approval_unavailable` |
| Insights | Dashboards keep today's per-product calls (`frontend/opsgrids-dashboard/src/api/index.ts:379-388`) |

### 4.6 Deploy-order and lost-event rules

1. **Consumer before producer.** Whatever binds a queue deploys, and runs once, before the producer
   starts emitting. R4.1 deploys notification's C58 and customer's routes before any caller.
2. **Schema before payload.** The committed schema and the producer change are one commit; the
   consumer reads the new field defensively (absent means `null`) until the producer has soaked.
3. **Every projection has a backfill.** Events published before a queue existed are gone; each
   projection ships a keyset export route and an owner-run tool (H4, H9, H12, H13; H14 for the erase catch-up)
   and a parity test.
4. **Expand, then contract, in separate releases.** No release in SP4 drops or renames a column or
   narrows a constraint another release reads.
5. **Migrations re-parent.** Whichever of two stacks merges second sets `down_revision` to the
   first's head at a `# RE-PARENT HERE` marker (`services/operations/migrations/versions/d7a3c9e5b184_*.py`).
   Operations in particular shares its head with two unmerged inventory stacks.

### 4.7 Rollback of a consumer

M2 says each release is reversible on its own. For **code and schema** that holds here: every migration is
additive and every previous image tolerates the new columns. For **events** it holds only where a catch-up
exists. A queue binding is durable and outlives the image that declared it, but a handler does not: after a
code rollback `EventConsumer._handle` finds no handler for a newer key, acknowledges the message and drops it
(S46), and a published outbox row cannot be replayed (`tools/outbox.py`). So:

1. **Every release states, per consumer, what a code rollback loses.** The table in each release's rollback
   section names the consumer, the events a rollback discards, and one of three answers: a **catch-up** that
   rebuilds the state from the producer (H4, H9, H12, H13, H14, the finance `reconcile_online_captures` sweep
   over C105, the storefront `:send-to-crm`); **forward-only**, a derived convenience whose loss is stated
   together with why it is safe (a timeline line, a bell alert, a system activity, a status the next event
   repairs); or **stop**, where neither is acceptable and a stop condition forbids the rollback.
2. **Prefer moving the consumer to rolling the image back.** When the fault is where a consumer runs (memory,
   the API process) and not what it does, set `RUN_CONSUMERS_IN_API=false` and add `split-workers` to the box
   profiles: the same new image keeps consuming in its `*-worker` container, so nothing is lost. It costs about
   90 MB per worker while it runs, an owner decision taken with D-13.
3. **Do not roll back the finance, deal, customer, storefront or notification image while its queue carries
   live traffic** unless the catch-up named in its table is ready to run the moment the image returns, and
   never roll back approval or deal while a quote is in `pending_approval` (§10.9). Each release's stop
   conditions repeat the rule for its own services.
4. The first lever of every release stays configuration (a mode or a setting), which loses nothing.

## 5. Product-by-product connection design

Effort uses the scale in the header. "Release" is where the connection ships.

### 5.1 Summary

| Product | Party records today | Connection built | Release | Effort / risk |
|---|---|---|---|---|
| CRM | companies, contacts, leads, deals, activities | the hub: ensure, resolve, export, erase, projections, 360 | R4.1 to R4.7 | XL / high |
| Engage (notification) | `notification.contacts`, conversations, consent ledger | projection of CRM contacts, consent writer and visibility, CRM sends, WhatsApp activity | R4.1, R4.2 | XL / high |
| Finance | `finance.customers` (billed party) | billing profile keyed on company, GSTIN profiles, terms, credit, captures, link tool | R4.3 | XL / high |
| Operations (incl. inventory) | order name and phone snapshot, `customer_id`, suppliers, `inventory_items` | crm ids on orders, credit check, order to invoice, item master for quotes | R4.3, R4.5 | L / high |
| Support | ticket requester strings | contact and company links, 360 section, notifications, ticket from conversation | R4.4 | M / medium |
| PulseBoard (`project`) | `client` free text | company link, won-deal draft project, delivery card | R4.4 | M / medium |
| Projects (`projects`) | `client` free text | company link, delivery card; kickoff after D-17 | R4.4 | S–M / low |
| Marketing | hand-typed campaign numbers | attribution projection, Engage reads | R4.6 | L / medium |
| Storefront | `enquiries`, `products` | enquiry to lead with consent, derived status, item mapping | R4.5, R4.7 | L / medium-high |
| HRMS | employees, hierarchy, leave | offboarding hooks, availability | R4.7 | M / medium |
| ITSM | staff requesters | **not connected** (§5.4) | R4.7 record | S / none |

### 5.2 Per-product design

#### CRM (customer, deal, lead)

| Aspect | Design |
|---|---|
| Party records | `customer.companies`, `contacts`, `owners`; `lead.leads`; `deal.deals`, `deal.activities` (S4, S11, S13) |
| Link columns | It is the authority. New: `customer.contacts.erased_at` (B12), `customer.companies.lifecycle_promoted_at` (B81), `customer.customer_settings` (B83), `deal.deals.marketing_campaign_id` and UTM (B30), lead equivalents (B32) |
| Internal routes | customer C45 (email), C46 (ids), C50, C51, C52, C53, C89 (the five new ones re-check the `crm` entitlement, §4.2); public C29 erase, C30 lookup-channels, C24 company 360, C106 settings; deal C20 handoff, C78; lead C56 capture |
| Events | EV1 to EV12 enriched (§4.3), EV16, EV17, EV18, EV22 to EV25 |
| Consumers and projections | customer: one projection handler per event into `party_documents`, `party_payments`, `party_rollup`, `party_timeline`, plus lifecycle (R4.3 to R4.5, only for an organization whose setting allows it: D-34), owner deactivation (R4.7); deal: conversation activity, automation task, quote expiry, approval decision, won-deal kickoff, erase, owner deactivated; lead: erase, availability, `owner_team` |
| Degradation | the hub; a CRM-only organization sees partner sections `null` |
| Scope rule | CRM scope for CRM data; the projection tier of §4.4 for other products' facts |
| Consent and erasure | customer is the erasing authority (C29); CRM stores and decides no consent (SD-11) |
| Deploy order | R4.1 customer routes and notification C58 first; each later release's consumers before its producers |
| Effort and risk | XL; high: the erase, the projection's money parity and the lifecycle automation change a headline metric (the dashboard's "Total Customers" counts `status = customer`: `frontend/opsgrids-dashboard/src/api/index.ts:382`) |

#### Engage (notification)

| Aspect | Design |
|---|---|
| Party records | `notification.contacts` (sealed email, hard-deleted, S24), `conversations`, `consent_events`, `messages` (plaintext `to_address`) |
| Link columns | `contacts.crm_contact_id` and `origin` (B47); `conversations.crm_contact_id` (B48, renamed with alias), plus **SP4** `crm_company_id` and `owner_membership_id`; **SP4** `messages.crm_contact_id` and `entity_ref` (B67); `campaigns.marketing_campaign_id` (B59) |
| Internal routes | serves C58, C59, C85; public C60 send, C72 feed, C84 contact routes; calls C45, C46, C52, C53 |
| Events | emits EV30; consumes EV4, EV18, EV15 to EV17 (rules); subscribable keys EV39 to EV42 |
| Consumers and projections | projection of CRM contacts for CRM-entitled organizations (thin triggers, state by C53), erase handler, assignment rules |
| Degradation | Engage-only organizations are unchanged (every contact is `manual`, `import` or `signup`); a CRM-only organization has no send affordance and in-app alerts still work |
| Scope rule | Engage permissions are ORG-only; a CRM view of messaging shows facts through the `channels` section, sends require an Engage grant (D-37) |
| Consent and erasure | the ledger is authoritative; the projection writes **no** consent event and never touches `suppressions`; erase handler in §6.5 and the audit scrub in §7.2 |
| Deploy order | notification handlers (R4.2) before customer's `contacts:lookup-channels` and before deal's conversation consumer; EV30 emitted last |
| Effort and risk | XL; high: consent semantics, duplicates, erasure fan-out (the discovery's highest-risk item) |

#### Finance

| Aspect | Design |
|---|---|
| Party records | `finance.customers` (billed party: name, contact name, email, GSTIN, city, state, three withholding flags) |
| Link columns | `customers.crm_company_id` (B41) and the existing `crm_contact_id` (B42, the billing contact on the primary profile), `payment_terms_days`, `credit_limit_minor`, `credit_limit_currency` (B43); `invoices.crm_company_id`, `source_type`, `source_id` (B58) and **SP4** `buyer_snapshot`; `payments.currency` (B44, **SP4** makes it nullable, §8.2); **SP4** `online_captures` (B82) |
| Internal routes | serves C61, C62, C63; calls C47, C50, C52, C105 (payment's capture export) |
| Events | EV32, EV33 (R4.1 gap closure and schemas; R4.3 company id); consumes EV38 `captured` and `refunded` (EV47); `finance.orders.invoice.issued` becomes subscribable (EV42) |
| Consumers and projections | capture consumer (§8.4); order-invoice consumer gains derived supply type, place of supply, due date and `hsn_sac`; erase handler (R4.3) |
| Degradation | CRM absent: profiles stay standalone (`crm_company_id` NULL); the finance form hides the company combobox |
| Scope rule | Finance is ORG-only; a CRM rep sees receivables through the projection tier, never through a finance grant |
| Consent and erasure | an issued invoice is a legal document and stays (D-27); on erase finance clears `contact_name` and `email` and the `crm_contact_id` link on profiles tied to the contact, rewrites their audit copies with `finance.scrub_customer_audit` (§8.2) and keeps the billed party's name and GSTIN |
| Deploy order | R4.1 schemas and gap closure; R4.3: customer projection consumer first, then finance fields and routes, then notification's one subscribable key, then operations wiring |
| Effort and risk | XL; high: first production order invoices, e-invoice auto-filing, online captures, a second copy of receivables |

#### Operations (including inventory)

| Aspect | Design |
|---|---|
| Party records | `orders.customer_id` (a CRM contact id in practice), `customer_name`, plaintext `customer_phone` (S19); `suppliers` (vendor side); `inventory_items` |
| Link columns | `orders.crm_company_id`, `crm_contact_id`, `finance_customer_id` (B45), `source_type`, `source_id` (B46, R4.5); **SP4** `fulfilment_settings.create_crm_party_on_order` and `order_invoice_chain_enabled` (the per-organization opt-in of SD-8) |
| Internal routes | serves C80, C64 `orders:create-from-quote`, C63 `orders:export`, C104; calls C50, C51 (the one caller, SD-7), C52 (a suggestion only, SD-28), C61, C62, C89; public C107 `:confirm-crm-link` |
| Events | EV34 enriched; `invoice_requested` lines gain `hsn_sac` (**SP4**, additive) |
| Consumers and projections | capture consumer unchanged (sets `paid_at`); erase handler overwrites `customer_name` and `customer_phone` on orders of the erased contact (**SP4** adds operations as an EV18 consumer, registry EV18 and EV55) |
| Degradation | CRM or finance absent: an order is accepted with null ids; the invoice chain stays in its visibly-logged skip |
| Scope rule | order permissions are ORG; the 360 orders section is the projection tier |
| Consent and erasure | order commercial facts stay; name and phone snapshot are identifiers and are overwritten |
| Deploy order | customer projection and finance routes before operations enables `ORDER_INVOICE_CHAIN_MODE` |
| Effort and risk | L; high (money, tax, stock reservation untouched) |

#### Support

| Aspect | Design |
|---|---|
| Party records | `tickets.requester_name`, `requester_email`, `requester_company`, `requester_kind` (S30) |
| Link columns | `tickets.crm_contact_id`, `crm_company_id`, `conversation_id` (B49); `channel` gains `whatsapp` (no DDL) |
| Internal routes | serves C79; calls C50, C52 (resolve a requester by e-mail, never create), C58 (not used: no consent surface, SD-11); public filters and body C65 |
| Events | EV35 enriched and committed; new `.agent_reply_sent` |
| Consumers and projections | first handler: erase anonymiser (EV48, fold F15) |
| Degradation | CRM absent: tickets work on the snapshot; the picker is hidden |
| Scope rule | both ticket permissions floor at ORG; the 360 support section is read as the viewer |
| Consent and erasure | on erase: requester name and e-mail overwritten, customer-authored message author names overwritten, message bodies retained (D-27) |
| Deploy order | support fold and handler first, then the producer fields, then notification rules |
| Effort and risk | M; medium (wrong auto-links: match only an unambiguous e-mail) |

#### PulseBoard (`project`) and Projects (`projects`)

| Aspect | PulseBoard | Projects |
|---|---|---|
| Party records | `projects.client` free text, `currency` default USD (S31) | `projects.client` free text, `currency` default INR |
| Link columns | `crm_company_id`, `crm_deal_id` (B50) | the same two columns |
| Internal routes | `projects:create-from-deal` (C64), C104 | the same route name, built after D-17 |
| Events | `pulseboard.engagements.project.created/updated` gain `crm_company_id`, `crm_deal_id`, `client` | `projects.portfolio.project.created/updated` gain the same |
| Consumers and projections | none; delivery card by public list `?crm_company_id=` as the viewer | none |
| Degradation | CRM absent: free text `client`, combobox hidden; kickoff refused | the same |
| Scope rule | project read is OWN/ORG; the section is the live-as-viewer tier | the same |
| Consent and erasure | `client` is a business label, not erased; a one-person company's name is a known residue (§6.5) | the same |
| Deploy order | migration, then producer fields, then customer's `delivery` leg | the same |
| Effort and risk | M; medium: draft status, currency default | S–M; low |

#### Marketing

| Aspect | Design |
|---|---|
| Party records | none; campaign numbers typed by hand (S32) |
| Link columns | inbound: lead and deal carry `marketing_campaign_id` and UTM (B30, B32); outbound: **SP4** `attribution_leads`, `attribution_deals` (B69, B70) and `campaign_attribution` (B53, **SP4** keyed by currency) |
| Internal routes | calls C85 (Engage campaign counts); serves nothing |
| Events | consumes EV10, EV12, EV6, EV8 (EV51) |
| Consumers and projections | first handler: attribution projection (fold F16) |
| Degradation | Marketing absent: the events are dropped; CRM keeps the ids |
| Scope rule | `marketing.analytics.campaign.read` is ORG-only; attribution is Marketing's own data |
| Consent and erasure | no personal data: ids and money only |
| Deploy order | marketing consumer first, then lead and deal producer fields |
| Effort and risk | L; medium (first-touch versus last-touch is a product call, D-36) |

#### Storefront

| Aspect | Design |
|---|---|
| Party records | `enquiries` (name, company, sealed e-mail and phone, GSTIN), `products` (second item master) (S33) |
| Link columns | `enquiries.crm_lead_id`, `crm_contact_id`, `crm_company_id` (B51); `products.inventory_item_id` (B52) |
| Internal routes | calls C56 (lead capture), C52 (resolve; storefront never creates a party, SD-7), C58, C80 (`ids`); public `PATCH /storefront/products/{id}` accepts `inventory_item_id` |
| Events | EV37 completed (`status`, `lines`), `.status_changed` now produced; consumes its own `.created`, `crm.lead.updated`, `crm.lead.converted`, `crm.pipeline.deal.stage_changed` and `crm.book.contact.erased` (EV49 widened by **SP4**, §12.3) |
| Consumers and projections | enquiry-to-lead consumer, converted-lead link, derived status, erase handler (fold F17) |
| Degradation | CRM absent: the editable enquiry status stays; the buyer form still records consent through C58 |
| Scope rule | enquiry permissions ORG-only; displayed status is derived, not gated |
| Consent and erasure | the buyer form gains a consent control; storefront writes the ledger through C58; on erase the enquiry's sealed e-mail, phone and name are overwritten |
| Deploy order | storefront consumer and `crm.lead.*` capture routes first; the buyer form field last |
| Effort and risk | L; medium-high (PII crosses a boundary; marketing consent) |

#### HRMS

| Aspect | Design |
|---|---|
| Party records | none (employees are staff; the link to a CRM rep is the membership's `employee_id`, S37) |
| Link columns | none new; **SP4** `employee_id` on the customer and lead `owner_team` copies (B71) |
| Internal routes | none |
| Events | `hrms.people.employee.transitioned` (to `exited`) and `hrms.time.leave.applied/cancelled` are consumed by CRM; the leave and employee producers close their schema gaps (S38, §12.3) |
| Consumers and projections | customer: owner deactivation; deal: reassignment follow-up; lead: availability table and round-robin skip |
| Degradation | HRMS absent: no availability; offboarding from the membership revoke only |
| Scope rule | CRM reads no employee data; only ids and dates arrive |
| Consent and erasure | not applicable (employee PII is HRMS's own) |
| Deploy order | deal, lead and storefront consumers first, then customer (it produces `crm.book.owner.deactivated`, which deal and lead consume), then the leave and employee producer fields |
| Effort and risk | M; medium |

#### ITSM

Not connected (§5.4).

### 5.3 Platform services

| Service | What SP4 does with it | Release |
|---|---|---|
| account / auth | membership is the rep spine (R2.x, R3.8); R4.5 adds `approval` as a caller of C57 for manager reads; no token or gateway change | R4.5 |
| notification | consent writer, batch summary, CRM sends (the server derives an e-mail's purpose from a template category), in-app alerts with `entity_ref` through SP3's `SYSTEM_IN_APP_RULES` (EV54), projection, erase, and the subscribable keys of EV40 to EV42 in each release | R4.1 to R4.7 |
| scheduler | quote expiry through `schedule_event` and A16 cancel; needs R3.6 (A16, A17, F10) | R4.5 |
| approval | membership requester abstraction, `crm.pipeline.discount` type, request-type labels, moved to the core profile, its decide-once and append-only guard triggers replaced to freeze the new columns; ADR `approvals-for-tenants-without-hrms` filed first (§10.4) | R4.5 |
| document / private store | quote and invoice PDFs, notes attachments, only after R19 **in ADR-0009 Q1's shape** (internal put and `:presign` routes; the unmerged `sprint/private-uploads` contract is a different one, S36, §10.1); `deal` and `finance` join its caller allow-list (C75) | R4.5 |
| payment | merchant collections gain `finance.invoice` and `finance` as a caller; a capture export route (C105) is the catch-up for lost capture events; the platform side is untouched | R4.3, R4.5 |
| billing, subscription, metering, catalog | **not connected** (§5.4) | R4.7 record |
| product | the entitlement read (`services/product/app/api/internal.py:57-79`) is the callee check of C20 and C64 (R4.5) and, from R4.1, the `crm` check of customer's five party routes (§4.2, W36); no change to the product service | R4.1, R4.5 |
| insights, dashboard | merge, adopt `stage_outcome`, tiles, search | R4.6 |

### 5.4 Deliberate non-connections

| Product or service | Why it is not connected | What SP4 does instead |
|---|---|---|
| **ITSM** | Incident and request requesters are internal staff identified by directory ids used for OWN scope (`services/itsm/app/models.py:108-125, 225-245`); linking them to CRM contacts would conflate staff and customers. Its only identity issue (OWN on the membership) is NEW-8 | R4.7 writes DR-2 in `docs/crm.md` (§12.6); ITSM has no `useGlobalSearch` source today (`frontend/opsgrids-client/src/platform/useGlobalSearch.ts:184-295`) and a test pins that none is added |
| **Platform tenant billing** (billing, subscription, metering, catalog, the platform side of payment, console) | The customer in OpsGrids billing is a tenant organization (`services/account/app/models.py:60-67`, `services/billing/app/models.py:83-91`), a different thing from the CRM company | Vocabulary only (§6.7) and DR-3 (§12.6); never attach a CRM party to an organization or a plan |
| **Vendor party** (`operations.suppliers`; finance purchase and IMS documents carrying `supplier_gstin` text) | A supplier is not a customer; merging the two books needs a role model nobody has asked for. The two supplier stores are not even linked to each other: operations' supplier has no GSTIN (`services/operations/app/models.py:97-147`), finance's documents carry one as text (`services/finance/app/models.py:536-537, 593-594`) | R4.7 writes DR-1 (§12.6): separate stores, no CRM id, revisit when partners or resellers must be both (the join key would then be a GSTIN added to the supplier) |
| **Standup, timesheet** | No party record; their only identity change is the PulseBoard roster convergence | none |

## 6. R4.1 Party identity and consent/erasure foundation

### 6.1 Purpose, needs and deploy surface

R4.1 gives every later release four things before any data fans out: the routes that find or make a
party by id without copying it (C50 to C53, C45 with an e-mail channel), events that carry the party
ids with committed schemas (§4.3), the consent writer and the erase (C58, C29, EV18, the ADR of §6.5),
and a vocabulary that stops calling three different things "customer" (§6.7). It adds one
user-visible action (erase) and no new screen. It deploys **no consumer**: EV18 is published with no
bound queue and dropped, which is intended (§4.6); each later consumer's first deploy re-applies earlier
erasures (H14).

**Needs.** R2.7 (so `owner_membership_id` equals `owner_id` and `crm.pipeline.*` ids are membership
ids) and R2.3 (`stage_outcome` on the deal events it enriches). **R3.1 as well**, which the programme's
§10 does not list: C50 reads `company_tax_ids`, C51 creates phone-only contacts and matches by GSTIN,
C52 and C45 read `email_index`, and the registry puts R4.1's `erased_at` after R3.1 in the customer chain
(registry §B.1: "R3.1 … → R3.8 → R4.1"). C45 and C46's caller allow-list (R3.1, after C77 has soaked)
must already be enforced, because R4.1 is the first release to add a caller (`lead`) to C46 and the
first to widen C45. The owner-run email backfill H8 must have run for every organization (registry B10).
This is recorded as deviation 1 in §17.

**Services touched:** customer (migration, routes with their `crm` entitlement re-check, events), deal and
lead (event fields), finance (schemas, the gap closure and the one-currency rule of `record_payment`, no
migration), notification (C58), `libs/contracts`, frontend (erase action, copy), docs (ADR draft, glossary
pointers).

### 6.2 Data model

Customer only. One Alembic revision on top of the customer head at merge (`down_revision` carries a
`# RE-PARENT HERE` comment: R3.1 and R3.8 are in the same chain). It follows the repo's
conventions: `SET LOCAL lock_timeout = '5s'`, named constraints, explicit grants.

```sql
SET LOCAL lock_timeout = '5s';

-- B12: the marker of an anonymised contact. The row id is kept, so deal.primary_contact_id,
-- deal.activities.contact_id, finance.customers.crm_contact_id and notification links still resolve.
ALTER TABLE customer.contacts ADD COLUMN erased_at TIMESTAMPTZ NULL;
CREATE INDEX ix_contacts_org_erased ON customer.contacts (organization_id, erased_at)
    WHERE erased_at IS NOT NULL;                       -- C53 erased_only and H14

-- R3.1's CHECK (B9) refuses a contact with neither e-mail nor phone; an erased contact has neither.
ALTER TABLE customer.contacts DROP CONSTRAINT ck_contacts_has_email_or_phone;
ALTER TABLE customer.contacts ADD CONSTRAINT ck_contacts_has_email_or_phone
    CHECK (erased_at IS NOT NULL OR email IS NOT NULL OR phone_index IS NOT NULL) NOT VALID;
ALTER TABLE customer.contacts VALIDATE CONSTRAINT ck_contacts_has_email_or_phone;
```

Row-level security, the `tenant_isolation` policy and the grants of `customer.contacts` are unchanged
(the table is already enabled, forced and granted); `erased_at` adds no table, so nothing joins
`TENANT_TABLES`. The audit scrub (SD-10) is two immutable helpers and one `SECURITY DEFINER` function:

```sql
CREATE FUNCTION customer.has_pii_keys(j jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT j IS NOT NULL AND jsonb_typeof(j) = 'object'
         AND j ?| ARRAY['email','full_name','title','phone','phone_from_lead',
                        'kept_existing_phone','raw_phone'] $$;
CREATE FUNCTION customer.erase_pii_keys(j jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN j IS NULL OR jsonb_typeof(j) <> 'object' THEN j ELSE   -- JSON null, array, scalar: unchanged
    (SELECT COALESCE(jsonb_object_agg(k, CASE
              WHEN k = 'full_name' THEN to_jsonb('Erased contact'::text)
              WHEN k IN ('email','title','phone','phone_from_lead','kept_existing_phone','raw_phone')
                   THEN to_jsonb('[erased]'::text)
              ELSE v END), '{}'::jsonb)
       FROM jsonb_each(j) AS e(k, v)) END $$;

CREATE FUNCTION customer.scrub_contact_audit(p_organization_id uuid, p_contact_id uuid)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = customer, pg_temp AS $$
DECLARE n integer;
BEGIN
  -- The definer's power is bound to the caller's own tenant: the transaction-local GUC the request set.
  -- IS DISTINCT FROM also refuses a NULL tenant (no GUC), so the function fails closed.
  IF p_organization_id IS DISTINCT FROM customer.current_org() THEN
    RAISE EXCEPTION 'scrub_contact_audit: organization does not match the session tenant'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  WITH touched AS (
    UPDATE customer.audit_logs a
       SET before = customer.erase_pii_keys(a.before),
           after  = customer.erase_pii_keys(a.after)
     WHERE a.organization_id = p_organization_id        -- the app-level org filter, kept
       AND a.entity = 'contact' AND a.entity_id = p_contact_id
       AND (customer.has_pii_keys(a.before) OR customer.has_pii_keys(a.after))
    RETURNING 1)
  SELECT count(*) INTO n FROM touched;
  RETURN n;
END $$;
ALTER FUNCTION customer.scrub_contact_audit(uuid, uuid) OWNER TO opsgrids_admin;   -- BYPASSRLS owner
REVOKE ALL ON FUNCTION customer.scrub_contact_audit(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION customer.scrub_contact_audit(uuid, uuid) TO opsgrids_customer;
```

*Why a function.* `apply_schema_grants` revokes `UPDATE` and `DELETE` on `audit_logs` from the app role
(`libs/platform-core/src/platform_core/db.py:140-146`), so the first-draft registry's "the erase rewrites
the audit rows in the same transaction" (B12) could not run as the app role; B12 now registers this function.
The function can touch only rows of one
`entity = 'contact'` and one id inside one organization, only the fixed key list, and only to a
tombstone; the table itself stays append-only for every other writer. `FORCE ROW LEVEL SECURITY` applies
to a non-bypass owner, so the owner is `opsgrids_admin`, the role the migrations already name
(`services/customer/app/config.py:14`), which owns the schemas and tables and is `BYPASSRLS`
(`docs/adr/0002-row-level-security.md:87`); a test asserts it (§6.8).

*Two guards, each found by reading how the rows are really written.* (1) **Objects only.** `record_audit`
passes an explicit `None` for an absent snapshot and the `JSONB` column stores it as JSON `null`, not SQL NULL
(S49), so every API-created contact has a `contact.created` row with `before = 'null'::jsonb` beside an
`after` that holds the e-mail and name. A helper that tests only `IS NULL` would call `jsonb_each` on that scalar,
which raises `22023` ("cannot call jsonb_each on a non-object"), and because the `UPDATE` evaluates its `SET`
for every row the `WHERE` selects, the whole erase would roll back and C29 would answer 500 for essentially
every contact. Both helpers therefore act only on objects and return JSON `null`, an array or a scalar unchanged.
(2) **The caller's own tenant only.** `SECURITY DEFINER` runs as `opsgrids_admin`, which bypasses row-level
security, and a definer function trusts its arguments, so without the check any code running as the app role
could rewrite another tenant's audit rows by passing that tenant's id and defeat RLS as the net (the platform
rule that the tenant filter is always written and RLS is only the net, in the repository's engineering rules). The
function compares `p_organization_id` with the schema's `current_org()`, the transaction-local GUC
(`services/customer/migrations/versions/d17e94b06c3a_enable_row_level_security.py:60-67`), which a
definer function reads unchanged, and refuses otherwise. The erase route, the notification, finance and lead
handlers and H14 all run with the tenant bound, so the legitimate callers always pass. The notification,
finance and lead copies (§7.2, §8.2, §12.2) carry the same two guards.

**Downgrade.** Drops the function, helpers, index and column, **refusing** (`RuntimeError`) while any
`erased_at IS NOT NULL` row exists unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` (precedent
`services/operations/migrations/versions/d7a3c9e5b184_*.py`), because restoring the stricter CHECK
would reject an erased row. On an empty table it reverses fully, so `make migration-roundtrip` stays green.

No DDL in deal, lead, finance or notification: R4.1 changes their events and one notification route.

### 6.3 Backend contract

All paths are under `/api/v1` when public; internal routes are rooted at
`/internal/v1/organizations/{organization_id}` and excluded from OpenAPI. Every handler declares its
permission; every query is scoped with `ctx.apply()` or the explicit organization filter; every id is
resolved inside the caller's organization.

**Public (customer, gateway prefix `/api/v1/contacts`, product `crm`)**

| Route | Permission, floor | Request | Response | Errors |
|---|---|---|---|---|
| `POST /contacts/{contact_id}:erase` (C29) | `crm.book.contact.erase` (E24, dangerous), `minimum=Scope.ORG`, `amr={"otp","stepup"}`, `Entitled("crm")` | `ContactEraseIn {confirm_contact_id: uuid}` (`extra="forbid"`; must equal the path id) | 200 `ContactEraseOut {contact_id, erased_at, already_erased: bool, audit_rows_scrubbed: int}` | 401; 402 not entitled; 403 missing permission, OWN grant (floor), or no step-up (`Additional authentication required: otp, stepup`); 404 foreign or missing (same body); 422 confirm mismatch. Erasing an erased contact is 200 with `already_erased: true` and writes nothing |
| `PATCH /contacts/{id}` and `PUT /contacts/{id}/phone` on an erased contact | unchanged | | | **409** `contact_erased` (new) |
| `ContactOut` | | gains `erased: bool` (C33) | | |

**Internal, customer** (callers and errors as §4.2; ids only; 404 for a foreign id; **403 `crm_not_active`**
for an organization without the `crm` entitlement on C50, C51, C52, C53 and C89, §4.2)

| Handle | Route and body | Rules |
|---|---|---|
| C50 | `GET .../companies/{company_id}` → `CompanySummaryOut {company_id, name, legal_name, status, owner_membership_id, tax_ids: [{kind, value, state_code, is_primary}], billing_address: {line1, line2, city, state_code, state, postal_code, country} \| null}` | business facts only, no contact data; a soft-deleted company is 404; `tax_ids` and `billing_address` are empty and `null` until R3.1's tables exist |
| C51 | `POST .../parties:ensure` `{company: {company_id?, name, legal_name?, gstin?, individual?: bool}, contact?: {contact_id?, full_name?, email?, phone?}, owner_membership_id?, source: {kind, ref}}` → 200 `{company_id, contact_id \| null, company_created, contact_created}` | §6.4.1; **`individual` (SP4 addition, default false)** says the company is a one-person buyer, so it is never found or reused by name |
| C52 | `POST .../parties:resolve` `{email?, phone?, company_name?, company_domain?, gstin?}` → 200 `{contact_id \| null, company_id \| null, owner_membership_id \| null, ambiguous}` | §6.4.2; at least one key, else 422 |
| C53 | `GET .../contacts:export?after_updated_at=&after_id=&limit=&ids=&erased_only=` → for **notification**: `{items: [{contact_id, company_id, full_name, email, status, owner_membership_id, updated_at, deleted_at, erased_at}], next: {after_updated_at, after_id} \| null}`; for **every other caller**, who must pass `erased_only=true` (else 403 `erased_only_required`): `{items: [{contact_id, company_id, erased_at, updated_at}], next}` | keyset on `(updated_at, id)`, `limit` ≤ 500 (default 200), `ids` ≤ 100 and only for notification, no phone key anywhere, `email` is `null` for a phone-only or erased contact, soft-deleted rows included, an erased row reads `full_name = "Erased contact"`. Only the projection needs state, so only notification may read names and e-mails; the six catch-up callers receive ids and a timestamp and nothing about a living contact |
| C45 | `GET .../contacts/{contact_id}/messaging-target?channel=` | `channel` in `{whatsapp, sms}` (phone, as today) or `email` (new); `MessagingTargetOut {contact_id, channel, phone, email}`; the `contact.messaging_target_resolved` audit row is kept for the e-mail channel; no `entity.sensitive_read` row (e-mail is plaintext business data); an erased or e-mail-less contact answers `email: null`, 200 |
| C89 (**SP4 addition**) | `GET .../contacts/{contact_id}/identity` | the write-time proof of a contact id (SD-2): `{contact_id, company_id, owner_membership_id, erased: false}` and nothing else; callers support, operations, deal, notification; **404** for a foreign, soft-deleted **or erased** contact (an erased contact can no longer be linked), no audit row (ids only), three negative tests and one cross-tenant test. C47 stays finance-only because it returns a name and an e-mail |

**Internal, notification** (C58): `POST .../consent-events`, callers **`storefront` and `lead` only** (the
first-draft registry also listed `customer` and `support`, which own no capture surface, SD-11, and are
refused 403; registry C58 and J29 drop them), org claim required. Body `{channel: "whatsapp"|"email"|"sms", purpose:
"marketing"|"transactional", status: "opted_in"|"opted_out", source: "web_form", evidence_ref: str (≤ 480),
address: str, occurred_at: datetime, idempotency_key: str (≤ 200)}` (`extra="forbid"`). Of the four `source`
values the first-draft registry listed (`web_form`, `checkout`, `import`, `api`) this route accepts **only
`web_form`** (registry C58):
the two callers write only what a person typed into their own form, and an `import` or `api` value would let
a bulk job attest an opt-in that is append-only and undone only by an opt-out (§6.5). Every other value, `admin`
and every system source are refused 422 `source_not_allowed` (ADR-0021 §7). It calls `record_consent_event`
(`services/notification/app/services.py:4443`) under a synthetic ORG context, stores
`evidence = "{caller}/{evidence_ref}"`, and answers `{recorded: bool, effective: bool}`: `recorded` is
`false` for a replay of the same `idempotency_key`; a key reused for a different event is 409. The address
travels in the body, **never as a blind index** (a later per-service HKDF would break cross-service
equality: `docs/adr/open-questions-register.md:96`, P3; the index is a function of the current key only,
`libs/platform-core/src/platform_core/pii.py:176-187`). It does not require the `engage`
entitlement: the ledger belongs to the platform.

**Events committed in R4.1** (schema file → payload; each in one commit with its producer change and
test, §4.3): `crm.book.contact.erased` → `{organization_id, contact_id, crm_contact_id, crm_company_id,
erased_at}`; `finance.billing.customer.created` → `{organization_id, customer_id, name, crm_contact_id?,
crm_company_id?}`; `finance.billing.customer.updated` → `{organization_id, customer_id, changed[]}`;
`finance.billing.customer.archived` → `{organization_id, customer_id}`; the id fields of §4.3 on
`crm.book.company.*`, `crm.book.contact.*`, `crm.pipeline.activity.logged`, `crm.pipeline.deal.*`,
`crm.lead.created|updated|converted`; the finance gap closure on `finance.billing.invoice.issued`,
`.paid`, `finance.billing.payment.received`, with `record_payment` refusing an allocation set that spans
currencies (422 `mixed_currency_payment`) because that schema carries one `currency` (S48).

### 6.4 Domain rules

**6.4.1 `parties:ensure`.** One decision order per entity, first match wins; nothing is ever merged or
moved.

1. *Validate.* `company.name` 1–200; `company.gstin`, if given, through `platform_core.gst.parse_gstin`
   (A15), else 422 on `company.gstin`; `contact` needs `email` or `phone` (`phone` canonicalised by the
   existing `_canonical_e164`, `services/customer/app/services.py:848`) and, to **create**, `full_name`;
   `source.kind` = `order` (the one caller is operations, SD-7: a later caller needs an amendment that adds
   its kind and its allow-list entry together) and `source.ref` matching
   `^[A-Za-z0-9:_./-]{1,80}$` (an id or a number, never prose).
2. *Company.* (a) `company_id` given and live → that company. Given and soft-deleted → 409
   `company_deleted` (the `leads:convert` rule, `CompanyDeletedProblem`). Given and absent from this
   organization → create with that id. Given and **present in another organization** → 409
   `id_conflict` with no detail (the primary key is global, so the insert would otherwise fail on a
   foreign row; the answer never says whose it is). (b) else GSTIN through `company_tax_ids` → that
   company. (c) else, **unless `company.individual` is true** (a walk-in buyer whose "company" is the person:
   a namesake must never be attached to a stranger's record, so step (c) is skipped and the company is
   created), `lower(btrim(name))` under the per-name advisory lock `_lock_company_name`
   (`services/customer/app/services.py:373`): one live match → it; two or more (the case-sensitive unique
   index permits `Acme Ltd` and `ACME LTD`) → 409 `ambiguous_company`, never a guess. (d) else create.
3. *Contact (when given).* (a) `contact_id` given: live → verify `company_id` equals the resolved
   company, else 409 `contact_company_mismatch` (a contact is never moved); deleted → 409
   `contact_deleted`; absent → create with that id; foreign → 409 `id_conflict`. (b) else `email_index`
   → that contact, same company check. (c) else `phone_index`: one live match → it, several → 409
   `ambiguous_contact`. (d) else create; `phone_enc`, `phone` (masked) and `phone_index` are written from
   the raw phone and the number is never logged.
4. *Owner of a created record* (D-35): `owner_membership_id` when it is an active member and the roster row
   exists or can be activated; else the organization's one active `is_owner` membership, activated as a
   rep with an audit row `owner.activated` (`acting_as = <caller>`); else 422 `owner_required`. The
   account read (C57) is made **before** the write transaction and only when a record is to be created.
5. *Shape.* The route is claim → call → record: a short read-only transaction decides whether anything
   must be created; only then is account asked; only then does one write transaction run. A concurrent
   creator that wins `uq_contacts_org_email`, `uq_contacts_org_phone_only` or the company name index is
   absorbed in a savepoint and the winner's row is returned (the shape of
   `finance/app/services.py:506-640`). Created companies are `prospect`, created contacts `lead`; the
   lifecycle promotion of §8.5 is separate.
6. *Audit and events.* `company.created` and `contact.created` audit rows carry `acting_as = <caller>` and
   `source.kind` only (from R3.1 contact audit rows hold changed-field names, B12); the ordinary
   enriched `crm.book.company.created` and `crm.book.contact.created` events are staged. A replay returns
   the same ids and stages nothing.

**6.4.2 `parties:resolve`.** Reads only; ids out; no audit row; the number or address is never logged.
The email and phone are compared through blind indexes, so no sealed value is opened. Candidates come
from every key present: `gstin` → company; `email` → contact; `phone` → contact (a non-E.164 phone
matches nobody, as `contacts:resolve` answers today); `company_domain` → companies whose lower-cased
`domain` equals it exactly; `company_name` → `lower(btrim(name))`. If any key yields two or more live
matches, or the resolved contact's company differs from the resolved company, the answer is
`{contact_id: null, company_id: null, owner_membership_id: null, ambiguous: true}`. Otherwise the answer
carries the contact (when one matched), its company (or the resolved company) and the owner membership of
the contact, falling back to the company's. An erased or deleted contact never matches. A caller treats
`ambiguous` as "link nothing and say so" (SD-3).

**6.4.3 Erase.** In one transaction, with the contact row locked: set `email = NULL`,
`full_name = 'Erased contact'`, `title = ''`, `phone = ''`, `phone_enc = NULL`, `phone_index = NULL`,
`email_index = NULL`, `erased_at = now()`, stamp `updated_by`; call `customer.scrub_contact_audit`; write
one audit row `contact.erased` (`after = {audit_rows_scrubbed: n}`); stage EV18. Kept: `id`, `company_id`,
`owner_id`, `status`, `last_activity_at`, `created_at`. Works on a soft-deleted contact. Afterwards the
contact cannot be edited or revealed (409, 404), matches no resolve, and an event for it carries the
tombstone name. There is no un-erase.

**6.4.4 The nightly orphan check (design; built in R4.7 as H5).** It reports every plain-UUID reference to
a CRM company or contact whose target no longer resolves, and never deletes (ADR-0000 §10).
*Where:* a `PartyOrphanSweep` inside `customer`, behind the `run_sweeps_in_api` setting (F20) that R4.3 introduces
for its rollup sweep (the `operations` pattern: `services/operations/app/config.py:32`), so there is no new
container; a thin
`tools/check_party_orphans.py` runs the same function read-only for the owner. *How:* for each
organization (discovered on the narrow outbox-role session, as operations' sweeps do:
`services/operations/app/main.py:70`; the compose note at `infrastructure/edge/compose.prod.yml:1297-1305`
says that role holds `SELECT (organization_id)` on the tables it discovers from, and the grant itself is
written in a service's `migrations/env.py`, so R4.3 adds customer's own there, S47, §8.2), customer pages C104 on finance (`customers`, `invoices`),
operations (`orders`), notification (`contacts`, `conversations`, `messages`), support (`tickets`),
project and projects (`projects`), storefront (`enquiries`), deal (`deals`, `activities`, `follow_ups`)
and lead (`leads`), 500 ids per page, outside any transaction and with the 360's 2-second budget per call. It
checks each page against `companies` and `contacts` with one `= ANY(:ids)` query, classifying every id as
`ok`, `missing` or `erased_linked` (§12.4.7), and logs, per organization and source, one
`party.orphan_check.summary` line with counts only (a non-zero `missing` also logs at `error` so the existing
log-based alerting sees it). Nothing is persisted: the ids
are listed on demand by `tools/check_party_orphans.py --org <uuid> --list`, read-only and owner-run, so no
table, no PII and no purge-tool change. A source that is down or not entitled is skipped with a `skipped`
count, never a failure. Cost: one keyset page per source per organization per night, bounded by the
number of linked records.

### 6.5 Consent and erasure design (draft of ADR `crm-consent-and-erasure`, expected 0039)

This subsection is the content of the ADR that R4.1 files as `Status: proposed`
(`docs/adr/0039-crm-consent-and-erasure.md`, number confirmed against `docs/decisions.md` at merge). It
builds on ADR-0000 (PII, audit, ids), ADR-0017 (decision 4), ADR-0020 §4, ADR-0021 and
`docs/engage-erasure-and-unsubscribe.md`, and amends ADR-0021's STOP and unsubscribe text.

**Context.** Consent exists only in `notification`: an append-only ledger keyed on a sealed address,
latest event wins, silence is not consent (`services/notification/app/consent.py:1-60`). CRM stores
none, yet CRM data fans out to ten products. Erasure exists only for an Engage contact
(`services.erase_contact_campaign_copies`, `services/notification/app/services.py:4020`); a CRM delete is
a soft delete nobody hears about; `deal.activities` and every audit table are append-only; all services
share one PII key, so there is no crypto-shredding (`docs/engage-erasure-and-unsubscribe.md:14-86`).

**Decisions.**

1. **Consent stays in notification and CRM never decides it.** The 360, the lists and the send dialog
   show the ledger's verdict; they never compute one. The Engage projection of CRM contacts
   (R4.2) writes **no** consent event and never touches `suppressions`; a projected contact is
   marketing-blocked until a real opt-in is captured (ADR-0021: silence is not consent).
2. **The system that owns a capture surface writes the ledger** through C58: storefront for its
   buyer form, lead for its capture endpoints (B39: from R4.7 it forwards the evidence it stored), the
   Engage inbox for a STOP or a START (already in notification). A bulk import never attests an opt-in.
   The `admin` source stays Engage-only and unverified (ADR-0021).
3. **One rule for STOP and unsubscribe (D-33, pending).** Both opt the address out of **marketing
   only**; a transactional opt-out is a separate act. This is what the code does today (STOP
   `services/notification/app/services.py:2102-2110`; unsubscribe `:4208-4216, 4314-4322, 4377-4386`);
   what is wrong is the prose: the `consent.py` module docstring (`:15-22`) and ADR-0021 (`:62-65`) each
   state the opposite for one of the two. The ADR corrects both and
   `docs/whatsapp/architecture.md`; it changes no behaviour.
4. **Erasure is anonymise-in-place plus an event.** `POST /contacts/{id}:erase` overwrites the contact,
   scrubs the contact's audit copies (SD-10) and publishes `crm.book.contact.erased` (not subscribable by
   tenant rules). Every store that keys a row by the contact id applies its own treatment (table
   below), in the release that creates the link it acts on.
5. **Events and the timeline are PII-free by construction.** Ids, enumerations, document numbers and
   dates only; the three legacy fields (`full_name` on `crm.book.contact.created`, `name` on
   `finance.billing.customer.created` and `crm.pipeline.deal.*`) are frozen and must not be read by a
   new consumer. System-written deal activities (`message`, `ticket`, handoffs) carry a generic subject
   and no body (B31).
6. **Retention exceptions are the owner's and legal's (D-27, pending).** The table lists the default;
   this ADR makes no legal claim. The owner confirms each exception before the ADR is accepted.
7. **What an erase does not reach**, stated plainly: backups, WAL and dead tuples (as in
   `docs/engage-erasure-and-unsubscribe.md`); free text that merely mentions the person (rep-authored
   activity subjects and bodies, ticket and message bodies, rendered message text, a deal named after a
   lead's full name by `services/lead/app/domain.py:159-177`); and a lead that never converted, which this
   event cannot reach because no contact id names it: that person is erased **one lead at a time** by the
   administrator route of SP3 (`POST /leads/{lead_id}:erase`, SP3 §11.3.3, R3.7a, pending D-27 for leads),
   which anonymises the lead and rewrites its audit copies with `lead.scrub_lead_audit`. A converted lead is
   erased by this event through the same function (§12.4.6), so "erased" means one thing for a lead. The
   first is a limitation to be shown in the activity form ("do not enter personal data in the subject or
   body", SP3); the others are D-27 residue.
8. **First deploy catches up.** An event published before a consumer's queue existed is lost
   (`events.py:250-262`), so each consumer's first deploy runs `tools/reapply_contact_erasures.py`
   (H14), which pages C53 with `erased_only=true` and applies the service's local treatment.
   H14 is idempotent and counts-only, and it is gated like every tool that overwrites personal data in
   production: a parent process with no service import starts one child per `--service` inside that
   service's own container (`import app` is ambiguous across services); `plan` is the read-only default and
   prints counts per organization; `apply` needs `--apply`, `--confirm-count N` (the count the owner read in
   `plan`), `--i-took-a-backup` and `--i-know-this-is-the-live-database` (the gating of
   `tools/purge_test_orgs.py:1892-1910`), one transaction per organization on the app role with the tenant
   bound, stop at the first failing organization; an organization without the `crm` entitlement answers 403
   (§4.2), is skipped and counted. It prints no id and no name. Tests are modelled on SP3's
   `test_apply_needs_all_three_gates`: `plan` writes nothing, `apply` refuses each missing gate, a second
   `apply` changes zero rows, the stdout holds no `@` and no name.

**Coverage by store.** "Ships" is the release that adds the handler; an erase before that release
reaches only the stores above it, and H14 closes the gap at that release's first deploy.

| Store | Holds about the person | Treatment on erase | Ships | Kept (D-27) |
|---|---|---|---|---|
| `customer.contacts` | name, title, e-mail, sealed phone, indices | anonymised in place | R4.1 | row id, company, owner, status |
| `customer.audit_logs` (entity contact) | e-mail, name, phone values in `before` and `after` of pre-R3.1 rows | keys rewritten by `scrub_contact_audit` | R4.1 | action, actor, time, ids |
| `customer.party_*` | none (ids, numbers, dates) | none needed | R4.3 | |
| `customer.companies` | a one-person company's name is the person's name | **not** touched; the company is a separate record the owner may rename or delete (blocked while it has commercial history, SD-20) | | residue |
| `notification.contacts` (projection) | sealed e-mail, name, attributes | row deleted; campaign-recipient copies overwritten (`erase_contact_campaign_copies`) | R4.2 | |
| `notification.audit_logs` (`engage.contact.*`, entity `contact`) | the contact's **name** and masked e-mail in `after` and `before` (`services/notification/app/services.py:3897-3900, 3950-3954, 3984-3987`) | from R4.2 these rows carry changed-field names only; rows that exist are rewritten by `notification.scrub_contact_audit` for the Engage contact the projection row names | R4.2 | action, actor, time, ids; rows of an Engage contact deleted **before** the erase (no id link remains) are D-27 residue |
| `notification.conversations`, inbound messages | sealed WhatsApp number, inbound bodies, profile name | for threads with `crm_contact_id` = the contact: number and index overwritten, `body_enc`, `raw_enc`, `profile_name_enc` overwritten, links nulled, thread closed | R4.2 | |
| `notification.messages` | plaintext `to_address`, `provider_template_payload` variables | overwritten for rows with `crm_contact_id` = the contact (B67) | R4.2 | `subject` and `body` (rendered record) |
| `notification.consent_events`, `suppressions`, `preferences` | sealed address, evidence reference | **kept**: the refusal and the evidence must outlive the person | | D-27 |
| `finance.customers` | contact name, e-mail | `contact_name`, `email`, `crm_contact_id` cleared on profiles whose billing contact is the person | R4.3 | party name, GSTIN, address |
| `finance.audit_logs` (`customer.updated`, entity `customer`) | `contact_name` and `email` in `before` and `after` (`services/finance/app/services.py:380-436`; `customer.created` holds only `{name, gstin}`) | from R4.3 these rows carry changed-field names only; rows that exist are rewritten by `finance.scrub_customer_audit` for each profile the contact was linked to | R4.3 | action, actor, time, ids |
| `finance.invoices`, `payments` | buyer snapshot (`buyer_snapshot`, GSTIN) | **kept**: legal documents | | D-27 |
| `operations.orders` | `customer_name`, plaintext `customer_phone` | overwritten where `crm_contact_id` matches | R4.3 | order facts |
| `support.tickets`, `ticket_messages` | requester name, e-mail, customer author names | requester name and e-mail overwritten, customer-authored `author_name` overwritten | R4.4 | message bodies, subject |
| `storefront.enquiries` | name, sealed e-mail and phone | overwritten on rows with `crm_contact_id` = the contact. An enquiry **with no `crm_contact_id`** (no contact matched when it was linked and its lead never converted, or it predates the link) is not reached: neither this event nor SP3's lead erase (its event, EV53 `crm.lead.erased`, has no consumer in this programme) names it, and Storefront has no enquiry delete or erase route today (its delete routes are domain, zone, theme, category and product: `services/storefront/app/api/routes.py:522, 684, 992, 1090, 1306`) | R4.7 | company, lines, requirement; an unlinked enquiry is D-27 residue, and an erase action for one is not in this programme (§15) |
| `lead.leads` (converted) | name, sealed e-mail and phone, notes | overwritten where `converted_contact_id` matches, through the function of SP3's lead erase (§12.4.6); `lead.audit_logs` rows scrubbed by `lead.scrub_lead_audit` (B86, §12.2) | R4.7 | company, source |
| `lead.leads` (never converted) | name, company, sealed e-mail and phone, notes, the visitor's messages | **not reached** by this event (no contact id names the lead); erased one lead at a time by the administrator route of SP3 (`POST /leads/{lead_id}:erase`, SP3 §11.3.3) | R3.7a | what SP3's D-27 row keeps: source, status, owner, attribution, consent evidence, converted ids |
| `deal.activities`, `deal.audit_logs` | rep-authored subject and body | **not reachable** (append-only trigger) | | D-27 residue |
| `deal.deals.name` (lead-converted) | the lead's full name | not reachable; recommended follow-up: derive the name from the company only (SP3) and rename existing rows with an owner-run tool | | D-27 residue |
| PulseBoard and Projects `client` | a business label | not erased | | residue for one-person companies |

**Consequences.** One new internal route (C58), one new public route (C29), one new permission (E24,
ORG floor, step-up), one new event, one `SECURITY DEFINER` function in each schema whose audit rows copy a
contact's personal data (customer R4.1, notification R4.2, finance R4.3, lead B86 created once in R3.7a for SP3's lead erase and
called again by R4.7 for the contact erase; support, operations, storefront and deal audit rows hold none, or only the free
text of D-27), each guarded to act on JSON objects only and on the session's own tenant only (§6.2), one
owner-run catch-up tool. ADR-0021 gets a pointer and an amendment note; the contradiction
between its text and `consent.py` is closed by D-33.

### 6.6 Consumers and idempotency

R4.1 binds no consumer. Idempotency of what it adds: C51 by the caller's deterministic ids, then GSTIN,
name, e-mail and phone (the `leads:convert` pattern); C29 by `erased_at` (a second call changes nothing and
stages no event); C58 by `idempotency_key` (unique per organization: `uq_consent_events_org_idempotency`,
`services/notification/app/models.py:967-972`); the enriched events by the outbox as before. Event ids are
the outbox row ids, so a relay retry republishes the same `event_id` and every later consumer's
`processed_events` row stops a second effect.

### 6.7 UI and copy

- **Erase action** (CRM). On the contact page (R3.5) and, until it ships, in the Contacts list row actions:
  a danger `RowActions` entry shown only with `useCan("crm.book.contact.erase", "ORG")` (a hide, not the
  enforcement). The dialog asks for step-up (`useStepUp`, `RevealHeaders` in `@opsgrids/client`), states
  the effect and requires typing `ERASE`. **The sentence names only the stores the deployed release covers.**
  At R4.1 nothing but CRM erases: "This overwrites the person's name, e-mail and phone in the CRM, including
  the copies in the CRM's own history. Copies in other products are overwritten as each is connected.
  Invoices, consent records and notes typed by your team are kept. This cannot be undone." Each later
  release extends the clause "and in {Engage, orders, billing contacts, tickets, enquiries, leads}" in the same
  commit as the handler it describes, from a per-release coverage list in the SPA
  (`frontend/product-crm/src/lib/eraseCoverage.ts`, a component test pins that the list equals the §6.5 stores
  whose "Ships" release is at or before the release being published); the SPA is published after the backends
  (§6.9), so the dialog never claims a store that the deployed backends do not erase, and H14 closes the gap
  for earlier erasures. States: idle, confirming, working, done (the row shows an "Erased" badge and edit,
  reveal and message controls are disabled), forbidden, step-up required, error with retry, already erased.
  **Accessibility:** the dialog is modal with a focus trap and returns focus to the row action when it closes;
  the confirmation field has a visible label ("Type ERASE to confirm") bound to the input, the confirm button
  stays disabled until the text matches exactly, the outcome (done, or the error with its retry) is announced
  through an `aria-live="polite"` region and focus moves to that result, and the "Erased" badge carries its
  text, never colour alone. A story and a visual baseline cover the dialog and the badge.
- **Vocabulary copy fixes** (copy only; no route, field, event or code name changes):

  | Where | Change |
  |---|---|
  | `frontend/product-finance/src/ui/useNavGroups.ts:28` | nav label "Customers" becomes "Billed parties" (the path `/customers` and the permission names stay) |
  | `frontend/product-finance/src/routes/Customers.tsx:189, 255`; `src/forms/CustomerFormDialog.tsx:100` | "Add customer" becomes "Add billed party"; page title and empty state say "billed party" |
  | `frontend/product-finance/src/data/customers.ts:95`; `frontend/opsgrids-dashboard/src/routes/Home.tsx:327` | "Unknown customer" becomes "Unknown billed party" |
  | `frontend/opsgrids-dashboard/src/ui/useNavGroups.ts:35`; `src/routes/Invoices.tsx:178` | "Invoices" becomes "OpsGrids billing"; the description says these are invoices OpsGrids issued to your organization |
  | `services/finance/app/models.py:86-89` | docstring "Distinct from any CRM account by design" becomes "A billed party. It links to a CRM company through `crm_company_id` from R4.3" |
  | `services/operations/app/orders/services.py:871-892` | docstring updated to the R4.3 resolution; the `invoice_requested` schema `$comment` ("the emit side is not built") corrected in R4.3 |
  | `services/customer/app/c1_360.py:65-73` | the `MISSING_UPSTREAMS` comment ("finance keys invoices by its own id with no link") corrected in R4.3 |
  | `services/account/app/models.py:60-67` | the `Organization` docstring says "A tenant: the OpsGrids customer organization. Never a CRM company" |

  Each frontend row ships with the component test that pins the label and, for finance and the
  dashboard, a refreshed visual baseline (`make ui-visual`, darwin only).

### 6.8 Tests

All backend tests run per service in isolated databases (`import app` is ambiguous across services).

| Area | Tests that will exist |
|---|---|
| Negative authz | `services/customer/tests/test_contact_erase_authz.py`: no permission (403), a role granted `crm.book.contact.erase` at OWN (403: the ORG floor), no step-up (403 naming `otp, stepup`), another organization's contact (404, same body as missing), confirm mismatch (422), unauthenticated (401), not entitled (402). `test_party_routes_authz.py` for C50 to C53 and C45: no token 401, wrong caller 403 (for each route a caller outside its list), wrong org claim 403, one test per route that uses the token minted by the **real client helper** of the first caller. **Narrowed allow-lists:** C51 from any caller but operations 403 (finance, storefront, notification each); C53 from finance, support, lead, operations, storefront and deal without `erased_only=true` 403 `erased_only_required` and with it a body holding only `contact_id, company_id, erased_at, updated_at` (a recursive key scan finds no `full_name` or `email`), `ids=` from a non-notification caller refused; C58 from `customer` and `support` 403, and `source` of `checkout`, `import`, `api`, `admin` or any system value 422 `source_not_allowed`. **Entitlement:** C50, C51, C52, C53 and C89 for an organization with no `crm` entitlement 403 `crm_not_active` (one test each, the entitlement read mocked at product-service's real route shape), product-service down 503 `entitlement_unavailable`, and a second call inside 60 s makes no second product call |
| Cross-tenant | an id from organization B under organization A's claim: C50 and C53 answer nothing, C51 answers 409 `id_conflict` without naming the owner, C52 never matches B's e-mail or phone; the scrub function with B's contact id and A's organization changes zero rows **and, called as tenant A with B's organization id, raises `insufficient_privilege`**; the notification C58 never writes to another organization |
| Idempotency and redelivery | C51 twice with the same ids (one company, one contact, one event pair); C51 concurrent for one name and one e-mail (savepoint winner returned); C29 twice (second writes nothing, stages no event); C58 same key twice and with a changed body (409); the enriched events redelivered change nothing |
| Migration | round-trip with `make migration-roundtrip`; the CHECK admits an erased row and still refuses a row with neither e-mail nor phone; downgrade refuses with an erased row and succeeds on an empty database and with `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`; `alembic check`; single head (`tests/infra/test_migration_revisions.py`) |
| Privilege | as the app role, a raw `UPDATE customer.audit_logs` fails with `InsufficientPrivilege`, while `SELECT customer.scrub_contact_audit(...)` succeeds; the function touches only the named keys, only `entity = 'contact'`, only the given contact and organization (a second contact's row, a company row and another organization's row are byte-identical afterwards); `opsgrids_admin` has `BYPASSRLS` (so the owner choice is not silently inert). **The rows are seeded through the real `record_audit`, not by hand:** a `contact.created` row (`before` JSON null, `after` an object holding the e-mail and name), a `contact.updated` row, a `contact.deleted` row, and rows whose `before` or `after` is an array or a scalar, so the helpers' non-object branch and the `22023` failure mode of S49 are each exercised; the function returns the count of rows it changed, leaves a non-object value byte-identical, and with no tenant GUC set raises rather than scrubbing |
| Erase behaviour | every field of §6.4.3; **a contact created through the API route and then erased** (so its JSON-null `before` is in the table); works on a soft-deleted contact; an erased contact is not returned by C52, appears in C53 with the tombstone and `erased_at`, cannot be PATCHed (409) or revealed; `phone:reveal` writes no audit row for it; a recursive scan of the stored `audit_logs` rows finds no e-mail or phone value of the erased contact |
| Contract | each new or changed schema is validated by `validate_event_schema` through the real write path (company create, update, delete; contact create, update, delete, erase; activity log; deal create, update, stage move, delete; lead create, update, convert; finance customer create, update, archive, invoice issue, payment record with the gap fields); a recursive key scan asserts no `email`, `phone` or `full_name` key outside the frozen `crm.book.contact.created.full_name` and `finance.billing.customer.created.name`; `make contracts-check`; `make openapi` and `make openapi-check` (the erase route and `ContactOut.erased`); `python tools/export_permissions.py --check` (E24 in the catalogue) |
| Consent writer | caller allow-list (storefront and lead only) and org claim; `source` of every value but `web_form` refused; evidence containing an e-mail or phone refused (422); a ledger row written for an organization with no `engage` entitlement; `consent.decide` afterwards allows a marketing send only after an `opted_in` |
| Tools (H14) | `tests/infra/test_reapply_contact_erasures_gates.py` (modelled on SP3's `test_apply_needs_all_three_gates`): `plan` writes nothing, `apply` refuses each missing gate by name, a second `apply` changes zero rows, an unentitled organization is skipped and counted, one child process per `--service`, and the tool's stdout in a test run contains no `@`, no id and no name |
| Frontend | the erase dialog in every state above, naming only the stores of the release (the coverage-list test), with its focus trap, labelled confirmation input and `aria-live` result; `useCan` hides the entry; the four frontend copy rows (the other four are docstrings and comments); visual baselines for finance Customers and the dashboard billing page; `pnpm typecheck && lint && test && build` for CRM, finance and the dashboard; `make ui-visual` |
| Infra | `tests/infra/test_io_outside_transaction.py` stays green (C51's account read is outside the write transaction); `make boundaries` |
| SP1's guards (edited in the closing commit) | The guards of SP1 §9.2.2 are edited with the code they pin. **Pins:** the `AHEAD_OF_PRODUCER` rows for `crm.book.company.created` and `.updated` (EV1, EV2) are deleted from `services/customer/tests/test_event_contracts.py` and the two names from `test_the_ahead_of_producer_schemas_are_exactly_the_pinned_eight` (`libs/contracts/tests/test_event_schemas.py`), so both events get the plain conformance tests through the real write path. **Docs:** `docs/crm.md` is edited for EV18 and for the E24 row of its permission table in the same commit, which `tests/infra/test_crm_doc_matches_contracts.py` enforces. **Clients:** `tests/infra/test_internal_client_paths.py` registers customer's product-service entitlement read (the customer pair of W36) with a driver that returns the token it sent. **Engage builder:** `SendSettings.tsx` (`frontend/product-engage/src/routes/automations/`) lifts rule 1 (switching the channel to e-mail removes `recipient_kind`) and the contact helper's WhatsApp-only wording in the same commit as the customer route that answers C45 on the e-mail channel, with `Builder.test.tsx` following; rule 2 (a "Send to" path that does not end in `contact_id` is an address) stays. **Recipe purpose:** the repaired starter recipe's `purpose: "marketing"` (EV39) is re-decided with the consent design of §6.5 by whichever of R3.7b and R4.7 first lets a converted lead carry evidence (§12.4.6) |

### 6.9 Rollout

Deploy order (owner-run; every step is a manual deploy that names a ref): (1) merge, `make gate` run
locally (Actions billing is blocked); (2) deploy **notification** (C58); (3) deploy **customer** (the
migration, routes with their entitlement re-check against the shared `PRODUCT_SERVICE_URL` (already in
`x-service-env`, so no compose line), enriched events, the `scrub` function); (4) deploy **deal**, **lead** and
**finance** (enriched events and finance's gap closure: the new fields are optional in the schemas and
absent-tolerant for every reader, so their order among themselves does not matter); (5) after the customer deploy, **reconcile the new
permission**: `make edge-reconcile PRODUCTS=crm` prints the dry run first, then `APPLY=1`, off-peak and
paced (E24 reaches `CRM_ADMIN` and the organization owners only; no non-admin template picks it up); (6)
publish the SPAs last (`make edge-web APP=crm`, `APP=finance`, `APP=dashboard`) from the merged checkout,
after checking out the deployed `main` (`edge-web` builds the local checkout). No flag and no new compose
line: customer's two new settings, `product_service_url` and `product_timeout_seconds`, read the shared
`PRODUCT_SERVICE_URL` that `x-service-env` already carries. **Docs and pins (not a deploy step):** the edits
in the last row of §6.8 land in the commits that change what they describe, `docs/crm.md` (EV18 and E24) with
the erase route and its event, the two `AHEAD_OF_PRODUCER` rows with the company events, and the Engage
builder lifts with the e-mail channel of C45.

*Memory.* No new container and no fold: `python3 infrastructure/edge/lib/memory_budget_totals.py` over
`infrastructure/edge/memory-budget.json` is unchanged at 3747 MB (ten profiles) and 3944 MB with `admin`
(S40); customer's resident set grows by the three functions' worth of Python only (inference, below
1 MB, measured in the PR).

*Verification (owner).* `alembic current` for customer; `\df+ customer.scrub_contact_audit` shows
`SECURITY DEFINER` and the owner; erase a synthetic contact in a test organization and read the
`audit_rows_scrubbed` count and the absence of the e-mail in its audit rows; call C50 to C53 with the
service token of a permitted and of an unpermitted caller. Agents never run the production smoke.

### 6.10 Rollback

*Reversible:* code (customer, deal, lead, finance, notification) rolls back to the previous images
because every change is additive: the previous images ignore `erased_at`, the function, the extra payload
fields and the new routes. The schema stays. *Not reversible:* an erase that has been performed is
destroyed by design; the downgrade refuses while one exists. *Stop conditions* (stop, do not continue
the deploy): the migration fails or takes the lock for more than 5 s; a synthetic erase leaves the e-mail
in any `audit_logs` row of that contact; an erase publishes no EV18 row to the outbox; any producer test
fails the schema; a notification C58 write appears without its audit/ledger row. **What a code rollback
loses (§4.7):** no consumer ships in R4.1, so no event is discarded; the table is empty on purpose.

| Consumer | Events a rollback discards | Catch-up |
|---|---|---|
| none: R4.1 binds no queue | none (EV18 is published to no queue, §4.6) | not needed; H14 runs at each later consumer's first deploy |

One rollback is not free: the previous customer image does not know `erased_at`, so it would accept a `PATCH`
or a phone change on an erased row and put personal data back. **Do not roll the customer image back once any
contact has been erased**; fix forward (a stop condition, not a lever).

### 6.11 Acceptance criteria

- [ ] `customer.contacts.erased_at`, `ix_contacts_org_erased` and `ck_contacts_has_email_or_phone` exist as specified; `make migration-roundtrip` is green; downgrade refuses with an erased row.
- [ ] `customer.scrub_contact_audit` exists, is `SECURITY DEFINER`, owned by `opsgrids_admin`, executable by `opsgrids_customer` only; a privilege test proves the app role cannot `UPDATE` `audit_logs` directly; its helpers return JSON `null`, arrays and scalars unchanged (tested on rows written by the real `record_audit`, including a contact created through the API and then erased), and it raises when `p_organization_id` is not the session's tenant.
- [ ] `POST /contacts/{id}:erase` answers 403 to an OWN grant and to a session without step-up, 404 to another organization's id, 422 to a mismatched confirm id, 200 twice with one event.
- [ ] After an erase, no row of `customer.audit_logs` for that contact contains the old e-mail, full name or phone, and C53 shows the tombstone.
- [ ] C50, C51, C52, C53, C89 and the C45 e-mail channel exist, each with the three negative tests, a cross-tenant test and an idempotency or keyset test; C50, C51, C52, C53 and C89 refuse an organization without `crm` (403 `crm_not_active`); C51 answers only operations, C53 answers a non-notification caller only with `erased_only=true` and ids, and C51 never moves a contact and never merges two companies.
- [ ] Every event in §4.3 marked R4.1 has its schema committed in the same commit as its producer change, and its producer-side test builds the payload through the real write path.
- [ ] The finance invoice, payment and customer events validate against their schemas through `create_invoice`, `record_payment` and `create_customer`; `finance.billing.customer.updated` carries no `email` or `contact_name` key.
- [ ] `libs/contracts/events/crm.book.contact.erased/v1.schema.json` exists and no tenant-rule subscribable key names it.
- [ ] C58 records a ledger row for an organization without `engage`, answers only storefront and lead, accepts only `source = web_form` (every other value, `admin` and every system source are 422), and refuses evidence that looks like an e-mail or a phone.
- [ ] The ADR draft of §6.5 is filed as `docs/adr/0039-crm-consent-and-erasure.md` (`Status: proposed`), indexed in `docs/decisions.md` and linked from `docs/adr/open-questions-register.md`; `docs/engage-erasure-and-unsubscribe.md` and `docs/adr/0021-consent-ledger.md` point to it.
- [ ] The eight copy rows of §6.7 are applied; no new `customer` noun appears in a column, route or event name.
- [ ] The erase dialog names only the stores the deployed release erases (R4.1: the CRM alone), traps focus, labels its confirmation field, announces its result in an `aria-live` region, and has loading, working, error, forbidden and step-up states at phone width; the token baseline has not grown (`pnpm tokens:baseline` without `--allow-increase` writes nothing), the dialog and badge have stories, and the visual and axe suites (`make ui-visual`) show no new violation.
- [ ] `tools/reapply_contact_erasures.py` (H14) exists with its read-only `plan` default, `--apply` and its three gates, its per-service children and its gate tests; it is not run in R4.1 (no consumer exists yet).
- [ ] `record_payment` refuses an allocation set that spans currencies (422 `mixed_currency_payment`) and `payment.received` validates with its one `currency`.
- [ ] SP1's guards follow R4.1 in its closing commit: the `AHEAD_OF_PRODUCER` rows and the two pinned-eight names for `crm.book.company.created` and `.updated` (EV1, EV2) are gone; `docs/crm.md` carries EV18 and the E24 permission row and `test_crm_doc_matches_contracts.py` passes; `test_internal_client_paths.py` registers customer's entitlement read; the Engage `SendSettings` builder keeps rule 2 only and its contact helper no longer says WhatsApp for an e-mail contact.
- [ ] `make check` and `make gate` pass locally.

## 7. R4.2 Messaging loop

### 7.1 Purpose and needs

Make Engage and the CRM one audience instead of two: for CRM-entitled organizations the Engage contact
book becomes a read-only projection of CRM contacts (D-15, standalone Engage kept); a conversation
knows its CRM contact, company and owner; the CRM shows consent standing in lists and pages from one
batch call; a rep can send a one-to-one or template message from the CRM through notification (never a
provider client of its own); a WhatsApp conversation leaves a PII-free line on the CRM timeline; a
rep is told in the in-app feed when a lead, deal or company is assigned to them.

**Needs:** R4.1 (C58 contract, C45 e-mail channel, C53, enriched `crm.book.contact.*` events, EV18);
R2.7 (membership ids, so `owner_membership_id` is meaningful); R3.7 (EV15's producer and
`lead.lead_settings`); R2.4 (deal's folded consumer); R3.4 (`deal.pipeline_settings`); **R3.6** (EV54,
`domain.SYSTEM_IN_APP_RULES`, which the assignment alerts extend rather than duplicate: §7.4). D-15 (pending
D-15) and D-33 (pending D-33, for transactional e-mail) are the open decisions.

### 7.2 Data model

**notification** (one Alembic revision on `f1c8a2d5b7e4`; `# RE-PARENT HERE`; `lock_timeout 5s`; indexes
on `messages` are created `CONCURRENTLY` inside an autocommit block, precedent
`services/operations/migrations/versions/c4a8e1f6d273_lots_rolls_validate_and_index.py`):

```sql
-- B47: the projection marker and link on the Engage contact book
ALTER TABLE notification.contacts ADD COLUMN crm_contact_id UUID NULL;
ALTER TABLE notification.contacts ADD COLUMN origin VARCHAR(12) NOT NULL DEFAULT 'manual';
ALTER TABLE notification.contacts ADD CONSTRAINT ck_contacts_origin
    CHECK (origin IN ('manual', 'import', 'signup', 'crm'));
ALTER TABLE notification.contacts ADD CONSTRAINT ck_contacts_crm_origin_iff_linked
    CHECK ((origin = 'crm') = (crm_contact_id IS NOT NULL));
CREATE UNIQUE INDEX uq_contact_org_crm_contact ON notification.contacts (organization_id, crm_contact_id)
    WHERE crm_contact_id IS NOT NULL;

-- B48 + SP4: the conversation knows its CRM contact, company and the contact's owner at resolve time
ALTER TABLE notification.conversations ADD COLUMN crm_contact_id UUID NULL;
ALTER TABLE notification.conversations ADD COLUMN crm_company_id UUID NULL;
ALTER TABLE notification.conversations ADD COLUMN owner_membership_id UUID NULL;
-- the B48 backfill is not in this transaction: it runs in batches afterwards (below)
CREATE INDEX ix_conversations_org_crm_contact ON notification.conversations (organization_id, crm_contact_id);

-- SP4 (B84): the server derives an e-mail's purpose from its template category (SD-29, §7.4)
ALTER TABLE notification.templates ADD COLUMN category VARCHAR(12) NULL;     -- NULL = unclassified = marketing for CRM sends
ALTER TABLE notification.templates ADD CONSTRAINT ck_templates_category
    CHECK (category IS NULL OR category IN ('marketing', 'transactional'));

-- SP4 (B67): sends and alerts carry their CRM link, so an erase finds them without an address
ALTER TABLE notification.messages ADD COLUMN crm_contact_id UUID NULL;
ALTER TABLE notification.messages ADD COLUMN entity_ref JSONB NULL;
ALTER TABLE notification.messages ADD CONSTRAINT ck_messages_entity_ref_shape
    CHECK (entity_ref IS NULL OR (jsonb_typeof(entity_ref) = 'object'
                                  AND entity_ref ?& ARRAY['product', 'kind', 'id']));
CREATE INDEX CONCURRENTLY ix_messages_org_crm_contact ON notification.messages (organization_id, crm_contact_id)
    WHERE crm_contact_id IS NOT NULL;
```

`contacts.crm_contact_id` has no foreign key (plain UUID, ADR-0000 §10); every index leads with
`organization_id`; the three tables are already `ENABLE` + `FORCE` row-level security with
`tenant_isolation` and keep their grants, so the migration adds no policy and no `TENANT_TABLES` entry.
The backfill of the rename (B48) is **batched and outside the DDL transaction**: an `UPDATE` in the same
transaction as the `ALTER TABLE` would hold the `ALTER`'s `ACCESS EXCLUSIVE` lock on `notification.conversations`
for the whole update. The revision commits the DDL first and then, in an autocommit block (the precedent
already used for the `CONCURRENTLY` index above), runs `UPDATE notification.conversations SET crm_contact_id =
contact_id WHERE id IN (SELECT id FROM notification.conversations WHERE crm_contact_id IS NULL AND contact_id
IS NOT NULL ORDER BY id LIMIT 1000)` until it touches no row, each batch its own short transaction under
`lock_timeout 5s`: idempotent, it writes only the new column (same schema, no PII, no key) and logs counts
only. `contact_id` stays populated and is **dual-written** and serialised as a deprecated alias for one
release train; a later contractive release drops it. `templates.category` is read only by C60: existing
rules and automations ignore it. `TemplateIn` and `TemplateOut` gain `category`; setting `transactional`
needs the existing template-write permission (`notification.delivery.template.write`, which the catalogue
holds at ORG only) and writes an audit row `template.category_set` (the old and new value, never a body).
Downgrade drops the columns and indexes and **refuses** while any `contacts.origin = 'crm'` row exists
(unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`), because the row would silently become a manual contact
the next sync would duplicate; on an empty database it reverses fully.

**Audit scrub** (SD-10, the shape of §6.2, in the same revision). Engage's own audit rows copy the contact's
**name** and masked e-mail (`engage.contact.created`, `.updated`, `.deleted`:
`services/notification/app/services.py:3897-3900, 3950-3954, 3984-3987`), so the erase must reach them:
`notification.has_pii_keys(j)` over `['name','email']`, `notification.erase_pii_keys(j)` (`name` becomes
`Erased contact`, `email` becomes `[erased]`) and `notification.scrub_contact_audit(p_organization_id uuid,
p_contact_id uuid)`, `SECURITY DEFINER`, `SET search_path = notification, pg_temp`, owner `opsgrids_admin`,
`EXECUTE` revoked from `PUBLIC` and granted to `opsgrids_notification` only; it rewrites rows with `entity =
'contact'` and that `entity_id` in that organization and nothing else. `p_contact_id` is the **Engage**
contact's id (the projected row's own id), which the erase handler reads from `contacts.crm_contact_id` before
it removes the row. Like customer's (§6.2), the helpers act only on JSON objects and return JSON `null`, an
array or a scalar unchanged, and the function raises unless `p_organization_id` equals
`notification.current_org()`. From R4.2 the three audit actions carry `changed` field names only, so no new
copy accumulates. The downgrade drops the three functions.

**deal** (one revision on the head at merge; `# RE-PARENT HERE`):

```sql
-- B31: system-authored activity lines (WhatsApp conversation, later ticket and handoff lines)
ALTER TABLE deal.activities ADD COLUMN origin VARCHAR(20) NOT NULL DEFAULT 'rep';
ALTER TABLE deal.activities ADD COLUMN ref_type VARCHAR(40) NULL;
ALTER TABLE deal.activities ADD COLUMN ref_id UUID NULL;
ALTER TABLE deal.activities ADD CONSTRAINT ck_activities_origin CHECK (origin IN ('rep', 'system'));
ALTER TABLE deal.activities ADD CONSTRAINT ck_activities_ref_pair CHECK ((ref_type IS NULL) = (ref_id IS NULL));
ALTER TABLE deal.activities ADD CONSTRAINT ck_activities_conversation_has_direction
    CHECK (ref_type IS DISTINCT FROM 'conversation' OR direction IS NOT NULL);
CREATE UNIQUE INDEX uq_activities_org_conversation_day ON deal.activities
    (organization_id, ref_id, ((occurred_at AT TIME ZONE 'UTC')::date), direction)
    WHERE ref_type = 'conversation';
-- B27 additions (R4.2 owns both columns)
ALTER TABLE deal.pipeline_settings ADD COLUMN log_conversations_as_activity BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE deal.pipeline_settings ADD COLUMN notify_assignments BOOLEAN NOT NULL DEFAULT true;
```

`ADD COLUMN ... DEFAULT` is a catalogue change, so the append-only trigger
(`trg_activities_append_only`, `e94d7a2c5f18:90-107`) never fires and no row is rewritten; the trigger is
untouched. The system activity's `activity_type` is `message` (`ActivityType` gains `message` and, in R4.4,
`ticket`; a rep-authored `POST /activities` with either is 422 `type_system_only`). `ActivityOut` gains
`origin`, `ref_type`, `ref_id`.

**lead** (R3.7's `lead_settings`): `ALTER TABLE lead.lead_settings ADD COLUMN notify_assignments BOOLEAN
NOT NULL DEFAULT true`. **customer**: no DDL.

### 7.3 Backend contract

| Handle | Route | Permission and floor | Request | Response and errors |
|---|---|---|---|---|
| C30 | customer `POST /contacts:lookup-channels` | `crm.book.contact.read` (any scope) + `Entitled("crm")` | `{contact_ids: uuid[] (1–100)}` | 200 `{items: {<id>: ChannelsSection \| null}}`. Each id is re-checked with `assert_can_touch`; an id the caller may not touch, a foreign id and an unreachable summary are `null` (a list page never fails); every item is `null` when the organization has no `engage` entitlement. Customer calls C59 **once**, 2-second budget, with each contact's e-mail in the body. 422 above 100 or empty |
| C59 | notification `POST /internal/v1/contacts:summarize-messaging` | caller `customer`; organization from the token claim (as the single sibling, `services/notification/app/api/c1_internal.py:81-114`) | `{contact_ids: uuid[] ≤ 100, emails?: {<id>: string}}` (e-mails in the body, never in a URL or header) | 200 `{items: {<id>: ChannelsOut \| null}}`; built with two batched queries (conversations by `crm_contact_id = ANY`, ledger decisions by index list), so the query count is constant in the number of ids. 403 wrong caller or no `org` claim |
| C60 | notification `POST /engage/messages:send-to-contact` | `engage.inbox.message.reply` (ORG), `Entitled("engage")` (D-37) | `{crm_contact_id: uuid, channel: "whatsapp" \| "email", purpose: "transactional" \| "marketing", template_ref?: str, text?: str (≤ 1000), variables?: {str: str} (≤ 20 keys, values ≤ 200), idempotency_key: str (≤ 120)}`; exactly one of `template_ref`, `text` | 202 `SendToContactOut {message_id, state: "pending"}`. 402 no Engage; 403; 404 contact unknown, erased or foreign (one body); 409 `consent_denied` `{reason, message_id}` (a `suppressed` row is kept as the record); 409 `outside_service_window` (free text outside the 24-hour window: send a template); 422 `no_address` (no address on that channel), `purpose_template_mismatch` (a `transactional` purpose on a WhatsApp template the provider registered `marketing`, or, for e-mail, on a template whose `category` is not `transactional`), `text_not_supported` (free text on e-mail); 503 `crm_unavailable` (customer unreachable, retryable). `purpose` is the caller's statement of intent; the **server derives the effective purpose** (§7.4) and uses the stricter of the two |
| C72 | notification `GET /notifications/me` | the caller's own feed | | `NotificationOut` gains `entity_ref: {product, kind, id} \| null` |
| C84 | notification `GET\|POST /engage/contacts`, `PATCH\|DELETE /engage/contacts/{id}`, `POST /engage/contacts/import`, list-member routes | existing `engage.audience.contact.read/write` | | `ContactOut` gains `origin`, `crm_contact_id`. For `origin = 'crm'` and an organization that holds the `crm` entitlement: `PATCH` and `DELETE` answer **409 `managed_in_crm`**; `POST` and import for an address that matches a projected row skip it and report `managed_in_crm` for that row; list membership keeps working. With no `crm` entitlement (lapsed) the rows are editable again |
| C46 | customer `POST .../contacts:resolve` | callers notification, lead | body `{phone?, email?}` | `ContactResolveOut {contact_id, company_id, owner_membership_id}` (ids only) |
| C58 | notification `.../consent-events` | as §6.3 | | callers now exist: storefront and lead (R4.7) and only those two (`customer` and `support` are refused: no capture surface); `source` `web_form` only |
| subscribable keys | `libs/contracts` + `services/notification/app/domain.py` `SUBSCRIBABLE_EVENTS` | | | EV40: `crm.book.company.updated`, `.deleted`, `crm.book.contact.updated`, `.deleted`, `crm.pipeline.deal.updated`, `.deleted`; EV41: `crm.lead.assigned`, `crm.pipeline.deal.owner_changed`, `crm.book.company.owner_changed` (their bell alerts are SP3's `SYSTEM_IN_APP_RULES`, §7.4). Each in the same commit as its committed schema and a repaired recipe in `frontend/product-engage/src/routes/automations/recipes.ts:20-55` |

**Events.** New producers: `crm.pipeline.deal.owner_changed` (deal, EV16: `organization_id, deal_id,
crm_company_id, owner_id, owner_membership_id, previous_owner_id, previous_owner_membership_id`) and
`crm.book.company.owner_changed` (customer, EV17, same shape with `company_id`), each staged by the
existing update path **only when `owner_id` actually changes**; `crm.lead.assigned` (lead, EV15) is R3.7's
producer. **EV30** `engage.inbox.conversation.touched` (notification): `{organization_id,
conversation_id, crm_contact_id, crm_company_id, owner_membership_id, direction: "inbound" | "outbound",
channel, occurred_at}`: no body, no number, no name. Its schema is committed with the producer.

### 7.4 Domain rules

**Projection of CRM contacts** (D-15, pending D-15). For an organization whose CRM events reach
notification, Engage's audience contains one row per **CRM contact that has an e-mail**:

1. A contact with no e-mail (phone-only), an erased contact or a deleted contact has no projected row;
   if one exists it is removed (`delete_contact` and `erase_contact_campaign_copies`), and every
   `campaign_recipients` copy of it is overwritten.
2. On `crm.book.contact.created` or `.updated` the handler reads the state through C53 `ids=` (the event
   is a thin trigger: the e-mail never rides the bus) and upserts the row by `crm_contact_id`: `name` is
   the CRM `full_name`, `origin = 'crm'`, the e-mail is sealed with the same blind index every Engage
   contact uses, `attributes` are left alone. A row with the same `email_index` and no `crm_contact_id`
   (a manual or imported contact) is **linked**, not duplicated. If the new e-mail already belongs to a
   *different projected* contact the handler skips and writes an audit row
   `engage.contact.projection_conflict` (ids only); it never merges two CRM contacts.
3. **The projection writes no consent event and never touches `suppressions` or `preferences`.** A
   projected contact is marketing-blocked until a real opt-in is captured; transactional sends follow
   `consent.decide` as for any address.
4. Existing Engage contacts are linked once by the owner-run `tools/sync_engage_contacts.py` (H9,
   dry-run default, counts and ids only) and by the same rule thereafter; contacts typed into Engage for
   people not in the CRM stay `manual` or `import`.

**Conversation link.** `resolve_conversation_contact` (`services/notification/app/services.py:2269`) now
receives the answer of C46 (`contact_id`, `company_id`, `owner_membership_id`) and writes
`crm_contact_id`, `contact_id` (alias), `crm_company_id`, `owner_membership_id` together; the messaging
summary and every conversation read use `crm_contact_id`. `ConversationOut` serialises `contact_id` as
the deprecated alias beside `crm_contact_id`.

**Touch events (SD-12).** `engage.inbox.conversation.touched` is staged in three places, each in the
transaction that makes the contact known or the message real: (a) `fold_inbound_contact`, in its
record transaction, when the resolver answered with a `contact_id` (direction `inbound`, `occurred_at` the
inbound message's own time read from `inbound_message_id`); (b) `reply_to_conversation`
(`services/notification/app/services.py:2309`), when the conversation has a `crm_contact_id` (direction
`outbound`); (c) C60 free text through (b). An outbound **template** send to a contact with no
conversation creates no touch (there is no conversation id), and an inbound message from a stranger
creates none. Delivery is at-least-once; the consumer is idempotent.

**WhatsApp conversation as a CRM activity** (deal consumer, per-organization opt-in
`pipeline_settings.log_conversations_as_activity`, default `false`). On the event the handler skips (and
marks processed) unless the setting is on and `owner_membership_id` is non-null (`Activity.owner_id` is
NOT NULL). It then inserts **one** system activity per `(conversation, UTC day, direction)`:
`activity_type = 'message'`, `origin = 'system'`, `ref_type = 'conversation'`, `ref_id = conversation_id`,
subject `"WhatsApp conversation"` (the same literal for every row), `body = NULL`, `company_id`,
`contact_id`, `owner_id = owner_membership_id`, `occurred_at` from the event, `ON CONFLICT DO NOTHING`
on `uq_activities_org_conversation_day`. It then stages the ordinary `crm.pipeline.activity.logged`, so the
contact's `last_activity_at` advances through the existing customer handler. The line carries no title,
number or name; the Engage thread is reached by the deep link `{engage host}/inbox/{conversation_id}`
the UI builds from `ref_id`.

**CRM-originated send (C60)** follows the order: validate shape → read the contact's address from
customer (C45, **outside any transaction**) → decide consent (`consent.check_send`, the one decision
function) → stage through `stage_message` with `dedupe_key = "crm:send:{crm_contact_id}:{idempotency_key}"`
and `message.crm_contact_id` set → audit row `engage.crm.message.send_requested` (ids and channel only,
never the address or the text) → 202. For WhatsApp, `template_ref` is a provider template code and the
category is derived from the provider's registration (`purpose_for_send`); free text needs an open
service window and goes through the existing `reply_to_conversation`. For e-mail, `template_ref` is an
Engage `Template.code` of channel e-mail, rendered strictly (`domain.render`: an unresolved placeholder
is a 422, never a half-filled mail). **The server derives the purpose (SD-29, S54).** An e-mail template
has no category at HEAD, so a caller-declared `purpose` would let a rep holding `engage.inbox.message.reply`
label marketing content transactional and bypass the opt-in. R4.2 therefore adds `templates.category` (B84,
§7.2): the effective purpose of an e-mail send is `marketing` unless the template's `category` is
`transactional`, a value only an administrator sets, audited; an unclassified template counts as marketing,
and C60 answers 422 `purpose_template_mismatch` for a `transactional` purpose on any other template. The
consent decision runs on the effective purpose (a marketing e-mail needs an `opted_in`), and a
`transactional` send is refused whenever the contact's latest ledger event is `opted_out`. The UI offers
`marketing` only from Engage contexts (§7.6), which is a convenience and not the control. Because STOP and an
unsubscribe link opt out of marketing only (D-33), a newsletter unsubscribe never suppresses an invoice or
ticket mail; only an explicit transactional opt-out does.

**Assignment alerts extend SP3's one in-code mechanism; they add no second table.** EV54
(`domain.SYSTEM_IN_APP_RULES`, `services/notification/app/domain.py`, evaluated in `apply_rules`
(`services/notification/app/services.py:608`) after the organization's own rules, SP3 DD-21) is the **one**
place built-in in-app rules live, so there is nothing to seed per organization and nothing a tenant can
break, and R3.6 lands it first (§7.1). R4.2 adds three entries to that mapping, keyed by event type, each
channel `in_app`, category `operational` and dedupe key `{code}:{event_id}` as EV54 specifies, recipient
`owner_membership_id`, one fixed sentence and no placeholders: `crm.lead.assigned` (code
`system.crm.lead_assigned`, "A lead was assigned to you."), `crm.pipeline.deal.owner_changed`
(`system.crm.deal_assigned`, "A deal was assigned to you.") and `crm.book.company.owner_changed`
(`system.crm.company_assigned`, "A company was assigned to you."). EV54's `SystemInAppRule {code,
recipient_path, subject, body, category = "operational"}` gains one optional field, `entity: {kind, id_path}`, which R4.2 fills (`lead`
with `lead_id`, `deal` with `deal_id`, `company` with `company_id`, and for EV54's own stale and overdue entries
`deal` with `deal_id`) so the stored message carries `entity_ref = {product: "crm", kind, id}` (B67, C72): the
bell links nowhere until this release, as SP3 says. A null recipient, or a recipient equal to the acting
membership (`envelope.actor.membership_id`), produces nothing. The per-organization on/off is the
producer's own setting (`deal.pipeline_settings.notify_assignments`, `lead.lead_settings.notify_assignments`:
the producer does not emit the event when it is off); customer has no settings table at R4.2 (R4.3 adds
`customer_settings`, B83), so company-owner alerts are always on until a later change adds the switch there (a
limit recorded in §14, risk 19). In-app has no consent regime (`consent.py` CHANNELS
excludes it, `services/notification/app/consent.py:60-62`).

**D-33 rule.** R4.2 changes no consent behaviour. It adds a test that pins STOP and an unsubscribe link
each to opting out of `MARKETING` only (the code's rule, S25), so the ADR's corrected text cannot drift
from the code again (pending D-33).

### 7.5 Consumers and idempotency

| Queue (owner) | Binds (new) | Handler and marker | Notes |
|---|---|---|---|
| `notification.events` (notification, folded: `containers.notification.kind = api_with_worker`) | `crm.book.contact.created`, `.updated`, `.deleted` | `project_crm_contact`, marker `uuid5(event_id, "crm-contact-projection")` | The same keys also reach the rule `fold` when subscribable (`.created` is, `domain.py:148`); the two handlers share an event id, so the projection must **not** mark the raw event id (SD-4). Claim: read the row and check the marker; call: C53 `ids=` with the service token, outside any transaction; record: upsert and mark in one transaction. Customer unreachable → raise, one redelivery, then the DLQ (`notification.events.dlq`, already in `CORE_DLQ_QUEUES`); H9 reconciles |
| | `crm.book.contact.erased` | `erase_crm_contact_copies`, marker `uuid5(event_id, "crm-contact-erase")` | §6.5 notification rows, including `notification.scrub_contact_audit` for the Engage contact's own audit rows; counts only in the audit row `engage.contact.erased_by_crm` |
| | the §7.3 subscribable keys | the existing `fold` | one binding per key in `SUBSCRIBABLE_EVENTS` |
| `deal.events` (deal, folded from R2.4) | `engage.inbox.conversation.touched` | `conversation_touched_activity`, marker the raw `event_id` | one handler on this key; envelope and payload organization cross-checked (a mismatch is marked processed and logged, the `finance` handler's rule) |

Every handler binds the tenant per message from the envelope's `organization_id`
(`async with _session_scope(organization_id)`), never from the payload. The unique indexes
`uq_contact_org_crm_contact` and `uq_activities_org_conversation_day` are the second net behind
`processed_events`.

The same key `engage.inbox.conversation.touched` is bound on `customer.events` by **R4.3**, where the
`party_timeline` table that its `conversation_touched` kind needs is created (registry EV30, EV43; §8.5).
EV30 is emitted from this release, so a touch published before R4.3 reaches `deal.events` and never the
customer timeline; the kind exists but is written only from R4.3 on, and the gap is forward-only (a timeline
line is "recent activity", §8.9).

### 7.6 UI

- **Engage** (`frontend/product-engage`): contact list and detail show "Managed in CRM" with a link to the CRM
  contact when `useEntitlement('crm')` and `origin = 'crm'`, and no edit or delete control (G17); the
  import dialog reports rows skipped as `managed_in_crm`; the repaired recipes; the inbox shows the
  contact's owner beside the assignee; the template editor gains a "Category" select (marketing,
  transactional, unclassified) with the help text "Only transactional templates can be sent to a contact who
  has not opted in to marketing; an administrator sets this". States unchanged: loading, empty, error,
  forbidden.
- **CRM** (`frontend/product-crm`): a compact `ConsentBadges` (WhatsApp: opted in, window open until,
  not opted in; e-mail: subscribed, not subscribed; muted "Messaging not set up" when `null`) in contact
  list rows through `useContactChannels(ids)` and the C30 call, one request per page (G24); a
  `SendMessageDialog` on the contact page, hidden unless `useEntitlement('engage')` and
  `useCan('engage.inbox.message.reply', 'ORG')`, that shows the consent verdict **before** the send
  button and answers a 409 with the ledger's sentence; a "Notifications" card in the settings hub (R3.4)
  with the two `notify_assignments` switches (each a `PUT` on its own service); the bell opens
  `entity_ref` on the CRM host.
- **Every new view** has loading, empty, error and forbidden states and phone-width layout (the harness's
  375 px, no horizontal scroll at 360 px); no raw token values (`pnpm tokens:baseline` without
  `--allow-increase` writes nothing); stories for every new `@opsgrids/ui` export and a fixture and `MockRoute` in
  `frontend/opsgrids-visual/fixtures/api/` for every new `GET`; refreshed visual baselines for the badge
  states, the dialog and the Engage badge; the axe baseline does not worsen (`make ui-visual`).

### 7.7 Tests

| Area | Tests that will exist |
|---|---|
| Negative authz | C60: no permission 403, no `engage` 402, a CRM-only rep with `crm.*` only 403, another organization's contact 404 (one body); C30: no `crm.book.contact.read` 403, an OWN-scope caller asking for ids outside scope gets `null` for those and nothing about them; C59: wrong caller 403, token without `org` 403; C84: `PATCH` and `DELETE` of a `crm` row 409 while `crm` is entitled and 200 when it is not; C58 callers as §6.8; **the purpose is the server's:** C60 e-mail with `purpose = transactional` on a template whose `category` is `marketing` or unset 422 `purpose_template_mismatch`, a marketing template sent as `transactional` to a contact with no opt-in is refused and writes only a `suppressed` row, a `transactional` template reaches an unopted contact, setting `category = transactional` without `notification.delivery.template.write` 403 and with it writes the `template.category_set` audit row |
| Cross-tenant | projection never links a contact of another organization; C59 with ids of another organization answers `null` for each; the touch event with an envelope and a payload organization that differ is dropped and marked processed; `uq_contact_org_crm_contact` is per organization |
| Idempotency and redelivery | `contact.created` delivered twice and out of order with `.updated` → one row, the latest state (state comes from C53, not from the event); `erased` twice; `touched` twice and for two messages on one day and direction → one activity, a second direction → a second; C60 with one key twice → one message |
| Consent invariants | after projecting a book of contacts, zero `consent_events` exist and a marketing campaign's audience is empty; STOP and unsubscribe each opt out of marketing only (D-33); C60 `marketing` refused without an opt-in; `transactional` refused after an `opted_out` |
| Erase | the notification rows of §6.5: projected contact deleted, `campaign_recipients` copies overwritten, conversation number and inbound bodies overwritten and links nulled, `messages.to_address` and `provider_template_payload` overwritten where `crm_contact_id` matches, `consent_events`, `suppressions`, `preferences` untouched; the audit row holds counts only; the Engage contact's earlier `engage.contact.*` audit rows hold no name or masked e-mail afterwards (a recursive scan), as the app role a raw `UPDATE notification.audit_logs` fails while `notification.scrub_contact_audit` succeeds and touches no other entity or organization |
| Migration | round-trip; the backfill sets `crm_contact_id = contact_id` in batches of 1000, each its own transaction after the DDL has committed (a test with 2500 rows and a second connection that is never blocked for longer than one batch), and is idempotent on a second run; `templates.category` accepts only `marketing`, `transactional` or NULL; `ck_contacts_crm_origin_iff_linked`; downgrade refusal with a `crm` row; deal's trigger still refuses `UPDATE` and `DELETE` on `activities`; the new unique index rejects a second row for one `(conversation, day, direction)` |
| Contract | EV30, EV16, EV17 and every EV40 and EV41 key validated through the real write path; `libs/contracts/tests` asserts every `SUBSCRIBABLE_EVENTS` key added here has a committed schema; `make openapi-check` for C30, C60, C72, C84 |
| Performance | C59 for 100 ids runs a constant number of queries (asserted with a statement counter) |
| System in-app rules | `services/notification/tests/test_system_in_app_rules.py`: the three assignment entries sit in SP3's one `SYSTEM_IN_APP_RULES` mapping (a source scan finds no second built-in table and no `BUILTIN_CRM_ALERTS`); each produces one `in_app` message to `owner_membership_id` with its fixed sentence, `entity_ref` and dedupe `{code}:{event_id}`; a null recipient, an actor equal to the recipient and an event redelivered each produce no second message; SP3's three entries (follow-up due, stale, overdue) still render and now carry `entity_ref` where their payload has the id |
| Frontend | badge states, dialog states, the 409 sentence, the managed badge, the import report, the bell deep link, the template category select; `pnpm typecheck && lint && test && build` for engage, CRM, dashboard; visual and a11y suites |
| SP1's guards (edited in the closing commit) | `docs/crm.md` is edited for EV16 and EV17 (`crm.pipeline.deal.owner_changed`, `crm.book.company.owner_changed`) in the same commit as their producers, which `tests/infra/test_crm_doc_matches_contracts.py` enforces; the EV41 trigger `crm.lead.assigned` (and any other EV41 key a repaired recipe uses) joins the obligation of `tests/infra/test_engage_recipe_triggers.py`, its key in `SUBSCRIBABLE_EVENTS` in the same commit as the recipe and the committed schema. R4.2 introduces no new W row (C59 rides W14, and the C45, C46 and C53 reads ride W1, both existing); a new call function in an existing client is still registered in `tests/infra/test_internal_client_paths.py` with a driver that returns the token it sent |

### 7.8 Rollout

Deploy order (owner-run, one named ref per deploy, consumers before producers, §4.6): (1) **deal** (migration;
binds the conversation consumer; the new settings columns); (2) **notification** (migration; projection and
erase handlers; C59; C60; the template category; the feed field; the D-33 pinning test; the subscribable-key
changes and the three `SYSTEM_IN_APP_RULES` entries; EV30
emission, which has its consumer from step 1); (3) **customer** (C46's wider answer, C30); (4) **lead**
(`notify_assignments`); (5) **H9** for every CRM-entitled organization: `plan` (read-only, reviewed by
the owner), then `apply` with its three gates, because the projection triggers published before step 2
were dropped; (6) publish the SPAs last: engage, crm, dashboard. No flag at deploy: the opt-in
`log_conversations_as_activity` defaults off and `notify_assignments` defaults on. **No new permission
code**, so no role reconcile; a CRM rep who is to send needs an Engage role, which is an owner action.
**Docs and pins (not a deploy step):** `docs/crm.md` for EV16 and EV17, and the recipe-trigger obligation for
the EV41 key `crm.lead.assigned`, land in the commits of the producers and recipes they describe (§7.7).
*Memory:* no fold and no container; the handlers join consumers that already run in `notification` and
`deal`, so `memory_budget_totals.py` is unchanged from R4.1 (3747 MB, 3944 MB with `admin`, plus the
folds of earlier releases).

### 7.9 Rollback

Code rolls back to the previous images; the schema stays. Projected rows remain with `origin = 'crm'`
and become editable again under the old code (harmless; the next deploy re-syncs through H9). The
touch events published after a rollback of notification stop. *Not reversible:* a message already sent;
an erase already applied. *Stop conditions:* any `consent_events` row appears from the projection; a
second `notification.contacts` row for one `crm_contact_id`; a send to a contact whose ledger verdict was
denied; an in-app alert addressed to a membership that is not the new owner; `deal.events.dlq` or
`notification.events.dlq` depth rises after the deploy; **a rollback of notification or deal while its queue
carries live traffic without H9 and H14 ready to run when it returns**. The levers that lose nothing are
configuration: switch `log_conversations_as_activity` and `notify_assignments` off.

**What a code rollback loses (§4.7).**

| Consumer (queue) | Events a rollback discards | Catch-up |
|---|---|---|
| `project_crm_contact` (`notification.events`: contact created, updated, deleted) | projection updates for CRM contacts changed while the old image runs | **catch-up:** H9 `plan`, then `apply`, re-reads state through C53 |
| `erase_crm_contact_copies` (`notification.events`: `crm.book.contact.erased`) | erasures while the old image runs | **catch-up:** H14 `--service notification` |
| the rule `fold` for the EV40 and EV41 keys, and the three `SYSTEM_IN_APP_RULES` entries (`notification.events`) | tenant-rule firings and assignment bell alerts raised while the old image runs | **forward-only:** an alert reports a moment and the ownership change itself stays visible in CRM; a missed tenant automation is the tenant's to re-trigger |
| `conversation_touched_activity` (`deal.events`) | the system activity of each conversation-day while deal runs the old image | **forward-only:** the activity is an opt-in convenience (default off) and the conversation itself is untouched in Engage |

### 7.10 Acceptance criteria

- [ ] `notification.contacts.crm_contact_id`, `origin`, the two CHECKs and `uq_contact_org_crm_contact`; `conversations.crm_contact_id`, `crm_company_id`, `owner_membership_id`; `messages.crm_contact_id`, `entity_ref`; `deal.activities.origin`, `ref_type`, `ref_id` and `uq_activities_org_conversation_day` exist; `make migration-roundtrip` is green in both services.
- [ ] After H9 and the event path, an organization with `n` CRM contacts that have an e-mail has `n` projected rows, zero `consent_events` written by the projection, and a marketing audience of zero without an opt-in.
- [ ] A projected row cannot be edited or deleted through the Engage API while the organization is entitled to `crm` (409 `managed_in_crm`), and can after the entitlement lapses.
- [ ] `contacts:lookup-channels` for 100 ids makes one C59 call and a constant number of queries, and returns `null` per item rather than failing.
- [ ] C60 refuses without consent (409 with the ledger reason and a kept `suppressed` row), without an address (422), outside the service window (409), and answers 202 once for a repeated key.
- [ ] One WhatsApp conversation with ten messages in a day and direction produces exactly one activity per direction, with `body = NULL`, subject `"WhatsApp conversation"` and no name or number in the row, its audit row or its event.
- [ ] A lead, deal and company reassignment each produce one in-app notification for the new owner with `entity_ref`, none when the owner is unchanged or is the actor, none when `notify_assignments` is off; the three alerts are entries of SP3's `SYSTEM_IN_APP_RULES` (one built-in table, no `BUILTIN_CRM_ALERTS`) and SP3's own three entries now carry `entity_ref` too.
- [ ] An e-mail sent from the CRM is marketing unless its template's `category` is `transactional`, which only an administrator sets (audited); a `transactional` purpose on any other template is 422, so a rep cannot bypass an opt-in by labelling.
- [ ] After `crm.book.contact.erased`, the contact's projected row, campaign-recipient copies, conversation number and bodies and send addresses are overwritten, and no `notification.audit_logs` row of its Engage contact holds the old name or masked e-mail; consent rows are untouched.
- [ ] STOP and an unsubscribe link each opt the address out of marketing only; ADR-0021 and the whatsapp architecture note say so.
- [ ] Every new event and subscribable key has a committed schema validated through its real write path.
- [ ] `docs/crm.md` carries EV16 and EV17 and `test_crm_doc_matches_contracts.py` passes; `crm.lead.assigned` is in `SUBSCRIBABLE_EVENTS` and the recipe-trigger test (`test_engage_recipe_triggers.py`) covers it, both in the same commit as its recipe.
- [ ] The conversation backfill ran in batches after the DDL committed and never held the `ALTER`'s lock; `notification.audit_logs` rewrites go through helpers that leave non-objects alone and refuse another tenant's id.
- [ ] Every new view has loading, empty, error and forbidden states at phone width, no new token-baseline count, a story per new `@opsgrids/ui` export, a fixture and `MockRoute` per new `GET`, and no new axe violation.
- [ ] `make check` and `make gate` pass locally; no new container; `EDGE_BUDGET_ACCEPTED_MB` is not touched.

## 8. R4.3 Money loop

### 8.1 Purpose and needs

Make an order, an invoice and a payment belong to the same CRM company, in both directions. Finance's
billing profile is keyed on the company (several GSTIN profiles per company); orders carry the CRM ids
and are wired to customer and finance; an accepted order resolves its billing profile and checks the
credit position; a ready order requests a correct invoice; a verified online capture becomes a finance
payment and allocation; the customer service folds finance and operations events into a per-company
projection that fills the 360's `orders` and `finance` sections and the company page; in an organization
whose administrator has turned it on (D-34, default off) the first accepted order promotes a prospect to a
customer (and, on the owner's extension, so does the first won deal or paid invoice); existing finance
customers are linked to companies by an owner-reviewed tool. The release also moves finance onto the shared
`formatMoney` and resolves billed-party names by id instead of by a 200-row page.

**Needs:** R4.1 (C50, C51, C52, enriched events, finance gap closure), R3.1 (company tax identity, A15
`platform_core.gst`, `company_tax_ids` for C50 and for the link tool), R2.7, **R3.2** (the `/api/v1/companies`
prefix and the `ids` filter behind C24 and `useCompanyLookup`: registry C21, C35, C67) and **R3.3** (G4
`formatMoney` and G5 `Customer360View`, which render the new sections and which finance moves onto). D-14
(pending D-14), D-22 (pending D-22), D-28, D-29 and D-34 (pending D-34) are the open decisions. R4.2 is not
required. This is recorded as part of deviation 2 in §17.

### 8.2 Data model

**finance** (one revision on `faf3ff81d039`; `lock_timeout 5s`; named constraints; the new table gets
`ENABLE` + `FORCE` row-level security and the `tenant_isolation` policy through the revision's own
`_rls()` helper, `opsgrids_finance` grants through `apply_schema_grants`, and joins the finance
`TENANT_TABLES` list in `services/finance/tests/conftest.py`):

```sql
-- B41, B43: the billing profile of a company; one company, several GSTIN profiles
ALTER TABLE finance.customers ADD COLUMN crm_company_id UUID NULL;
ALTER TABLE finance.customers ADD COLUMN payment_terms_days INTEGER NULL;
ALTER TABLE finance.customers ADD COLUMN credit_limit_minor BIGINT NULL;
ALTER TABLE finance.customers ADD COLUMN credit_limit_currency VARCHAR(3) NULL;
ALTER TABLE finance.customers ADD CONSTRAINT ck_customers_payment_terms
    CHECK (payment_terms_days IS NULL OR payment_terms_days BETWEEN 0 AND 365);
ALTER TABLE finance.customers ADD CONSTRAINT ck_customers_credit_limit
    CHECK ((credit_limit_minor IS NULL) = (credit_limit_currency IS NULL)
           AND (credit_limit_minor IS NULL OR credit_limit_minor >= 0));
CREATE INDEX ix_customers_org_crm_company ON finance.customers (organization_id, crm_company_id)
    WHERE crm_company_id IS NOT NULL;
CREATE UNIQUE INDEX uq_customers_org_company_gstin
    ON finance.customers (organization_id, crm_company_id, (coalesce(gstin, '')))
    WHERE crm_company_id IS NOT NULL AND deleted_at IS NULL;
-- uq_customers_org_crm_contact (B42) is NOT touched: it keeps R4.3 rollback-tolerant (see registry B42)

-- B44 (SP4 amends it to nullable): a payment records its currency. NULL means "not stated": a legacy payment
-- whose invoices span several currencies stays NULL, and a previous image that inserts a payment after a
-- rollback writes NULL, never a wrong 'INR'
ALTER TABLE finance.payments ADD COLUMN currency VARCHAR(3) NULL;
ALTER TABLE finance.payments ADD CONSTRAINT ck_payments_currency_iso CHECK (currency IS NULL OR length(currency) = 3);
-- the backfill is not in this transaction: it runs in batches afterwards (below)

-- B58 + SP4: the frozen header gains the party snapshot, written once at creation, never updated
ALTER TABLE finance.invoices ADD COLUMN crm_company_id UUID NULL;
ALTER TABLE finance.invoices ADD COLUMN source_type VARCHAR(20) NULL;     -- R4.5 writes 'quote'
ALTER TABLE finance.invoices ADD COLUMN source_id UUID NULL;
ALTER TABLE finance.invoices ADD COLUMN buyer_snapshot JSONB NULL;        -- SP4, see below
ALTER TABLE finance.invoices ADD CONSTRAINT ck_invoices_buyer_snapshot_shape
    CHECK (buyer_snapshot IS NULL OR jsonb_typeof(buyer_snapshot) = 'object');
CREATE INDEX ix_invoices_org_crm_company ON finance.invoices (organization_id, crm_company_id)
    WHERE crm_company_id IS NOT NULL;

-- SP4 (B82): a verified online capture, parked until it can be applied to its invoice
CREATE TABLE finance.online_captures (
    id UUID PRIMARY KEY,                       -- TenantBase columns: id, organization_id, created_at,
    organization_id UUID NOT NULL,             --   updated_at, created_by, updated_by, deleted_at
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by VARCHAR(64), updated_by VARCHAR(64), deleted_at TIMESTAMPTZ,
    charge_id UUID NOT NULL,                   -- payment.collections charge id
    reference_type VARCHAR(40) NOT NULL,       -- 'operations.order' now, 'finance.invoice' from R4.5
    reference_id UUID NOT NULL,                -- order id or invoice id (plain UUID)
    amount_minor BIGINT NOT NULL,
    currency VARCHAR(3) NOT NULL,
    provider VARCHAR(40) NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    state VARCHAR(12) NOT NULL DEFAULT 'pending',
    allocated_minor BIGINT NOT NULL DEFAULT 0,
    payment_id UUID NULL REFERENCES finance.payments(id) ON DELETE RESTRICT,   -- same schema
    applied_at TIMESTAMPTZ NULL,
    unmatched_reason VARCHAR(40) NULL,
    refunded_total_minor BIGINT NOT NULL DEFAULT 0,
    refund_noted_at TIMESTAMPTZ NULL,
    CONSTRAINT ck_online_captures_state CHECK (state IN ('pending', 'applied', 'unmatched')),
    CONSTRAINT ck_online_captures_amount CHECK (amount_minor > 0 AND allocated_minor BETWEEN 0 AND amount_minor
                                                AND refunded_total_minor >= 0),
    CONSTRAINT ck_online_captures_currency CHECK (length(currency) = 3),
    CONSTRAINT ck_online_captures_reference CHECK (reference_type IN ('operations.order', 'finance.invoice')),
    CONSTRAINT ck_online_captures_applied CHECK ((state = 'applied') = (payment_id IS NOT NULL))
);
CREATE UNIQUE INDEX uq_online_captures_org_charge ON finance.online_captures (organization_id, charge_id);
CREATE INDEX ix_online_captures_org_reference ON finance.online_captures (organization_id, reference_id);
CREATE INDEX ix_online_captures_org_state ON finance.online_captures (organization_id, state)
    WHERE state <> 'applied';
-- ix_online_captures_organization_id comes from OrgScopedMixin like every TenantBase table
-- RLS: ALTER TABLE ... ENABLE and FORCE ROW LEVEL SECURITY; CREATE POLICY tenant_isolation ... USING and WITH CHECK
--      (organization_id = finance.current_org()) TO opsgrids_finance, in this same revision.
```

**`payments.currency` (B44, amended) is a named exception to "the payment ledger is append-only".** The
model docstring states that rule (`services/finance/app/models.py:11-20`), but no trigger guards `payments`
(S48), and this release writes **one new column of existing rows, once**: never an amount, a date, a
reference or an allocation. The exception is named in the PR and in registry B44. The first-draft `NOT NULL DEFAULT
'INR'` is not used: it would stamp every legacy payment `INR` and, after a rollback, let the previous image
write `INR` for a USD payment, a wrong value that looks right. The column is **nullable**, and the backfill is
**batched and outside the DDL transaction** (an `UPDATE` in the same transaction as the `ALTER TABLE` would
hold the `ALTER`'s `ACCESS EXCLUSIVE` lock on `payments` for the whole update): after the DDL has committed, an
autocommit block runs, per batch of 1000 payments, an `UPDATE finance.payments` that sets `currency` from
`min(i.currency)` over the payment's allocations joined to their invoices **`HAVING count(DISTINCT i.currency)
= 1`** and only `WHERE p.currency IS NULL`, each batch its own short transaction under `lock_timeout 5s`,
idempotent. A payment whose allocations span several currencies (`record_payment` never checked, S48), or has
none, is **not guessed**: it keeps `NULL`, and the migration logs how many, counts only, for an owner decision
(§13 step 5). `NOT NULL` is a later contractive release, after that decision and after R4.3 has soaked. From R4.1
`record_payment` refuses an allocation set spanning currencies (422 `mixed_currency_payment`), so no new mixed
payment appears, and from R4.3 it writes `currency` on every payment.

**The sweep's discovery grant.** `reconcile_online_captures` (§8.4.4) finds its organizations on the narrow
outbox role, whose finance grant is `_SWEEP_READER_COLUMNS` in `services/finance/migrations/env.py:36-76`:
`REVOKE ALL`, then a column-level `GRANT SELECT`, on every migration run (S47). `online_captures` is not in
it, so without a new entry the sweep's discovery would fail with `42501` or find nothing, and a pending capture
would never be applied or marked unmatched: the silent stall the repository's engineering rules warn about for
the outbox role (nothing errors, the work just stops), and the failure operations met on 2026-10-01. The revision therefore adds `"online_captures": ("organization_id", "id", "state",
"created_at", "deleted_at")` to that dict (the sweep also reads `invoices` for organizations with an open
invoice, columns the dict already grants). `tests/infra/test_sweep_discovery_grants.py` and a sweep test run as
the narrow role close the class (§8.7).

*`buyer_snapshot`* (amendment of B58): `{legal_name, billing_address: {line1, line2, city, state_code, state,
postal_code, country}, gstin_state_code}`, business identity only (never an e-mail, a phone or a contact
name), written at creation from the billing profile and, when the profile is linked, from C50. It exists
because an issued invoice today snapshots only the two GSTINs and the place of supply (S23), so a
rename of the billed party rewrites how every issued invoice reads, and R4.5's PDF would not be
reproducible. Existing invoices keep `NULL` and render from the live profile, flagged "as at today".

**Audit scrub** (SD-10, the shape of §6.2, in the same revision). A billed party's `customer.updated` audit
rows copy `contact_name` and `email` into `before` and `after` (`services/finance/app/services.py:380-436`;
`customer.created` holds only `{name, gstin}`), so the erase must reach them: `finance.has_pii_keys(j)` over
`['contact_name','email']`, `finance.erase_pii_keys(j)` (`contact_name` becomes `Erased contact`, `email`
becomes `[erased]`) and `finance.scrub_customer_audit(p_organization_id uuid, p_customer_id uuid)`, `SECURITY
DEFINER`, `SET search_path = finance, pg_temp`, owner `opsgrids_admin`, `EXECUTE` revoked from `PUBLIC` and
granted to `opsgrids_finance` only; it rewrites rows with `entity = 'customer'` and that `entity_id` in that
organization and nothing else. Like customer's (§6.2), the helpers act only on JSON objects and the function
raises unless `p_organization_id` equals `finance.current_org()`. From R4.3 `customer.updated` records
`changed` field names only for those two keys (the event is already reduced to `{customer_id, changed[]}`,
§4.3), so no new copy accumulates. The downgrade drops the three functions.

**operations** (one revision on `d7a3c9e5b184`; `# RE-PARENT HERE`: two unmerged inventory stacks share
this head; same conventions):

```sql
-- B45: the order carries the CRM link and the resolved billing profile
ALTER TABLE operations.orders ADD COLUMN crm_company_id UUID NULL;
ALTER TABLE operations.orders ADD COLUMN crm_contact_id UUID NULL;
ALTER TABLE operations.orders ADD COLUMN finance_customer_id UUID NULL;
CREATE INDEX ix_orders_org_crm_company ON operations.orders (organization_id, crm_company_id)
    WHERE crm_company_id IS NOT NULL;
CREATE INDEX ix_orders_org_crm_contact ON operations.orders (organization_id, crm_contact_id)
    WHERE crm_contact_id IS NOT NULL;
-- R4.3-S (D-29): the opt-in of SD-7, and the per-organization opt-in of the order-to-invoice chain (SD-8);
-- both are written only through the R4.3-S route, never by a migration or by the box variable
ALTER TABLE operations.fulfilment_settings ADD COLUMN create_crm_party_on_order BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE operations.fulfilment_settings ADD COLUMN order_invoice_chain_enabled BOOLEAN NOT NULL DEFAULT false;
```

`orders.customer_id` stays and **mirrors `crm_contact_id`** from now on (dual write) until a later
contractive release; `customer_name` and `customer_phone` stay as snapshots (S19).

**customer** (one revision after R4.1's; `# RE-PARENT HERE`; every table below is `ENABLE` + `FORCE` with
`tenant_isolation` and joins the customer `TENANT_TABLES`; the three business tables use a composite
primary key that starts with `organization_id` as B15 does, so they have no `id` or soft-delete column):

```sql
-- B79: per-document state, version-guarded (SD-5). Never summed across sources or currencies.
CREATE TABLE customer.party_documents (
    organization_id UUID NOT NULL,
    source VARCHAR(12) NOT NULL,                   -- 'finance' | 'operations'
    document_id UUID NOT NULL,                     -- invoice id or order id
    company_id UUID NOT NULL,                      -- crm_company_id carried by the event
    contact_id UUID NULL,
    kind VARCHAR(12) NOT NULL,                     -- 'invoice' | 'credit_note' | 'order'
    number VARCHAR(40) NOT NULL,                   -- a document number, never a person's name
    currency VARCHAR(3) NOT NULL,
    total_minor BIGINT NOT NULL,                   -- negative for a credit note
    status VARCHAR(20) NOT NULL,                   -- invoice: sent|paid|void|draft; order: placed|accepted|cancelled|handed_over
    issued_on DATE NULL, due_on DATE NULL,         -- invoices only
    occurred_at TIMESTAMPTZ NOT NULL,              -- issued / placed
    source_occurred_at TIMESTAMPTZ NOT NULL,       -- the version: applied only if strictly newer
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, source, document_id),
    CONSTRAINT ck_party_documents_source CHECK (source IN ('finance', 'operations')),
    CONSTRAINT ck_party_documents_kind CHECK (kind IN ('invoice', 'credit_note', 'order')),
    CONSTRAINT ck_party_documents_currency CHECK (length(currency) = 3)
);
CREATE INDEX ix_party_documents_org_company ON customer.party_documents
    (organization_id, company_id, kind, occurred_at DESC);

-- B80: one row per (payment, invoice) allocation; the invoice's paid figure is their sum
CREATE TABLE customer.party_payments (
    organization_id UUID NOT NULL, payment_id UUID NOT NULL, invoice_id UUID NOT NULL,
    company_id UUID NOT NULL, currency VARCHAR(3) NOT NULL,
    amount_minor BIGINT NOT NULL CHECK (amount_minor > 0),
    received_on DATE NOT NULL, source_occurred_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, payment_id, invoice_id)
);
CREATE INDEX ix_party_payments_org_invoice ON customer.party_payments (organization_id, invoice_id);
CREATE INDEX ix_party_payments_org_company ON customer.party_payments (organization_id, company_id, received_on DESC);

-- B15 exactly as registered: a derived cache, recomputed from the two tables above, per currency
CREATE TABLE customer.party_rollup (
    organization_id UUID NOT NULL, company_id UUID NOT NULL, currency VARCHAR(3) NOT NULL,
    receivable_minor BIGINT NOT NULL DEFAULT 0, overdue_minor BIGINT NOT NULL DEFAULT 0,
    billed_lifetime_minor BIGINT NOT NULL DEFAULT 0, paid_lifetime_minor BIGINT NOT NULL DEFAULT 0,
    open_orders INTEGER NOT NULL DEFAULT 0,
    last_order_at TIMESTAMPTZ NULL, last_invoice_at TIMESTAMPTZ NULL, last_payment_at TIMESTAMPTZ NULL,
    as_of TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, company_id, currency)
);

-- B16 exactly as registered: PII-free, append-only, idempotent on the source event
CREATE TABLE customer.party_timeline (
    id UUID PRIMARY KEY, organization_id UUID NOT NULL, company_id UUID NOT NULL, contact_id UUID NULL,
    kind VARCHAR(40) NOT NULL, occurred_at TIMESTAMPTZ NOT NULL,
    ref_type VARCHAR(40) NOT NULL, ref_id UUID NOT NULL,
    title VARCHAR(160) NOT NULL,                   -- a document number or a generic label
    source_event_id UUID NOT NULL,
    CONSTRAINT ck_party_timeline_kind CHECK (kind IN ('invoice_issued', 'payment_received', 'order_placed',
        'order_delivered', 'ticket_opened', 'ticket_solved', 'conversation_touched', 'quote_sent',
        'quote_accepted', 'project_started'))
);
CREATE UNIQUE INDEX uq_party_timeline_org_event ON customer.party_timeline (organization_id, source_event_id);
-- SP4 (B16 amended): one `conversation_touched` line per conversation and UTC day, however many messages it
-- carries (the touch event is staged per message), the shape of deal's `uq_activities_org_conversation_day`
CREATE UNIQUE INDEX uq_party_timeline_org_conversation_day ON customer.party_timeline
    (organization_id, ref_id, ((occurred_at AT TIME ZONE 'UTC')::date)) WHERE kind = 'conversation_touched';
CREATE INDEX ix_party_timeline_org_company ON customer.party_timeline (organization_id, company_id, occurred_at DESC);
CREATE FUNCTION customer.forbid_party_timeline_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'party_timeline is append-only: % is not permitted', TG_OP; END; $$;
CREATE TRIGGER trg_party_timeline_append_only BEFORE UPDATE OR DELETE ON customer.party_timeline
    FOR EACH ROW EXECUTE FUNCTION customer.forbid_party_timeline_mutation();

-- B81: automation promotes a company at most once, so a person's later edit is never overwritten
ALTER TABLE customer.companies ADD COLUMN lifecycle_promoted_at TIMESTAMPTZ NULL;

-- B83: one row per organization, lazily created by the first PUT (the shape of deal.pipeline_settings, B27);
-- an absent row means every default. A TenantBase table: id, organization_id, created_at, updated_at,
-- created_by, updated_by, deleted_at
CREATE TABLE customer.customer_settings (
    id UUID PRIMARY KEY, organization_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by VARCHAR(64), updated_by VARCHAR(64), deleted_at TIMESTAMPTZ,
    lifecycle_promotion VARCHAR(24) NOT NULL DEFAULT 'off',     -- D-34: off | first_order | first_commercial_event
    CONSTRAINT ck_customer_settings_lifecycle
        CHECK (lifecycle_promotion IN ('off', 'first_order', 'first_commercial_event'))
);
CREATE UNIQUE INDEX uq_customer_settings_org ON customer.customer_settings (organization_id)
    WHERE deleted_at IS NULL;
```

**The customer sweeps' discovery grant is not in this revision.** `PartyRollupSweep` (here) and the orphan
sweep (R4.7) discover organizations on the narrow outbox role, and a bare `GRANT` written in a revision is the
wrong place for it: `apply_schema_grants` runs after every migration, and the repo's own incident (S47) is a
sweep that shipped without its grant and failed every tick, silently. The grant lives in
`services/customer/migrations/env.py`, in the shape of operations' `grant_reconcile_discovery`
(`services/operations/migrations/env.py:96-157`): a dict `SWEEP_DISCOVERY_TABLES = {"companies":
("organization_id", "deleted_at"), "party_documents": ("organization_id", "kind", "status")}` (exactly the
columns the two sweeps' discovery queries read, and not one more), and a `grant_sweep_discovery(connection)`
that, after `apply_schema_grants` and on **every** run (upgrade and downgrade), is guarded on the role existing
(`pg_roles`) and, per table that exists, does `REVOKE ALL` and then a column-level `GRANT SELECT`. R4.7's sweep
adds nothing to the dict if it reads the same columns, and a change to either sweep's discovery SQL changes
the dict in the same commit (`tests/infra/test_sweep_discovery_grants.py`, §8.7).

The timeline trigger is the `deal.activities` pattern (`e94d7a2c5f18:90-107`); the purge tool lifts every
`DELETE` trigger by name inside each organization's own transaction (`tools/purge_test_orgs.py:62-72`,
ADR-0036 decision 6), so it needs no change, and every new table has `organization_id`, so
`tests/infra/test_purge_test_orgs_db.py`'s unreached list does not grow. **Downgrades** of all three
services drop what they added and **refuse** (`RuntimeError`, override
`OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`) while any `online_captures`, `party_*`, `customer_settings`,
`crm_company_id`, `finance_customer_id`, `buyer_snapshot` or `payments.currency` value exists, or any
`order_invoice_chain_enabled` is true; on an empty database they reverse fully for
`make migration-roundtrip`. The projection tables are rebuildable with H4, which is why a code rollback
(not a downgrade) is the supported path.

### 8.3 Backend contract

**finance, internal** (callers, org claim, three negative tests and an unentitled-organization test for
each; `finance` answers 503 `crm_unavailable` when customer cannot be asked, never a placeholder)

| Handle | Route | Callers | Request → response |
|---|---|---|---|
| C61 | `POST .../customers:resolve-for-crm-company` | operations, customer | `{crm_company_id, crm_contact_id?, gstin?}` → 200 `{customer_id, created}`. Lookup before create inside the savepoint and unique-index race handling of `ensure_customer_for_crm_contact` (`services/finance/app/services.py:506-640`); the company's facts come from C50, read **before** the write transaction (404 `company_not_found` when C50 says so). Choice among a company's profiles: the one whose `gstin` equals the request's, else the profile that holds `crm_contact_id`, else the oldest live one; a new profile takes the company's primary GSTIN and billing address and sets `crm_contact_id` only when no other profile of the company holds a contact. The contact-keyed `customers:resolve-for-crm-contact` (`.../api/internal.py:366`) is **kept**, caller `operations`, and now delegates here when the contact's company is known |
| C62 | `GET .../customers/credit-position?crm_company_id=` | operations | `{positions: [{currency, credit_limit_minor, outstanding_minor, overdue_minor}]}`, one entry per currency **across every billing profile of the company**; `outstanding` and `overdue` are summed over the profiles by finance's own rules (`app/domain.py:2640-2647`, effective status as of the UTC date), the limit is the sum of limits that are set and `null` when none is, nothing is summed across currencies; a company with no profile answers `{positions: []}`. Read-only: it creates no row |
| C63 | `GET .../invoices:export`, `GET .../payments:export`; operations `GET .../orders:export` | customer | keyset on `(updated_at, id)`, `limit` ≤ 500, the same state the events carry (`document_id, kind, number, currency, total_minor, status, issued_on, due_on, occurred_at, crm_company_id, crm_contact_id`; payments with their allocations and `currency`); the caller allow-list gains `customer`; PII-free |

**payment, internal** (caller `finance`, org claim, three negative tests and a cross-tenant test; this is the catch-up for
lost capture events, §8.4.4 and §4.7)

| Handle | Route | Caller | Request → response |
|---|---|---|---|
| C105 | `GET .../collections:export-captured?after_updated_at=&after_id=&limit=&since=` | finance | keyset on `(updated_at, id)`, `limit` ≤ 500, merchant charges (`payee = 'merchant'`) in state `captured` with `updated_at` at or after `since` (a date, at most 90 days back, default the last 30): `{charge_id, reference_type, reference_id, amount_minor, currency, provider, captured_at, refunded_total_minor, updated_at}`. No payer, no provider payment id, no provider order id. `captured_at` is the time of the transition to `captured` recorded in the charge's status history (`services/payment/app/models.py:698-735`: inference, the PR confirms) and `refunded_total_minor` is the sum of the charge's refunds. It sits beside the existing per-reference collections routes whose allow-list stays `operations` (`services/payment/app/api/collections_internal.py:51`); only this route admits `finance` in R4.3, and R4.5-C widens `COLLECTION_CALLERS` for the invoice checkout |

**finance, public** (`/api/v1/finance`, entitlement `finance`; permissions exist)

| Route | Permission | Change |
|---|---|---|
| `GET /finance/customers` | `finance.billing.customer.read` (ORG) | filters `crm_company_id` and `ids` (comma list ≤ 100: the by-id resolution that replaces the 200-row name map, S42); `CustomerOut` gains `crm_company_id`, `crm_contact_id`, `payment_terms_days`, `credit_limit_minor`, `credit_limit_currency` |
| `POST /finance/customers`, `PATCH /finance/customers/{id}` | `finance.billing.customer.write` (ORG) | accept the same fields. A non-null `crm_company_id` is proved through C50 in a claim/call/record step before the write (SD-2): 404 `crm_company_not_found`, 503 `crm_unavailable`, 409 `profile_exists` when `(company, gstin)` already has a live profile. A profile whose GSTIN is not among the company's tax ids is saved with the response header `X-Warning: gstin_not_on_company` (a company may hold more registrations than CRM knows) |
| `GET /finance/online-captures?state=` | `finance.billing.payment.read` (ORG) | **new**: `Page[OnlineCaptureOut {id, charge_id, reference_type, amount_minor, currency, captured_at, state, unmatched_reason, refunded_total_minor}]`; the Payments screen shows a banner "n online payments need a match" |
| `GET /finance/payments`, `PaymentOut` | existing | gains `currency`; `method` may be `online` |
| invoice reads | existing | gain `crm_company_id` (nullable) |

**operations, public** (`/api/v1/operations`, entitlement `operations`)

| Route | Permission | Change |
|---|---|---|
| `POST /operations/orders`, `PATCH` | `operations.orders.order.create` / `.write` (ORG) | body gains `crm_company_id?` (proved through C50), `crm_contact_id?` (proved through C89, which also answers its company; `customer_id` stays accepted and means `crm_contact_id`); the call is made before the write transaction (the route uses the raw-session shape `accept` already uses); `OrderOut` gains `crm_company_id`, `crm_contact_id`, `finance_customer_id` and `crm_suggestion {company_id, contact_id} \| null` (ids only: the unconfirmed phone match of §8.4.2) |
| `POST /operations/orders/{order_id}:confirm-crm-link` (C107, **SP4 addition**) | `operations.orders.order.write` (ORG) | no body: it confirms only the suggestion the server itself stored at accept (`settings_snapshot.crm_suggestion`), never a company the caller names; both ids are **re-proved** through C50 and C89 before the write (SD-2); it sets `crm_company_id`, `crm_contact_id` and the mirrored `customer_id`, resolves the finance profile through C61, and writes the audit row `order.crm_link_confirmed` (ids only). 409 `no_suggestion` when none is stored or the order is already linked, 404 a foreign order, 503 `crm_unavailable` |
| `GET /operations/fulfilment/settings` (R4.3-S, D-29) | `operations.orders.order.read` (ORG) | `{invoice_timing, default_tax_rate_pct, create_crm_party_on_order, order_invoice_chain_enabled, source: "row" \| "default"}` for the organization-default row; `source` is `default` exactly when no row exists, which is when `resolve_settings` answers its hard-coded rate 0 (S50) |
| `PUT /operations/fulfilment/settings` (R4.3-S) | **new** `operations.fulfilment.settings.write` (dangerous, default ORG, max ORG), `minimum=Scope.ORG` | body `{invoice_timing: "on_accept" \| "on_dispatch" \| "on_delivery", default_tax_rate_pct: decimal 0.00–100.00, create_crm_party_on_order: bool, order_invoice_chain_enabled: bool}` (`extra="forbid"`, every field required, so any row this route writes states its rate on purpose, even `0.00` for a zero-rated seller); upserts the row with `warehouse_id IS NULL`; one audit row `fulfilment.settings.updated` with before and after (numbers, enums and the two flags only; turning `order_invoice_chain_enabled` on is the audited act after which orders start raising tax invoices); no event |

**customer, public**

| Route | Permission | Change |
|---|---|---|
| `GET /contacts/{contact_id}/360` (C23) | `crm.book.contact.read` + `assert_can_touch` + `Entitled("crm")` | `orders` and `finance` leave `MISSING_UPSTREAMS` and are filled from the projection for the contact's company **only when** the caller holds `crm.book.company.read_commercial` and may touch the company's owner; otherwise they stay `null` and named in `unavailable`. `OrdersSection` and `FinanceSection` each gain `by_currency` (§8.5) |
| `GET /companies/{company_id}/360` (C24) | `crm.book.company.read` + `assert_can_touch`; the money sections additionally `crm.book.company.read_commercial` | `Company360 {company, contacts (≤ 20), crm, orders, finance, support, delivery, timeline (≤ 20 entries), unavailable}`; support and delivery are `null` until R4.4 |
| `crm.book.company.read_commercial` (E23) | catalogue | dangerous: no; default OWN, max ORG; description "See a company's receivables, orders, credit position and cross-product timeline" (the description is final before the first reconcile, which never rewrites metadata) |
| `DELETE /companies/{id}` | existing | **409 `has_commercial_history`** while any `party_documents` row exists for the company (SD-20) |
| `GET /companies/settings`, `PUT /companies/settings` (C106, **SP4 addition**; the `/customers/settings` alias comes with the shared router) | `GET`: `crm.book.company.read` (any scope). `PUT`: `crm.book.company.write` with `minimum=Scope.ORG` | `CustomerSettingsOut {lifecycle_promotion: "off" \| "first_order" \| "first_commercial_event", materialised}`; defaults and `materialised: false` when no row, and a `GET` never writes; `PUT` upserts the one row and writes the audit row `customer_settings.updated` (before and after, an enum), no event; 422 on any other value. Declared before `/{company_id}`. An OWN holder cannot change what promotes every company in the organization (D-34) |

**Events.** `crm.book.company.updated` with `changed: ["status"]` for a lifecycle promotion (EV2, only for an
organization whose D-34 setting allows it); `finance.orders.invoice.issued` joins `SUBSCRIBABLE_EVENTS`
(EV42: one line in `services/notification/app/domain.py` in the same commit as a test that the key has a
committed schema, and a deploy of **notification** before the chain flips, §8.8); the
finance, operations and payment enrichments of §4.3 (EV33, EV34, EV38) with their committed schemas;
`operations.orders.order.invoice_requested` lines gain optional `hsn_sac` (string, 4 to 8 digits) and the
payload may omit `place_of_supply` and `supply_type` (the schema's `default: b2b` is documentation, not
applied by any consumer). The six order schemas `libs/contracts/events/operations.orders.order.{placed,accepted,held,cancelled,ready,handed_over}`
are committed here (EV34; today only `invoice_requested` has one). **Settings.** On operations, `customer_internal_url`, `finance_internal_url` (each
with `*_timeout_seconds: float = 5.0`), `order_invoice_chain_mode: Literal["off","log_only","on"] =
"log_only"` and `credit_check_mode: Literal["off","warn","refuse"] = "warn"`; on finance
`customer_internal_url` exists (`services/finance/app/config.py:52`) and gains its compose line, and finance
gains `payment_service_url` (the name operations already uses for the same callee,
`services/operations/app/config.py:59`) with `payment_timeout_seconds: float = 5.0` for C105; on
customer `finance_internal_url`, `operations_internal_url`, `run_sweeps_in_api: bool = False`, and (from R4.1)
`product_service_url` and `product_timeout_seconds`. The
compose lines are in §8.8 (wiring rows W3, W4, W15, W16, W27, W37).

### 8.4 Domain rules

**8.4.1 The billing profile.** A finance customer is **the billed party** (P§4.1). It may link to one CRM
company; one company may have several profiles, one per GSTIN (`uq_customers_org_company_gstin`:
`coalesce(gstin, '')` makes "no GSTIN" a single profile of its own). Terms and the credit limit live on the
profile because finance owns receivables; CRM displays them and quotes snapshot them (R4.5). Editing a
profile never touches an issued invoice (S23), and from R4.3 the invoice carries its own
`buyer_snapshot`. A profile with `crm_company_id` NULL behaves exactly as today.

**8.4.2 Accepting an order.** `accept_order` keeps its claim, call, record shape
(`services/operations/app/orders/services.py:483-600`). The **call** step, with no session open, now
(a) when the order has no CRM link and a buyer phone, **resolves** by phone through C52 (a read: SD-1) and,
on one unambiguous match, records only a **suggestion** (`settings_snapshot["crm_suggestion"] = {company_id,
contact_id}`, ids only) and the audit row `order.crm_link_suggested`; **it links nothing** (SD-28). A shared or
reused number would otherwise choose the legal buyer, GSTIN and place of supply of a tax invoice (and its
e-invoice filing) for an operator who holds no CRM permission, and a suggestion feeds neither the credit check
nor the invoice. The link is written only by (i) a company or contact chosen at order entry (the `POST` or
`PATCH` body, proved through C50 or C89; audit row `order.crm_linked`, basis `explicit`), (ii) an operator's
`POST /operations/orders/{order_id}:confirm-crm-link` (C107, basis `confirmed`), or (iii) step (a2), basis
`created`. The order detail tells the operator "A CRM account may match this phone number" beside a **Confirm
link** action, and names the suggested company only to a viewer who can read it in the CRM (the SPA reads it
as that viewer, under `crm.book.company.read` and the `crm` entitlement); (a2) when still unlinked, the
setting `create_crm_party_on_order` is on and the order has a buyer name and phone, calls C51 with
`company.individual = true` (§6.3), so a one-person company is never reused by name; a phone that already
belongs to another company's contact is C51's 409 `contact_company_mismatch` (§6.4.1), which the order records
as `crm_ambiguous`, so no existing record is ever linked by phone alone. An ambiguous or unreachable
answer leaves the order unlinked with an audit reason (`crm_ambiguous`, `crm_unavailable`) and never
blocks the accept; (b) when the order has a `crm_company_id`, calls C61 and, for a credit-terms accept only (`payment_term_for(channel) == "credit"`,
as today), C62. The **record** step persists `finance_customer_id`, `crm_company_id` and
`crm_contact_id`, and `settings_snapshot["credit_check"]`: `ok`, `warn` (with `{currency, limit_minor,
outstanding_minor, order_total_minor}`, numbers only), `not_checked` (finance or CRM unreachable, or the
organization is not entitled to finance), or `not_applicable` (not a credit-terms accept, or no company).
`credit_check_mode = warn` records and logs but accepts; `refuse` answers 409 `credit_limit_exceeded` with
the same four numbers (ADR-0025 §1: "refused with the numbers"); `off` skips the call. The mode ships
`warn`; flipping to `refuse` is the owner's box `.env` edit.

**8.4.3 The order-to-invoice chain.** `request_invoice_for_order` (called inside the READY transaction that
holds the order lock) reads `order.finance_customer_id` and the order's own accept-time settings snapshot
(`ResolvedSettings.as_snapshot()`, which carries `source` and `default_tax_rate_pct` today and gains
`order_invoice_chain_enabled`, `services/operations/app/orders/selectors.py:310-363`); it makes **no network
call**. A pure `domain.decide_invoice_request(...)` returns `request` or a skip with one of three reasons,
tested first to last: `no_finance_customer_mapping` (`finance_customer_id` is null: a walk-in, or an order
whose link was never written, §8.4.2), `tax_rate_unset` (the snapshot's `source` is `default`: the organization
has no settings row, so the rate is the hard-coded 0, S50) and `chain_not_enabled` (a row exists but its
`order_invoice_chain_enabled` is false, or the key is absent from a snapshot taken before this release: fail
closed). A tax invoice is a legal document that only a credit note corrects, and readiness is per
organization while the mode is one box-wide variable, so **the mode alone never decides**. By
`order_invoice_chain_mode`:

- `off`: today's visibly-logged skip, audit row `order.invoice_request_skipped`.
- `log_only` (the default): the same skip, plus one structured line, counts and ids only: for a decision of
  `request`, `orders.invoice_chain.would_request` with order id, finance customer id, currency, expected
  totals, the tax rate and its `source` (`row` for `warehouse` or `organization`, else `default`) and the number
  of lines without an HSN; for a skip, `orders.invoice_chain.would_skip` with the reason. Nothing is emitted.
- `on`: emits `operations.orders.order.invoice_requested` **only when the decision is `request`**; every skip
  writes the audit row `order.invoice_request_skipped` with its reason and a log line (a walk-in with no
  company keeps today's behaviour, and an organization that never opted in keeps it too, whatever the box
  variable says).

The payload no longer sends the constants `supply_type = "b2b"` and `reverse_charge = false` and never
sends a `place_of_supply`; each line carries `hsn_sac` from `InventoryItem.hsn_code`
(`services/operations/app/models.py:536`, empty string becomes absent). Finance's
`evaluate_order_invoice_request` now derives what the event omits, with the rules `create_invoice`
already applies: `supply_type = "b2b"` when the billing profile has a GSTIN, else `"b2c"`;
`place_of_supply` through `_place_of_supply` (the profile's GSTIN state, else its recorded state, else the
seller's: `services/finance/app/services.py:1265-1289`); `due_date = issued_date + payment_terms_days`
(or the issue date when the profile has no terms); `buyer_snapshot` and `crm_company_id` copied from the
profile; line `hsn_sac` copied from the event. It still recomputes the tax and refuses a mismatch
(`finance.orders.invoice.request_rejected`). Because the request's tax comes from
`fulfilment_settings.default_tax_rate_pct`, which has no writer today (S17), **the chain emits for an
organization only after its own administrator has written a settings row through R4.3-S and switched
`order_invoice_chain_enabled` on**, whatever the box variable says; the owner's flip checklist is in §13
and the tenant's setting is not the owner's to make. Nothing
in R4.3 changes the default `invoice_timing = on_accept` handover refusal (S18); R4.3-S lets an organization
choose `on_delivery`.

*An e-invoice test is part of the release:* an order invoice built from a b2b profile with a GSTIN, an HSN on
every line and a state place of supply must be accepted by the IRP stub (`services/finance/app/irp/stub.py`),
and one with no buyer GSTIN must be issued as `b2c`, so it is never auto-filed as a b2b document.

**8.4.4 Online captures (SD-9).** A verified `payment.collections.payment.captured` reaches finance's
consumer (`finance.events`, new binding; `.refunded` also). The handler (a) cross-checks the envelope and
payload organization (a mismatch is marked processed and logged), (b) ignores a `reference_type` it does not
know (marked processed), (c) in one transaction takes `pg_advisory_xact_lock(hashtextextended(
'finance.money:' || reference_id::text, 0))`, inserts the `online_captures` row `ON CONFLICT
(organization_id, charge_id) DO NOTHING`, and calls `apply_capture`. `apply_capture`:

1. finds the invoice: for `operations.order` by `invoices.order_id = reference_id` and
   `document_type = 'invoice'`; for `finance.invoice` (R4.5) by id; none yet → stays `pending`;
2. refuses with `unmatched` and a reason when the invoice is `void` or fully paid
   (`invoice_closed`), its currency differs (`currency_mismatch`), or the customer is gone
   (`customer_missing`);
3. allocates `min(amount, outstanding)` through `record_payment` with `reference = "ONL-" + charge_id`
   (exactly 40 characters, unique per organization: `uq_payments_org_reference`), `method = "online"`
   (added to `domain.PAYMENT_METHODS`, to the `payment.received` schema's closed enum and to the SPA label
   map), `received_date` the UTC date of `captured_at`, `customer_id` the invoice's customer and
   `currency` the capture's; a remainder is `allocated_minor < amount_minor` with state `applied` and the
   banner shows it, never silently dropped;
4. sets `state = 'applied'`, `payment_id`, `applied_at`.

`evaluate_order_invoice_request` takes the **same advisory lock first** (keyed on the order id, before its
duplicate check) and, after the invoice is flushed, applies every `pending` capture of that order in the
same transaction, so a prepaid order captured before its invoice exists, an invoice created before the
capture, and the two arriving together all end in the same state, and a lost interleaving is impossible.
A finance sweep (`reconcile_online_captures`, hourly, in the folded worker) has three jobs, per organization
it discovers on the narrow outbox role (one with an open invoice or a capture that is not `applied`; the
columns are granted in `_SWEEP_READER_COLUMNS`, §8.2). (1) **Catch-up for lost events.** A finance outage or
a code rollback discards `payment.collections.payment.captured` (S46) and a paid order would be left with an
open invoice and no repair path, so with no transaction open the sweep pages payment's capture export (C105,
the last 30 days) and inserts, `ON CONFLICT (organization_id, charge_id) DO NOTHING` and under the same
advisory lock, any `online_captures` row it does not hold, and raises `refunded_total_minor` where the export
shows a larger figure. (2) It applies any `pending` capture older than ten minutes whose invoice now exists.
(3) It marks one older than seven days `unmatched` (`no_invoice`); it never deletes. A refund event updates `refunded_total_minor` and `refund_noted_at`,
writes an audit row, logs `finance.online_capture.refund_noted` (ids and amounts only) and creates **no**
credit note or negative allocation (pending D-28). *Known limit:* an online checkout charges the order's
stored total, which excludes GST (`services/operations/app/orders/services.py:384-393` stores the
subtotal), so with a non-zero tax rate the invoice stays open for the tax portion; this is shown, not
corrected, here.

**8.4.5 Company deletion** is refused while `party_documents` exist (SD-20); `churned` is the supported
retirement.

**8.4.6 The link and merge tool (H2, `tools/link_finance_customers.py`).** Owner-run, in finance's
container (wrapper `remote.sh link-finance-customers`), per organization, one transaction each, stop at the
first failing organization, counts and ids in the console and names only in the plan file.
- `plan --org <uuid>|--all --out FILE` (read-only, the default): for each finance customer with no
  `crm_company_id`, ask C52 in this order and stop at the first hit: (1) the profile's **GSTIN** →
  `matched_gstin`; (2) the profile's **e-mail** (resolves a contact) **and** a case-insensitive equal
  company name → `matched_name_email`; anything with several candidates → `ambiguous`; nothing →
  `unmatched`; two profiles that would take the same `(company, gstin)` → `conflict`. **A name alone never
  matches** (CRM names are case-sensitively unique and duplicates exist:
  `docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md:139-149`). The file lists finance id,
  proposed company id, the basis and both display names for the reviewer, and an `approve: false` flag;
  it is kept off the repository and off shell history.
- `apply --plan FILE` (dry run unless `--apply`, `--confirm-count N`, `--i-took-a-backup`,
  `--i-know-this-is-the-live-database`, the gating of `tools/purge_test_orgs.py:1892-1910`): writes
  `crm_company_id` only on rows the reviewer set `approve: true`, after C50 proves each company; never
  touches an invoice; re-running changes nothing; audit row per organization (`acting_as =
  link-finance-customers-cli`, counts). A company with several GSTIN profiles is normal (B41); duplicates
  among legacy profiles are resolved by the owner archiving one in finance and re-planning, never by a
  merge of invoices.

**8.4.7 Seeds.** `tools/demo/seed_finance.py`, `seed_crm.py` and `seed_operations.py` build one linked
world through the real write paths (party book first, then billing profiles with `crm_company_id`, orders
with contacts, invoices, payments, an enquiry, a project client), so no demo organization shows the
duplication.

### 8.5 Consumers, projection and lifecycle

`customer.events` (folded, `containers.customer.kind = api_with_worker`) gains the bindings
`finance.billing.invoice.issued`, `.paid`, `finance.billing.payment.received`,
`operations.orders.order.placed`, `.accepted`, `.cancelled`, `.handed_over` and
`engage.inbox.conversation.touched` (registry EV43 and EV30: R4.2 emits it, this release is the first to
bind it on customer) and `crm.pipeline.deal.stage_changed` (lifecycle, read only when D-34 asks
for deals; registry EV43). One handler per event type (SD-4), marker
`uuid5(event_id, "party-projection")`, tenant bound from the envelope's `organization_id` (payload
organization cross-checked), claim → record in one transaction (no outbound call) **that takes the
per-company advisory lock of SD-5 first**, so two writers of one company's facts serialize; H4 and the nightly
sweep take the same lock, per company, in their own short transactions. For each event:

1. **Skip** when `crm_company_id` is null (log `party.projection.unlinked`, ids only) and mark processed.
2. **Document upsert** into `party_documents` guarded by `source_occurred_at` (an older event never
   overwrites a newer state; fields an event does not carry, such as a handed-over order's total, keep
   their stored values through `COALESCE`); **payment upsert** into `party_payments` from each allocation of
   `payment.received` (idempotent by primary key; a payment may arrive before its invoice).
3. **Recompute** `party_rollup` for each touched `(company, currency)` from the two tables, with finance's
   own rules: `receivable_minor` is the outstanding amount of invoices whose effective status is `sent` or
   `overdue`, **credit notes excluded** (`services/finance/app/domain.py:2630-2647`); `overdue_minor` the
   outstanding of those past `due_on` against the UTC date; `billed_lifetime_minor` the signed total of
   non-draft, non-void invoices and credit notes; `paid_lifetime_minor` the sum of `party_payments`;
   `open_orders` the orders in `placed` or `accepted`; `as_of` the latest applied `source_occurred_at`.
   A nightly sweep (`PartyRollupSweep`, customer process, `RUN_SWEEPS_IN_API`) recomputes the rows that
   have an open invoice, because `overdue` depends on the date.
4. **Timeline row** (kinds `invoice_issued`, `payment_received`, `order_placed`, `order_delivered`
   from `handed_over`) with a document number or reference as `title`, idempotent on
   `(organization_id, source_event_id)`. **`conversation_touched`** is written from
   `engage.inbox.conversation.touched` only (it has no document, payment or rollup effect): `ref_type =
   'conversation'`, `ref_id` the conversation id, `title` a fixed generic label (`WhatsApp conversation`),
   never a name or a number, `ON CONFLICT DO NOTHING` so that `uq_party_timeline_org_conversation_day` keeps one
   line per conversation and UTC day however many messages the day carries. Without this binding the kind could
   never be written (the registry assigns it to R4.3, EV30 and EV43).
5. **Lifecycle** (below).

**Per currency, never summed.** Every figure is keyed by currency. The 360's existing single-`Money` fields
carry the **primary currency** (finance: the currency with the largest `receivable_minor`, ties by
`billed_lifetime_minor`, then the currency code; orders: the largest 90-day total, else the latest
order's), and each section gains an additive `by_currency: [{currency, ...}]` list holding every currency,
empty for an older client. Nothing is ever added across currencies.

**The 360 sections** are read from the projection by company: `OrdersSection {count_90d, total_90d,
latest}` counts orders that are not cancelled with `occurred_at` in the last 90 days; `FinanceSection
{outstanding, overdue_count, latest_invoice {number, status, due_on}}`. Both are labelled "as of
{as_of}" in the UI and are never presented as finance's live figure.

**Lifecycle (D-34; off by default).** An organization with no `customer_settings` row, or with
`lifecycle_promotion = 'off'`, is never promoted: the handler reads the setting in its own transaction and
returns. The programme names one trigger, "first order → customer" (P§6), so `first_order` promotes on the
first accepted order (`order.accepted`) only; `first_commercial_event` is the owner's explicit extension and
also promotes on a won deal (`crm.pipeline.deal.stage_changed` with `to_outcome = "won"`) and a paid invoice
(`invoice.paid`). The promotion is **one atomic claim**, not a read followed by a write: `UPDATE
customer.companies SET status = 'customer', lifecycle_promoted_at = :occurred_at, updated_by = 'lifecycle'
WHERE organization_id = :org AND id = :company AND status IN ('prospect', 'lead') AND lifecycle_promoted_at
IS NULL AND deleted_at IS NULL RETURNING id`. Only when a row comes back does the handler write the audit row
`company.updated` with `acting_as = "lifecycle"` and `after = {status, trigger, trigger_event_id}` and stage
`crm.book.company.updated` with `changed: ["status"]`, so two events for one company arriving together (an
accepted order and a paid invoice) promote it once; a `customer` or `churned` company is never touched and a
person's later edit is never overwritten. **Only a live event promotes: H4, the nightly sweep and every replay
never run lifecycle**, because replaying history would flip every company with a past paid invoice on the day
an administrator turns the setting on; the setting applies from then on. Contacts keep their own status.

**Backfill.** Events published before the bindings existed are gone. `tools/backfill_party_rollup.py`
(H4, owner-run, dry-run default, gated like H1, in customer's container) pages C63 per organization,
applies the same upserts with `source_occurred_at = updated_at` (so a later live event wins), recomputes
the touched rollups under the per-company lock and reports counts only; **it writes no timeline row and runs
no lifecycle** (an export row has no event id, and history must not promote). It is idempotent and resumable.

**Erase handlers (EV18).** Both bind `crm.book.contact.erased` on a queue that already folds (no budget
line), and each is the only handler of that key on its queue, so the raw `event_id` is its marker.
**Operations' queue is folded but unwatched (S45, registry EV55), and a handler that overwrites personal data
must not sit on an unmonitored queue.** This release therefore gives operations the fold set the registry gives
every other fold (F5, F7 and F8; EV55), together with the erase handler (EV18):
`PROFILE_DLQ_QUEUES["inventory"] = ("operations.events",)` in
`libs/platform-core/src/platform_core/ops_monitor/config.py`, an `operations-worker` rollback container
(`profiles: ['split-workers']`, `command: ['./bin/worker']`, the shape of `customer-worker`;
`services/operations/bin/worker` exists) because `tests/infra/test_ops_monitor_dlq_queue_sync.py` derives its
workers from compose in both directions and a queue cannot be listed without its worker, its
`containers["operations-worker"]` budget entry (priced 0 while `split-workers` is off), and the RUNBOOK
literals, all in one commit. `containers.operations.kind` is **not** repriced: its consumers already run in
the API process by the shared `RUN_CONSUMERS_IN_API` default and are inside its existing resident set
(inference: the PR measures operations with the consumers on and off, and the owner re-prices only if the
delta exceeds 1 MB, with D-13).
*Finance* (`finance.events`): for every `finance.customers` row whose `crm_contact_id` equals the contact
(live or archived, found **before** any change) call `finance.scrub_customer_audit`, then set `contact_name =
''`, `email = ''` and `crm_contact_id = NULL`; the billed party's `name`, GSTIN, city, state and withholding
flags and every invoice, payment and `buyer_snapshot` stay (legal documents, D-27); one counts-only audit row
`customer.contact_erased`. *Operations* (`operations.events`): set `customer_name = ''` and `customer_phone =
''` on every order whose `crm_contact_id` **or `customer_id`** equals the contact (`customer_id` is a CRM
contact id in practice for orders that predate this release, S19), leaving totals, lines, status and payment
facts; one counts-only audit row (the `order.created` audit row holds no customer field,
`services/operations/app/orders/services.py:235-250`, and the erase test scans the rows of every order audit action for the name and phone).
Both are idempotent by value (a second pass finds nothing to change) and by marker, and ship with the
release that creates the link they act on; H14 (`--service finance`, `--service operations`) re-applies
erasures that preceded the consumer.

### 8.6 UI

- **Finance** (`frontend/product-finance`): the nav and page say "Billed parties" (R4.1 copy); each row
  shows the linked CRM account (name, status, owner) with a link on the CRM host, or "Not linked"; the
  form gains a company combobox (`useCompanyLookup`, G13, hidden unless `useEntitlement('crm')` and the
  user holds `crm.book.company.read`), payment terms (days) and credit limit (amount and currency, written
  in minor units, never a float); an "online payments need a match" banner on Payments; the money helpers
  become wrappers over `@opsgrids/ui` `formatMoney(minor, currency)` (G4), so a USD invoice no longer
  renders a rupee sign; names are resolved by `GET /finance/customers?ids=`, so "Unknown billed party"
  stops appearing past 200 customers. Every list has loading, empty, error and forbidden states.
- **Operations** (`frontend/product-operations`): the order form keeps its typed name and phone (a
  warehouse operator holds no CRM permission, and a phone must never reach a URL); the server resolves by
  phone at accept and offers the result as a **suggestion** (§8.4.2): the order detail says "A CRM account may
  match this phone number" beside a **Confirm link** action (C107) and names the company only to a viewer who
  can read it. A contact picker (a plain `GET /contacts?q=` name search) appears only for a user who
  holds `crm.book.contact.read` and the `crm` entitlement. The order detail shows the linked company, the
  finance profile and the invoice status ("Invoice requested", "Invoice issued", "Invoice skipped: no
  billing profile", "Invoice skipped: tax rate not set", "Invoice skipped: automatic invoices are off for
  this organization"); a "Billing defaults" card on the payment-settings screen carries R4.3-S's four fields:
  invoice timing, the tax rate, "Create a CRM record for a new buyer" (shown only with
  `useEntitlement('crm')`, a hide and never the enforcement, which is customer's 403 `crm_not_active`), and
  "Raise tax invoices automatically when an order is ready" (default off, with the sentence "Ready orders
  will create invoices at the rate above; a mistake is corrected only by a credit note"). It is **the
  organization's administrator** who sets them, not the platform owner.
- **CRM**: the 360 and the company page render the `orders`, `finance` sections with the "as of" label,
  `by_currency` tabs when more than one currency exists, and "Not available" when the viewer lacks
  `read_commercial`; the company page's Billing tab lists the company's profiles read live **as the
  viewer** under their finance grant (the live tier of §4.4) beside the projected figures. A "Customer
  status" card in the settings hub (R3.4) edits `customer_settings` (C106): "Promote a prospect to customer
  automatically" with Off, "On the first order" and "On the first order, won deal or paid invoice", and the
  sentence "This changes the Total Customers figure on the dashboard"; read-only for a caller without
  `crm.book.company.write` at ORG.
- **Every changed or new screen** has loading, empty, error and forbidden states at phone width (the
  harness's 375 px, no horizontal scroll at 360 px); no raw colour or size value (`pnpm tokens:baseline`
  without `--allow-increase` writes nothing); a story for every new `@opsgrids/ui` export; a fixture and a
  `MockRoute` in `frontend/opsgrids-visual/fixtures/api/` for every new `GET`; visual baselines updated and
  no new axe violation (`make ui-visual`).

### 8.7 Tests

| Area | Tests that will exist |
|---|---|
| Negative authz | C61, C62, C63 and the operations export: no token 401, wrong caller 403, wrong org claim 403, an organization not entitled to finance or operations refused; `PUT /operations/fulfilment/settings` 403 without the permission, 403 with a custom OWN grant (the ORG floor), and `GET /operations/fulfilment/settings` 403 without `operations.orders.order.read`; `POST /operations/orders/{id}:confirm-crm-link` 403 without `operations.orders.order.write`, 404 for another organization's order, 409 `no_suggestion` when none is stored, and no way to name a company in the request; C105: no token 401, wrong caller 403 (operations, customer), wrong organization claim 403; `GET /finance/online-captures` 403 without `finance.billing.payment.read`; `GET /companies/{id}/360` money sections `null` without `read_commercial` and 404 for another organization's id; `GET /companies/settings` 403 without `crm.book.company.read` and `PUT` 403 without `crm.book.company.write`, 403 with a custom OWN grant (the ORG floor); `DELETE /companies/{id}` 409 with history; finance customer write with a `crm_company_id` from another organization 404 |
| Cross-tenant | a capture, an event or a company id from organization B never creates, updates or links a row of organization A (finance, operations and customer each); `party_*` reads filter on `organization_id`; the C62 position never includes another organization's profiles |
| Idempotency and redelivery | every projected event delivered twice, out of order (payment before invoice, `handed_over` before `placed`) and concurrently → one row per document and per allocation and a rollup equal to a fresh recompute; the capture handler delivered twice and concurrently with the invoice creation → exactly one payment, one allocation, one `online_captures` row (a test holds the advisory lock to force each interleaving); C61 twice and concurrently → one profile. **Concurrency on the rollup:** two **real concurrent connections** each deliver one event of the same company (a payment and an invoice, then two invoices in two currencies) with the per-company lock released in each order, and the rollup always equals a fresh recompute; the nightly sweep and a handler racing on one company do the same; without the lock the test demonstrably fails (a mutation check). **Lifecycle:** see its own row |
| Lifecycle | with no `customer_settings` row, `off`, nothing promotes on any event; `first_order` promotes on `order.accepted` only and ignores a won deal and a paid invoice; `first_commercial_event` promotes on each of the three; two events of one company delivered **concurrently on two real connections** promote it once (one audit row, one `crm.book.company.updated`); a `customer`, `churned` or manually demoted company is never touched; H4 and a replay promote nothing; the setting's `PUT` audits the old and new value |
| Parity | a golden case `libs/contracts/parity/party_receivables_v1.json`: finance's own test builds customers in two currencies, invoices (one partly paid, one overdue, one paid, one void), a credit note and an online payment through the real write paths, records the emitted envelopes and the numbers of `domain.total_outstanding` and `total_overdue`; customer's test replays the envelopes through its handler and asserts `party_rollup` equals them, **per currency**; either side failing breaks its own suite |
| Chain | an order invoice from a profile with a GSTIN, an HSN per line and a state place of supply is accepted by the IRP stub; the same order with no GSTIN is issued `b2c` and never filed as b2b; `due_date` follows `payment_terms_days`; `buyer_snapshot` holds no e-mail, phone or contact name (recursive key scan); a rate mismatch is refused as before; `log_only` emits nothing and logs once (`would_request` or `would_skip` with its reason); **the per-organization guard, both branches:** `on` with an organization that has no settings row (`source = default`) skips with `tax_rate_unset` and emits nothing, `on` with a row whose `order_invoice_chain_enabled` is false (and with a snapshot taken before this release) skips with `chain_not_enabled`, `on` with a row and the flag emits once per order; an unlinked order is skipped with `no_finance_customer_mapping`; **a phone match never links** (the order carries only `crm_suggestion`, no credit check and no invoice request), `:confirm-crm-link` links it and a later READY then requests, and an explicit company at order entry links with basis `explicit` |
| Credit | `warn` accepts and records the four numbers; `refuse` answers 409 with the same four numbers; finance down → `not_checked` and the accept proceeds; no limit → `ok`; two currencies are never added |
| Erase | finance: profiles linked to the contact lose `contact_name`, `email` and the link, their `customer.updated` audit rows hold neither value afterwards (a recursive scan; as the app role a raw `UPDATE finance.audit_logs` fails while `finance.scrub_customer_audit` succeeds and touches no other entity or organization), and invoices, payments and snapshots are byte-identical; operations: orders matching by `crm_contact_id` and by legacy `customer_id` lose name and phone and nothing else; a second delivery changes zero rows; an event of organization B changes nothing of A |
| Migration | round-trip for finance, operations and customer; **the payments backfill** sets `currency` only for a payment whose allocated invoices share one currency, leaves a mixed-currency payment and one with no allocation `NULL` and reports their count, runs in batches of 1000 each its own transaction after the DDL has committed (a 2500-row test with a second connection that is never blocked for longer than one batch), is idempotent, touches no column but `currency` (a row-hash comparison of every other column before and after), and a payment inserted by the previous image after a rollback has `currency NULL`, not `INR`; `uq_customers_org_company_gstin` allows two profiles with different GSTINs for one company, refuses a second profile with the same GSTIN and a second profile with none; the timeline trigger refuses `UPDATE` and `DELETE`; downgrade refusal with data; single head each |
| Contract | EV33, EV34, EV38 and the finance customer events validated through the real write paths (create invoice, record payment, place, accept, cancel, hand over, a captured payment); `invoice_requested` with and without `hsn_sac`; `finance.orders.invoice.issued` is a `SUBSCRIBABLE_EVENTS` key with a committed schema (`libs/contracts/tests`); `make openapi-check` for the new public fields, C106 and C107; `export_permissions.py --check` (E23 and the operations code) |
| Sweep discovery | `tests/infra/test_sweep_discovery_grants.py`: every sweep that discovers organizations on the outbox role (finance's `reconcile_online_captures` and filing sweeps, operations', customer's `PartyRollupSweep` and orphan sweep) declares its discovery columns as a module constant its own SQL is built from, and the service's `migrations/env.py` grant dict equals the union of those constants (finance's `_SWEEP_READER_COLUMNS` gains `online_captures`; customer gains `SWEEP_DISCOVERY_TABLES`), so a new discovery query without its grant fails in CI and not silently on the box (S47); a sweep test per service runs the discovery **as the narrow role** (the `opsgrids_outbox` session the service tests already provision) and finds the seeded organizations; `grant_sweep_discovery` is guarded on the role and revokes before it grants, on upgrade and downgrade |
| Rollback and catch-up | finance down and back: capture events published while finance's consumer is not bound are absent, and one run of `reconcile_online_captures` over a payment fixture (C105) inserts the missing `online_captures` rows, applies them, and a second run changes nothing; H4 after the customer projection missed events rebuilds `party_documents`, `party_payments` and `party_rollup` to the golden numbers, writes no timeline row and promotes no company; the operations erase handler's queue is in `PROFILE_DLQ_QUEUES` |
| Infra | `tests/infra/test_party_link_env_wiring.py` (§8.8) loads W3, W4, W15, W16, W27, W37 and fails when a caller's `Settings` or compose block lacks the line, or a service gains an `*_internal_url` no W row names; `test_ops_monitor_dlq_queue_sync.py` and `test_ops_monitor_profile_containers.py` for operations (`operations-worker`, `operations.events`); `test_io_outside_transaction.py` stays green for accept, create, confirm and C61; `test_planned_headroom.py` and `test_compose_memory_budget.py` for the `operations-worker` entry; `tests/infra/test_sweeps_in_api.py` (customer joins `SWEEPING_SERVICES` here, because `PartyRollupSweep` is the first customer sweep) and `test_sweeps_run_somewhere.py`; `tests/infra/test_internal_client_paths.py` (SP1) registers every internal client call this release adds, the pairs of W3, W4, W15, W16, W27 and W37, each in `CALLS` with a driver that returns the token it sent (a client in a new file fails the guard until it is registered); `docs/crm.md` is edited for the permission row of E23 in the commit that adds the code, which `test_crm_doc_matches_contracts.py` enforces |
| Frontend | finance form, list and banner states; the order lookup, the phone-match suggestion and its Confirm action, and invoice status; the Billing defaults card (four fields, the two hidden states); the Customer status card; the 360 and company page sections including `by_currency`, "as of" and "Not available"; `formatMoney` for INR, USD and a zero-decimal currency; `pnpm typecheck && lint && test && build` for finance, operations, CRM, dashboard; visual and a11y suites |

### 8.8 Rollout

**Compose and settings (one commit with `tests/infra/test_party_link_env_wiring.py`):** operations block
`CUSTOMER_INTERNAL_URL: http://customer:8000`, `FINANCE_INTERNAL_URL: http://finance:8000` (W4, W27);
finance block `CUSTOMER_INTERNAL_URL` (W3: the setting exists, the line does not,
`infrastructure/edge/compose.prod.yml:1567-1577`); customer block `FINANCE_INTERNAL_URL`,
`OPERATIONS_INTERNAL_URL` (W15, W16, beside the existing `NOTIFICATION_INTERNAL_URL` and
`DEAL_INTERNAL_URL` at `:1436-1437`); finance block `PAYMENT_SERVICE_URL: http://payment:8000` (W37, for C105; the line operations' block already carries);
`ORDER_INVOICE_CHAIN_MODE: ${ORDER_INVOICE_CHAIN_MODE:-log_only}` and
`CREDIT_CHECK_MODE: ${CREDIT_CHECK_MODE:-warn}` in operations' block; the keys documented in
`infrastructure/edge/.env.example` (commented), `docs/deploy/ENV_SOURCE_MATRIX.md` and the RUNBOOK. The
profile caveat: `customer` runs only under `crm`, so a box without it answers "unavailable", never wrong.
Customer's `product_service_url` is the shared `PRODUCT_SERVICE_URL`, so it needs no line. One more commit,
with the infra tests of §8.7: the `operations-worker` rollback container, its `containers` entry and
`PROFILE_DLQ_QUEUES["inventory"]` (§8.5).

**Deploy order (owner-run, one named ref per deploy, consumers first):** (1) **customer**: migration (the
settings table, the timeline index), the sweep-discovery grant in `env.py`, the projection handlers (bound
before any producer emits company ids, `conversation_touched` included), `run_sweeps_in_api`, 360 and company
360, C106 with the lifecycle setting at its default `off`; (2) **payment**: C105; (3) **finance**: migration
(the nullable `payments.currency` and its batched backfill, `online_captures` and its sweep grant), C61 to
C63, the capture consumer and `reconcile_online_captures`, derivations, the enriched events; (4)
**notification**: the one `finance.orders.invoice.issued` subscribable key, before the chain can emit it; (5)
**operations**: migration, wiring, the R4.3-S routes and C107, the `operations-worker` container and DLQ entry,
modes at their defaults and `order_invoice_chain_enabled` false everywhere; (6) **permissions**: `make
edge-reconcile PRODUCTS=crm` then `PRODUCTS=operations`, each dry run first, then `APPLY=1`, off-peak,
paced (E23 and the settings code reach admin templates only); (7) **data steps (owner-run)**: H4 for every
organization with finance or operations data (dry run reviewed), then H2 `plan`, the owner's review of the
plan file, `apply`, then H14 with `--service finance` and `--service operations` to re-apply erasures that
preceded the two erase handlers; (8) **flip** per §13 only after the checklist; (9) publish the SPAs last
(finance, operations, crm, dashboard).

*Memory.* No new container that is running. Customer's handlers join its folded consumer: the budget kinds are
unchanged (`api_with_worker` 99 MB, 157 MB below its 256 MB limit); the measured resident delta (expected
about +1 MB, P§4.3, an inference until measured) is recorded in the PR. The `operations-worker` container sits
behind `split-workers`, priced 0 while that profile is off, and operations' kind is not repriced (§8.5);
`python3 infrastructure/edge/lib/memory_budget_totals.py` stays at 3747 MB (ten profiles) and 3944 MB (with
`admin`) plus the folds of releases before this one.

### 8.9 Rollback

The first levers are **configuration**, and each loses nothing: an organization's administrator switches
`order_invoice_chain_enabled` off (one `PUT`), or the owner sets `ORDER_INVOICE_CHAIN_MODE=log_only` (or `off`)
and `CREDIT_CHECK_MODE=warn` in the box `.env`, which stops new invoice requests and refusals at once; an
administrator sets `lifecycle_promotion` back to `off`. Code rolls back to the previous images; the schema
stays (additive). Not reversible: invoices the chain issued (frozen; a correction is a credit note),
payments and allocations recorded, e-invoice filings, lifecycle promotions (the audit row names the trigger;
a person edits the status, and `lifecycle_promoted_at` stays so automation never repeats it), a
`crm_company_id` written by H2 (the tool's audit counts let the owner nullify exactly those rows).

**What a code rollback loses (§4.7).**

| Consumer (queue) | Events a rollback discards | Catch-up |
|---|---|---|
| customer projection handlers for invoice, payment and order events (`customer.events`) | the document, payment and rollup facts of events published while the old image runs | **catch-up:** H4 rebuilds `party_documents`, `party_payments` and `party_rollup` from the finance and operations exports (C63) |
| customer timeline lines (invoice, payment, order and `conversation_touched`) | the timeline line of each lost event | **forward-only:** the timeline is "recent activity", H4 writes no timeline row (an export row has no event id), and the facts themselves are repaired by H4 |
| lifecycle promotion (`customer.events`) | a promotion that a lost event would have made | **forward-only, self-healing:** the claim is idempotent and the next qualifying event promotes; H4 never promotes (§8.5) |
| finance capture handler (`finance.events`: `payment.captured`, `.refunded`) | the capture of a paid order, which would leave its invoice open with no repair | **catch-up:** `reconcile_online_captures` pages C105 (the last 30 days) and applies; a finance rollback or outage is repaired within an hour of the image's return |
| finance and operations erase handlers (`finance.events`, `operations.events`) | erasures while the old image runs | **catch-up:** H14 `--service finance` and `--service operations` |

**Do not roll back finance, customer or operations while its queue carries live traffic** unless the catch-up
named in its row is ready to run when it returns, and never roll finance back during live online captures
unless payment already serves C105. *Stop conditions:* a parity test fails after H4; a
paid order has no payment row after the sweep ran; a second payment appears for one `charge_id`; an
order invoice appears with a NULL place of supply, for an organization that did not opt in, or with
`tax_rate_pct` the organization did not choose;
`finance.orders.invoice.request_rejected` events rise after the flip; `customer.events.dlq`,
`finance.events.dlq` or **`operations.events.dlq`** (now watched, §8.5, EV55) depth rises.

### 8.10 Acceptance criteria

- [ ] The three migrations exist as specified, `make migration-roundtrip` passes, downgrades refuse with data, one head per service; the `payments.currency` backfill ran in batches after the DDL, left mixed-currency payments `NULL` and counted them, and touched no other column.
- [ ] A company can have two finance billing profiles with different GSTINs and not two with the same GSTIN; C62 returns one entry per currency across them, never summed.
- [ ] An accepted order with a `crm_company_id` has `finance_customer_id` set from C61 and `settings_snapshot.credit_check` in `{ok, warn, not_checked, not_applicable}`; an unreachable finance or CRM never blocks the accept; the accept makes no call inside a transaction (`test_io_outside_transaction.py` green); a phone match leaves the order unlinked with only a `crm_suggestion`, and only an explicit choice, `:confirm-crm-link` or the opted-in create writes a link.
- [ ] In `log_only` no `invoice_requested` is emitted and one `orders.invoice_chain.would_request` or `would_skip` line is logged per ready order; in `on` exactly one is emitted per order with a finance customer **in an organization that has a settings row and `order_invoice_chain_enabled`**, and an organization without one is skipped with `tax_rate_unset` or `chain_not_enabled` whatever the box variable says.
- [ ] An order invoice has `due_date` from the profile's terms, `place_of_supply` and `supply_type` derived by finance, `hsn_sac` on each line, `buyer_snapshot` with business identity only, and passes the IRP stub test.
- [ ] A captured online payment for a prepaid order creates exactly one finance payment and allocation whichever of the capture and the invoice arrives first; a redelivery and a concurrent invoice creation change nothing; a refund is recorded and creates no credit note.
- [ ] After H4, `party_rollup` equals finance's own receivables and overdue per currency on the golden case, H4 wrote no timeline row and promoted no company, and the golden case's replayed live events leave `party_timeline` with one row per source event and no personal data; `conversation_touched` is written from `engage.inbox.conversation.touched` with one line per conversation and UTC day.
- [ ] A finance outage or rollback during an online payment loses nothing permanently: one run of `reconcile_online_captures` over C105 inserts the missing capture and applies it exactly once.
- [ ] Two real concurrent connections writing one company's invoice and payment facts always leave `party_rollup` equal to a fresh recompute (the per-company lock; the test fails without it).
- [ ] The customer sweeps and `reconcile_online_captures` discover organizations as the narrow outbox role: finance's `_SWEEP_READER_COLUMNS` holds `online_captures`, customer's `migrations/env.py` holds `SWEEP_DISCOVERY_TABLES`, no revision carries a bare `GRANT`, and `test_sweep_discovery_grants.py` is green.
- [ ] `operations.events` is in `PROFILE_DLQ_QUEUES`, an `operations-worker` rollback container exists behind `split-workers`, and `test_ops_monitor_dlq_queue_sync.py` passes in both directions (registry EV55: the fold set of F5, F7 and F8, with `containers.operations.kind` not repriced).
- [ ] After `crm.book.contact.erased`, no finance profile of the contact holds its name, e-mail or link and no `finance.audit_logs` row carries them; no order matched by `crm_contact_id` or legacy `customer_id` holds its name or phone; invoices, payments and snapshots are unchanged.
- [ ] The 360 and company 360 show `orders` and `finance` only with `crm.book.company.read_commercial` and company scope, with "as of" and `by_currency`; otherwise `null` and named in `unavailable`.
- [ ] With no `customer_settings` row or `lifecycle_promotion = off` nothing is promoted; with `first_order` a first accepted order promotes a `prospect` once, and with `first_commercial_event` so do a first won deal and a first paid invoice; two events for one company arriving together promote it once; a `churned` company and a manually demoted one are never touched; the dashboard's customer count changes only through audited `acting_as = lifecycle` rows, and only in an organization whose administrator turned the setting on.
- [ ] `H2 plan` never proposes a match on a name alone; `apply` changes only approved rows and no invoice.
- [ ] `GET /finance/customers?ids=` resolves names beyond 200 customers; finance renders a USD invoice with its own currency.
- [ ] Every changed screen has loading, empty, error and forbidden states at phone width, no new token-baseline count, a story per new `@opsgrids/ui` export, a fixture and `MockRoute` per new `GET`, and no new axe violation; the Billing defaults card and the Customer status card are written by the organization's administrator and hide what the entitlement hides.
- [ ] `tests/infra/test_party_link_env_wiring.py` passes; every internal client call this release adds (W3, W4, W15, W16, W27, W37) is registered in `tests/infra/test_internal_client_paths.py` with a driver that returns the token it sent; `docs/crm.md` carries the E23 permission row; no new running container; `make check` and `make gate` pass locally.

## 9. R4.4 Service and work loop

### 9.1 Purpose and needs

Support tickets and projects stop being islands. A ticket knows its CRM contact and company; the 360
and the company page show the support and delivery sections; the customer is told about their ticket
through notification; a rep or agent can open a ticket from a WhatsApp conversation; a project knows
its CRM company and, for a won deal in an organization that opted in, starts as a **draft**; an Engage
automation's `create_task` step becomes a CRM follow-up.

**Needs:** R4.1 (C50, C52 and the identity read C89 of §6.3, enriched events); R4.3 (the
projection tables and 360 framework that the `ticket_*` and `project_started` timeline kinds extend);
R2.4 (deal's folded consumer) and R3.6 (`deal.follow_ups`, for the automation task); **R3.2** (the `ids`
filter and the `/api/v1/companies` vocabulary behind `useCompanyLookup`, G13: registry C21, C35) and **R3.3**
(`Customer360View`, G5, which renders the support and delivery sections). R4.2 is
recommended, not required. Open decisions: D-17 (the kickoff target; the default is PulseBoard) and D-13
(support's fold, +1 MB). The R3.2 and R3.3 prerequisites are recorded in deviation 2 of §17.

### 9.2 Data model

**support** (one revision on `d4e5f6a7b8c9`; `lock_timeout 5s`; the table is already `ENABLE` + `FORCE`
with `tenant_isolation`):

```sql
ALTER TABLE support.tickets ADD COLUMN crm_contact_id UUID NULL;      -- B49
ALTER TABLE support.tickets ADD COLUMN crm_company_id UUID NULL;
ALTER TABLE support.tickets ADD COLUMN conversation_id UUID NULL;     -- an Engage conversation, plain UUID
CREATE INDEX ix_tickets_org_crm_contact ON support.tickets (organization_id, crm_contact_id)
    WHERE crm_contact_id IS NOT NULL;
CREATE INDEX ix_tickets_org_crm_company ON support.tickets (organization_id, crm_company_id)
    WHERE crm_company_id IS NOT NULL;
CREATE INDEX ix_tickets_org_conversation ON support.tickets (organization_id, conversation_id)
    WHERE conversation_id IS NOT NULL;
```

`channel` is `String(20)` with no CHECK, so `whatsapp` needs no DDL: the `Channel` literal in
`services/support/app/schemas.py:17` gains it. The requester snapshot columns are untouched and remain
the fallback (SD-3).

**project and projects** (one revision each, on `c92e07b13fd5` and `c7b52e0a9f43`; B50, one shared
contract so the second product adopts it without redesign):

```sql
ALTER TABLE project.projects ADD COLUMN crm_company_id UUID NULL;     -- and the same in schema projects
ALTER TABLE project.projects ADD COLUMN crm_deal_id UUID NULL;
CREATE INDEX ix_projects_org_crm_company ON project.projects (organization_id, crm_company_id)
    WHERE crm_company_id IS NOT NULL;
CREATE UNIQUE INDEX uq_projects_org_crm_deal ON project.projects (organization_id, crm_deal_id)
    WHERE crm_deal_id IS NOT NULL AND deleted_at IS NULL;             -- one project per deal: the kickoff's idempotency
```

`client` stays as the display snapshot. In `project` only, `ProjectStatus` gains `draft`
(`services/project/app/schemas.py:23`; the column has no CHECK, so no DDL): a draft is excluded from the
at-risk, blocked and overdue attention lists and from the active counts until a person moves it to
`on_track`. `projects` needs no new status (its phase `discovery` plays the role when D-17 moves the
kickoff there).

**deal** (one revision): `ALTER TABLE deal.pipeline_settings ADD COLUMN create_project_on_won BOOLEAN NOT
NULL DEFAULT false` (B27, R4.4's column, the organization opt-in). `ActivityType` gains `ticket`
(system-only, as `message` in R4.2). No other DDL: the follow-up table is R3.6's (B25).

Customer, notification: no DDL (the `delivery` and richer `support` sections are code and wire shape).

### 9.3 Backend contract

**customer, internal.** Every link write is proved first (SD-2): a company id through C50, a contact id
through C89 (`GET .../contacts/{contact_id}/identity`, shipped in R4.1, §6.3), each in a claim/call/record
step before the write transaction.

**support, public** (`/api/v1/support`, entitlement `support`; both ticket permissions floor at ORG)

| Route | Permission | Change |
|---|---|---|
| `GET /support/tickets` | `support.desk.ticket.read` (ORG) | filters `crm_contact_id`, `crm_company_id`, `conversation_id`; `TicketOut` gains the three ids |
| `GET /support/tickets/summary` (**SP4 addition**, C90) | `support.desk.ticket.read` (ORG) | query `crm_contact_id` or `crm_company_id` (exactly one) → `{open_tickets, total_tickets, last_ticket_at, breaching_sla}`; counts only, **no thread or message crosses it** (the list route returns every message body: `services/support/app/api/routes.py:172-188`), so the 360 never carries ticket text. Declared before `/support/tickets/{ticket_id}` |
| `PATCH /support/tickets/{ticket_id}` | `support.desk.ticket.write` (ORG) | `TicketUpdateIn` (`services/support/app/schemas.py:187-201`, today `priority`, `subject`, `tags`) also accepts `crm_contact_id` and `crm_company_id`: each is re-proved before the write (C89, C50: 404 `crm_contact_not_found`, `crm_company_not_found`; 503 `crm_unavailable`), a contact id implies its company, an audit row `ticket.relinked` holds the old and new ids (ids only) and `.updated` is republished with `changed` naming them; an explicit `null` is 422 (a link is cleared only by the erase handler); the requester snapshot is never touched |
| `POST /support/tickets` | `support.desk.ticket.write` (ORG) | body gains `crm_contact_id?`, `crm_company_id?`, `conversation_id?`; `channel` may be `whatsapp`; `requester.email` is optional **only** for `channel = whatsapp` with a `crm_contact_id` (stored as the empty string, never a placeholder address). A given contact id is proved through C89 in a claim/call/record step (404 `crm_contact_not_found`, 503 `crm_unavailable`) and implies its company. With no contact id and a requester e-mail, support asks C52; one unambiguous answer fills both ids, anything else leaves them null; the ticket is created either way |

**support, internal.** C79 (`GET .../tickets/{ticket_id}/messages/{message_ref}`, caller notification) as
the registry defines it: `{message_ref, author_type, body}`, 404 for an internal note and for any
author that is not an agent, three negative tests plus one proving an internal note is refused.

**project and projects, public and internal.** `GET /projects?crm_company_id=` on PulseBoard (service
`project`, `services/project/app/api/routes.py:245`) and `GET /projects-workspace/projects?crm_company_id=`
on Projects (`services/projects/app/api/routes.py:94`); create and patch bodies accept `crm_company_id`
(proved through C50 before the write) and `crm_deal_id` (C73). Internal, caller `deal` only:
`POST .../projects:create-from-deal` on `project` (C64), body `{crm_deal_id, crm_company_id,
deal_label, value_minor, currency, close_date?}` → 200 `{project_id, code, created}`. The callee checks
the caller, the org claim, that the organization is entitled to `pulseboard` (product service, W36),
asks C50 for the company's name **before** its write transaction, and inserts a `draft` project: `name =
"{company name} · {deal_label}"`, `client` the company name, `budget_minor = value_minor`, `currency` the
deal's, `billable = true`, no owner (a person assigns one), idempotent on `uq_projects_org_crm_deal`.
`projects:create-from-deal` on `projects` is specified with the same body and built after D-17 moves the
kickoff (pending D-17). The deal's own name is **never** sent: it can embed a person's name (S13).

**deal, public.** `POST /deals/{deal_id}:create-project` (**SP4 addition**, C92): `crm.pipeline.deal.write` +
`assert_can_touch`, with `minimum=Scope.ORG` unless `pipeline_settings.create_project_on_won` is true (the
rule of C20 for `target = project`); body `{idempotency_key}`; claim → C64 call outside any transaction →
record a system activity (B31: `activity_type = 'note'`, `origin = 'system'`, `ref_type = 'project'`,
`ref_id` the project id, subject "Project created", no body) and an audit row; 409 `deal_not_won` unless the
deal's stage outcome is `won`; 404 for a foreign or out-of-scope deal; 503 `project_unavailable`. A caller
below the floor, an organization not entitled to PulseBoard and a callee that refuses each have a named
negative test.

**360 and company page.** `SectionName` gains `delivery`; `SupportSection` gains `last_ticket_at` and
`breaching_sla` (additive); `DeliverySection {pulseboard: {total, items: [{code, name, status,
progress_pct}]} \| null, projects: {total, items: [...]} \| null}`; `support` leaves `MISSING_UPSTREAMS`.
Customer reads each leg **as the viewer** (forwarded identity headers on the real `/api/v1` mount, the
shape of the deal leg after R1.2a's path fix), with the 2-second budget and per-section isolation of
`services/customer/app/c1_360.py:209-228`: support through `GET /api/v1/support/tickets/summary`, delivery
through the two list routes with `size=5`. A viewer without `support.desk.ticket.read` or without a
project grant gets that section `null` and named in `unavailable` (§4.4). New customer settings:
`support_internal_url`, `project_internal_url`, `projects_internal_url` (W10 to W12).

**Events.** EV35 for support (committed in this release with a producer-side test each): `.created`
`{organization_id, ticket_id, number, priority, status, channel, requester_kind, assignee_ref,
crm_contact_id, crm_company_id}`; `.status_changed` `{..., from, to, solved_at, sla_breached,
crm_contact_id, crm_company_id}` where
`solved_at` is the time of the transition when `to = solved` and `null` otherwise (support has no
`solved_at` column: `services/support/app/models.py:87-135`); `.updated` `{..., requester_kind, assignee_ref,
changed[]}`; `.assigned` `{..., requester_kind, assignee_ref}`; `.replied` `{..., message_ref, author_type,
internal, requester_kind, assignee_ref}`: **`.updated`, `.assigned` and `.replied` carry no CRM id**, because
rules have no conditions and `recipient_contact_path` is a free path into any payload (S53), so an id there
would let a tenant rule message the customer about an agent's internal note, an assignment or a tag edit
(§9.4);
new `.agent_reply_sent` `{organization_id, ticket_id, number, message_ref, crm_contact_id,
crm_company_id}`, published **only** for an agent's non-internal reply and never carrying the text. EV36
for both project products (`crm_company_id`, `crm_deal_id`, `client`). EV31: the automation event gains an
optional `subject` object `{crm_contact_id, crm_company_id, deal_id, lead_id, owner_membership_id}`, each
nullable, copied only as ids from the **trigger** event's payload (the run's context), so no read is
needed to find an owner (**SP4 adds `owner_membership_id`**, registry EV31). The keys `support.desk.ticket.status_changed`,
`.updated`, `.agent_reply_sent` join `SUBSCRIBABLE_EVENTS` with their schemas (EV42).

### 9.4 Domain rules

**Linking.** A ticket's links are written at creation and may be corrected afterwards by the audited,
re-proved `PATCH` of §9.3 (a ticket is a mutable working record, unlike an invoice or an accepted order, whose
links are never rewritten: §4.1); H11 sets them once on tickets that predate B49. The snapshot is stored always. Ticket-from-conversation is the public create route called by
the Engage inbox under the caller's own `support.desk.ticket.write` grant: the SPA sends the
conversation's `crm_contact_id`, name and, when the contact has one, e-mail; the inbox action is hidden
without that grant or without the `support` entitlement and the server refuses either way. There is no
internal `tickets:from-conversation` route (C65).

**Customer-facing notifications.** A tenant (or an Engage recipe) authors rules on the three support keys
that carry a contact id, `.created`, `.status_changed` and `.agent_reply_sent`, with `recipient_contact_path =
crm_contact_id`; the address comes from C45 (e-mail or WhatsApp) at send
time, the purpose is `transactional` (category `support`) and `consent.decide` applies as for any send. A rule
on `.replied`, `.assigned` or `.updated` has **no contact id to address**: those payloads do not carry one
(§9.3, S53), which is deliberate and pinned by a test, because `.replied` fires for a customer's own message
and for an agent's internal note and `.updated` for a priority or tag edit, and rules have no condition that
could tell them apart from a reply meant for the customer.
For `.agent_reply_sent` the fold reads the reply text through C79 **before** it opens its transaction and
injects it into the render context only; the text is never written to the outbox or the event, and the
rendered message is stored in `notification.messages.body` as every rendered message is (§6.5, kept).
Dedupe key `support:{ticket_id}:{message_ref}`: a redelivery sends one mail.

**Won-deal kickoff.** Deal consumes its **own** `crm.pipeline.deal.stage_changed` (queue `deal.events`):
when `to_outcome = "won"`, `pipeline_settings.create_project_on_won` is true and the deal has a
`crm_company_id`, it runs claim → C64 on `project` (outside any transaction) → record the system activity of
C92. Default off; the opt-in is an ORG-floor `PUT /deals/settings`. The project is a `draft` with no owner;
only future wins are affected (no backfill). A callee outage leaves the event to redeliver once and then
dead-letter (`deal.events.dlq`); C92 is the manual recovery.

**Automation task.** Deal consumes `notification.automation.task_requested`: it creates one `deal.follow_ups`
row through R3.6's `create_follow_up` (so the reminder is armed the same way), `origin = 'automation'`,
`origin_ref = "{run_id}:{node_id}"` (the partial unique index makes a redelivery a no-op), `title` from the
event, `due_at = now + follow_up_default_hours`, `owner_id` the subject's `owner_membership_id`, targets from
the subject ids. With no owner or no target the handler logs `automation_task.unsupported_subject` (ids
only) and marks the event processed.

**Timeline kinds.** `ticket_opened` (from `.created`), `ticket_solved` (from `.status_changed` with `to =
solved`), `project_started` (from both products' `.created`) join `party_timeline` through the R4.3
handlers: one handler per event, company id from the payload, title the ticket number or project code.

**Erase** (support, EV48): the first support handler, on `crm.book.contact.erased`: for tickets with
`crm_contact_id` = the contact, `requester_name` → "Erased contact", `requester_email` → `''`, and
`author_name` of customer-authored messages → "Erased contact"; bodies and subjects are retained (D-27);
audit row with counts. It is idempotent by value and by marker. Tickets created before this release have
strings only and cannot be reached, so the owner-run `tools/link_support_tickets.py` (H11) links them by an
unambiguous e-mail through C52 first. H11 writes links, so it is gated like every owner-run tool that writes
production rows: `plan` is the read-only default, printing counts per organization (matched, ambiguous,
unmatched; never an address); `apply` needs `--apply`, `--confirm-count N`, `--i-took-a-backup` and
`--i-know-this-is-the-live-database` (`tools/purge_test_orgs.py:1892-1910`), runs in support's container as
one transaction per organization on the app role with the tenant bound, stops at the first failing
organization, links only a ticket whose requester e-mail resolves **unambiguously** through C52 (an ambiguous
answer is counted, never guessed), never touches the requester snapshot, and is idempotent. Its tests mirror
SP3's `test_apply_needs_all_three_gates` (`plan` writes nothing, `apply` refuses each missing gate, a second
`apply` changes zero rows, stdout holds no `@`).

### 9.5 Consumers and idempotency

| Queue | Binds | Handler and marker | Notes |
|---|---|---|---|
| `support.events` (support, **new fold F15**) | `crm.book.contact.erased` | `anonymise_requester`, raw `event_id` | `run_consumers_in_api`, lifespan hooks, `containers.support.kind = api_with_worker`, a `support-worker` rollback container (profile `split-workers`, `./bin/worker`, which exists), `PROFILE_DLQ_QUEUES["support"] = ("support.events",)` (a new profile key, `libs/platform-core/src/platform_core/ops_monitor/config.py:62-109`), RUNBOOK literals. +1 MB |
| `customer.events` (customer, folded) | `support.desk.ticket.created`, `.status_changed`, `projects.portfolio.project.created`, `pulseboard.engagements.project.created` | the R4.3 projection handler (SD-4), marker `uuid5(event_id, "party-projection")` | `crm_company_id` null → skipped and logged |
| `deal.events` (deal, folded) | `crm.pipeline.deal.stage_changed` (its own), `notification.automation.task_requested` | `kickoff_project`, `automation_follow_up`, raw `event_id` | claim → call → record; unique indexes behind both |
| `notification.events` (notification) | `support.desk.ticket.status_changed`, `.updated`, `.agent_reply_sent` | the rule `fold` (new subscribable keys) | the C79 read happens before the transaction |

### 9.6 UI

- **Support** (`frontend/product-support`): the new-ticket dialog gains a CRM contact picker (a plain
  `GET /contacts?q=` name search, shown only with the `crm` entitlement and `crm.book.contact.read`; the
  e-mail resolution by C52 on the server is the default) and shows the linked contact and company with
  links on the CRM host; a WhatsApp ticket may have no e-mail; the queue filters by company.
- **Engage** (`frontend/product-engage`): a "Create ticket" action in the inbox thread, visible with the
  `support` entitlement and `support.desk.ticket.write`; the Customer 360 panel renders the support and
  delivery sections.
- **CRM**: the 360 and company page get a Support section and a Delivery card ("Not available" when
  denied); the deal page gets a "Create project" action (C92) for a won deal, with the organization opt-in
  explained beside it in the settings hub (R3.4) as "Create a draft project when a deal is won".
- **PulseBoard and Projects**: the project form gets a company combobox (`useCompanyLookup`, G13), shown
  only with `crm` and `crm.book.company.read`; otherwise `client` stays free text; a project created from a
  deal shows "From deal" and the `draft` chip.
- **Every changed or new screen** has loading, empty, error and forbidden states at phone width (the
  harness's 375 px, no horizontal scroll at 360 px); no raw colour or size value (`pnpm tokens:baseline`
  without `--allow-increase` writes nothing); a story for every new `@opsgrids/ui` export and a fixture and
  `MockRoute` in `frontend/opsgrids-visual/fixtures/api/` for every new `GET`; refreshed baselines and no new
  axe violation (`make ui-visual`).

### 9.7 Tests

| Area | Tests that will exist |
|---|---|
| Negative authz | `GET /support/tickets/summary` and the new filters: no `support.desk.ticket.read` 403, not entitled 402, another organization's company id returns zero; `POST /support/tickets` and `PATCH /support/tickets/{id}` with a `crm_contact_id` of another organization 404, a patch without `support.desk.ticket.write` 403, an explicit `null` link 422; C79: wrong caller 403, token without `org` 403, an internal note and a customer-authored message both 404; C89 and C64: the three negatives each, plus an unentitled organization for C64; C92: a rep at OWN without the opt-in 403 (the ORG floor), with the opt-in 200 for an own deal and 404 for another rep's, `deal_not_won` 409. **PulseBoard and Projects:** `GET /projects?crm_company_id=` and `GET /projects-workspace/projects?crm_company_id=` 403 without the product's project read code, another organization's company id returns zero rows and never an error that confirms it exists; project create and patch with a `crm_company_id` or `crm_deal_id` of another organization 404 (`crm_company_not_found`), without the project write code 403, an explicit `null` link 422, and an unentitled organization 402 |
| Customer-facing notifications | `services/notification/tests/test_ticket_rule_recipients.py`: a rule authored on `support.desk.ticket.replied`, `.assigned` or `.updated` with `recipient_contact_path = crm_contact_id` resolves **no** contact (the event holds none: `notification.rule.no_contact_in_event`) and sends nothing, an internal-note `.replied` included; a rule on `.agent_reply_sent` sends once per reply and never for an internal note; a recursive key scan finds no `crm_*` key in the committed schemas of `.replied`, `.assigned` and `.updated` |
| Cross-tenant | a ticket, project or event of organization B never links to organization A's company; the summary and the list filters filter on `organization_id`; the kickoff for a deal of B is a no-op |
| Idempotency and redelivery | the kickoff for one deal twice and concurrently → one project (`uq_projects_org_crm_deal`) and one system activity; an automation task twice → one follow-up; `.agent_reply_sent` twice → one message; the erase handler twice and out of order with a later edit → the same final state |
| Isolation | the 360 with support returning 403, 500 and a 3-second timeout → `support` `null` and named in `unavailable`, every other section intact and the response 200; a partner section never changes the CRM scope decision |
| Contract | the six support schemas and `agent_reply_sent`, both projects' created and updated events, the extended `task_requested` and `.erased` validated through the real write paths; a recursive key scan finds no `requester_*`, `email` or `body` key in any support event; `make openapi-check` (new filters, `summary`, `TicketOut`, `ProjectOut`, the `whatsapp` channel); `libs/contracts` subscribable-key test |
| Migration | round-trip for support, project, projects, deal; `uq_projects_org_crm_deal` refuses a second live project for one deal and allows one after the first is soft-deleted; a `draft` project is excluded from the attention lists; downgrade refusals with link data |
| Tools (H11, H14) | `tests/infra/test_link_support_tickets_gates.py` and `test_reapply_contact_erasures_gates.py`: `plan` writes nothing, `apply` refuses each missing gate by name (`--apply`, `--confirm-count`, `--i-took-a-backup`, `--i-know-this-is-the-live-database`), a second `apply` changes zero rows, an ambiguous e-mail is counted and never linked, stdout holds no `@` and no name |
| Infra | `tests/infra/test_ops_monitor_dlq_queue_sync.py` (the `support-worker` container and the `support` queue), `test_ops_monitor_profile_containers.py`, `test_compose_memory_budget.py`, `test_planned_headroom.py` and `test_party_link_env_wiring.py` (W5 to W7, W10 to W12, W26, W30, W31, W36) pass with the new rows; `tests/infra/test_internal_client_paths.py` (SP1) registers every internal client call this release adds, the pairs of W5 to W7, W10 to W12, W26, W30, W31 and W36 (the project and projects pairs), each in `CALLS` with a driver that returns the token it sent; `tests/infra/test_sweeps_run_somewhere.py` unchanged |
| Frontend | pickers, linked chips, the Engage action's three hidden states, the delivery card, the "Create project" action and its explanation, the `draft` chip; `pnpm typecheck && lint && test && build` for support, engage, crm, pulseboard, projects; visual and a11y suites |

### 9.8 Rollout

**Deploy order (owner-run, one named ref per deploy, consumers first):** (1) raise
`EDGE_BUDGET_ACCEPTED_MB` by one for the support fold (to 3947 when R2.4 and R3.8 have shipped; D-13; the
owner edits the box `.env`, an agent never does); (2) **customer** (new bindings, 360 legs, new settings);
(3) **project** and **projects** (migrations, filters, events, the create-from-deal route); (4)
**support** (migration, fold and handler, enriched events, C79, summary, filters; compose
`CUSTOMER_INTERNAL_URL`, the `support-worker` rollback container, memory-budget and ops-monitor entries,
RUNBOOK literals changed in the same commit to this release's running total); (5) **notification** (C79
client, subscribable keys); (6) **deal** (settings column, kickoff and automation consumers, C92); (7)
**owner-run data**: H11 for tickets (dry run first, reviewed, counts only, gated like H14: §9.4) and H14 with `--service support`;
projects that predate this release keep their free-text `client` until a person picks the company in the
form, because a free-text name is never matched in bulk; (8) SPAs last: support, engage, crm, pulseboard,
projects. **No new permission code**, so no reconcile.
**Flag:** `create_project_on_won` defaults off.

*Memory (computed with `infrastructure/edge/lib/memory_budget_totals.py`, running totals assume R2.4 and
R3.8 have shipped):* the support fold is `kinds.api` 98 → `kinds.api_with_worker` 99, +1 MB: ten profiles
**3750 MB (97.1% of 3862)**, with `admin` **3947 MB (102.2%)**, against `EDGE_BUDGET_ACCEPTED_MB` that must
be at least 3947; the `support-worker` container sits under `split-workers`, priced 0 while its profile is
off. Customer's extra handlers add no budget line.

### 9.9 Rollback

Code rolls back to the previous images; the schema stays. To stop the loop without a rollback: set
`create_project_on_won` false (one `PUT`), unbind nothing. *Not reversible:* tickets and projects that now
carry links keep them (harmless), a draft project already created stays (a person deletes it), an erase
already applied. Rolling support back removes the fold, so `crm.book.contact.erased` is not applied to
support again until the next deploy; the next deploy's H14 run closes the gap. *Stop conditions:* any
support event payload contains a name, e-mail or message text; a ticket linked to a contact of another
organization; two projects for one deal; `support.events.dlq` or `deal.events.dlq` depth rises; the 360's
latency rises above its 2-second leg budget on the box; **a rollback of deal or customer while its queue
carries live traffic without the catch-up of the table below ready**.

**What a code rollback loses (§4.7).**

| Consumer (queue) | Events a rollback discards | Catch-up |
|---|---|---|
| `anonymise_requester` (`support.events`: `crm.book.contact.erased`) | erasures while the old image runs | **catch-up:** H14 `--service support` |
| customer timeline lines `ticket_opened`, `ticket_solved`, `project_started` (`customer.events`) | the line of each lost event | **forward-only:** "recent activity"; the 360's support and delivery sections are read live as the viewer and never depend on an event |
| `kickoff_project` (`deal.events`: its own `stage_changed`) | the draft project of a deal won while the old image runs | **catch-up by a person:** C92 creates it for that deal (idempotent on `uq_projects_org_crm_deal`); the won deal itself is intact |
| `automation_follow_up` (`deal.events`: `task_requested`) | the follow-up an automation step requested | **forward-only:** an automation step is a trigger, not a record; the tenant's rule re-fires on the next event, and the lost one is not replayable |
| the rule `fold` for the support keys (`notification.events`) | customer-facing ticket notifications raised while the old image runs | **forward-only:** the ticket itself is intact, and the customer is told by the next status change or reply |

**Do not roll back deal or customer while its queue carries live traffic** unless the catch-up named in its
row is ready to run when it returns. The lever that loses nothing is `RUN_CONSUMERS_IN_API=false` with
`split-workers` (§4.7).

### 9.10 Acceptance criteria

- [ ] `support.tickets.crm_contact_id`, `crm_company_id`, `conversation_id`, `project.projects.crm_company_id`, `crm_deal_id` and the same in `projects`, `deal.pipeline_settings.create_project_on_won` exist; `make migration-roundtrip` is green for all four services.
- [ ] A ticket created with a contact id, with only an e-mail that resolves unambiguously, and with neither all work; an ambiguous or unreachable CRM never blocks creation; a WhatsApp ticket can be created without an e-mail and stores the empty string.
- [ ] The 360 and company 360 show support and delivery as the viewer; a viewer without the partner grant sees `null` for that section only; no ticket text, name or e-mail appears in any customer response or log.
- [ ] A customer-facing notification is staged for a rule on `.agent_reply_sent` with the reply text read at send time, one per reply, and never for an internal note.
- [ ] With the organization opt-in on, a won deal creates exactly one `draft` PulseBoard project with the company's name as `client`, no owner and the deal's currency; with it off nothing is created and C92 is the only path; a rep without the floor cannot cause it.
- [ ] An automation `create_task` creates exactly one follow-up for a subject with an owner and none otherwise.
- [ ] A contact erase overwrites the support requester fields of tickets linked to it and leaves bodies; H14 closes the gap for earlier erasures, and H11 and H14 are gated (`plan` default, `--apply` and three gates, per-organization transactions, tests).
- [ ] Support, project and projects events validate against committed schemas through their real write paths; `.replied`, `.assigned` and `.updated` carry no CRM id, and a tenant rule on them cannot address a contact.
- [ ] Every changed or new screen has loading, empty, error and forbidden states at phone width, no new token-baseline count, a story per new `@opsgrids/ui` export, a fixture and `MockRoute` per new `GET`, and no new axe violation.
- [ ] Every internal client call this release adds (W5 to W7, W10 to W12, W26, W30, W31 and the project and projects pairs of W36) is registered in `tests/infra/test_internal_client_paths.py` with a driver that returns the token it sent.
- [ ] `support` is `api_with_worker` with a `support-worker` rollback container and a `support.events` DLQ entry; the memory totals are 3750 and 3947; `make check` and `make gate` pass locally.

## 10. R4.5 Quotes-lite, discount approval and handoffs

### 10.1 Purpose, needs and deploy stages

A rep builds a priced, GST-aware quote from a deal in `deal`'s own schema; a discount above the
organization's threshold is approved through the **approval engine** by a person who may not hold an HR
record; the approver decides in a platform inbox every tenant has; an accepted quote hands off to an
order, an invoice or a project; the quote and the invoice have a PDF in the private store; storefront
products map to the one item master; and a staff member can take an online payment on an invoice.

**Needs:** R4.1, R4.3 (profile, C61, capture consumer, `buyer_snapshot`, `source_*` columns), R2.7, R3.1
(`deals.number`, company tax ids), R3.4 (`deal.pipeline_settings`), **R3.6** (A16 cancel, A17 scheduler
namespace, F10 scheduler in the core profile: the quote expiry is a scheduled event), R4.4 (C64 project
route, C92). The PDF and attachment half needs **R19** (the private store), which is outside this
programme and **not on `main`** (S36). Decisions: D-18 and D-19 (pending), D-30 (deferred), D-39, D-40.

**R19 must land in the shape ADR-0009 Q1 decided, and R4.5-B does not adopt the contract of the unmerged
branch.** Q1: the owning product authorizes and audits, `document` only presigns through an internal
`POST /internal/v1/private-objects/{id}:presign` and takes the bytes from the product server-side through an
internal put route (C75), both behind a caller allow-list that gains `deal` and `finance`, and there is no
browser upload path. Commit `45a7f8bf` (branch `sprint/private-uploads`, S36) implements a **different**
contract: public `/documents/private-objects` routes, a browser presigned POST, and authorization by the
document service under `core.content.private_document.read` and `.write` held at **OWN keyed on the employee
link**. SP4 cannot use it for three reasons. (1) A quote's or an invoice's PDF is rendered by `deal` or
`finance` on the server, so it needs the internal put route, which the branch lacks. (2) Who may read a quote
is the CRM's scope on its deal (OWN by membership) and who may read an invoice is finance's ORG grant, neither
of which `private_document.read` at OWN-by-employee-link can express, and a CRM-only organization has no
employee link at all (the no-HRMS gap the branch's own commit message records). (3) ADR-0009 Q1 puts
authorization with the owning product on purpose. **So R4.5-B waits for R19 to be reworked to Q1**: the
branch's storage layer, `private_objects` table and sniffed-content verification look reusable (inference from
its commit message), its public routes and employee-keyed permission are not used by SP4. If the owner prefers
to merge the branch as it stands, R4.5-B is redesigned first and ADR-0009 Q1 is amended on purpose, not
contradicted by accident; §13 step 8 has the owner confirm which R19 landed before R4.5-B starts.

**Deploy stages** (one release id, three deploy stages; each is independently reversible): **R4.5-A**
quotes, discount approval, the inbox, handoffs, the item-master mapping, the scaffold deletion;
**R4.5-B** PDFs (and, once D-39 is answered, attachments), after R19; **R4.5-C** staff-assisted invoice
collection.

### 10.2 Data model

**deal** (one revision on the head at merge; `# RE-PARENT HERE`; the three tables are `TenantBase`
(`id`, `organization_id`, `created_at`, `updated_at`, `created_by`, `updated_by`, `deleted_at`), `ENABLE` +
`FORCE` row-level security with `tenant_isolation`, joined to the deal `TENANT_TABLES`; same-schema
foreign keys only):

```sql
-- B28: one chain of versions per deal; every version is its own row with its own number (SD-14)
CREATE TABLE deal.quotes (
    -- TenantBase columns, then:
    deal_id UUID NOT NULL REFERENCES deal.deals(id) ON DELETE CASCADE,
    number VARCHAR(40) NOT NULL,                      -- e.g. "SO-1042/Q2" for prefix SO: {deal_number_prefix}-{deal.number}/Q{version}
    version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    currency VARCHAR(3) NOT NULL,                     -- the deal's currency; never changes
    subtotal_minor BIGINT NOT NULL DEFAULT 0,         -- sum of line gross (quantity x unit price)
    discount_minor BIGINT NOT NULL DEFAULT 0,         -- sum of line discounts
    tax_minor BIGINT NOT NULL DEFAULT 0,
    total_minor BIGINT NOT NULL DEFAULT 0,            -- subtotal - discount + tax
    discount_bps INTEGER NOT NULL DEFAULT 0,          -- derived: round_half_up(10000 * discount / subtotal)
    valid_until DATE NULL,
    terms_snapshot JSONB NOT NULL DEFAULT '{}',       -- {payment_terms_days?, notes?}  (business text, <= 1000 chars)
    buyer_snapshot JSONB NOT NULL DEFAULT '{}',       -- {legal_name, gstin?, place_of_supply?}: business identity only
    discount_reason VARCHAR(500) NULL,                -- the rep's justification: never in an event or an audit `after`
    approval_request_id UUID NULL,                    -- the approval request's `source_ref`: a fresh UUID created by
                                                      -- every `:submit` that needs approval (one per submission)
    pdf_object_id UUID NULL,                          -- plain UUID into document.private_objects (R4.5-B)
    submitted_at TIMESTAMPTZ NULL,                    -- SP4: "waiting since", and the resend control
    sent_at TIMESTAMPTZ NULL, accepted_at TIMESTAMPTZ NULL,
    CONSTRAINT ck_quotes_status CHECK (status IN ('draft', 'pending_approval', 'approved', 'sent',
                                                  'accepted', 'rejected', 'expired', 'superseded')),
    CONSTRAINT ck_quotes_currency CHECK (length(currency) = 3),
    CONSTRAINT ck_quotes_amounts CHECK (subtotal_minor >= 0 AND discount_minor >= 0 AND tax_minor >= 0
        AND total_minor = subtotal_minor - discount_minor + tax_minor AND discount_minor <= subtotal_minor),
    CONSTRAINT ck_quotes_discount_bps CHECK (discount_bps BETWEEN 0 AND 10000),
    CONSTRAINT ck_quotes_version CHECK (version >= 1)
);
CREATE UNIQUE INDEX uq_quotes_org_number ON deal.quotes (organization_id, number) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_quotes_org_deal_version ON deal.quotes (organization_id, deal_id, version);
CREATE INDEX ix_quotes_org_deal ON deal.quotes (organization_id, deal_id);
CREATE INDEX ix_quotes_org_status ON deal.quotes (organization_id, status) WHERE deleted_at IS NULL;
CREATE INDEX ix_quotes_org_approval_request ON deal.quotes (organization_id, approval_request_id)
    WHERE approval_request_id IS NOT NULL;                -- the decision handler finds its quote by source_ref

-- B29: lines carry snapshots so a later item edit never reprices a sent quote
CREATE TABLE deal.quote_lines (
    -- TenantBase columns, then:
    quote_id UUID NOT NULL REFERENCES deal.quotes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    inventory_item_id UUID NULL,                      -- plain UUID into operations; NULL = free text
    name VARCHAR(200) NOT NULL, sku VARCHAR(60) NULL, hsn_sac VARCHAR(12) NULL, uom VARCHAR(20) NOT NULL DEFAULT '',
    quantity_milli BIGINT NOT NULL,                   -- milli-units (ADR-0014)
    unit_price_minor BIGINT NOT NULL,
    discount_bps INTEGER NOT NULL DEFAULT 0,
    tax_rate_bps INTEGER NOT NULL DEFAULT 0,          -- 1800 = 18%, never a float
    line_gross_minor BIGINT NOT NULL,                 -- SP4: derived and stored (half-even: quantity x price)
    line_total_minor BIGINT NOT NULL,                 -- taxable value: gross - line discount (discount half-up)
    line_tax_minor BIGINT NOT NULL,                   -- SP4: ROUND_HALF_UP(line_total * tax_rate_bps / 10000), as finance's gst_amount_minor
    CONSTRAINT ck_quote_lines_quantity CHECK (quantity_milli > 0),
    CONSTRAINT ck_quote_lines_price CHECK (unit_price_minor >= 0),
    CONSTRAINT ck_quote_lines_bps CHECK (discount_bps BETWEEN 0 AND 10000 AND tax_rate_bps BETWEEN 0 AND 10000),
    CONSTRAINT ck_quote_lines_amounts CHECK (line_gross_minor >= 0 AND line_total_minor >= 0 AND line_tax_minor >= 0
                                             AND line_total_minor <= line_gross_minor)
);
CREATE UNIQUE INDEX uq_quote_lines_org_quote_position ON deal.quote_lines (organization_id, quote_id, position);
CREATE INDEX ix_quote_lines_org_quote ON deal.quote_lines (organization_id, quote_id);
CREATE INDEX ix_quote_lines_org_item ON deal.quote_lines (organization_id, inventory_item_id)
    WHERE inventory_item_id IS NOT NULL;

-- SP4 (B68): what has already been handed off, for the UI and for idempotency
CREATE TABLE deal.quote_handoffs (
    -- TenantBase columns, then:
    quote_id UUID NOT NULL REFERENCES deal.quotes(id) ON DELETE CASCADE,
    target VARCHAR(10) NOT NULL,
    ref_id UUID NOT NULL,                             -- the callee's id: order, invoice or project
    CONSTRAINT ck_quote_handoffs_target CHECK (target IN ('order', 'invoice', 'project'))
);
CREATE UNIQUE INDEX uq_quote_handoffs_org_quote_target ON deal.quote_handoffs (organization_id, quote_id, target)
    WHERE deleted_at IS NULL;

ALTER TABLE deal.pipeline_settings ADD COLUMN discount_approval_threshold_bps INTEGER NULL;   -- B27; NULL = no approval
ALTER TABLE deal.pipeline_settings ADD CONSTRAINT ck_pipeline_settings_discount_threshold
    CHECK (discount_approval_threshold_bps IS NULL OR discount_approval_threshold_bps BETWEEN 0 AND 10000);
```

`B57` (`deal.activity_attachments`) and its routes C81 to C83 are **not** in this revision: they wait for the
ADR's answer to D-39 (default in §6.5) and for R19.

**finance** (one revision on R4.3's head): `invoice_documents` (B55, insert-only, refused `UPDATE` and
`DELETE` by trigger in the shape of `einvoice_filings`, R4.5-B) and the quote back-reference guarantee:

```sql
CREATE UNIQUE INDEX uq_invoices_org_source ON finance.invoices (organization_id, source_type, source_id)
    WHERE source_type IS NOT NULL AND deleted_at IS NULL;                -- one invoice per quote
CREATE TABLE finance.invoice_documents (
    -- TenantBase columns, then:
    invoice_id UUID NOT NULL REFERENCES finance.invoices(id) ON DELETE RESTRICT,
    pdf_object_id UUID NOT NULL, rendered_at TIMESTAMPTZ NOT NULL, renderer_version VARCHAR(20) NOT NULL
);
CREATE UNIQUE INDEX uq_invoice_documents_org_invoice_object
    ON finance.invoice_documents (organization_id, invoice_id, pdf_object_id);
CREATE INDEX ix_invoice_documents_org_invoice ON finance.invoice_documents (organization_id, invoice_id, rendered_at DESC);
-- trigger forbid_invoice_document_mutation: BEFORE UPDATE OR DELETE ... RAISE EXCEPTION (precedent einvoice_filings)
```

**operations**: `ALTER TABLE operations.orders ADD COLUMN source_type VARCHAR(20) NULL, ADD COLUMN source_id UUID
NULL` (B46) and `CREATE UNIQUE INDEX uq_orders_org_source ON operations.orders (organization_id, source_type,
source_id) WHERE source_type IS NOT NULL AND deleted_at IS NULL`. **storefront**: `ALTER TABLE
storefront.products ADD COLUMN inventory_item_id UUID NULL` and `CREATE INDEX ix_products_org_inventory_item ON
storefront.products (organization_id, inventory_item_id) WHERE inventory_item_id IS NOT NULL` (B52; many
products may map to one item).

**approval** (B66; head `435fc681c9e5`; additive, rollback-tolerant by SD-13):

```sql
ALTER TABLE approval.approval_requests ADD COLUMN requester_membership_id UUID NULL;
ALTER TABLE approval.approval_requests ADD COLUMN decided_by_membership_id UUID NULL;
ALTER TABLE approval.approval_steps ADD COLUMN approver_membership_id UUID NULL;
ALTER TABLE approval.approval_requests ADD CONSTRAINT ck_approval_requests_membership_decider
    CHECK (decided_by_membership_id IS NULL OR (status <> 'pending'
           AND decided_by_membership_id IS DISTINCT FROM requester_membership_id));
CREATE INDEX ix_approval_requests_org_requester_membership ON approval.approval_requests
    (organization_id, requester_membership_id) WHERE requester_membership_id IS NOT NULL;
CREATE INDEX ix_approval_steps_org_approver_membership ON approval.approval_steps
    (organization_id, approver_membership_id) WHERE approver_membership_id IS NOT NULL AND outcome = 'open';
```

**The guard functions are replaced in the same revision (S44).** `approval.guard_approval_request_change()` and
`approval.guard_approval_step_change()` (`c8d3e6f4a1b2_guard_triggers.py:39-64, 97-146`) each compare an
explicit column tuple, so a column outside it stays mutable by the one `UPDATE` the guard permits: the new
`requester_membership_id` would be editable on a pending request, `decided_by_membership_id` rewritable in the
single post-decision `UPDATE` that writes the `application_*` trio, and `approver_membership_id` alterable
while an open step is closed, which defeats the decide-once and append-only guarantees that SD-13 relies on.
The revision `CREATE OR REPLACE`s both functions with `requester_membership_id` added to the identity tuple of
**both** states (pending and decided, as `requester_employee_id` is), `decided_by_membership_id` added to the
decided-state tuple (beside `decided_by_employee_id`) and `approver_membership_id` added to the step tuple
(beside `approver_employee_id`). The triggers themselves, and the delegation guard (a membership requester has
no delegations), are untouched. The **downgrade restores the previous function bodies first** and then drops
the columns, so the previous image runs under exactly the guards it shipped with.

The existing employee columns, their NOT NULLs and CHECKs are **untouched**. For a membership-keyed
request, `requester_employee_id` and, once decided, `decided_by_employee_id` carry the surrogate
`uuid5(MEMBERSHIP_SURROGATE_NAMESPACE, str(membership_id))` (a constant UUID fixed once in
`services/approval/app/domain.py`), and a step assigned to a membership carries it in
`approver_employee_id` beside `approver_membership_id`. The surrogate is never used to authorize: every
authorization check in the new code reads the `*_membership_id` column when it is set. It exists so that the
previous image, which reads these rows as it always did, finds a non-null employee id with no hierarchy
node, shows an unknown requester and routes nothing, instead of failing the HR approvals list for the whole
organization. Downgrade drops the columns and **refuses** while any membership-keyed row exists.

### 10.3 Backend contract

**deal, public** (`/api/v1/deals`, product `crm`, `Entitled("crm")`; static paths declared before `/{deal_id}`;
every route re-applies `assert_can_touch` through the deal)

| Handle | Route | Permission, floor | Request → response and errors |
|---|---|---|---|
| C18 | `GET /deals/{deal_id}/quotes`, `GET /deals/quotes/{quote_id}` | `crm.pipeline.quote.read` (E25) | `QuoteOut` with `lines`, `handoffs [{target, ref_id}]`, `approval {state, since}`; 404 foreign or out of scope |
| C18 | `POST /deals/{deal_id}/quotes` | `crm.pipeline.quote.write` (E26) | `{valid_until?, terms?}` → 201 draft quote v1; the buyer snapshot is read from C50 **before** the write transaction; 409 `deal_closed` (the deal's outcome is not open), 409 `quote_exists` (a live chain exists: use `:revise`), 409 `deal_number_missing`, 422 |
| C18 | `PATCH /deals/quotes/{quote_id}`, `PUT /deals/quotes/{quote_id}/lines` | `crm.pipeline.quote.write` | only while `draft` (409 `not_editable`); `PUT` replaces all lines atomically, at most 100, each `{inventory_item_id?, name, sku?, hsn_sac?, uom?, quantity (decimal string), unit_price_minor, discount_bps?, tax_rate_bps?}`; totals are **derived and never accepted**; a line with `inventory_item_id` is allowed only for an organization entitled to Operations and is proved through C80 `ids` (404 `item_not_found`); an omitted price takes the item's `selling_price_minor` when its currency equals the quote's, else 422 `price_required` |
| C19 | `POST /deals/quotes/{quote_id}:submit` | `crm.pipeline.quote.write` | `{discount_reason?}` → 200 `QuoteOut`; at or below the threshold, or no threshold: `approved`; above it: 422 without a 1 to 500 character `discount_reason`, else `pending_approval`, a fresh `approval_request_id` and EV23. Resubmitting a `pending_approval` quote re-publishes EV23 with the **same** id (the approval insert is idempotent on it, and an already decided request re-stages its decision, §10.4); 409 for any other state |
| C19 | `POST /deals/quotes/{quote_id}:send` | `crm.pipeline.quote.write` | from `approved` only; refreshes the buyer snapshot (C50), sets `sent_at`, supersedes the previous version, arms expiry, EV22 `.sent`; 409 otherwise |
| C19 | `POST /deals/quotes/{quote_id}:accept`, `:reject` | `crm.pipeline.quote.write` | from `sent` only (the rep records the customer's answer); `:accept` also moves the deal to the organization's first `won` stage through `move_stage` (acting_as `quote.accepted`), cancels the expiry, EV22; `:reject` sets `rejected`, cancels expiry |
| C91 (**SP4 addition**) | `POST /deals/quotes/{quote_id}:revise` | `crm.pipeline.quote.write` | from `sent`, `rejected`, `expired` or `approved`: a new `draft` row, `version + 1`, lines and terms copied, number `…/Q{version}`; the old row stays as it is until the new one is `:send`-ed (then it becomes `superseded`) |
| C20 | `POST /deals/quotes/{quote_id}:handoff` | `crm.pipeline.quote.write`, `minimum=Scope.ORG` for `order` and `invoice`; `project` keeps the caller's own scope only while `create_project_on_won` is true (registry C20) | `{target: "order" \| "invoice" \| "project", idempotency_key}` → 200 `{target, ref_id, replayed}`. `order` and `invoice` need `accepted`; `project` needs a won deal. Claim, call C64 outside any transaction, record `quote_handoffs` plus a system activity (B31) and an audit row; 409 `not_accepted`, 422 `lines_need_inventory_items` (an order from free-text lines), 422 `multiple_warehouses`, 503 `callee_unavailable`. **Negative tests, all five** (C20) |
| C87 | `GET /deals/quote-items:search?q=&limit=` | `crm.pipeline.quote.write` | trimmed item list via C80; **404** when the organization has no Operations |
| C11 | `PUT /deals/settings` | `crm.pipeline.settings.write` (ORG) | gains `discount_approval_threshold_bps` (0 to 10000 or `null`); setting it audits the old and new value; refuses a `deal_number_prefix` outside `^[A-Z][A-Z0-9]{0,7}$` (422 `prefix_invalid`, §10.4) |
| E25, E26 | catalogue | `crm.pipeline.quote.read` and `.write`, OWN default, ORG max, not dangerous | admin-first (E.4), reach `CRM_MANAGER` (TEAM, †) and `CRM_REP` (OWN) after R4.5 in a later reconcile; **`CRM_VIEWER` holds neither** (registry E.2: "no money"); pending D-20 and D-24 |

**deal, internal.** C78 `GET .../quotes/{quote_id}/approval-summary` (caller `approval`): `{quote_id,
quote_number, version, deal_name, buyer_legal_name, currency, subtotal_minor, discount_bps, discount_minor,
total_minor, discount_reason}`; the registry keeps `deal_name` and `buyer_legal_name` for approval's own
display, and approval stores **neither** in the request (§10.4).

**approval, public** (`/api/v1/approvals`, platform-wide, no product gate)

| Route | Permission | Change |
|---|---|---|
| `GET /approvals/request-types` (C66) | `core.approval.request.read` | `[{request_type, label, product}]` for every known type, served from a static map in approval, so a new type needs no HRMS release |
| `GET /approvals/inbox`, `GET /approvals/requests`, `GET /approvals/requests/{id}`, `POST /approvals/requests/{id}/decision` | existing `core.approval.request.read/decide` | a request may now be keyed on a membership: the inbox shows a step to its `approver_membership_id` at OWN, every pending request at ORG; the decision records `decided_by_membership_id` for every decider and refuses a decider equal to the requester (403) |

**Other services.** C64: operations `POST .../orders:create-from-quote` (creates a `DRAFT` order with
`source_type = 'quote'`, `source_id`, the CRM ids, channel `sales`, priced lines from the quote's snapshots;
the warehouse is that of the lines' items, 422 `multiple_warehouses` otherwise; idempotent on
`uq_orders_org_source`) and finance `POST .../invoices:create-from-quote` (resolves the profile through C61,
applies `evaluate_order_invoice_request`'s snapshot semantics, `line_amount_minor` taken as the taxable value
and the rate from `tax_rate_bps / 100`, derives supply type and place of supply, writes `source_type`,
`source_id`, `crm_company_id`, `buyer_snapshot`; **finance's totals win**: the response carries
`quote_total_delta_minor` and deal records it in the audit row; idempotent on `uq_invoices_org_source`).
Because the quote rounds every percentage half-up exactly as finance does (§10.4), the delta is **zero in the
normal case** and a non-zero value is a safety net that raises an alarm (a log line at `error` and the audit
row), not a routine outcome.
Both callers: `deal`; org claim; entitlement of the callee's own product re-checked through product service
(W36); three negative tests, an unentitled-organization test and a below-floor test (C20) each. C80 gains an
`ids` parameter and the caller `storefront`; `PATCH /storefront/products/{id}` accepts `inventory_item_id`
(proved through C80 `ids`; 404 `item_not_found`); `GET /storefront/products?unmapped=true` and
`GET /storefront/products/{id}/item-suggestions` (**SP4 addition**, C93; `storefront.catalogue.product.write`,
ORG; at most five `{id, sku, name}` from C80 search by the product's code).

**Events** (committed in this release, each with a producer-side test): EV22 `crm.pipeline.quote.created`,
`.sent`, `.accepted`, `.rejected`, `.expired`; EV23 `crm.pipeline.discount.requested`; EV24
`crm.pipeline.discount.decided`; EV28 `crm.pipeline.quote.expiry_requested`; `core.approval.request.decided`
(the first committed schema for a `core.approval.*` event: `decided_by_employee_id` becomes nullable and
`decided_by_membership_id` is added; `decided_by_name` is a display name already on the bus and is left
frozen); `payment.collections.payment.captured` and `.refunded` `reference_type` enum gains
`finance.invoice` (R4.5-C). Scheduled targets use A17's default prefix `crm.pipeline.*` for producer `deal`;
if R3.6 leaves the scheduler rule alone the same tails use `deal.pipeline.*`.

### 10.4 Domain rules

**Totals (server only, `app/domain.py`, pure).** Two rounding rules, each the one the owning code already
applies. **Quantity times price is half-to-even** (ADR-0014 §2, `quantity.value_minor`, which
`operations.app.domain.order_line_amount_minor` reuses: `services/operations/app/inventory/quantity.py:280-282`,
`services/operations/app/domain.py:338-342`). **Every percentage of money is half-up through `Decimal`**, exactly
as finance's `gst_amount_minor` (`services/finance/app/domain.py:573-581`: `ROUND_HALF_UP`), operations'
`expected_gst_amount_minor` that mirrors it (`services/operations/app/domain.py:351-370`) and marketing's
`_round_half_up` (`services/marketing/app/domain.py:42-45`) do; a non-negative integer form is
`(n * bps + 5000) // 10000`. For each line:
`gross = round_half_even(quantity_milli * unit_price_minor / 1000)`, `line_discount =
round_half_up(gross * discount_bps / 10000)`, `line_total = gross - line_discount` (the taxable value),
`line_tax = round_half_up(line_total * tax_rate_bps / 10000)`, i.e. `gst_amount_minor(line_total,
Decimal(tax_rate_bps) / 100)`. The quote's `subtotal`, `discount`, `tax` and `total`
are the sums, `discount_bps = round_half_up(10000 * discount / subtotal)` (0 when `subtotal = 0`).
Rounding is once per line, as finance's `create_invoice` rounds the tax once per line and sums
(`services/finance/app/services.py:729-768`), so the quote's tax equals the invoice's tax and
`quote_total_delta_minor` (C64) is a safety net and not the normal case: a half-even tax would disagree with
finance on every exact-half amount. Tax is a single amount:
the CGST/SGST/IGST split depends on the seller's state, which only finance holds, so the quote shows
"GST" and finance computes the split on the invoice (SD-14).

**State machine** (the only transitions; anything else is 409):

| From | Event | To | Side effects |
|---|---|---|---|
| draft | `:submit`, no approval needed | approved | `submitted_at` |
| draft | `:submit`, discount above threshold | pending_approval | `submitted_at`, a fresh `approval_request_id`, EV23 |
| pending_approval | decision `approved` | approved | EV24 |
| pending_approval | decision `rejected` | **draft** | audit `quote.discount_rejected`, EV24; the rep revises and resubmits, which generates a new `approval_request_id` and so opens a new request |
| approved | `:send` | sent | `sent_at`, buyer snapshot refreshed, previous version `superseded`, expiry armed, EV22 |
| sent | `:accept` | accepted | `accepted_at`, deal moved to its first won stage, expiry cancelled, EV22 |
| sent | `:reject` | rejected | expiry cancelled, EV22 |
| sent | expiry fires | expired | EV22 `.expired` |
| sent, rejected, expired, approved | `:revise` | (new draft) | new row, `version + 1` |

*Deal gating.* `move_stage` into a stage whose outcome is `won` is refused with 409
`discount_pending_approval` while any quote of the deal is `pending_approval`; accepting a quote is the
normal path. A closed deal accepts no new quote. *Expiry.* `valid_until` is a calendar date; the quote
expires at `valid_until + 1 day, 00:00 UTC` through a scheduled `crm.pipeline.quote.expiry_requested`
(idempotency key `quote:{id}:{valid_until}`, **single-use** by A16: a quote row is sent once and `:revise`
creates a new row with its own key, EV28, so a cancelled key is never requested again, and a cancel for a job
that is no longer pending is a no-op); the handler re-reads the row and is a no-op unless the status
is still `sent` and `valid_until` is unchanged; `:accept`, `:reject`, `:revise`-then-`:send` and a deletion
cancel the job by key (A16). **A lost expiry event must not let an expired quote be accepted** (a deal rolled
back while the job fires discards it, S46): `:accept` also refuses a `sent` quote whose `valid_until` has passed
(409 `quote_expired`), and the UI shows such a quote as "Expired" beside its stored status. *Numbering.* `number = "{deal_number_prefix}-{deal.number}/Q{version}"`; so
that the number is never free text (it sits in the approval summary, ADR-0006 §3), `PUT /deals/settings` refuses
a prefix outside `^[A-Z][A-Z0-9]{0,7}$` with 422 `prefix_invalid`.

**The discount approval flow.** (1) `:submit` generates a fresh `approval_request_id`, stores it on the quote,
and stages EV23 `{organization_id, discount_request_id (that id), quote_id, deal_id, requester_membership_id
(the caller's membership, never `own_ref`), discount_bps, discount_minor, deal_value_minor, currency}`: ids and
numbers only. The id is **per submission, not the quote id**: approval's request key
`(organization_id, request_type, source_ref)` is unique (`services/approval/app/models.py:292-297`) and
`source_ref` is a UUID, so keying on the quote would make a resubmission after a rejection collide with the
decided request and leave the quote waiting for ever. (2) Approval's handler (claim, then call,
then record) checks its marker, reads C78 with the payload's `quote_id` and, for the approver, up to three direct-manager reads from account
(C57, `ids=`, outside any transaction: requester → manager → manager's manager, the first **active** one
that is not the requester), then opens the request: `request_type = crm.pipeline.discount`, `source_ref`
the `discount_request_id` (a redelivered EV23 opens nothing new, and when that request is **already decided**
it re-stages `core.approval.request.decided` with the stored decision instead, which is how a decision that
deal never applied is applied by the rep's "Resend request": §10.9), `summary =
{quote_number, quote_version, discount_bps, discount_minor, deal_value_minor, currency}` (enumerable facts
only: **no deal name and no buyer name**, because a lead-converted deal name embeds a person's name, S13, and
a one-person company's legal name is a person's), `request_note` = the rep's reason from C78 (free text is
classified customer content in ADR-0006 §3), one step to the manager, or **unassigned** (any holder of
`core.approval.request.decide` at ORG) when no active manager exists within three levels. (3) The decision
publishes `core.approval.request.decided`; deal's handler finds the quote by `approval_request_id = source_ref`
(a decision for a request that is no longer the quote's current one is a no-op), applies the decision (the
table above) and stages EV24 `{organization_id, quote_id, deal_id, decision, decided_by_membership_id}`;
approval consumes EV24 as the product's **return leg** and records `application_state = applied`
(ADR-0006 §12). (4) A membership requester has no delegations and no hierarchy escalation: where the
organization has enabled an SLA policy for the type (a new type has none, so by default a step simply waits for
its manager), the sweep walks the **employee** hierarchy from the surrogate, finds no node and moves the step
to unassigned (`escalate_step`: `services/approval/app/services.py:1397-1430`), which is the intended ceiling.
The decision permission is `core.approval.request.decide`, a `core` code that a CRM role template can never
hold (the exporter refuses `cross_product`: `tools/export_permissions.py:191-221`), so an approver is a
person an organization admin has given an approval role; a manager decides a step addressed to them with the
OWN grant and any step with the ORG grant (inference: `libs/contracts/permissions/core.json` lists the code in
four role grants, three at ORG and one at OWN; the reconcile dry run prints which templates hold it, and the
release note tells the owner to read it before the first request is raised).

**Approval moves to the core profile (SD-13, F18).** The approval container runs under `hrms` today
(`infrastructure/edge/memory-budget.json`), so a box with CRM and no HRMS would have no consumer for
EV23. R4.5 sets `containers.approval.profile` to `null` (core), removes `approval` from
`PROFILE_API_CONTAINERS["hrms"]` and adds it to the core list in
`libs/platform-core/src/platform_core/ops_monitor/config.py`, moves `approval.events` from
`PROFILE_DLQ_QUEUES["hrms"]` to `CORE_DLQ_QUEUES`, and drops `profiles: ['hrms']` from the compose service.
The ten-profile total is unchanged (approval is already counted under `hrms`), a box that runs no HRMS
gains one 99 MB container, and the HR behaviour is unchanged.

**Draft of ADR `approvals-for-tenants-without-hrms` (expected 0042; D-18).** R4.5 files it as
`docs/adr/0042-approvals-for-tenants-without-hrms.md`, `Status: proposed` (the number confirmed against
`docs/decisions.md` at merge; the next free number there is 0038 today), indexed in `docs/decisions.md` and
linked from `docs/adr/open-questions-register.md`, in the same change as the first approval edit. ADR-0000
forbids a later ADR to contradict an earlier one quietly, and this one narrows two decisions of ADR-0006.
*Context.* The engine is employee-keyed throughout (S34): `requester_employee_id` is NOT NULL, steps are
assigned by employee, escalation walks the employee hierarchy and the container runs under `hrms`, so a tenant
without HRMS has no approvals. ADR-0006 refuses `account.memberships.manager_membership_id` as a routing input
("a second, unsynchronised manager field") and its Q1 says approval "never reads it"
(`docs/adr/0006-approval-engine.md:71, 1479-1504`). *Decisions.* (1) A request may be keyed on a
**membership**: additive nullable columns, surrogate employee ids so the previous image can read the row, and
the guard triggers extended to freeze the new columns (§10.2). (2) **For a membership-keyed request only**, the
approver is the requester's direct manager read from account (C57), up to three levels, else the step is
unassigned (`core.approval.request.decide` at ORG); an employee-keyed request routes on the HR hierarchy
exactly as before, so Q1's "never" becomes "never for an employee-keyed request" and the two manager facts
still gate different requesters. (3) A membership requester has no delegations and no hierarchy escalation; an
SLA moves its step to unassigned. (4) The engine runs in the **core profile** and the inbox is a platform page
every tenant has, gated by `core.approval.request.read` and `.decide`. (5) Request-type labels come from a
static map, so a new type needs no HRMS release. *Rejected.* A third, CRM-local approval state machine
(D-18); relaxing the NOT NULLs and storing only membership ids (the previous image would 500 on every
approvals list, SD-13); routing HR requests on the member manager (Q1's reasoning stands for them).
*Consequences.* Two manager facts that can disagree stay visible and harmless; one 99 MB container now runs on
a box with no HRMS; ADR-0006 gets a pointer and Q1 an amendment note. The owner accepts the ADR before the
approval deploy of §10.8.

**Item master and the storefront mapping (D-19).** A quote line's `inventory_item_id` is allowed only for an
organization entitled to Operations and is proved server-side; the composer reads the master through C87,
which calls C80 and returns 404 when Operations is absent, so a CRM-only rep needs no `operations.*`
permission. `storefront.products.inventory_item_id` is optional and many-to-one; the mapping screen lists
unmapped products with suggestions by `code` equals `sku`; price and stock authority stay where they are
(inventory price for quotes, storefront price band for the shop).

**Private objects and PDFs (R4.5-B, after R19 in ADR-0009 Q1's shape: §10.1).** The owning product
authorizes and audits; `document` only presigns (ADR-0009 Q1), and the owning service puts the rendered bytes
through `document`'s internal put route. `GET /deals/quotes/{id}/pdf` (`crm.pipeline.quote.read` and
`assert_can_touch` through the deal) and `GET /finance/invoices/{id}/pdf` (C74, `finance.billing.invoice.read`,
ORG) check scope, write an audit row (ids only), ask `document` for a 60-second
presigned URL and answer **302**; the URL is never stored or returned in a body. The renderer runs in the
owning service, lazily imported, and uses a **permissive pure-Python library** that `make licenses` accepts
(`tools/licenses.toml` lists BSD, MIT, Apache and no LGPL; candidate `reportlab`, BSD; `fpdf2`, LGPL, is
excluded by that policy: inference from the packages' published licences, confirmed by the PR's licence
run). **The library has a memory price that D-13 does not yet include**: it raises deal's and finance's resident
set (inference: 10 to 20 MB each once imported, **not measured**), so the PR measures both before R4.5-B ships
and prices them in a new budget kind, `api_with_worker_render` (F8; `api_with_worker`'s 99 MB plus the measured
delta rounded up to a whole MB; `mem_limit` raised in the same commit only if the measured set approaches
256 MB), which **only `deal` and `finance` use**, so `kinds.api` and `kinds.api_with_worker`, which price
dozens of containers, never move. R4.5-B ships only after the owner has raised `EDGE_BUDGET_ACCEPTED_MB` to the
figure `memory_budget_totals.py` then prints (with insights the worst case is 3871 to 3891 MB for the ten
profiles and 4068 to 4088 with `admin`: D-13, §11.9); `preflight.sh` is the stop condition, refusing a deploy
whose post-deploy total exceeds the accepted ceiling or 125% of MemTotal. A document service that is not running (it runs only under the `storefront` profile) makes both routes
answer 503 `store_unavailable`, never a wrong answer. The invoice PDF renders from `invoices.buyer_snapshot`
and the stored lines, so a re-render after a customer rename reads the same; an invoice without a snapshot
renders from the live profile with the footer "Buyer details as at {date}". Each render inserts one
`invoice_documents` row; the current file is the newest by `rendered_at`.

**Invoice collection (R4.5-C, SD-15, pending D-30).** `POST /finance/invoices/{invoice_id}/payments/checkout`
(`finance.billing.payment.record`, ORG) calls payment's collections API with `reference_type =
finance.invoice` for the invoice's outstanding amount (payment's `COLLECTION_CALLERS` gains `finance` and the
reference constant a second value: `services/payment/app/api/collections_internal.py:51`,
`services/payment/app/collections/domain.py:58`), and returns what the browser needs to open the hosted
checkout exactly as operations does for an order; `GET /finance/invoices/{invoice_id}/payments`
(`finance.billing.payment.read`, ORG; 404 for another organization's invoice) lists the
collections. A verified capture is applied by §8.4.4 (reference by invoice id). There is no URL a customer
can open: a public pay page is D-30. Refunds are recorded only (D-28).

### 10.5 Consumers and idempotency

| Queue | Binds (new) | Handler and marker | Notes |
|---|---|---|---|
| `approval.events` (approval, folded) | `crm.pipeline.discount.requested`, `crm.pipeline.discount.decided` | `open_discount_request`, `apply_discount_return`, raw `event_id` | claim: marker and existing request; call: C78 and C57 outside any transaction; record: insert `ON CONFLICT DO NOTHING` on the request key and its step, mark processed. EV24's handler sets `application_state` exactly once |
| `deal.events` (deal, folded) | `core.approval.request.decided`, `crm.pipeline.quote.expiry_requested`, `crm.book.contact.erased` | `apply_discount_decision` (filtered on `request_type`), `expire_quote`, `erase_quote_copies` | the decision handler is a no-op for a quote not in `pending_approval`; erase clears `buyer_snapshot`'s name on the contact's quotes only when the company is the contact's one-person company (counts only), and, if B57 ships, purges attachments (D-39) |
| `customer.events` (customer, folded) | `crm.pipeline.quote.sent`, `.accepted` | the R4.3 projection handler | timeline kinds `quote_sent`, `quote_accepted` |
| `notification.events` | `crm.pipeline.quote.accepted`, `.rejected`, `.expired`, `crm.pipeline.discount.decided` | the rule `fold` | EV41: in-app to the requester only |
| `finance.events` (finance, folded) | (R4.3 bindings) | the capture handler | gains `reference_type = finance.invoice` |

Every handler binds the tenant from the envelope and cross-checks the payload organization. The unique
indexes `uq_quotes_org_number`, `uq_quote_handoffs_org_quote_target`, `uq_invoices_org_source` and
`uq_orders_org_source` and approval's `uq_approval_requests_org_type_source` are the second net.

**Both new bindings on `customer.events` and `notification.events` are deployed, and bound, before deal's
producer is enabled** (§4.6 rule 1, §10.8): deal emits EV22 (`quote.sent`, `.accepted`, ...) and EV24
(`discount.decided`) from the moment its quote routes are live, and an event with no bound queue is dropped
(`events.py:250-262`), so a missing binding would silently lose the timeline line and the requester's
decision notification.

### 10.6 UI

- **CRM**: a quote tab on the deal page and a quote page (`/quotes/:id`): a line editor (item lookup
  `useItemLookup`, G18, hidden without `useEntitlement('operations')`; quantity, price, discount and tax
  fields in money and percent inputs that convert to minor units and basis points at the boundary; totals
  shown as server-returned, never computed on the client), a status chip with the next legal action only,
  "Waiting for approval since {time}" with **Resend request** after 15 minutes, handoff buttons that show
  what has already been handed off, and the PDF link when it exists. The settings hub (R3.4) gets
  "Discount approval": a percent input for the threshold with "Off" and a sentence naming who decides
  (G11 reload after a write). Every view has loading, empty, error and forbidden states and phone-width
  layout.
- **Dashboard**: an Approvals page and a nav entry shown with `core.approval.request.read` (G14): the
  components `ApprovalInbox`, `ApprovalRequestCard`, `ApprovalDecisionDialog` in `@opsgrids/ui` and
  `useApprovalInbox`, `useDecideApproval` in `@opsgrids/client`; a card shows the type label from C66, the
  summary numbers, the note and "Open in CRM" (a host link from the type's product); the dialog requires
  a comment on a rejection. HRMS' `Approvals.tsx` and `approvalCopy.ts` migrate onto the shared
  components as the **last, separable task** (its own visual diff and baseline).
- **Finance**: invoice detail gets "Download PDF" (R4.5-B) and "Collect online payment" (R4.5-C) with the
  launcher extracted from `product-operations` into `@opsgrids/client` as `useHostedCheckout` (G25)
  so the two apps share one implementation; the Payments banner of R4.3 lists unmatched captures.
- **Storefront**: a product mapping screen (unmapped filter, suggestions, one action).
- **Operations**: an order created from a quote shows "From quote {number}".
- **Every changed or new screen** has loading, empty, error and forbidden states at phone width (the
  harness's 375 px, no horizontal scroll at 360 px); no raw colour or size value (`pnpm tokens:baseline`
  without `--allow-increase` writes nothing); a story for each new `@opsgrids/ui` export (`ApprovalInbox`,
  `ApprovalRequestCard`, `ApprovalDecisionDialog`) and a fixture and `MockRoute` in
  `frontend/opsgrids-visual/fixtures/api/` for each new `GET` (inbox, request types, quotes, online
  captures); the HRMS migration onto the shared components keeps its own baseline diff; no new axe violation
  (`make ui-visual`).

### 10.7 Tests

| Area | Tests that will exist |
|---|---|
| Negative authz | `services/deal/tests/test_quote_authz.py`: every quote route: no permission 403, not entitled 402, a deal outside the caller's scope 404, another organization's quote 404; `:handoff` the five negatives of C20 plus `not_accepted` 409; `PUT /deals/settings` threshold at a custom OWN grant 403 and a prefix outside the pattern 422; `services/approval/tests/test_membership_requests_authz.py`: approval decision by the requester 403, by a non-approver without ORG 403, by an ORG holder on an unassigned step 200, `GET /approvals/request-types` without `core.approval.request.read` 403; the internal routes C78, C64 (operations, finance) and C80 `ids`: three negatives each (`services/deal/tests/test_approval_summary_authz.py`, `services/operations/tests/test_create_from_quote_authz.py`, `services/finance/tests/test_create_from_quote_authz.py`, `services/operations/tests/test_inventory_search_authz.py`); account's membership read C57 for its new caller `approval`: no token 401, a caller off the list 403, a wrong organization claim 403, and a call through approval's real client helper accepted for its own organization and refused for another's (the tests SP2 and SP3 write for that route, extended); `services/finance/tests/test_invoice_collection_authz.py`: the collection route 403 without `finance.billing.payment.record`, `GET /finance/invoices/{id}/payments` 403 without `finance.billing.payment.read` and 404 for another organization's invoice; `GET /finance/invoices/{id}/pdf` 403 without `finance.billing.invoice.read` and 404 for another organization's invoice, `GET /deals/quotes/{id}/pdf` 403 without `crm.pipeline.quote.read` and 404 out of scope. **The storefront mapping routes** (`services/storefront/tests/test_product_mapping_authz.py`): `PATCH /storefront/products/{id}` with `inventory_item_id`, `GET /storefront/products?unmapped=true` and `GET .../item-suggestions` each 403 without `storefront.catalogue.product.write`, 403 with a custom OWN grant (the ORG floor), another organization's product 404, another organization's item id 404 `item_not_found`, an organization with no Operations answers as C87 does (404) and never maps a free-text id |
| Cross-tenant | a quote, line, handoff, approval request or capture of organization B is invisible and unwritable from A; `uq_quotes_org_number` is per organization; C78 for B's quote answers 404 under A's claim; the approver search never reads another organization's memberships |
| Idempotency and redelivery | `:submit` twice (one request, one `approval_request_id`), EV23 twice (one request), a rejected discount revised and resubmitted (a **second** request with its own `source_ref`, the first stays rejected), a decision for a superseded request id (no-op), `.decided` twice and out of order (one decision), expiry delivered twice and after an `:accept` (no change), a handoff replayed (one order, one invoice, one project, one `quote_handoffs` row), `:revise` twice (versions 2 and 3, numbers unique), the capture on an invoice twice; **Resend request** for a quote whose decision deal never applied re-stages the decision once and the quote follows (a second resend changes nothing), and for a request approval never opened it opens it |
| Arithmetic | table-driven totals: quantity times price half-to-even per line, and the line discount, the tax and `discount_bps` **half-up** (an exact-half case for each: a taxable 25 at 1800 bps is 4.5 and rounds to 5 where half-even would give 4; a gross of 25 at a 1000 bps discount is 2.5 and rounds to 3), a 0% and an 18% line, a zero quantity refused, `total = subtotal - discount + tax` holds for 10 000 random quotes (property test), `line_tax == finance.domain.gst_amount_minor(line_total, Decimal(tax_rate_bps) / 100)` for the same 10 000 lines **including every exact half** (a test that imports finance's function only in the test, never in the service, because services share no code), `line_total <= line_gross`; and the invoice created from a quote through the real C64 path reports `quote_total_delta_minor == 0` for that set |
| State machine | every row of the table and every illegal transition 409; a rejected discount returns to `draft`; a `move_stage` to `won` with a pending quote 409; accepting moves the deal and cancels the expiry (the real scheduler consumer is run once in an integration test for A16 and A17); a `:revise`-then-`:send` arms the new row under its own key, and a cancel for a job that has already fired or been cancelled changes nothing (the key is single-use, EV28) |
| Approval abstraction | a CRM-only organization (no employee link, no HRMS) raises, routes to its manager, escalates to unassigned and is decided by a membership; a leave request in the same database still routes as before (the previous suite is green unchanged); a **previous-image check**: the HR approvals list and inbox query still serialize a membership-keyed row (surrogate employee id) without error. **Guard triggers** (`services/approval/tests/test_membership_guard_triggers.py`): after a decision an `UPDATE` of `decided_by_membership_id` is refused (`restrict_violation`), including inside the `application_*` write; an `UPDATE` of `requester_membership_id` on a pending request and on a decided one is refused; an `UPDATE` of `approver_membership_id` while closing an open step is refused; the employee twins still behave as before (the existing suites run unchanged); the downgrade restores the old function bodies before the columns go |
| Privacy | no event and no approval summary carries `deal_name`, `buyer_legal_name`, an e-mail or the reason text (recursive key scan on EV22, EV23, EV24 and the stored `summary`); `discount_reason` never appears in an audit `after`; the PDF route returns 302 with no URL in the body and an audit row |
| Handoffs | invoice created from a quote passes the IRP stub for b2b with a GSTIN; its totals equal the quote's or differ by the reported delta; the order lines carry item ids; free-text lines refuse the order handoff; two warehouses refuse; each callee refuses an unentitled organization |
| Migration | round-trip for deal, finance, operations, storefront, approval; `uq_quote_handoffs_org_quote_target`, `uq_invoices_org_source`, `uq_orders_org_source` refuse duplicates; approval downgrade refuses with a membership row; the insert-only trigger on `invoice_documents` |
| Infra | `tests/infra/test_active_profiles.py`, `test_ops_monitor_profile_containers.py`, `test_ops_monitor_dlq_queue_sync.py`, `test_compose_memory_budget.py`, `test_planned_headroom.py` for the approval move and, in R4.5-B, for the `api_with_worker_render` kind that prices only `deal` and `finance`; `test_party_link_env_wiring.py` rows W9, W28, W29, W32 to W34, W36, W38, W39; `tests/infra/test_route_table_parity.py` after the scaffold's route is deleted; `make licenses` for the PDF library; `tests/infra/test_crm_consumer_bindings.py`: for each key deal emits in this release (EV22, EV24, EV23, EV28) the consuming module that §10.5 names declares the binding (customer's handler registry, notification's `SUBSCRIBABLE_EVENTS`, approval's and deal's consumers), so a producer cannot be merged ahead of the consumers that the deploy order of §10.8 puts first; `tests/infra/test_internal_client_paths.py` (SP1) registers every internal client call this release adds, the pairs of W9, W28, W29, W32 and W33 (with R4.5-B), W34, W36 (finance and operations), W38 and W39, and the finance → payment checkout call of R4.5-C (W37), each in `CALLS` with a driver that returns the token it sent; `docs/crm.md` is edited in the commits that add them for EV22 to EV24 and EV28 (the event table) and for the permission rows of E25 and E26, which `test_crm_doc_matches_contracts.py` enforces |
| Expiry | `:accept` of a `sent` quote past `valid_until` is 409 `quote_expired` even when the expiry event never arrived (the handler dropped it); the UI chip reads "Expired" |
| Frontend | the composer (money and bps conversion, item lookup hidden without Operations), the status chip's legal actions, resend, handoff state, the inbox (loading, empty, error, forbidden, decide, reject needs a comment), HRMS after its migration, finance PDF and collection, the mapping screen; `pnpm typecheck && lint && test && build` for crm, dashboard, hrms, finance, storefront, operations; visual and a11y suites |

### 10.8 Rollout

**R4.5-A (owner-run, one named ref per deploy, consumers first):** (1) delete the scaffold in the same
reviewed change (ADR `crm-quotes-in-deal`, expected 0043, registry I2): `services/quotation/`, the
`opsgrids_quotation` role in `tools/dev-bootstrap/init-db.sql:102,130`, `tools/shard-weights.txt:46`,
`libs/contracts/openapi/quotation.json`, the dev route `services/app-gateway/routes.json:89`, README and
architecture rows, `docs/plan.md:38` (the PulseBoard row, which still lists `quotation`; the CRM phase is `plan.md:39`: registry I5, J33), **and file ADR 0042
`approvals-for-tenants-without-hrms` (§10.4) as `Status: proposed` in that same change**; (2) raise
`EDGE_BUDGET_ACCEPTED_MB` only if D-13 requires (no fold in this release: approval and deal already fold);
(3) **account** (C57's caller allow-list gains `approval`, before its first caller), then **approval**,
**only once the owner has accepted ADR 0042** (the approval deploy changes its schema,
profile and authorization code and narrows ADR-0006 Q1, so it is gated on the ADR; migration, guard-function
replacement, profile move, handlers, C66, membership branch; compose and budget edits and RUNBOOK literals in
the same commit); (4) **operations**, **finance**, **storefront** (migrations, C64 and C80 routes, mapping,
`ids`); (5) **customer** (the `crm.pipeline.quote.sent` and `.accepted` bindings and the `quote_sent`,
`quote_accepted` timeline kinds: the R4.3 handlers) and **notification** (the subscribable `quote.accepted`,
`.rejected`, `.expired` and `discount.decided` keys), **both before deal**, so that deal's EV22 and EV24 have
bound queues when its first quote is sent (§4.6 rule 1); (6) **deal** last of the backends (migration, quote
routes, settings column, consumers, EV23 producer; no request is ever raised until an organization sets a
threshold); (7) **permissions**: E25 and E26:
`make edge-reconcile PRODUCTS=crm` dry run, then `APPLY=1`, off-peak, paced; the non-admin templates pick
them up in a later reconcile after the dry run is read; (8) SPAs last: dashboard (inbox), crm, hrms,
finance, storefront, operations. **R4.5-B** (after R19 **in ADR-0009 Q1's shape**, which the owner confirms
first, §13 step 8, and after the PDF library's memory has been measured and priced into
`api_with_worker_render`, §10.4): `document`'s internal put and presign routes and their caller
allow-list (`deal`, `finance`), the PDF renderer, `invoice_documents`, then the SPAs. **R4.5-C**: payment
caller, reference, finance routes, the SPA hook. Compose lines: deal block `CUSTOMER_INTERNAL_URL`,
`OPERATIONS_INTERNAL_URL`, `FINANCE_INTERNAL_URL`, `PROJECT_INTERNAL_URL`, `PROJECTS_INTERNAL_URL`,
(`DOCUMENT_INTERNAL_URL` in B); approval block `DEAL_INTERNAL_URL` (the shared `ACCOUNT_SERVICE_URL`
already exists); finance block `DOCUMENT_INTERNAL_URL` (`PAYMENT_SERVICE_URL` arrived with R4.3, W37); storefront block
`OPERATIONS_INTERNAL_URL`, `LEAD_INTERNAL_URL`, `NOTIFICATION_INTERNAL_URL`, `CUSTOMER_INTERNAL_URL` (the
last three are R4.7's). **Flags:** `discount_approval_threshold_bps` NULL (no approval) and
`create_project_on_won` false by default. **Docs and pins (not a deploy step):** `docs/crm.md` (EV22 to EV24,
EV28, E25, E26) and the registrations in `tests/infra/test_internal_client_paths.py` land in the commits that
add the events, codes and clients (§10.7).

*Memory.* No new container and no fold. `containers.approval.profile` `hrms` → `null`: `python3
infrastructure/edge/lib/memory_budget_totals.py` over the edited `memory-budget.json` stays at the same
ten-profile total as before this release (3750 MB after R4.4: approval was already inside it) and the same
total with `admin`, and rises by 99 MB for a box that enables `crm` without `hrms` (for example a `crm`-only
box: 2067 MB measured today, 2166 MB after). **R4.5-B is different and is conditional on a measurement.** The
PDF library raises deal's and finance's resident set by an amount **not measured here** (inference: 10 to 20
MB once imported, against 157 MB of headroom under the 256 MB container limit); the PR records both
measurements, prices them in the new kind `api_with_worker_render` that only `deal` and `finance` use (§10.4),
and **R4.5-B does not deploy until the owner has raised `EDGE_BUDGET_ACCEPTED_MB` to the figure
`memory_budget_totals.py` prints with that kind applied**: with insights already running the ten-profile
total is then 3871 to 3891 MB, over the box's 3862 MB MemTotal, and with `admin` 4068 to 4088 MB (D-13,
§11.9). A preflight that refuses the post-deploy total is the stop condition.

### 10.9 Rollback

**R4.5-A:** the lever that needs no deploy is the organization setting: `discount_approval_threshold_bps =
NULL` (no new approvals) and withholding the SPA publish (no quote UI). Code rolls back to the previous
images; the schema stays. **Approval is the one service that is not freely reversible once used**: the
previous image tolerates membership-keyed rows (SD-13) but has no handler for EV23 and no inbox for a
membership approver, so a quote left in `pending_approval` is stuck until the new image returns; the stop
rule is **do not roll approval back while any `crm.pipeline.discount` request is `pending`; decide or
withdraw them first** (an ORG holder decides them in the inbox), and the same rule holds for **deal**, whose
handler applies the decision. Not reversible: invoices and orders created
by a handoff (an invoice is corrected by a credit note), a decided approval, a deleted scaffold (restore
from git). The profile move rolls back with a compose and budget revert. **R4.5-B and C:** disable the
routes by removing the SPA entries; the stored objects and rows stay. *Stop conditions:* any approval row
that the previous image fails to serialize; a quote that reaches `approved` without a decision when a
threshold is set; two orders, invoices or projects for one quote; a quote or approval event carrying a name,
an e-mail or the reason text; the HR approvals list erroring after the migration; `approval.events.dlq`
depth rising; **a rollback of approval, deal, customer or finance while its queue carries live traffic
without the catch-up of the table below ready**.

**What a code rollback loses (§4.7).** "Resend request" is the catch-up for both approval handlers: it
re-publishes EV23 with the same `source_ref`, which opens a request that was never opened and re-stages the
decision of one that is already decided (§10.4), and deal applies it only to a quote still `pending_approval`.

| Consumer (queue) | Events a rollback discards | Catch-up |
|---|---|---|
| `open_discount_request` (`approval.events`: EV23) | a discount request raised while the old image runs is never opened | **catch-up:** the rep's **Resend request** (after 15 minutes) once the image returns |
| `apply_discount_return` (`approval.events`: EV24) | the return-leg report of a decision applied while the old image runs | **forward-only:** `application_state` is bookkeeping on approval's side; the quote holds the outcome |
| `apply_discount_decision` (`deal.events`: `core.approval.request.decided`) | a decision made while deal runs the old image: the request is decided and the quote stays `pending_approval` | **catch-up:** **Resend request** re-stages the decision and deal applies it |
| `expire_quote` (`deal.events`: `quote.expiry_requested`) | an expiry that fires while the old image runs | **forward-only, made safe:** `:accept` refuses a past-date quote (409 `quote_expired`), so a missed expiry only leaves a stale status label |
| `erase_quote_copies` (`deal.events`: `crm.book.contact.erased`) | erasures while the old image runs | **catch-up:** H14 `--service deal` |
| customer timeline `quote_sent`, `quote_accepted`; the notification alerts for `quote.accepted`, `.rejected`, `.expired` and `discount.decided` | the line and the alert of each lost event | **forward-only:** "recent activity" and a bell alert; the quote and the deal hold the facts |
| finance capture handler for `finance.invoice` references (R4.5-C) | captures while the old image runs | **catch-up:** `reconcile_online_captures` over C105 (§8.4.4) |

### 10.10 Acceptance criteria

- [ ] `deal.quotes`, `deal.quote_lines`, `deal.quote_handoffs`, `finance.invoice_documents` (stage B), the `source_*` indexes, `storefront.products.inventory_item_id` and the three approval columns exist as specified; every migration round-trips; approval's downgrade refuses with data.
- [ ] A quote's totals satisfy `total = subtotal - discount + tax` for every stored row, are never accepted from a client, and round once per line with the rule each amount already has: quantity times price half-to-even, the line discount, `discount_bps` and the GST half-up exactly as finance's `gst_amount_minor`; the invoice created from a quote reports `quote_total_delta_minor` of zero on the property set.
- [ ] With no threshold, `:submit` approves; above a set threshold it requires a reason, raises exactly one approval request, and the deal cannot move to won until it is decided; a rejected discount returns the quote to `draft`, and after a revision and a resubmission opens a second request that is decided on its own.
- [ ] In an organization with no HRMS and no employee links, a member raises a discount request, their direct manager decides it in the dashboard inbox, and the quote and deal follow; with no active manager within three levels an ORG holder can decide it.
- [ ] The HR approvals suites pass unchanged and the previous-image serialization test passes with a membership-keyed row present; the two replaced guard functions refuse an `UPDATE` of `requester_membership_id`, `decided_by_membership_id` (also inside the `application_*` write) and `approver_membership_id`, and the downgrade restores the old bodies first.
- [ ] ADR 0042 `approvals-for-tenants-without-hrms` is filed `Status: proposed` in the R4.5 change, indexed in `docs/decisions.md`, linked from the open-questions register and from ADR-0006 Q1, and the owner has accepted it before the approval deploy runs.
- [ ] `customer.events` and `notification.events` carry the §10.5 bindings before deal's quote routes are live (the binding test is green and the deploy order of §10.8 puts both first); "Resend request" applies a decision that deal missed and opens a request that approval missed.
- [ ] No event, approval summary, audit `after` or log line holds `deal_name`, `buyer_legal_name`, an e-mail or `discount_reason`.
- [ ] `:accept` moves the deal to its first won stage and cancels the scheduled expiry; an expired quote cannot be sent or accepted; `:revise` creates a new version and `:send` supersedes the old one.
- [ ] Each handoff creates exactly one order, invoice or project per quote, refuses a rep below the ORG floor and an organization not entitled to the callee, and an invoice from a quote passes the IRP stub test.
- [ ] `services/quotation/` and every reference in §10.8 are gone, `tests/infra/test_route_table_parity.py` is green, and `docs/decisions.md` carries the new ADR.
- [ ] `approval` runs in the core profile; the ten-profile and `admin` totals from `memory_budget_totals.py` are unchanged; the infra profile tests are green.
- [ ] (R4.5-B) the owner has confirmed that R19 landed in ADR-0009 Q1's shape (internal put and `:presign`, `deal` and `finance` on the caller allow-list, no browser upload), not as the `sprint/private-uploads` contract; a PDF is a 302 to a 60-second URL with an audit row and no URL stored; the library passes `make licenses`; `deal` and `finance` are priced by `api_with_worker_render` with the measured delta, `kinds.api` and `kinds.api_with_worker` are unchanged, and the owner raised the accepted ceiling before the deploy; (R4.5-C) a staff member can collect an invoice online and the capture is applied exactly once.
- [ ] The storefront mapping routes, `GET /finance/invoices/{id}/payments` and both PDF routes each have their negative and cross-tenant tests; `:accept` refuses a past-date quote.
- [ ] Every changed or new screen has loading, empty, error and forbidden states at phone width, no new token-baseline count, a story per new `@opsgrids/ui` export (the three approval components), a fixture and `MockRoute` per new `GET`, and no new axe violation.
- [ ] Every internal client call this release adds (W9, W28, W29, W32, W33, W34, W36 for finance and operations, W38, W39 and the R4.5-C checkout call of W37) is registered in `tests/infra/test_internal_client_paths.py` with a driver that returns the token it sent; `docs/crm.md` carries EV22 to EV24, EV28 and the E25 and E26 permission rows and `test_crm_doc_matches_contracts.py` passes.
- [ ] A quote's expiry key is single-use: `:revise` arms its new row under its own key, and a cancel for a job that is no longer pending is a no-op.
- [ ] `make check` and `make gate` pass locally.

## 11. R4.6 Analytics and attribution

### 11.1 Purpose, needs and stages

Marketing's campaign figures are typed by hand and the form says so ("there is no ingestion path, so
hand-editing spend, revenue, pipeline, leads and conversions is the only way those numbers ever move":
`frontend/product-marketing/src/forms/CampaignFormDialog.tsx:40-43`; columns
`services/marketing/app/models.py:127-138`). The insights service of ADR-0020 exists only as four
unmerged lines of work (`sprint/insights`, `-q1`, `-q2`, `-q3`: S43), has no frontend, and its CRM
facts test the literal keys `closed_won` and `closed_lost` and store deal and company names. The cross-product
search hook has no caller (S42). R4.6 fixes those four things and records the design that makes
"everything about a company" a query rather than six calls.

**Needs.** R2.3 (`stage_outcome` on the deal events), R2.7 (membership subject), R3.1 (`marketing_campaign_id` and
`utm_*` on the lead, B32), R3.3 (the `CommandMenu` wiring and the CRM search sources, G12), R3.7 (capture
endpoints that carry a campaign id), R4.1 (the id fields on every CRM event). R4.5 for the quote facts only.
Decisions: D-12, D-13 (memory), D-16, D-36 and L10 (all pending).

SP4 introduces no container here: `insights` in R4.6-B is the container ADR-0020 decided
(`docs/adr/0020-insights-projection.md:67-103`) and `memory-budget.json` already prices as
`planned.insights-api`; R4.6-A and R4.7 only fold a consumer into an existing API process.

**Five stages, each its own deploy and its own rollback** (each can ship alone; C needs B):

| Stage | Content | Memory | Needs from the owner |
|---|---|---|---|
| **R4.6-A Attribution** | marketing consumes lead and deal events into attribution facts; campaign figures become CRM-attributed plus a typed adjustment; Marketing reads Engage delivery counts (D-16) | +1 MB (marketing fold, F16) | D-13 for the one MB; D-16; D-36 |
| **R4.6-B Insights adoption** | the four insights branches merge in a fixed order and adopt outcome, membership subject and the no-names rule; stage and quote facts; two metrics and one board | +99 MB (`insights`, core) | D-12, D-13 |
| **R4.6-C Dashboard tiles** | four CRM pipeline tiles and the customers tile on the Owner home, one summary call | none | needs B |
| **R4.6-D Search and palette** | cross-product sources in `useGlobalSearch`, the palette wired in all twelve apps | none | none |
| **R4.6-E Company facts v2** | a design only (§11.5): nothing is built | none | none |

### 11.2 Data model

**deal** (one revision on the head at merge, `# RE-PARENT HERE`; no new table, so no RLS change; the
columns are B30):

```sql
SET LOCAL lock_timeout = '5s';
ALTER TABLE deal.deals ADD COLUMN marketing_campaign_id UUID NULL;      -- a Marketing campaign id: plain UUID, no FK
ALTER TABLE deal.deals ADD COLUMN utm_source   VARCHAR(120) NULL;
ALTER TABLE deal.deals ADD COLUMN utm_medium   VARCHAR(120) NULL;
ALTER TABLE deal.deals ADD COLUMN utm_campaign VARCHAR(120) NULL;
ALTER TABLE deal.deals ADD COLUMN utm_term     VARCHAR(120) NULL;
ALTER TABLE deal.deals ADD COLUMN utm_content  VARCHAR(120) NULL;
CREATE INDEX ix_deals_org_marketing_campaign ON deal.deals (organization_id, marketing_campaign_id)
    WHERE marketing_campaign_id IS NOT NULL;
```

Downgrade drops the index and the columns and **refuses** while any `marketing_campaign_id IS NOT NULL` row
exists unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`. The lead columns are B32 (R3.1) and are not created
here.

**notification** (one revision on `f1c8a2d5b7e4`, B59): `ALTER TABLE notification.campaigns ADD COLUMN
marketing_campaign_id UUID NULL` and `CREATE INDEX ix_campaigns_org_marketing_campaign_id ON
notification.campaigns (organization_id, marketing_campaign_id) WHERE marketing_campaign_id IS NOT NULL`.
Several Engage campaigns may name one Marketing campaign. Engage edits a campaign only while it is a
`draft` (`update_campaign` refuses anything else: `services/notification/app/services.py:4878-4895`), so the
link is set at create or while the campaign is a draft and never changes on a sent campaign.

**marketing** (one revision on `96e4780b8df1`; `lock_timeout 5s`; named constraints). The three tables are
projections of CRM events (SD-16): state, never counters (ADR-0020 §2). Each gets `ENABLE` + `FORCE` row-level
security and the `tenant_isolation` policy through the revision's `_enable` helper (the shape of
`f6b63708a2e2_enable_row_level_security.py`), `opsgrids_marketing` grants through `apply_schema_grants`, and
joins `TENANT_TABLES` in `services/marketing/tests/test_cross_tenant_isolation.py`.

```sql
-- B69: one row per attributed lead (first touch: the campaign the lead was captured under)
CREATE TABLE marketing.attribution_leads (
    organization_id UUID NOT NULL,
    lead_id UUID NOT NULL,                              -- plain UUID into lead.leads
    campaign_id UUID NOT NULL,                          -- plain UUID; may name no live campaign (see unmatched)
    lead_created_at TIMESTAMPTZ NOT NULL,               -- the envelope occurred_at of crm.lead.created
    converted_at TIMESTAMPTZ NULL,
    deal_id UUID NULL,
    source_occurred_at TIMESTAMPTZ NOT NULL,            -- the version guard: applied only if strictly newer
    PRIMARY KEY (organization_id, lead_id)
);
CREATE INDEX ix_attribution_leads_org_campaign ON marketing.attribution_leads (organization_id, campaign_id);

-- B70: one row per attributed deal; money stays in the deal's own currency
CREATE TABLE marketing.attribution_deals (
    organization_id UUID NOT NULL,
    deal_id UUID NOT NULL,                              -- plain UUID into deal.deals
    campaign_id UUID NOT NULL,
    currency VARCHAR(3) NOT NULL,
    value_minor BIGINT NOT NULL,
    stage_outcome VARCHAR(4) NOT NULL,
    closed_at TIMESTAMPTZ NULL,
    deleted_at TIMESTAMPTZ NULL,
    source_occurred_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, deal_id),
    CONSTRAINT ck_attribution_deals_outcome CHECK (stage_outcome IN ('open', 'won', 'lost')),
    CONSTRAINT ck_attribution_deals_value CHECK (value_minor >= 0),
    CONSTRAINT ck_attribution_deals_currency CHECK (length(currency) = 3)
);
CREATE INDEX ix_attribution_deals_org_campaign ON marketing.attribution_deals
    (organization_id, campaign_id, currency) WHERE deleted_at IS NULL;

-- B53 (amended: the key gains the currency, `leads` is computed at read, see registry B53): the derived rollup
CREATE TABLE marketing.campaign_attribution (
    organization_id UUID NOT NULL,
    campaign_id UUID NOT NULL,
    currency VARCHAR(3) NOT NULL,
    open_deals INTEGER NOT NULL DEFAULT 0,
    conversions INTEGER NOT NULL DEFAULT 0,             -- won deals
    lost_deals INTEGER NOT NULL DEFAULT 0,
    pipeline_minor BIGINT NOT NULL DEFAULT 0,           -- sum of open deals' value
    revenue_minor BIGINT NOT NULL DEFAULT 0,            -- sum of won deals' value
    source_occurred_at TIMESTAMPTZ NOT NULL,
    as_of TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, campaign_id, currency),
    CONSTRAINT ck_campaign_attribution_nonneg CHECK (open_deals >= 0 AND conversions >= 0 AND lost_deals >= 0
        AND pipeline_minor >= 0 AND revenue_minor >= 0),
    CONSTRAINT ck_campaign_attribution_currency CHECK (length(currency) = 3)
);
```

`campaigns.leads`, `conversions`, `revenue_minor` and `pipeline_minor` keep their names, types and meaning on
the wire (`services/marketing/app/schemas.py:146-167` is a published contract): they become the **typed
adjustment** (SD-16). Downgrade drops the three tables; they are derived, but events are not replayable, so
the downgrade **refuses** while any row exists unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` (H12 can
rebuild them from the CRM exports).

**insights** (the merge adopts unshipped migrations; §11.4.2). One new revision after the merged chain
creates `stage_facts` and `quote_facts`. The two reshaped tables below are edited **in place** in
`ad6f07c940d8_deal_lead_company_facts.py` **only when the owner has recorded, in §13 step 10, that no shared
database carries the unmerged insights schema** (the branches are local worktrees and `ui/sprint-integration`
contains them, so "never run" is asserted and not evidenced, SD-22); **otherwise** (the default) they are
altered by a forward revision that does the same to the live tables: drop the two `name` columns and the stage
vocabulary CHECK, add `deal_number`, `crm_company_id` and `stage_outcome` (backfilled from the stored stage key,
`closed_won` to `won`, `closed_lost` to `lost`, anything else `open`), and make `owner_ref` nullable. Both paths
ship the same end state and are proved by the same test (§11.8):

```sql
-- B72: deal_facts and company_facts lose their names and gain the outcome and the membership owner
-- deal_facts:    DROP name; ADD deal_number INTEGER NULL, stage_outcome VARCHAR(4) NOT NULL, crm_company_id UUID NULL;
--                stage VARCHAR(20) NOT NULL stays but its vocabulary CHECK (deal_facts_stage_vocabulary) is NOT created;
--                owner_ref becomes NULLable (owner_membership_id is null until mapped);
--                CHECK ck_deal_facts_stage_outcome (stage_outcome IN ('open', 'won', 'lost'));
--                ix_deal_facts_org_open becomes ON (organization_id) WHERE deleted_at IS NULL AND stage_outcome = 'open'
-- company_facts: DROP name; owner_ref NULLable; status stays the frozen B17 vocabulary

-- B73
CREATE TABLE insights.stage_facts (
    organization_id UUID NOT NULL, id UUID NOT NULL,                -- id = the stage id
    source_created_at TIMESTAMPTZ NOT NULL, source_updated_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ NULL, last_seen_run UUID NULL, ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    pipeline_ref UUID NOT NULL, stage_key VARCHAR(20) NOT NULL, label VARCHAR(80) NOT NULL,
    outcome VARCHAR(4) NOT NULL, position INTEGER NOT NULL, archived BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (organization_id, id),
    CONSTRAINT ck_stage_facts_outcome CHECK (outcome IN ('open', 'won', 'lost'))
);
CREATE INDEX ix_stage_facts_org_position ON insights.stage_facts (organization_id, pipeline_ref, position)
    WHERE deleted_at IS NULL AND archived = false;

-- B74
CREATE TABLE insights.quote_facts (
    organization_id UUID NOT NULL, id UUID NOT NULL,                -- id = the quote id
    source_created_at TIMESTAMPTZ NOT NULL, source_updated_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ NULL, last_seen_run UUID NULL, ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deal_ref UUID NOT NULL, quote_number VARCHAR(40) NOT NULL, version INTEGER NOT NULL,
    status VARCHAR(16) NOT NULL, currency VARCHAR(3) NOT NULL, total_minor BIGINT NOT NULL,
    owner_ref UUID NULL, company_ref UUID NULL, sent_at TIMESTAMPTZ NULL, accepted_at TIMESTAMPTZ NULL,
    PRIMARY KEY (organization_id, id),
    CONSTRAINT ck_quote_facts_total CHECK (total_minor >= 0),
    CONSTRAINT ck_quote_facts_currency CHECK (currency ~ '^[A-Z]{3}$')
);
CREATE INDEX ix_quote_facts_org_sent ON insights.quote_facts (organization_id, sent_at) WHERE sent_at IS NOT NULL;
CREATE INDEX ix_quote_facts_org_accepted ON insights.quote_facts (organization_id, accepted_at) WHERE accepted_at IS NOT NULL;
CREATE INDEX ix_quote_facts_org_owner ON insights.quote_facts (organization_id, owner_ref);
```

Both new tables join the insights RLS migration's `TENANT_TABLES` (`ENABLE` + `FORCE` + `tenant_isolation`)
and its `test_rls.py`. `quote_facts` holds a quote **number** and ids and nothing a person typed.

### 11.3 Backend contract

**marketing, public** (`/api/v1/marketing`, product `marketing`, `Entitled`; every route re-reads through the
organization filter; the permission and ORG floor are those of `services/marketing/app/api/routes.py:75-80`)

| Route | Permission, floor | Request → response | Errors |
|---|---|---|---|
| `GET /marketing/campaigns`, `GET /marketing/campaigns/{campaign_id}`, `POST`, `PATCH` | `marketing.analytics.campaign.read` / `.write`, ORG | `CampaignOut` keeps every existing field and gains `attribution {leads, as_of, by_currency: [{currency, open_deals, conversions, lost_deals, pipeline_minor, revenue_minor}]}` and `effective {leads, conversions, pipeline_minor, revenue_minor}`; `CampaignIn` and `CampaignPatch` are unchanged | as today |
| `GET /marketing/overview`, `GET /marketing/channels` | read, ORG | the rollups sum `effective` (§11.4.1); keys unchanged | as today |
| `GET /marketing/campaigns/{campaign_id}/delivery` (**SP4 addition**, C96) | read, ORG | `{campaigns: [{campaign_id, name, status, recipients, delivered, opened, clicked, unsubscribed, bounced, complaints}], unavailable: bool}`: calls C85 with the campaign id, 2 s budget, outside any transaction; counts only | 404 foreign or missing campaign; 200 with `unavailable: true` when notification is down; 200 with `campaigns: []` when Engage is not active |
| `GET /marketing/attribution/unmatched` (**SP4 addition**, C97) | read, ORG | `{leads: int, deals: int, campaign_ids: uuid[] (at most 20)}`: attributed rows whose campaign id names no live campaign of the organization | |

**Internal.**

| Handle | Route | Caller, allow-list | Rows |
|---|---|---|---|
| C98 (**SP4 addition**) | lead `GET .../leads:export-attribution?after_updated_at=&after_id=&limit=` | `marketing`; org claim required | keyset `(updated_at, id)`, `limit` ≤ 500; `{lead_id, marketing_campaign_id, created_at, converted_at, converted_deal_id, updated_at}` for rows with a campaign id; no name, e-mail or phone key |
| C99 (**SP4 addition**) | deal `GET .../deals:export-attribution` | `marketing`; org claim required | `{deal_id, marketing_campaign_id, currency, value_minor, stage_outcome, closed_at, deleted_at, updated_at}`, soft-deleted rows included; no name |
| C85 | notification `GET .../campaigns:summarize?marketing_campaign_id=` | `marketing` (registry C85) | as the registry |

Each internal route ships the three negative tests (no token 401, wrong caller 403, wrong organization claim
403) and a cross-tenant test.

**CRM public changes.** `DealIn`, `DealPatch` and `DealOut` gain `marketing_campaign_id` and `utm_*` (the columns are B30, and C7
registers the `DealOut` fields); `LeadIn`, `LeadPatch` and `LeadOut` already carry them (B32). `GET /deals` and `GET /leads` accept
`marketing_campaign_id=<uuid>` (**SP4 addition** to the list vocabulary C35; the permission and scope are the
list's own). **Write-once rule** (SD-21): a value may be set when the stored one is `NULL` and never changed
or cleared afterwards, on a lead or a deal; the refusal is **409 `attribution_locked`** and writes nothing
(the UTM columns follow the same rule as one group). `LeadConversionDealIn` gains `marketing_campaign_id?`
and `utm_*?` (C54) and lead's `convert_lead` passes its own. Engage's `CampaignIn` and `CampaignPatch`
(`services/notification/app/schemas.py:697, 742`) gain optional `marketing_campaign_id` (shape-validated, never
by a call to marketing) and `CampaignOut` returns it.

**Events** (additive within `v1`; schema and producer test in the same commit): `marketing_campaign_id`
(`uuid | null`, always present) joins `crm.pipeline.deal.created` (EV6), `.updated` (EV7, **SP4 addition**),
`.stage_changed` (EV8), `crm.lead.created` (EV10), `.updated` (EV11, **SP4 addition**) and `.converted` (EV12).
The `utm_*` values are **not** on any event: they are free text of a visitor's choosing, which the §D rule
keeps off the broker.

### 11.4 Domain rules

#### 11.4.1 Attribution (R4.6-A)

1. **First touch, write-once (SD-21; pending D-36).** A lead carries the campaign it was captured under; the
   deal that conversion creates copies it; a deal created directly may be given one at creation. After it is
   set it never changes (409 `attribution_locked`). A person captured twice under two campaigns is one lead
   (blind-index dedupe) and keeps the first campaign.
2. **No retroactive attribution.** Leads and deals created before a campaign link existed stay unattributed.
   H12 (below) catches up only rows that already carry a campaign id and whose event preceded the queue.
3. **Per-event fold.** One handler per event type, raw `event_id` marker (marketing has no second handler for
   any of them). Every handler binds the tenant from the envelope, cross-checks the payload organization,
   and applies only if the envelope `occurred_at` is **strictly newer** than the row's `source_occurred_at`.
   `crm.lead.created`/`.updated`: no campaign id in the payload and no row → no-op; campaign id and no row →
   insert; a row exists → keep its campaign (a later payload cannot change it). `crm.lead.converted`: set
   `converted_at` and `deal_id` on the row when it exists (an event for an unattributed lead is a no-op).
   `crm.pipeline.deal.created`/`.updated`/`.stage_changed`: upsert `value_minor`, `currency`, `stage_outcome`
   (`stage_outcome` from created and updated, `to_outcome` from stage_changed), `closed_at`.
   `crm.pipeline.deal.deleted`: set `deleted_at`.
4. **Rollup recompute.** After a deal upsert the handler recomputes `campaign_attribution` for the touched
   `(campaign, currency)` and, when the currency changed, for the old one, in the same transaction, with one
   aggregate `INSERT … ON CONFLICT DO UPDATE` over `attribution_deals` where `deleted_at IS NULL`
   (`open_deals`, `conversions`, `lost_deals` by outcome; `pipeline_minor` and `revenue_minor` as sums of
   open and won values). Never a delta. **The transaction takes a per-campaign advisory lock first**
   (`pg_advisory_xact_lock(hashtextextended('marketing.campaign:' || organization_id || ':' || campaign_id, 0))`,
   before the fact upsert, the same rule as SD-5): the handler, the H12 backfill (which writes through the same
   fold functions) and the second consumer that a rollback to `split-workers` briefly runs would otherwise each
   aggregate under READ COMMITTED without the other's uncommitted fact and let the later commit overwrite with
   a partial total, and no sweep recomputes attribution to repair it. H12 takes the same lock per campaign in
   its own short transaction. A currency change, which moves value between two rows of one campaign, needs
   only that one lock.
5. **Effective figures.** `effective.leads = campaigns.leads + attribution.leads` (a live count of
   `attribution_leads` for the campaign); `effective.conversions = campaigns.conversions + Σ conversions over
   every currency` (a count has no currency); `effective.revenue_minor = campaigns.revenue_minor +
   revenue_minor of the row whose currency equals the campaign's`, and likewise `pipeline_minor`. Money in any
   other currency is reported in `by_currency` and is **never added** to a total or converted. ROAS, cost per
   lead and CAC are computed by `app.domain` from the effective figures as they are today
   (`services/marketing/app/domain.py:48-66`).
6. **Unmatched.** A campaign id that names no live campaign of the organization (a typo on a public form, a
   deleted campaign) keeps its rows and is counted by C97; nothing is attributed to another campaign.
7. **What does not change.** Spend, budget and the monthly revenue series stay typed; the weekly performance
   chart is a fixed shape scaled to the totals (`services/marketing/app/domain.py:26-39`) and is labelled as
   such in the UI. The Overview's one-currency assumption (`services/marketing/app/schemas.py:78-87`) is
   unchanged and recorded as a risk.
8. **Delivery (D-16).** Marketing never touches Engage's tables: `delivery` is C85 and nothing else.

#### 11.4.2 Insights adoption (R4.6-B)

The merge is five pull requests, in this order, each green on its own and each ending at **one Alembic head
for `insights`**:

1. `sprint/insights` (ADR-0020 slice I1: scaffold, tenancy, the API shell, the edge wiring commit `35e0ecd3`).
2. `sprint/insights-q1` (`d543211a`: finance facts, migration `048dbce2f957`).
3. `sprint/insights-q2` (`a4b248f1`: deal, lead and company facts, migration `ad6f07c940d8`) with the
   adoption below.
4. `sprint/insights-q3` (`93265864`: operations, projects, storefront facts, migration `a7c4e1f9b3d2`).
5. R4.6's own revision: `stage_facts`, `quote_facts`, the new metrics and board.

The three branch migrations each have `down_revision = '5cead06f3d54'` (verified on each branch), so after the
merges there would be three heads; each later revision is re-parented onto the previous one at its
`# RE-PARENT HERE` marker (`048dbce2f957` → `ad6f07c940d8` → `a7c4e1f9b3d2` → the R4.6 revision).
`tests/infra/test_migration_revisions.py` proves the single head.

**Hunk rule for files the branches share with the CRM releases.** `sprint/insights-q2` changed deal,
customer and lead producers (`services/deal/app/services.py`, `models.py`, `schemas.py`; the three event
schemas; migration `a1c9e2f7b3d4`). R2.3 adopts `a1c9e2f7b3d4` unchanged (registry J1) and R2.3, R3.7 and R4.1
re-implement the event payloads with the outcome and id fields. At the merge, **main's version of every such
file wins** and only q2's additions that main lacks are re-applied on top: the three internal export routes
(`services/deal/app/api/internal.py`, `services/customer/app/api/internal.py` and a new
`services/lead/app/api/internal.py`, whose router is **merged into** the one R3.7 creates for C56: one file,
the export route allowed to caller `insights` and the capture route to `notification` and `storefront`) and
their `test_insights_export.py` tests. **Those three export routes are re-cut in the merge, not re-applied
unchanged (S55).** q2's `DealExportRowOut` returns `name` (a lead-converted deal's name embeds a person's name,
S13, which SD-22 says insights never stores) and types `stage` as the six-value `DealStage` literal, so a
custom stage fails to serialize; `CompanyExportRowOut` returns `name`; `LeadExportRowOut` types `source` as the
three-value `LeadSource`, which R3.7's new sources break. The merged row models drop `name` (a deal's label is
`deal_number`), carry `stage` as a key string beside `stage_outcome` (`open`, `won`, `lost`) and
`crm_company_id`, carry the owner as the nullable `owner_membership_id` in place of the roster id, and type
`source` as a string, so each export has the shape of its fact table (B72). Each has a name-free test: a
recursive key scan of a response finds no `name`, `full_name`, `email` or `phone` key, and a deal in a custom
stage and a `whatsapp` lead both serialize. q3's storefront hunk (`status` and `lines` on `.created`, the new
`.status_changed` emission, `services/storefront/app/services.py:1904-1912, 1942-1960` on that branch) is
**exactly** the producer change registry EV37 assigns to R4.7: whichever of R4.6-B or R4.7 ships first
carries it, the other finds it already applied.

**Adoption changes (all in PR 3 unless stated):**

| Change | Rule | Evidence on the branch |
|---|---|---|
| Outcome, not keys (L10) | `DEAL_STAGES`, `DEAL_CLOSED_STAGES`, the stage CHECK and every predicate on `'closed_won'` or `'closed_lost'` become `stage_outcome` predicates: `deals_won` is `stage_outcome = 'won'`, `deals_lost` `'lost'`, the open pipeline `'open'`. The fold reads `stage_outcome` (created, updated) and `to_outcome` (stage_changed); a payload without it **raises** (dead-letter: `docs/insights/events.md:24-27`), which is why R2.3 must have shipped | `a4b248f1:services/insights/app/models.py:164-169, 210`; `selectors.py:275, 290, 317, 368`; `snapshot.py:82, 101, 135`; migration `ad6f07c940d8:87, 93` |
| Membership subject for CRM OWN | for a metric whose product is `crm`, `Gate.subject_ref` is `principal.membership_id` (A10, from the `mem` claim) and the fact's `owner_ref` is the event's `owner_membership_id`, **nullable** (a fact whose owner is not yet mapped is visible at ORG only, as the CRM lists treat it). Other products keep `principal.subject_ref` (A7). A test pins both | `services/insights/app/access.py` (q3), `Gate.subject_ref` |
| No names (SD-22) | `deal_facts.name` and `company_facts.name` are not stored; the deal's label is `deal_number` (EV6 `deal_number`, R3.1) and the SPA resolves a company or contact name through the viewer's own CRM read (`ids=`, C35). ADR-0020's non-goals say "Person names, contact details or free text in the insights schema" (`docs/adr/0020-insights-projection.md:682`) and a lead-converted deal's name embeds a person's name (S13), which no erase could reach | `a4b248f1:services/insights/app/models.py` `DealFact.name`, `CompanyFact.name`; `docs/insights/metrics.md:173-176` |
| Lead source values | `lead_facts.source` accepts the B33 values (`webhook`, `whatsapp`, `storefront`); the `breakdown=source` series lists whatever values exist, never a fixed set | `docs/insights/api.md:155` |
| Settings and wiring | `lead_service_url` defaults to `http://localhost:8135` (the q2 default `:8110` is the auth service: `services/app-gateway/routes.json:52-53, 81`); the compose `insights` block gains `DEAL_SERVICE_URL`, `LEAD_SERVICE_URL`, `CUSTOMER_SERVICE_URL`, `FINANCE_SERVICE_URL`, `OPERATIONS_SERVICE_URL`, `PROJECTS_SERVICE_URL` and `STOREFRONT_SERVICE_URL` as `http://<service>:8000` (the branch's compose block sets only `ACCOUNT_SERVICE_URL`, so on the box every backfill would dial `localhost`); W44 to W51 name each pair | `a4b248f1:services/insights/app/config.py`; the slice I1 compose diff |
| Registration (PR 1) | `insights` joins `SERVICES_WITH_SCHEMA` (`infrastructure/edge/remote.sh`), both `EDGE_RELAY_SCHEMAS` defaults, ops-monitor `CORE_API_CONTAINERS` and `CORE_DLQ_QUEUES` (`insights.events`), the gateway route tables (no `product` key) and `libs/contracts/openapi/insights.json` (regenerated with `make openapi`) | commit `35e0ecd3` file list |
| Budget (PR 1) | `containers.insights` (`api_with_worker`, core) and `containers.insights-worker` (`split-workers`) replace `planned.insights-api`; every RUNBOOK ten-profile literal is recomputed to the **running total at merge**, not the branch's 4043 | `35e0ecd3:infrastructure/edge/memory-budget.json`; F8 |
| Docs | `docs/insights/metrics.md` §4.2 and §6, `events.md` §2 and `data-model.md` §3 change in the same commits as the code (the file's own rule: "A definition that changes is a change to this file in the same commit as the code"); ADR-0020 gets an "Amended" note recording outcome, the membership subject, the no-names rule and D-13 | `docs/insights/metrics.md:3-6` |

**New in PR 5.** Facts and folds: `stage_facts` from `crm.pipeline.stage.created`, `.updated`, `.reordered`
(EV14: `.reordered` carries `stage_ids[]`, so positions are set from its order), `.archived`; `quote_facts` from
`crm.pipeline.quote.created`, `.sent`, `.accepted`, `.rejected`, `.expired` (EV22; `sent_at` and `accepted_at` are
the envelope `occurred_at` of `.sent` and `.accepted`). Gate catalogue (`app/access.py`):

| Key | Kind · unit | Definition | Gate |
|---|---|---|---|
| `quotes_sent` | flow · count | count of `quote_facts` with `sent_at` in the window (each version is its own send) | `crm.pipeline.quote.read`, OWN-capable on `owner_ref` |
| `quotes_accepted_value` | flow · money | Σ `total_minor` of `quote_facts` with `accepted_at` in the window, per currency | same |
| board `pipeline_by_stage` | rows | for each non-archived `open` stage of the organization's pipeline in `position` order (at most 12): `stage_key`, `label`, `open_deals` and the open value (organization-currency group plus `other_currencies`, never summed) from `deal_facts` joined to `stage_facts` on `stage_key`; response carries `truncated` | `crm.pipeline.deal.read`, OWN-capable (own deals) |

The quote metrics' `history_from` is `NULL` from the first quote event (source dates); the board is a live
read, not a snapshot.

#### 11.4.3 Tiles (R4.6-C)

The Owner home gains one section "Sales pipeline" with four slots from a **single** summary call
(`GET /api/v1/insights/summary?metrics=pipeline_value,deals_won_value,win_rate,leads_created&period=this_month`)
and the existing "Total Customers" slot reads `customers` from the same envelope. A slot resolves in this order:
the caller's entitlement (`resolveAccess`, `frontend/opsgrids-dashboard/src/ui/home/access.ts:13-17`), then the
metric's own `state` (`ok` renders; `no_access` and `not_active` render the page's existing copy;
`insufficient_history` renders the value without a delta; `unavailable` renders an inline retry). **A card
never mixes sources**: while `/insights` answers 404 or 503, the "Total Customers" slot keeps today's
`countCrmCustomers` call and the pipeline slots are not drawn (no fallback figure is invented). Money slots show
the organization-currency group and, when `other_currencies` is non-empty, the caption "Also in other
currencies" with no sum.

#### 11.4.4 Search and palette (R4.6-D)

`SEARCH_SOURCES` (`frontend/opsgrids-client/src/platform/useGlobalSearch.ts:184-295`) gains the sources below
and one relabel. A source runs only when `usePlatformModules()` says the product is entitled and granted
(`:341-342`); results are names and numbers and never an e-mail or phone in a URL (SD-19). The palette change
is the one R3.3 ships (`AppShell`'s `search` config accepting result groups); all twelve apps already pass a
`search` config (verified), so each adopts through one shared hook, `useShellSearch()` (**SP4 addition**,
G23), and a one-line change.

| Product | Group | Path | Row → result |
|---|---|---|---|
| operations | Orders | `GET /operations/orders?q=` (R4.6-D adds `q`: order number prefix or buyer name, never the phone; `services/operations/app/api/routes.py:1750-1757` has no `q` today) | number · status → `/orders/{id}` |
| pulseboard | Projects (PulseBoard) | `GET /projects?q=` | name · client → the project |
| marketing | Campaigns | `GET /marketing/campaigns?q=` | name · channel · status → the campaign |
| storefront | Enquiries | `GET /storefront/enquiries?q=` | company · status → the enquiry |
| finance | **Billed parties** (relabel of "Finance customers", S42 copy rule) | existing | with `crm_company_id` present (R4.3), the result opens the CRM company when the caller is entitled to `crm`, else the billed party |

ITSM registers no source (its requesters are staff, §5.4); a test pins that no `itsm` source exists.

### 11.5 Insights v2: cross-product facts keyed by company (R4.6-E, design only)

*Why a design now.* R4.1, R4.3 and R4.4 put `crm_company_id` on the finance, operations and support events
(EV33 to EV35), so a projection keyed by company needs **no producer change** after R4.5. The v2 is therefore
a consumer-side release, and its only gates are D-13 (memory) and one more owner decision (below).

*Facts (insights schema, same `FactMixin`, the same version guard, the same rule that nothing a person typed is
stored):*

| Fact table | Source events | Adds | Keyed by |
|---|---|---|---|
| `invoice_facts` | `finance.billing.invoice.issued`, `.paid` | `crm_company_id UUID NULL`, `crm_contact_id UUID NULL` columns | `(organization_id, id)` as today |
| `payment_facts` | `finance.billing.payment.received` | `crm_company_id`, `crm_contact_id` | as today |
| `order_facts` (new) | `operations.orders.order.placed`, `.accepted`, `.cancelled`, `.handed_over` (EV34) | `id`, `number`, `status`, `currency`, `total_minor`, `crm_company_id`, `placed_at`, `handed_over_at` | `(organization_id, id)` |
| `ticket_facts` (new) | `support.desk.ticket.created`, `.status_changed` (EV35) | `id`, `number`, `status`, `crm_company_id`, `opened_at`, `solved_at`, `sla_breached` | `(organization_id, id)` |

*Questions it answers* (each a new metric or board under the existing gate catalogue, never summed across
currencies): revenue and receivables by company (`finance.reports.summary.read`, ORG), orders by company
(`operations.orders.order.read`, ORG), open tickets by company (`support.desk.ticket.read`, ORG), and "companies
with an open deal, an overdue invoice and an open ticket" as a board that returns **ids** only. The gate of a
metric is the **strictest source gate it touches**; no CRM scope widens it, and a rep without the finance grant
sees none of it in insights either (the live-as-viewer tier of §4.4 still serves the company page).

*Open decision for the owner (new, D-32 in §2.1 and registry §K):* whether v2 is wanted at all once the projection tier of the
company page (R4.3) exists, because the two answer overlapping questions from two stores (the same "two
definitions of one number" risk ADR-0020 names, risk 2). *Default: do not build v2 until a dashboard asks for a
company-ranked board that the party projection cannot serve.* The design above is kept so the decision costs a
document, not a producer change.

### 11.6 Consumers and idempotency

| Queue | Binds | Handler and marker | Notes |
|---|---|---|---|
| `marketing.events` (marketing, **first handlers**, fold F16) | `crm.lead.created`, `.updated`, `.converted`; `crm.pipeline.deal.created`, `.updated`, `.stage_changed`, `.deleted` | `fold_lead_attribution`, `fold_deal_attribution`; raw `event_id` | EV51 plus three keys (**SP4 addition**, registry EV51): `.updated` carries the value and outcome after an edit and `.deleted` the removal; without them a re-valued or deleted deal leaves attributed pipeline wrong until the next stage move |
| `insights.events` (insights, **already folded**) | the registry's list (EV1 to EV3, EV6 to EV12, EV14, EV22) plus the finance, operations, project and storefront keys of the branches | the branch handlers, with the §11.4.2 changes | one handler per event; version guard `source_updated_at <= occurred_at` |
| `notification.events` | (unchanged) | none | the Engage `marketing_campaign_id` link adds no consumer |

Redelivery and reordering are harmless by construction: every write is an upsert guarded by a strictly newer
`source_occurred_at`, and the rollup is recomputed from facts, not incremented. An off-contract payload (a
`stage_outcome` outside the three values, a missing id) raises and dead-letters to `marketing.events.dlq` or
`insights.events.dlq`, both watched by ops-monitor.

### 11.7 UI

- **Marketing** (`product-marketing`, local components; nothing forked): the campaign detail gains an
  **Attribution** panel (leads from CRM, deals by outcome, pipeline and revenue **per currency**, "as of
  {time}", a "View in CRM" link to the CRM host with `marketing_campaign_id`) and a **Delivery** panel (the
  Engage counts; states: loading, "Engage is not active", "Delivery counts are unavailable", counts). The
  campaign form relabels its four typed fields "Manual adjustment: {leads, deals, revenue, pipeline}" with the
  help text "Added to what CRM attributes. Leave at 0 unless you are recording results CRM cannot see." The
  Overview shows an "Unmatched campaign links: N" banner when C97 is non-zero. Every panel has loading, empty,
  error and forbidden states and a phone-width layout.
- **CRM**: lead and deal forms and the capture-endpoint settings (R3.7) gain a **Campaign** picker through
  `useCampaignLookup(query | ids)` (**SP4 addition**, G21, `@opsgrids/client`: debounced
  `GET /marketing/campaigns?q=`), **hidden** without `useEntitlement('marketing')` or the marketing read grant
  and read-only once set (the write-once rule is explained in the control's help text). UTM values show on the
  record; they never leave CRM.
- **Dashboard**: the section of §11.4.3 through `useInsightsSummary(metrics, options)` (**SP4 addition**, G22,
  `@opsgrids/client`, typed envelope, `apiRequest` on `/insights/summary`) and the existing `KpiSlot`; the
  "Pipeline by stage" card uses the board. Fixtures for `/insights/summary` and the board are added to
  `frontend/opsgrids-visual/fixtures/api/insights/` with a `MockRoute` each.
- **Search**: the palette groups of §11.4.4 in every app, each result a labelled link; keyboard behaviour and
  `aria-activedescendant` are the existing palette's.
- **Every changed or new screen** has loading, empty, error and forbidden states at phone width (the
  harness's 375 px, no horizontal scroll at 360 px); no raw colour or size value (`pnpm tokens:baseline`
  without `--allow-increase` writes nothing); a story for every new `@opsgrids/ui` export and a fixture and
  `MockRoute` in `frontend/opsgrids-visual/fixtures/api/` for every new `GET`; refreshed baselines and no new
  axe violation (`make ui-visual`).

### 11.8 Tests

| Area | Tests that will exist |
|---|---|
| Negative authz | `services/marketing/tests/test_attribution_authz.py`: C96 and C97 with no `marketing.analytics.campaign.read` (403), an OWN grant (403, the ORG floor), another organization's campaign id (404), an unentitled organization (402); C98 and C99, the three negatives each (`services/lead/tests/test_attribution_export_authz.py`, `services/deal/tests/test_attribution_export_authz.py`); C85 the three negatives; `GET /operations/orders?q=` is refused without `operations.orders.order.read` and matches a number prefix and a buyer name, never a phone number (`services/operations/tests/test_orders_search.py`); the insights metrics `quotes_sent`, `quotes_accepted_value` and the board answer `no_access` (a 200 inside the envelope) to a caller without `crm.pipeline.quote.read` or `crm.pipeline.deal.read` and to an OWN caller for an ORG-only key |
| Cross-tenant | marketing's three tables join `test_cross_tenant_isolation.py` (a row of organization B is invisible and unwritable from A); an event of B under A's claim is refused; C96 answers only A's Engage campaigns; `insights` `test_rls.py` gains `stage_facts` and `quote_facts`; an OWN CRM caller of A never matches B's `owner_ref` |
| Idempotency and redelivery | `services/marketing/tests/test_attribution_consumers.py`: every marketing handler delivered twice and out of order (a stale `stage_changed` after a newer `updated`) leaves one row and the newer state; a `deleted` before its `created` creates nothing; the rollup equals a full recompute after a random sequence of 500 events (property test); a deal that changes currency moves its value between the two rollup rows; insights handlers likewise. **Concurrency:** two **real concurrent connections** each applying one deal event of the same campaign, and H12 writing beside a live handler, always leave `campaign_attribution` equal to a full recompute with the per-campaign lock released in either order, and the test fails when the lock is removed (a mutation check) |
| Attribution rules | write-once on lead and deal (set from `NULL`, change, clear: 200, 409, 409), copy at conversion, a second capture of the same person keeps the first campaign, effective figures with a second-currency won deal (counts added, money not), unmatched count, the typed adjustment still accepted by `CampaignPatch` |
| Insights adoption | `test_outcome_adoption.py` (a custom stage whose outcome is `won` counts as won; the literals do not appear in any module: a source scan), `test_membership_subject.py` (a CRM OWN caller matches by `membership_id`, an HR OWN caller by `subject_ref`, an unmapped owner is ORG-only), `test_no_names.py` (an introspection of every insights column against a deny-list of name-like columns and a recursive scan of stored rows from a fixture containing a lead-converted deal name), the suites of ADR-0020 slices I1 to I7 rerun, the parity tests of metrics.md §7 rerun with `stage_outcome`; the three re-cut export routes (a name-free recursive key scan, a custom stage and a `whatsapp` lead both serialize) |
| Migration | round-trip for deal, notification, marketing and insights (`make migration-roundtrip`); marketing and deal downgrades refuse with rows; one insights head (`tests/infra/test_migration_revisions.py`); `alembic upgrade head` on an **empty** database runs the merged insights chain; **both adoption paths reach one end state:** a database stamped at the unedited q2 head `ad6f07c940d8`, with a `deal_facts` row that holds a name and a stage key, upgrades through the forward revision (the stage CHECK and both `name` columns gone, `stage_outcome` backfilled, `owner_ref` nullable) to a schema whose comparison with the in-place chain is empty; the in-place variant is built and merged only when §13 step 10 records an absent schema everywhere |
| Contract | the six events carry `marketing_campaign_id` through the real write paths and `validate_event_schema`; a recursive key scan finds no `utm_*` key on any event; `make contracts-check`, `make openapi-check` (marketing, deal, lead, notification, insights) |
| Frontend | `useGlobalSearch.test.ts` (each new source runs only when entitled and granted; no `itsm` source; ids and names only), `useCampaignLookup` and `useInsightsSummary` tests, the dashboard `Home.test.tsx` states for every slot (ok, delta, `no_access`, `not_active`, `insufficient_history`, `unavailable`, `/insights` 503 fallback), marketing `CampaignDetail.test.tsx` for both panels, the CRM picker hidden and read-only cases; `pnpm typecheck && lint && test && build` for dashboard, crm, marketing and every app that adopts `useShellSearch`; stories for new `ui` exports; `make ui-visual` baselines |
| Infra | `test_party_link_env_wiring.py` rows W35, W41, W42 and W44 to W51; `test_ops_monitor_dlq_queue_sync.py`, `test_ops_monitor_profile_containers.py`, `test_compose_memory_budget.py`, `test_planned_headroom.py` (marketing fold and insights); `tests/infra/test_internal_client_paths.py` (SP1) registers every internal client call this release adds, in marketing (W35, W41, W42, R4.6-A) and in insights (W44 to W51, R4.6-B, wherever the client lives), each in `CALLS` with a driver that returns the token it sent; `make boundaries` |

### 11.9 Rollout

**R4.6-A (owner-run, one named ref per deploy, consumer first):** (1) **marketing** (migration; fold: `run_consumers_in_api`,
lifespan hooks on the `customer` template, `containers.marketing.kind` → `api_with_worker`, a `marketing-worker`
rollback container, `PROFILE_DLQ_QUEUES["marketing"]`, RUNBOOK literals, all in one commit); (2) **notification**
(migration, C85 and the campaign bodies); (3) **lead** and **deal** (migration, write-once rule, filters,
export routes C98 and C99, the event field); (4) owner-run **H12** `tools/backfill_campaign_attribution.py`
(`--service marketing`, runs inside the marketing container): `plan` is read-only and the default, `apply` is
gated like H1, it pages C98 and C99 and writes through the same fold functions, so it is idempotent and safe
beside live events; (5) SPAs last (`make edge-web APP=marketing`, `APP=crm`). Compose lines: marketing block
`LEAD_INTERNAL_URL`, `DEAL_INTERNAL_URL`, `NOTIFICATION_INTERNAL_URL` (W41, W42, W35).

**R4.6-B:** (0) the owner records the read-only database check of §13 step 10, which decides whether PR 3 edits
`ad6f07c940d8` in place or ships the forward revision (default: forward, when no answer is recorded); (1) `make
gate` on each of the five pull requests, merged in order; (2) the owner raises
`EDGE_BUDGET_ACCEPTED_MB` (below) **before** the deploy; (3) deploy `insights` after every source service (the
sources' export routes and event fields must exist first: ADR-0020 §12 order); the backfill runner starts itself,
paced; (4) the owner reads `bin/backfill --verify` per organization. **R4.6-C** (dashboard SPA) and **R4.6-D**
(`make edge-web` for the apps that adopt `useShellSearch`, plus the one operations route change) follow;
R4.6-C only after B. No flag and no reconcile (no new permission).

*Memory (reasoned from `infrastructure/edge/lib/memory_budget_totals.py`; every fold is +1 MB: `kinds.api` 98,
`kinds.api_with_worker` 99; assuming R2.4, R3.8 and R4.4 have shipped and R4.5 moved no profile total).*

| Step | Ten profiles | With `admin` | Owner action |
|---|---|---|---|
| after R4.5 | 3750 MB (97.1% of 3862) | 3947 MB (102.2%) | accepted ceiling 3947 |
| R4.6-A marketing fold | 3751 MB | 3948 MB (102.2%) | raise to 3948 before the deploy |
| R4.6-B insights (+99) | 3850 MB (99.7%) | 4047 MB (104.8%) | raise to 4047; the preflight cap is 125% = 4827 MB |
| R4.7 storefront fold | 3851 MB (99.7%) | 4048 MB (104.8%) | raise to 4048 |

Without insights the end state is 3752 and 3949 MB (L17). The `planned.insights-api` placeholder is deleted by
PR 1. `EDGE_BUDGET_ACCEPTED_MB` is a box `.env` value that only the owner edits.

**The PDF library of R4.5-B is not in these running totals, and it can push the end state over the box.** It is
unmeasured (inference: +10 to +20 MB in each of `deal` and `finance`, so +20 to +40 MB) and R4.5-B waits for
R19, so its place against R4.6 is not fixed. Whenever it lands it adds the measured delta through the
`api_with_worker_render` kind (§10.4). Worst case with insights and every fold in place:

| Step | Ten profiles | With `admin` | Owner action and stop condition |
|---|---|---|---|
| after R4.7 with insights | 3851 MB (99.7%) | 4048 MB (104.8%) | the ceiling of the row above |
| R4.5-B PDF library, worst case (+20 to +40 MB) | **3871 to 3891 MB (100.2% to 100.8%)** | **4068 to 4088 MB (105.3% to 105.9%)** | over the box's 3862 MB MemTotal in both columns, so R4.5-B ships only after the owner raises the accepted ceiling to the figure `memory_budget_totals.py` prints; the preflight refuses above that ceiling and above 125% (4827 MB), and that refusal is the stop condition |
| the same without insights | 3772 to 3792 MB (97.7% to 98.2%) | 3969 to 3989 MB (102.8% to 103.3%) | raise by the measured delta |

Re-pricing `kinds.api` or `kinds.api_with_worker` for two containers is rejected: it would move every container of
those kinds (dozens) by the same amount.

### 11.10 Rollback

*R4.6-A.* Stop consuming by rolling marketing back to the previous image (the tables stay; the API ignores
them) or by `RUN_CONSUMERS_IN_API=false` plus the `split-workers` profile; withhold the SPA publish. Lead and
deal roll back freely (additive columns and fields). *Not reversible:* nothing destructive (the projection is
derived; H12 rebuilds it). *R4.6-B.* **Stop the `insights` container** (ADR-0020's rollback): dashboards fall
back to their existing calls, the sources keep their additive changes. `daily_snapshots` is the one table that
cannot be rebuilt; it stays in the backup and no rollback step truncates it. *R4.6-C and D.* Revert the SPA
publish. *Stop conditions:* a migration longer than the 5 s lock timeout; a `*.events.dlq` depth that rises
after the deploy; a CRM OWN caller who sees a figure for a record they do not own (stop and withdraw the
dashboard publish); a fact row that holds a deal or company name; an attributed total that disagrees with a full
recompute; **a rollback of marketing or insights while its queue carries live traffic without H12 or the insights
backfill ready to run when it returns**.

**What a code rollback loses (§4.7).**

| Consumer (queue) | Events a rollback discards | Catch-up |
|---|---|---|
| `fold_lead_attribution`, `fold_deal_attribution` (`marketing.events`) | attribution events published while marketing runs the old image, or is stopped | **catch-up:** H12 `plan`, then `apply`: it pages C98 and C99 and writes through the same fold functions under the per-campaign lock |
| the insights handlers (`insights.events`) | fact events published while insights is stopped or rolled back | **catch-up:** insights' own backfill runner (`bin/backfill`, with `--verify` read by the owner) rebuilds each fact table from the export routes; `daily_snapshots` is the one table it cannot rebuild, which is why nothing truncates it |
| notification's `marketing_campaign_id` link | none: it adds no consumer | not affected |

### 11.11 Acceptance criteria

- [ ] `deal.deals` carries `marketing_campaign_id` and `utm_*` with `ix_deals_org_marketing_campaign`; `notification.campaigns.marketing_campaign_id` exists; the three marketing tables exist with `ENABLE`, `FORCE` and the policy; all four migrations round-trip and the two that hold data refuse a downgrade with rows.
- [ ] A lead or deal accepts a campaign id once and refuses a change or a clear with 409 `attribution_locked`; conversion copies the lead's campaign and UTM to the deal.
- [ ] The six events carry `marketing_campaign_id`, never a `utm_*` key; each producer test passes `validate_event_schema`.
- [ ] A won deal in the campaign's currency raises `effective.revenue_minor` and `conversions`; a won deal in another currency raises `conversions` only and appears in `by_currency`; no total in any response adds two currencies.
- [ ] After any sequence of redelivered, reordered and deleted events, `campaign_attribution` equals a full recompute from `attribution_deals`, including when two real connections (or H12 beside a live handler) write one campaign at once; the per-campaign lock is what the mutation check removes.
- [ ] `GET /marketing/campaigns/{id}/delivery` returns Engage counts for linked campaigns, `unavailable: true` when notification is down, and never an address; `GET /marketing/attribution/unmatched` counts only ids with no live campaign.
- [ ] `services/insights` has a single Alembic head and `alembic upgrade head` succeeds on an empty database **and, through the forward revision, on a database stamped at the unedited q2 head**; the in-place edit of `ad6f07c940d8` is merged only when §13 step 10 records that no shared database carries the schema; no module under `services/insights/app` contains `closed_won` or `closed_lost`; no insights column is a name, e-mail, phone or free text; the three export routes are name-free and serialize a custom stage and a `whatsapp` lead.
- [ ] The worst case of §11.9 (the PDF library with insights and every fold) has been priced through `api_with_worker_render` and the owner's accepted ceiling covers it before R4.5-B deploys.
- [ ] A CRM OWN caller's `deals_won` and `pipeline_by_stage` match exactly the deals whose `owner_membership_id` is theirs; an unmapped owner's deal appears at ORG only.
- [ ] `quotes_sent`, `quotes_accepted_value` and `pipeline_by_stage` follow the catalogue rows of §11.4.2 and return `no_access` inside a 200 for an ungranted caller.
- [ ] The Owner home draws the four pipeline slots from one summary request, keeps `countCrmCustomers` while `/insights` answers 404 or 503, and never shows a figure from two sources in one slot.
- [ ] `useGlobalSearch` has the sources of §11.4.4, none for ITSM, and every app passes the shared palette configuration; `GET /operations/orders?q=` matches a number prefix and a buyer name and never a phone.
- [ ] RUNBOOK literals, `memory-budget.json`, compose and ops-monitor lists carry the running totals of §11.9 in the same commit as each fold; the infra tests of §11.8 are green.
- [ ] `docs/insights/*.md` and ADR-0020 carry the changes of §11.4.2 in the same commits as the code; the v2 design of §11.5 is filed as `docs/insights/v2-company-facts.md`.
- [ ] Every internal client call this release adds (W35, W41 and W42 in marketing; W44 to W51 in insights) is registered in `tests/infra/test_internal_client_paths.py` with a driver that returns the token it sent.
- [ ] Every changed or new screen has loading, empty, error and forbidden states at phone width, no new token-baseline count, a story per new `@opsgrids/ui` export, a fixture and `MockRoute` per new `GET`, and no new axe violation.
- [ ] `make check` and `make gate` pass locally.

## 12. R4.7 Remaining products and coverage audit

### 12.1 Purpose, needs and stages

Four things are left after R4.6, and the release ends with the audit that says whether the programme did what
P§6 promised. (A) **HRMS hooks**: an employee who leaves, or whose membership is revoked, stops being a rep
without anyone's book being reassigned for them; a rep on approved leave is skipped by round-robin. (B)
**Storefront**: an enquiry becomes a lead with consent evidence, the enquiry shows what CRM did with it, and the
buyer's data is erased with the contact. (C) **The orphan check** designed in §6.4.4 is built. (D) **Records
and audit**: the vendor-party, ITSM and platform-billing decisions are written down where a developer finds
them, and a final table maps every row of P§6 to what exists and the test that proves it.

**Needs.** R3.8 (`owner_team`, lead's fold F9, TEAM, and the `owner_team_changed` handler and
`tools/backfill_owner_team.py` that this release extends: §12.2), R4.1 (C50 to C53, C58, C89, EV18), R4.4
(support and project link columns, so C104 has something to list), R4.5 (`storefront.products.inventory_item_id`,
the mapping screen), R3.7 (C56, `external_ref`, `consent_evidence`, `gstin`, the `storefront`, `whatsapp` and
`webhook` source values, and SP3's lead erase and its audit scrub). R4.6 is **not** a prerequisite: the
storefront producer change is shared with insights q3 and applied by whichever ships first (§11.4.2).
Decisions: D-13 (the last +1 MB), D-27 and D-31 (new), and the inherited D-11 and D-24 (§2.1).

| Stage | Content | Memory |
|---|---|---|
| **R4.7-A HRMS** | HR producer gap closure (leave, employee); customer deactivates the roster entry; deal queues one reassignment task; lead skips a rep on leave | none |
| **R4.7-B Storefront** | enquiry to lead, consent, derived status, erase handlers in storefront and lead, the lead's own consent forwarding | +1 MB (storefront fold, F17) |
| **R4.7-C Orphan check** | C104 on nine services, the customer sweep, the owner tool | none (a sweep inside `customer`) |
| **R4.7-D Records and audit** | `docs/crm.md` records, `docs/crm-ecosystem-coverage.md`, the doc test | none |

### 12.2 Data model

**customer** (one revision on the head at merge, `# RE-PARENT HERE`; no new table):

```sql
SET LOCAL lock_timeout = '5s';
ALTER TABLE customer.owner_team ADD COLUMN IF NOT EXISTS employee_id UUID NULL;      -- B71; SP3's DDL has none, so this adds it
CREATE INDEX IF NOT EXISTS ix_owner_team_org_employee ON customer.owner_team (organization_id, employee_id)
    WHERE employee_id IS NOT NULL;
```

**Who writes `owner_team.employee_id`.** The HR-exit mapping (§12.4.1) and the leave mapping (§12.4.3) read it
and nothing else writes it: SP3's `owner_team` has no such column, its `owner_team_changed` handler upserts only
`manager_membership_id`, `status` and the version (SP3 §12.3.4), and its tool `tools/backfill_owner_team.py`
(H10) does not write it either. With the column NULL an HR exit is "unmapped" and does nothing, and every rep
reads as available: a silent no-op in both hooks. R4.7 therefore **extends R3.8's three pieces**. (1) The
`owner_team_changed` handler in customer and in lead also writes `employee_id` from the enriched
`core.membership.*` payload, which already carries it (EV29, SP3 §12.3.4), under the same strictly-newer
version guard. (2) `tools/backfill_owner_team.py` `apply` also writes `employee_id` from the C57 rows, which
carry it, and `plan` counts a row whose `employee_id` differs as drift (a second `plan` shows zero drift
again). (3) Tests for both (`owner_team_changed` writes and clears `employee_id`, a stale event does not; the
tool's `plan` reports the drift and `apply` closes it, gated by the three gates). After the deploy the owner
**re-runs `backfill_owner_team.py apply`** (a gated step, §13 step 11), because membership events are not
replayable and existing rows stay NULL until it runs. The check that it worked is an acceptance criterion
(§12.12): per organization, the reps mapped to an employee equal the active members that carry an employee link.

The orphan sweep discovers organizations through the outbox-role grant that customer's `migrations/env.py`
holds from R4.3 (`SWEEP_DISCOVERY_TABLES`, §8.2: `companies (organization_id, deleted_at)`); R4.7 adds no
grant, and changes that dict in the same commit if its discovery SQL ever reads another column.

**lead** (one revision on R3.8's head, `# RE-PARENT HERE`; the registry's §B.3 chain note lists it:
`rep_unavailability` B75, `owner_team.employee_id` B71, `lead_settings.timezone` B76):

```sql
SET LOCAL lock_timeout = '5s';
ALTER TABLE lead.owner_team ADD COLUMN IF NOT EXISTS employee_id UUID NULL;          -- B71
CREATE INDEX IF NOT EXISTS ix_owner_team_org_employee ON lead.owner_team (organization_id, employee_id)
    WHERE employee_id IS NOT NULL;
ALTER TABLE lead.lead_settings ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Kolkata';   -- B76

-- B75: approved leave, projected from HRMS events; one row per leave request
CREATE TABLE lead.rep_unavailability (
    -- TenantBase columns, then:
    source_ref VARCHAR(80) NOT NULL,                     -- 'leave:{leave_request_id}'
    employee_id UUID NOT NULL,
    starts_on DATE NULL,
    ends_on DATE NULL,
    state VARCHAR(10) NOT NULL,
    source_occurred_at TIMESTAMPTZ NOT NULL,             -- the version guard
    CONSTRAINT ck_rep_unavailability_state CHECK (state IN ('active', 'cancelled')),
    CONSTRAINT ck_rep_unavailability_dates CHECK (state = 'cancelled'
        OR (starts_on IS NOT NULL AND ends_on IS NOT NULL AND starts_on <= ends_on))
);
CREATE UNIQUE INDEX uq_rep_unavailability_org_source ON lead.rep_unavailability (organization_id, source_ref);
CREATE INDEX ix_rep_unavailability_org_employee ON lead.rep_unavailability (organization_id, employee_id)
    WHERE state = 'active';
```

`rep_unavailability` gets `ENABLE` + `FORCE` row-level security and the `tenant_isolation` policy through the
revision's `_rls()` helper, `opsgrids_lead` grants through `apply_schema_grants`, and joins lead's
`TENANT_TABLES`. **The lead audit scrub is created once, by R3.7a, and R4.7 does not create it.**
`lead.scrub_lead_audit(p_organization_id uuid, p_lead_id uuid)` (B86) and its two helpers have the customer
shape (§6.2): `SECURITY DEFINER`, `SET search_path = lead, pg_temp`, owner `opsgrids_admin`, `EXECUTE` revoked from
`PUBLIC` and granted to `opsgrids_lead` only, rewriting rows with `entity = 'lead'` and that `entity_id` in that
organization and nothing else. SP3's administrator erase of one lead (C108, R3.7a, SP3 §11.3.3) cannot ship
before it, because public capture is what first stores a stranger's details, so **SP3's R3.7a revision creates
the function once** (SP3 §11.2), with this signature and over the union of the two specifications' key lists
(registry B86, J26). R4.7 needs R3.7a (it needs R3.7, and R3.8, which needs R3.7a: registry J30) and therefore never creates
`lead.scrub_lead_audit`: it only **calls** it
from the contact-erase handler (§12.4.6), and R4.7's tests assert the two guards of §6.2 against the function
R3.7a created: the helpers return JSON `null`, arrays and scalars unchanged, and the function raises unless
`p_organization_id` equals `lead.current_org()`. The keys it rewrites are SP3's six
(`full_name` to `Erased lead`; `company`, `notes`, `email`, `phone`, `raw_phone` to `[erased]`), which contain
this document's four (`full_name`, `notes`, `email`, `phone`) and are a superset of what lead writes at
`services/lead/app/services.py:525-528, 633-637`. Downgrade drops the table, index and
column that R4.7 added and **refuses** while any `rep_unavailability` row exists unless
`OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`.

**storefront** (one revision on `ea0b8b84b84d`; `lock_timeout 5s`; no new table, so no RLS change):

```sql
-- B77: the consent control and its evidence, written once at creation
ALTER TABLE storefront.storefronts ADD COLUMN enquiry_consent_text VARCHAR(500) NULL;   -- NULL: the form shows no control
ALTER TABLE storefront.enquiries ADD COLUMN marketing_consent BOOLEAN NULL;             -- NULL: no control was shown
ALTER TABLE storefront.enquiries ADD COLUMN consent_text VARCHAR(500) NULL;             -- the text shown, snapshotted
ALTER TABLE storefront.enquiries ADD COLUMN consent_at TIMESTAMPTZ NULL;
ALTER TABLE storefront.enquiries ADD CONSTRAINT ck_enquiries_consent_shape CHECK (
    (marketing_consent IS NULL) = (consent_text IS NULL) AND (marketing_consent IS NULL) = (consent_at IS NULL));
-- B51 + B78: what CRM did with the enquiry
ALTER TABLE storefront.enquiries ADD COLUMN crm_lead_id UUID NULL;
ALTER TABLE storefront.enquiries ADD COLUMN crm_contact_id UUID NULL;
ALTER TABLE storefront.enquiries ADD COLUMN crm_company_id UUID NULL;
ALTER TABLE storefront.enquiries ADD COLUMN crm_deal_id UUID NULL;
ALTER TABLE storefront.enquiries ADD COLUMN crm_status VARCHAR(12) NULL;
ALTER TABLE storefront.enquiries ADD COLUMN crm_status_at TIMESTAMPTZ NULL;
ALTER TABLE storefront.enquiries ADD CONSTRAINT ck_enquiries_crm_status
    CHECK (crm_status IS NULL OR crm_status IN ('new', 'in_progress', 'won', 'lost'));
CREATE INDEX ix_enquiries_org_crm_lead    ON storefront.enquiries (organization_id, crm_lead_id)    WHERE crm_lead_id IS NOT NULL;
CREATE INDEX ix_enquiries_org_crm_contact ON storefront.enquiries (organization_id, crm_contact_id) WHERE crm_contact_id IS NOT NULL;
CREATE INDEX ix_enquiries_org_crm_company ON storefront.enquiries (organization_id, crm_company_id) WHERE crm_company_id IS NOT NULL;
CREATE INDEX ix_enquiries_org_crm_deal    ON storefront.enquiries (organization_id, crm_deal_id)    WHERE crm_deal_id IS NOT NULL;
```

The `PublicStore` projection (`services/storefront/app/selectors.py:70-98`) gains `enquiry_consent_text`, read by
the directory reader from the same identity row (table-level `SELECT` on `storefront.storefronts` is
*inference* from ADR-0010 §4's "one row of one table"; the PR confirms it with `\dp` as the directory role).
The enquiry's own docstring ("only `status` ever changes", `services/storefront/app/models.py:780-796`) is
amended in the same commit: the CRM link columns and the erase of §12.4.6 are the two other writers. Downgrade
drops the indexes, constraints and columns, **refusing** while any `crm_lead_id` or consent value exists unless
`OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`.

**deal, operations, finance, notification:** no DDL in R4.7.

### 12.3 Backend contract

**HR producers (no route; the schemas are already committed).** `_publish_leave_applied`
(`services/leave/app/services.py:636-661`) adds `status`, `employee_id`, `leave_type_id`, `start_date`,
`end_date` (ISO) and `total_half_days`; `transition_status` (`services/employee/app/services.py:503-517`)
adds `date_of_joining` and `date_of_exit` (ISO or `null`). These are the producer hunks of ADR-0020 slice I9
and the schema `$comment` ("Ahead of the producer") is removed in the same commit; insights I9 later finds
them done. New producer tests: `services/leave/tests/test_event_contracts.py` and
`services/employee/tests/test_event_contracts.py`, each through the real write path and
`validate_event_schema`. `display_name` stays on the employee event (frozen) and **no CRM consumer reads or
stores it**.

**customer**

| Route or event | Permission, floor | Change and errors |
|---|---|---|
| `PATCH /customers/owners/{owner_id}` with `is_active = false` (C28) | `crm.book.owner.write` (existing), ORG | the guard counts open deals and leads as well as live companies and contacts (C94 below); 409 `owner_has_open_records` names the counts; **503 `owner_counts_unavailable`** when deal or lead cannot answer (fail closed: nothing is deactivated on a guess); on success it stages EV25 with `reason: "manual"` |
| EV25 `crm.book.owner.deactivated` | not subscribable | `reason` is `membership_revoked`, `employee_exited` (**SP4 addition**, SD-17) or `manual`; payload otherwise as the registry; committed schema `libs/contracts/events/crm.book.owner.deactivated/v1.schema.json`, producer test through each of the three paths |
| C104 on every source (**SP4 addition**) | internal, caller `customer`, org claim | `GET /internal/v1/organizations/{organization_id}/crm-references?kind=company\|contact&after=<cursor>&limit=<=500` → `{items: [{kind, id, source}], next}`; `items` are **distinct `(source, id)`** keyset-paged; `source` is a fixed allow-listed label (below), never free text; 422 on another `kind`; three negative tests and a cross-tenant test per service |
| C94 on deal and on lead (**SP4 addition**) | internal, caller `customer`, org claim | `GET .../owners/{owner_id}/open-counts?membership_id=` → `{open_deals}` (deal; open by the stage's outcome) or `{open_leads}` (lead; status not `converted` or `disqualified`); counts only. **Customer sends both ids** of the roster entry, the path's `owners.id` and the query's `owners.membership_id` (null for an unmapped row), and the callee counts rows whose `owner_id` equals **either**: after R2.7 a mapped row's `owner_id` is a membership id while legacy and unmapped rows keep the roster id (registry B3, D-8 leaves them untouched), so counting one id would under-count and let the guard allow a deactivation it should refuse. A row that still holds an **employee** id (an unmapped legacy deal) is not counted: the owner's `verify` report lists those rows (D-8) |

C104 sources by service: **finance** `finance.customers.crm_company_id`, `finance.customers.crm_contact_id`,
`finance.invoices.crm_company_id`; **operations** `operations.orders.crm_company_id`,
`operations.orders.crm_contact_id`; **notification** `notification.contacts.crm_contact_id`,
`notification.conversations.crm_contact_id`, `notification.conversations.crm_company_id`,
`notification.messages.crm_contact_id`; **support** `support.tickets.crm_company_id`,
`support.tickets.crm_contact_id`; **project** `project.projects.crm_company_id`; **projects**
`projects.projects.crm_company_id`; **storefront** `storefront.enquiries.crm_company_id`,
`storefront.enquiries.crm_contact_id`; **deal** `deal.deals.company_id`, `deal.deals.primary_contact_id`,
`deal.activities.contact_id`, `deal.follow_ups.company_id`, `deal.follow_ups.contact_id`; **lead**
`lead.leads.converted_company_id`, `lead.leads.converted_contact_id` (lead holds plain UUIDs into customer too,
`services/lead/app/models.py:109-111`, so it is the ninth source, named in §4.2 and §6.4.4).

**lead**

| Route | Permission, floor | Change |
|---|---|---|
| `GET /leads/assignment-rule` | `crm.lead.lead.read` (existing) | `AssignmentRuleOut` gains `unavailable_owner_ids: uuid[] \| null` (`services/lead/app/schemas.py:172`): the rotation members on approved leave today, **only for a caller whose scope is ORG**; for any narrower scope it is `null`, because an absence is a colleague's personal fact and an OWN reader has no business with it |
| `PUT /leads/settings` (C40) | `crm.lead.settings.write`, ORG | gains `timezone` (an IANA name, validated against `zoneinfo`; 422 `timezone_invalid`) |
| C94, the erase handler, the HR handlers | internal and consumer | §12.4 |

**leave** (HRMS; runs under the `hrms` profile): C100 (**SP4 addition**), `GET
/internal/v1/organizations/{organization_id}/leave-requests:export-approved?from=<date>&after_id=&limit=`,
caller `lead`, org claim required, read-only on the tenant-bound session; rows `{leave_request_id, employee_id,
start_date, end_date, updated_at}` for requests with `status = 'approved'` and `end_date >= from`, keyset on
`(updated_at, id)`, `limit` at most 500; **no reason, no leave type, no name**; 422 on a missing `from`; the
three negative tests and a cross-tenant test (`services/leave/tests/test_availability_export_authz.py`). On a
box without the `hrms` profile lead's call fails and availability stays empty (everyone is available).

**storefront**

| Route | Permission, floor | Change and errors |
|---|---|---|
| `POST /storefront/public/enquiry` | none (public, rate-limited by the gateway) | the body gains `marketing_consent: bool \| null`; allowed only when the store has an `enquiry_consent_text` (422 `consent_not_offered` otherwise); when the store offers the control and the field is absent it is stored as `false`; the server snapshots the store's text and `now()` (the buyer never supplies text) |
| `POST /storefront/public/store-context` | none | the response gains `enquiry_consent_text: string \| null` |
| `PUT /storefront/store` | `storefront.store.settings.write`, ORG | gains `enquiry_consent_text` (brand-text cleaned, at most 500 characters, `null` or empty clears it) |
| `GET /storefront/enquiries`, `GET /storefront/enquiries/{id}` | `storefront.sales.enquiry.read`, ORG | `EnquiryOut` gains `crm_lead_id`, `crm_contact_id`, `crm_company_id`, `crm_deal_id`, `crm_status` and `marketing_consent`; `status` keeps its meaning |
| `POST /storefront/enquiries/{enquiry_id}:send-to-crm` (**SP4 addition**, C95) | `storefront.sales.enquiry.write`, ORG | claim, call, record with the consumer's own function, on its own sessions; 200 `{linked: bool, crm_lead_id: uuid \| null}` and idempotent; 404 foreign; **409 `crm_not_active`** when lead answers C56 with `skipped_reason = "not_entitled"` (registry J27); **503 `crm_unavailable`** on a timeout or a 5xx. It is also the catch-up for enquiries created before the queue existed |

**Events.** EV37 `storefront.enquiry.created` (`status`, `lines`) and `.status_changed` are produced exactly as
`sprint/insights-q3` does (§11.4.2); no other event changes. `storefront.events` binds `storefront.enquiry.created`
(its own event, after commit through the outbox), `crm.lead.updated`, `crm.lead.converted`,
`crm.pipeline.deal.stage_changed` and `crm.book.contact.erased` (EV49 gains three keys, **SP4 addition**, registry EV49);
`lead.events` gains `crm.lead.created` (its own, for consent forwarding) and `crm.book.contact.erased` (EV45
gains two keys); `customer.events` gains `hrms.people.employee.transitioned` (EV43, SD-17).

### 12.4 Domain rules

#### 12.4.1 Owner deactivation (customer)

Three triggers, one outcome: the roster entry becomes inactive, one EV25 is staged, **nothing is reassigned**.

1. `core.membership.revoked` (`organization_id, membership_id, user_id` at HEAD, enriched by R3.8): find the
   `owners` row with that `membership_id`; none means "not a rep", a no-op.
2. `hrms.people.employee.transitioned` with `to = 'exited'` (an HR exit does **not** revoke the membership,
   SD-17, so this binding is what notices it): find the membership by `owner_team.employee_id` (filled by the
   extended `owner_team_changed` handler and the re-run `backfill_owner_team.py`, §12.2); none found logs
   `hr_exit.unmapped` (a count, no id) and stops.
3. The manual `PATCH` route above.

Triggers 1 and 2 **bypass the open-records guard**: the person has left, and refusing would leave an active
rep who cannot act. An already inactive owner is a no-op (no second event). The audit row names the trigger
(`acting_as = account.membership_revoked`, `hrms.employee_exited` or the caller) and holds ids only. There is
no automatic reactivation when a membership returns: the Team screen's activation (C26) is the act. Access to
the product after an HR exit is the organization's own decision in account administration; SP4 does not
revoke a login.

#### 12.4.2 The reassignment task (deal)

On `crm.book.owner.deactivated`, deal counts the open deals the owner holds (`deals.owner_id` equal to the
event's `owner_id` **or** its `owner_membership_id`, the two id spaces of C94; open by the stage's outcome).
Zero: acknowledge and stop. Otherwise it creates **one** follow-up through the same
service function `POST /deals/follow-ups` uses (R3.6), with `origin = 'system'`, `origin_ref =
'owner_deactivated:{owner_membership_id}:{yyyy-mm-dd}'` (the partial unique index on `(organization_id, origin,
origin_ref)` makes a replay a no-op and lets a later departure on another day create a new task), `deal_id` =
the owner's oldest open deal (a deal-schema follow-up needs one target, B25), `title = "Reassign {n} open
deals of a deactivated rep, and check their leads"` (a number and no name), `due_at = now +
pipeline_settings.follow_up_default_hours`, and as its `owner_id` the departed rep's manager from deal's own
`owner_team` when that manager is `active`, else the organization's `is_owner` membership (C57, read before the
write transaction), else nobody: no task, a `deal.owner_deactivated.no_assignee` error log with counts. Deals
and leads keep their owner until a person reassigns them (C37 for leads).

#### 12.4.3 Round-robin availability (lead)

1. *Fold.* `hrms.time.leave.applied` with `status = 'approved'` upserts `rep_unavailability` for
   `source_ref = 'leave:{leave_request_id}'` (`employee_id`, `starts_on`, `ends_on`, `state = 'active'`);
   `rejected` and `failed` write nothing. `hrms.time.leave.cancelled` sets that row to `cancelled`, inserting a
   tombstone first when the row does not exist, so an `applied` that arrives afterwards is older and loses to the
   strictly-newer guard. Every handler applies only if the envelope `occurred_at` is strictly newer than
   `source_occurred_at`.
2. *Rotation.* `_resolve_round_robin_snapshot` (`services/lead/app/services.py:339-364`) also reads the set of
   rotation members whose mapped employee has an `active` row with `starts_on <= today <= ends_on`, where
   `today` is the date in `lead_settings.timezone` (a local read, before the rule's row lock, like the active
   snapshot). `_assign_owner` (`:367-417`) builds `rotation = [o for o in rule.owner_ids if o in active and o
   not in unavailable]`. **If that is empty and `active` is not, availability is ignored for this lead** and
   `lead.assignment.availability_ignored` is logged: a lead with an owner who returns beats a lead nobody sees
   (the rejected alternative: leave it unassigned). A rep with no mapped employee is available. A half-day
   leave counts as a full day away.
3. *Deactivation.* On `crm.book.owner.deactivated` lead evicts its in-process active-owner caches for the
   organization (`_assignment_active_cache`, `_assignment_unavailable_cache`) so the next lead cannot be
   assigned from a stale answer, and logs the count of open leads the owner still holds. It reassigns nothing.
4. *Catch-up.* Leave events published before lead's binding existed are lost, and an approved leave that is
   already running or booked matters, so lead pages leave's **C100** (`GET .../leave-requests:export-approved?from=<date>&after_id=&limit=`,
   caller `lead`, org claim, rows `{leave_request_id, employee_id, start_date, end_date, updated_at}` for approved
   requests with `end_date >= from`) through the owner-run tool H13 (below) before the HR producers change.

#### 12.4.4 Enquiry to lead (storefront)

The handler of `storefront.enquiry.created` runs claim, call, record (`docs/io-outside-transactions.md`):

1. *Claim* (one short transaction under the envelope's organization): load the enquiry; absent or already
   carrying `crm_lead_id` means mark processed and stop; otherwise read `name`, `company`, `gstin`,
   `requirement`, `marketing_consent`, `consent_text`, `consent_at`, and unseal `email_enc` and `phone_enc` in
   process (the plaintext lives only in memory and in the authenticated bodies below).
2. *Call* (no transaction open): (a) C56 `leads:capture` with `{source: "storefront", external_ref:
   "<enquiry_id>", full_name, company, gstin?, email, phone?, message: requirement (first 1000 characters),
   consent?: {granted, text_hash: sha256(consent_text)}}` (the consent block only when the store offered the
   control); (b) C52 `parties:resolve` with `{email, phone?, company_name, gstin?}` (SD-1: resolve, never
   ensure: SD-7 reserves party creation to conversion); (c) when `marketing_consent` is `true`, C58 with
   `{channel: "email", purpose: "marketing", status: "opted_in", source: "web_form", evidence_ref:
   "storefront.enquiry:<enquiry_id>", address: email, occurred_at: consent_at, idempotency_key:
   "storefront-enquiry:<enquiry_id>"}`. A `false` writes nothing: silence is already "not consented" and a
   refusal at a form is not an opt-out of anything.
3. *Record* (one short transaction): `crm_lead_id = duplicate_of_lead_id ?? lead_id`; `crm_contact_id` and
   `crm_company_id` from C52 unless `ambiguous` (then from `duplicate_contact_id`, company left null);
   `crm_status = 'new'` and `crm_status_at` = the event's `occurred_at` when they are null; one audit row
   `sales.enquiry.linked_to_crm` (ids only); mark processed.

*Failure classes.* C56 answers **200 with `skipped_reason: "not_entitled"`** (registry J27; beside `disabled`
and `rate_limited`, with `lead_id` null) when the organization is not entitled to `crm`: lead re-checks the
entitlement itself through product service, W36, as C64's callees do, because an internal route sits behind no
entitlement gate, and a definite "no" is a 200, not an error (SP3 owns C56 and its answer; the 403
`crm_not_active` stays on customer's C50 to C53 and C89, §4.2). Storefront's handler maps
`skipped_reason = "not_entitled"` to its audit row `crm_not_active` and acknowledges: no retry storm for a
store whose owner never bought CRM. A timeout or 5xx raises:
one redelivery, then `storefront.events.dlq`. C56 is idempotent on `(source, external_ref)` and C58 on its key,
so any re-run converges. The merchant's `:send-to-crm` runs the same function for an enquiry that has no link.

#### 12.4.5 Derived status (storefront)

`status` stays the merchant's own desk workflow (`new → contacted → won | lost`,
`services/storefront/app/domain.py:1086-1091`) and is **never** overwritten by CRM. `crm_status` is a second,
read-only field and the SPA's headline when it is not null (SD-23). Each handler applies only if the event's
`occurred_at` is strictly newer than `crm_status_at`.

| CRM fact | Event | `crm_status` |
|---|---|---|
| lead `new` | the link record of §12.4.4 | `new` |
| lead `working` or `qualified` | `crm.lead.updated` (`status`, via `crm_lead_id`) | `in_progress` |
| lead `disqualified` | `crm.lead.updated` | `lost` |
| lead converted | `crm.lead.converted` (sets `crm_contact_id`, `crm_company_id`, `crm_deal_id`) | `in_progress` |
| deal outcome `won` / `lost` / `open` | `crm.pipeline.deal.stage_changed` (`to_outcome`, matched by `crm_deal_id`) | `won` / `lost` / `in_progress` |

**`crm_status` is forward-only.** It is a derived display stamped with `crm_status_at`, not a workflow state, and
nothing acts on it. A storefront code rollback or a lost event leaves it stale until the next event for that lead
or deal repairs it (each handler wins only if its event is strictly newer than `crm_status_at`, so a later event
always overwrites an earlier state); the merchant's own `status` is never involved. The inbox therefore shows
"as of {crm_status_at}" beside the headline and offers a filter of enquiries that have no CRM link, so the
merchant can run `:send-to-crm` for each (§12.4.4); a bulk refresh was considered and not built, because it needs
a lead and a deal read route for a display that the next event corrects (§15).

#### 12.4.6 Consent and erasure at the two capture surfaces

*Single writer (SD-24).* The system that owns the surface writes the ledger (§6.5, decision 2). Storefront
writes for its buyer form (§12.4.4 step 2c). Lead writes for **its own** capture endpoints only: on its own
`crm.lead.created`, for a lead with `source` in (`form`, `webhook`), an e-mail and `consent_evidence.granted =
true` without a `ref`, lead calls C58 (`channel: "email"`, `purpose: "marketing"`, `source: "web_form"`,
`evidence_ref: "lead:<lead_id>"`, `idempotency_key: "lead-consent:<lead_id>"`, `occurred_at` =
`captured_at`) and stores the key as `ref` in the same JSONB object (B39). A lead whose `source` is
`storefront`, `whatsapp`, `manual` or `import` is **never** forwarded by lead, so no consent is written twice;
a lead with only a phone is not forwarded (the ledger is keyed on an address and the endpoint's text names no
channel).

*Single opt-in with evidence (pending D-31).* The control is unticked, carries the merchant's own text and the
ledger row names the enquiry, so a complaint is traceable; every marketing message carries the unsubscribe
link (ADR-0021). The weakness is the one every public form has: anyone may type another person's address with
the box ticked. The alternative, a confirmed opt-in (a confirmation e-mail whose link writes the `opted_in`
event), needs a public confirmation route and token and is not in R4.7. No WhatsApp or SMS opt-in is ever
inferred from an e-mail opt-in.

*The repaired starter recipe.* `welcome-converted-lead` (EV39) sends with `purpose: "marketing"`, which R1.2b
chose before any consent design existed (SP1 §9.2.2). Whichever of R3.7b and R4.7 first lets a converted lead
carry consent evidence re-decides that purpose with this design, and R4.7's closing commit confirms or changes
it: the answer is written into the ADR of §6.5, and into `recipes.ts` if the purpose changes.

*Erase handlers.* On `crm.book.contact.erased` **storefront** overwrites, for rows with `crm_contact_id` equal
to the contact, `name = 'Erased contact'`, `email_enc = NULL`, `email_masked = ''`, `phone_enc = NULL`,
`phone_masked = ''` (company, lines, requirement, the consent evidence and the status are kept, D-27; its audit
rows hold only `{status, company}`, so no scrub function is needed); **lead** applies, for leads with
`converted_contact_id` equal to the contact, **the same function as SP3's administrator erase of one lead**
(SP3 §11.3.3: `full_name` becomes `Erased lead`; company, e-mail, phone, GSTIN, `external_ref`, the duplicate
pointers, `notes` and the visitor's messages are cleared; the row is soft-deleted; source, status, owner,
attribution, `consent_evidence` and the converted ids stay) with `acting_as = crm.book.contact.erased`, which
calls `lead.scrub_lead_audit` for each such lead and writes one counts-only audit row, so "erased" means one
thing for a lead. A lead of the same person that **never converted** is not reached by this event (no contact id
names it): an administrator erases it with SP3's `POST /leads/{lead_id}:erase` (§6.5, D-27), and what that
route keeps is SP3's D-27 default. The owner-run H14 re-applies earlier erasures for `--service lead` and
`--service storefront` at the first deploy of each handler.

#### 12.4.7 The orphan check (built)

As designed in §6.4.4 and §4.2, with these concrete rules: the sweep infrastructure of `customer` (the
`app/cron.py` module, `Settings.run_sweeps_in_api`, the lifespan hooks that follow
`services/operations/app/main.py:60-90` with a bulkhead engine named `sweep` and discovery on the outbox role,
the rewritten `bin/cron` that today is a copy of `bin/relay`: `services/customer/bin/cron`, and the compose
line `RUN_SWEEPS_IN_API: ${RUN_SWEEPS_IN_API:-true}`) arrived with R4.3's `PartyRollupSweep` (§8.5). R4.7 adds
`PartyOrphanSweep` to `app/cron.py` as a **second supervised task** beside it, so one sweep's failure never
touches the other's schedule (the shape of operations' reconcile and alert sweeps); `party_orphan_interval_seconds`
defaults to 86400 with the first pass 15 minutes after start. For each
organization and each source it pages C104 and classifies every id in one `= ANY(:ids)` query: **`ok`** (a live
company, or a live contact), **`missing`** (no row or soft-deleted) or **`erased_linked`** (the contact exists
but is erased, which a store that clears links on erase should not still hold). It logs one
`party.orphan_check.summary` per organization and source with the three counts, at `error` when `missing > 0`,
never ids, and **deletes and repairs nothing**. A source that is down, not entitled or over its 2-second budget
increments `skipped`. `tools/check_party_orphans.py --org <uuid> [--list]` runs the same function read-only for
the owner and, with `--list`, prints ids for that one organization (the only place ids leave the process).

### 12.5 Consumers and idempotency

| Queue | Binds (new in R4.7) | Handler; marker | Notes |
|---|---|---|---|
| `customer.events` | `core.membership.revoked` (already bound by R3.8 for `owner_team`), `hrms.people.employee.transitioned` | `deactivate_owner_for_membership`; marker `uuid5(event_id, "crm-owner-deactivation")` for both keys | `EventConsumer.on` keeps a **list** of handlers per event type (`libs/platform-core/src/platform_core/events.py:446-451`), so R4.7's handler runs beside R3.8's `owner_team` upsert; each needs its own marker because `processed_events` is keyed on `event_id` alone (SD-4); "already inactive" is the second net |
| `deal.events` | `crm.book.owner.deactivated` | `queue_reassignment_task`; raw `event_id` | unique `(organization_id, origin, origin_ref)` is the second net |
| `lead.events` | `hrms.time.leave.applied`, `hrms.time.leave.cancelled`, `crm.book.owner.deactivated`, `crm.lead.created`, `crm.book.contact.erased` | `fold_leave`, `evict_owner_caches`, `forward_capture_consent`, `erase_converted_leads`; raw `event_id` | availability rows are version-guarded; the consent key is unique per organization in the ledger |
| `storefront.events` (**first handlers**, fold F17) | `storefront.enquiry.created`, `crm.lead.updated`, `crm.lead.converted`, `crm.pipeline.deal.stage_changed`, `crm.book.contact.erased` | `send_enquiry_to_crm`, `derive_enquiry_status` (three event types, one function), `erase_enquiries`; raw `event_id` | `external_ref` and the ledger key are the second net; `crm_status_at` is the version guard |
| `notification.events` | `storefront.enquiry.created`, `storefront.enquiry.status_changed` (the two subscribable keys of EV42, one `SUBSCRIBABLE_EVENTS` line each, S52) | the rule `fold` | consumer before producer: notification deploys before storefront's producer change (§12.10); the repaired `enquiry-follow-up` recipe triggers on a key that is now subscribable |
| `finance.events`, `operations.events`, `support.events` | (unchanged) | none | their erase handlers shipped with R4.2 to R4.4 |

Every handler binds the tenant from the envelope and cross-checks the payload organization; an off-contract
payload raises and dead-letters (`*.events.dlq`). Every queue the programme binds is watched: it is in
`CORE_DLQ_QUEUES` or `PROFILE_DLQ_QUEUES`, operations' `operations.events` having been added by R4.3 (§8.5, registry EV55) and
`PROFILE_DLQ_QUEUES["storefront"] = ("storefront.events",)` and the `storefront-worker` rollback container
being part of this fold (F17).

**The storefront recipe.** `enquiry-follow-up` (`frontend/product-engage/src/routes/automations/recipes.ts:51-55`)
triggers on `storefront.enquiry.created`, which `SUBSCRIBABLE_EVENTS` does not hold at HEAD, so the builder
flags it today and it would stay flagged (S52); the key joins the list in this release in the same commit as its
producer test, and `tests/infra/test_engage_recipe_triggers.py` (SP1) pins that every recipe trigger is
subscribable. Its `create_task` step still creates **no CRM follow-up** from that trigger: R4.4's deal handler
needs a CRM subject (EV31) and an enquiry event carries none, because the lead does not exist yet (§15).

### 12.6 Decision records for the deliberate non-connections

These three sections are written into `docs/crm.md` (I3) in R4.7 and are the text a developer is pointed to;
none is an ADR because no code depends on them (registry I3).

**DR-1 Vendor party.** *Context (facts).* A supplier exists in two unlinked places: `operations.suppliers`
(`code`, `name`, `category`, `contact_name`, `contact_email`, no GSTIN: `services/operations/app/models.py:97-147`)
and the text columns `supplier_gstin` and `supplier_name` on finance's purchase and IMS documents
(`services/finance/app/models.py:536-537, 593-594`). Neither holds a CRM id. *Decision.* A vendor is **not** a CRM
party: no `crm_*` column on either store, no event, no resolve call. *Why.* A company that both buys and sells
needs a role model nobody has asked for; CRM's rep ownership, OWN scope and erase semantics do not describe a
procurement relationship; and merging the two books would put supplier contacts under CRM permissions.
*Consequence.* The same real company may legitimately appear as a CRM company, a billed party and a supplier, and
no screen claims they are one. *Revisit when* a customer needs one record to be both (resellers, partners):
the join key would be the GSTIN, and the first step would add a validated `gstin` to `operations.suppliers`.

**DR-2 ITSM.** *Context.* Requesters and assignees are internal staff identified by directory ids used for OWN
scope (`services/itsm/app/models.py:108-125, 225-245`; `services/itsm/app/scope.py:1-42`); ITSM holds no
customer. *Decision.* Not connected: no `crm_*` column, no CRM event consumer, no search source. Staff identity
reaches ITSM only through the membership spine (NEW-8, outside this programme). *Why.* Linking a requester to a
CRM contact would conflate staff and customers and widen a CRM erase to an IT ticket. *Revisit when* ITSM adds an
external-customer channel (a service desk for the tenant's own customers): that is Support's job today.

**DR-3 Platform tenant billing.** *Context.* The customer in OpsGrids billing, subscription, metering and the
catalogue is a tenant **organization** (`services/account/app/models.py:60-67`,
`services/billing/app/models.py:83-91`). *Decision.* No link to a CRM company, ever; the dashboard calls those
invoices "OpsGrids billing" (§6.7). The merchant side of `payment` (collections for orders and, from R4.5-C,
invoices) is connected through operations and finance and is a different table family from the platform side.
*Why.* An organization is not a company the tenant sells to; attaching a CRM party to one would put a
tenant's customer list next to the platform's own. *Revisit when* never, unless OpsGrids sells to organizations
that are also tenants of another OpsGrids organization.

### 12.7 Final coverage audit against programme section 6

The table is the content of `docs/crm-ecosystem-coverage.md` (I9). "Proof" names the test that fails if the
connection breaks; a section reference points at that release's test table and acceptance list. The first row is
not in P§6 (CRM is the hub), the last two make the platform-service list of the SP4 task complete.

| Row (P§6) | Held at HEAD | Connection built | Release | Proof | Deliberately not built |
|---|---|---|---|---|---|
| CRM (hub) | companies, contacts, leads, deals, activities (S4, S11, S13) | ensure, resolve, export, erase, id-bearing events, projections, 360 sections | R4.1 to R4.7 | `services/customer/tests/test_party_routes_authz.py`, `services/customer/tests/test_contact_erase_authz.py` (§6.8), §6.11 | custom objects, a second contact book |
| Finance | `finance.customers` billed party, unwired bridge (S14, S15) | billing profile on `crm_company_id`, GSTIN profiles, terms and credit, captures, receivables projection, link tool H2, invoice PDF and staff-assisted collection | R4.1, R4.3, R4.5 | golden fixture `libs/contracts/parity/party_receivables_v1.json` (§8.7), §8.10, §10.10 | a public pay page (D-30), refund accounting (D-28), merging on name |
| Operations / inventory | order name and phone snapshot, `customer_id`, suppliers, `inventory_items` (S16 to S19) | CRM ids on orders, credit position, order-to-invoice chain (dark default), item master for quote lines | R4.3, R4.5 | §8.10, §10.10, `tests/infra/test_io_outside_transaction.py` | the vendor party (DR-1) |
| Storefront | `enquiries`, `products` (S33) | enquiry to lead with consent evidence, derived status, erase handler, `inventory_item_id` mapping | R4.5, R4.7 | `services/storefront/tests/test_enquiry_to_lead.py`, `services/storefront/tests/test_derived_status.py`, `services/storefront/tests/test_enquiry_consent.py`, §12.12 | confirmed opt-in (D-31), bulk import of old enquiries |
| Engage / notification | contact book, consent ledger, conversations (S24) | C58 writer, batch summary, Engage projection, CRM sends, WhatsApp activity, in-app alerts, erase | R4.1, R4.2 | §7.7, §7.10 | consent stored in CRM (SD-11), an automatic opt-in |
| Support | tickets with requester strings (S30) | contact and company links, 360 section and summary, customer notifications, ticket from conversation, erase anonymiser | R4.4 | §9.7, §9.10 | ticket bodies copied into CRM |
| Projects / PulseBoard | `client` free text (S31) | company link on both products, draft project on a won deal (default off), delivery card | R4.4 | §9.7, §9.10 | the `projects` kickoff before D-17 |
| Marketing | hand-typed numbers (S32) | attribution projection, Engage delivery read | R4.6-A | `services/marketing/tests/test_attribution_consumers.py`, §11.11 | spend ingestion, multi-touch attribution (D-36), retroactive attribution |
| HRMS | employees, reporting lines, leave | offboarding deactivation, one reassignment task, availability for round-robin, HR producer closure | R3.8 (TEAM), R4.7 | `services/customer/tests/test_owner_deactivation_consumer.py`, `services/lead/tests/test_assignment_availability.py`, §12.12 | automatic reassignment, revoking a login on HR exit |
| ITSM | internal staff requesters (S39) | **none** (DR-2) | R4.7 record | `tests/infra/test_crm_ecosystem_coverage_doc.py` (no `crm_*` column in ITSM) | everything |
| Insights / dashboard | unmerged `sprint/insights-q1..q3` (S43) | merge, outcome, membership subject, no names, stage and quote facts, tiles, search | R4.6-B to D | `services/insights/tests/test_outcome_adoption.py`, `services/insights/tests/test_membership_subject.py`, `services/insights/tests/test_no_names.py` (§11.8) | company facts v2 (design only, D-32) |
| Approval engine | employee-keyed (S34) | membership requester, `crm.pipeline.discount`, platform inbox, core profile | R4.5 | §10.7 (approval abstraction and previous-image check), §10.10 | delegation and hierarchy escalation for a membership requester |
| Scheduler | namespace rule, no cancel, finance profile (S35) | quote expiry through the scheduler (R3.6 delivers cancel, the CRM prefix and the core profile) | R3.6, R4.5 | §10.7 (expiry and cancel) | Engage's own scheduled sends (registry J11) |
| Document / private store | none for CRM (S36) | quote and invoice PDFs, notes attachments after R19 | R4.5-B | §10.7 (PDF route), §10.10 | attachments until D-39 |
| Account / auth | memberships, `manager_membership_id`, `employee_id` (S37) | membership spine, enriched events (R3.8, registry J17), approver manager reads, HR-exit mapping | R3.8, R4.5, R4.7 | §10.7, `services/customer/tests/test_owner_deactivation_consumer.py` | any token or gateway change |
| Payment / billing / subscription / metering | tenant billing (DR-3) and merchant collections | merchant side: captures to finance and `finance.invoice` collections; platform side: **none** | R4.3, R4.5-C | §8.10 (captures), §10.10 (collection); `tests/infra/test_crm_ecosystem_coverage_doc.py` (no `crm_*` column in billing, subscription, metering, catalog) | any link from a tenant organization to a CRM party |
| Product service | entitlement read (`services/product/app/api/internal.py:57-79`) | the callee entitlement check of C20 and C64 | R4.4, R4.5 | the unentitled-organization tests of C64 (§10.7) | none |
| Catalog | platform plans and prices | **none** (DR-3) | R4.7 record | `tests/infra/test_crm_ecosystem_coverage_doc.py` | everything |

The document test `tests/infra/test_crm_ecosystem_coverage_doc.py` is the mechanical check: (1) every
`PlatformProductKey` in `frontend/opsgrids-client/src/platform/catalogue.ts` (eleven) appears as a row label
in `docs/crm-ecosystem-coverage.md`, and every platform service of §5.3; (2) every backticked path in the Proof column is a full repository path and exists (a section reference such as
§8.10 is not a path and is skipped); (3) no `services/itsm`, `billing`, `subscription`, `metering` or `catalog` model
file contains `crm_company_id` or `crm_contact_id`; (4) `docs/crm.md` carries the three records of §12.6.

### 12.8 UI

- **Storefront merchant console**: the Enquiries inbox (`frontend/product-storefront/src/enquiries/Enquiries.tsx`)
  shows `crm_status` as the headline badge, stamped "as of {crm_status_at}" (it is forward-only, §12.4.5), with
  the desk status beneath ("Desk: contacted"), a "In CRM" link
  (to the CRM lead or deal when the caller is entitled to CRM), a "Not in CRM" filter and a **Send to CRM** action
  on an unlinked enquiry (disabled with an explanation when `useEntitlement('crm')` is false). The store settings form
  (`src/settings/storeForm.ts`) gains one textarea "Marketing opt-in text (optional)" with the help text "Shown
  as an unticked box on your enquiry form. Leave empty to show none." States: loading, empty, error,
  forbidden, saving; phone-width layout.
- **Storefront buyer bundle** (`frontend/product-storefront/src/theme/render/EnquiryForm.tsx`, the form that
  already has a "Consent line" setting): when `store-context` carries `enquiry_consent_text`, an **unticked**
  checkbox with exactly that text appears under the required fields and its state is posted as
  `marketing_consent`; with no text there is no control. The informational "Consent line" stays as it is.
- **CRM**: the assignment-rule settings screen shows an "On leave today" badge beside each rotation member from
  `unavailable_owner_ids` (for an ORG reader only; otherwise no badge is drawn), and the lead settings screen
  gains the timezone select; the owner picker shows an
  inactive rep as "Inactive" and does not offer them.
- **Dashboard**: no change in R4.7. **ITSM**: none, and a test that its search source stays absent.
- **Every changed or new screen** has loading, empty, error and forbidden states at phone width (the
  harness's 375 px, no horizontal scroll at 360 px); no raw colour or size value (`pnpm tokens:baseline`
  without `--allow-increase` writes nothing); a story for every new `@opsgrids/ui` export and a fixture and
  `MockRoute` in `frontend/opsgrids-visual/fixtures/api/` for every new `GET`; refreshed baselines and no new
  axe violation (`make ui-visual`); the buyer form's checkbox is labelled and keyboard-operable.

### 12.9 Tests

| Area | Tests that will exist |
|---|---|
| Negative authz | `services/storefront/tests/test_send_to_crm_authz.py` (C95: no `storefront.sales.enquiry.write` 403, an OWN grant 403, another organization's enquiry 404, unentitled 402, and 409 `crm_not_active` when lead answers C56 with `skipped_reason = "not_entitled"`); `PUT /storefront/store` without `storefront.store.settings.write` 403 and `enquiry_consent_text` refused to an OWN grant; `PATCH /customers/owners/{id}` without the write code 403; `GET /leads/assignment-rule` answers `unavailable_owner_ids = null` to an OWN-scope reader and the list to an ORG reader; C104 on each of nine services and C94 on deal and lead: no token 401, wrong caller 403, wrong organization claim 403, one of them with the token of the real customer client helper; C100 the same; the public enquiry route refuses `marketing_consent` when no text is offered (422) |
| Cross-tenant | an enquiry, lead, owner, leave row or reference of organization B is invisible and unwritable from A; C104 and C94 count only A's rows; C94 called with the roster id of one rep and the membership id of another counts only the two ids it was given, never a third rep's rows; the two scrub functions called with B's ids and A's organization change zero rows; the ledger write names A only |
| Idempotency and redelivery | `services/storefront/tests/test_enquiry_to_lead.py`: `storefront.enquiry.created` delivered twice and concurrently with `:send-to-crm` (one lead via `external_ref`, one ledger row, one link), C56 answering 200 `skipped_reason = "not_entitled"` (acknowledged, audit row `crm_not_active`, no retry), a timeout (raised, redelivered once); each `crm.lead.*` and `stage_changed` out of order against `crm_status_at`; `leave.applied` then `cancelled` then a stale `applied` (cancelled wins); `membership.revoked` and `employee.transitioned` for one person (one EV25, one task); the follow-up replayed (one row); `erased` replayed for each store (second pass changes zero rows) |
| HR hooks | `services/customer/tests/test_owner_deactivation_consumer.py` (both triggers bypass the guard, an unmapped exit logs and does nothing, an inactive owner gives no event, no automatic reactivation); `test_owner_guard_counts.py` (manual path refuses with open deals or leads, 503 when a count call fails, **and counts a mapped row (membership id), an unmapped row (roster id) and a legacy row** so that sending only one id fails the test); `test_owner_team_employee_id.py` in customer and in lead (`owner_team_changed` writes `employee_id` from the enriched payload and a stale event does not overwrite it) and `tests/infra/test_backfill_owner_team.py` extended (`plan` reports an `employee_id` drift, `apply` closes it, a second `plan` shows zero); `services/deal/tests/test_owner_deactivated_followup.py` (anchor deal, assignee order manager, owner, nobody; zero open deals); `services/lead/tests/test_assignment_availability.py` (skip on leave, ignore when all away, timezone boundary at local midnight, no mapping means available) and `test_rep_unavailability.py` |
| Consent and erasure | `services/storefront/tests/test_enquiry_consent.py` (control offered or not, `true`, `false`, absent; only `true` writes the ledger; text snapshot; no WhatsApp event); `services/lead/tests/test_consent_forwarding.py` (own endpoints only; `storefront` source never forwarded; `ref` written once); erase handlers for storefront and lead against a recursive scan of their rows and the lead audit scrub (`test_erase_handler.py`, `test_scrub_privilege.py`: as the app role a raw `UPDATE lead.audit_logs` fails while the function succeeds, the helpers leave JSON `null`, arrays and scalars unchanged, and the function raises for another tenant's id: the guards of §6.2 against the function R3.7a created); the lead handler applies the **same function** as SP3's administrator erase (a converted lead is left identical by either route) and an unconverted lead of the same person is not touched by the event |
| Derived status | `services/storefront/tests/test_derived_status.py`: the table of §12.4.5 driven through the real handlers, out of order against `crm_status_at`; `status` is never changed by CRM; the two keys `storefront.enquiry.created` and `.status_changed` are in `SUBSCRIBABLE_EVENTS` with committed schemas and the `enquiry-follow-up` recipe passes `test_engage_recipe_triggers.py` |
| Orphan check | `services/customer/tests/test_party_orphan_sweep.py` (`ok`, `missing`, `erased_linked`, `skipped` for a down source, nothing deleted, no id in any log line, `error` level only when `missing > 0`); `tests/infra/test_sweeps_run_somewhere.py` and `test_sweeps_in_api.py` (customer joined `SWEEPING_SERVICES` in R4.3; the orphan sweep is a second supervised task of the same process, so the existing parametrised checks cover it) |
| Migration | round-trip for customer, lead and storefront; the lead and storefront downgrades refuse with rows; `ADD COLUMN IF NOT EXISTS` is a no-op when R3.8 already added `employee_id`; single heads |
| Contract | `test_event_contracts.py` in leave, employee and storefront through the real write paths (and `validate_event_schema` for EV25 in all three reasons); `make contracts-check`, `make openapi-check` |
| Frontend | the buyer form (control present, absent, unticked default, post body), the merchant inbox (headline, link, Send to CRM states), the settings textarea, the assignment badge; `pnpm typecheck && lint && test && build` for storefront and crm; `make ui-visual` |
| Audit | `tests/infra/test_crm_ecosystem_coverage_doc.py` (§12.7) |
| Infra | `test_party_link_env_wiring.py` rows W8, W22, W23, W25, W40, W43, W52; `test_ops_monitor_dlq_queue_sync.py` and `test_ops_monitor_profile_containers.py` for storefront; `test_compose_memory_budget.py` and `test_planned_headroom.py`; `tests/infra/test_internal_client_paths.py` (SP1) registers every internal client call this release adds, the pairs of W8, W22, W23, W25, W40, W43 and W52, each in `CALLS` with a driver that returns the token it sent; `docs/crm.md` is edited for EV25 (`crm.book.owner.deactivated`) in the commit that adds the event, which `test_crm_doc_matches_contracts.py` enforces; EV42's storefront keys (`storefront.enquiry.created`, `.status_changed`) join the obligation of `tests/infra/test_engage_recipe_triggers.py` with the `enquiry-follow-up` recipe, the keys in `SUBSCRIBABLE_EVENTS` in the same commit |

### 12.10 Rollout

**Owner-run, one named ref per deploy, consumers before producers (§4.6 rule 1).** (1) Merge; `make gate` run
locally. (2) Deploy **notification** (the two subscribable storefront keys, EV42), because storefront's producer
change emits `.status_changed` for the first time. (3) Deploy, in any order among themselves, the services
that **consume** an event that customer or lead will produce: **deal** (C104, C94, the reassignment handler
bound to `crm.book.owner.deactivated`), **lead** (migration, bindings, handlers, C104, C94, consent
forwarding; its audit scrub function already exists from R3.7a), **leave** (C100 only; the producer hunk waits
for step 7) and **storefront** (migration, fold F17, handlers bound to `crm.lead.*`, `stage_changed` and
`crm.book.contact.erased`, C104, the public body and `store-context` fields; the buyer form field waits for step
9). (4) Deploy **customer last of these** (migration, bindings, handlers, C104, C94 caller, the sweep): it
**produces** `crm.book.owner.deactivated`, which deal and lead consume, and `crm.book.contact.erased`, which
lead and storefront consume, and an event with no bound queue is dropped, so any other order loses events that
no tool replays. (5) The owner **re-runs R3.8's `owner_team` backfill** (`tools/backfill_owner_team.py`,
H10, extended by this release, §12.2; a gated `apply`) so `employee_id` is filled for existing memberships:
until it has run, an HR exit is "unmapped" and does nothing (§13 step 11). (6) Owner-run **H13**
`tools/backfill_rep_unavailability.py --service lead` (`plan` is read-only and the default, `apply` is gated like
H1): pages C100 and writes through the fold function, so it is safe beside live events. (7) Deploy the HR
producer hunks (**leave** and **employee**), so the events bind into queues that already exist. (8) Owner-run
**H14** with `--service lead` and `--service storefront`. (9) Publish the SPAs and the buyer bundle last
(`make edge-web APP=storefront`, `APP=crm`) from the checked-out merged main. (10) The owner confirms
`RUN_SWEEPS_IN_API` is not overridden to false for customer and reads the first `party.orphan_check.summary`
lines. (11) Docs: `docs/crm.md` (DR-1 to DR-3, and EV25 in the commit that adds the event), `docs/crm-ecosystem-coverage.md`;
the recipe-trigger and internal-client registrations of §12.9 land in the commits that add the keys and clients.
No permission changes, so no reconcile.
Compose lines: storefront block `CUSTOMER_INTERNAL_URL`, `LEAD_INTERNAL_URL`, `NOTIFICATION_INTERNAL_URL` (W8,
W22, W25); lead block `NOTIFICATION_INTERNAL_URL` (W23) and `LEAVE_INTERNAL_URL` (W52); customer block
`STOREFRONT_INTERNAL_URL` (W40), `LEAD_INTERNAL_URL` (W43) and `RUN_SWEEPS_IN_API: ${RUN_SWEEPS_IN_API:-true}`.

*Memory.* One fold, storefront: `containers.storefront.kind` `api` → `api_with_worker`, a `storefront-worker`
rollback container (profile `split-workers`), `PROFILE_DLQ_QUEUES["storefront"]`, the RUNBOOK literals, all in one
commit: ten profiles **3752 MB** (97.2%) and **3949 MB** with `admin` (102.3%) with no insights, **3851 MB**
(99.7%) and **4048 MB** (104.8%) with it (§11.9). The owner raises `EDGE_BUDGET_ACCEPTED_MB` to that figure
before the deploy. Customer's sweep adds no container and a few hundred kilobytes (inference, measured in the PR).

### 12.11 Rollback

Code rolls back to the previous images (every change is additive); `RUN_CONSUMERS_IN_API=false` plus
`split-workers` is the documented fold rollback. **Not reversible:** an owner deactivation (re-activate in the
Team screen), a ledger row written for a ticked box (append-only; the answer to a mistaken row is an opt-out
event), an erase, a created lead. *Stop conditions:* an EV25 for a person who is still a working rep (a
mapping error: stop and clear the HR hook by unbinding `hrms.people.employee.transitioned` from
`customer.events`); a lead created twice for one enquiry; a ledger row for a `false` consent; an unassigned lead
count that rises after the availability change; `storefront.events.dlq` or `lead.events.dlq` depth rising; any
orphan-sweep write; **a rollback of customer, deal, lead or storefront while its queue carries live traffic
without the catch-up of the table below ready**.

**What a code rollback loses (§4.7).**

| Consumer (queue) | Events a rollback discards | Catch-up |
|---|---|---|
| `deactivate_owner_for_membership` (`customer.events`: `core.membership.revoked`, `hrms.people.employee.transitioned`) | an HR exit or a revoke while the old image runs: the roster entry stays active | **forward-only, with a hand repair:** a person deactivates it in the Team screen; that is why the rollback is a stop condition and not a lever |
| `queue_reassignment_task` (`deal.events`: `crm.book.owner.deactivated`) | the reassignment follow-up of a departure | **forward-only:** a reminder; the departed rep's deals keep their owner and the Team screen's open-records counts still show them |
| `fold_leave` (`lead.events`: leave applied, cancelled) | availability rows | **catch-up:** H13 (C100) |
| `evict_owner_caches` (`lead.events`: EV25) | a cache eviction | **self-healing:** the in-process caches expire on their own |
| `forward_capture_consent` (`lead.events`: `crm.lead.created`) | the ledger row of a lead captured while the old image runs | **forward-only, and the safe side:** no row means no opt-in, so the address stays marketing-blocked (silence is not consent) |
| `erase_converted_leads` (`lead.events`), `erase_enquiries` (`storefront.events`): `crm.book.contact.erased` | erasures while the old image runs | **catch-up:** H14 `--service lead`, `--service storefront` |
| `send_enquiry_to_crm` (`storefront.events`: `enquiry.created`) | enquiries created while the old image runs are not sent to CRM | **catch-up:** `:send-to-crm` per enquiry; the inbox's "Not in CRM" filter lists them |
| `derive_enquiry_status` (`storefront.events`) | status updates | **forward-only:** `crm_status` is a stamped display, repaired by the next event for that lead or deal (§12.4.5) |

### 12.12 Acceptance criteria

- [ ] `lead.rep_unavailability`, `lead.lead_settings.timezone`, `owner_team.employee_id` (customer and lead), the storefront consent and link columns and their indexes exist as specified; the three migrations round-trip; the lead and storefront downgrades refuse with rows.
- [ ] `hrms.time.leave.applied` and `hrms.people.employee.transitioned` validate against their committed schemas through the real write paths and their `$comment` lines are removed.
- [ ] An HR exit and a membership revoke each deactivate the mapped roster entry once, stage one `crm.book.owner.deactivated` with the right `reason`, reassign nothing, and bypass the open-records guard; the manual path refuses while deals or leads are open and answers 503 when a count is unavailable.
- [ ] **`owner_team.employee_id` is written**: the extended `owner_team_changed` handler and `tools/backfill_owner_team.py apply` fill it, and after the owner's re-run, for every organization, the reps mapped to an employee (an `owner_team` row with an `employee_id` whose membership is active) equal the active members that carry an employee link in C57's answer; a second `plan` shows zero drift including `employee_id`.
- [ ] The manual deactivation guard counts open deals and leads under both ids of the roster entry (mapped, unmapped and legacy rows), and answers 503 when a count is unavailable.
- [ ] A departed rep with open deals gets exactly one system follow-up per day, addressed to their manager or the organization owner, whose title contains a number and no name.
- [ ] Round-robin skips a rep on approved leave today in the organization's timezone, ignores availability only when every active rep is away, and treats an unmapped rep as available; H13 catches up leave approved before the queue existed.
- [ ] A storefront enquiry becomes exactly one lead (via `external_ref`), is linked to an existing contact and company when resolvable, and shows `crm_status` derived per §12.4.5 while `status` stays the merchant's.
- [ ] A ticked marketing box writes exactly one `opted_in` ledger row with the enquiry as evidence; an unticked box and a store with no text write none; no WhatsApp or SMS consent is ever written; a lead from the lead service's own endpoint with a granted consent is forwarded once and a `storefront` lead never.
- [ ] After `crm.book.contact.erased`, the contact's storefront enquiries and converted leads hold no name, e-mail or phone (the lead through the same function as SP3's administrator erase), the lead audit rows hold none of the erased values, and a second delivery changes nothing; a lead that never converted is untouched by the event and is erased only by SP3's route.
- [ ] `customer.events` is deployed after deal, lead and storefront (it produces EV25 and EV18), and notification before storefront's producer change; `storefront.enquiry.created` and `.status_changed` are subscribable and the `enquiry-follow-up` recipe passes `test_engage_recipe_triggers.py`; `crm_status` shows "as of" and the "Not in CRM" filter lists unlinked enquiries.
- [ ] The nightly sweep reports `ok`, `missing`, `erased_linked` and `skipped` per organization and source, writes and deletes nothing, and logs no id.
- [ ] `docs/crm.md` carries DR-1 to DR-3, `docs/crm-ecosystem-coverage.md` carries every row of §12.7, and `tests/infra/test_crm_ecosystem_coverage_doc.py` passes.
- [ ] The running totals of §12.10 are in RUNBOOK, `memory-budget.json`, compose and ops-monitor in the same commit as the storefront fold; the infra tests of §12.9 are green.
- [ ] Every internal client call this release adds (W8, W22, W23, W25, W40, W43, W52) is registered in `tests/infra/test_internal_client_paths.py` with a driver that returns the token it sent; `docs/crm.md` carries EV25 and `test_crm_doc_matches_contracts.py` passes; `storefront.enquiry.created` and `.status_changed` are covered by `test_engage_recipe_triggers.py` with the `enquiry-follow-up` recipe.
- [ ] The starter recipe's `purpose: "marketing"` (EV39) has been re-decided with the consent design of §6.5, by R3.7b or by this release, whichever first let a converted lead carry evidence, and the answer is written down (§12.4.6).
- [ ] `lead.scrub_lead_audit` is not created by this release: its migration (R3.7a's) is a prerequisite, and R4.7 only calls it and asserts its two guards.
- [ ] Every changed or new screen has loading, empty, error and forbidden states at phone width, no new token-baseline count, a story per new `@opsgrids/ui` export, a fixture and `MockRoute` per new `GET`, and no new axe violation.
- [ ] `make check` and `make gate` pass locally.

## 13. Owner-run steps

Everything below is done by the owner (or the owner's named operator), never by an agent. Agents write and
test the tools and the migrations; they never read production counts, run a production apply, edit the box
`.env`, publish a bundle or run the production smoke. Every deploy is manual, names a ref, runs after a local
`make gate` (Actions billing is blocked), and **stops on the first failure**; no MinIO rotation, session cap or
production configuration edit happens without the owner's approval. Dry runs are read-only and are the default
of every tool below.

| # | Release | Owner action | What the owner reads before applying |
|---|---|---|---|
| 1 | R4.1, before | Confirm `tools/backfill_pii_indexes.py` (H8) has indexed `contacts.email_index` for every organization with CRM data | its `plan` output: zero rows with a NULL index and a present source value |
| 2 | R4.1 | Answer D-33 and D-27 (with legal) and accept the ADR `crm-consent-and-erasure` before R4.1 is called done; answer SP2's D-26 (default no: C45, C47 and C53 stay on the plaintext e-mail) | §6.5's coverage table, line by line |
| 3 | R4.1 | Deploy notification, customer, then deal, lead and finance; `make edge-reconcile PRODUCTS=crm` dry run, then `APPLY=1` off-peak (E24); publish the SPAs last | `\df+ customer.scrub_contact_audit` (`SECURITY DEFINER`, owner); a synthetic erase in a test organization and its `audit_rows_scrubbed` count |
| 4 | R4.2 | `tools/sync_engage_contacts.py` (H9): `plan`, then gated `apply`, per organization; then `tools/reapply_contact_erasures.py` (H14) with `--service notification` | matched, created and skipped counts; confirmation that H9 wrote no consent event; the H14 counts |
| 5 | R4.3 | `tools/link_finance_customers.py` (H2): `plan --out`, review each `matched`, `ambiguous`, `unmatched` row per organization, then `apply` for the approved rows; `tools/backfill_party_rollup.py` (H4): `plan`, `apply`; H14 with `--service finance` and `--service operations`; answer D-34; decide each payment the migration left with `currency NULL` (a mixed-currency payment, counted in the migration log). **The tenants' administrators, not the owner, set each organization's tax rate and switch on automatic invoices** in the Billing defaults card | the plan files (they hold names: kept off the repo and off shell history); the rollup parity counts; the H14 counts; the count of payments left `NULL` |
| 6 | R4.3 | Flip `ORDER_INVOICE_CHAIN_MODE` from `log_only` to `on` in the box `.env` **only after a log census** shows, over a representative period, that the organizations that opted in (`order_invoice_chain_enabled`) logged `orders.invoice_chain.would_request` with `source=row`, the rate they chose and no line without an HSN, and that none logged `source=default`. An organization that has not opted in is skipped in `on` mode whatever the variable says (`tax_rate_unset`, `chain_not_enabled`), so the flip changes nothing for it; the `CREDIT_CHECK_MODE` flip from `warn` to `refuse` only if decided; reconcile E23 and E27 | the `log_only` lines (`would_request` and `would_skip`, with ids, amounts and reasons) |
| 7 | R4.4 | Raise `EDGE_BUDGET_ACCEPTED_MB` to **3947** before the support fold deploys with `admin` on; answer D-17; `tools/link_support_tickets.py` (H11) `plan` and gated `apply`; H14 with `--service support` | the printout of `memory_budget_totals.py` for the exact figure; the H11 plan's ambiguous rows; the H14 counts |
| 8 | R4.5 | Review the scaffold deletion; reconcile E25 and E26 (dry run, then `APPLY=1`); answer D-18, D-19, D-30; **accept ADR 0042 `approvals-for-tenants-without-hrms` before the approval deploy**; before R4.5-B confirm that R19 landed **in ADR-0009 Q1's shape** (internal put and `:presign`, `deal` and `finance` on the allow-list, no browser upload, not the `sprint/private-uploads` contract) and raise the accepted ceiling to the figure printed with the PDF library's measured delta priced | the reconcile dry run (which templates pick the codes up); the ADR; `document`'s route list; the printout of `memory_budget_totals.py` |
| 9 | R4.6-A | Raise the accepted ceiling to **3948**; answer D-16 and D-36; run `tools/backfill_campaign_attribution.py` (H12) `plan`, then `apply` | the plan's lead and deal counts per organization |
| 10 | R4.6-B | **Before PR 3 merges**, a read-only check on every shared database (the box, any staging and any developer database other people use): `\dn insights`, or `SELECT version_num FROM insights.alembic_version`. Record "absent everywhere" (PR 3 then edits `ad6f07c940d8` in place) or the databases that carry any of `048dbce2f957`, `ad6f07c940d8`, `a7c4e1f9b3d2` (PR 3 then ships the forward revision and the in-place edit is not made; with no recorded answer the forward revision is the default). Answer D-12 and D-13; raise the ceiling to **4047** (4046 if A has not shipped; the preflight prints the exact figure); after the deploy run `bin/backfill --verify` per organization; answer D-32 | the check's output per database; the `--verify` report; the first hour of `insights.events.dlq` depth |
| 11 | R4.7 | Raise the ceiling to **4048** (**3949** without insights); re-run `tools/backfill_owner_team.py apply` (H10, **extended by R4.7 to write `employee_id`**; gated like the purge tools) and read the second `plan` for zero drift and the mapped-rep count of §12.12; `tools/backfill_rep_unavailability.py` (H13) `plan`, `apply`; `tools/reapply_contact_erasures.py` (H14) with `--service lead` and `--service storefront`; answer D-31 | the plans; the H14 counts; the mapped-rep count against C57's active members with an employee link; the first `party.orphan_check.summary` lines |
| 12 | every release | Merge (squash) after review; deploy with a named ref; verify (the owner's smoke, not an agent's); publish a bundle only from the checked-out deployed `main` (`edge-web` builds the local checkout) | the release's acceptance list |
| 13 | when a summary shows `missing` | `tools/check_party_orphans.py --org <uuid> --list` | the ids, printed for that one organization only |

## 14. Risks specific to SP4

| # | Risk | Effect | Mitigation in this design |
|---|---|---|---|
| 1 | An erase that stops at CRM's own copy | a person's data survives in a connected product | per-store coverage table and per-release handlers (§6.5); H14 catch-up at each first deploy; the dialog names only the stores the deployed release covers (§6.7); the residue (free text, append-only deal text, backups) stated plainly as D-27 |
| 2 | The CRM's money mirror disagrees with finance | a rep quotes a wrong receivable | state-not-counters facts (SD-5); a golden fixture shared with finance (§8.7); `as_of` on every figure; the nightly overdue recompute; per-currency, never summed |
| 3 | The first production order invoices with a 0% tax rate and enters e-invoice auto-filing | wrong tax on a legal document | the chain ships dark and `log_only` by default (SD-8) and, **in `on` mode, emits only for an organization that has a settings row and opted in** (`tax_rate_unset` and `chain_not_enabled` are skipped), so the box-wide flip cannot invoice an organization at the hard-coded 0%; finance derives supply type, place of supply and due date; R4.3-S gives the rate a writer, used by the tenant's administrator; the flip is an owner `.env` edit after a log census |
| 4 | Lifecycle automation changes a headline number | "Total Customers" jumps when companies are promoted | an **organization setting, default off** (D-34), limited to the programme's one trigger unless the owner extends it; H4 and replays never promote; one atomic claim promotes a company at most once (`lifecycle_promoted_at`), never over a manual value; the dashboard slot is documented in the release note |
| 5 | A public form records another person's marketing opt-in | unsolicited marketing | unticked box, the merchant's own text, evidence naming the enquiry, unsubscribe on every message; confirmed opt-in is D-31 |
| 6 | Memory: three folds, the insights container, the approval profile move and the PDF library on a box already at 102% | a refused deploy or an OOM | one MB per fold priced in each release (§11.9, §12.10); the owner raises the accepted ceiling by hand; insights ships only after D-13; the approval move changes only boxes with `crm` and no `hrms` (§10.8); the PDF library has its own risk (16); no other new container |
| 7 | An event published before its queue existed is lost | a projection silently incomplete | consumer before producer (§4.6); a backfill and an owner tool for every projection (H4, H9, H12, H13, H14); `:send-to-crm` for enquiries; the same loss on a **code rollback** is stated per consumer in each rollback section, with a catch-up, a forward-only reason or a stop rule (§4.7, SD-31) |
| 8 | Approval changes under HR | the HR approvals list fails for an organization that raised a CRM request | additive membership columns with surrogate employee ids (SD-13); a previous-image serialization test; the stop rule against rolling back with pending requests (§10.9) |
| 9 | The insights merge: three migration heads, hunks that overlap the CRM releases, unshipped migrations that may have run somewhere | a failed deploy or a wrong figure | fixed merge order, main wins on shared files, single-head test, an empty-database upgrade test and a forward-revision test, parity tests rerun, the export routes re-cut name-free, and the in-place edit only after the owner's database check (SD-22, §13 step 10, §11.4.2) |
| 10 | An HR mapping error deactivates the wrong rep | a working rep cannot be assigned work | the mapping is `owner_team.employee_id` only; an unmapped exit does nothing; no reassignment, no login revoked; stop condition and an unbinding lever (§12.11) |
| 11 | The 360 and company page slow down or break when a partner product is down | a blank page | each section is failure-isolated with a 2-second budget and named in `unavailable` (§4.4); the projection tier needs no outbound call |
| 12 | A dependency outside SP4 slips | a release cannot ship | R4.5's PDFs and attachments wait for R19 and its quotes for R3.6; R4.6-D for R3.3; R4.7 for R3.8 (§17 deviation 2): each has an independent stage so the rest still ships |
| 13 | Legacy fields that carry a person's name | a name in a store no erase reaches | `deal.name` of a lead-converted deal (S13), `full_name` on `crm.book.contact.created`, finance `name`: frozen, never read by a new consumer, insights drops them (SD-22), the rest listed as D-27 residue |
| 14 | Marketing's one-currency assumption | a mixed total in the Overview | attributed money is added only in the campaign's currency (SD-21); the assumption is recorded and unchanged (`services/marketing/app/schemas.py:78-87`) |
| 15 | The hosted-checkout seam is used for invoices | the first merchant collection against a legal document | staff-assisted only, `finance.invoice` reference, capture parked until applied exactly once (SD-9, SD-15); no public page (D-30) |
| 16 | A PDF library with the wrong licence or weight | a refused `make licenses`, or a resident-set jump that pushes the box over its MemTotal (worst case 3871 to 3891 MB with insights, §11.9) | candidate named and checked by the policy (`tools/licenses.toml`); both measurements recorded in the PR; a new budget kind `api_with_worker_render` for `deal` and `finance` only; R4.5-B deploys only after the owner raised the accepted ceiling, with the preflight as the stop condition (§10.4, §10.8) |
| 17 | The orphan check cries wolf or leaks | log noise, or ids in logs | three outcomes and counts only; `error` only when `missing > 0`; ids appear only in the owner-run `--list` |
| 18 | The coverage document rots | a false audit | `tests/infra/test_crm_ecosystem_coverage_doc.py` (SD-27) |
| 19 | Company-owner alerts cannot be switched off per organization | a noisy bell for an organization that reassigns many companies | `customer` has no settings table at R4.2 (`notify_assignments` lives in deal and lead only); the alert is one fixed generic sentence, in-app only, with no consent regime (§7.4); R4.3 adds `customer.customer_settings` (B83), which is where a `notify_assignments` switch for company owners would go in a later change |
| 20 | An audit copy of a contact's personal data in a schema that has no scrub function | a name or address survives an erase | every schema known to copy it has one (customer, notification, finance, lead: §6.5); each release's erase test scans the audit rows of that schema, so a new copy fails a test |
| 21 | The audit scrub fails on the rows it exists to scrub, or trusts its argument | an erase that rolls back with a 500 for nearly every contact, or one tenant's audit rows rewritten through another's id | helpers act on JSON objects only (an absent snapshot is stored as JSON `null`, S49), the function refuses an organization other than the session's tenant (§6.2), and tests seed rows through the real `record_audit` and call it with a neighbour's id |
| 22 | A discount decision or an approval request is lost by a rollback of approval or deal | a quote waits for ever in `pending_approval` | the stop rule, and "Resend request" re-publishes EV23 so approval opens a missing request or re-stages a decision that deal never applied (§10.9); a past-date quote cannot be accepted even if its expiry event was lost |
| 23 | A phone number links an order to a stranger's company | a wrong buyer, GSTIN and place of supply on a tax invoice | a phone match is a suggestion only; the link comes from an explicit choice, an operator's confirmation or the opted-in create (SD-28, §8.4.2) |
| 24 | A CRM-only rep can label marketing e-mail transactional | an e-mail to a contact who never opted in | the server derives the purpose from a template category that only an administrator sets, audited (SD-29, §7.4) |
| 25 | A customer route answers an organization that never bought CRM | master data created or read for an unentitled tenant | the five new customer routes re-check the `crm` entitlement through product-service and answer 403 `crm_not_active` (§4.2) |

## 15. Out of scope

In addition to the programme's list (P§5: two-way e-mail or calendar sync, telephony, AI scoring or
drafting, a second workflow engine, full CPQ, custom objects, tiering CRM modules by plan, a new container per
feature, a CRM-local BI service, deleting the `eventbridge` scaffold) and §1.2:

- a customer-facing payment page and refund accounting (D-30, D-28);
- confirmed (double) opt-in, and any inference of WhatsApp or SMS consent from an e-mail opt-in;
- multi-touch or retroactive attribution, spend ingestion, and a real weekly performance series for a campaign;
- a vendor party, an ITSM or tenant-billing link (DR-1 to DR-3), and a bulk merge of duplicate companies;
- automatic reassignment of a departed rep's records, and revoking a login on an HR exit;
- notes attachments before R19 is built and D-39 is answered (B57, C81 to C83), and e-sign on a quote;
- the build of insights v2 (§11.5), of insights slice I9 (HR facts), and of any Support, ITSM, Marketing,
  PulseBoard or Engage metric in insights (ADR-0020 non-goals);
- a bulk import of enquiries that predate R4.7 (the merchant's `:send-to-crm` is the path);
- per-organization feature flags (the platform has none: staged rollout is admin-first role templates and an
  organization setting that defaults off);
- fixing Engage's own scheduled sends under the scheduler's ownership rule (registry J11), and the Marketing
  Overview's one-currency assumption;
- the `projects` kickoff of a won deal before D-17 is answered, and time-and-materials invoicing from
  timesheets;
- a CRM follow-up task created by an Engage automation on a storefront enquiry (the enquiry event carries no
  CRM subject, §12.5), and a bulk refresh of `storefront.enquiries.crm_status` for rows whose last lead or deal
  event predates the fold (it needs a lead and a deal read route for a display the next event corrects, and the
  inbox shows "as of" and the "Not in CRM" filter instead, §12.4.5);
- linking an order or an enquiry to a company by a phone number alone, and an automatic merge of two contacts
  that share a phone (a phone match is a suggestion for a person to confirm, SD-28);
- a per-organization switch for company-owner assignment alerts (risk 19), and an e-mail or WhatsApp form of
  any assignment alert (they are in-app only, with no consent regime, §7.4);
- an erase or delete action for a storefront enquiry that has no CRM contact, and a storefront consumer of SP3's
  `crm.lead.erased` (D-27 residue, §6.5).

## 16. Registry amendments

This section no longer carries a table. The amendments this document proposed (schema, routes, events and
permissions, settings, wiring, client hooks, tools, documents and decisions) have been **folded into the
registry**: its section J.2 ("Folded amendments: the handle map and the ledger") lists every source row of the
first draft of this section and says where it went, and the rows themselves are in registry sections B to I and
K, under the final handles written throughout this document. Nothing here is left to apply to the registry by
hand. The handle map of J.2 gives the old and the final handle of every renumbered row, for a reader who holds
an earlier draft: the handles this document moved to leave SP3 its numbers (now B79 to B82, C104, G24, G25 and
H14), SP3's unnumbered placeholders (now B85, B86, C108, C109 and EV53) and the one mechanism for system in-app
alerts that SP3 and this document had each drafted under the same handle (now EV54; this document's
`BUILTIN_CRM_ALERTS` stays withdrawn).

**Rejected, in part: C56 (registry J27).** The consent rule is in the registry: a `consent` block with
`source = "storefront"` is stored on the lead as evidence and never forwarded by lead (C56, B39). The 403
`crm_not_active` this document proposed for C56 when the organization is not entitled to `crm` is **rejected**.
SP3 owns C56 and fixed its answer as a **200 with `skipped_reason: "not_entitled"`** (beside `disabled` and
`rate_limited`), because Engage's best-effort stranger step reads `skipped_reason` to cache a definite "no" for
five minutes, and a 403 would read as a refused token, which SP3 treats as an unreachable lead and does not
cache. The 403 `crm_not_active` stays where this document owns the route: customer's C50 to C53 and C89 (§4.2).
§12.4.4 and C95 follow the rejection: storefront's handler maps `not_entitled` to its audit row `crm_not_active`
and acknowledges, and `:send-to-crm` answers 409 `crm_not_active`. No other row of this document was rejected.

**Decisions.** D-27 to D-34 are recorded in registry §K under the ids used here (the STOP and unsubscribe rule
this document first drafted as D-26 is D-33, because SP2's D-26 is a different question; D-27 is shared with
SP3). Five registry questions are the decisions D-35, D-36, D-37, D-39 and D-40 of §2.1, each with its registered
default; D-36, first or last touch for campaign attribution (default first touch, write-once), blocks R4.6-A.
The D-39 default, that deal binds `crm.book.contact.erased` unconditionally so that a later attachment purge joins
an existing handler, is registry EV44. The registry's memory row with the five-fold arithmetic is the amendment
text of D-13: one decision under two ids, kept under the lower.

## 17. Deviations from the programme spec

Each is explicit, with the evidence that forced it. Items 1 to 11 and 13 change no decision the owner has
already made. **Item 12 is different: it records where this design cannot meet the owner's own decision M2** ("each
release is reversible on its own", P§3), with the stop rule that stands in for the guarantee, so the owner
accepts or refuses it knowingly when approving this document.

1. **R4.1 needs R3.1, not only R2.x.** P§10 lists "R2.x; R4.1 before every other R4". R4.1's routes read
   `company_tax_ids`, create phone-only contacts, match by `email_index` and add a caller to C45 and C46, all of
   which R3.1 creates or hardens; the registry puts `erased_at` after R3.1 in the customer chain (§B.1).
   *Effect:* R4.1 waits for R3.1 and for C77's soak (§6.1; registry J30 returns the same prerequisite against P§10).
2. **Prerequisites beyond P§10's "Needs".** R4.3 needs R3.2 (the `/api/v1/companies` prefix and the `ids`
   filter behind C24 and `useCompanyLookup`) and R3.3 (`formatMoney` and `Customer360View`, which render the new
   sections); R4.4 needs the same two and R3.6 (`deal.follow_ups`, for the automation task); R4.2 needs R3.6
   (EV54's mapping, which the assignment alerts extend, §7.1); R4.5's expiry needs R3.6 (cancel, the CRM
   scheduler prefix, the scheduler in the core profile: A16, A17, F10) and its PDFs and attachments need R19,
   which is outside the programme and unbuilt (S36); R4.6-D needs R3.3's palette wiring; R4.7 needs R3.8
   (`owner_team`, lead's fold). *Effect:* R4.2, R4.3 and R4.4 wait for the SP3 release they need; the PDF and
   attachment half of R4.5, R4.6-D and R4.7's HR half each have their own stage, so the rest of their release
   still ships (§7.1, §8.1, §9.1, §10.1, §11.1, §12.1). Registry J30 returns the same list against P§10.
3. **Payment links are staff-assisted only.** P§10 lists "payment links" in R4.5. No public payment surface
   exists, the provider seam has hosted-checkout orders only (`services/payment/app/providers/base.py:394-480,
   629-643`) and a tokenized public page is a new anonymous money surface. *Effect:* R4.5-C lets a staff member
   collect an invoice online; the customer-facing page is D-30, default deferred (§10.4; registry J31, C103).
4. **Memory.** P§8 D-13 assumed no increase. Registry J22 and L17 show every consumer fold is +1 MB and the
   programme has five; SP4 adds three (support, marketing, storefront), the insights container (+99 MB) and, in
   R4.5-B, a PDF library resident in deal and finance (not measured: 10 to 20 MB each by inference).
   *Effect:* the owner raises `EDGE_BUDGET_ACCEPTED_MB` by hand, release by release, to the figures in §11.9 and
   §12.10; insights ships only after D-13; the PDF library is priced through a new budget kind
   `api_with_worker_render` after its measurement, and with insights the ten-profile worst case (3871 to 3891 MB)
   passes the box's 3862 MB MemTotal, which only a raised ceiling covers (§10.8, §11.9).
5. **`source_ref` is `marketing_campaign_id`.** P§6's Marketing row says "`source_ref` and UTM on lead and deal";
   `source_ref` already means a request key in approval, attendance and finance (registry J8). *Effect:* the
   campaign link is `marketing_campaign_id` and the enquiry's idempotency key is `external_ref`.
6. **Enriched membership events are R3.8.** P§6's Account row dates them "R2.2 to R2.7"; nothing consumes them
   before R3.8's `owner_team` (registry J17). *Effect:* R4.5's approver search and R4.7's HR mapping read what
   R3.8 provides.
7. **The HRMS trigger set is widened.** P§6 names `core.membership.revoked` for offboarding. At HEAD an HR exit
   does not revoke a membership: the account consumer binds only product-activation events
   (`services/account/app/consumers.py:192-194`) and `core.membership.revoked` is staged only by the admin
   revoke (`services/account/app/services.py:920-970`). *Effect:* customer also consumes
   `hrms.people.employee.transitioned`, and EV25's `reason` gains `employee_exited` (SD-17, SD-26; registry
   J32, EV25, EV43).
8. **Two fact tables beneath the projection.** P§4.3 names `party_rollup` and `party_timeline`. Counters
   cannot produce `overdue_minor`, cannot reconcile with a backfill and lose a payment that arrives before its
   invoice event (SD-5). *Effect:* `party_documents` and `party_payments` (B79, B80) hold the state and the
   rollup is derived from them.
9. **Insights: v2 is a design, and names are dropped.** P§6 says "cross-product facts keyed by company in a
   v2"; R4.6 records the design and builds nothing (D-32). The accepted insights data model stores a deal name and
   a company name as "business labels" (`docs/insights/data-model.md:175-199`) and the unmerged q2 branch
   implements it, but a lead-converted deal name holds a person's name (S13), against ADR-0020's own non-goal
   (`docs/adr/0020-insights-projection.md:682`). *Effect:* R4.6-B stores no names (SD-22; registry J32, B72).
10. **The storefront's status is derived beside the merchant's own.** P§6 says "status derived from the linked
    lead/deal when CRM is entitled". Overwriting the stored status would reverse a merchant's manual move and
    make the committed `status_changed` contract false. *Effect:* `crm_status` is a second field and the
    headline (SD-23; registry J32, B78).
11. **Registry chain and tests.** The first-draft registry's "R4.7 (no lead migration)" did not hold, and its
    bindings for EV45, EV49 and EV51 were narrower than R4.6 and R4.7 need; the registry now carries both
    (the §B.3 chain note, and the rows EV45, EV49 and EV51). *Effect:* amendments only, already folded
    (registry J.2); no programme decision moves.
12. **M2 is met for code, schema and configuration, and not for everything.** P§3 decision M2 says each
    release is reversible on its own. This design keeps it where it can: every migration is additive, every
    previous image tolerates the new columns, and each release's first lever is a mode or a setting that loses
    nothing (or `RUN_CONSUMERS_IN_API=false` with `split-workers`, which moves a consumer instead of removing
    it, §4.7). It cannot keep it for six things, each named in its release's rollback section with the rule
    that replaces the guarantee:
    (a) **an erase** is irreversible by design, and the previous customer image does not know `erased_at`, so
    **customer is not rolled back once any contact has been erased** (§6.10);
    (b) **a tax invoice the chain issued, and a company the lifecycle setting promoted,** survive a rollback
    (an invoice is a legal document corrected only by a credit note; a promotion keeps `lifecycle_promoted_at`),
    so the levers are the organization's own switches and the box mode, and the stop conditions watch the
    first organizations that opt in (§8.9);
    (c) **approval and deal are not rolled back while a quote is in `pending_approval`**: the previous image has
    no handler for the request or the decision, so the quote waits until the new image returns, and
    "Resend request" is the catch-up (§10.9);
    (d) **approval's move from the `hrms` profile to core** adds a 99 MB container to every box that runs `crm`
    without `hrms`, and reverting the profile line stops the only consumer of EV23 for those tenants (the ADR
    gate of D-18, §10.8);
    (e) **events a code rollback discards** (S46: a bound queue outlives the handler, the message is
    acknowledged and dropped, and a published outbox row cannot be replayed) are recoverable only where a
    catch-up exists (H4, H9, H12, H13, H14, `reconcile_online_captures` over C105, "Resend request",
    `:send-to-crm`); elsewhere the loss is stated as forward-only with the reason it is safe, or a stop rule
    forbids the rollback (§4.7, SD-31);
    (f) **an in-place edit of the unshipped insights migration** (SD-22) is made only after the owner's
    database check shows it has run nowhere; otherwise a forward revision ships (§13 step 10).
    *Effect:* every rollback section ends with a table of what a rollback loses and its stop conditions. The
    owner accepts these six exceptions, or refuses a release that needs one, by approving this document; the
    alternative to accepting (a) to (d) is not building the release that causes it.
13. **SP1's guards bind SP4's releases a little further than SP1's hand-off list says.** SP1 §9.2.2 names the
    SP4 releases that edit `docs/crm.md` (it lists events only), the `AHEAD_OF_PRODUCER` pins, the recipe-trigger
    test and `tests/infra/test_internal_client_paths.py` (R4.3 to R4.7). Two facts make the list incomplete.
    `test_crm_doc_matches_contracts.py` also ties the permission table to the manifest, so a release that adds a
    CRM code edits its row in the same commit (R4.1 for E24, R4.3 for E23, R4.5 for E25 and E26). And R4.1 adds
    one internal client call of its own, customer's entitlement read (the customer pair of W36), so it registers
    it too. *Effect:* §6.8, §8.7 and §10.7 carry the extra obligations beside SP1's own; no programme decision
    moves.

