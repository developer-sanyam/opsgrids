# Order Fulfilment — assessment and production plan

Decisions: [ADR-0024](../adr/0024-fulfilment-boundaries.md), [ADR-0025](../adr/0025-order-and-task-state.md).
Each phase below is a set of slices (schema → API → UI → tests), shipped as its own PR, and none
counts as done until `make check`, migrations and the frontend build pass.

---

## 1. Assessment

### What exists and is reused

| Area | Where | Reuse |
|---|---|---|
| Tenancy / RLS | `org_id` on every table + RLS, transaction-local GUC (ADR-0002) | As-is |
| Auth / authz | JWKS verification, `product.module.resource.action:SCOPE` grammar, `services/operations/app/catalogue.py` | Extend the operations catalogue |
| Events | Transactional outbox + relay + idempotent consumers (`OutboxEvent`, `ProcessedEvent`) | Reused, unchanged pattern |
| Audit | `AuditLog` mixin written in the same transaction | Reused |
| Notifications | `services/notification` | Reused — customer messages on order/dispatch events |
| Files | `services/document` + object storage | Reused for photos, labels, receipts |
| Approvals | `services/approval` | Reused for refund/override approvals |
| Scheduler | `services/scheduler` | Reused for offer timeouts |
| Stock kernel | `app/inventory/ledger.py::post_move` / `reserve` / `release` (ADR-0014) | **Reused for every pick/pack/dispatch move.** This is the single most important reuse in the whole build — picking, packing and dispatch are just more callers of the same kernel that already proves reserve/issue safe under concurrency; nothing here re-implements a stock move. |
| Warehouses / Locations | `operations.warehouses`, `operations.locations` (`Warehouse`, `Location` in `services/operations/app/models.py`) | Extended with new `kind` values and geometry/capacity columns, not duplicated |
| Item master | `operations.inventory_items` (`InventoryItem`) | Gains a selling-price field — pending ADR-0024 decision A2 |
| Design system | `@opsgrids/ui`, `opsgrids-client` (generated) | Reused |
| CRM | `services/customer` (`Contact.phone_index`, `get_contact_by_phone_index`) | Reused for caller lookup by phone and for the order's customer reference |
| Finance | `services/finance` | Reused for invoicing/credit notes — operations only requests via event, never writes finance's tables |
| Payment | `services/payment` | Extended with a collections module — pending ADR-0024 decision A4 |

### Gaps

Order object, order lines, order state machine, pick/pack tasks, worker model, assignment/offers,
warehouse geometry (floors / layout versions / walk graph), route calculation, shipments/dispatch,
live-updates transport, worker app (no mobile/PWA surface exists today), selling price on items,
tenant payment collection, feature flags (none exist platform-wide — propose a per-org JSON
settings blob the same way `fulfilment_settings` is proposed below, not a new platform primitive).

### Risks

1. **The price gap blocks any real order total** until ADR-0024 decision A2 (selling price on
   `inventory_items`) is answered. Every phase past F2 needs a number to quote and to invoice.
2. **`operations` is not the platform's largest service today, but it is carrying the committed
   growth of two plans at once.** Counted 2026-09-28 over app code: `operations` 8,800 lines
   against `notification`'s 30,869 and `finance`'s 17,136 (ADR-0024 Consequences). ADR-0014
   predicted it would become the largest once manufacturing landed; it has not yet. What is real
   is that inventory's own Phases 2–10 and this build's four new module trees (`app/orders/`,
   `app/fulfilment/`, `app/warehouse/`, `app/dispatch/`, on top of `app/inventory/`) all write
   through one kernel and deploy as one unit. The module split and the dependency rules
   ADR-0024 §1 sets (`inventory` imports none of the four; `orders` never imports `fulfilment` or
   `dispatch`) stay required, not stylistic, and the single API container's memory is the first
   thing to watch as more sweeps fold into it.
