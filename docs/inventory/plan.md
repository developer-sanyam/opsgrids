# Textile & Manufacturing Inventory — assessment and production plan

Decisions: [ADR-0014](../adr/0014-inventory-and-manufacturing.md). Each phase below is a set of
**slices** (schema → API → UI → tests), shipped as its own PR, and none counts as done until
`make check`, migrations and the frontend build pass.

---

## 1. Assessment (audit of 2026-09-22)

### What exists and is reused

| Area | Where | Reuse |
|---|---|---|
| Tenancy | `org_id` on every table + RLS, transaction-local GUC (ADR-0002) | As-is. Every new table ships with a policy in its migration |
| Auth / authz | JWKS verification, `product.module.resource.action:SCOPE` grammar, per-product catalogue (`services/operations/app/catalogue.py`) | Extend the operations catalogue |
| Events | Transactional outbox + relay + idempotent consumers (`OutboxEvent`, `ProcessedEvent`) | Every stock-moving write emits via outbox |
| Audit | `AuditLog` mixin written in the same transaction | Every write, with before/after |
| Notifications | `services/notification` | Consume `inventory.*` alert events |
| Files | `services/document` + object storage (ADR-0009) | Import files, GRN attachments, adjustment evidence |
| Approvals | `services/approval` (ADR-0006) | Adjustments, stocktake variance, transfers above threshold |
| Suppliers, POs | `operations.suppliers`, `purchase_orders`, `purchase_order_lines`, `PO_TRANSITIONS` | Extend with GRN + partial receipt |
| Warehouses | `operations.warehouses` (code, name, location) | Becomes the root of the location tree |
| Item + balance | `operations.inventory_items` (sku, category, warehouse **string**, `on_hand` **int**) | Split into master + balances (ADR-0014 §10) |
| Ledger | `operations.stock_movements`, append-only, `UPDATE/DELETE` revoked, `on_hand_after >= 0` | Evolves into `inventory_ledger` |
| GST | `finance`: tax rates, GST registrations, e-way bills | HSN/tax on items; e-way bill on dispatch/transfer/job-work challan |
| Frontend | `frontend/product-operations` (Inventory, InventoryItemDetail, Warehouses, Suppliers, POs, PO form), `opsgrids-ui`, `opsgrids-client` (generated) | New routes in the same app and design system |

### Gaps

Item master attributes/variants, UOM + conversions, lots, rolls, zone/rack/bin locations, stock
statuses, reservations, transfers, stocktake, BOM, production, wastage, QC, job work, MRP,
purchase requisitions, GRN, costing/valuation, barcode/QR, import/export, reports, mobile scan.

### Risks

1. **Integer `on_hand`** cannot hold 82.5 m → scaled-integer migration (ADR-0014 §2) must land
   first; everything else builds on it.
2. **`warehouse` stored as a name string** on items and POs → backfill to `location_id`.
3. **`services.py` is 1,467 lines** → module split before adding domains (ADR-0014 §1).
4. **Concurrency** on reserve/issue → bucket-row locking + idempotency keys, proven by
   concurrent tests, not assumed.
5. **Scope:** the spec is ~15 slices. Shipping it as one PR is not reviewable; phases are.
6. **Sales source:** no sales-order service yet; dispatch starts from storefront orders and a
   manual dispatch note.

---

## 2. Domain model (operations schema)

| Group | Tables |
|---|---|
| Master | `uoms`, `uom_conversions`, `item_categories`, `items`, `item_attributes` (EAV: key, type, value), `item_variants` |
| Location | `warehouses` (+ `factory_id`, `kind`), `locations` (tree: zone → rack → bin, `path` ltree, `barcode`) |
| Tracking | `lots`, `rolls` (`parent_roll_id`, primary + secondary qty), `roll_events`, `lot_genealogy` |
| Stock | `inventory_ledger`, `inventory_balances` (bucket = item, lot, roll, location, status), `cost_layers` |
| Documents | `goods_receipts` (+lines), `purchase_requisitions` (+lines), `transfers` (+lines), `adjustments` (+lines), `stocktakes` (+lines), `reservations`, `dispatches` (+lines) |
| Manufacturing | `boms`, `bom_versions`, `bom_components` (+ `substitutes`), `production_orders`, `production_materials`, `production_outputs`, `production_wastage`, `wastage_thresholds` |
| Quality | `qc_templates` (+params), `inspections`, `inspection_results` |
| Job work | `job_work_orders`, `job_work_materials`, `job_work_receipts` |

