# ADR-0033 — The customer's own plan change: preview, offerable plans, and which prices a tenant sees

**Status:** Proposed for owner sign-off — built on `fix/customer-subscriptions-page`, not deployed ·
**Date:** 2026-10-06 · **Services:** `subscription`, `catalog`, `frontend/opsgrids-dashboard` ·
**Design:** [customer Subscriptions page](../superpowers/specs/2026-10-06-customer-subscriptions-page-design.md)

Builds on [ADR-0004](0004-commercial-model.md) §2 (a bespoke price is an audience-of-one version) and
§4 (one event per accepted move); [ADR-0005](0005-dunning-and-grace.md) addendum §4 (a paid downgrade
waits for the next period, no credit); and [ADR-0029](0029-staff-plan-control.md), whose preview, coded
refusals, expected-state guard and "only what catalog offers now" rule this extends from the staff path
to the customer's. Complies with the 2026-09-24/25 owner rule recorded in commits `c0fda10a` and
`33baa3eb` (released in #59): pricing is agreed per client, so catalog's tenant reads carry no amounts
and the dashboard shows no list price.

## Context

On 2026-10-06, after the billing run converted the owner organization's ended trials to `active`, the
customer Subscriptions page (`app.opsgrids.com/subscriptions`) was wrong in four ways, each verified in
code:

1. The Change plan dialog said *"Takes effect immediately"*. The customer `change_plan` applies a paid
   **downgrade** at the next period (no credit), a paid **upgrade** now with a prorated charge, a trial
   change now and free, and an equal-price change now. Nothing told the customer which.
2. The picker read catalog's tenant `GET /plans` and listed every published version — superseded ones
   too, since publishing does not grandfather the old version (ADR-0029 §8) — so "Revenue Growth"
   appeared twice beside "Revenue", with no plan in force marked. The write accepted any of them:
   a direct call could pin a superseded or retired-plan version.
3. The "Trial ends" column kept showing a converted trial's end ("5 Oct 2026 (0 days)") on an
   `active` subscription.
4. No row said which plan it was on, its cadence, or that a change was scheduled.

## Decision

1. **The customer asks the server what a change would do before making it.**
   `POST /api/v1/subscriptions/{id}/change-plan/preview` runs the customer write's own evaluation
   (`_evaluate_change_plan` with no forced timing and no registry) without locks or writes, and answers
   direction, `effective` (`now` | `next_period`) and `effective_at`, `paid`, **`prorated_charge_now`**
   (exactly the write's `_claim_proration` condition), what it replaces, the products it adds or removes,
   usage notes and the `expected` state. A refusal is an answer, with ADR-0029's codes plus `bundle`
   for a bundle subscription (whose write is a 403). No amount: the billing run prices a change when it
   raises the invoice. The staff preview now shares the same function (`_preview_change_plan`) and gains
   the two additive fields; console ignores fields it does not read.
2. **The confirm carries what the customer saw — including what the billing run decides.**
   `ChangePlanIn` takes an optional `expected`; the write compares it under the row lock (409
   `state_changed`). Optional so an older caller keeps working; the dashboard always sends it. On the
   customer path it is `CustomerExpectedState`: state, pin and pending change **plus the period end and
   the billing run's pending lifecycle claim** (both required, null included), read before the
   preview's evaluation. Those two decide *when* a change lands and *whether money moves*, and neither
   state, pin nor pending moves when the run records or claims a renewal or claims a trial's
   conversion — so without them a downgrade previewed "on 5 Nov" was scheduled for 5 Dec after a roll,
   and a trial switch previewed as free raised a prorated charge after a claimed conversion. The run's
   claim and record take the same row lock, so the comparison holds for the write's transaction. The
   staff path keeps the three-field `ExpectedState` console builds.
3. **Only what is on offer — enforced, not only shown.** The customer write refuses a target outside
   catalog's offerable set for the organization (422 `not_offerable`), checked after the request's own
   refusals and before `billing_catching_up`. Re-pinning the plan in force is exempt: it withdraws a
   waiting change ("Keep current plan"), even when that plan is no longer offered — **unless the OpsGrids
   team scheduled that change (§6)**. Catalog's `offerable-versions` internal read now admits
   `subscription` as a caller beside `console` (subscription already holds the unfiltered pin lookup, so
   this widens nothing it could read).
4. **Plan reads for the page.** `GET /api/v1/subscriptions/{id}/plan-options` (the write's permission,
   `core.product.activation.write` at ORG) returns the offerable set with the plan in force first and
   disabled, the scheduled one marked and disabled (`pending`: picking it again would replace it with
   itself), and cadence/currency/bundle blocks with a reason; `change_blocked_code` is `bundle`, `state`
   or `scheduled_by_opsgrids`. `GET /api/v1/subscriptions/plan-summaries`
   (`core.product.activation.read` at ORG) gives each row its plan, cadence, version, the scheduled plan,
   when it lands — the boundary the write schedules against and the billing run applies at — and who
   scheduled it (`pending_scheduled_by`: `opsgrids` or `customer`). A **cancelled** subscription reports
   no scheduled change: it never reaches another period (§7). Both sit under the existing
   `/api/v1/subscriptions` prefix: no gateway route-table change. Both filter on the caller's
   organization explicitly as well as through RLS, as do the change-plan write and cancel.
5. **Which prices a tenant sees.** An amount is returned only for a version **negotiated for this
   organization** (`restricted_to_org_id` = the caller's organization), and only to a caller holding
   **`core.billing.invoice.read` at ORG** (the people who already see what the organization is billed;
   `ORG_ADMIN` has subscription read but not invoices). A public version's amount is a list price and is
   **never** sent to a tenant — not in the picker, and not for the public version the organization is
   itself on, because every trial is pinned to one and showing it would publish the list price to every
   trialist the Marketplace deliberately does not show it to. Catalog's tenant `/plans` stays amount-free.
   The rule is one function (`plan_options.amount_visible`); widening it is a one-line owner decision.
   The customer preview answers **no `direction`** when nothing is charged (a trial with nothing claimed):
   such a change is now and free either way, so the comparison is never needed, and answering it would let
   a trialist compare every public plan against a negotiated amount they can see.
6. **A change the OpsGrids team scheduled is theirs to change.** Console's Set price and the staff plan
   switch schedule a paying subscription's negotiated price from the next period, with an operator as
   the actor. While such a change waits, **every customer change-plan and preview is refused 422
   `scheduled_by_opsgrids`** — withdrawing it ("Keep current plan"), replacing it with another scheduled
   change, or superseding it with one that applies now would each undo an agreed deal with nothing but an
   audit row to say so, and nothing tells staff. The dashboard labels such a change "agreed with
   OpsGrids", does not offer Keep current plan for it, and disables Change plan with who to contact. Staff
   change or withdraw it on the staff path as before. A change stored before the actor was recorded on it
   (before 2026-09-23, earlier than any staff path that schedules one) counts as the customer's own.
7. **Cancelling drops a waiting change.** `services.cancel` applies a change whose boundary has already
   passed (unchanged) and then clears any change still waiting, with its own audit row
   (`commerce.subscription.scheduled_change_cancelled`, reason `subscription_cancelled`): the billing run
   never rolls a cancelled row, so the change would otherwise sit on it for good while the cancel confirm
   says it "will not happen". Rows cancelled before this still carry one; the reads above do not report
   it. No data migration.

## Consequences

* The customer sees, before confirming, whether a change lands now or at the period end and whether a
  prorated charge is raised now. There is still no amount before the invoice; the copy says so.
* A customer on a public plan sees its name and cadence but no price in the table; the price is on their
  invoices (for those who may read them). A negotiated price is shown as "agreed for your organisation".
* **Deploy order:** catalog before subscription. Until catalog admits `subscription`, the new reads and
  every customer change-plan answer 503 `catalog_unavailable` and change nothing. The dashboard renders
  the table without the plan line when its read fails. No migration; rollback is the images.
* `BILLING_RUN_ENABLED` still decides whether "next period" ever arrives (ADR-0029 Consequences); while it
  is off a paying customer's change answers `billing_catching_up` once a period is overdue, and the copy
  tells them to try later or contact OpsGrids.
* A version negotiated for an organization stays offerable to it until staff withdraw it on the Plans
  page (ADR-0030); a customer moved off a bespoke price by staff can otherwise move back to it.
* **Timing discloses a comparison for paid subscriptions.** On a paid subscription the timing answer is
  the requirement — a downgrade waits, an upgrade applies now with a proration, an equal price applies
  now without one — so the preview necessarily tells an organization whether a public plan is above,
  below or equal to the price it is on (and, against a negotiated amount it can see, that public plan's
  list price exactly when the answer is "equal"). No amount is sent, but §5's "no list-price information
  reaches a tenant" holds only for trials. Owner question 3 below.
* A confirm that crosses a billing-run roll or claim is refused once and re-previewed; the dashboard
  returns to the picker on `state_changed`.

## Numbering

Drafted as ADR-0032; renumbered 0033 because `main` took 0032 for the payment provider seam and checkout.

## Deviations from the brief, recorded

* **List prices are not shown.** The brief asked for prices in the picker and the table "(public list
  price or the org's own bespoke price)". The owner's rule (`c0fda10a`: "the change-plan picker on the
  Subscriptions page lists plans by name only"; `33baa3eb`: "a client must never receive a list price")
  is the more specific, earlier and owner-made decision, so only the organization's own negotiated price
  is shown, and public plans show their cadence. Open question for the owner below.
* **The dedicated price read lives in subscription, not catalog.** Whether a version is the
  organization's own, and what the page needs beside it (the plan in force, the scheduled change, the
  cadence the subscription renews on), is subscription's knowledge; catalog's tenant reads are unchanged.

## Open questions for the owner

1. Should a customer ever see a **public** plan's price in Change plan (for example a published price
   list), or the price of the public plan they are on? Today: no, per the 2026-09-24 rule.
2. Should the customer path ever be allowed to schedule a paid **upgrade** for the next period instead of
   prorating now (as staff changes do)? Today the customer path keeps its existing direction-based rule.
3. Is it acceptable that a paid subscription's preview discloses whether a public plan costs more, less
   or the same as the price the organization is on (Consequences)? The alternative is to stop answering
   timing before the confirm, which reintroduces the "takes effect immediately" problem this page fixed.
4. §6 refuses every customer change while a staff-scheduled change waits. Should a customer instead be
   allowed to **upgrade** past it (superseding the agreed price with a dearer plan), with staff notified?
   Today: no — the agreed deal stands until staff change it.
