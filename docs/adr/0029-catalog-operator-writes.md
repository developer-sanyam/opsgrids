# ADR-0029 — Catalog operator surface: role-split reads, reasoned idempotent writes, withdraw

**Status:** Accepted by the owner, 2026-10-06. Built on `feat/admin-plans-page`, not deployed ·
**Date:** 2026-10-06 · **Service:** `catalog` (+ `opsgrids-internal`) ·
**Design:** [admin Plans & prices page](../superpowers/specs/2026-10-06-admin-plans-page-design.md)

Builds on [ADR-0000](0000-phase-0-decisions.md) §8 (integer minor units), §9 (plan versions are immutable,
subscriptions pin a version) and "Also settled" (authorization is server-side); [ADR-0003](0003-surfaces-and-authorization-topology.md)
(the operator role matrix, updated by this change); [ADR-0004](0004-commercial-model.md) §2 (bespoke versions are
`restricted_to_org_id`). ADR-0027 is reserved for another change and is not this one.

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
   today's product registry (a bundle with no registry to ask is refused). A plan's `product_key` must be a
   registered, non-platform product at creation.
4. **Withdraw is the one change a published row may take.** The row trigger now allows exactly
   `state: published -> grandfathered` with every other column identical (compared as whole rows, so a later
   column is covered) and refuses any update of a grandfathered row. Withdraw writes an audit row and emits
   `catalog.plan_version.grandfathered`; existing pins keep the version, nothing new may pin it.
5. **Drafts can be deleted; retired plans reactivated** (`catalog.plan.reactivated`); a draft that loses the
   version-number race retries inside a SAVEPOINT (three attempts, then 409).
6. **The product registry is read before the transaction opens** (D3, `docs/io-outside-transactions.md` #10,
   now fixed): a dependency declared ahead of the session dependency fetches it and the services receive a
   `ProductSnapshot`, never the HTTP client. A failed read is kept and raised (503) only by a check that needs
   it — a plain version drafts and publishes while product-service is down.

## Consequences

- The existing `/admin/catalog` write routes changed contract: each now requires a body with `reason` and the
  `Idempotency-Key` header, and the plan writes answer with the table row shape (`PlanListItemOut`) instead of
  the plan with every version embedded. The only caller was the console stub; console's own bespoke-price path
  (`/admin/internal/catalog`) is untouched and keeps its content-based idempotency.
- `PlanVersionOut` gains `plan_id`, `plan_name`, `product_key`, `plan_state`; `PriceOut` gains `id`; prices and
  features come back in a deterministic order. Additive for every reader.
- **Retiring does not yet stop a pin by version id.** Subscription's `_validate_new_pin` does not read plan state;
  making it refuse new pins on a retired plan is a subscription change deferred to a later slice. Until then
  retire hides the plan from the customer browse (`selectors.visible_plans` reads only active plans), and a version that must stop selling is withdrawn as well.
- `idempotency_keys` grows by one row per owner write and has no purge; at owner-write volume that is negligible.
  A purge (or a TTL sweep) is a follow-up if that ever changes.
- Rollback: the frontend and catalog images roll back independently. Migration `b7e4d2a9c613` downgrades
  cleanly (drops the claim table, restores the old trigger body); a version withdrawn meanwhile stays
  `grandfathered`.
