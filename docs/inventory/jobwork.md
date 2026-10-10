# Job work (Phase 6)

What Phase 6 of the [inventory plan](plan.md) built, how it behaves, and how to operate it.
Decisions behind the stock model are in [ADR-0014](../adr/0014-inventory-and-manufacturing.md); the
posting kernel it moves stock through is in [ledger.md](ledger.md). This page is the reference.

Job work is material sent to an outside processor (a dyer, a stitcher, a finisher) and expected
back. The company keeps title to the goods, so they stay on the books — in the `AT_JOB_WORKER`
bucket, reported separately from on-hand — and the paperwork (a delivery challan, sometimes an
e-way bill) travels with them.

---

## The flow

```
DRAFT ──approve──▶ APPROVED ──issue──▶ ISSUED ──receive──▶ PARTIALLY_RECEIVED ──receive──▶ RECEIVED ──close──▶ CLOSED
  │                    │                  │ └────────────────receive (all at once)────────────▲
  └──cancel──▶ CANCELLED ◀──cancel────────┘ (issued orders cannot be cancelled: the stock is at the vendor)
```

`app/jobwork/domain.py::JOB_WORK_TRANSITIONS` is the whole state machine; `can_transition` is the
only gate. **Overdue is not a status** — it is derived (`due_date` passed while the order is
`ISSUED` or `PARTIALLY_RECEIVED`).

| Step | Permission | What happens (one transaction) |
|---|---|---|
| Draft | `jobwork.order.write` | Order + materials. Quantities are exact decimal strings validated against the item's unit. No stock moves. |
| Approve | `jobwork.order.approve` | `DRAFT → APPROVED`. |
| Issue | `jobwork.order.issue` | Stock `AVAILABLE → AT_JOB_WORKER` at the same bin through `post_move`; a numbered, immutable delivery challan is cut; `operations.jobwork.issued` staged; an e-way request staged when the consignment is over the threshold. |
| Receive | `jobwork.order.receive` | Per material: `received` (good), `rejected`, `shortage`. Good → `AVAILABLE` (or `QUALITY_HOLD` if the order wants QC), rejected → `REJECTED`, shortage → written off the vendor bucket as a `lost` adjustment. `operations.jobwork.received` staged, plus the QC hand-off event when applicable. |
| Close | `jobwork.order.approve` | Refused unless the reconciliation balances (below). |

Approving and closing share a permission on purpose: the person who drafts cannot approve their
own order, and the person who counts goods back cannot declare the books square. `OPERATIONS_MANAGER`
holds all five; `OPERATIONS_ADMIN` gets them with the catalogue's `all()`; ordinary seats and the
worker role hold none.

## Tables (migration `d7b3e5a19f62`, `down_revision` is the MRP migration `e7a4c1d9b352`)

| Table | What it is |
|---|---|
| `job_work_orders` | The document and its state. `number` `JW-{yyyy}-{seq}`. `transition_keys` remembers which `Idempotency-Key` produced which status, so a retried approve/cancel/close replays. |
| `job_work_materials` | One row per bucket: planned/sent quantity and the running `received / rejected / shortage` figures. `CHECK (received + rejected + shortage <= sent)`. |
| `job_work_receipts` | One row per material per receipt posting. **Append-only** (`UPDATE`/`DELETE` revoked from the app role, re-applied after every grant refresh in `env.py`). |
| `challans` | The delivery challan. **Immutable once issued** — a trigger refuses any change to number, order, supplier, goods snapshot or value, and any delete. Only the e-way fields and the transport facts that feed them can change. |

