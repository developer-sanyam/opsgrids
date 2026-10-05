# The stock ledger (Phase 0)

What Phase 0 of the [inventory plan](plan.md) built, how it behaves, and how to operate it.
Decisions and their reasons are in [ADR-0014](../adr/0014-inventory-and-manufacturing.md); this
page is the reference.

---

## Quantities

| On the wire | In Python | In PostgreSQL |
|---|---|---|
| `"82.5"` (decimal string, ≤ 3 decimals) | `82500` (`int`, milli-units) | `82500` (`BIGINT`) |

- One scale for every unit: 1 unit = 1000 milli-units. 82.5 m → `82500`, 14.8 kg → `14800`,
  1 piece → `1000`.
- `app/inventory/quantity.py`: `parse_qty` (refuses floats, and refuses more than 3 decimals
  rather than rounding them), `format_qty`, `convert` (exact `Fraction` conversions for fixed
  units only, and explicit rounding only), `value_minor` (money × quantity, rounded once).
- Discrete units (`pcs`, `box`, `carton`, `bundle`, `set`, `roll`) refuse fractions.
- A roll's length and weight are two measurements. They are never converted into each other.
- `SUM(bigint)` in PostgreSQL returns `NUMERIC`, so every aggregate is cast back to `BIGINT`,
  and a quantity is always an `int` all the way to the wire.

## Tables

| Table | What it is | Writable by the app role |
|---|---|---|
| `locations` | zone / rack / bin; one `is_default` bin per warehouse | yes |
| `inventory_items` | item master; **no stored stock** since Phase 0; `home_location_id` | yes |
| `inventory_moves` | one header per posting: kind, reason, reference, idempotency key | INSERT only |
| `inventory_ledger` | one row per bucket delta (formerly `stock_movements`, same row ids) | INSERT only |
| `inventory_balances` | current quantity + reserved per bucket, projection of the ledger | yes (kernel only) |
| `inventory_reservations` | promised stock; open remainder cached on the balance | yes (kernel only) |
| `inventory_discrepancies` | drift the reconciler found | yes |

Bucket = `(item, location, status, lot, roll)`. `lot`/`roll` are nullable UUIDs until Phase 1
adds their tables and foreign keys. Statuses: `AVAILABLE, QUALITY_HOLD, QUARANTINE, DAMAGED,
REJECTED, WIP, IN_TRANSIT, AT_JOB_WORKER, BLOCKED` (CHECK-constrained).

Figures on an item:

- **on hand** = sum of all buckets except `IN_TRANSIT` and `AT_JOB_WORKER`
- **reserved** = the open remainder of active reservations
- **available** = `AVAILABLE` stock − reserved (stock on hold, damaged, WIP etc. is on hand but
  not available)

## The posting kernel

`app/inventory/ledger.py` is the **only** writer of ledger, balances and reservations.

```python
posted = await ledger.post_move(
    session,
    ctx,
    kind="issue",  # opening|receipt|adjustment|count|relocation|issue
    lines=[MoveLine(item_id, location_id, -q("12.5"), roll_id=roll, reservation_id=res)],
    occurred_on=date.today(),
    reference_type="production_order",
    reference_id=po_id,
    idempotency_key="po-17-issue-3",  # optional
)
```

In one transaction (the caller's, i.e. the request's):

1. validates lines (no floats, no zero change except counts, known status/kind, each bucket once)
2. loads items and locations **filtered by the caller's organization** → 404 for anything else
3. inserts the move header (`ON CONFLICT DO NOTHING` on the idempotency key → replay or 409)
4. for each line, **in bucket order**: creates the balance row if missing, then `SELECT … FOR
   UPDATE`s it
5. checks under the lock: result ≥ 0, and an outbound line may not dip into reserved stock unless
   it draws on a reservation. Otherwise it raises `InsufficientStock` (409,
   `type …/insufficient-stock`) with `requested / on_hand / reserved / available` in `errors`. A
   **count** line is checked the same way, against the counted figure rather than a delta — a
   count that lands below the bucket's `reserved_milli` is refused, not reconciled against the
   reservations it disagrees with (see *Not in Phase 0* below)
