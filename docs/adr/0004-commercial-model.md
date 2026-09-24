# ADR-0004 — The commercial model: plans, subscriptions, trials, custom pricing, grants and billing

**Status: Accepted 2026-08-24 (design). Not yet implemented** — like [ADR-0002](0002-row-level-security.md),
this records the shape before the code exists, because the irreversible parts are cheap to fix on
paper and expensive to fix once invoices exist.

Builds on decisions 8 (money in minor units) and 9 (immutable plan versions, append-only ledgers)
of [ADR-0000](0000-phase-0-decisions.md), and on the activation/entitlement machinery already
running in `product-service`. Sequenced by [plan.md](../plan.md) (phase 8) and
[backlog.md](../backlog.md). Supersedes nothing.

---

## Context

The platform sells many products against one organization, one account, one user directory
([README](../README.md)). "Adding a product" is an activation, not a new account. What is *not* yet
decided is everything commercial around that activation: what a plan is, how a subscription moves
through its life, how one customer gets a private price without disturbing anyone else, how a
salesperson grants three months free, and how any of it becomes an invoice that survives a dispute.

Several of the hard choices are already made, and this ADR is mostly a matter of not contradicting
them:

- **Money is integer minor units** with an ISO-4217 currency (ADR-0000 §8). No floats, no
  `Numeric(_, 2)` — two decimal places is wrong for JPY (zero) and KWD (three), and exact
  proration and largest-remainder discount allocation want integers.
- **Plan versions are immutable; a subscription pins one version** (ADR-0000 §9). Editing a plan
  clones the active version into a draft; existing subscriptions stay on their pinned version until
  an audited migration moves them and previews the money delta.
- **Invoices are frozen at finalize, the credit ledger is append-only with balance as `SUM`, usage
  events are append-only, tax rates are versioned by `effective_from`** (ADR-0000 §9, related
  immutability rules). A correction is a new row, never a mutation.
- **Entitlement is already the on/off switch.** `product-service` holds an `Activation` row per
  organization per product; auth-service reads it into the `ent` claim; the gateway turns a missing
  entitlement into a **402** before proxying (`services/product/app/models.py`,
  `services/product/app/api/internal.py`, `platform_core/gateway.py`).

The job of the commercial model is to decide *whether an activation stays entitled*, and to produce
a defensible financial record while doing it — without re-opening any of the decisions above.

---

## Decision

### 1. Five services, and the boundary between each

Commerce is five services, split by the thing each one owns and the rate at which that thing
changes. The split is the same discipline as the rest of the platform: one schema each, no
cross-schema foreign keys, events over the outbox between them.

| Service | Owns | Why it is its own boundary |
|---|---|---|
| **`catalog`** | Products, plans, plan versions, features, prices | The definitional layer — *what can be sold*. Changes on a pricing cadence, is immutable-by-version, and is read by everything downstream. |
| **`subscription`** | The per-customer lifecycle: onboarding → trial → active → renewal / upgrade / downgrade → cancelled | The state machine — *what this customer has and where they are in its life*. This is where entitlement is decided. |
| **`metering`** | Usage events (append-only) | *How much was used.* High write volume, different scaling shape from everything else, and the one thing you cannot backfill — so it starts recording before it is billed. |
| **`billing`** | Invoices (append-only, frozen at finalize), proration, the credit ledger | *What is owed.* The financial record of truth. Append-only, and it must be reconstructable years later. |
| **`payment`** | Provider adapters, mandates, charges, reconciliation | *What was actually collected*, and the messy provider-specific reality behind it. Isolated so a provider's constraints do not leak into the rest. |

The seam that matters most is **`catalog` immutable, `subscription` mutable**. Catalog holds
frozen plan versions; subscription holds a mutable pointer *to* one. A customer's life changes
constantly; what they are pinned to does not change under them. Keeping these in one service would
invite an "edit the plan for this customer" shortcut that ADR-0000 §9 exists to forbid.

`payment` is deliberately last in the dependency order and most isolated, because it carries the
most provider-specific truth (see §6).

### 2. Per-customer custom pricing is first-class — a bespoke plan version, pinned

"Assign a price per customer, not fixed tiers" is not a feature to add; it is what ADR-0000 §9
*already makes true*, stated explicitly here so nobody builds a parallel mechanism for it.

Because a plan version is immutable and a subscription pins exactly one version, a private price is:

1. clone the plan into a new **plan version** in `catalog` with the bespoke price;
2. pin **this one customer's** subscription to that version.

That is the entire mechanism. The new version re-prices nobody else — every other subscription
stays pinned to whatever it was on. Past invoices stay reconstructable, because the version they
were computed against still exists, unchanged. There is no "custom price" flag, no per-customer
override column layered on a shared plan, no discount hack standing in for a real price. A negotiated
enterprise deal is a plan version with an audience of one, and it lives in exactly the same table as
every public plan. This is the direct payoff of choosing immutable versions on day zero rather than
mutable plans — the thing sales asks for on week one is free.

Migrating a customer *off* a bespoke version later is the same audited, delta-previewing operation
ADR-0000 §9 already requires for any version migration. No new machinery.

