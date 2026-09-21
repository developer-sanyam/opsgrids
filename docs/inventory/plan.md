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

### Phase 0 — Foundations (blocks everything)
- Module split of `operations` (no behaviour change; the existing tests must stay green).
- `uoms`, scaled-integer quantity type, domain conversion functions + property tests.
- `items` / `inventory_balances` / `inventory_ledger` migration from `inventory_items` and
  `stock_movements`, compatibility routes kept.
- `locations` tree with a default bin per warehouse.
- The stock posting kernel: `post_move(ctx, lines, source, idempotency_key)` — the only writer of
  ledger + balances. Lock order, negative check, idempotency, audit, outbox.
- **Exit:** concurrency test (50 parallel issues against 10 units → exactly 10 succeed);
  reconciler reports zero drift on seed data; cross-tenant negative tests.

### Phase 1 — Item master, lots, rolls, locations
APIs + screens: `/inventory/items`, `/items/:id` (overview, locations, lots, rolls, ledger tabs),
`/lots`, `/rolls` (+ detail with split/merge history), `/warehouses`, `/locations`, `/stock`,
`/ledger`. Roll split/merge, bin transfer. Attribute templates per category (textile preset:
GSM, width, shade, composition…; nothing textile-only in core columns).

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
