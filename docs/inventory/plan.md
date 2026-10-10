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

### Phase 1 — Item master, lots, rolls, locations — **backend DONE** (2026-10-04) · **frontend built** (2026-10-05) · reference: [ledger.md](ledger.md)

Frontend status (`frontend/product-operations`): `/inventory/stock`, `/inventory/ledger`, `/inventory/lots` (+ `:id` with genealogy and trace), `/inventory/rolls` (+ `:id` with parent/children and history), the Locations / Lots / Rolls / Ledger tabs on the item screen, the dialogs for opening stock, transfer, status change, roll split and merge, lot and roll creation, location delete, and the textile attribute preset on the item form. Quantities are decimal strings validated against the item's unit; every stock-moving write sends an `Idempotency-Key`; every list is cursor-paged. Not built: the `/inventory/warehouses` location-tree rewrite (the existing `/warehouses/:id/locations` tree gained delete), lot genealogy linking (`BLEND`/`REWORK`), editing a lot or roll after creation, multi-line opening stock.

Backend status: I0-I2 (partial release, UOMs, item master) shipped earlier; I3-I6 (lots/rolls/genealogy tables and FKs, opening/transfer/status/split/merge through `post_move`, cursor-paginated stock/ledger/lot/roll reads and traces, location delete, the four permissions and their ADR-0007 rollout) are in migrations `b7d41e92c05a` (tables, `NOT VALID` keys) and `c4a8e1f6d273` (key validation and concurrent read-path indexes). Not done: `uom_conversions` wiring (deferred to Phase 2 — see ledger.md).