> Since accepted: this same mechanism turned out to carry **partner/reseller pricing** and
> **graduated bundles** with no code at all — a wholesale price is `restricted_to_org_id` pointed
> at the reseller, and a bundle is a plan version whose features name its members. See
> [commerce-packaging.md](../commerce-packaging.md).

### 3. Grants, discounts, credits and comp access are ledger entries, never mutable balances

A grant — a discount, a credit, three months complimentary, a goodwill adjustment with an expiry —
is an **append-only entry in the credit ledger** (ADR-0000 §9), with an amount in integer minor
units, a currency, a reason, and a validity window (`valid_from`, `valid_until`). Nothing is
mutated to create one, and nothing is mutated to spend or expire one: consuming credit is another
row, revoking a grant is a reversing row, and a lapsed grant is simply an entry whose window has
closed.

Two consequences follow, and both are rules elsewhere in the platform (README, "Things that will
bite you"):

- **Balance is derived, never stored.** The credit available to an organization at a moment is a
  `SUM` over entries whose window covers that moment — not a column someone increments. A stored
  balance is a second source of truth that drifts from the ledger the first time two writes race.
- **"Who is on comp access, and when does it lapse?" is a query**, not a status field to keep
  accurate. It reads directly off the same append-only rows, at any point in time, including
  historically — which is exactly what an audit or a revenue report needs.

Every amount is integer minor units with an ISO currency (ADR-0000 §8). There are **no floats in
billing** — not in a grant, not in proration, not in a line total. Proration and largest-remainder
discount allocation are integer arithmetic, which is the other reason §8 chose this representation.

> Since accepted: a **prepaid credit pack** is this and nothing more — a time-boxed grant drawn
> down by later invoices — and it is how an annual commitment above a UPI Autopay mandate's
> per-debit cap is collected without a debit that would fail into dunning. One correction was
> needed: a draw-down must inherit the window of the pack it spends
> ([commerce-packaging.md](../commerce-packaging.md)), or a lapsed pack drags the balance negative.

### 4. Trials and the lifecycle are subscription states, and every transition is an event

A trial is **not** a separate object or a flag on an activation. It is a **state of the
subscription** with an end date — the `subscription` state machine's `trial` state, with
`trial_ends_at`. This already has a foothold: `Activation.status` is `trial | active | suspended`,
and `Activation.entitles` evaluates trial expiry *at read time* rather than trusting a nightly job
to flip a column (`services/product/app/models.py`) — "the job has not run yet" is never a reason
to keep serving a product nobody is paying for. The subscription service is the authority for that
status; the activation is the entitlement projection of it.

Conversion from trial to paid is a **state transition**, not a new record. So are renewal, upgrade,
downgrade, suspension and cancellation. And every one of them is a **domain event on the outbox**,
carrying the ADR-0000 §7 envelope with provenance. This is the design's second payoff: the operator
console (ADR-0003 §2) does not poll tables to compute revenue, trial-conversion, churn and usage —
it consumes the transition stream. MRR is the running sum of activations and their pinned prices;
trial-conversion is `trial → active` transitions over `trial` starts; churn is `active → cancelled`.
Because the events exist from the first subscription, these metrics are answerable historically the
day the console is built, not only from the day someone remembered to start recording.

### 5. Entitlement is the switch commerce sells through; billing decides whether it stays on

The commercial model does **not** invent a new enforcement point. It drives the one that already
exists:

- a **`product.activations` row** — the per-org, per-product record (`services/product/app/models.py`);
- the token **`ent` claim** — built by auth-service from
  `/internal/v1/organizations/{id}/entitlements` (`services/product/app/api/internal.py`);
- the gateway's **402 gate** — a route whose product is not in `ent` is refused with 402, not 403,
  so the client offers an upgrade rather than showing an error (`platform_core/gateway.py`;
  `require_product` in `platform_core/auth.py` repeats the check in-service as defence in depth).

Commerce's job is to keep that activation row honest. A subscription entering `active` (or a trial
that has not lapsed) is what makes an activation entitle; a cancellation, a failed final payment, or
a lapsed trial is what suspends it. **Billing decides *whether* the activation stays active; it does
not re-implement *how* access is enforced.** The clean split is: `subscription` owns the state,
`product-service` projects it to an entitlement, the gateway enforces the projection. A payment
failure becomes a suspension event becomes a de-entitled activation becomes a 402 — each step in the
service that owns it.

