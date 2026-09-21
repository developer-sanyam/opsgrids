# ADR-0014 — Inventory & manufacturing: ownership, quantities, ledger

**Status:** Accepted — Phase 0 implemented · **Date:** 2026-09-22 ·
**Plan:** [inventory/plan.md](../inventory/plan.md)

The textile & manufacturing inventory spec (lots, rolls, bins, BOMs, production, QC, job work,
MRP, costing) lands on top of what `operations` already ships. Several choices below are
**irreversible once customer stock exists** and must be settled before the first migration.
§2, §3 and §10 were revised during Phase 0 after checking them against the code and the driver.

## 1. `operations` stays the single owner of stock, and grows manufacturing in-schema

`operations` already owns suppliers, warehouses, purchase orders, `inventory_items` and the
append-only stock ledger. Issue-to-production, QC release and job-work receipt must move stock
**in the same transaction** as the document that causes the move. Putting BOM / production / QC
in a separate service would turn every material issue into a saga over RabbitMQ with
compensations — for no isolation benefit, because they share one ledger.

**Decision:** inventory, BOM, production, quality and job work live in the `operations` schema.
The service splits internally by module (`app/inventory/` first; `app/manufacturing/`,
`app/quality/`, `app/jobwork/` as their phases land), each keeping the standard anatomy
(`selectors`, `services`, `domain`). Models stay in `app/models.py` — one metadata, one alembic
history. Rejected: a new `manufacturing` service (distributed transactions on the hottest path).

Finance, storefront/sales and notification integrate **only by events** (outbox → RabbitMQ).
No cross-schema FKs (ADR-0000 / CLAUDE.md).

## 2. Quantities are integer milli-units of the item's stock UOM — one scale for every unit

Every stock quantity column is `BIGINT` holding **thousandths of the item's stock UOM**
(`QTY_SCALE = 1000`, `app/inventory/quantity.py`). 82.5 m is `82500`; 14.8 kg is `14800`;
0.125 kg is `125`; 1 piece is `1000`. The wire carries a **decimal string** (`"82.5"`) next to the
UOM code, and the API never accepts or emits a float for a quantity.

The first draft of this ADR gave each UOM its own scale (m → mm, kg → g, pcs → 1). Rejected on
review:

- the scale becomes part of a unit's identity forever — `pcs` at scale 1 can never hold half a
  set, and changing a unit's scale means rewriting every row that used it;
- every cross-unit operation becomes a rescale between two integer scales, which is exactly where
  rounding bugs live;
- one scale gives the resolution the per-unit design wanted (1 mm for metres, 1 g for kilograms)
  with none of the bookkeeping.

`NUMERIC` was checked too: asyncpg returns it as an exact `Decimal`, so it would also be lossless.
It is not chosen because the house rule for exact values is already scaled integers (money), and
integer arithmetic has no decimal-context or `Decimal(float)` traps in Python or SQL. `SUM`,
`CHECK` and indexes on `BIGINT` are the cheapest, and `BIGINT` milli-units still hold
9.2 × 10¹² units.

Rules that make it safe:

- **Input is parsed, never rounded.** More than three decimals is a validation error.
- **Discrete units are whole.** `pcs`, `box`, `carton`, `bundle`, `set`, `roll` must be multiples
  of 1000 milli; `1.5 pcs` is refused.
- **Fixed conversions are exact rationals** (`fractions.Fraction`): 1 m = 1000 mm, 1 kg = 1000 g,
  1 in = 25.4 mm. A conversion whose result is not a whole milli-unit raises unless the caller
  names a rounding mode.
- **Variable conversions are not conversions.** A roll's length and weight (82.5 m / 14.8 kg) are
  two measured quantities stored on the roll (Phase 1), never derived from a factor. Container
  units (`box` = 12 pcs) are item-specific `uom_conversions` rows (Phase 1); the fixed registry
  refuses them.
- **Attributes are not stock.** GSM, width and similar specs are item/lot/roll attributes, not
  ledger quantities, stored as integers in their own units (GSM, mm) from Phase 1.
- **Money × quantity** is `quantity_milli × unit_cost_minor / 1000`, rounded half-to-even once,
  at valuation — never per ledger row.

## 3. The ledger is the truth; balances are a locked projection

- `inventory_moves` is the document header (kind, reason, reference, note, idempotency key);
  `inventory_ledger` (the renamed `stock_movements`, UUIDs kept) holds one row per bucket delta.
  Both are append-only: `UPDATE`/`DELETE` revoked from the app role, re-revoked after every grant
  re-application in `migrations/env.py`. Bucket = `(item, location, status, lot?, roll?)`.