6. updates the balance and appends the ledger row with `balance_after_milli`
7. stages one `inventory_move.posted` audit row and one `operations.inventory.stock.posted`
   outbox event (published by the relay after commit)

`reserve()` / `release()` follow the same lock order (balance, then reservation). A count line
passes `counted_milli`, and the change is computed under the lock.

Concurrency is proven, not assumed: `tests/test_concurrency.py` races 2–50 real connections
through each scenario (same stock consumed twice, 50 issues against 10 units, 40 × 2.5 m cuts from
82.5 m, over-reservation, reservation vs issue, receipts and issues mixed, 10 retries of one
request, opposite-order multi-line moves). With the row lock removed, 7 of its 9 tests fail.

## Reconciliation

- `app/inventory/reconcile.py::reconcile_organization` compares, **in one statement** so both
  sides see one snapshot: `balance.quantity_milli` vs `SUM(ledger)` per bucket (both directions,
  so a ledger bucket with no balance row is drift too), and `balance.reserved_milli` vs the
  active reservations.
- Each finding becomes one open `inventory_discrepancies` row per (kind, bucket). A later run
  refreshes `last_seen_at` instead of duplicating it. A **new** finding also logs
  `inventory.reconcile.drift` at error level and stages `operations.inventory.inconsistency_detected`.
- **Nothing is repaired.** A person investigates, fixes the cause (usually with an adjustment),
  and resolves the discrepancy with a note. If the drift is still there, the next run opens a
  new one.
- `bin/cron` → `app/cron.py` runs every `RECONCILE_INTERVAL_SECONDS` (default 86 400).
  Discovery runs as `opsgrids_outbox`, which is granted `SELECT (organization_id)` on the ledger
  and balances and nothing else (`migrations/env.py`). Every organization is then checked as the
  app role under its own tenant binding.

## API

New (all `Entitled`; permissions in brackets):

| Route | |
|---|---|
| `GET /operations/inventory/{id}/balances` | per-bucket stock of one item [`inventory.item.read`] |
| `POST /operations/inventory/reconcile` | run the check for the caller's org now [`inventory.stock.reconcile`, ORG] |
| `GET /operations/inventory/discrepancies` | open (or `?include_resolved=true`) findings [`inventory.stock.reconcile`] |
| `POST /operations/inventory/discrepancies/{id}/resolve` | `{note}`; changes no stock [`inventory.stock.reconcile`] |
| `POST /operations/inventory/reservations/{id}:release` | give back all or part of a reservation's open remainder (ADR-0014 §11) [`inventory.stock.reserve`, ORG] |

`POST /operations/inventory/{id}/adjustments` now accepts an **`Idempotency-Key`** header. A
retry replays the result; the same key with a different body is a 409. The SPA sends one per
distinct submission.

### Compatibility — what changed for existing clients