> **Addendum, 2026-09-23 — an activation is the OR of its sources.** This section assumed one
> subscription per activation. Bundles broke that: a `suite/trio` subscription entitles `hrms`,
> `crm` and `finance`, and the same customer may also hold a standalone `hrms` subscription. With
> one status column written by whichever event arrived last, cancelling the suite would switch
> off the HRMS they still pay for — and suspending a standalone HRMS would switch off the one
> their suite includes.
>
> So `product.activation_sources` records *why* an activation is on: one row per
> `(organization, product, source)`, where the source is the key the providing subscription was
> bought under (`hrms` for the standalone, `suite` for the bundle; subscription-service keeps one
> subscription per organization per key for ever, so the key names the subscription). The
> activation's status is **recomputed** from its sources on every change — `active` if any source
> is active, else `trial` if any is a trial (ending at the latest end), else `suspended` — and is
> never written directly. The enforcement chain is unchanged: the activation row is still what
> `/entitlements` reads, the `ent` claim is still built from it, and the gateway still 402s on
> it. No new gate, no new claim.
>
> Three rules came with it. **Members come only from the pinned catalog version**:
> subscription snapshots `bundled_products` as `entitled_products` at subscribe and change-plan,
> and every lifecycle event carries it; nobody downstream reads catalog, and nothing reads a
> member list from a client. **The ordering guard is per source**, not per activation: each
> source row carries the `occurred_at` of the last event applied to it, and an older event for
> that source is dropped whole; two sources are independent histories. **A dropped member is
> tombstoned, not deleted**: a trio→duo downgrade marks `finance`'s suite source `removed` at the
> event's time, so a stale trio event cannot re-add it. A hand activation is the product's own
> source, shared with its standalone subscription exactly as the two always shared the row; a
> hand deactivation of a product a bundle still feeds is refused rather than answered with a 200
> that changes nothing. Details: [commerce-packaging.md](../commerce-packaging.md).
>
> **Concurrency.** Both the recompute and the watermark are read-then-write, so each fold holds
> transaction-scoped advisory locks — on the source first, then on every activation it feeds now
> or fed before, in key order — and the hand activate/deactivate paths take the same activation
> lock. Without them two replicas folding at once each OR together a view missing the other's
> uncommitted write, and the later *commit* wins rather than the later *event*. Two-connection
> regression tests pin the three shapes that race (a leak, a lockout, an ordering bypass).
>
> **Decisions taken with it, each the safe default and each reversible.** (1) Bundles are
> **sales-led**: customer routes refuse to subscribe to or re-pin a bundle; the platform team
> sells one over subscription's service-token operator routes, which re-check the members against
> product's registry at the moment of sale. Opening self-serve is one guard. (2) **The plan
> version decides a trial's length** (`trial_days`, default 14, 0 for none); a customer's request
> is capped at it, closing the self-granted 365-day trial on every product. (3) **A `soon`
> product is never a member**: catalog refuses it at authoring, subscription at sale, product at
> projection, and the seeded `suite/full` rung, which includes `projects`, is retired until
> Projects launches. (4) A member list is honoured only on a bundle product's subscription.

### 6. Razorpay first, Stripe later — build against the more constrained provider

`payment` ships a **Razorpay** adapter before a Stripe one, per [plan.md](../plan.md). This is
counter-intuitive — Stripe is the more familiar API — and deliberate. Razorpay is the *more
constrained* provider: mandate caps, a legally required pre-debit notice, multi-day eMandate
activation, and no true idempotency key. An interface designed Stripe-first produces abstractions
Razorpay cannot satisfy, and that mismatch is discovered only after the interface has ten call
sites. Designing against the tighter constraints first yields an abstraction the looser provider
fits inside easily; the reverse does not hold. The provider seam is `payment`'s alone — no other
service knows which processor took the money, only that `billing` recorded what was collected.
"No true idempotency key" has a consequence for every retried or resumed charge; how the adapter
contract answers it — lookup before create, against a provider Order — is the addendum at the end
of this ADR.

### 7. Start metering now, billed later

Per [plan.md](../plan.md) and [backlog.md](../backlog.md), product services **emit usage events
from the moment they exist, with `is_billable=false`**. Usage events are append-only (ADR-0000 §9)
and cannot be backfilled — a meter reading not taken is gone. Recording them from day one costs
almost nothing; turning on usage-based pricing later then becomes *plan configuration in `catalog`*
rather than an architecture change and a scramble to reconstruct history that was never captured.
`metering` is the sink for these from the start; `billing` starts reading them only when a plan
version prices them.

---

## What this rejects

- **Mutable prices / editing a live plan.** Re-prices every customer on it and makes past invoices
  impossible to reconstruct — an invoice you cannot reconstruct is one you cannot defend
  (ADR-0000 §9). Custom pricing goes through §2's bespoke-version mechanism instead, which gets the
  same outcome without the retroactive damage.
- **Storing balances instead of deriving them.** A credit or wallet balance kept as a column is a
  second source of truth that drifts from the ledger under concurrency. Balance is `SUM` over
  append-only entries (§3), always.
- **Float money, anywhere in billing.** `Numeric(_, 2)` breaks on JPY and KWD and on exact
  proration, and is unfixable once invoices exist (ADR-0000 §8). Integer minor units throughout.
- **A trial as its own object or an activation flag.** It is a subscription state with an end date
  (§4); anything else duplicates the lifecycle and invites the two to disagree about whether a
  customer is trialling.
- **Stripe-first.** Produces abstractions the more-constrained Razorpay cannot satisfy, discovered
  after the interface has many call sites (§6).
- **Commerce inventing a new enforcement point.** No new gate, no new claim: it drives the existing
  activation → `ent` → 402 path (§5). A second enforcement mechanism is a second thing to get wrong.

## Consequences