- `inventory_balances`: one row per bucket, updated **in the same transaction** as the ledger
  insert, under `SELECT … FOR UPDATE` on the bucket rows taken in a deterministic order (item,
  location, status, lot, roll) so two multi-line moves cannot deadlock. `CHECK (quantity_milli
  >= 0)` and `CHECK (0 <= reserved_milli <= quantity_milli)`.
- Available = `quantity_milli − reserved_milli` in the `AVAILABLE` bucket. Reservations never
  touch the ledger (they do not move stock); they are rows in `inventory_reservations` whose open
  remainder is cached on the locked balance row.
- `app/inventory/ledger.py::post_move` is the **only** writer of ledger and balances. A negative
  result, or an outbound move that would eat into reserved stock, is refused with the numbers
  (on hand, reserved, available); the `CHECK`s are the backstop, not the guard.
- A post may carry an idempotency key. `inventory_moves` has a partial unique index on
  `(organization_id, idempotency_key)`; the header is inserted with `ON CONFLICT DO NOTHING`, so a
  concurrent duplicate waits for the first transaction and then replays its result. The same key
  with a different request is a 409.
- A nightly reconciler (`bin/cron`) recomputes `SUM(ledger)` against the stored balance for every
  bucket, and the open reservations against `reserved_milli`. Drift becomes an
  `inventory_discrepancies` row, an `operations.inventory.inconsistency_detected` event and an
  error log line. **It never repairs** — a person resolves a discrepancy explicitly.

## 4. Status changes are ledger moves

Quality hold → available, available → at-job-worker, in-transit, etc. are pairs of ledger rows
(−source bucket, +destination bucket) with the same `move_id`. Physical total is unchanged, and
history keeps every state change.

## 5. Traceability is a graph, not columns

`lot_genealogy (parent_lot, child_lot, move_id)` and `roll.parent_roll_id` + `roll_events`
record splits, merges, consumption and output. Forward/backward trace is a recursive CTE over
these tables. The ledger's `lot_id`/`roll_id` columns exist from Phase 0 (so the bucket key never
changes); their foreign keys arrive with the `lots`/`rolls` tables in Phase 1.

## 6. Documents follow explicit state machines in `domain.py`

Transfers, GRNs, stocktakes, production orders, job-work orders, inspections and reservations
each get a `*_TRANSITIONS` map like `PO_TRANSITIONS`. A reversal is its own transition that
posts opposite ledger rows; nothing moves backwards silently.

## 7. BOM versions are immutable

Same as plan versions (ADR-0000 §9): a published `bom_version` is never edited; production
orders pin `bom_version_id`.

## 8. Costing: weighted average first, FIFO layers kept from day one

Inbound ledger rows carry `unit_cost_minor` (from Phase 0); a `cost_layers` table is written for
every receipt from Phase 8 so FIFO can be switched on per org without backfill. Valuation is
computed in `operations`. Finance receives `inventory.valuation_posted` events; GL posting is
finance's job once it has a general ledger (it does not today).

## 9. Permissions follow the house grammar

The spec's `inventory.adjust` etc. map to `operations.<module>.<resource>.<action>`, e.g.
`operations.inventory.stock.adjust`, `operations.manufacturing.production_order.issue`,
`operations.quality.inspection.approve`, `operations.jobwork.order.receive`. Adjustments,
reconciliation, stocktake approval and negative-stock override are `is_dangerous`. Role templates
stay built from the operations catalogue only.

## 10. `inventory_items` stays the item master; `on_hand` stops being stored

Splitting the table (the first draft) would rename the join key purchase-order receipts use and
break every client for no gain. Instead:

- `inventory_items` keeps being the item master and gains `home_location_id` — the warehouse's
  default bin, where the pre-Phase-1 single-location API posts.
- The stored `on_hand` column is **dropped**. `on_hand_milli` is a SQL expression over
  `inventory_balances` (physical stock: every status except `IN_TRANSIT`/`AT_JOB_WORKER`), still
  filterable and sortable in SQL — the ledger/balance projection is the only source of truth.
- The wire keeps `on_hand` (integer, exact while stock is whole units — nothing before Phase 1
  can post a fraction through the HTTP API) and adds exact decimal strings `on_hand_qty`,
  `reserved_qty`, `available_qty`. `StockMovementOut` keeps its shape, read from ledger + header.
- The migration refuses to run if any item's stored `on_hand` disagrees with the sum of its
  movements, rather than silently picking one; its downgrade refuses if any quantity carries a
  fraction or any stock sits outside an item's home bucket.

## Consequences

- `operations` becomes the largest service; the module split is required, not optional.
- The milli-unit quantity and the ledger bucket key cannot change after go-live.
- Sorting the item list by `on_hand` is a correlated aggregate; at 100k+ SKUs it needs an
  item-level rollup (Phase 10 performance work), not a second writable source of truth.
- Sales dispatch depends on a sales-order source (storefront orders today); CRM sales orders
  integrate by event when they exist.
