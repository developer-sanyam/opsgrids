# Manufacturing (Phase 5)

What Phase 5 of the [inventory plan](plan.md) built, how it behaves, and how to operate it.
Decisions and their reasons are in [ADR-0014](../adr/0014-inventory-and-manufacturing.md); the stock
kernel it rides on is documented in [ledger.md](ledger.md). This page is the reference.

Code: `services/operations/app/manufacturing/` (`models`, `domain`, `schemas`, `selectors`,
`common`, `bom_services`, `services`, `api` — the same anatomy as every service, enforced by an
import-linter contract). Screens: `frontend/product-operations` under `/manufacturing/...`.

---

## The one rule

**Manufacturing never writes stock.** Every quantity that physically moves is a
`ledger.post_move`, `ledger.reserve` or `ledger.release` call in the request's transaction, so
over-issue is impossible under concurrency by the same row locks every other stock write uses, and
the reconciler stays at zero drift. There is no manufacturing balance table. WIP is the existing
`WIP` status bucket.

| Step | Kernel call | Buckets |
|---|---|---|
| Reserve | `reserve` | `AVAILABLE` bucket gains a reservation (`reference_type=production_order`); nothing moves |
| Issue to WIP | `post_move(kind=relocation, reason=production_issue)` | `AVAILABLE` −q (draws the reservation) → `WIP` +q, same lot/roll |
| Output | `post_move(kind=issue, reason=production_consume)` then `post_move(kind=receipt, reason=production_output)` | `WIP` −consumed; finished item `AVAILABLE` (or `QUALITY_HOLD`) +good at the output location |
| Wastage | `post_move(kind=issue, reason=production_waste)` | `WIP` −lost, by type |
| Return | `post_move(kind=relocation, reason=production_return)` | `WIP` −q → `AVAILABLE` +q at a chosen location |
| Cancel / complete | `release` | open reservations given back |

Both output moves are posted in the same transaction (the consume and the receipt succeed or fail
together).

## Tables