| | Before Phase 0 | After Phase 0 |
|---|---|---|
| Where stock lives | `inventory_items.on_hand` (int) + `stock_movements` | `inventory_balances` + `inventory_ledger`; the item column is **dropped** |
| `InventoryItemOut.on_hand` | the stored int | whole units of the balances (exact while stock is whole, which is all this API can create) |
| New fields | — | `on_hand_qty`, `reserved_qty`, `available_qty` (exact decimal strings) |
| `StockMovementOut` | movement row | ledger row + its move header; same `id`s, same fields, plus `quantity`, `balance_after`, `location_id`, `status` |
| Movement `kind` | 4 values | + `relocation` (an item's warehouse changed), `issue` |
| Changing an item's warehouse | relabelled the number | posts a `relocation` move, old bin → new bin; refused while that stock is reserved |
| Count (`counted_on_hand`) | set the item's number | sets the home bucket, computed under its lock; refused if the item holds stock anywhere else |
| Delete item / warehouse | refused while `on_hand ≠ 0` / items named it | also refused while any bucket holds stock or reservations |
| Negative-stock refusal | `…would take on-hand below zero` | `Cannot take 20 MT of … : 6 MT on hand, 0 MT reserved, so 6 MT available` + structured `errors` |
| Events | `stock.adjusted`, `purchase_order.received` unchanged | + `stock.posted` for every move, `stock.reserved`, `stock.released`, `inconsistency_detected`, `discrepancy.resolved` |

There is one source of truth, the ledger, with the balances as its locked projection. Nothing
reads or writes a second stock number.

## Migration `d4e8b1c93f20`

What it does, in order (all inside alembic's transaction):

1. **Refuses** if any item's `on_hand` ≠ the sum of its `stock_movements` (names the first
   offender). Fix the data, then re-run. Nothing is half-applied.
2. Creates `locations` with one `DEFAULT` bin per warehouse (a deleted warehouse gets a deleted
   bin, so history still has a location).
3. Items whose `warehouse` string matches no warehouse get a per-org `UNASSIGNED` /
   "Unassigned stock" warehouse, so their stock is never dropped.
4. Sets `home_location_id`; creates `inventory_moves` (one per movement, same id); renames
   `stock_movements` → `inventory_ledger` **in place** (ids, RLS policy and privilege revoke
   carry over); converts `× 1000`; builds balances as `SUM(ledger)` per bucket; drops
   `inventory_items.on_hand`.
5. Creates reservations and discrepancies. Every new table gets ENABLE + FORCE RLS and the
   `tenant_isolation` policy.

**Downgrade** restores the integer model with the same ids and numbers, and refuses rather than
truncate when there's a fractional quantity, stock outside an item's home bucket, an active
reservation, or a move kind the old ledger lacks.

Verified by `tests/test_migration_inventory_ledger.py` (real alembic, old-shape data including an
orphaned warehouse name and a soft-deleted item; upgrade → reconcile clean → downgrade → same
numbers) and by `make migration-roundtrip`.

**Before running it in production:** take the nightly backup (`infrastructure/edge/backup.sh`),
then run the check the migration itself enforces, so any drift is known beforehand:

```sql
SELECT i.id, i.sku, i.on_hand, COALESCE(SUM(m.quantity_delta), 0) AS ledger
  FROM operations.inventory_items i
  LEFT JOIN operations.stock_movements m ON m.inventory_item_id = i.id
 GROUP BY i.id, i.sku, i.on_hand
HAVING i.on_hand <> COALESCE(SUM(m.quantity_delta), 0);
```

Deploy order: migrate → `operations` API (the reconciler sweep runs inside it as a supervised
background task, `RUN_SWEEPS_IN_API`; it was its own `operations-cron` container until ADR-0013's
fix round 2) → the Operations SPA. The SPA must go **after** the API: it reads `on_hand_qty` and the
movement `quantity`, which the old API does not send. The old SPA keeps working against the new
API, since every field it reads is still there.

## Count-below-reserved, and the release that resolves it (Phase 1, ADR-0014 §11)

**A count that lands below a bucket's reserved quantity is still refused, not reconciled** — the
Phase 0 behaviour is unchanged. `post_move` applies one shortfall check to every outbound line and
to a count: if the result would leave the bucket promising more than it holds, it raises
`InsufficientStock` rather than posting. Silently shrinking a reservation to make a count fit would
break a promise the count-taker cannot see and someone else made.

What Phase 1 adds is the way out, and the numbers to act on it:

- **`errors[0].reservations`** names every active reservation standing in the way of a shortfall
  caused by reserved stock — `reservation_id`, `reference_type`/`reference_id`, and its open
  quantity, oldest first. A shortfall with nothing physically there at all (`on_hand` itself would
  go negative) carries no reservations — releasing one would not have helped.
- **`POST /operations/inventory/reservations/{id}:release`** (`operations.inventory.stock.reserve`,
  ORG) gives back all or part of a reservation's open remainder, through `ledger.release` — the
  same kernel, under the same balance lock, audited and eventable
  (`operations.inventory.stock.released`). An empty body releases everything still open; `{"quantity":
  "12.5"}` releases only that much and leaves the reservation active for the rest. It is
  idempotent: a retried `Idempotency-Key` replays; the same key against a different quantity is a
  409.
- **The same refusal, and the same naming, covers every outbound path** — issue, relocation (a
  warehouse change, and a bin-to-bin transfer once Phase 1 exposes one), a status change's paired
  move, and a count. They are all just lines with a negative delta (or a count below the held
  figure) against a bucket, checked once in the kernel and never copied into a caller.

So the resolution a stocktake screen offers is concrete: read `errors[0].reservations`, call
`:release` on the one(s) blocking the count (in full or in part), then retry the count.

## Lots, rolls and stock operations (Phase 1 backend, migration `b7d41e92c05a`)

The kernel has always carried `lot_id`/`roll_id` in the bucket key; Phase 1 gives them tables and
HTTP. All of it lives in `app/inventory/` (`tracking_*`, `stock_ops`, `api`, `pagination`).

| Table | What it is | Writable by the app role |
|---|---|---|
| `lots` | batch of one item: internal + supplier lot no., dates, quality status, source document, flat attributes | yes |
| `rolls` | one physical roll: primary measured quantity (the item's UOM) + secondary (e.g. 82.5 m / 14.8 kg), width, GSM, shade, grade, `parent_roll_id`, state `ACTIVE`/`SPLIT`/`MERGED` | yes |
| `roll_events` | a roll's history (created, opening, split, merged, moved, status changed, re-measured) | INSERT only |
| `lot_genealogy` | parent -> child lot edges | INSERT only |

`inventory_ledger`, `inventory_balances` and `inventory_discrepancies` now have **organization-scoped**
foreign keys to `lots`/`rolls` (`(organization_id, lot_id)` -> `lots (organization_id, id)`), so a bucket
cannot name another tenant's lot even if a query forgot the filter. `post_move` checks the same thing
first and answers 404/422 instead of an `IntegrityError`: the lot and roll exist in this organization, belong
to the line's item, agree with each other, and a `SPLIT`/`MERGED` roll cannot receive stock.

### Operations (all through `post_move`, all take `Idempotency-Key`, all audit + emit an event)

| Route | Permission | Move |
|---|---|---|
| `POST /operations/inventory/stock/opening` | `inventory.stock.adjust` | `opening`, one line per location/status/lot/roll |
| `POST /operations/inventory/stock/transfers` | `inventory.stock.transfer` | `relocation` (`bin_transfer`), same warehouse only |
| `POST /operations/inventory/stock/status-changes` | `inventory.stock.transfer`; **plus `inventory.stock.adjust` to move stock out of a hold** (REJECTED, DAMAGED, QUARANTINE, QUALITY_HOLD, BLOCKED) into AVAILABLE or WIP — 403 otherwise | paired `relocation` (`status_change`); never to `IN_TRANSIT`/`AT_JOB_WORKER` |
| `POST /operations/inventory/rolls/{id}:split` | `inventory.roll.write` | `relocation` (`roll_split`): parent out, n children in |
| `POST /operations/inventory/rolls:merge` | `inventory.roll.write` | `relocation` (`roll_merge`): n sources out, one result in |
| `POST/PATCH /operations/inventory/lots`, `POST .../lots/{id}/genealogy` | `inventory.lot.write` | no stock |
| `POST/PATCH /operations/inventory/rolls` | `inventory.roll.write` | no stock |
| `DELETE /operations/locations/{id}` | `inventory.location.write` | refused while the location holds stock or has **any** ledger history, is a default bin, has children, or is an item's home bin |

Reads (all `inventory.stock.read`, ORG, **cursor-paginated** — `?cursor=&limit=`, response
`{items, next_cursor}`): `GET .../stock` (by item/warehouse/location/lot/roll/status),
`GET .../ledger` (item, location, lot, roll, kind, status, `from_date`/`to_date` on the move's business
date), `GET .../lots`, `.../lots/{id}`, `.../lots/{id}/genealogy`, `.../lots/{id}/trace`, `GET .../rolls`,
`.../rolls/{id}`, `.../rolls/{id}/events`, `.../rolls/{id}/trace`. A cursor is opaque, validated on every
request (422 when malformed) and carries no tenant or scope — it only moves the window.

### Rules worth knowing

- **Split.** The children's quantities (decimal strings) must add up to **exactly** what the roll's bucket
  holds (422 with both numbers otherwise). The roll's measured secondary quantity is apportioned in
  proportion to the children's primary quantities: every child but the last gets
  `floor(secondary * child / total)` in milli-units, **the last takes the remainder**, so the children's weights
  always sum to the parent's. 82.5 m / 14.8 kg into 50 + 32.5 gives 8.969 kg + 5.831 kg. An unweighed roll gives
  unweighed children. Children inherit item, lot, width, GSM, shade and grade, and are numbered `<parent>-1…`
  unless named. A roll splits once: the second of two racing splits finds it `SPLIT` and gets 409.
- **Merge.** Same item, same lot, same shade, all `ACTIVE`, all holding stock in the named bucket, or 422/409.
  Width, GSM and grade survive only where every source agrees; the result's weight is the sum of the sources'
  weights only if every source was weighed in one unit.
- **Reserved stock** cannot be transferred, status-changed, split or merged away: the kernel refuses with
  `InsufficientStock` and `errors[0].reservations` (ADR-0014 §11) — release the reservation, then retry.
- **Idempotency.** Keyed split/merge derive the ids of the rolls they create from `(organization, key)`, so a
  retry rebuilds identical ledger lines and the kernel replays it (200, `replayed: true`); the same key with a
  different request is 409. Replays stage no further audit rows or events.
- **One bucket.** A roll is split or merged only when it lives in one bucket: its total stock across *all*
  buckets must equal what the named bucket holds, else 409 `roll_in_several_buckets` naming where the rest is.
  Consolidate first (transfer / status change). The check runs under `FOR UPDATE` on all the roll's balance rows.
- **Derived child numbers** (`<parent>-1…`) longer than 60 characters are a 422 `too_long`: name the children.
- **Concurrency.** Lock order is roll -> balance, everywhere. Split/merge take roll rows `FOR UPDATE` (id order),
  then the roll's balances; `post_move` takes every roll its lines name `FOR SHARE` (id order) before its first
  balance lock, so a transfer racing a split queues on the roll and loses with 409/`INSUFFICIENT_STOCK`, never a
  deadlock. `post_move` takes the location rows `FOR KEY SHARE` (id order) so a post and a `delete_location`
  serialise. A keyed retry is looked up before the roll/lot state is validated, so it replays even after what
  it created has since been split again.
- **Traces.** Lot and roll traces are recursive CTEs over `lot_genealogy` and over `rolls.parent_roll_id` +
  `roll_events` `MERGE_RESULT` rows, filtered by organization at every step, depth-capped at 25. A split/merge
  inside one lot leaves a self-edge in `lot_genealogy` as evidence tied to the move; the trace skips
  self-edges. `BLEND`/`REWORK`/`DERIVED` links between two lots are what it follows, and creating one that would
  close a loop is refused (under a per-organization advisory lock) by an **uncapped** reachability check — the
  25-hop cap is for display only.
- **Tracking flags.** A lot can only be registered for an item with `track_lot`, a roll for one with
  `track_roll`; opening stock for a tracked item must name its lot/roll, and for an untracked item must not.

### Migrations `b7d41e92c05a` and `c4a8e1f6d273`

`b7d41e92c05a` creates the four tables (ENABLE + FORCE RLS, `tenant_isolation`, tenant-leading indexes),
backfills, and adds the bucket foreign keys **`NOT VALID`** (last, under `lock_timeout = 5s`).
`c4a8e1f6d273` validates the keys and builds the keyset indexes on the ledger/balances/moves with
`CREATE INDEX CONCURRENTLY` (in `autocommit_block`), so neither blocks stock posts for longer than a lock
acquisition; it is a separate revision so a failed build resumes on re-run rather than stranding a half-applied
first revision. **Existing data:** if any ledger/balance/discrepancy/count-line row already names a lot or roll
(the kernel allowed it, nothing in the product could set one), the upgrade creates a `LEGACY-…` placeholder
lot/roll per distinct (organization, item, id) instead of refusing — an id shared by several items gets one
placeholder each and each item's rows are re-pointed at its own, so no item is stranded; no quantity changes.
**Downgrade** drops the tables and constraints (the ids stay on the ledger as plain UUIDs) but refuses when any
non-placeholder lot or roll exists, unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`; the supported rollback is
code-only (RUNBOOK, "Inventory Phase 1 rollback"). Verified by `tests/test_migration_lots_rolls.py` (real alembic:
orphan ids, shared ids, validated keys and valid indexes, resume after an interrupted build, lock timeout, FK
enforcement incl. cross-tenant, RLS, append-only privileges, refusing downgrade, down and up again).

### Deferred

`uom_conversions` (per-item box = 12 pcs) already exists as a table but has **no API and is not applied in
quantity handling**: wiring a container unit through opening, transfer and split touches the item master's UOM
rules, and a half-applied conversion is worse than none. It lands with the receiving slice (Phase 2), where
`box` quantities first appear on a document.

## Storage put-away, warehouse coordinates and the map reads

*Built on `feat/warehouse-map-putaway`, not deployed.* Migration `d7a3c9e5b184` (down_revision
`c4a8e1f6d273`; it is re-pointed if another inventory stack merges first). Design:
[`docs/superpowers/specs/2026-10-05-warehouse-map-putaway-design.md`](../superpowers/specs/2026-10-05-warehouse-map-putaway-design.md);
decisions: [ADR-0028](../adr/0028-putaway-gps-captures.md) and the 2026-10-05 amendment to
[ADR-0014](../adr/0014-inventory-and-manufacturing.md) §12; rollout, rollback and the purge:
RUNBOOK, "Warehouse map and storage put-away".

| Table / column | What it is | Writable by the app role |
|---|---|---|
| `warehouses.lat_micro`, `lng_micro` | the *site's* position, integer micro-degrees (no float), both set or both empty, range-checked | yes |
| `putaway_captures` | one row per put-away move: the scanned bin, the suggested bin, the worker (nullable), the actor, the GPS fix when the device gave one, `captured_at` (device's claim) and `created_at` (server) | INSERT only; deleted only by `tools/purge_putaway_captures.py` as the admin role |

A **storage put-away** is a `relocation` move with `reference_type` `putaway`: stock out of a `receiving` or
`staging` bin, into a storable bin (`bin` or `shelf`) of the same warehouse, found by **scanning the bin's
barcode** (resolved server-side, so a worker without `location.read` can still do it). The bin is the location of
record; the GPS fix is proof, never the location. `bin_transfer` and its `operations.inventory.stock.transferred`
event are unchanged; a put-away stages `operations.inventory.putaway.recorded` instead (no coordinates).

| Route | Permission | Notes |
|---|---|---|
| `POST /operations/inventory/putaway` | `inventory.stock.transfer` (ORG) | `Idempotency-Key` required (stored `putaway:<key>`); source must be `receiving`/`staging` (422 `source_not_receiving_or_staging`); unknown, foreign or deleted barcode is one identical 404; bad geo is dropped with a warning, never blocks the move; geo is not in the request hash, so a retry with different geo replays the original |
| `GET /operations/inventory/putaway/suggest` | `inventory.stock.transfer` | the bin already holding the item, else the empty storable bin with the lowest `pick_sequence`; no capacity logic |
| `GET /operations/inventory/putaway/{move_id}` | `inventory.stock.read` | bin, worker, time, `matched_suggestion`, `has_location`; **never coordinates**; 404 once the capture is purged |
| `POST /operations/inventory/putaway/{move_id}/location:reveal` | `inventory.putaway.location_read` (ORG, ADMIN only) | the only response that carries coordinates; POST only, `Cache-Control: no-store`; one `entity.sensitive_read` audit row per successful call |
| `GET /operations/inventory/locate` | `inventory.stock.read` | bins holding an item/lot/roll with their placement on the *published* layout (own or nearest placed ancestor); geometry only with `warehouse.layout.read` |
| `GET /operations/floors/{floor_id}/published-layout` | `warehouse.layout.read` | published placements only; draft data never appears |
| `GET /operations/warehouses/map` | `inventory.warehouse.read` (as the warehouse list) | sites with coordinates, and those without |

**Retention.** Captures are kept 30 days from `created_at`, then erased by the admin-run
`tools/purge_putaway_captures.py` (dry run unless `--apply`; counts only). It is not an in-app job: the app role
cannot delete the rows.

**Limitation.** The order engine reserves only at `item.home_location_id` (`orders/services.py`), so stock put away
into any other bin is not seen by order accept until multi-location allocation exists; that is why put-away is
restricted to `receiving`/`staging` sources.

## Not in Phase 0

Costing layers and every later phase (lots, rolls, multi-location moves and locations got their HTTP
APIs in Phase 1 — see above). See [plan.md](plan.md).