All four carry `organization_id`, ENABLE + FORCE RLS and the `tenant_isolation` policy.
`supplier_id`, `item_id`, `lot_id`, `roll_id`, `location_id`, `source_warehouse_id` are **plain
indexed UUIDs** — no foreign key to anything outside these tables (ADR-0000 §10). The only FKs
are between the four tables themselves. A lot or roll a material names is checked against Phase 1's
`lots` / `rolls` when the order is drafted (`ledger.validate_tracking`: it must exist in the
caller's organization and belong to the item, else 404 / 422), and again by the kernel when stock
moves.

Reversible: the downgrade drops the trigger, the policies and the tables. Verified by
`tests/test_migration_jobwork.py` (real alembic, up → down with rows → up → `alembic check`).

## Quantities and the ledger

Same rules as everywhere (ADR-0014 §2): decimal strings on the wire, `BIGINT` milli-units in the
database, no float anywhere. Every stock effect goes through `app.inventory.ledger.post_move` and
carries `reference_type="job_work_order"`, `reference_id=<order id>`:

| Event | Move |
|---|---|
| Issue | one `relocation`: `-q AVAILABLE` / `+q AT_JOB_WORKER` per material, same bin. Kernel key `jw-issue:<order id>` — an order can be issued once, ever. |
| Receive (good + rejected) | one `relocation`: `-(good+rejected) AT_JOB_WORKER` at the issue bin, `+good` at the receive location in `AVAILABLE`/`QUALITY_HOLD`, `+rejected` in `REJECTED`. Lines landing in one bucket are summed first (a kernel move touches a bucket once). |
| Receive (shortage) | one `adjustment`, reason `lost`: `-shortage AT_JOB_WORKER`. |

Where returned goods land: the receipt line's `location_id`, else the order's `receive_location_id`,
else the bin the material was issued from.

## Reconciliation — must balance before close

For every material: **`sent = received + rejected + shortage + outstanding`**, where `outstanding`
is what is still at the vendor. An order *balances* when

1. every material's outstanding is zero (`documents_settled`), **and**
2. the ledger agrees (`ledger_consistent`): per item, the net of the `AT_JOB_WORKER` ledger rows
   posted under this order equals the documents' outstanding. This is a sum over the append-only
   ledger, not a read of a balance — balances are shared by every order sending the same item from
   the same bin.

`GET /operations/jobwork/orders/{id}/reconciliation` returns every figure plus any drift (item,
documents' number, ledger's number). `:close` recomputes it under the order and material locks and
answers 409 `jobwork-reconciliation-not-balanced` with the figures in `errors[0]` — the order's
status is only a summary of this, never the test. (The stock reconciler in `app/inventory` cannot
see this kind of drift: it compares the ledger with the balances, not with a document.)

## Delivery challan and the e-way bill

* Number `DC/{fy}/{seq:04d}` (e.g. `DC/26-27/0001`), a financial-year series (April–March) that
  stays within 16 characters. `app/jobwork/numbering.py` serialises each series per organization
  with a transaction-scoped advisory lock and reads the committed maximum, so numbers are
  race-safe and **gapless** (a request that rolls back never consumed one); a unique index is the
  backstop. Proven under 12 concurrent issues: numbers `1..12`, and a refused issue consumes none.
* Value = quantity × the item's current `unit_value_minor`, rounded half-to-even once per line;
  one currency per challan (a mixed-currency order is refused at issue).
* **The e-way bill is not built here.** `services/finance` generates e-way bills from an *invoice*
  (`GenerateEwayIn.invoice_id`); a challan is not one. This module follows the hook
  `operations.dispatch.shipment.eway_requested` set: it emits
  **`operations.jobwork.challan.eway_requested`** (fire-and-forget, schema in `libs/contracts`)
  when the consignment is over INR 50,000 — mirroring finance's threshold constant rather than
  importing it — and again whenever someone supplies transport facts
  (`POST /challans/{id}:request-eway`). `challans.eway_status` records `NOT_REQUIRED | REQUESTED |
  GENERATED | FAILED`; only finance may move it to the last two. **No finance consumer exists for
  this event yet** — see *Deferred*.

## QC hand-off

When an order has `qc_required`, returned goods are parked in `QUALITY_HOLD` and
**`operations.jobwork.receipt.qc_requested`** is emitted with the lines to inspect. This module
neither imports nor waits for quality. The quality package consumes the event
(`app/quality/handoff.py`, bound in `app/consumers.py::job_work_qc_requested`, the same worker
process as the other consumers) and opens one `incoming` inspection per line with good quantity:
`hold_stock: false` (the goods are already in `QUALITY_HOLD`), `reference_type: job_work_order`, the
vendor as supplier, lot and roll carried through. Releasing or rejecting happens through the
inspection (`operations.quality.inspection.approve` / `.reject`), not `stock.adjust`.

Idempotent: the event id is marked processed in the handler's transaction, and each inspection is
created under the key `jw-qc:<order>:<receipt>:<material>`, so a republished event replays the
first inspection. A line whose stock has already left `QUALITY_HOLD` (or whose item, lot or roll is
gone) is skipped and logged inside its own savepoint, never raised — redelivery would only refuse
again. A rejected-only line has nothing on hold and opens nothing. Tests:
`tests/test_quality_handoff.py`.

## Lock order with rolls

Issue is one `post_move`, which share-locks every roll (id order) before its first balance lock.
Receive posts a return move and then a shortage move; it validates and share-locks the rolls of
**both** up front, so the shortage move's roll is never waited for while the return move holds
balances (the reverse of the roll -> balance order a concurrent split or merge relies on). Proven
by `tests/test_jobwork_tracking.py`: the receipt parks on a held roll while the other roll's
vendor-bucket balance is still free.

## Overdue alert

`app/jobwork/sweep.py::JobWorkOverdueSweep` — the same discovery/work split as the reconcile,
alert and channel sweeps. Discovery runs as the BYPASSRLS `opsgrids_outbox` role, which may read
exactly the columns `migrations/env.py` grants it on `job_work_orders` (`organization_id, status,
due_date, overdue_notified_at, deleted_at`) — the F11/F12 incident was a sweep reading a column
that role was never granted, so `tests/test_jobwork_events_and_sweep.py` runs discovery as the real
role, and proves it can read nothing wider. Work runs per organization on the app role under a
tenant binding; one organization failing is counted and never stops the next.

Each overdue order is announced **once**: `overdue_notified_at` is set in the same transaction as
the `operations.jobwork.overdue` event (`FOR UPDATE SKIP LOCKED`). It runs every
`JOBWORK_OVERDUE_INTERVAL_SECONDS` (default 3600) — inside the API process on the edge
(`RUN_SWEEPS_IN_API`, visible on `/metrics` as `operations.jobwork.overdue`) and in `bin/cron`
everywhere else.

## Events (`libs/contracts/events/`)

| Event | When |
|---|---|
| `operations.jobwork.issued` | material sent, challan cut |
| `operations.jobwork.received` | a receipt (partial or final) |
| `operations.jobwork.overdue` | once per overdue order (notification) |
| `operations.jobwork.closed` | reconciled and closed |
| `operations.jobwork.challan.eway_requested` | finance asked to consider an e-way bill (no consumer yet) |
| `operations.jobwork.receipt.qc_requested` | QC hand-off — consumed by `app.consumers.job_work_qc_requested` (quality) |

All are staged through the outbox in the writing transaction and published after commit. Every
payload the code builds is validated against its committed schema in the test suite.

## API

All under `/operations/jobwork`, all `Entitled`, all permissions floored at ORG. `Idempotency-Key`
is **required** on create, issue and receive; honoured on approve, cancel and close. A retry with
the same key and body replays; the same key with a different body is a 409.

| Route | Permission |
|---|---|
| `GET /orders` (`status`, `supplier_id`, `overdue`, `search`) | `order.read` |
| `POST /orders` | `order.write` |
| `GET /orders/{id}` (materials, receipts, challans, reconciliation) | `order.read` |
| `PATCH /orders/{id}` (draft header only; `expected_version`) | `order.write` |
| `POST /orders/{id}:approve` | `order.approve` |
| `POST /orders/{id}:cancel` (before issue) | `order.write` |
| `POST /orders/{id}:issue` | `order.issue` |
| `POST /orders/{id}:receive` | `order.receive` |
| `GET /orders/{id}/reconciliation` | `order.read` |
| `POST /orders/{id}:close` | `order.approve` |
| `GET /challans/{id}` | `order.read` |
| `POST /challans/{id}:request-eway` | `order.issue` |

## Screens (`frontend/product-operations`)

`/jobwork/orders` (list: status pills, overdue filter, search, empty/loading/error states) →
`/jobwork/orders/:id` (materials, reconciliation, receipts, challans; issue, receive, close,
cancel, approve — each rendered only when both the order's status and the caller's grant allow it)
→ `/jobwork/challans/:id/print` (outside the app shell; `window.print()`). Quantities are the
decimal strings the server sent; `lib/jobwork.ts` compares and sums in integer milli-units and
validates a receipt against what is outstanding before the round trip (the server checks it again
under a lock). Every stock-moving write sends an `Idempotency-Key` that is reused when the same
request is retried.

## Rollout to live tenants (ADR-0007)

Adding permissions to the catalogue and to the `OPERATIONS_MANAGER` template is the whole code
change; delivery to existing organizations is the reconcile ADR-0007 describes (system roles are
widened, customer-authored roles are left alone). After deploying, run
`make edge-reconcile HOST=… CONFIRM=… PRODUCTS=operations` (dry run), read the diff, then again
with `APPLY=1`, so existing tenants' managers hold the five new permissions. The runbook
(`infrastructure/edge/RUNBOOK.md`, *Job work: the permission reconcile and the overdue sweep*)
has the exact commands and what to look for in the logs. Until it runs, every job-work endpoint answers
403 to every existing manager — which is the failure ADR-0007 exists to name.

Deploy order: migrate → `operations` API (the overdue sweep starts inside it) → Operations SPA.
The SPA must go after the API; the old SPA never calls the new routes.

## Verification

* `tests/test_jobwork_domain.py` — state machine, reconciliation identity, e-way threshold,
  overdue and financial-year arithmetic (pure).
* `tests/test_jobwork_flow.py` — the whole flow against the real ledger: exact partial-receipt
  maths (82.5 m = 30.125 + 2.375 good/rejected, then 45.5 + 4.5 short; the last milli-unit),
  over-receipt refusal with the numbers, QC hand-off, challan numbering and immutability below the
  application, idempotent replays, and **close refused unless the reconciliation balances** — both
  when material is outstanding and when the documents say settled but the ledger disagrees.
* `tests/test_jobwork_authz.py` — every endpoint: 401, no permission, every *other* job-work
  permission, a sub-ORG grant, missing entitlement; and another tenant's order/material/challan
  is a 404 that changes nothing. A coverage test fails if a route is added without a row.
* `tests/test_jobwork_concurrency.py` — real connections: one order issued by 8 racing requests
  is issued once; five orders of 30 m against 100 m send exactly 90 m; receipts racing for the
  last of a quantity cannot both take it; duplicate keys replay; challan numbers unique and
  gapless. Reconciler: zero drift after each. (With the order-row lock removed, 3 of these fail.)
* `tests/test_jobwork_events_and_sweep.py`, `tests/test_migration_jobwork.py`,
  `tests/test_main_jobwork_sweep_wiring.py`.
* Frontend: `lib/jobwork.test.ts`, `JobWorkOrders.test.tsx`, `JobWorkOrderDetail.test.tsx`,
  `JobWorkChallanPrint.test.tsx`.

## Deferred

* **A finance consumer for `operations.jobwork.challan.eway_requested`.** Finance's generator is
  invoice-keyed; a challan-keyed path (and writing the bill number back to `challans.eway_*`) is a
  finance-side slice.
* **Output different from input** (greige in, dyed out as a new item): a receipt returns the same
  item. The BOM/production phase models conversion.
* **Job charges and costing** (rate per unit, vendor invoice matching): Phase 8.
* **Editing materials on a draft** (cancel and re-draft today), **multiple issues per order**,
  **reversing a wrong receipt** (a new corrective move, not an edit).
* **Part-return challans** (a return challan kind is reserved in the schema, not issued).
* Mobile receive screen in the worker app.