- The commercial model rests entirely on decisions already made irreversibly in ADR-0000. Nothing
  here re-opens money representation, plan-version immutability, or ledger append-only-ness — this
  ADR is largely the act of *not* contradicting them under commercial pressure, which is where they
  are most likely to be contradicted.
- Per-customer pricing, grants, comp access and trial reporting are all **queries over immutable
  rows**, not mutable state to keep accurate. The cost is paid up front in write discipline
  (everything is append-only) and repaid every time someone asks "what did this customer have, and
  when?" and the answer is a `SELECT` rather than an archaeology project.
- The operator console (ADR-0003) becomes a **consumer of the transition event stream** rather than
  a reporting query over live commercial tables. That coupling is loose and durable, but it means
  the events must carry the ADR-0000 §7 envelope from the first subscription — a thin envelope
  cannot be enriched retroactively for consumers already written against it.
- Five services is real operational weight — five schemas, five outboxes, more consumers, more DLQ
  to watch. It is justified by the boundary in §1: the definitional layer, the lifecycle, the meter,
  the money and the provider genuinely change at different rates and fail in different ways, and
  folding any two together would couple things the platform will later want to scale, rewrite or
  swap independently.
- Because entitlement already exists and works, commerce can be built **behind a running platform**:
  activations are switched by hand today, and each service replaces a manual step with a governed
  one without a flag-day cutover. Suspension via de-entitlement is already exercised by the 402 gate;
  billing just becomes the thing that pulls the switch.

## Open, for a later ADR (as written 2026-08-24; every item is decided in "Decisions on open questions" below)

Recorded so they are not mistaken for settled:

- ~~**Dunning and the grace window**~~ — **settled by [ADR-0005](0005-dunning-and-grace.md).**
  Three retries at T+3d/T+7d/T+12d inside a 14-day grace window; `past_due` keeps entitling,
  suspension happens when the window closes and not on a failure, a captured payment recovers from
  either rung, and nothing is ever cancelled by a timer. What remains open there is narrower: a
  per-plan grace window if one is ever sold, and immediate rather than eventual de-entitlement.
- **Tax computation and jurisdiction** — ADR-0000 §9 fixes that statutory rates are versioned by
  `effective_from` and a re-run for March uses March's rates. *Which* rate applies to a given
  customer, and where GST/VAT logic lives (a `billing` concern or its own service), is unsettled.
  *Interim rule since 2026-09-23:* one deployment-wide rate code, exclusive, on the subtotal, in
  force on the period's start — [ADR-0005's billing-run addendum](0005-dunning-and-grace.md) §5,
  which also records trial conversion, the invoicing cadence and the plan-change rule this ADR
  left open (upgrades prorate now, downgrades wait for the next period).
- **Multi-currency on one account** — the schema carries an ISO currency per amount, so it is
  *possible*; whether one organization can hold subscriptions or ledgers in more than one currency,
  and how that reports, is deferred until a customer needs it.
- **Revenue recognition** — invoices and collection are in scope; recognizing revenue over a
  subscription term (deferred revenue schedules) is a finance concern layered on the append-only
  invoice and ledger record, not modelled here.

---

## Decisions on open questions (2026-09-24)

The three items still open in "Open, for a later ADR" above are decided here. That list is left as
written so the history reads; read it through this section. Dunning, the first item, was already
settled by ADR-0005. In-flight work that this section builds on and does not contradict: the
billing-run addendum to [ADR-0005](0005-dunning-and-grace.md) (backlog R2) with its interim tax
rule, and [ADR-0019](0019-statutory-portal-credentials.md) for where a *tenant's* GST portal
credentials live. The register of every ADR question is
[open-questions-register.md](open-questions-register.md).

### Q1. Tax computation and jurisdiction, including OpsGrids' own GST on what commerce bills — **DECIDED (direction; built by O6)**

**Facts this rests on.** `billing.tax_rates` exists (global, un-policied, `rate_bps`,
versioned by `effective_from`) and nothing reads it yet (`services/billing/app/models.py`). The
billing run (R2) adds an interim rule: one deployment-wide `BILLING_TAX_CODE` (and optional
`BILLING_TAX_JURISDICTION`), the rate in force on the period's start, exclusive, computed **once on
the subtotal** with `Money.percent_bps` half-up, written as its own line; unset means no tax line,
and a code with no rate in force refuses to invoice. `billing.invoices.number` is unique per
organization and R2 derives it from a hash of the invoice key, so it is idempotent but not
consecutive. Finance already has the Indian GST rules as pure functions for its tenants:
`gst_amount_minor` (once, half-up), `split_gst` (odd paisa to SGST), `is_intra_state` (export and
SEZ are inter-state by statute), `parse_gstin` (check digit), `financial_year`; a per-organization
`InvoiceNumberSeries` allocated with an atomic upsert; the IRP seam (`services/finance/app/irp/`);
and GSTR-1 assembly (`assemble_gstr1`).

**Decision.**

