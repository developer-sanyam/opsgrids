# ADR-0030 — Staff Add product and Extend trial

**Status:** Proposed for owner sign-off — built to the owner decisions recorded in the build brief
(2026-10-06) on `feat/admin-add-product-trial` (stacked on `feat/admin-plan-switch`), not deployed ·
**Date:** 2026-10-06 ·
**Services:** `subscription`, `console` (and `opsgrids-internal`), consumers in `product`, `console`,
`notification` · **Design:** [admin add product and extend trial](../superpowers/specs/2026-10-06-admin-add-product-extend-trial-design.md)

Builds on [ADR-0029](0029-staff-plan-control.md), whose shared path this reuses unchanged: the
`console.idempotency_keys` claim written before the downstream call, the coded-refusal vocabulary,
`outcome_unknown` with a live-read reconcile (including its review fix: a retry's call refused
because a *late* first attempt just landed is recognised as that attempt, not replayed as a
refusal), the operator's reason as a note on the audit row only — never the transition's machine
reason and never on the event, which a customer org's notification templates can render — the
`RESERVED_REASONS` guard, the "only what catalog offers now" re-read (`not_offerable`), and
`operator:<sub>`. Also builds on ADR-0004 §4 (a
trial is a subscription state, one event per accepted move) and ADR-0005's addendum (the billing
run converts a trial at its stored end and voids a claim whose subscription moved under it).

## Context

Staff could switch a plan (ADR-0029) but not give an organization a product it did not have, and not
give a trialling customer more time. Both had owning-service pieces — the operator `subscribe` that
sells bundles; nothing at all for a trial end — and a design review found what made each unsafe to put
behind a button:

* **Add product.** `_subscribe` claims the first period at once for a paid start and nothing checks a
  payment mandate, so a failed charge opens dunning; it accepted a `one_time` version (never billed —
  a free subscription); it never compared currencies across the organization's subscriptions, while
  console's MRR sums amounts and takes the first currency; and its refusal of an existing row was an
  uncoded 409 that a concurrent request could turn into a 500 at the unique index.
* **Extend trial.** `trial -> trial` maps to `trial_started`, which console counts as a trial start
  and notification rules fire on. A relative "add N days" adds the time twice on a retry. The billing
  run voids a claimed conversion whose trial end moved (`still_due_for`), so an extension under a
  pending claim would void an invoice being priced. There is no trial-start column, and
  `MAX_TRIAL_DAYS` (365) bounds what a *version* may sell, not how far one trial may be stretched.

## Decision

1. **Who.** Add product: `owner` and `billing_ops` (`OperatorBilling`). Extend trial: **owner only**,
   through a new `OperatorOwner` alias in console — billing_ops and support are 403. A UI role hint is
   a hint; the server refuses regardless.
2. **Add product is a trial by default.** A paid start is refused with 422 `charge_not_acknowledged`,
   before anything is claimed or called, unless the request carries `acknowledge_immediate_charge:
   true` — the UI makes the operator tick that billing starts now and that no mandate is checked. The
   console route is `POST /api/v1/admin/organizations/{id}/subscriptions` with a required
   `Idempotency-Key`, `{product_key, plan_version_id, trial, acknowledge_immediate_charge, reason}`.