3. **No real-time transport exists anywhere in the platform today.** Grepped `services/`, `libs/`
   and `frontend/` for `websocket`, `EventSource`, `socket.io` and `server-sent` (case-insensitive)
   and found no real matches — the only hits were `assert`/`processed_events` substrings tripping
   the `sse` pattern, not an actual SSE implementation. Live order and task updates (offer pushed
   to a worker, order status changed on a dispatcher's screen) need new infrastructure — a
   transport decision, not just new endpoints — before F3 can ship a usable worker experience.
4. **The worker app is new frontend surface with no existing template to fork.** Every product
   SPA under `frontend/` (`product-operations`, `product-crm`, …) is a desktop-oriented admin
   console; none is a PWA, none is built for a phone held with one hand in a warehouse aisle. F3
   starts that surface from nothing.
5. **Scope.** This is a much larger spec than the inventory plan's ~15 slices — six domain groups
   (order, fulfilment, packing, dispatch, warehouse geometry, plus the worker app itself) each
   with their own state machine. Phases exist so it ships incrementally, not as one PR.

---

## 2. Domain model (operations schema, new tables)

| Group | Tables |
|---|---|
| Order | `orders`, `order_lines`, `order_transitions`, `fulfilment_settings` |
| Fulfilment | `workers`, `worker_skills`, `worker_zone_access`, `worker_presence`, `worker_position_samples`, `pick_tasks`, `pick_task_lines`, `assignment_offers`, `pick_exceptions`, `scan_events` |
| Packing | `packages`, `package_lines`, `packaging_materials` |
| Dispatch | `shipments`, `shipment_transitions` |
| Warehouse geometry | `warehouse_floors`, `layout_versions`, `location_rules`, `walk_nodes`, `walk_edges` |

`Location` (existing, `services/operations/app/models.py`) gains new `kind` values beyond its
current `zone | rack | bin` — the full vocabulary per ADR-0024 §5 is `zone, aisle, rack, shelf,
bin, staging, pack_station, dock, receiving, returns, quarantine` — plus geometry/capacity columns
(floor reference, coordinates, max weight/volume), rather than a new table. The same tree
(`parent_id`, `warehouse_id`, per-warehouse unique `code`) carries the new kinds. The column itself
must widen: `kind` is `String(10)` today and `pack_station` is 12 characters, so this ships as its
own reversible migration (F1 §3) before the orders/fulfilment tables need it.

---

## 3. Phases

### F0 — Decisions and ADRs

**Status: written, pending sign-off.** ADR-0024 (fulfilment boundaries: A1 orders live in
`operations`, A2 selling price, A3 the worker PWA, A4 payment collections) and ADR-0025 (order and
task state, the two state machines and their guards) are both written, in the same "Proposed —
pending sign-off" state ADR-0014 used before Phase 0 shipped.

**Scope:** no code. Sign-off on the two ADRs above.

**Not in Phase 0:** any schema, any endpoint, any UI.

**Exit criteria:** the person who owns this platform signs off on ADR-0024 and ADR-0025.

### F1 — Locations and barcodes

**Scope**

*Backend* (`app/warehouse/`, one migration):
1. Location kind CRUD extension: `Location.kind` widens from `String(10)` to `String(20)` and its
   `kind IN (...)` check constraint is dropped and recreated with the ADR-0024 §5 vocabulary
   (`zone, aisle, rack, shelf, bin, staging, pack_station, dock, receiving, returns, quarantine`).
   Ships as its own migration, separate from every other F1–F5 migration; its downgrade refuses if
   any row already uses a new kind, the same refusal shape ADR-0014's migrations use rather than
   guessing. No production location uses anything but `bin` today (nothing writes `kind`), so this
   is the cheapest the change ever gets.
2. `barcode` column on `Location` (unique per org where set), `pick_sequence` (integer, orders a
   picker's walk through a zone), capacity columns (`max_weight_grams`, `max_volume_milli`,
   `storage_type`).
3. Label PDFs: one endpoint per location generating a printable barcode label (reuses
   `services/document` for storage), and a batch endpoint for a zone's labels.
4. Permission: `operations.warehouse.layout.publish` guards any change to published capacity or
   kind data on locations already in use (dangerous — see §4).

*Frontend* (`product-operations`):
`/operations/warehouses/:id/locations` gains barcode and pick-sequence fields on the existing
location tree UI; a "print labels" action per zone.

**Not in Phase F1:** floors, layout versions, walk graph, route calculation (all F5+ or later,
not in F0–F5's MVP scope beyond what dispatch needs); those stay `warehouse_floors`,
`layout_versions`, `walk_nodes`, `walk_edges` as domain-model rows only, not built until a phase
past F5 needs them.

**Exit criteria**
- Migration up/down on a copy of seed data; existing locations keep their `kind`, `code` and
  tree shape unchanged.
- A location barcode round-trips: printed label → scanned value → resolves to the same location
  id, exact string match.
- Every new endpoint has a negative-authz test and a cross-tenant 404 test.
- Frontend: typecheck, lint, unit tests and build green.

### F2 — Order engine

**Scope**

*Backend* (`app/orders/`, one migration):
1. `orders` (order number, org, customer reference via `services/customer` phone lookup or a
   manual name/phone pair, channel: `phone | manual | sales | walk_in`, order-level status —
   `DRAFT, PLACED, ACCEPTED, IN_FULFILMENT, ON_HOLD, READY, HANDED_OVER, DELIVERED, COMPLETED,
   CANCELLED, REJECTED` per ADR-0025 — plus `version` for optimistic locking, warehouse/location
   assignment, totals). Requires ADR-0024 A2 signed off first: the line price is
   `selling_price_minor`, and F2's migration does not start on a placeholder, because ADR-0024 §3
   rejects the one candidate that exists (`unit_value_minor` is cost, not price, and would make
   every margin figure zero). `order_lines` (item, quantity in the item's UOM,
   `selling_price_minor` **snapshotted at accept** so a later price change never reprices an
   accepted order, line total). `order_transitions` (append-only: `order_id, from_status,
   to_status, actor, reason, at` — joins `APPEND_ONLY_TABLES` in
   `services/operations/migrations/env.py`), driven by an `ORDER_TRANSITIONS` map in
   `app/orders/domain.py`, the same shape as `PO_TRANSITIONS` in
   `services/operations/app/domain.py`. `fulfilment_settings` (per-org, optionally per-warehouse:
   invoice timing, payment defaults per channel, offer timeout and round count default 3 — the
   platform's first per-org settings table, since none exists today; an order records the resolved
   settings it was accepted under).
2. Quote preview: given lines and a warehouse, return a priced total plus a per-line availability
   check against `InventoryItem.available_milli` — read-only, posts nothing.
3. Order lifecycle through the kernel, per ADR-0025's two guards:
   - `place` (`DRAFT → PLACED`): validates and prices every line. **Reserves nothing.** Reserving
     at `PLACED` is explicitly rejected in ADR-0025 §1 — it would promise stock for an order the
     business hasn't agreed to fill yet.
   - `accept` (`PLACED → ACCEPTED`): for a credit-terms order, the customer's credit position is
     read from `services/finance` **before** the transaction opens (no network call under a lock,
     per `io-outside-transactions.md`); over the limit refuses with the numbers. Then one
     transaction: `SELECT … FOR UPDATE` on the order row checking `status` and `version`, then
     **every line reserved in one all-or-nothing kernel call**, idempotency key
     `order:{id}:accept:v{version}` (per line: `…:{line_id}`). Any line short refuses the whole
     accept with `InsufficientStock` (ADR-0014 §11's shape) — no order is ever half-accepted.
     **Kernel prerequisite:** `app.inventory.ledger.reserve` today takes one bucket per call. This
     phase adds a multi-line entry point to `ledger.py` that locks every bucket in `post_move`'s
     deterministic order and checks all of them before writing any — kernel work under ADR-0014's
     rules (`app/inventory/`), not `app/orders/` code, and it lands before F2's `accept` endpoint
     can pass its own exit criteria.
   - `hold` / release, `cancel`: move the order's own status only (ADR-0025 §1, separate from any
     task/offer/invoice/payment status); `cancel` fans out — releases every open reservation,
     creates a putaway task for anything already picked, and (after commit) lets finance and
     payment issue their own credit note and refund from the cancellation event. Operations never
     writes finance's or payment's tables.
4. Permissions: `operations.orders.order.read`, `.create`, `.write`, `.accept`, `.cancel`.

*Frontend* (`product-operations`):
`/operations/orders` (list, filters by status/channel), `/operations/orders/new` with three entry
modes on one screen — phone lookup (calls `services/customer` phone index), manual entry, and a
sales-order path — `/operations/orders/:id` (lines, status, quote preview before placing).

**Not in Phase F2:** picking, packing, workers, dispatch, invoicing, payment collection (F3–F5).
ADR-0024 A2 must be signed off before this phase's migration — there is no interim placeholder
price, because the one candidate (`unit_value_minor`) is rejected in the ADR itself.

**Exit criteria**
- 50 parallel `accept` attempts against one order's worth of stock on a single SKU with only one
  order's quantity available → exactly one accept's reservations succeed and 49 see the actionable
  shortfall (`InsufficientStock`, same shape ADR-0014 §11 already defines).
- An order with a two-line accept where one line is short reserves neither line — proven by a test
  that checks both buckets' `reserved_milli` are unchanged after the refused accept.
- `cancel` on an order with partial reservations releases every open reservation it holds, proven
  by the reconciler staying clean afterward.
- Every `order_transitions` edge is exercised by a test; every non-edge (e.g. `CANCELLED` →
  `ACCEPTED`) answers 409, same convention as `can_transition_po`.
- Every new endpoint has a negative-authz test and a cross-tenant 404 test.
- Frontend: typecheck, lint, unit tests and build green; the phone-lookup → quote → place journey
  covered by an E2E test against a real build.

### F3 — Picking and packing

**Scope**

*Backend* (`app/fulfilment/`, one migration):
1. `workers` (directory reference, active/inactive, warehouse assignment), `worker_skills`,
   `worker_zone_access` (which zones a worker may be offered tasks in), `worker_presence`
   (online/offline, last-seen), `worker_position_samples` (coarse location pings, if the worker
   app requests permission — optional, off by default).
2. `pick_tasks` + `pick_task_lines` (derived from an accepted order's lines, sequenced by the
   location's `pick_sequence` from F1), `assignment_offers` (one row per offer made to a worker,
   with an expiry timestamp scheduled through `services/scheduler`), `pick_exceptions` (short
   pick, damaged, not found — with a reason code), `scan_events` (append-only, one row per
   barcode scan: location, item, task, worker, timestamp).
3. `packages` + `package_lines` (what went into which package) + `packaging_materials` (a small
   reference table: box sizes, weights).
4. Every pick and pack confirmation posts through `app.inventory.ledger.post_move` as a
   **relocation that transfers the reservation** to the destination (bin → staging on pick,
   staging → pack/dock on pack) — never an issue, and never an unreserving `post_move` followed by
   a separate `reserve` call. Consuming the reservation is dispatch's move, not this phase's
   (F5). **Kernel prerequisite:** ADR-0024 §6 gives `post_move` an optional reservation-transfer
   parameter — draws the quantity off the source reservation and opens/tops up one on the
   destination bucket with the same `reference_type`/`reference_id`, in the same call, under the
   same bucket-then-reservation lock order `post_move` already uses. Kernel work in
   `app/inventory/ledger.py`, landing before F3's pick-confirm endpoint can meet its own exit
   criteria — this is the second kernel prerequisite this build needs, alongside F2's multi-line
   reserve.
5. Task states follow ADR-0025 §2 (`CREATED, OFFERED, ESCALATED, ACCEPTED, IN_PROGRESS, EXCEPTION,
   PICKED, CLOSED, CANCELLED`) and `PICK_TASK_TRANSITIONS` in `app/fulfilment/domain.py`. Assignment
   in this phase is **manual only**: a dispatcher offers a task to a specific worker, which is the
   same `OFFERED` state and the same accept path auto-assignment will use later — no auto-assign
   algorithm yet (deferred, no phase number assigned in this MVP set). A task that exhausts its
   round count (default 3, `fulfilment_settings`) still reaches `ESCALATED` and sits in a manager
   queue, so escalation behaves the same on the day the engine arrives.
6. Permissions: `operations.fulfilment.task.{read,execute,assign,override}`,
   `operations.fulfilment.worker.{read,location_read}`, `operations.fulfilment.pack.{read,execute}`.
   `override` is dangerous (a supervisor forcing a task past its normal transition).

*Frontend*: the worker PWA (new app under `frontend/`, no existing template forked — Risk 4).
Screens: login, task list, scan, pick confirmation, pack confirmation. Installable, offline-
tolerant for the scan screen (queues scans locally and replays on reconnect), large touch targets.
Also, in `product-operations`: `/operations/fulfilment/tasks` (dispatcher's board — offer, track,
reassign), `/operations/fulfilment/workers`.

**Not in Phase F3:** auto-assignment, live map/position display beyond raw presence, route
calculation over the walk graph (F1's `walk_nodes`/`walk_edges` stay schema-only), dispatch and
shipments (F5), billing (F4).

**Exit criteria**
- An offer accepted after its own expiry event has already fired changes nothing — the accept is
  rejected because the offer is already in its terminal `EXPIRED` state, proven by a test that
  fires the scheduled expiry before sending the accept (both are conditional updates racing on one
  row, per ADR-0025 §3; whichever lands first wins and the loser gets a 409, not a 500).
- A pick confirmation transfers the reservation rather than freeing it: after the pick, the
  destination staging bucket's `reserved_milli` is up by the picked quantity and the source bin's
  is down by the same amount, proven under a concurrent second-order test that tries to reserve
  the freed-looking source stock and is refused.
- Two workers scanning the same task's confirmation concurrently → exactly one pick confirmation
  posts through the kernel; the second sees the task's already-updated state, not a duplicate
  ledger row.
- A short pick (exception) leaves the order's remaining lines pickable and the shorted line
  visibly flagged on the dispatcher board; it does not silently complete the task.
- Every new endpoint has a negative-authz test and a cross-tenant 404 test.
- Worker PWA: builds, lints, typechecks; the login → offer → accept → scan → pick → pack journey
  covered by an E2E test against a real build, including one offline-scan-then-reconnect case.

### F4 — Billing and counter payments

**Scope**

*Backend* (`app/orders/`, extends F2's module):
1. On order completion of picking/packing, emit `operations.orders.order.invoice_requested` and
   consume the resulting invoice reference from `services/finance` (request-only — operations
   never writes finance's tables, per the Assessment table).
2. Counter payment recording: `services/payment`'s new collections module (pending ADR-0024 A4)
   records a payment against an order at the point of handover; operations only calls it and
   stores the reference, it does not own payment state.
3. The billing gate (`READY → HANDED_OVER`, ADR-0025 §1's second guard): implemented once in
   `app/orders/services.py`, reading only facts already recorded on the order from finance's and
   payment's events — invoice exists if `fulfilment_settings`' invoice-timing policy requires one
   by now, paid if the payment terms require payment before dispatch, pickup code verified if the
   org requires one. It calls no other service on the handover path itself (the facts arrived
   earlier, by event), so a failed gate is a 409 naming which condition is missing rather than a
   timeout. This phase exercises it from the counter/walk-in handover; F5 exercises the same
   function from a carrier handover. The credit check is not here — it already ran at `accept` in
   F2 (ADR-0025 §1's first guard), before this phase's invoice or payment exists.

*Frontend*: `/operations/orders/:id` gains an invoice/payment tab; the worker PWA's pack/handover
screen gains a "collect payment" step for counter/walk-in orders.

**Not in Phase F4:** e-way bill generation (F5), refund workflows beyond what `services/approval`
already provides generically, any new finance ledger (finance owns its own).

**Exit criteria**
- A counter handover with a missing invoice, an unpaid prepaid order, or an unmatched pickup code
  is refused with a 409 naming the specific missing condition, proven by one test per condition.
- A duplicate `invoice_requested` event (redelivered by the outbox relay) does not create a second
  invoice request — proven via the existing `ProcessedEvent` idempotency ledger, not new code.
- Every new endpoint has a negative-authz test and a cross-tenant 404 test.
- Frontend: typecheck, lint, unit tests and build green.

### F5 — Dispatch and notifications — MVP release

**Scope**

*Backend* (`app/dispatch/`, one migration):
1. `shipments` + `shipment_transitions` (an `*_TRANSITIONS` map, same shape as `PO_TRANSITIONS`):
   pickup handover, in-transit, delivered, failed, returned. The carrier handover scan is where the
   order's `READY → HANDED_OVER` billing gate (F4) is called from for delivery/courier orders.
2. Dispatch posts through `app.inventory.ledger.post_move` as an **issue** against the reservation
   that pick and pack transferred (ADR-0024 §6) — the first point in the whole flow where the
   reservation is actually consumed, not merely relocated.
3. E-way bill request when required: an event to `services/finance` (which already holds e-way
   bill integration per ADR-0014 §8/GST work), never a second implementation in `operations`.
4. Customer messages on the new events via `services/notification`: order placed, ready,
   dispatched, delivered — consuming the `operations.orders.*` and `operations.dispatch.*` events
   below.
5. Permissions: `operations.dispatch.shipment.{read,write}`.

*Frontend*: `/operations/dispatch` (shipment list, handover confirmation), worker PWA gains a
"hand over to carrier" step if dispatch is done by the same worker.

**Not in Phase F5:** carrier API integrations beyond a manual tracking-reference field, route
optimisation, multi-leg shipments.

**Exit criteria — MVP release.** The phone / manual / sales / walk-in order journey passes end to
end with manual worker assignment: an order is placed (F2), reserved, picked and packed by a
worker who received a manual offer (F3), invoiced and paid at the counter where applicable (F4),
handed to a carrier or collected, and the customer is notified at each step (F5) — covered by one
E2E suite exercising all four channels, against a real build, with negative-authz and
cross-tenant tests on every endpoint introduced across F1–F5.

---

## 4. Permissions

| Code (`operations.` prefix) | Dangerous |
|---|---|
| `orders.order.read`, `.create`, `.write`, `.accept`, `.cancel` | |
| `fulfilment.task.read`, `.execute`, `.assign` | |
| `fulfilment.task.override` | ✔ |
| `fulfilment.worker.read`, `.location_read` | |
| `fulfilment.pack.read`, `.execute` | |
| `dispatch.shipment.read`, `.write` | |
| `warehouse.layout.publish` | ✔ |

Role templates stay built from the operations catalogue only (CLAUDE.md), each with a
negative-authz test per list endpoint, matching `services/operations/app/catalogue.py`'s existing
guard.

## 5. Events

`operations.orders.order.placed|accepted|held|cancelled|ready|handed_over|completed|invoice_requested`,
`operations.fulfilment.task.offered|accepted|declined|expired|escalated|started|completed`,
`operations.fulfilment.pick.confirmed|exception_raised`,
`operations.fulfilment.pack.started|completed`,
`operations.dispatch.shipment.created|dispatched|delivered|failed|returned`.
Schemas go in `libs/contracts`, envelope v1 per ADR-0000 §7.

## 6. Definition of done (per phase)

`make check` green · migrations up **and** down on a copy of seed data · contracts regenerated ·
`opsgrids-client` regenerated and the frontend builds · negative-authz + cross-tenant tests for
every new endpoint · reconciler zero drift · E2E journeys of the phase pass · docs updated.
