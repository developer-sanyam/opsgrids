# ADR-0030 — Catalog operator surface: role-split reads, reasoned idempotent writes, withdraw

**Status:** Accepted by the owner, 2026-10-06. Built on `feat/admin-plans-page`, not deployed ·
**Date:** 2026-10-06 · **Service:** `catalog` (+ `opsgrids-internal`) ·
**Design:** [admin Plans & prices page](../superpowers/specs/2026-10-06-admin-plans-page-design.md)

Builds on [ADR-0000](0000-phase-0-decisions.md) §8 (integer minor units), §9 (plan versions are immutable,
subscriptions pin a version) and "Also settled" (authorization is server-side); [ADR-0003](0003-surfaces-and-authorization-topology.md)
(the operator role matrix, updated by this change); [ADR-0004](0004-commercial-model.md) §2 (bespoke versions are
`restricted_to_org_id`). ADR-0027 is reserved for another change and is not this one; ADR-0029 is the staff
plan-control record (merged in #105). This record was first drafted as 0029 and renumbered before merge so the
two never share a number.

## Context

The staff console's Plans page was a stub: catalog's `/admin/catalog` prefix had owner-only writes and no read,
and those writes were unsafe to put behind a button (admin_synth H2, H3, H5, B6):

- no reason and no idempotency — a retried publish was a 422 "already published", a retried create a second
  plan or a 409;
- publish validated nothing — a usage price with no `meter_key` would fail billing on every invoice of every
  pin, permanently, and a zero, an arbitrary three-letter currency or an unregistered product key all froze;
- there was no way to stop selling a version (`published -> grandfathered` was blocked by the immutability
  trigger like any other edit) while a `grandfathered` row was not protected by that trigger at all;
- drafts could not be discarded, retired plans could not be reactivated, and `create_version` raced on
  `uq_plan_versions_plan_version` with no recovery.

## Decision

1. **Reads are `owner` and `billing_ops`; writes are `owner` only.** `support` reads nothing here: the catalogue
   carries every audience-of-one version, which is every customer's negotiated price. Reads are paginated
   (keyset), default to the public audience, and load a version's sheet only when it is opened.
2. **Every write carries a reason and a required `Idempotency-Key`.** The reason lands on the audit row
   (`AuditLog.reason`), never in an event payload. The key is claimed in `catalog.idempotency_keys` — unique
   `(organization_id, key)`, the request's fingerprint (action, target, body), the operator, and the response —
   **before** the change and in the same transaction, with `INSERT … ON CONFLICT DO NOTHING`. Same key and body
   replays the recorded answer (`Idempotent-Replayed: true`); a different body or operator is a 409; a missing key
   is a 422. A refused request rolls its claim back, so only changes that happened are replayed. The table is
   policied like `audit_logs` (platform sentinel organization, RLS enabled and forced).
3. **Publish validates the sheet and reports every problem at once** (`errors[]`): at least one price; a currency
   in `domain.SELLABLE_CURRENCIES`; a usage price names its meter and a rate; a per-seat price names its unit; no
   zero amount unless `allow_zero_amounts` is sent; `trial_days` re-checked; bundle members re-checked against
   today's product registry (a bundle with no registry to ask is refused). **A version of a registered bundle
   product must declare `bundled_products`** (`bundle_members_missing`): without it subscription entitles only
   the bundle's own key, which routes nowhere. Draft checks this when the registry answered; publish always
   checks it, so publishing needs the registry for every version. A plan's `product_key` must be a registered,
   non-platform product at creation. Problems name prices in the order every read returns them.
4. **Withdraw is the one change a published row may take.** The row trigger now allows exactly
   `state: published -> grandfathered` with every other column identical (compared as whole rows, so a later
   column is covered) and refuses any update of a grandfathered row; a draft cannot jump straight to
   `grandfathered`, and no price, feature or seat class can be inserted under (or moved onto) a published or
   grandfathered version. Withdraw writes an audit row and emits
   `catalog.plan_version.grandfathered`; existing pins keep the version, and no new subscription or new plan
   change can choose it. **A plan change already scheduled onto it still lands**: subscription's
   `apply_pending_change` re-pins at renewal without re-reading catalog, so a deferred downgrade requested before
   the withdraw applies anyway. The RUNBOOK has the query to run before withdrawing; re-validating the pending
   pin is deferred subscription work (Consequences).
5. **Drafts can be deleted; retired plans reactivated** (`catalog.plan.reactivated`); a draft that loses the
   version-number race retries inside a SAVEPOINT (three attempts, then 409).
6. **The product registry is read before the transaction opens** (D3, `docs/io-outside-transactions.md` #10,
   now fixed): a dependency declared ahead of the session dependency fetches it and the services receive a
   `ProductSnapshot`, never the HTTP client. A failed read is kept and raised (503) only by a check that needs
   it — a plain version drafts while product-service is down; publishing waits for it (decision 3).

## Consequences

- The existing `/admin/catalog` write routes changed contract: each now requires a body with `reason` and the
  `Idempotency-Key` header, and the plan writes answer with the table row shape (`PlanListItemOut`) instead of
  the plan with every version embedded. The only caller was the console stub. Console's own bespoke-price path
  (`/admin/internal/catalog`, `owner` and `billing_ops`) keeps its content-based idempotency but is narrowed:
  it reuses only a **published** version of exactly its own shape (one flat price, no seat classes) and never
  publishes a draft it did not just write — a matching draft is an owner's sheet under review, and publishing it
  there would bypass decision 1. It also reads the registry before its transaction, because its publish needs
  it (decision 3).
- `PlanVersionOut` gains `plan_id`, `plan_name`, `product_key`, `plan_state`; `PriceOut` gains `id`; prices and
  features come back in a deterministic order. Additive for every reader.
- **Retiring does not yet stop a pin by version id.** Subscription's `_validate_new_pin` does not read plan state;
  making it refuse new pins on a retired plan is deferred, and it needs **two** changes, not one: catalog's
  internal version read (`GET /internal/v1/plan-versions/{id}`, `InternalPlanVersionOut`) must carry
  `plan_state`, and subscription must refuse on it. Until then retire hides the plan from the customer browse
  (`selectors.visible_plans` reads only active plans) and from the staff plan switch (ADR-0029: console offers
  and accepts only `selectors.offerable_versions`, which is built on `visible_plans`, and answers anything else
  `not_offerable`), and a version that must stop selling is withdrawn as well. The console's retire dialog says
  exactly this.
- **The staff plan switch honours withdraw.** A withdrawn version is not published, so `offerable_versions`
  never offers it and subscription's own pin check refuses it. Withdrawn versions also supersede nothing: withdraw
  a plan's newest version and the published one before it is offered again, so withdraw that one too if it must
  stop selling. Staying on a withdrawn version is not a new pin: subscription's `_validate_new_pin` accepts the
  subscription's own current pin whatever its state, so a scheduled change can still be withdrawn back to it
  (before withdraw existed no current pin could be anything but published, so nothing exercised this).
- **Withdraw does not stop a scheduled change** (decision 4). Deferred subscription work: re-validate the pending
  pin in `apply_pending_change` (refuse or drop a change onto a version that is no longer published).
- `idempotency_keys` grows by one row per owner write and has no purge; at owner-write volume that is negligible.
  A purge (or a TTL sweep) is a follow-up if that ever changes.
- Rollback: the frontend and catalog images roll back independently. Migration `b7e4d2a9c613` downgrades
  cleanly (drops the claim table, restores the old trigger body); a version withdrawn meanwhile stays
  `grandfathered`.
