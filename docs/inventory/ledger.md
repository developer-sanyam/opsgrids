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
   `type …/insufficient-stock`) with `requested / on_hand / reserved / available` in `errors`
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

Deploy order: migrate → `operations` API → `operations-cron` (new container, `inventory`
profile) → the Operations SPA. The SPA must go **after** the API: it reads `on_hand_qty` and the
movement `quantity`, which the old API does not send. The old SPA keeps working against the new
API, since every field it reads is still there.

## Not in Phase 0

HTTP APIs and screens for locations, lots, rolls, reservations and multi-location moves (the
kernel supports them; nothing exposes them yet), per-item `uom_conversions`, costing layers,
and every later phase. See [plan.md](plan.md).