1. **The supplier is the legal entity that operates the deployment**: OpsGrids' own GSTIN, legal
   name and state, held as billing's deployment settings (`BILLING_SUPPLIER_GSTIN`,
   `BILLING_SUPPLIER_LEGAL_NAME`, `BILLING_SUPPLIER_STATE_CODE`). Billing validates the GSTIN's
   check digit at startup and refuses to boot if a tax code is set with no valid supplier GSTIN.
   There is one supplier per deployment. It is not a tenant row, and it is not finance's
   `gst_registrations`, which hold a tenant's own registration as a seller of its own goods.
2. **The recipient is the customer organization.** Its tax identity is a **billing profile owned
   by billing**: legal name, GSTIN (optional; no GSTIN means B2C), state code and billing address.
   It is tenant-scoped under RLS and written by the organization's admin under two new ORG-only
   permissions, `core.billing.profile.read` and `core.billing.profile.write`, which need
   ADR-0007's reconcile in the deploy that ships them. The profile is **snapshotted onto each
   invoice at finalize**, the same way finance snapshots `seller_gstin` and `buyer_gstin`. Billing
   never reads finance's `customers` or `gst_registrations`: that would be a cross-schema read,
   and a tenant does not have to own the finance product.
3. **Billing computes the tax.** The R2 interim rule stays as it is and becomes the IGST case of
   the general rule. The GST total is `round_half_up(subtotal × rate)`, computed once on the
   invoice's taxable subtotal. It is then split by place of supply:
   - **IGST** takes the whole total when the place of supply differs from the supplier's state, or
     when the recipient is SEZ or export.
   - Otherwise **CGST** is `⌊total / 2⌋` and **SGST** is the remainder. That is finance's
     `split_gst` rule, so the heads always add back to the total.

   Place of supply is the recipient GSTIN's state (B2B). Without a GSTIN it is the profile's state
   if an address is on record, and otherwise the supplier's state (IGST Act s.12(2)). Each tax head
   is its own invoice line, so the invoice still foots. Line-level heads for an e-invoice or GSTR-1
   are the header heads **allocated** to the taxable lines by largest remainder
   (`Money.allocate`). They are never recomputed per line, so the header and the lines cannot
   disagree. The SAC code belongs to the catalog **product**. It defaults to one deployment-wide
   `BILLING_DEFAULT_SAC`, whose value is **DECIDED (default; owner/CA may revise)**. In O6,
   finance's pure GST functions move to `libs/platform-core` as `platform_core.gst`. They now have
   two concrete users, finance and billing, and copying them would give two places to get GST
   wrong.
4. **The tax-invoice series lives in billing and belongs to the supplier.** It is not per customer
   organization. CGST Rule 46(b) requires a consecutive serial number of at most 16 characters that
   is unique within a financial year. So there is one counter row per `(series, financial_year)`,
   with an `invoice` series and a `credit_note` series. The number is allocated **inside the
   finalize transaction** with the same atomic `INSERT … ON CONFLICT DO UPDATE … RETURNING` that
   finance's `allocate_invoice_number` uses. A rolled-back finalize therefore consumes no number,
   and a draft never holds one. R2 finalizes only after the record step, so a voided draft leaves
   no gap. The format is `OG/2627/000001` (14 characters). The per-organization `number` that R2
   derives stays as the idempotent internal reference, and the tax-invoice number is an additional
   column. The counter row has no `organization_id` because it is the supplier's and not a
   tenant's. It is an **explicit RLS exemption** beside `tax_rates` (ADR-0002 §5), documented in
   the model and pinned by a billing test, and the app role gets INSERT/UPDATE on that table only.
5. **Once an invoice has a tax-invoice number, it is corrected with a credit note and never
   voided.** The credit note takes a number from the `credit_note` series and references the
   invoice, as GST requires and ADR-0000 §9 already says. `finalized → void` stays legal only for
   invoices finalized before O6, which have no tax-invoice number.
