# ADR-0038 — CRM programme decisions

Status: **proposed** · Date: 2026-10-07

Builds on [ADR-0000](0000-phase-0-decisions.md) (§5, §6, §7, §8, §10, §11),
[ADR-0006](0006-approval-engine.md), [ADR-0007](0007-permission-rollout-to-live-tenants.md),
[ADR-0008](0008-membership-employee-link.md) (Q5),
[ADR-0013](0013-self-serve-products-and-the-one-box-deployment.md),
[ADR-0015](0015-scheduler-and-not-before-delivery.md),
[ADR-0017](0017-crm-lead-service-and-pipeline-scaffold-removal.md),
[ADR-0020](0020-insights-projection.md), [ADR-0021](0021-consent-ledger.md),
[ADR-0022](0022-bulk-import-export.md), [ADR-0023](0023-member-step-up.md),
[ADR-0035](0035-list-prices-2026-10-06.md) and
[ADR-0036](0036-removing-test-organisations.md). **Amends** ADR-0017, ADR-0008 (Q5) and ADR-0020
(§6 and Risk 1) by dated notes only: no accepted decision is rewritten, and the one in-place text
edit is a corrected citation in ADR-0017 (`domain.py:38` is `domain.py:21`).

**This ADR is a proposal.** Every default below is the programme's recommendation and stays one
until the owner answers it in the State column. A document that wants to name a default says
"proposed in ADR-0038 (pending D-n)"; it never states one as a settled outcome.

## Context

The CRM overhaul is one programme of four sub-projects: foundations and cleanup (SP1), platform
backbone (SP2), core revamp (SP3) and suite connection (SP4). Its contract is
[the programme design](../superpowers/specs/2026-10-07-crm-overhaul-programme-design.md) (cited
`P§n`), the names every release must use are in
[the interface registry](../superpowers/specs/2026-10-07-crm-overhaul-interface-registry.md), and
the first sub-project is
[the foundations design](../superpowers/specs/2026-10-07-crm-1-foundations-and-cleanup-design.md).

The facts behind the decisions are `P§3` F1 to F11 and are not restated here. Three of them
correct the record this ADR amends: F1 (OWN scope is empty for a tenant without HRMS), F2
(ADR-0008's migration recipe does not match the CRM's owner data) and F3 (the orphan cleanup
ADR-0017 sketches is unsafe as worded).

This ADR records the owner's answers once, one row each, so that a later release checks one cell
instead of reading a design document.

## Decisions

The table holds the four owner decisions of 2026-10-07 (M1 to M4, `P§2`) and the programme
register's forty-one decisions (D-1 to D-41, `P§8`). D-1 to D-24 are the original rows; D-25 to
D-41 were raised later, by the interface registry and the sub-project designs, and a D-id above 24
in an older text means the row here. The detail of each decision lives in one design (`P§8`): SP1 for
D-11; SP2 for D-1 to D-10, D-12, D-13, D-20, D-21, D-24 to D-26 and D-38; SP3 for D-14, D-22, D-23
and D-41; SP4 for D-15 to D-19, D-27 to D-37, D-39 and D-40.

Each decision is one line of the table. A row has six cells, in this order: ID, Decision,
Recommended default, Amends or builds on, Needed by (a release id from `R1.1` to `R4.7` for a D
row, with a parenthetical where the programme register gives one, or `—` for D-30 and D-32, which
nothing waits on; the M rows say `all`, `SP4` or `—`) and State. State is exactly one of
`open (default recommended)`, `approved YYYY-MM-DD` or `amended YYYY-MM-DD`; an amended row's
default cell is rewritten to the owner's wording in the same edit. The owner answers a decision by
editing that one cell, and a decision is answered only when its State cell says so.