Statuses: `AVAILABLE, RESERVED*, ALLOCATED*, QUALITY_HOLD, QUARANTINE, DAMAGED, REJECTED, WIP,
IN_TRANSIT, AT_JOB_WORKER, BLOCKED`. (*Reserved/allocated are derived from `reservations`, not
ledger buckets — ADR-0014 §3.)

Available = AVAILABLE bucket − active reservations − allocations. Physical = sum of all buckets
except IN_TRANSIT/AT_JOB_WORKER, which are reported separately.

---

## 3. Phases

### Phase 0 — Foundations — **DONE** (2026-09-22) · reference: [ledger.md](ledger.md)

Built:
- `app/inventory/` module (quantity, ledger kernel, reconcile, selectors, services). Inventory and
  warehouse code moved out of `app/services.py` / `app/selectors.py`; procurement stays there.
- Milli-unit quantities (ADR-0014 §2, revised to one scale for every unit), `Fraction`
  conversions for fixed units, and a code registry of units.
- Migration `d4e8b1c93f20`: `locations` (default bin per warehouse), `inventory_moves`,
  `stock_movements` → `inventory_ledger` in place, `inventory_balances`, `inventory_reservations`,
  `inventory_discrepancies`; `inventory_items.on_hand` dropped. Refuses on pre-existing drift;
  reversible.
- `post_move` / `reserve` / `release`: the only writers; bucket-ordered row locks, idempotency
  keys, audit rows, outbox events, actionable `InsufficientStock` errors.
- Reconciler (`bin/cron`; on the edge it runs inside the `operations` API container via
  `RUN_SWEEPS_IN_API`, no longer a separate `operations-cron` — ADR-0013 fix round 2), discrepancy
  list/resolve API, permission `operations.inventory.stock.reconcile` (dangerous).
- Item-level API kept compatible, plus exact `*_qty` fields, `/balances`, and `Idempotency-Key` on
  adjustments. SPA shows exact quantities and sends idempotency keys.

Differences from the original Phase 0 bullet list, and why:
- **No `uoms` table yet.** Items still carry free-text `uom`. The fixed registry lives in code,
  and per-org units and conversions are Phase 1 tables (they need item master UI to be useful).
- **`inventory_items` was not split into `items` + balances.** It *is* the item master. Splitting
  would have renamed the PO join key for no gain (ADR-0014 §10, revised).
- **Reservations were pulled forward into the kernel**, so over-reservation could be proven
  impossible under concurrency now. Their documents and screens are still Phase 3.

Exit criteria met: 50 parallel issues against 10 units → exactly 10; reconciler clean after every
concurrency scenario and after migrating old-shape data; cross-tenant negative tests on every new
route; the pre-existing operations suite green.

### Phase 1 — Item master, lots, rolls, locations

**Scope**

*Backend* (`app/inventory/`, one migration):
1. `uoms` (per org, seeded from the fixed registry) and `uom_conversions` (item-specific,
   rational `numerator/denominator`, e.g. box = 12 pcs). `inventory_items.uom` becomes an FK to
   `uoms`, backfilled from the free text with unknown units created per org, never dropped.
2. Item master: `item_categories`, `item_attributes` (typed key/value; textile preset GSM, width
   mm, shade, composition, count, construction, finish, grade, season, collection, all optional),
   `item_variants`, tracking flags (`track_lot`, `track_roll`, `track_expiry`, `track_quality`),
   HSN/tax fields, active/inactive, secondary UOM.