6. **Finance keeps OpsGrids' own returns, and its IRNs once they are required, under OpsGrids' own
   organization.** A finance consumer of `commerce.invoice.finalized` (and the credit-note
   equivalent) imports billing's tax invoice **verbatim** into the operator organization's book:
   billing's number, lines and heads. Finance checks that the document foots and never recomputes
   it; a document that does not foot is dead-lettered with an alarm. GSTR-1/3B for OpsGrids'
   supplies then come from machinery that already exists. The IRN is requested through finance's
   IRP pipeline **only once the owner confirms OpsGrids' AATO has crossed the e-invoice threshold**
   (O4's gate) — **DECIDED (default; owner may revise)**. The IRN and QR come back to billing as an
   event and are stored in an append-only billing table beside the frozen invoice. This consumer is
   the one finance write that binds to an organization other than the envelope's. It binds only to
   the configured `FINANCE_OPERATOR_ORG_ID`, accepts only envelopes whose `producer` is `billing`,
   and never writes to the customer's tenant.

**Why.** The tax must be computed where the money is charged. Payment collects billing's
`total_minor`, so if anything else computed the tax, the tax invoice and the amount collected could
differ by a paisa, and a tax invoice that disagrees with the money cannot be defended (ADR-0000 §9).
The customer must be able to read their own tax invoice through their own tenant
(`core.billing.invoice.read`, RLS); a document stored in OpsGrids' finance organization would need
a cross-tenant read to show it. GST numbering belongs to the supplier: per-customer series would
give every customer an `INV-0001` under one GSTIN. Returns and IRN filing already exist in
finance, and building them a second time in billing would duplicate the part of the platform that
is hardest to get right.

**Rejected.** Issuing the tax invoice from finance under OpsGrids' organization: the customer
could not read it without crossing tenants, and commerce's core document would depend on a product
service. A per-organization series: it does not comply. R2's hash-derived numbers as GST numbers:
they are not consecutive. A Postgres `SEQUENCE`: it leaves gaps on rollback, and every gap becomes
an audit explanation. Copying finance's GST functions into billing. Per-line tax computation in
billing: it contradicts R2's once-on-subtotal rule for no statutory gain, and allocation already
gives every line a head within one minor unit of its exact share. A separate credential path for
OpsGrids' own filings: as a taxpayer, OpsGrids' portal login is the operator organization's
finance credential row, stored like any tenant's. Only the platform's ASP/GSP client credential is
a deployment secret ([ADR-0019](0019-statutory-portal-credentials.md) §4).

**Consequences.** Until O6 ships, the platform's invoices are **commercial invoices, not GST tax
invoices**: no supplier GSTIN, no heads and no Rule 46 number. The owner must not issue them as tax
invoices. O6 is several slices: the billing profile with its two permissions and ADR-0007
reconcile, supplier settings, the series table and its exemption test, tax-head lines, credit
notes, the `platform_core.gst` move, finance's operator-organization import consumer, and the IRN
back-channel. The finance profile becomes part of what OpsGrids needs for its own compliance; the
edge box already runs it. Every finalize serialises for one statement on one counter row per
financial year. At hundreds of invoices a day that cost is not visible, and it is the only shape
that keeps the series gap-free.

### Q2. More than one currency on one account — **DECIDED**

**Decision.** An organization has **one billing currency**, fixed by its first subscription that
is not cancelled. A subscribe whose pinned version is priced in any other currency is refused with
422, naming the organization's currency. The credit ledger stays keyed by currency (its balance
index is already `(organization_id, currency, valid_from)`), so an existing grant in a
different currency is never silently converted. A customer that needs to be billed in two
currencies becomes **two organizations**, not one organization with mixed ledgers (ADR-0000 §1:
one person may belong to many organizations). INR is the default. A non-Indian customer is billed
in its own currency from its first subscription, and exporting to it — zero-rated under a LUT —
is added to O6's supply types when the first such customer exists.

**Grounding.** `subscriptions.currency` is already documented as "ADR-0004 defaults: one currency
per org for now", and `_change_plan` already refuses a cross-currency change. But `_subscribe`
(`services/subscription/app/services.py`) never checks the organization's other subscriptions,
so today one organization can hold an INR subscription and a USD one.

**Rejected.** Per-subscription currencies inside one organization: it breaks every sum a report
or the credit ledger takes, and prepaid packs cannot be drawn across currencies. FX conversion at
invoice time: it adds a rate source, a rounding policy and a reconciliation problem, and no
customer has asked for it.

**Consequences.** One guard in `_subscribe`, with a negative test. **Not covered by any ledger
item. New backlog item needed.**

### Q3. Revenue recognition — **DECIDED (no ledger now; one column now)**

**Decision.** Commerce does not model deferred revenue. Recognized revenue is a **derived report**
over finalized invoices that are not void, never a stored balance:
- recurring lines are recognized over their service window, day-weighted by overlap with each
  month;
- usage lines are recognized in the window they measured;
- proration and closing lines are recognized over their own window.

One thing has to be captured now because it cannot be backfilled later. `billing.invoice_lines`
are append-only (UPDATE is revoked), and an R2 renewal invoice mixes advance recurring lines for
the new period with arrears usage lines for the old one. So **every invoice line gets its own
nullable `service_start` and `service_end`**, written at pricing time. Lines written before that
column exists keep the invoice's period, and are exact for every invoice that has only recurring
lines.

**Why.** It follows the reasoning of ADR-0009 §7 and this ADR's §7: defer what can be rebuilt from
stored rows, and capture now what cannot. The report can be written on the day an auditor or the
owner needs Ind AS 115 figures. A line's service window cannot be written after the line exists.

**Rejected.** A deferred-revenue schedule table written at finalize: it is a second financial record
that must agree with the invoice forever, built for a report nobody runs yet. Recognizing revenue
at invoice date: it is wrong for a yearly plan.

**Consequences.** The report is built when the owner or a statutory audit asks for recognized
revenue by month; the likely home is Q4's analytics projection. The line-window column is a small
billing change to make after R2 merges. **It is not covered by any ledger item. New backlog item
needed.**

## Addendum (2026-09-24) — §6 in practice: lookup before create (D3)

Razorpay offers no idempotency key on a payment create, so sending ours on the call makes nothing
idempotent at the provider: a charge resumed after a crash between the provider call and
recording its answer could be created twice. The adapter contract (`payment/app/providers/
base.py`) is therefore **lookup before create**:

