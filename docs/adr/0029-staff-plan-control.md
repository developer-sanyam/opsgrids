# ADR-0029 — Staff plan control: what staff may change, and what every change is made of

**Status:** Accepted by the owner, 2026-10-06 — first release (hardening and plan switching) built on
`feat/admin-plan-switch`, not deployed · **Date:** 2026-10-06 ·
**Services:** `subscription`, `catalog`, `console` (and `opsgrids-internal`) ·
**Design:** [admin plan switch](../superpowers/specs/2026-10-06-admin-plan-switch-design.md)

Builds on [ADR-0000](0000-phase-0-decisions.md) §8 (money is integer minor units) and §9 (plan versions are
immutable; invoices, usage events and the ledger are append-only); [ADR-0002](0002-row-level-security.md) (every
console table is tenant-scoped under forced RLS); [ADR-0003](0003-surfaces-and-authorization-topology.md) (the
operator surface and its role matrix); [ADR-0004](0004-commercial-model.md) (the pin, the transition stream and the
events); and [ADR-0005](0005-dunning-and-grace.md) addendum §4 (a paid downgrade waits for the next period; the
billing run applies it). It amends ADR-0005's account of which event a deferred change emits (decision 6).

## Context

Staff could not change a customer's plan from the console. The pieces existed in the owning services — an operator
change-plan on subscription, a catalog read, a bespoke-price flow on console — but none was safe to put behind a
button: a retry produced a 422, a 409 or a duplicate audit row; the operator's reason went nowhere (the transition's
`reason` is `VARCHAR(60)` and types the emitted event); two operator writes on subscription accepted a token from
*any* service that held one for it; every business refusal was a 422 with no machine-readable cause; and nothing
told the operator what a change would do before it was made.

A design review verified each of those against the code and found one rule that could not be built as first
proposed: a staff "change now, prorated" on a paying subscription. Billing never credits (a downgrade can only be
deferred), an immediate upgrade charges a card for a plan the customer did not choose and moves entitlement before
payment, there is no mandate or amount preview in console, and it reverses an owner-reviewed rule that staff changes
on paying subscriptions wait for the next period.

## Decision

1. **What staff may change, and when it lands.** A **trial** switches immediately and free. A **paying**
   subscription switches at its **next billing period**. An **equal-price** change on a paying subscription applies
   now with no proration — but because the pin moves now, the usage metered so far is closed at the current plan's
   usage rates and invoiced by the billing run, and later usage is priced on the new plan. The preview says when
   that happens (`usage_closed_now`, decided by the write's own rule), and the confirm copy says "nothing is
   charged" only when it does not. **The server computes the direction** (subscription's own list-price rule, recurring
   flat and per-seat at quantity one, usage ignored); the console shows what the preview says and never decides it.
   There is **no immediate-proration path and no immediate-downgrade path** to ask for: the `timing` the console
   accepts is `auto` or `next_period`, which mean the same thing. Goodwill for a customer who should have the new
   plan sooner goes through the existing comp-credit action. A later, owner-only "now, prorated" for upgrades would
   need a billing quote endpoint, a mandate precheck, the billing run verified on, the expected-state guard and
   owner sign-off; none of that exists, and nothing here forecloses it.
2. **Who.** `owner` and `billing_ops` (`OperatorBilling`). `support` is not admitted to any of the three routes: the
   picker returns amounts, including a customer's negotiated bespoke price. A UI role hint is a hint; the server
   refuses regardless.
