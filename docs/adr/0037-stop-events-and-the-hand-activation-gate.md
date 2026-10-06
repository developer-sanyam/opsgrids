# ADR-0037 — Stop events and the hand-activation gate

**Status:** Proposed for owner sign-off — built on `fix/subscription-stop-events-and-activation-gate`,
not merged or deployed · **Date:** 2026-10-06 · **Services:** `subscription`, `product`
(tests also touch `console`) · **No migration, no new or changed event type, no new consumer binding.**

Builds on [ADR-0004](0004-commercial-model.md) §4–§5 (one event per accepted move; an activation is
the OR of its sources) and [ADR-0005](0005-dunning-and-grace.md) addendum §4 (a deferred change takes
effect at the first period boundary nobody has claimed, and a stop that finds one applies it).
Prerequisite for any staff-initiated cancel (a later release): a cancel that can be silently dropped,
or undone by one owner request, is not a cancel.

## Context

Two defects, both found in review of the staff-cancel design, both verified in code, and both older
than that work.

**1. A stop can be dropped by the consumers.** A pending plan change whose boundary has passed is
applied by the next stop that finds it (`apply_lapsed_change`, called by `cancel`, the operator
`suspend` and `suspend_for_dunning`). Applying it called `_transition`, which stages a
`commerce.subscription.upgraded` or `downgraded` event. The stop's own event (`cancelled` or
`suspended`) is staged in the **same transaction**. An outbox row's `created_at` is `server_default
now()`, the transaction's start, and the envelope's `occurred_at` is that value, so the two events tie
to the microsecond. Both consumers refuse an event that does not advance what a subscription already
reflects: product's `project_subscription` drops `occurred_at <= watermark`, and console's
`domain._is_stale` drops `occurred_at <= applied`. The relay orders by `created_at` alone, so tied rows
leave in no defined order. When the change folded first, the stop was dropped. A cancellation is
terminal and never emits again, so nothing repaired it: the product stayed entitled and console counted
an active subscription for good. The trigger is any pending change whose boundary has passed, which is
the normal state of a staff "next period" change while `BILLING_RUN_ENABLED` is false.

**2. A hand activation undoes a cancellation or a suspension.** `POST /products/activations` (owner
scope, `core.product.activation.write`) upserts the product's own source row
`(organization, product_key, product_key)`. That is the row its standalone subscription folds into, so
after a cancel or a suspend the owner could set it `active` again with one request, with a trial of up
to 365 days that resets on every call. Nothing in the product service looked at a subscription, and a
cancelled subscription never emits again, so nothing would switch it back off.

## Decision

1. **A stop applies a lapsed change silently.** `apply_lapsed_change` calls
   `apply_pending_change(..., silent=True)`: the change is applied in full (pin, items,
   `entitled_products_snapshot`, the pending state resolved, the transition row and the audit row
   written) and **no event is staged**. The stop event that follows carries the final pin and
   `entitled_products`, which is everything a consumer would have learned from the announcement, and
   product already retires members absent from that payload. It is the shape of the existing suspended
   branch of `apply_pending_change` (the change takes effect, the record is written, no event). The
   billing run's own application, at a renewal boundary in the claim's transaction, is not a stop and
   still announces its change; it is followed by `renewed` in a later transaction, so the two never tie.
   `_transition` gained `emit_event` for this; every other caller leaves it on.
2. **Hand activation is refused for a product a subscription manages.** In `services.activate`, after
   the activation lock and before any write, every source row of the activation is read (fresh, so a
   fold that held the lock first is seen). If any carries subscription provenance the request is
   refused with **409, problem type `conflict`, `errors[0].code = "subscription_managed"`** and the
   message *"This product is managed by a subscription. Manage it on the Subscriptions page or contact
   OpsGrids."* Nothing is written: no activation change, no audit row, no outbox row. The predicate is
   one property, `ActivationSource.is_subscription_managed`: `source_subscription_id IS NOT NULL OR
   state_occurred_at IS NOT NULL`. The fold writes both on every row it touches, nothing else writes
   either, and a hand activation keeps whatever a fold left. It is read over **all** of the activation's
   sources (its own and any bundle's) in **any** status, including `suspended` and the `removed`
   tombstone a downgrade leaves, because a cancelled subscription leaves the row and nothing else.
   The error class is defined in the product service (product cannot import subscription; the
   contract between them is the event). Either column is enough: an event from before the payload named
   its subscription has only the time, and one without a time has only the id.
3. **Fresh organizations and hand-only organizations still activate (201).** A hand activation never
   writes provenance, so the gate never closes on one: activate, deactivate and reactivate, with or
   without `trial_days`, all work for an organization no subscription has fed. A **standalone**
   subscription whose first event has not folded yet is allowed through, and the fold then overwrites
   the row, because its own source is the very row the hand activation wrote: the subscription wins in
   either order (race-tested both ways). A **bundle** is different: it writes a row of its own for each
   member (`source_product_key = 'suite'`), so a hand activation of a member made before the suite's
   first event folded is not overwritten and outlives the suite's cancel. From the suite's first fold
   the gate refuses a second one. That is known leak (e) below, pinned by a test, found by the audit's
   query 2, and not closed here: closing it means the fold retiring a hand row, a policy change on
   legitimate gifts that is the owner's to make.
4. **No "trial already used" rule.** It would be bypassed by omitting `trial_days`, and it is not what
   this closes: the hole is a hand activation over a subscription's row, not a second trial.
5. **Deactivation of a live subscription-fed own source is refused too (409 `subscription_managed`).**
   A hand deactivation of a product its own subscription is serving lasts only until that subscription's
   next event and strands a paying customer in the meantime. Nothing depends on the route: the dashboard
   only `GET`s activations, no e2e journey or demo tool calls `DELETE`, and it is not in the demo tooling.
   A dead subscription's source, a hand activation and a bundle's inclusion (still 422 with its own
   words) are unchanged.
6. **The demo activation script says so.** `tools/demo/activate_product.py` writes the activation row
   on the admin engine and cannot refuse for the API's reasons. For an organization with no subscription,
   the case it exists for, nothing changes. For one a subscription has fed it warns on stderr and adds
   `subscription_managed: true` to its JSON line, then does what it was asked.
7. **Repair is the owner's, and nothing here writes data.** The gate closes the hole going forward. Orgs
   that already hand-activated after a cancel or a suspend are found by a **read-only** audit query
   documented in the RUNBOOK (and listed with the stop-event victims of defect 1, which look the same:
   a cancelled or suspended subscription beside an entitled activation). A repair is a one-off product-side
   write plus a console re-fold, and is deliberately not shipped. The queries list a row only when nothing
   a subscription still feeds keeps the product on: a customer who moved from a standalone product to a
   suite keeps a `cancelled` standalone row beside a live suite (a subscription keeps one row per
   organization and product for ever), and that product is correctly on. "On" is the gateway's own
   meaning (`active`, or a trial that has not lapsed). The queries are run as written, against seeded
   rows, by `subscription/tests/test_runbook_audit_queries.py`.
8. **A stop writes no source for a member its subscription never fed.** Decision 1 means a stop that
   applies a lapsed rung-up (duo to trio, `next_period`) names the *new* pin's members, including one
   the subscription never entitled. Product's fold used to write a row for every named member whatever
   the state, which made a `suspended` activation and source with subscription provenance for a product
   the organization never had, and the gate then refused the owner that product. `project_subscription`
   now skips a member with no row when the event de-entitles and the source already has rows: there is
   nothing to switch off, and the rows it does have carry the watermark, so an older event is still
   dropped. A stop that is the first event its source hears has no rows to carry the watermark and
   nothing says what was held, so it writes all its members as before (that needs the stop to overtake
   every earlier event of its subscription; the member then stays a suspended, subscription-managed row). The alternative, having subscription name
   only the members it held before the change, would make the stop's payload disagree with the pin it
   carries for every consumer, console included.

## Consequences

- A cancel, an operator suspend and a dunning suspend with a lapsed change now fold as stops whatever the
  delivery order. Regression tests in `subscription/tests/test_stop_event_folds.py` drive the real stop
  paths, fold the events subscription really staged through product's and console's real consumers (in
  subprocesses; `import app` is ambiguous across services), and assert the folded state: activation off,
  console `cancelled` or `suspended`. The old announcing behaviour is put back in the same file and shown
  to fold to an entitled product, which is the evidence the tests can fail. To keep the two sibling suites
  from truncating each other's schema while that runs, product's and console's truncating fixtures take
  the cross-service schema lock the employee, attendance and approval suites already share
  (`platform_core.testing`).
- The change is invisible to anything reading the lifecycle stream except in the stop paths: a lapsed
  change applied by a stop no longer appears as an `upgraded` or `downgraded` event. Its transition row
  and its audit row (`commerce.subscription.downgraded` or `upgraded`) still do. A report that counted
  lapsed-then-stopped changes from events loses those; one counting transitions or audit rows does not.
- The owner can no longer re-enable a product that a cancelled or suspended subscription fed. The
  message names the Subscriptions page; the owner's other route is OpsGrids staff, whose suspend lift
  (`reactivate`) is the sanctioned one.
- `DELETE /products/activations/{key}` answers 409 where it used to switch off a source that the next
  lifecycle event would switch back on. A client that relied on it has none known.
- A hand activation made before this release, over a subscription that never reached the fold, is not
  visible to the gate (it has no provenance). The audit query is the only net for those.
- **Known leaks this does not repair.** (a) Organizations that already hand-activated after a cancel or a
  suspend (the audit). (b) Defect 1's past victims: a subscription cancelled or suspended whose stop was
  dropped. (c) Provenance for old rows is thin: an activation older than `a6b1c8d3e9f2` (which added
  `activations.state_occurred_at`), carried into `activation_sources` by `b3d5f7a9c1e2`, has neither
  column until its first event, so it looks like a hand activation to the gate.
  (d) An organization with no subscription provenance can still self-grant any launched product, trial
  or not. That is the "no subscription" case, not a bypass of a cancel.
  (e) A hand activation of a bundle **member** made before the suite's first event folded (decision 3)
  outlives the suite's cancel. Query 2 of the audit lists it.

## Alternatives rejected

- **Make the stop's timestamp later than the announcement's**, by stamping the second event with a
  clock read instead of the transaction start. Every outbox row's `occurred_at` is `created_at`; two
  clocks in one transaction invite the same tie one level up, and a second source of time for events is a
  contract change for every consumer.
- **Loosen the consumers' guard from `<=` to `<`.** Two tied events would then both fold, in whatever
  order they arrive, and the outcome would be whichever came last: the bug moved from "first wins" to
  "last wins". It would also change the ordering rule of every consumer (product, console and any
  future one) to fix a producer that should not have staged the pair.
- **Order tied events by a sequence number in the envelope.** The right long-term shape, and a contract
  change (`libs/contracts`, every consumer, the relay's ordering) with its own review. Not needed once a
  transaction stops announcing what it is about to supersede.
- **Gate in the route by asking subscription.** A cross-schema read, a synchronous dependency of an
  owner write on another service being up, and a second answer to a question the product's own source
  rows already answer from the fold.
- **A "trial already used" or "one hand activation ever" rule.** See decision 4.

## Rollout

No migration. Deploy `product` and `subscription` in either order; nothing is bound or emitted that
the other must know. Product first is slightly better: from the moment it is live, a hand activation of
a subscription-managed product is refused. See the RUNBOOK section "Stop events and the hand-activation
gate" for the audit query and what to check afterwards. Rollback is the images; the rows written stay and
mean the same thing to the old code.