1. `ensure_order(receipt=<charge id>, …)` finds the charge's Order by its `receipt` and creates
   it only if Razorpay holds none. The receipt is the charge's own id — 36 characters, globally
   unique and stable — not the platform idempotency key: Razorpay caps `receipt` at 40
   characters (`invoice:<uuid>` is 44) and does not enforce its uniqueness, so a key two tenants
   could share would not be safe to look up by;
2. `find_payment(order_id=…, charge_id=…)` returns a payment already made against that Order
   (matched on the `notes.charge_id` the platform stamps), if any;
3. only then `charge(order_id=…)` creates one.

The Order id is recorded on the charge (`charges.provider_order_id`). A charge also carries a
**lease** (`attempts`, `next_attempt_at`, `lease_until`), claimed with one conditional
`UPDATE … RETURNING`, so only one caller at a time — a request, its own HTTP retry, or the
stuck-charge sweep — may call the provider for it; the sweep backs off between attempts. At the
budget it does **not** fail the charge blind: the last attempt's create may have reached Razorpay
with its answer lost, and failing it would send dunning to debit again. It makes a **lookup-only**
call (`find_order` by receipt, then `find_payment`; never a create) and records a payment it finds;
only if Razorpay holds none does it fail the charge (`failure_reason = attempts_exhausted`) and emit
`commerce.payment.failed` so dunning proceeds. If the lookup itself cannot be made it is retried a
bounded number of times, then the charge fails as `lookup_unavailable` with an alert. A charge
failed for either of those reasons still accepts a late `payment.captured` — the money moved, so
the record follows it — while a failure Razorpay reported stays final. The verified webhook also
records the provider payment and Order ids when the charge has none (a call that outlived its
timeout was never finalized), and it takes the same row lock as the sweep and finalize. The lease
is validated at start-up to be at least twice the provider call timeout plus a minute, and an
adapter's per-request timeouts over the three wire calls must sum under that timeout. The live
wire (real Razorpay HTTP) is out of scope here and belongs to the provider-integration work
(B5g); the stub adapter keeps the Orders and payments a real account would, so the contract is
exercised end to end in tests. A Stripe adapter forwards the key to Stripe's native idempotency
header and may answer the two lookups trivially. The full claim/call/record contract is
`docs/io-outside-transactions.md`.

**The live adapter is bound by this, and there is a test that says so.** The Razorpay adapter is
stubbed until B5g gives it live keys. When it does, it must implement exactly the contract above
— `ensure_order` (`GET /orders?receipt=…`, `POST /orders` only if that found none), `find_order`
(the GET alone, never a create), `find_payment` (`GET /orders/{id}/payments`, matched on
`notes.charge_id`), `charge` only after that found none — and declare the connect and read
timeout of each request it makes (`request_timeouts_seconds`), whose sum stays inside
`provider_call_timeout_seconds`. That is not left to prose:
`services/payment/tests/provider_contract.py::ProviderContract` is the same statement as tests —
`ensure_order` is idempotent by receipt, `find_order` never creates, `find_payment` finds the
payment made against its own Order, the provider call creates at most once per receipt however
many times, and from however many processes, it is made, and the declared timeouts fit the budget.
The stub passes it today; the live adapter's test module subclasses it and passes it unchanged
before it can merge. `test_provider_contract.py` also runs each check against an adapter broken
in the way the check guards against, and requires it to fail.

## Addendum (2026-09-24) — webhook identity and permanent conditions (PAY-A)

Two defects in how a verified Razorpay webhook was recorded, both invisible while the wire is
stubbed and both fatal once real events flow. Neither changes §6's contract; both sit in the
inbox (`provider_events`, insert-only) and the reconciliation in front of it.

### The event id is a header, and the inbox never keys on `''`

A real Razorpay body is `{entity: "event", account_id, event, contains, payload, created_at}` —
**no top-level `id`**. The per-delivery event id is the `X-Razorpay-Event-Id` **header**. The
adapter read it from the body and defaulted the missing value to `''`, so `(organization_id, '')`
was the dedupe key of every event an organization received: the first was recorded, every later one
was dropped as a "duplicate" and answered 200 — so Razorpay stopped retrying and the capture or
failure was never reconciled, no `commerce.payment.*` event reached subscription, and dunning was
never opened or recovered. The suite's own bodies synthesised an `id`, which is why nothing saw it.

The id the inbox is idempotent on is now resolved in `domain.resolve_event_id`, in this order:

1. the `X-Razorpay-Event-Id` header, if it is a usable id (printable ASCII, no whitespace, at
   most 120 characters — the column — and not claiming the `derived:` prefix);
2. an id the body itself carries (a fixture, or a future provider whose body has one);
3. `derived:` + the SHA-256 of `(provider, event name, entity id, created_at)` — the body's own
   identifying fields, the same on every redelivery of one event and different for a different
   one. Derived only from all three; an id built from less would fold distinct events together.

A webhook that yields none is **refused, 400, with no state change** — a delivery nothing tells
apart from any other cannot be deduplicated. Genuine Razorpay deliveries always carry the header, so
this is a hand-built or mangled request. The signature is still verified over the **raw body**
before anything else, and a body that verified but is not a JSON object is refused the same way
(`MalformedWebhook`, 400) rather than surfacing as a 500.