3. **Every action is previewed, confirmed, claimed, guarded and audited.**
   * A **server-computed preview** (`plan-change:preview`) is subscription's own evaluation — the same function the
     write runs, so the two cannot disagree. It carries direction, when it lands, whether it replaces a scheduled
     change, the products added and removed, a usage-price warning, and the live `expected` state. **No amount**: a
     paid change is priced by the billing run from catalog when it claims the period, so there is no quote to read,
     and a second implementation in console could disagree with the first invoice. A refusal is part of the answer.
   * A **required `Idempotency-Key`** backed by `console.idempotency_keys`: a row unique on
     `(organization_id, key)`, written **before** the downstream call and committed with an *intent* audit row, then
     moved to `succeeded`, `refused` or `unknown` with the replayable response and an *outcome* audit row in one
     transaction. Two concurrent requests with one key make one change (the loser is 409 `request_in_progress`); a
     retry replays the recorded result — success or refusal — without a second call; the same key with a different
     body is 409 `idempotency_key_reused`. The fingerprint is what the request *decided* (organization, product,
     subscription, target, the state the operator confirmed); the free-text reason is excluded so rephrasing it on a
     retry is still a replay. The table is tenant-scoped under forced RLS, written under the app role bound to the
     **target** organization, and deliberately not a directory-reader table.
   * An **expected-state guard**: `{state, plan_version_id, pending_plan_version_id}` from the preview, compared by
     subscription under its row lock, *before* any other rule. A mismatch is 409 `state_changed`.
   * A **reason**, 1–500 characters, required.
