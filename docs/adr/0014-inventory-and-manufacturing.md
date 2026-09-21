# ADR-0014 — Inventory & manufacturing: ownership, quantities, ledger

**Status:** Proposed · **Date:** 2026-09-22 · **Plan:** [inventory/plan.md](../inventory/plan.md)

The textile & manufacturing inventory spec (lots, rolls, bins, BOMs, production, QC, job work,
MRP, costing) lands on top of what `operations` already ships. Several choices below are
**irreversible once customer stock exists** and must be settled before the first migration.

## 1. `operations` stays the single owner of stock, and grows manufacturing in-schema

`operations` already owns suppliers, warehouses, purchase orders, `inventory_items` and the
append-only `stock_movements` ledger. Issue-to-production, QC release and job-work receipt must
move stock **in the same transaction** as the document that causes the move. Putting BOM /
production / QC in a separate service would turn every material issue into a saga over RabbitMQ
with compensations — for no isolation benefit, because they share one ledger.

**Decision:** inventory, BOM, production, quality and job work live in the `operations` schema.
The service splits internally by module (`app/inventory/`, `app/manufacturing/`, `app/quality/`,
`app/jobwork/`), each keeping the standard anatomy (`selectors`, `services`, `domain`, routes).
Rejected: a new `manufacturing` service (distributed transactions on the hottest path).

Finance, storefront/sales and notification integrate **only by events** (outbox → RabbitMQ).
No cross-schema FKs (ADR-0000 / CLAUDE.md).

## 2. Quantities are scaled integers, never floats

`on_hand` is `Integer` today; fabric is 82.5 m. Money is integer minor units, and quantities
follow the same rule: every quantity column is `BIGINT` in the UOM's **base sub-unit**
(`uoms.scale`: m → 1000 (mm), kg → 1000 (g), pcs → 1). The wire carries a decimal string plus the
UOM code; conversion happens in `domain.py` with exact integer arithmetic and explicit rounding.

Per-roll conversions (82.5 m ↔ 14.8 kg) are stored **on the roll** (primary + secondary qty),
never derived from a universal factor. Item-level `uom_conversions` apply only where the
conversion is fixed (box = 12 pcs).

## 3. The ledger is the truth; balances are a locked projection

- `inventory_ledger` (evolves `stock_movements`): append-only, `UPDATE`/`DELETE` revoked from the
  app role, one row per bucket delta. Bucket = `(item, lot?, roll?, location, status)`.
- `inventory_balances`: one row per bucket, updated **in the same transaction** as the ledger
  insert, under `SELECT … FOR UPDATE` on the bucket row(s) taken in a deterministic order (item,
  location, lot, roll) to avoid deadlocks. `CHECK (qty >= 0)` unless the org's
  `allow_negative_stock` flag is set.
- Available = balance in `AVAILABLE` status − active reservations. Reservations never touch the
  ledger (they do not move stock); they are a separate table, locked with the balance row.
- Every write carries an `Idempotency-Key`; the ledger has a unique
  `(organization_id, source_type, source_id, source_line, bucket)` so a retried post is a no-op.
- A nightly reconciler recomputes `SUM(ledger) = balance` per bucket and raises
  `inventory.inconsistency_detected` on drift.

## 4. Status changes are ledger moves

Quality hold → available, available → at-job-worker, in-transit, etc. are pairs of ledger rows
(−source bucket, +destination bucket) with the same `move_id`. Physical total is unchanged, and
history keeps every state change.

## 5. Traceability is a graph, not columns

`lot_genealogy (parent_lot, child_lot, move_id)` and `roll.parent_roll_id` + `roll_events`
record splits, merges, consumption and output. Forward/backward trace is a recursive CTE over
these tables.

## 6. Documents follow explicit state machines in `domain.py`

Transfers, GRNs, stocktakes, production orders, job-work orders, inspections and reservations
each get a `*_TRANSITIONS` map like `PO_TRANSITIONS`. A reversal is its own transition that
posts opposite ledger rows; nothing moves backwards silently.

## 7. BOM versions are immutable

Same as plan versions (ADR-0000 §9): a published `bom_version` is never edited; production
orders pin `bom_version_id`.

## 8. Costing: weighted average first, FIFO layers kept from day one

Every inbound ledger row stores `unit_cost_minor`; a `cost_layers` table is written for every
receipt so FIFO can be switched on per org later without backfill. Valuation is computed in
`operations`. Finance receives `inventory.valuation_posted` events; GL posting is finance's job
once it has a general ledger (it does not today).

## 9. Permissions follow the house grammar

The spec's `inventory.adjust` etc. map to `operations.<module>.<resource>.<action>`, e.g.
`operations.inventory.stock.adjust`, `operations.manufacturing.production_order.issue`,
`operations.quality.inspection.approve`, `operations.jobwork.order.receive`. Adjustments,
stocktake approval and negative-stock override are `is_dangerous`. Role templates stay built
from the operations catalogue only.

## 10. The existing `inventory_items` table is split, not dropped

`inventory_items` today mixes item master and per-warehouse balance. Migration: create `items`
(master) + `inventory_balances`, backfill one balance per existing row into the warehouse's
default bin, and convert each `stock_movements` row into ledger rows with the same UUIDs. The
existing API routes are kept as a compatibility view until the frontend moves over.

## Consequences

- `operations` becomes the largest service; the module split is required, not optional.
- The scaled-integer quantity and the ledger bucket key cannot change after go-live.
- Sales dispatch depends on a sales-order source (storefront orders today); CRM sales orders
  integrate by event when they exist.
