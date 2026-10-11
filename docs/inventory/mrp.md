# MRP — material requirements planning (Phase 7)

What Phase 7 of the [inventory plan](plan.md) built, how it behaves, and how to operate it.
Decisions are in [ADR-0014](../adr/0014-inventory-and-manufacturing.md); the BOMs it explodes and the
production orders it reads are in [manufacturing.md](manufacturing.md); the stock kernel is in
[ledger.md](ledger.md). This page is the reference.

Code: `services/operations/app/mrp/` (`models`, `domain`, `schemas`, `selectors`, `services`, `api`,
`sweep` — the same anatomy as every package, enforced by an import-linter contract). Screens:
`frontend/product-operations` under `/manufacturing/mrp`.

---

## The one rule

**MRP never writes stock, and never creates an order.** It reads balances, reservations, sales
orders, purchase orders and production orders, and writes only its own four tables. What it hands
back is a *shortage table*; acting on it (a purchase requisition, a production order) is a person's
decision. The only outward effects are an audit row, `operations.mrp.run_completed`, and — when
asked — `operations.mrp.requisition_requested`.

## What a run does

1. **Collect demand** (`mrp_demands`, frozen so a result can always be traced to what it planned
   from):
   - **Sales orders** in `PLACED, ACCEPTED, IN_FULFILMENT, ON_HOLD` (`DRAFT` is not a commitment;
     `READY` and later have their goods staged), one row per (order, item).
   - **Open production orders** (`DRAFT, APPROVED, IN_PROGRESS`): each material's `required −
     issued`.
   - **Manual demand**: items, quantities and optional needed-by dates entered with the run.
   For the first two, the part the order **already reserved from stock** is subtracted
   (`requested`, `reserved`, `quantity = requested − reserved`), because that reservation is also
   out of the supply side — counting it on both sides would double it.
2. **Load the BOMs in force** on the *plan date* for every demanded item and everything beneath it,
   a frontier at a time (three queries per BOM depth, not per item): the published version in force
   (`resolve_version`, the same rule production orders use), component windows applied. An item with
   no live BOM, or none in force on that date, is a purchased item. **Substitutes are ignored
   unless chosen** on the run (`substitutes: [{component_item_id, substitute_item_id}]`, each of
   which must be declared by some BOM line; the line's ratio then scales the need).
3. **Net, level by level** (`domain.plan`, pure):
   - Items are processed in low-level-code order (Kahn's algorithm, ties by item id): an item is
     netted only once every produced parent that could ask for it has been, so one shared supply
     pool is used exactly once, against all of an item's demand at once.
   - `supply = max(0, on_hand − reserved) + open PO + open production output`, where
     - *on hand / reserved* are the `AVAILABLE` buckets and their open reservations (quarantined,
       held, WIP, in-transit and job-work stock is not usable);
     - *open PO* is `quantity − received_qty` on **approved** purchase orders, matched to items by
       SKU (a draft PO is not a promise; received and closed POs are done);
     - *open production output* is `planned − produced` of open production orders.
   - `shortage = max(0, gross − supply)`. With a shortage, a **produced** item is a `MAKE` row and
     its **net** (not gross) quantity explodes into its components — stock of a sub-assembly is
     used before more is planned; a purchased item is a `BUY` row.
4. **Store** `mrp_shortages` (one row per short item, with `gross`, `on_hand`, `reserved`,
   `open_po`, `open_production`, `shortage`, `level`, `need_by`) and `mrp_shortage_lines`
   (pegging), a result hash, counts; emit the event.

### Arithmetic

Quantities are integer milli-units. Each BOM line's need is computed by
`manufacturing.domain.required_milli` — the **same function production orders use**, so MRP and an
order can never disagree about what a BOM line needs: one integer division, rounded **up**, scrap
allowance included, a discrete unit rounded to whole units after. Each level's need is rounded once
before it is carried down. Worked example (the tests pin it): 10 shirts at 1.2 m of panel each, a
panel being 1.234 m of fabric at 2.5 % scrap per metre; 2 m of panel in stock → 12 m of panel
required, **10 m** made, which needs `ceil(1.234 × 10 × 1.025)` = **12.649 m** of fabric.

### Pegging

Supply covers the **earliest** demand first (due date, then source, then reference); the
uncovered tail is what a produced item's explosion is attributed to, split across the tail's pegs
with an exact largest-remainder allocation (`allocate_proportional`: integers, sums to the total,
deterministic). So every component shortage traces to the root demand that caused it, through the
parent that exploded into it, and a shortage's `need_by` is the earliest due date among the demand
that is actually left uncovered. `mrp_shortage_lines` sum to the shortage's `gross`.

### Cycle safety, determinism

A BOM cycle cannot loop the planner: an item left with an unprocessed parent after the queue drains
is on a cycle, reported with its SKU path (`409`, or `FAILED` with the same sentence for a worker
run). Publishing already refuses cycles; this is the net for data that predates the guard. Every
ordering is explicit (item id, due date, source reference), no clock or randomness is read, and the
result carries a SHA-256 of the canonical shortage list: the same data gives the same hash, however
the demand arrives (property-tested over shuffled input, and across repeated runs).

## Small organizations inline, large ones in the worker

`POST /runs` decides from the count of open demand lines (open sales-order lines + open production
materials, restricted to the chosen sources, + manual lines) against
`Settings.mrp_sync_max_demands` (default 200), or from `mode`: `auto` (default), `sync`, `worker`.

- **Inline** (`201`): the run is computed in the request's transaction; a failure (a cyclic BOM) is
  a `409` and **nothing is stored**.
- **Queued** (`202`, `status: QUEUED`): only the run row (with its parameters) is stored. The
  `MrpRunSweep` (`app/mrp/sweep.py`) executes it:
  - **Discovery** as the BYPASSRLS outbox role: `SELECT DISTINCT organization_id FROM mrp_runs WHERE
    status = 'QUEUED'`. The role holds `SELECT (organization_id, status)` on `mrp_runs` and nothing
    else (`migrations/env.py`, `RECONCILE_DISCOVERY_TABLES` — the incident of 2026-10-01 is why that
    list matters; a test proves the role cannot read `params`).
  - **Work** as the app role in `session_scope(organization_id)`, oldest run first,
    `FOR UPDATE SKIP LOCKED`: two workers never take the same run (tested with two concurrent
    ticks → one run, one event). Each run executes in its own savepoint; a failing run becomes
    `FAILED` with its reason and the next still runs. There is no persisted "running" state: a
    crash rolls the whole transaction back and the run is simply still `QUEUED`.
  - Runs where `bin/cron` runs (own container, locally) and **inside the `operations` API process**
    when `RUN_SWEEPS_IN_API` is on (the edge), on its own bulkhead engine, like the other sweeps;
    visible on `/metrics` as `operations.mrp`. Interval: `MRP_SWEEP_INTERVAL_SECONDS` (30).
  - The screen polls the run every 3 s while it is `QUEUED`.

A queued run and an inline run over the same data store byte-identical results (same hash).

## API (`/api/v1/operations/manufacturing/mrp`)

| Method and path | Permission (`operations.manufacturing.mrp.`) |
|---|---|
| `GET /demand-sources` — open counts, the sync limit, where `auto` would run | `read` |
| `GET /runs` — newest first; `status`, `q` | `read` |
| `POST /runs` — start a run; `Idempotency-Key` | `run` |
| `GET /runs/{id}` | `read` |
| `GET /runs/{id}/demands` — `source_type` | `read` |
| `GET /runs/{id}/shortages` — `action`, `requested`, `q` | `read` |
| `GET /runs/{id}/shortages/{shortage_id}` — with pegging | `read` |
| `POST /runs/{id}/requisitions` — `Idempotency-Key` | `requisition` |

All ORG-only (a plan is a shared operational document; every route floors at ORG), none
dangerous: a run reads stock and writes its own tables, and a requisition *request* commits nobody
to buy. Every read goes through `selectors` that call `ctx.apply()` and filter `organization_id`;
a run or shortage of another organization is a **404**, never a 403. Every endpoint has a
negative-authz row and a cross-tenant 404 row in `tests/test_mrp_authz.py`, and a test fails if the
router gains a route the table does not cover.

Roles: `OPERATIONS_ADMIN` (everything, by construction), `OPERATIONS_MANAGER` and
`PRODUCTION_PLANNER` (all three). `OPERATIONS_USER` and `OPERATIONS_WORKER` gain nothing.

**Idempotency.** `POST /runs` and `POST /runs/{id}/requisitions` take `Idempotency-Key`, claimed
first in `manufacturing_requests` (`ON CONFLICT DO NOTHING`, scope `mrp.run.create` /
`mrp.requisition:<run>`): a replay returns the first result (`200`, `replayed: true`) and does no
work or event twice; the same key with a different body is a `409`.

## "Create requisition from shortages"

Purchase requisitions (inventory Phase 2) are built on another branch, so **nothing here depends on
them**:

- `POST /runs/{id}/requisitions` takes `{shortage_ids?, note?}`. Without ids: every **`BUY`**
  shortage of the run not yet requested; with ids: exactly those (`MAKE` rows are `422` — a produced
  item is made with a production order; an unknown id `404`; an already-requested one `409`; a run
  that is not `COMPLETED` `409`). Shortage rows are locked in a fixed order, so concurrent requests
  queue instead of both seeing a shortage as un-requested.
- It generates a `request_id`, stamps it (and the time) on each shortage as plain UUID columns
  (`requisition_request_id`, `requisition_requested_at`), and stages
  `operations.mrp.requisition_requested` carrying the lines (`shortage_id`, `item_id`, `sku`,
  `uom`, `quantity`, `need_by`) in the same transaction.
- `app.consumers.mrp_requisition_requested` consumes the event: one **DRAFT** purchase requisition
  per `request_id` (its reason carries `MRP request <id>`, which makes redelivery a no-op), created
  through `receiving/requisitions.py` as the planner who asked, and its id is written to
  `mrp_shortages.requisition_id` (a plain UUID, never an FK). Purchasing submits, approves and
  converts it as usual.
- A shortage is not requested again while an earlier run's request for the same item is still on its
  way (no requisition yet, or one not cancelled, rejected or converted): a request without ids skips
  such items, one naming them is a `409`.

## Tables

All in the `operations` schema, one migration (`e7a4c1d9b352`, down-revision `d3b9f2a7c614`),
`organization_id` + `ENABLE`/`FORCE` RLS + the `tenant_isolation` policy on every one.

| Table | What it is |
|---|---|
| `mrp_runs` | The run: `number` (`MRP-00042`, per-org sequence), `status` (`QUEUED/COMPLETED/FAILED`), `execution` (`SYNC/WORKER`), `plan_date`, `params` (sources, manual demand, chosen substitutes), counts, `result_hash`, `error` |
| `mrp_demands` | The demand planned from: source, reference, item, requested / reserved / planned quantity, due date |
| `mrp_shortages` | One row per short item (`BUY`/`MAKE`), the whole arithmetic, `need_by`, the requisition linkage |
| `mrp_shortage_lines` | Pegging: source, reference, producing parent, quantity |

`item_id`, source ids and the requisition columns are **plain UUIDs with no foreign key**; the only
foreign keys are between MRP's own tables (`ON DELETE CASCADE` from the run). CHECK constraints hold
the arithmetic below the service (`quantity = requested − reserved`, `shortage > 0`, known
statuses/sources/actions). Idempotency claims reuse `manufacturing_requests`.

## Events

| Event | When |
|---|---|
| `operations.mrp.run_completed` | a run finished, inline or in the worker: counts, buy/make split, result hash |
| `operations.mrp.requisition_requested` | shortages were requested: `request_id`, run, note, lines |

Schemas in `libs/contracts/events/`, validated against real payloads in the tests. Every write also
stages audit rows (`mrp_run.created`, `.completed`, `.failed`, `.requisition_requested`).

## Screens

`/manufacturing/mrp` (runs list; filters; **New run** with demand sources, manual lines, plan date,
force-background switch, and a sentence saying where it will run) and `/manufacturing/mrp/:runId`
(facts; queued → polled; failed → the reason; completed → shortage table with the supply columns,
a pegging drawer per row, a demand tab, and **Request requisition (N)** listing exactly what it will
send). Permission-aware (`mrp.read` shows the nav entry and the screens, `mrp.run` the New run
button, `mrp.requisition` the request button), every state covered (loading, empty, error with the
server's sentence, queued, failed), quantities decimal strings end to end.

## Migration `e7a4c1d9b352`

Up: four tables, their RLS and indexes, the queued-run partial index. Down: drops all four —
**destructive by design** (the tables are new in this revision). Verified by
`tests/test_migration_mrp.py` (down → nothing left and the previous revision intact, up →
everything present, RLS forced and policied on every table, no foreign key out of MRP, the outbox
discovery grant and its limits, tenant isolation and WITH CHECK as the app role, CHECKs below the
service, down over seeded data, and `alembic check`).

## Deferred, and why

- **Lead times and time-phased buckets.** Needed-by dates are carried and the earliest uncovered one
  is reported, but there is no offsetting by supplier or production lead time and no weekly bucketing:
  the run is a single net-requirements pass. Supplier lead times exist (`suppliers.lead_time_days`)
  and the PO line has no item FK, so wiring them is a separate slice.
- **Planned orders.** MRP stops at the shortage table; it does not create production orders
  (a person plans one from the `MAKE` rows) or POs. Safety stock, reorder points, lot sizing and
  minimum order quantities are not modelled.
- **Real requisitions.** Waiting for inventory Phase 2; see above.
- **Alternate-warehouse netting.** Supply is organization-wide `AVAILABLE` stock; a warehouse filter
  on a run is not built.
- **Partial PO receipts by line item id.** PO lines are matched to items by SKU (that is how the
  procurement tables are keyed today); a line whose SKU matches no item contributes nothing.
- **Re-pegging for substitutes at order level** — a chosen substitute is global per component on a
  run, not per order.