| ID | Decision | Recommended default | Amends or builds on | Needed by | State |
|---|---|---|---|---|---|
| M1 | Four sub-projects: foundations and cleanup, platform backbone, core revamp, suite connection; order 1, 2, then 3 and 4 | As decided by the owner on 2026-10-07 | — | all | open (default recommended) |
| M2 | Independent releases: each is reversible on its own and follows review, merge, manual deploy, verify, stopping on any failure | As decided by the owner on 2026-10-07 | builds on ADR-0013 and ADR-0036 | all | open (default recommended) |
| M3 | All four connection loops matter: data reaches every product that holds a person or a company | As decided by the owner on 2026-10-07 | ADR-0000 §10 and §11 | SP4 | open (default recommended) |
| M4 | This pass produces specs and plans only; building starts after the owner approves them | As decided by the owner on 2026-10-07 | — | — | open (default recommended) |
| D-1 | NEW-9: what happens to the orphan deal | Soft-delete it with an audit row naming the consumer; emit no `stage_changed` event (closing it as lost would distort the win rate and fire tenant automations) | ADR-0017 Known limitations, "An aborted conversion leaves orphaned records behind"; register NEW-9 (its "marks the orphaned deal lost" is corrected: closing a deal as lost distorts the win rate and fires tenant automations on `stage_changed`, `P§3` F3) | R2.4 | open (default recommended) |
| D-2 | NEW-9: how far customer cleanup may go | Only records provably created by the aborted attempt (the id equals the id re-derived from the lead id and generation, and the entity's own creation audit row has `acting_as='lead'` and is its only audit row), with no other contacts and no live deal; never a reused record. `CRM_CONVERSION_CLEANUP_MODE` ships as `log_only` and the owner flips it to `on` after reading the log lines | register NEW-9 ("removes the orphaned company and contact if nothing else references them" is narrowed) | R2.4 | open (default recommended) |
| D-3 | Orphans from past aborts already exist? | The owner runs a read-only count of `lead.conversion_aborted` audit rows (`lead.audit_logs`) before R2.4, as hand-run SQL; the historical sweep (registry `H3`) is built only if the count is not zero | ADR-0017 Known limitations, "An aborted conversion leaves orphaned records behind" | R2.4 | open (default recommended) |
| D-4 | Lead bumps `conversion_generation` on every abort and announces the deleted-record bump | Yes to both: bump on every abort, carry the generation in the payload and schema, and emit an event for the deleted-record bump (registry `EV13`, reason `record_deleted`) | ADR-0017 Known limitations, "A deleted record no longer permanently blocks a lead's conversion" (today `finalize_conversion` bumps only for an `owner_changed` abort, `services/lead/app/services.py:1125-1132`, and `fail_conversion` bumps for a deleted record but stages no event, `:1226-1280`, so "the event is the hook" is inaccurate there) | R2.1 | open (default recommended) |
| D-5 | Stage model | One pipeline per organization (tables shaped so more can be added); an immutable stage key with an editable label, order, tone and default probability; an outcome of `open`, `won` or `lost`; at least one won and one lost stage (several allowed); stages are archived, never deleted; closed deals are immutable (the no-move rule; reopening is a separate decision) | ADR-0017 decision 4 ("accepted debt" becomes scheduled work); register row for hard-coded stages; the stage event contract below | R2.3 | open (default recommended) |
| D-6 | Who edits stages | A new permission `crm.pipeline.stage.write` with a route floor of ORG; reading stays under `crm.pipeline.deal.read`; no new admin template | builds on ADR-0007 | R2.5 | open (default recommended) |
| D-7 | What a rep is after C15 | `customer.owners` gains a nullable `membership_id` column (a column, not a primary-key rekey); the Team screen lists organization members and activates them as reps; free-form rep creation retires after the cutover (R2.7 keeps the first-run self-claim, R2.9 removes the button) | ADR-0008 Q5 (a column, not a rekey); register row 20 and NEW-7 | R2.6 | open (default recommended) |
| D-8 | Owner-id mapping rule | A human-reviewed plan: employee-link evidence for employee-valued rows; email match from roster to member only as a proposal the owner accepts; unmapped rows untouched and listed (visible at ORG, invisible at OWN); never guessed; an owner-run dry run per organization with a reversible journal. Historical `deal.activities.owner_id` stays untouched (append-only; registry `L7`) | ADR-0008 Q5's migration recipe, whose premise the data do not support (`P§3` F2); ADR-0008 Q1 rejected email matching for the HRMS link, so the plan is human-reviewed | R2.6 | open (default recommended) |
| D-9 | Sign-off for the access widening C15 causes | Approve (reps gain OWN write, convert and reveal on their own rows), with a signed security review of every OWN route (the SP2 checklist S-1 to S-12) before the R2.7 flip | ADR-0008 Q5 Consequences (CRM's OWN semantics change for all three processes at once) | R2.7 | open (default recommended) |
| D-10 | Production CRM census | The owner runs a read-only count: organizations with CRM data, roster sizes, employee links and the `lead.outbox` backlog | builds on ADR-0036 (the box is staging-like; production reads are the owner's) | R2.6 | open (default recommended) |
| D-11 | Fold `lead` into `customer`? | **Keep three processes** for this programme; this ADR costs the fold (about 98 MB freed, supersedes ADR-0017 decision 1, needs a cross-schema data move) and it is revisited after R2 | would supersede ADR-0017 decision 1; costed in the section "Costing of D-11" below | R2.4 (only if D-13 holds the ceiling; R1.1 merges with this row open) | open (default recommended) |
| D-12 | Insights vs stages landing order (event contract) | Stages land first; the insights migration `a1c9e2f7b3d4` is adopted unchanged (byte-pinned); insights is rebased onto `outcome`; the event contract (`outcome`, frozen stage keys, `closed_at`) is recorded in this ADR. Insights' `deal_facts` stage CHECK and `closed_won`/`closed_lost` predicates must move to `outcome` before custom stages are enabled in any organization with insights (registry `L10`) | ADR-0020 §6 and Risk 1 notes; `docs/insights/events.md` (rebased in R2.3); the stage event contract below | R2.3 | open (default recommended) |
| D-13 | Memory ceiling (amended by registry `L17`) | The first draft's "no increase" assumption is already broken by the first consumer fold (+1 MB each). The owner raises `EDGE_BUDGET_ACCEPTED_MB` by one per fold, release by release, in the box `.env` before each deploy (3945 at R2.4 deal, 3946 R3.8 lead, 3947 R4.4 support, 3948 R4.6 marketing, 3949 R4.7 storefront; ten profiles plus `admin`), or frees memory (D-11). Insights (+99 MB) and the PDF library (+10 to +20 MB each in deal and finance, not measured) ship only after a separate owner answer on the ceiling | ADR-0017 Consequences (stale memory figures); ADR-0020 Risk 1; registry `L17` (amends the decision to the five folds and the three further items SP4 adds, and lists three fold alternatives that trade memory for cross-service calls) | R2.4 (the first fold's deploy) | open (default recommended) |
| D-14 | Party identity | Company is the party, contact the person, phone-only contacts allowed (`contacts.email` nullable) | builds on ADR-0017; `P§4.2` | R3.1 (also gates R4.1) | open (default recommended) |
| D-15 | Engage contact book | A projection of CRM contacts for CRM-entitled organizations; standalone Engage mode kept; the projection writes no consent and never opts anyone in | builds on ADR-0021 ("silence is not consent", `docs/adr/0021-consent-ledger.md:61`) and the Engage design §1.2 (`docs/superpowers/specs/2026-09-21-engage-email-platform-design.md:34-54`) | R4.2 | open (default recommended) |
| D-16 | Marketing vs Engage | Engage owns sending and delivery analytics; Marketing owns planning and attribution and reads Engage | — | R4.6 | open (default recommended) |
| D-17 | Target for the client link and won-deal kickoff: `project` (PulseBoard) or `projects` | Both products get the same link columns and event fields (one shared contract); the kickoff is built in PulseBoard (`project`) first because ADR-0000 section 11 already says the PulseBoard client id becomes the CRM customer id; `projects` adopts the same route later. The owner picks | builds on ADR-0000 section 11 | R4.4 (kickoff only) | open (default recommended) |
| D-18 | Discount approvals | The approval engine is the one mechanism (no third local state machine), with a membership requester abstraction and a platform inbox UI; recorded in the follow-up ADR `approvals-for-tenants-without-hrms` (expected 0042, filed proposed, accepted before the approval deploy). Approval moves from the `hrms` profile to core (a 99 MB container on boxes with `crm` and no `hrms`) | builds on ADR-0006 (its Context names discount approvals in CRM as a consumer, `docs/adr/0006-approval-engine.md:62`); supersedes, for tenants without HRMS, the employee-keyed requesters and HRMS-only inbox `P§6` records, in the follow-up ADR `approvals-for-tenants-without-hrms`; the programme register words this as superseding ADR-0006 Q1's narrow stance (Q1 is at `docs/adr/0006-approval-engine.md:1479`) | R4.5 | open (default recommended) |
| D-19 | Quote home and item master | Quotes live in `deal`'s own schema (never the `quotation` scaffold, which is deleted with the quote work); `inventory_items` is the one item master; `storefront.products` gains `inventory_item_id`; recorded in the follow-up ADR `crm-quotes-in-deal` (expected 0043) | records the deletion on the ADR-0017 precedent | R4.5 | open (default recommended) |
| D-20 | Role consolidation | Four templates, `CRM_ADMIN`, `CRM_REP`, `CRM_MANAGER` and `CRM_VIEWER`, by same-code merging across the three catalogues; the six legacy role codes and all 17 permission codes are frozen, never renamed or removed; the four later codes (registry `E19` to `E22`) are catalogued admin-first in R2.8 | ADR-0017 decision 1 as built (`CRM_LEAD_REP`); builds on ADR-0007 | R2.8 | open (default recommended) |
| D-21 | Plan packaging | Stay flat (one `crm` entitlement, no per-module tiering); fix or retire the INR 299 per-seat `crm/growth` plan by a staff action in the admin console (ADR-0035's open question); no code in this programme | ADR-0035 open question (`docs/adr/0035-list-prices-2026-10-06.md:40-41,165-169`) | R2.8 | open (default recommended) |
| D-22 | API nouns | `/companies` canonical (`/customers` aliased), the 360 under `/contacts/{id}/360`, `/activities` aliased | — | R3.2 | open (default recommended) |
| D-23 | Removals that had live users | Replace, then delete: fixtures mode, `Login.tsx`, the CSV export and the per-card stage select go only after their replacements ship. The `/deals/summary` server fields are **not** deleted (registry `C9` freezes all nine; the five unrendered ones get a renderer in R3.4 and only the SPA's trend scaffolding goes) | builds on ADR-0022 (the export engine) | R3.4 | open (default recommended) |
| D-24 | TEAM scope for managers | Include, sourced from `manager_membership_id` through a per-service `owner_team` projection; for a membership subject TEAM also admits the caller's own rows (registry `J10`, `L6`); wired in R3.8; the widening is a separate owner-run reconcile after a soak and cannot be narrowed by any tool | builds on ADR-0006 Q1 (`manager_membership_id` kept as core's TEAM rung, `docs/adr/0006-approval-engine.md:1479`) | R2.2 (the membership-TEAM semantics tasks only; they flip as a unit on a no); R3.8 for the wiring and the widening | open (default recommended) |
| D-25 | May managers reveal the sealed phone or email of their whole team (raised by registry `L20`) | **No**: `CRM_MANAGER` holds `crm.book.contact.read_sensitive` and `crm.lead.lead.read_sensitive` at OWN, the rep-equivalent. On a yes those cells become TEAM through the R3.8 widening and D-9's security review covers them | builds on ADR-0023 (step-up); raised by the interface registry (its `L20`) | R2.8 | open (default recommended) |
| D-26 | Seal `contacts.email` at rest (raised by SP2; registry `B56`) | **No**: R2.9 Part C is built only on an explicit yes and when its gates hold (R3.1 shipped with nullable email and the `email_index` backfill run; the plaintext readers `C45`, `C47` and `C53` sealed-aware or consciously left on plaintext; the reveal route's negative-authz set green). A yes reopens R4.1 and R4.2 | registry `B56`, `B10`, `L16`; the "C15/Q2" of the open-questions register (its plaintext-email row) | R2.9 (Part C only; an optional release) | open (default recommended) |
| D-27 | What each store keeps after an erase, including what a lead erase keeps (raised by SP4, shared with SP3; absorbs SP3 question `L23`) | The coverage table in SP4's design: identifiers are overwritten wherever a store keys a row by the contact id; legal documents (invoices), consent ledger rows, suppressions, free-text bodies and append-only deal text are retained and listed as residue; the owner and legal confirm each exception before the follow-up ADR `crm-consent-and-erasure` (expected 0039) is accepted. Leads: an administrator erases one lead at a time, in place, on registry `E22` behind a step-up (`POST /leads/{lead_id}:erase`), clearing name, company, email, phone, notes, visitor messages, GSTIN and duplicate pointers and rewriting audit copies, and keeping source, status, owner, attribution, consent evidence and converted ids; no bulk erase and no dedicated `crm.lead.lead.erase` code (the alternatives `L23` puts to the owner); recorded in the follow-up ADR `crm-public-lead-capture` (expected 0041) | follow-up ADRs `crm-consent-and-erasure` (0039) and `crm-public-lead-capture` (0041); registry `I2`, `C108`, `L23` | R3.7 (R3.7b does not ship without the lead answer); R4.1 (ADR acceptance, contacts); R4.3; whichever of R3.7b and R4.1 is scheduled first | open (default recommended) |
| D-28 | Accounting for an online refund of a captured invoice or order payment | **Record only**: finance stores the refund fact on `online_captures`, writes an audit row and logs it; it creates no credit note and no negative allocation; a person issues the credit note | registry `B82`, `C103`, `C105` | R4.3 | open (default recommended) |
| D-29 | Fulfilment billing settings surface (R4.3-S) | R4.3 includes a minimal slice: `GET` and `PUT /operations/fulfilment/settings` for the organization-default row (`invoice_timing`, `default_tax_rate_pct`, `create_crm_party_on_order` and `order_invoice_chain_enabled`, default false), one new permission (registry `E27`, operations' own) and one card on the existing payment-settings screen, used by the organization's own administrator and never the platform owner. The alternative is to ship the chain dark and set rows by owner SQL | registry `C102`, `E27`, `F13`, `F19` | R4.3 (chain enablement only) | open (default recommended) |
| D-30 | Customer-facing pay page (payment link) for invoices | **Defer**: no public payment surface exists and the provider seam has hosted-checkout orders only; R4.5-C ships staff-assisted collection (finance staff open the hosted checkout) | registry `C103`, `G25`, `EV38` | — (nothing waits on it; the default defers) | open (default recommended) |
| D-31 | Marketing opt-in captured on a public form (storefront buyer form and lead capture forms) | **Single opt-in recorded with evidence**: the ledger row names the form text and the enquiry, an unticked box writes nothing, and every marketing message carries the unsubscribe link; a confirmed (double) opt-in is deferred | registry `B39`, `B77`, `C58`; builds on ADR-0021 | R4.7 (storefront consent only) | open (default recommended) |
| D-32 | Insights v2: company-keyed cross-product facts | **Design only**: not built until a dashboard needs a company-ranked board that the R4.3 party projection cannot serve; the design is kept in `docs/insights/v2-company-facts.md` so the decision costs a document, not a producer change | registry `I12`, `B72` to `B74` | — (design only) | open (default recommended) |
| D-33 | One stated rule for STOP and unsubscribe (first drafted as D-26 in SP4) | **Confirm the code's rule**: STOP and the unsubscribe link both opt out of marketing only; a transactional opt-out is a separate explicit act (an administrator, or the person's own request). R4.1 corrects the two stale texts (the `consent.py` docstring and ADR-0021) and `docs/whatsapp/architecture.md` in the follow-up ADR; no behaviour changes | registry `I2`, `I11`; builds on ADR-0021 | R4.1 (ADR acceptance) | open (default recommended) |
| D-34 | Lifecycle automation: may CRM promote a company to customer on a commercial event? | A per-organization setting `customer.customer_settings.lifecycle_promotion`, default off. `first_order` promotes on the first accepted order only (the one trigger `P§6` names); `first_commercial_event` also promotes on the first won deal and the first paid invoice (the owner's explicit extension). No organization changes until its administrator turns it on; backfills and replays never promote | registry `EV2`, `B81`, `B83`, `C106` | R4.3 (the setting; no promotion happens while it is off) | open (default recommended) |
| D-35 | Who owns a party created by `parties:ensure` (registry `L1`) | The caller's `owner_membership_id` when given and activatable, else the organization's single active `is_owner` membership (activated as a rep with an audit row), else 422 `owner_required` | registry `L1`, `C51`, `C57` | R4.1 | open (default recommended) |
| D-36 | First-touch or last-touch campaign attribution (registry `L9`) | **First touch**, write-once: a lead's campaign id is set at capture and the deal copies it at conversion; neither changes afterwards (409 `attribution_locked`); campaign figures are CRM-attributed plus the typed adjustment, money added only in the campaign's own currency; the schema carries one `marketing_campaign_id` and one UTM set per lead and deal either way | registry `L9`, `B53` | R4.6 (R4.6-A attribution) | open (default recommended) |
| D-37 | Permission for CRM-originated sends (registry `L13`) | `engage.inbox.message.reply`, so the rep also needs an Engage role (a CRM role template cannot hold a cross-product code); no new CRM code | registry `L13`, `C60` | R4.2 | open (default recommended) |
| D-38 | Should a rep read only their own records? (registry `L18`, `J19`) | **No**: reps read at ORG and write at OWN, which is today's behaviour; success criterion 1 of the programme (`P§1`) is worded to match. Narrowing reads would hide teammates' records from every rep's lists, boards and 360 and is a larger change than D-9 approves | registry `L18`, `J19`; `P§3` F2 | R2.8 | open (default recommended) |
| D-39 | Notes attachments and erasure (registry `L19`) | Until the consent and erasure ADR chooses, R4.5 ships the PDF rows but not attachments (`B57`, `C81` to `C83`); deal binds `crm.book.contact.erased` unconditionally and has document delete the private objects of the erased contact's activities (the attachment row stays as a fact); the ADR also decides whether `original_name` is stored at all | registry `L19`, `B57`, `C81` to `C83` | R4.5 (attachments only, R4.5-B) | open (default recommended) |
| D-40 | Who may hand a quote off to finance, operations or projects (registry `L21`) | **No new permission code**: the ORG floor on `crm.pipeline.quote.write` for order and invoice targets plus the callee's entitlement re-check. The alternative is a dedicated dangerous `crm.pipeline.quote.handoff` (default OWN, max ORG) | registry `L21`, `C20` | R4.5 | open (default recommended) |
| D-41 | May `PATCH` still edit a closed deal's value, currency, probability and close date? (SP3 question `L22`) | **Yes**, no server change in this programme: D-5's "closed deals immutable" is the no-move rule; the deal page renders a closed deal read-only; bulk update and import keep working. Win and loss figures can change retroactively if someone edits a closed deal through the API | registry `L22`; D-5 | R3.5 | open (default recommended) |

Later releases read the table with one command. For D-9 it prints the row when D-9 is answered and
nothing while it is open; a release that needs one specific answer tests for `approved` alone:

```
grep -E '^\| D-9 \|.*\| (approved|amended) [0-9]{4}-[0-9]{2}-[0-9]{2} \|$' docs/adr/0038-crm-programme-decisions.md
```

Evidence that travels with an answer (D-9's signed security review, D-21's staff plan action) goes
on one line under Sign-offs, `D-n: <what was signed or done> on <date>`; a Sign-offs line is
evidence and never an answer. When the owner answers D-n, the same commit replaces the
`(pending D-n)` clause in each note that cites it; a clause that cites several decisions loses only
the answered id. For D-9 the notes are listed by the command below. It finds `pending D-9` and
`pending D-7, D-8, D-9` alike and reaches the service READMEs as well as `docs/`
(`services/deal/README.md` cites D-1); it leaves out `docs/superpowers`, whose specs and plans are
the design record and not notes.

```
grep -rnE 'pending[^)]*D-9([^0-9]|$)' docs services --include='*.md' --exclude-dir=superpowers
```

### The stage event contract (proposed under D-5 and D-12)

If D-5 and D-12 are answered as recommended, the CRM's deal events change in these ways and no
others. Every change is additive within `v1` and lands in the same commit as its committed schema.

- A stage has an **outcome**: `open`, `won` or `lost`. A consumer tests the outcome and never the
  key.
- The six stored keys `lead`, `qualified`, `proposal`, `negotiation`, `closed_won` and `closed_lost`
  are frozen. They become the default pipeline's keys and never change meaning, because tenant
  rules match `payload.to`, the Customer 360 tests them literally
  (`services/customer/app/c1_360.py:95`) and three event contracts carry them.
- `crm.pipeline.deal.created` and `crm.pipeline.deal.updated` gain `stage_outcome`;
  `crm.pipeline.deal.stage_changed` gains `from_outcome`, `to_outcome` and an optional `to_label`,
  while `from` and `to` stay keys. The six-value enumerations that carry a stage key become the key
  pattern `^[a-z0-9_]{1,20}$`.
- `deals.closed_at` is a stored column, set when a deal reaches a won or lost outcome. R2.3 creates
  it with the deal migration `a1c9e2f7b3d4`, which is not on `main` (it is on the unmerged branch
  `sprint/insights-q2`, parent `e94d7a2c5f18`) and is adopted unchanged and byte-pinned; the
  committed deal event schemas already require `closed_at`.
- The insights projection (ADR-0020) is rebased onto `outcome` before custom stages are enabled in
  any organization that has insights, because its deal fold tests the literal keys and would
  dead-letter otherwise.

## What this amends

Accepted ADRs are amended by dated notes, never rewritten; the precedent is ADR-0008's own
"Amended 2026-09-17" notes. The notes this ADR adds, so that a reader of the older document can
find them (all dated 2026-10-07; each states what the code or the data shows as fact and what this
ADR only proposes as "proposed in ADR-0038 (pending D-n)"):

| Document | Where | What the note says |
|---|---|---|
| ADR-0017 | Decision 1 | As built, `lead` declares `CRM_LEADS_ADMIN` and a third default role `CRM_LEAD_REP`; neither older rep template holds a `crm.lead.*` grant. Merging the three rep templates is proposed in D-20 |
| ADR-0017 | Decision 4 | The one in-place edit: the citation `domain.py:38` is `domain.py:21` |
| ADR-0017 | The `customer-worker` section | The consumer now runs inside the customer API process; the container survives only behind `split-workers` |
| ADR-0017 | Known limitations, "An aborted conversion leaves orphaned records behind" | Idempotent-by-id reuse is why the sketched cleanup is unsafe; the cleanup is proposed in D-1 to D-3 |
| ADR-0017 | Known limitations, "A deleted record no longer permanently blocks a lead's conversion" | `fail_conversion` stages no event, so the event is not a hook for every earlier generation; bump and event are proposed in D-4 |
| ADR-0017 | Consequences | The memory figures are recomputed; the ceiling is D-13 |
| ADR-0008 | Q5 | CRM owner values are roster ids or employee ids, not one space, and the platform-core change is not small; the mapping plan and a `membership_id` column are proposed in D-7 and D-8 |
| ADR-0020 | Risk 1 | The +99 MB is an owner decision against today's totals (D-13) |
| ADR-0020 | Section 6 (Event contracts) | The stage vocabulary moves from keys to `outcome` (D-5, D-12) |

The open-questions register gains one clause naming this ADR in its row 20, in the three
ADR-0017 limitation rows for plaintext contact email, aborted conversions and hard-coded stages
and in the note under them, in NEW-7, NEW-8 and NEW-9 and in the C15/C14 scope note; its counts do
not change, and NEW-9's "marks the orphaned deal lost" is annotated, not replaced.

One scope move is recorded here because it changes where the registry and the core-revamp spec look. The
four placeholder routes of `product-crm` (Reports, Integrations, Settings and Help), their
navigation entries, `routes/ComingSoon.tsx` and the seven dead rollups of `data/derive.ts` are to be
removed in R1.2b and not in R3.4, and the 404 page, today a `ComingSoonPage` on the `*` route
(`frontend/product-crm/src/main.tsx:110-119`), is replaced there by a new `routes/NotFound.tsx`, so
the registry row `G15` and the core-revamp spec's replace-then-delete ledger follow.

## C15, named

The open-questions register and ADR-0008 use "C15" for a CRM rebuild slice that exists in no other
document: the register's row 20, its limitation rows for plaintext contact email and for
hard-coded stages, NEW-7 to NEW-9 and the C15/C14 scope note, and ADR-0008 Q5 Consequences. This
ADR defines it so the label can be traced.

- **C15 is OWN on the membership.** R2.6 expands (`owners.membership_id`, the account bulk read,
  the owner-run mapping tool); R2.7 flips (CRM scope on the membership, with the data applied
  first); the optional R2.9 contracts (retire the one-click owner claim button and the naming
  residue; the write-side employee-id fallbacks already retire in R2.7).
- **C15 is configurable stages.** R2.3 expands; R2.5 adds the authoring API and
  `crm.pipeline.stage.write`.
- **NEW-7**, the platform-core membership subject, is R2.2. It is larger than the register says,
  because `register_scope_resolver` writes a dictionary that nothing reads
  (`libs/platform-core/src/platform_core/auth.py:297-301`).
- **NEW-9** is R2.1 (lead hardening) and R2.4 (the consumers).
- The optional sealing of `customer.contacts.email`, the register's "C15/Q2", is R2.9 (Part C,
  pending D-26).

`docs/whatsapp/audit.md` (lines 95 and 239) uses `C15` for "Human handoff": an unrelated id in
another audit's own numbering. The interface registry's own row `C15`
(`GET /deals/{deal_id}/history`) is a third use; this programme writes the slice as "C15" and the
registry row as "registry `C15`".

## Costing of D-11

**What the fold is.** `lead`'s code and tables become part of the `customer` service and schema;
the `lead` container and the `lead` schema go away; `leads:convert` becomes a local transaction for
the company and contact, while the deal call stays an HTTP call outside any transaction.

**Memory, by the repo's own arithmetic.** The CLI that `preflight.sh` uses is
`python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles <csv>`;
the D-11 rows are computed on a copy of the budget with the `lead` entry removed from `containers`.
The reference MemTotal is 3862 MB. The box's accepted ceiling is 3944 MB according to the owner's
note (`EDGE_BUDGET_ACCEPTED_MB`); that figure cannot be verified from the repository, and
`make edge-preflight` prints the verdict. "Ten profiles" is `storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm`.

| Scenario | Ten profiles | % of 3862 | Ten plus `admin` | % of 3862 | against 3944 | against 3862 |
|---|---|---|---|---|---|---|
| Today | 3747 | 97.02 | 3944 | 102.12 | 0 | +82 |
| Registry plan: five folds of +1 MB (deal R2.4, lead R3.8, support R4.4, marketing R4.6, storefront R4.7) | 3752 | 97.15 | 3949 | 102.25 | +5 | +87 |
| **D-11 yes**: the `lead` container removed, `customer` stays `api_with_worker` (99) | **3649** | 94.48 | **3846** | 99.59 | -98 | -16 |
| **D-11 yes** and the four remaining folds (no lead fold) | 3653 | 94.59 | 3850 | 99.69 | -94 | -12 |
| For context: a standalone `deal-worker` (+90) instead of a fold | 3837 | 99.35 | 4034 | 104.45 | +90 | +172 |
| For context: `deal` folded into `customer` too (not proposed) | 3551 | 91.95 | 3748 | 97.05 | -196 | -114 |
| For context: the planned insights container (+99) on top of the D-11 and four-fold row | 3752 | 97.15 | 3949 | 102.25 | +5 | +87 |

The first and third rows reproduce with four commands: the CLI over the repo's budget with the ten
profiles (3747) and with `,admin` added (3944), and over a copy without the `lead` container with
the same two lists (3649 and 3846). The other rows add the fold or container cost on top (a fold
moves a service from kind `api`, 98 MB, to kind `api_with_worker`, 99 MB; a standalone worker is
kind `background`, 90 MB).

`customer` would absorb `lead`'s modules. The kind's own measurements put `customer` at 91.7 MiB
and `lead` at 92.2 MiB (`memory-budget.json`, `kinds.api.measured_mib`), nearly all of it the
shared module graph, so the saving is 98 MB less whatever `lead`'s modules add to `customer`'s
resident set: small, but not measured (inference), and the 98 is itself a rounded mean. Every
ten-profile row sits between the soft line (85% of 3862, 3282.7 MB) and the hard line (3862 MB).
With `admin`, only the two D-11 rows and the deal-folded row fall below the hard line; every row is
far below the accepted maximum of 125% (4827.5 MB).

**What it removes.** The registry rows that would not be built: the lead fold `F9` with its
`lead-worker`, `lead.events` (`EV45`) and its addition to `PROFILE_DLQ_QUEUES["crm"]`; lead's copy of
`owner_team` (`B4`); the wiring rows `W2` and `W19`; the `owners:validate` route (`C49`) and lead's
process-local owner caches; three entries in
`tests/infra/test_io_outside_transaction.py` (lead's `put_assignment_rule`, `create_lead` and
`update_lead`); and the customer half of NEW-9 (D-2), because the company and contact are written
in one local transaction. The orphan deal remains (D-1). The registry's decision-defaults table
(its section K) adds what a yes would move into `customer` instead of dropping: `B86`, the lead
sides of `C94` and `C104`, and the lead handlers that R4.7 binds on `lead.events` (`EV45`), all of
which assume `lead` stays its own process.

**What it costs.**

1. It supersedes ADR-0017 decision 1 ("`lead` is a third process", accepted 2026-09-23).
2. An irreversible data move on live tenants. `lead.leads`, `assignment_rules`, `audit_logs`,
   `outbox` and `processed_events` become tables in the `customer` schema: customer migrations
   create them; an owner-run tool copies the rows per organization with the admin role (idempotent,
   counts only); the PII columns are sealed under the platform-wide key, so nothing is
   re-encrypted; the `tenant_isolation` policies are rebuilt on `customer.current_org()`;
   `opsgrids_customer` is granted on them; `lead.outbox` must be drained first because the relay
   entry for `lead` goes away. After the first write to the new tables there is no code-only
   rollback.
3. File and test churn: `lead`'s 3,103 lines of application code and 5,287 of tests move under
   `customer`; `/api/v1/leads` is re-pointed to `customer` in **both** route tables; the `lead`
   block leaves `compose.prod.yml`; the ten-profile literals in `RUNBOOK.md` (3747 becomes 3649)
   change in the same commit or
   `tests/infra/test_planned_headroom.py::test_runbook_ceiling_example_is_the_number_preflight_would_print`
   fails; `PROFILE_API_CONTAINERS["crm"]` (`libs/platform-core/src/platform_core/ops_monitor/config.py:83`)
   is a `libs/` change, so that deploy is a full rebuild; `tools/dev-bootstrap/init-db.sql` and
   `SERVICES_WITH_SCHEMA` in `infrastructure/edge/remote.sh` lose `lead`.
4. The event envelope's `producer` for `crm.lead.*` becomes `customer` (`services/lead/app/relay.py:38`
   takes it from the service name). Only the scheduler's ownership rule reads `producer`
   (`services/scheduler/app/consumers.py:90`), and no CRM event passes through it.
5. Size XL on the repo's scale (more than a week, several slices): moving the code and tests is L,
   the data-move tool with its runbook M, the gateway, compose and documents S.

**Recommendation.** Keep three processes for this programme. **Revisit when** (a) the owner answers
D-13 "no increase": D-11 must then be answered yes *before R2.4 deploys*, because R2.4's single
fold takes the ten profiles plus `admin` to 3945 MB, above 3944, and the D-11 rows are the only
registered way to stay under the ceiling; or (b) the owner holds the planned insights container
until memory is freed; or (c) R2.1 and R2.4 have shipped and NEW-9's customer half is already
hardened, which shrinks the fold's benefit to the 98 MB.

## Consequences

The release train is `P§10` as amended by the registry. A release whose Needed-by row is still
open in the table above does not start: its plan task runs the answer check and stops. R1.1 (this
ADR) and R1.2 need no answer, and where an answer would change anything in them it changes
documents only.

| Release | Decisions that must be answered first |
|---|---|
| R1.1, R1.2 | none |
| R2.1 | D-4 |
| R2.2 | D-24 (the membership-TEAM semantics tasks only; they flip as a unit on a no) |
| R2.3 | D-5, D-12 |
| R2.4 | D-1, D-2, D-3, D-13 (and D-11 if D-13 is answered "no increase": the ceiling holds) |
| R2.5 | D-6 |
| R2.6 | D-7, D-8, D-10 |
| R2.7 | D-9 |
| R2.8 | D-20, D-21, D-25, D-38 |
| R2.9 | D-26 (Part C only) |
| R3.1 | D-14 |
| R3.2 | D-22 |
| R3.4 | D-23 |
| R3.5 | D-41 |
| R3.7 | D-27 (for leads, before R3.7b) |
| R3.8 | D-13 (+1 MB), D-24, D-25 |
| R4.1 | D-14, D-27, D-33, D-35 |
| R4.2 | D-15, D-37 |
| R4.3 | D-14, D-22, D-27, D-28, D-29 (chain enablement only), D-34 |
| R4.4 | D-13 (+1 MB), D-17 |
| R4.5 | D-18, D-19, D-40 (and D-39 for attachments) |
| R4.6 | D-12, D-13, D-16, D-36 |
| R4.7 | D-13 (+1 MB), D-27, D-31 |

Releases not listed need no decision beyond those of their prerequisites. D-30 and D-32 block no
release: the default of D-30 defers the pay page and D-32 is a design only.

## Sign-offs

None yet. One line per signed or completed piece of evidence, `D-n: <what was signed or done> on
<date>`; a line here is evidence and never an answer.

## Open items

- **D-13 first bites at R2.4**, not at R4.6 as the programme register's first draft said (`P§8`
  now gives R2.4). One consumer fold takes the ten sellable profiles plus `admin` from 3944 MB to
  3945 MB, above the ceiling the owner noted; the registry's five folds reach 3949 MB (registry
  `F8` and `L17`).
- **D-11 first bites at R2.4 too**, and only if D-13 holds the ceiling: not at R1.1, which merges
  with D-11 open (the programme register's first draft listed R1.1 for it; `P§8` now says R2.4).
- **The CSV formula guard this record once required before R3.7 has shipped.** The page-only CSV
  export of Companies used to quote a cell without neutralising a first character of `=`, `+`, `-`
  or `@`, so R3.4 (proposed to delete the export, pending D-23) had to deploy before R3.7's public
  lead capture gave company names an external writer. #123 put the guard in the shared writer:
  `frontend/product-crm/src/lib/csv.ts:7` now uses `csvCell` from `@opsgrids/client`
  (`frontend/opsgrids-client/src/csv.ts:9-12`), which prefixes `'` to a cell starting with `=`,
  `+`, `-`, `@`, tab or CR. R3.4 still stands on D-23 alone; it no longer has to precede R3.7.
- **Follow-up ADRs** the programme allocates (`P§8`, "ADR numbers the programme allocates";
  registry `I2`). The slug is binding and the number is the expected one: each release confirms its
  number against `docs/decisions.md` when it files the ADR, and if a parallel branch lands above a
  number first, the number moves and the slug does not. `crm-consent-and-erasure` (0039) is filed
  by R4.1 as a draft and accepted before R4.1 is called done (D-27, D-33);
  `crm-public-lead-capture` (0041) is filed by R3.7b and accepted before the R3.7b deploy (D-27 for
  leads); `approvals-for-tenants-without-hrms` (0042) is filed by R4.5, proposed, and accepted
  before the approval deploy (D-18); `crm-quotes-in-deal` (0043) is filed with R4.5 (D-19). 0040 is
  `faster-ci`, merged (#118).