**Follow-on (2026-10-05) — warehouse map, find-stock and storage put-away: built on branch `feat/warehouse-map-putaway`, not deployed.** A site map and a 2-D/3-D floor view fed by the *published* layout, a search that shows where an item, lot or roll sits, and a worker-app **Put away** screen: scan the destination bin's barcode, with the device's GPS fix kept as 30-day audit evidence (migration `d7a3c9e5b184`; [ledger.md](ledger.md#storage-put-away-warehouse-coordinates-and-the-map-reads), [ADR-0028](../adr/0028-putaway-gps-captures.md), ADR-0014 §12's amendment, the [design](../superpowers/specs/2026-10-05-warehouse-map-putaway-design.md)). Put-away is allowed only from `receiving` or `staging` bins; the order engine still reserves only at an item's home bin, so stock put away elsewhere is invisible to order accept until multi-location allocation exists (RUNBOOK; backlog).

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

### Phase 2 — Receiving and purchase — **DONE** (2026-10-05) · reference: [receiving.md](receiving.md)

Purchase requisition -> PO (existing) -> GRN with partial/short/over-receipt tolerance, lot + roll
creation on receipt, supplier lot, receipt into `QUALITY_HOLD` when the item tracks quality.

Built (migration `c5e91a7d3b28`, after `d7a3c9e5b184`, `services/operations/app/receiving/`, `frontend/product-operations`):
- Purchase requisitions with a state machine (DRAFT -> SUBMITTED -> APPROVED -> CONVERTED, REJECTED /
  CANCELLED), approval gated at ORG, conversion into a draft purchase order through the PO module's own writer.
- Goods receipts: one `receipt` move per receipt through `post_move`; per-item tolerance (integer basis
  points, floored limit); partial receipts add up across documents under the PO row lock; `close_short`;
  per-line receipt location; supplier lot number; lots and rolls (primary + measured secondary quantity)
  created on receipt; `QUALITY_HOLD` routing; `Idempotency-Key` replay; the PO moves along
  `PO_TRANSITIONS` (`approved -> partially_received -> received`).
- `uom_conversions` CRUD and exact-math application in receipts (box = 12 pcs), the deferred Phase 1 item.
- The single-shot `receive_purchase_order` is now a thin wrapper over the goods-receipt path;
  `receive_into_home` is removed. `POST /purchase-orders/{id}/receive` now also requires
  `procurement.receipt.write` and `inventory.stock.receive` at ORG (it posts stock), closing the gap
  where `purchase_order.write` alone could receive.
- Permissions `procurement.requisition.read|write|approve`, `procurement.receipt.read|write`,
  `inventory.stock.receive`; role `BUYER`; grants for `OPERATIONS_MANAGER`, `OPERATIONS_WORKER`,
  `OPERATIONS_USER`; events `operations.stock.received` and six `purchase_requisition.*`.
- Frontend: `/purchase-requisitions` (list, new, detail, approve, reject, convert), receive-goods against a
  PO, `/goods-receipts` (list, detail), unit conversions and tolerance on the item.

Not done / deferred: PO line quantities are still whole units (so fractional requisitions cannot be
converted and fabric cannot be ordered as 82.5 m); receiving without a PO; returns to vendor; releasing
held stock (Phase 4); the E2E browser journey.

### Phase 3 — Movements and control — **DONE** (2026-10-05) · reference: [movements.md](movements.md)
Transfers (DRAFT→REQUESTED→APPROVED→IN_TRANSIT→RECEIVED/CANCELLED, plus REJECTED) with the in-transit
bucket, partial receipt and shortage write-off; reservations (order/production order/manual) with a list
API and expiry sweep; adjustments with reason + evidence, held for the approval service above a threshold;
stocktake (snapshot, blind count, variance, approval, auto-adjustment).

Status: migration `c5a82f6d3b19`; backend and the Operations SPA screens (Transfers, Reservations,
Adjustments, approval thresholds) shipped; five permissions and their ADR-0007 rollout (RUNBOOK); fourteen
event contracts; approval service gains the two request types. **Stocktake was not rebuilt:** counting
(snapshot, blind count, variance, manager approval, posting through the kernel) is `app/counting`
(ADR-0026) and unchanged; what remains of the plan's line is routing a *large* count variance through the
approval service and auto-adjusting under a threshold — deferred, see movements.md.

Exit criteria met: 50 parallel reservations against 10 units reserve exactly 10; two concurrent dispatches
of one transfer have one winner; a dispatch racing an issue never goes negative; in-transit and physical
reported separately and reconciling; partial receipt and shortage; reservation expiry through the real
sweep; reconciler clean after every concurrency scenario; negative-authz and cross-tenant tests on every
route; migration up/down verified; the event payloads validated against their committed contracts.

### Phase 4 — Quality — **BUILT on `feat/inventory-q4-on-p9`** (2026-10-05, stacked on Phase 9; not yet merged or deployed) · reference: [quality.md](quality.md)
QC templates (incoming / in-process / finished), inspections with PENDING/PASSED/FAILED/HOLD/
REWORK/PARTIAL_PASS, release/reject moving stock between buckets, rejection → return-to-vendor
or scrap.

Shipped: tables `qc_templates`, `qc_template_params`, `inspections`, `inspection_results`
(migration `a3c5e8f1b290`); the `app/quality/` package; fifteen endpoints under
`/operations/quality`; permissions `operations.quality.inspection.read|inspect|approve|reject`
(approve and reject dangerous) and the `QC_INSPECTOR` role; events `operations.quality.hold`,
`.released`, `.rejected` and `.disposed`; screens `/quality/templates`, `/quality/inspections`
and `/quality/inspections/:id`. Deferred: see [quality.md §10](quality.md#10-deferred-and-risks).

### Phase 5 — Manufacturing — **DONE** (2026-10-05) · reference: [manufacturing.md](manufacturing.md)
Versioned multi-level BOMs with scrap %, substitutes and effective dates; production orders
pinned to a BOM version; reservation → issue (BOM-based, lot/roll picking) → WIP → output →
finished-goods receipt with lot genealogy; wastage by type vs standard, with threshold alerts;
returns to stock; variance.

Built (`services/operations/app/manufacturing/`, migration `d3b9f2a7c614`):
- 12 tables (`boms`, `bom_versions`, `bom_components`, `bom_substitutes`, `production_orders`,
  `production_materials`, `production_picks`, `production_outputs`, `production_wastage`,
  `production_genealogy`, `wastage_thresholds`, `manufacturing_requests`), RLS on every one; a
  published BOM version is immutable in the service **and** by trigger; cycles refused with the
  path; effective dating resolves the version in force without editing older rows.
- All stock movement through `post_move` / `reserve` / `release` (WIP is the existing `WIP`
  status bucket): reserve → issue (`AVAILABLE` → `WIP`) → output (consume WIP at standard, receive
  finished goods) → wastage by type / return / complete; exact integer arithmetic, scrap rounded
  up once, cumulative so split outputs never drift.
- Events `operations.production.started|completed|material_shortage` plus
  `wastage_threshold_exceeded`, audit rows, `Idempotency-Key` on every mutating endpoint.
- Permissions `manufacturing.bom.read/write/publish`, `manufacturing.production_order.read/write/
  approve/issue/complete` (publish and approve dangerous), role `PRODUCTION_PLANNER`, manager grants.
- Screens `/manufacturing/boms` (+ detail, version, publish) and `/manufacturing/orders` (+ detail
  with reserve / issue / output / wastage / return / complete).

Differences from the plan text, and why:
- **Genealogy lives in `production_genealogy`** (the production-side record, per output, with roll
  and quantity) **and**, now that Phase 1's `lot_genealogy` exists, lot-level `BLEND` / `DERIVED`
  edges are written for the output lot too (see manufacturing.md); the ids here stay plain UUIDs
  with no foreign key, checked in the service.
- **One live BOM per item**, with versions underneath — alternates are versions or substitutes, not
  parallel BOMs, so a component's BOM is unambiguous.
- **Production orders are single-level**; multi-level is a produced component with its own BOM and
  its own order (explosion shows the whole tree).

Not in Phase 5: costing and cost roll-up (8), MRP (7), routings/work centres/labour, co-products,
QC release of output (4), mobile scan issue/output (9). See manufacturing.md.

### Phase 6 — Job work — **BUILT** (2026-10-05) · reference: [jobwork.md](jobwork.md)
Job-work orders, issue to vendor (`AT_JOB_WORKER` bucket, delivery challan / e-way bill via
finance), partial receipts, QC, reconciliation (sent / received / rejected / shortage) that must
balance before close; overdue alerts.

Built: `app/jobwork/` (domain, numbering, selectors, services, API, overdue sweep), migration
`d7b3e5a19f62` (`job_work_orders`, `job_work_materials`, append-only `job_work_receipts`, and
`challans` frozen by a trigger once issued — no FK to items, lots, rolls, locations or suppliers),
gapless financial-year challan numbering (`DC/26-27/0001`), partial receipts with exact
milli-unit arithmetic, a reconciliation (`sent = received + rejected + shortage + outstanding`,
documents **and** ledger) that `:close` refuses to pass unless it balances, `AT_JOB_WORKER` moves
only through `post_move`, `Idempotency-Key` on every stock-moving or numbering write, six events with JSON contracts,
five permissions (`jobwork.order.read/write/issue/receive/approve`) with the ADR-0007 reconcile
documented in the runbook, and the Operations screens `/jobwork/orders`, the order detail and the
challan print view.

Differences from the bullet above, and why:
- **The e-way bill is requested, not built.** Finance generates from an invoice; a challan is not
  one. The hook is `operations.jobwork.challan.eway_requested` (the same shape as dispatch's), and
  a finance consumer is a follow-up.
- **QC is a hand-off, not a dependency.** Returned goods go to `QUALITY_HOLD` and
  `operations.jobwork.receipt.qc_requested` is emitted; the quality package consumes it and opens an
  incoming inspection (see jobwork.md).
- **Same item in, same item out.** Conversion to a different output item belongs to Phase 5's BOM
  and production orders; job charges and costing to Phase 8.

### Phase 7 — MRP — **DONE** (2026-10-05) · reference: [mrp.md](mrp.md)
BOM explosion over demand (sales + production + manual), net of available, reserved, open PO and
open production; shortage table; "create requisition from shortages". Runs as a worker job for
large orgs, results stored per run.

Built (`services/operations/app/mrp/`, migration `e7a4c1d9b352`):
- 4 tables (`mrp_runs`, `mrp_demands`, `mrp_shortages`, `mrp_shortage_lines`), RLS on every one,
  plain-UUID references (no cross-module foreign keys); results stored per run with a result hash.
- Pure planner (`domain.plan`): multi-level explosion on the BOM in force at the plan date (scrap,
  effective windows, substitutes only when chosen), netted level by level in low-level-code order
  against `AVAILABLE` stock less reservations, approved-PO remainder and open production output;
  exact integer milli-unit arithmetic through `manufacturing.domain.required_milli` (one rounding,
  always up), cycle-safe, deterministic; pegging back to the root demand.
- Small organizations compute inline (`201`); large ones (over `MRP_SYNC_MAX_DEMANDS` open lines,
  or `mode: worker`) are queued (`202`) and executed by `MrpRunSweep` — outbox-role discovery,
  app-role work under the tenant policy, `SKIP LOCKED`, per-run savepoints — in `bin/cron` and in
  the API process on `RUN_SWEEPS_IN_API`.
- "Request requisition": `operations.mrp.requisition_requested` plus plain-UUID linkage
  (`requisition_request_id`, `requisition_id`) on each shortage; **no dependency on Phase 2's
  purchase requisitions**, which consume the event when they land.
- Permissions `manufacturing.mrp.read/run/requisition` (ORG-only, none dangerous) for
  `OPERATIONS_MANAGER` and `PRODUCTION_PLANNER`; events `operations.mrp.run_completed` and
  `operations.mrp.requisition_requested`; `Idempotency-Key` on both mutating endpoints; audit rows.
- Screens `/manufacturing/mrp` (runs, new run) and `/manufacturing/mrp/:runId` (shortage table,
  pegging, demand, request requisition).

Not in Phase 7: lead-time offsetting and time-phased buckets, planned-order generation, safety
stock / lot sizing, per-warehouse netting, real requisitions (Phase 2). See mrp.md.

### Phase 8 — Costing, dashboard, reports
Weighted-average valuation, FIFO layers retained; manufacturing cost roll-up (material + labour +
machine + job work + overhead − scrap recovery); `inventory.valuation_posted` to finance.
Dashboard at `/inventory` from backend aggregates only. The 18 reports in the spec, each a
server-side paginated/cursor query; heavy ones (ageing, valuation, dead stock) via the worker
with a stored result and export.

### Phase 9 — Scan, mobile, import/export — **DONE** (2026-10-05) · reference: [scan-import.md](scan-import.md), [ADR-0027](../adr/0027-inventory-scan-and-bulk-data.md)

Built: `barcode` on items, lots and rolls (migration `d9a4c6e1b3f7`; locations already had one); the `og:<type>:<code>` payload and `GET /operations/inventory/scan/{payload}`; label print views for all four types (QR + Code 128); `/inventory/scan` with camera (BarcodeDetector, jsqr fallback), manual entry, move and hold/release through the stock endpoints, and links into receive / pick / count / inspect that appear only when the route exists; all-or-nothing server import for items, locations, lots, rolls and opening stock (preview, row errors, duplicate detection, `Idempotency-Key`, append-only `inventory_import_runs`); exports for six lists; `operations.inventory.data.import` (dangerous) and `data.export`. **Not built:** BOM-line import (Phase 5 tables are not on this branch), update-from-sheet, a barcode field in the item form. The ADR amends ADR-0022 for inventory data: the plan's all-or-nothing import could not be per-row browser work.
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
| `inventory.transfer.write`, `inventory.transfer.dispatch`, `inventory.transfer.receive` (Phase 3) | |
| `inventory.transfer.approve`, `inventory.control.configure` (Phase 3) | ✔ |
| `inventory.stock.adjust`, `inventory.stocktake.approve`, `inventory.stock.negative_override` | ✔ |
| `inventory.putaway.location_read` (a worker's GPS fix at put-away; ADMIN only; the route also needs a one-time-code session; every reveal audited, ADR-0028) | ✔ |
| `inventory.data.import`, `inventory.data.export` | import ✔ |
| `manufacturing.bom.read/write/publish`, `manufacturing.production_order.read/write/approve/issue/complete` | |
| `quality.inspection.read/inspect/approve/reject` | |
| `jobwork.order.read/write/issue/receive/approve` | |
| `procurement.requisition.read/write/approve`, `procurement.receipt.write` | |

Role templates: OPERATIONS_ADMIN (catalogue `all()`), STORE_MANAGER, STOREKEEPER, PRODUCTION_
PLANNER, QC_INSPECTOR, BUYER — each with a negative-authz test per list endpoint.

## 5. Events

(Names as built live under `operations.inventory.*` — `…transfer.requested|approved|rejected|cancelled|
dispatched|received|applied`, `…adjustment.requested|applied|cancelled`, `…reservation.expired`,
`…stock.reserved|released|adjusted|transferred|posted` — see [movements.md](movements.md).)
`operations.stock.received|issued|transferred|adjusted|reserved|released`,
`operations.stocktake.completed`, `operations.stock.low|stockout`, `operations.quality.hold|released|rejected`,
`operations.production.started|completed|material_shortage`, `operations.jobwork.issued|received|overdue`,
`operations.inventory.valuation_posted`, `operations.inventory.inconsistency_detected`.
Schemas in `libs/contracts`, envelope v1 (ADR-0000 §7).

## 6. Definition of done (per phase)

`make check` green · migrations up **and** down on a copy of seed data · contracts regenerated ·
`opsgrids-client` regenerated and the frontend builds · negative-authz + cross-tenant tests for
every new endpoint · reconciler zero drift · E2E journeys of the phase pass · docs updated.