4. **The reason is a note, not the transition's reason.** The transition's `reason` stays a short machine code (it
   is 60 characters and `decide` types the emitted event from it). The operator's words are the audit row's `reason`
   (text) and **nothing else** — they are not put on the `commerce.subscription.*` event. That event is stamped
   with the customer's organization, and notification renders a customer org's own templates from the whole
   payload, so a `{{note}}` placeholder in a rule the customer's Notification author wrote would mail staff's
   internal reason to the customer. A deferred change keeps its note on `pending_change` (subscription's own row)
   and it becomes the applied transition's audit reason. Free text is checked against `RESERVED_REASONS` on **every** write that
   takes words — change-plan, subscribe, suspend, reactivate, and the customer cancel (where `renew` used to emit a
   `renewed` event for a cancellation) — and refused with 422 `reserved_reason`. This also removes a latent 500:
   suspend, reactivate and cancel accepted 200 characters into a 60-character column.
5. **Operator writes on subscription are console-only.** `suspend` and the operator `subscribe` now take the same
   `ConsoleCallerDep` the other operator routes had; any service holding a token for subscription could previously
   open or suspend a subscription for any organization. The new preview route takes it too.
6. **Refusals are machine-distinguishable, and the right one comes first.** Business refusals carry
   `errors[0].code` (the shape every other service's coded refusal uses; no change to `platform-core`): `state`,
   `cadence`, `currency`, `already_pinned`, `product_mismatch`, `not_published`, `not_found`, `reserved_reason`,
   `bundle`, `not_offerable` (422); `state_changed`, `billing_catching_up`, `request_in_progress`,
   `idempotency_key_reused` (409); `outcome_unknown` (a write's answer was lost), `upstream_refused`,
   `catalog_unavailable` and, for the read-only preview, `preview_unavailable` — the last three say nothing was
   changed (503). **Precedence:** `state_changed`, then every refusal that is a property
   of the request or the subscription's state, and only then `billing_catching_up` — the one transient refusal. A
   past-due subscription is no longer told to "try again shortly" for a change that will never be allowed. (While
   `BILLING_RUN_ENABLED=false` nothing claims the due period, so `billing_catching_up` is indefinite and the
   operator surface says so.) A version restricted to *another* organization is the same 404 `not_found` as one that
   does not exist, **whatever its state** (the audience check precedes the published check, so another
   organization's draft never answers `not_published`): a distinct `not_visible` code would disclose it.
7. **A deferred change keeps its operator and is labelled by its direction.** The scheduled-change and
   withdrawn/superseded audit rows carry `acting_as`; a second scheduled change names the one it replaced in
   `before`; a change that applies now over a waiting one leaves a `change_superseded` row (it used to clear it
   silently). The direction is stored with the pending change, and the event the billing run emits when it applies
   it is `upgraded` for a price increase and `downgraded` for a decrease. **Amends ADR-0005 addendum §4**, which said
   a deferred change always emits `downgraded`. Every consumer — product, account, console, notification — folds the
   two events identically (they read the payload's state, pin and members), verified before changing it; a pending
   change scheduled before this ships has no stored direction and keeps the old label.
8. **What the picker offers.** Catalog's console-only internal read returns active plans' published versions that
   are public or negotiated for exactly the named organization (`visible_plans`' audience rule), and drops a version
   when a newer published version of the same plan has the same audience, cadence and currency — publishing does not
   grandfather the old one, so it stays pinnable by id and would otherwise be offered beside its successor.
   **The same set is enforced, not only shown.** A UI gate hides a control; it does not protect data: a picker
   loaded before a newer version was published or the plan retired, or a direct call with any published id of the
   product, would otherwise pin a withdrawn price — subscription's pin lookup sees only that a version is published
   and whose it is. So console re-reads the offerable set on every preview (after subscription's own refusals) and
   every confirm (before the claim, so a catalogue outage claims nothing; enforced after the replay branches, so a
   recorded success still replays), and refuses anything else with 422 `not_offerable`, recorded and replayed like
   any refusal. The one exception is a withdrawal back to the pin the subscription is on, which is often a
   superseded version and never a new price.
9. **Outcome unknown is said plainly and reconciled, not guessed.** A timeout, transport failure or 5xx after the
   request left marks the claim `unknown` and answers 503 `outcome_unknown`. A retry with the same key reads the
   live subscription: if the target is already pinned or scheduled it records and returns that; otherwise it makes
   the call, which the expected-state guard keeps from applying twice if the first attempt was merely late. If that
   call is told `state_changed`, the live subscription is read again before the answer is recorded: a late first
   attempt that committed in between is the change landing, recorded as succeeded (reconciled), not a refusal the
   key would replay forever. A claim left `in_progress` past a 60-second lease (a crashed process) is reconciled the
   same way. A failed **preview** is never `outcome_unknown`: it is a read with no key, and says nothing was changed.
10. **One operator id format on new flows: `operator:<sub>`.** The older suspend/reactivate delegation sends the bare
    `sub`; changing it would rewrite what existing audit rows say, so it is left (recorded below).

## Consequences

* **The billing run must be on for "next billing period" to happen.** A scheduled change is applied only when the run
  claims the next period; while `BILLING_RUN_ENABLED` is off, or before `BILLING_RUN_NOT_BEFORE`, it waits, and
  changes on overdue subscriptions answer `billing_catching_up`. The production setting could not be verified from
  the repository. **Verify both before enabling the console route** (RUNBOOK, "Staff plan switching").
* **Entitlement lag is ~15 minutes** after a change takes effect (outbox, product consumer, token refresh). The
  confirm and result copy say so.
* **A scheduled change emits no event**, so console's projection never shows it; the live pricing composite is the
  only place it appears, which is why the dialog is driven from it.
* **Migration:** one additive console migration (`console.idempotency_keys`). Rollback is the frontend and console
  images; subscription and catalog changes are additive (new optional fields, new routes). Written data stays: the
  transition, audit and outbox rows are append-only.
* **Deploy order:** catalog and subscription first, then console and the `opsgrids-internal` frontend. The
  admin-gateway is not recreated: the new paths sit under `/api/v1/admin/organizations`.
* A **stale preview** is a 409 `state_changed`, not a silently different change.
* `e2e/conftest.py`'s `operator_call` now mints its token as `console` (the harness stands in for console); it did
  not run here.

## Not built (and not blocked)

Cancel, add product, extend trial and the Plans page are later releases of the same design. This release leaves
room for each: the claim table is generic over `action`, the coded-error vocabulary is open, and `ExpectedState`,
the note and the reserved-reason guard are on the shared path. Still open, and not decided here: end-of-period
cancel (needs the billing run to honour `cancel_at`), reopening after a cancel, gating owner hand-activation of a
product, per-action operator step-up, and moving the older suspend/reactivate delegation to `operator:<sub>`.