3. `lots` (internal + supplier lot no., dates, attributes, quality status, source document) and
   `rolls` (number, lot, primary qty + measured secondary qty, e.g. 82.5 m / 14.8 kg, width,
   GSM, shade, grade, `parent_roll_id`) + `roll_events`, `lot_genealogy`. FKs from the
   ledger/balances `lot_id`/`roll_id` to them.
4. Location CRUD: zones / racks / bins under a warehouse, codes unique per warehouse, barcode
   field; deleting a location with stock is refused.
5. Ledger-backed operations through `post_move`: opening stock per location/lot/roll with decimal
   quantities, bin-to-bin transfer (same-warehouse, immediate), roll split (one → n children,
   quantities must sum exactly, weight split proportionally with explicit rounding) and merge
   (same item/lot/shade only), status change (e.g. → `QUALITY_HOLD`) as a paired move.
6. Read APIs: stock by item/location/lot/roll with cursor pagination, ledger with filters
   (item, location, lot, roll, kind, date range), forward/backward trace for lots and rolls
   (recursive CTE), item-detail aggregates.
7. Permissions: `inventory.location.write`, `inventory.lot.write`, `inventory.roll.write`,
   `inventory.stock.transfer`, `inventory.stock.read`. Role templates updated, and the
   permission rollout to live tenants done per ADR-0007.

*Frontend* (`product-operations`, same design system):
`/inventory/items` (list, filters, create with category attributes), `/inventory/items/:id`
(Overview, Locations, Lots, Rolls, Ledger tabs), `/inventory/lots`, `/inventory/rolls` +
roll detail (split/merge history, parent/children), `/inventory/warehouses` with location tree,
`/inventory/stock`, `/inventory/ledger`. Decimal quantity inputs validated against the UOM
(no floats sent; strings only). Permission-aware actions; empty, loading and error states.

**Not in Phase 1:** GRN/purchase changes (Phase 2), inter-warehouse transfers with in-transit,
reservations UI, stocktake (Phase 3), QC (4), BOM/production (5), costing (8), scanning (9).

**Exit criteria**
- Migration up/down on seed data. Existing items keep their stock; the `uom` backfill loses no
  unit string; `alembic check` shows no drift.
- A roll split of 82.5 m into 50 + 32.5 is exact; the children's weights sum to the parent's
  exactly; trace from child → parent → lot → source works both ways.
- Every new endpoint has a negative-authz test and a cross-tenant 404 test. List endpoints use
  cursor pagination and are covered by an EXPLAIN-based index test at 100k items / 1M ledger rows.
- Concurrency: two simultaneous splits of the same roll → one succeeds; transfer vs issue on the
  same bin never goes negative; reconciler is clean after each test.
- Frontend: typecheck, lint, unit tests and build green; the item → roll → split → ledger journey
  covered by an E2E test against a real build.
- Docs: `ledger.md` extended, API contracts regenerated, runbook updated.

### Phase 2 — Receiving and purchase
Purchase requisition → PO (existing) → GRN with partial/short/over-receipt tolerance, lot + roll
creation on receipt, supplier lot, receipt into `QUALITY_HOLD` when the item tracks quality.
Replaces the current single-shot `receive_purchase_order`.

### Phase 3 — Movements and control
Transfers (DRAFT→REQUESTED→APPROVED→IN_TRANSIT→RECEIVED/CANCELLED) with in-transit bucket;
reservations (sales/production/manual) with expiry; adjustments via the approval service;
stocktake (snapshot, blind count, variance, approval, auto-adjustment).

### Phase 4 — Quality
QC templates (incoming / in-process / finished), inspections with PENDING/PASSED/FAILED/HOLD/
REWORK/PARTIAL_PASS, release/reject moving stock between buckets, rejection → return-to-vendor
or scrap.