The header is **not** covered by Razorpay's HMAC, which is over the body only. So it is a delivery
id and nothing more. Reconciliation does not depend on it for correctness: a redelivery under a
fresh id is a no-op on the charge's state (no second event), and the derived id is computed from
signed bytes. The worst a forged header can do is suppress an event whose id the forger already
knows, which requires a body Razorpay signed for the same organization.

The database now says the same thing: `provider_events` has
`CHECK (length(provider_event_id) > 0)`, created **`NOT VALID`** — enforced on every new row,
without failing an upgrade on a legacy `''` row, which the append-only trigger would make
uncorrectable. Nothing depends on validating it.

### A permanent condition is recorded and answered 200, never retried

`_reconcile_charge` called `assert_legal`, and `ingest_webhook` raised for a body naming no
organization. Both were a 422, rolled back whole by the route's transaction — the
inbox row, the audit row and the state change together. Razorpay retries any non-2xx for days, so a
webhook that could never succeed was redelivered until its window closed, **and the authentic
evidence was kept nowhere**. A malformed organization id in `notes` was worse: a `ValueError`, a
500. A status code is a promise that a retry can help; these are the cases where it cannot.

`ingest_webhook` now records the inbox row and reconciles **inside a SAVEPOINT**. Reconciliation
raises a typed verdict, `domain.WebhookNotApplied` (deliberately not a `ProblemError` — it cannot
reach the HTTP layer), for each permanent condition. The SAVEPOINT rolls the attempt back — the
inbox row and any partial write, provider ids included — and the event is recorded once more, with
its outcome stated, because `provider_events` is insert-only and a disposition can only be written
when the row is:

| `disposition` | `disposition_reason` | Meaning | Operator action |
|---|---|---|---|
| `ignored` | `unknown_charge` | The `charge_id`/payment named is not this organization's (another tenant's is invisible under RLS) | none |
| `conflict` | `illegal_transition` | An outcome the state machine forbids: `captured -> failed`, or `failed -> captured` for a failure the **provider** reported | look — money may have moved |
| `conflict` | `amount_mismatch` / `currency_mismatch` | The payment is not for the charge it names (integer minor units and ISO currency, exact) | look |
| `conflict` | `provider_id_conflict` | The webhook asserts a payment or Order id another charge of the organization already holds | look |

`processed` is the default: reconciliation ran without meeting any of these (it may still have been
a no-op — an intermediate event, a redelivered outcome). A reason code is a code, never free text,
and never any part of the body.

Each recorded case also writes an audit row `payment.webhook.ignored` (entity `provider_event`,
`after` = the reason, the event's name and id, the charge's id and state, integer amounts or ISO
currencies — nothing from the body) and one structured log line of the same name, at `error` for a
`conflict` and `warning` for `ignored`. The answer is 200, with `ignored_reason` on the ack. A
redelivery of an ignored event is deduplicated like any other: the first delivery kept its row.

The amount and currency checks run first, before the charge is touched, on every event that carries a
payment entity (`captured`, `failed`, `authorized`); a body that omits either is not a mismatch —
what the provider does not say cannot contradict the charge. **D3's rule is unchanged:** `failed ->
captured` stays legal when the charge's `failure_reason` is one of the platform's own codes
(`attempts_exhausted`, `lookup_unavailable`) — the money moved, the record follows it.

A webhook that names **no usable organization** — a payment made on the same Razorpay account
outside this platform, or a `notes.organization_id` that is not an id — has no tenant to record
under (every table carries `organization_id`, and the GUC has nothing to bind). It is logged
(`payment.webhook.unattributable`: the event's id and name only) and answered 200, because the
provider redelivering it can never make it attributable.

**What is non-2xx.** Only these: a bad signature (**400**, unchanged), a webhook that cannot be
identified or read (**400**), and a *transient* failure to record it (**503**: the database was
unreachable or the pool exhausted — the transaction rolls back whole, the event id is not burned,
and the redelivery lands as a first delivery). Nothing else. A defect is still a 500, on purpose:
only the typed verdict is treated as a permanent condition, so a bug is never recorded as
`ignored` and answered 200.

**Seeing them.** There is no Prometheus counter: `/metrics` is `platform_core`'s shared surface with
no per-service hook, and the platform's convention is that a metric is a query over append-only
rows. So:

```sql
SELECT disposition, disposition_reason, count(*)
FROM payment.provider_events
WHERE disposition <> 'processed' AND received_at > now() - interval '1 day'
GROUP BY 1, 2;
```

and an alert on the log event `payment.webhook.ignored` at level `error` (a `conflict`).

**Rejected.** Answering these 4xx with a "please stop" header — Razorpay documents none, and a
non-2xx is a retry by definition. Updating the inbox row after the fact — it is append-only, and
weakening the trigger for one column would weaken it for all. Recording the row and *then*
reconciling in the same statement order, without the SAVEPOINT — a partial reconcile (provider ids,
an audit row) would survive a conflict. A blanket `except` around reconciliation — it would turn a
defect into silently lost payments. A digest of the whole body as the fallback id — it changes
whenever the provider re-serialises an otherwise identical event, where the chosen tuple does not.