3. **What subscription refuses on the operator subscribe** (the route console's Add product drives,
   and the only way a bundle is sold — bundles stay allowed):
   * a row for the product **in any state, `cancelled` included** — 409 `already_subscribed` (one row
     per product per organization; reopening a cancelled subscription is a later decision). A lost race
     on the unique index is the same coded 409, not a 500;
   * a `one_time` version — 422 `one_time`;
   * a currency other than the organization's **live** subscriptions' (every state but `cancelled`) —
     422 `org_currency_mismatch`;
   * a trial on a version that sells none — 422 `trial_not_offered` (code added to the existing
     refusal).
   The operator subscribe is serialised per organization with a transaction-scoped advisory lock, so
   two staff adds cannot both pass the currency check. Console additionally refuses a version catalog
   no longer offers the organization (422 `not_offerable` — superseded by a newer version, or its plan
   retired; subscription's pin lookup cannot see either), re-read on every confirm before the claim;
   a catalogue outage there is 503 `catalog_unavailable` and claims nothing.
4. **The picker** (`GET .../add-product-options`, `OperatorBilling`) offers every product that publicly
   sells a plan and that the organization has **no row for in any state**, each with catalog's
   offerable versions for it (ADR-0029 §8's audience and supersession rule). A version the organization
   cannot take is listed, disabled, with the reason (`org_currency_mismatch`, `one_time`); a bundle
   whose members it already pays for separately names them (`overlapping_products`), because both are
   billed with no credit (docs/commerce-packaging.md).
5. **Extend trial is absolute and guarded.** `POST .../subscriptions/{product_key}/extend-trial` takes
   `new_trial_ends_at` (an instant with a zone) and `expected_trial_ends_at` (the end the operator saw,
   sent back verbatim). Subscription, under the row lock the billing run's claim takes, refuses in this
   order: `state_changed` (409, the end moved — which is also what makes a replay add nothing),
   `not_trial`, `claim_in_flight` (a pending conversion or renewal claim), then `trial_not_extended`
   (not later than the current end, or not in the future) and `trial_cap`.
6. **The cap.** `MAX_TOTAL_TRIAL_DAYS = 90`, measured from `current_period_start` — which nothing moves
   while a subscription trials. It is an **owner-adjustable default** in subscription's `domain.py`, not
   a law; a later release may make it per product or per plan. Subscription states it on the operator
   listing as `trial_max_ends_at`, which console passes through the pricing composite, so neither
   console nor the UI restates the number. A version that already sold a trial longer than the cap
   cannot be extended at all.
7. **A new event, `commerce.subscription.trial_extended`.** One `trial -> trial` transition with reason
   `trial_extended` (added to `RESERVED_REASONS`, and accepted by `decide` only on that self-edge),
   carrying the full lifecycle payload — whose `trial_ends_at` is the new end — plus
   `previous_trial_ends_at` (the operator's reason is the audit row's, not the event's). Product binds it and projects the new end (a
   customer whose old end had passed is entitled again). Console binds it, folds the new end, and
   records it under its own kind, so it is never counted as a trial start or a conversion.
   Notification lists it in `SUBSCRIBABLE_EVENTS`, so a rule can be written for it deliberately — none
   fires by default. Account does not bind it: an extension installs nothing.
8. **The dates are IST in the UI.** The operator picks the trial's new last day in IST; the request is
   the last second of that day with its zone (`…T23:59:59+05:30`); every date the dialog shows says
   "IST (UTC+05:30)". The rest of the console stays UTC.

## Consequences

* **Deploy order matters** (RUNBOOK, "Staff add product and extend trial"): product's and console's
  consumers (they bind the new routing key) first; then subscription; then console and the
  opsgrids-internal frontend. Subscription first would emit `trial_extended` into queues not bound to it
  — the product projection would keep the old end and lock the customer out while billing waited for
  the new one.
* **A paid add can fail to collect.** The acknowledgement says so; a mandate precheck (console reading
  payment's mandate state) is not built.
* **No migration.** Console reuses `console.idempotency_keys` (actions `add_product`, `extend_trial`);
  subscription stores nothing new. Rollback is the console and frontend images; subscription's changes
  are new refusals on a staff-only route and a new staff-only route; consumers binding an extra key are
  harmless. Written transition, audit and outbox rows stay.
* The customer subscribe path gains only the codes (`already_subscribed`, `trial_not_offered`) and the
  race fix; it still accepts `one_time` and mixed currencies (see below).

## Not changed, and not built

* **The customer subscribe path's `one_time` and currency rules** are unchanged: the owner decided
  these refusals for the staff path, and tightening self-serve is its own decision.
* **The picker's product list** comes from catalog's public-flat-plans read (the same source as the
  pricing screen), so a product whose only plans have no flat price, or whose only versions are
  negotiated for this organization, is not offered. Catalog has no "every product with an offerable
  version" read, and adding one to catalog was out of this track's scope.
* Cancel, reopening a cancelled subscription, a mandate precheck for a paid add, a per-product cap, a
  customer notice for an extension, and the unbuilt `trial_ending` notice.