### Phase 5 — Manufacturing
Versioned multi-level BOMs with scrap %, substitutes and effective dates; production orders
pinned to a BOM version; reservation → issue (BOM-based, lot/roll picking) → WIP → output →
finished-goods receipt with lot genealogy; wastage by type vs standard, with threshold alerts;
returns to stock; variance.

### Phase 6 — Job work
Job-work orders, issue to vendor (`AT_JOB_WORKER` bucket, delivery challan / e-way bill via
finance), partial receipts, QC, reconciliation (sent / received / rejected / shortage) that must
balance before close; overdue alerts.

### Phase 7 — MRP
BOM explosion over demand (sales + production + manual), net of available, reserved, open PO and
open production; shortage table; "create requisition from shortages". Runs as a worker job for
large orgs, results stored per run.

### Phase 8 — Costing, dashboard, reports
Weighted-average valuation, FIFO layers retained; manufacturing cost roll-up (material + labour +
machine + job work + overhead − scrap recovery); `inventory.valuation_posted` to finance.
Dashboard at `/inventory` from backend aggregates only. The 18 reports in the spec, each a
server-side paginated/cursor query; heavy ones (ageing, valuation, dead stock) via the worker
with a stored result and export.

### Phase 9 — Scan, mobile, import/export
Barcode/QR on items, lots, rolls, locations (Code128 + QR payload `og:<type>:<code>`), label
print. Mobile routes `/inventory/scan` → receive / pick / issue / transfer / count / inspect with
large touch targets (BarcodeDetector with a JS fallback). CSV/XLSX import (items, opening stock,
lots, rolls, locations, BOM lines) with preview, row errors, duplicate detection, all-or-nothing
commit; export on every list.

### Phase 10 — Hardening and release
Security audit (IDOR, mass assignment, import files, rate limits), load test against 100k SKUs /
5M ledger rows, EXPLAIN for every list/report query, E2E suite for the 16 journeys in the spec,
observability (slow queries, posting failures, reconciler drift alert), runbook + docs, seed
data (multi-warehouse textile org).

---

## 4. Permissions (operations catalogue additions)

| Code (`operations.` prefix) | Dangerous |
|---|---|
| `inventory.item.read/write`, `inventory.warehouse.read/write` (existing) | |
| `inventory.location.write`, `inventory.lot.write`, `inventory.roll.write` | |
| `inventory.stock.read`, `inventory.stock.transfer`, `inventory.stock.receive`, `inventory.stock.issue`, `inventory.stock.reserve` | |
| `inventory.stock.adjust`, `inventory.stocktake.approve`, `inventory.stock.negative_override` | ✔ |
| `inventory.data.import`, `inventory.data.export` | import ✔ |
| `manufacturing.bom.read/write/publish`, `manufacturing.production_order.read/write/approve/issue/complete` | |
| `quality.inspection.read/inspect/approve/reject` | |
| `jobwork.order.read/write/issue/receive/approve` | |
| `procurement.requisition.read/write/approve`, `procurement.receipt.write` | |

Role templates: OPERATIONS_ADMIN (catalogue `all()`), STORE_MANAGER, STOREKEEPER, PRODUCTION_
PLANNER, QC_INSPECTOR, BUYER — each with a negative-authz test per list endpoint.

## 5. Events

`operations.stock.received|issued|transferred|adjusted|reserved|released`,
`operations.stocktake.completed`, `operations.stock.low|stockout`, `operations.quality.hold|released|rejected`,
`operations.production.started|completed|material_shortage`, `operations.jobwork.issued|received|overdue`,
`operations.inventory.valuation_posted`, `operations.inventory.inconsistency_detected`.
Schemas in `libs/contracts`, envelope v1 (ADR-0000 §7).

## 6. Definition of done (per phase)

`make check` green · migrations up **and** down on a copy of seed data · contracts regenerated ·
`opsgrids-client` regenerated and the frontend builds · negative-authz + cross-tenant tests for
every new endpoint · reconciler zero drift · E2E journeys of the phase pass · docs updated.