All in the `operations` schema, one migration (`d3b9f2a7c614`, down-revision `a3c5e8f1b290`, Phase 4's),
`organization_id` + `ENABLE`/`FORCE` RLS + the `tenant_isolation` policy on every one.

| Table | What it is |
|---|---|
| `boms` | One per produced item (unique per org among live rows), so "the BOM of this item" is unambiguous for multi-level lookup |
| `bom_versions` | `DRAFT → PUBLISHED → RETIRED`; batch size (`output_quantity_milli`), effective window, content hash stamped at publish. At most one draft per BOM |
| `bom_components` | Line: component item, quantity per batch, scrap allowance (basis points), optional effective window |
| `bom_substitutes` | Alternative item for a line with a conversion ratio and priority |
| `production_orders` | The state machine; pins `bom_version_id`; per-org sequence → `MO-00042` |
| `production_materials` | One row per BOM line the order needs: the BOM figures **copied** in, plus required / reserved / issued / consumed / returned / wasted |
| `production_picks` | One row per stock bucket (location, lot, roll) a material was reserved from / issued out of — what makes WIP exact per lot |
| `production_outputs`, `production_wastage`, `production_genealogy` | Append-only facts (UPDATE/DELETE revoked from the app role; re-applied in `migrations/env.py` after every grant re-application) |
| `wastage_thresholds` | Acceptable wastage by produced item and/or type |
| `manufacturing_requests` | `Idempotency-Key` claims |

`item_id`, `location_id`, `lot_id`, `roll_id` columns are **plain UUIDs with no foreign key**
(an item or location is checked in the service, filtered by organization, so a foreign id is a
404; a **lot or roll** a request names is checked against Phase 1's `lots`/`rolls` — existing in
the caller's organization and belonging to the item, 404 / 422 — by `ledger.validate_tracking`, the
check a posted move makes, run up front for `reserve`, `output` and `complete`, and by the move
itself for `issue`, `return` and `wastage`). The only foreign keys
are between manufacturing's own tables. A migration test asserts it.

## Versioned BOMs

- A version is mutable **only while `DRAFT`**. Publishing freezes it: the service answers 409 for
  every edit, and **triggers** on `bom_versions`, `bom_components` and `bom_substitutes` refuse the
  same writes below the service (a SQL console, an import script, the app role *and* the bypassing
  role). The only status change allowed afterwards is `PUBLISHED → RETIRED`.
- Publish stamps `published_at/by` and a SHA-256 `content_hash` of the canonical recipe, so it can
  be shown later that what an order was planned from is still what is stored.
- **Effective dating.** The version in force on a date is the published one with the latest
  `effective_from` not after it and whose `effective_to` has not passed (ties: higher number). A
  newer version therefore supersedes an older one from its own start date **without editing the
  older row**. A component's own window narrows which lines apply on the order's resolution date
  (`planned_start`, else today).
- **Multi-level.** A component that is itself a produced item (has a live BOM) is what makes a BOM
  multi-level. `GET …/explosion?quantity=` returns the full tree and the purchased-item totals,
  each level's need rounded up once before it is carried down. Production orders are
  single-level: a sub-assembly is its own order, whose output becomes stock the parent order
  reserves (genealogy chains across them).
- **Cycles are refused**, with the path: adding a component or substitute walks the published BOM
  graph from the produced item (`409 … PANEL-NAVY -> SHIRT-01 -> PANEL-NAVY`), and publishing
  re-checks — the safety net for two drafts that are each fine alone.

## Arithmetic (`domain.py` — integers, one rounding, always up)

Quantities are milli-units, scrap and thresholds basis points (2.5 % = 250). For a line with
`c` per batch of `b`, planned output `P`, scrap `s` bps, substitute ratio `r` (1000 = 1:1):

```
required(P)      = ceil( c · P · (10000 + s) · r / (b · 10000 · 1000) )   # whole units for a discrete UOM
theoretical(G)   = same with s = 0, for the RUNNING TOTAL G of good output
```

Worked example (the tests pin it): 7 shirts at 1.234 m with 2.5 % scrap need `1234·7000·10250 /
(1000·10000)` = 8853.95 → **8.854 m**. Output of 3 consumes `ceil(1.234·3)` = 3.702; a further 4
consumes `ceil(1.234·7)` − 3.702 = 4.936 — the parts sum to the whole (8.638), never a rounded
part at a time.

Planned requirement includes the scrap allowance; **consumption at output is the no-scrap
standard** of the running total; the scrap allowance stays in WIP until it is consumed by wastage,
returned, or scrapped at completion.

## Flow, states and rules

`DRAFT → APPROVED → IN_PROGRESS → COMPLETED`, `CANCELLED` from any of the first three
(`domain.PRODUCTION_ORDER_TRANSITIONS`; anything else, including a no-op, is 409).

1. **Plan** (`POST /orders`): picks the version in force (or a named published one), copies each
   effective line into `production_materials` with its required quantity. A draft can be edited
   (quantity recomputes every line); a material can be switched to one of its line's declared
   **substitutes** while nothing of it is reserved or issued (requirement rescales by the ratio).
2. **Approve** — own permission.
3. **Reserve** (`POST /orders/{id}/reserve`): by default every material's open need from the
   largest free `AVAILABLE` buckets; or explicit lines with a location, lot and/or roll. A short
   result is **not an error**: it reserves what exists, returns `shortages`, and stages
   `operations.production.material_shortage`. A later reserve tops a pick up (release and
   re-reserve the combined quantity in a savepoint, so a refusal leaves the old reservation as it
   was). Racing orders each get what is actually left: a refused bucket is re-read and retried.
4. **Issue** (`POST /orders/{id}/issue`): empty body issues every open reservation in full (the
   BOM-based issue); lines name a pick, or a material + location (+ lot/roll) + quantity for stock
   that is not reserved. Issuing beyond a material's requirement needs `over_issue: true`. The
   first issue starts the order (`operations.production.started`). Before every action the order
   re-reads each reservation's open remainder from the kernel, so a reservation released through
   the reservations API is noticed rather than trusted.
5. **Output** (`POST /orders/{id}/outputs`): consumes the standard from WIP **oldest issue first**
   (so lot attribution is deterministic) and receives the good quantity. Not enough WIP for the
   standard → 409 `insufficient-wip` naming each material and the shortfall, with nothing
   partially posted. Each consumed pick writes a **genealogy row** (parent item/lot/roll → output
   item/lot/roll, quantity) — the production-side record — and, when the output names a lot, the
   **lot graph** is linked too: `lot_genealogy` edges input lot → output lot through Phase 1's
   `link_lots`, relation `BLEND` when several lots of one item went in, `DERIVED` otherwise (an
   input that is the output's own lot adds none; an existing edge is reused; an edge that would
   close a loop is 409). The lot trace of a finished lot therefore walks back to its inputs.
6. **Wastage** (`POST /orders/{id}/wastage`): a material, a type (`CUTTING, DEFECT, SPILLAGE,
   DAMAGE, PROCESS_LOSS, OTHER`; `LEFTOVER` is written by completion), a quantity, optionally a
   pick. Drawn from WIP, one wastage row per pick (so a loss is attributable to a lot).
7. **Return** (`POST /orders/{id}/returns`): WIP back to `AVAILABLE` at the pick's source location
   or another.
8. **Complete** (`POST /orders/{id}/complete`): needs output; material still in WIP must be
   resolved with `leftover: "return"` or `"scrap"` (scrap = `LEFTOVER` wastage) or it is 409.
   Releases open reservations, stages `operations.production.completed` with the variance.
9. **Cancel**: refused while material is in WIP (return it, or complete and scrap it); releases
   reservations.

**Variance** (`GET /orders/{id}/variance`, also in the order detail): per material, `standard`
(the BOM need with scrap for the **produced** quantity) vs `actual` (consumed + wasted);
`variance = actual − standard`, positive is over-use, with a two-decimal percentage computed in
integers.

**Wastage threshold alerts.** The line is the most specific matching `wastage_thresholds` row
(product + type, product, type, everything), else the BOM line's own scrap allowance, applied as
basis points of the order's no-scrap need of that material. A write that takes cumulative wastage
(of the row's type scope) over it stages `operations.production.wastage_threshold_exceeded` — **once
per material**, on the crossing.

## Events

Staged in the request's transaction through the outbox; the relay publishes after commit. Schemas
in `libs/contracts/events/`, checked against real payloads in the tests.

| Event | When |
|---|---|
| `operations.production.started` | the order enters `IN_PROGRESS` |
| `operations.production.completed` | the order completes; carries produced quantity and per-material variance |
| `operations.production.material_shortage` | a reserve could not cover every material |
| `operations.production.wastage_threshold_exceeded` | wastage crossed its threshold |

Every write also stages audit rows (`production_order.*`, `bom_version.published`, …) and the
kernel stages its own `inventory_move.posted` / `stock.posted` for each move.

## Idempotency and concurrency

- Every mutating endpoint accepts `Idempotency-Key`. The key is claimed first
  (`manufacturing_requests`, `ON CONFLICT DO NOTHING`): a concurrent duplicate blocks on the unique
  index until the first request commits, then **replays** it (`replayed: true`, or `200` instead of
  `201` on a create). The same key with a different request is 409. Stock moves also carry a
  derived kernel key (`mo:<order>:<op>:<key>:<n>`).
- Lock order: the **order row first**, then its materials and picks, then whatever the kernel
  locks (balances, then reservations). Two actions on one order queue; two different orders
  competing for the same stock meet only on the balance row.
- **Rolls lock before balances.** A request that touches rolls takes them `FOR SHARE`, in id
  order, before its first balance lock (the order Phase 1's split and merge rely on). `issue`,
  `return` and `wastage` are one move each and get that from `post_move`; `output` (a consume move
  then a receipt move) and `complete` (a return or several scraps) lock every roll up front with
  `ledger.validate_tracking`, so a later move's roll is never waited for while an earlier move's
  balance is held. Proven by `tests/test_manufacturing_tracking.py` (the output parks on a held
  roll while the consumed WIP balance is still free).
- Proven in `tests/test_manufacturing_concurrency.py` with real connections: two issues for the
  same stock → exactly one succeeds, never negative; 10 orders × 15 against 100 → exactly 6;
  4 orders reserving 160 against 100 → exactly 100 promised; two outputs against the same WIP →
  one wins; 8 retries of one keyed issue → issued once; issue racing cancel → consistent either
  way. The reconciler is clean after each.

## Permissions

| Code (`operations.manufacturing.`) | Gates | Dangerous |
|---|---|---|
| `bom.read` | BOMs, versions, explosion, thresholds list | |
| `bom.write` | create BOM, edit drafts, components, substitutes, thresholds | |
| `bom.publish` | publish / retire a version | ✔ |
| `production_order.read` | orders and everything under them | |
| `production_order.write` | plan, edit, substitute, cancel | |
| `production_order.approve` | approve an order | ✔ |
| `production_order.issue` | start, reserve, issue, return, wastage | |
| `production_order.complete` | record output, complete | |

All ORG-only (shared operational documents; every route also floors at ORG). Roles:
`OPERATIONS_ADMIN` (everything, by construction), `OPERATIONS_MANAGER` (all eight),
**`PRODUCTION_PLANNER`** (new: read/write BOMs, plan and run orders, read items/warehouses/
locations — **not** `bom.publish` or `production_order.approve`). `OPERATIONS_USER` and
`OPERATIONS_WORKER` gain nothing.

**Live tenants (ADR-0007).** A permission added to an existing role template reaches new
organizations only. Regenerate the manifest (`make openapi`, done) and, after the deploy, run the
reconcile for `operations` — see the runbook entry *Inventory Phase 5*.

## Migration `d3b9f2a7c614`

Up: 12 tables, their RLS and indexes, three trigger functions + triggers (the immutability guard),
`REVOKE UPDATE, DELETE` on the three append-only tables. Down: drops all of it; **destructive by
design** (the tables are new in this revision). Verified by `tests/test_migration_manufacturing.py`
(down → nothing left, up → everything present, RLS forced and policied on every table, no foreign
key out of manufacturing, privileges, tenant isolation as the app role, down over seeded data, and
`alembic check`).

## Deferred, and why

- **Lot and roll pickers / creating a lot on output.** Orders and genealogy carry `lot_id`/`roll_id`
  that are validated against Phase 1's tables, but the UI still takes them as ids and an output
  cannot mint its own lot.
- **QC.** Output can land in `QUALITY_HOLD`; inspections and release are Phase 4.
- **Costing / cost roll-up, `valuation_posted`** — Phase 8. **MRP** (demand-driven order creation,
  net requirements) — Phase 7. Production orders are created by a person.
- **Routings, work centres, labour and machine time, co-/by-products, rework loops** — not built;
  a BOM has one output, and scrap is material loss.
- **Approval-service routing** for `approve` (ADR-0006): approval is a plain permission-gated step.
- **Backflush on scan / mobile issue and output** — Phase 9.
