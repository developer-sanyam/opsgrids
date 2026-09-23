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

## Open, for a later ADR

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
  which also records trial conversion, the invoicing cadence and the "priced from the next
  period" plan-change rule this ADR left open.
- **Multi-currency on one account** — the schema carries an ISO currency per amount, so it is
  *possible*; whether one organization can hold subscriptions or ledgers in more than one currency,
  and how that reports, is deferred until a customer needs it.
- **Revenue recognition** — invoices and collection are in scope; recognizing revenue over a
  subscription term (deferred revenue schedules) is a finance concern layered on the append-only
  invoice and ledger record, not modelled here.
