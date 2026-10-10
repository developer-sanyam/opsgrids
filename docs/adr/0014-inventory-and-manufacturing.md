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

## 11. Count-below-reserved stays refused; the resolution is a partial release

**Amended 2026-09-23, resolving the Phase 1 prerequisite [ledger.md](../inventory/ledger.md) §
"Not in Phase 0" left open.** Phase 0 built the check — a count (or any outbound move) that would
leave a bucket promising more than it holds is refused, `InsufficientStock`, rather than posted —
and left the way out of that refusal unbuilt, because nothing yet exposed a reservation over HTTP.
Phase 1's item master and location APIs make that refusal reachable for the first time, so it
needs a real resolution now, not a future promise.

**Decision: the refusal stays exactly as Phase 0 built it, and the resolution is releasing the
reservation(s) in the way, through the same kernel.**

- **A count below `reserved_milli` is still refused, not reconciled.** Silently shrinking a
  reservation to make a count fit would break a promise the count-taker has no way to know was
  made, to someone the count screen never shows. A person decides, explicitly, which promise gives
  way — the kernel does not decide it for them.
- **The resolution is `POST /operations/inventory/reservations/{id}:release`** (`operations.
  inventory.stock.reserve`, ORG). It can give back a reservation's whole open remainder, or —
  new in Phase 1 — a **named quantity**, in milli-units, leaving the rest active. Partial release
  exists because a reservation is often bigger than the one count that is blocked by it: releasing
  all of a 500-piece production reservation to fix a count that is 12 pieces short over-corrects.
  Both forms go through `app.inventory.ledger.release` — still the kernel's, still under the
  balance's lock, still audited (`inventory_reservation.released`) and still eventable
  (`operations.inventory.stock.released`, carrying `released_milli`, `remaining_milli` and
  `fully_released`).
- **The endpoint is idempotent.** A retried release with the same `Idempotency-Key` replays
  rather than releasing twice; the same key against a different quantity is a 409. The key and a
  hash of the request are kept on the reservation row itself, compared under the row's own lock,
  rather than a global unique index — the property being protected is "this reservation's writers
  serialise", which the lock already gives, not "this key is unique across every reservation in
  the organization".
- **`InsufficientStock` names what is blocking it.** When a shortfall is caused by reserved stock
  rather than by there being too little on hand at all, `errors[0].reservations` lists every
  active reservation on that bucket — `reservation_id`, `reference_type`/`reference_id`, and the
  open quantity each still holds, oldest first. A shortfall with nothing physically there to give
  (`on_hand` itself would go negative) carries no reservations: releasing one would not have
  helped, so naming one would mislead. This is what lets a stocktake screen offer "release 12
  pieces from reservation X, then count" instead of a bare refusal.
- **Every outbound path through the kernel is covered by the same check, not a copy of it.**
  `post_move` applies one shortfall test to every line — issue, relocation (a warehouse change,
  and Phase 1's bin-to-bin transfer), a status change's paired move, and a count — because they
  are all just lines with a negative delta (or a count below the held figure) against a bucket.
  There is one refusal, proven once, reused everywhere a bucket can go outbound; a transfer or a
  status change out of a reserved bucket refuses on exactly the same numbers a count would.

**Rejected:**

- **Auto-releasing the oldest reservation to let a count through.** Silent, and wrong exactly when
  the oldest reservation is the most important one (a production order already committed to a
  ship date). A person who can see *which* reservation is in the way makes a better call than a
  FIFO guess.
- **A "force count" override that posts anyway and desyncs `reserved_milli` from real
  reservations.** That is precisely the drift `inventory_discrepancies` exists to catch, invented
  on purpose instead of found by accident. Every override this platform has (negative-stock
  override, is planned the same way, dangerous and explicit) — this is not the place to add one
  without also adding its permission and its audit trail, which a resolved shortfall does not need
  once release exists.
- **Rejecting the reservation's remainder instead of shrinking it.** Would need a second verb
  (`reject`) with its own semantics for what happens to the demand that made the reservation in
  the first place (a production order, a sales order) — a Phase 3+ concern once those documents
  exist. Release is the only operation Phase 1 needs, because nothing yet creates a reservation
  over HTTP for a person to reject *from*.

## 12. Phase 1 backend: what the lot / roll / stock-operation slice decided

**Added 2026-10-04, with the Phase 1 backend ([ledger.md](../inventory/ledger.md) § Lots, rolls and stock
operations).** Four choices a later phase will lean on:

- **Splits, merges, transfers and status changes are `relocation` moves**, told apart by `reference_type`
  (`roll_split`, `roll_merge`, `bin_transfer`, `status_change`; `putaway` added 2026-10-05, see the amendment at
  the end of this section) — not four new move kinds. Stock is conserved in each
  (a negative line and positive ones in one move), which is what `relocation` already means; a new kind would
  have meant every reconciler, filter and event consumer learning a vocabulary for no new semantics.
- **The bucket's lot/roll foreign keys are composite and organization-scoped**
  (`(organization_id, lot_id)` -> `lots (organization_id, id)`), not single-column. A single-column FK would let a
  bucket name another tenant's lot; the composite one makes that a constraint violation even if a query forgot
  the filter. Orphan ids already on a database are given `LEGACY-…` placeholder rows by the migration, never
  refused and never dropped.
- **A roll split apportions weight by flooring, remainder to the last child** (`domain.split_secondary`).
  Rounding to nearest can push the earlier children past the whole and leave the last a negative weight;
  flooring cannot, at the price of at most `n - 1` milli-units on the last child.
- **Lists are keyset-paginated** (`app/inventory/pagination.py`: `(sort value, id)` cursor, no `COUNT(*)`), because
  the ledger grows without bound; the platform's offset `paginate` stays for admin-sized lists.

Opening stock requires `inventory.stock.adjust` (it creates quantity); transfers and status changes require
`inventory.stock.transfer`; split and merge require `inventory.roll.write`. `uom_conversions` stays unwired until
Phase 2.

Three further rules, added after review of the slice:

- **A status change has a direction.** `stock.transfer` may only move stock *into* a hold (AVAILABLE ->
  QUALITY_HOLD / QUARANTINE / DAMAGED / REJECTED / BLOCKED, or between holds). Moving it *out of* REJECTED,
  DAMAGED, QUARANTINE, QUALITY_HOLD or BLOCKED into AVAILABLE or WIP is a release, and also requires
  `inventory.stock.adjust` at ORG scope (403 otherwise): it turns stock someone withheld back into sellable stock,
  which is the same power as creating quantity. Without the gate a floor worker holding only `stock.transfer`
  could launder rejected stock into AVAILABLE.
- **A roll is split or merged only when it lives in one bucket.** The roll's total on-site stock across *all*
  buckets must equal the quantity held in the bucket the request names (409, other buckets named, otherwise).
  The split's child rolls and the merge's result roll live in a single bucket, so a roll with stock elsewhere
  (another bin, a QUALITY_HOLD portion) would silently strand that remainder on a `SPLIT`/`MERGED` roll. The
  check runs under `FOR UPDATE` on all of the roll's balance rows, taken after the roll lock, so a concurrent
  transfer cannot change the picture between check and post.
- **Lock order is item -> roll -> balance, everywhere.** `post_move` takes every roll its lines name `FOR SHARE`
  in id order before its first balance lock; split and merge take theirs `FOR UPDATE`. A transfer racing a split
  of the same roll therefore queues on the roll row and loses cleanly (409 / `INSUFFICIENT_STOCK`) instead of
  deadlocking. A split/merge also verifies the lines it posted left the consumed roll at exactly zero.

**Amended 2026-10-05 — storage put-away joins the move reference vocabulary.** The warehouse-map change
([ADR-0028](0028-putaway-gps-captures.md); design in
`docs/superpowers/specs/2026-10-05-warehouse-map-putaway-design.md`) adds one value, **`putaway`**, to the
`relocation` references listed in the first bullet above (`reference_type` is a free `String(30)`, so there is no
migration for it). A storage put-away moves received or staged stock into a rack bin by scanning the bin's
barcode. It is the same conserving relocation as a `bin_transfer` (a negative line out of the source bin, a
positive line into the destination, one `post_move`, through the same private `_transfer` helper, the same lock
order and the same refusal of reserved stock with a 409), and it differs from a bin transfer in these ways. None
of them touches `bin_transfer`, whose behaviour and `operations.inventory.stock.transferred` event are unchanged
byte for byte:

- **The reference.** `reference_type` is `putaway` (a bin transfer's is `bin_transfer`), and its `reference_id` is
  a deterministic uuid5 of the idempotency key (a bin transfer's is empty), because both are part of the move's
  request hash.
- **The audit row and the event.** It stages the audit action `putaway.recorded` and the event
  `operations.inventory.putaway.recorded` (a closed schema that carries `matched_suggestion` and `has_location`
  and never a coordinate) **instead of** `stock.transferred`. `post_move` still stages `inventory_move.posted` /
  `stock.posted` for both.
- **A capture row.** It writes a `putaway_captures` row (the scanned bin, the suggested bin, the worker, the
  worker's GPS fix when the device gave one), which is what ADR-0028 decides. The row is written in a savepoint:
  if it cannot be written the move stands and the response carries the warning `geo_not_recorded`.
- **Where the stock may come from and go to.** The source must be a `receiving` or `staging` bin and the
  destination a storable bin (`bin` or `shelf`) in the same warehouse (owner decision D1, 2026-10-05: 422 otherwise,
  with the codes `source_not_receiving_or_staging` and `destination_not_storable`). A bin transfer has no
  source-kind or destination-kind rule, only the same-warehouse one. Because the order engine reserves only at an
  item's `home_location_id` (`orders/services.py`), stock put away into any other bin is not seen by order accept
  until multi-location allocation exists; that limitation is documented in the RUNBOOK and the backlog.
- **How the destination is named.** By the bin's barcode, resolved server-side (a worker holds no
  `location.read`), where a bin transfer names `to_location_id`. An unknown, foreign or deleted barcode is one
  identical 404.
- **What is moved.** Only `AVAILABLE` stock (a bin transfer takes any on-site `status`), and it runs
  `check_tracking`, so a tracked item must name its lot or roll and an untracked one must not (the bin-transfer
  path does not call it).
- **The idempotency namespace.** The `Idempotency-Key` header is **required** (a bin transfer's is optional) and is
  stored as `putaway:<key>`, so a put-away's key can never collide with a bin transfer's. The GPS fix,
  `captured_at` and `suggested_location_id` are outside the request hash: a retry that differs only in those
  replays the original and writes nothing.
- **The response.** `PutawayOut` carries `matched_suggestion`, `geo_recorded` and a `warnings` list
  (`geo_dropped:<reason>`, `captured_at_adjusted`, `suggestion_ignored`, `geo_not_recorded`), and never a
  coordinate; invalid geo is dropped with a warning rather than refused.

**Terminology.** "Storage put-away" is the name of this feature and of the `putaway` reference. ADR-0025's
"putaway task" (the reversal that carries picked stock back to its bin when an order is cancelled, §1 and the
state table) is a different thing and is left untouched; where both could be meant, say "storage put-away" for the
first and "putaway task" for the second.
## 13. Phase 2: what the receiving slice decided

**Added 2026-10-05, with Phase 2 ([receiving.md](../inventory/receiving.md)).** Five choices a later phase will
lean on:

- **A receipt is one `receipt` move per goods receipt, and the receipt rows are immutable.** The header is
  inserted (under a unique `(organization_id, idempotency_key)` index, `ON CONFLICT DO NOTHING`) before any lot,
  roll or ledger row, so a retried or duplicated request replays instead of double-posting; the stock is
  found through the move's `reference_id`, so the receipt needs no mutable `move_id` and can be
  `UPDATE`/`DELETE`-revoked like the ledger. A correction is a later document.
- **Tolerance is integer basis points on the item, floored.** `limit = ordered + floor(ordered * bp / 10_000)`;
  0 (the default) means exactly the ordered quantity. Rejected: a float percentage (the limit must be
  reproducible to the milli-unit) and a tolerance on the PO line (the supplier agreement is per item; the
  line would have to be edited to change it).
- **Concurrency is the PO row lock, not a counter.** Receipts lock the PO, then its lines, then the items
  (each by id) and re-read them (`populate_existing`) before deciding, so two receipts against one line
  serialise and the second sees the first's cumulative figure. `purchase_order_lines.received_milli` is the
  one source for "how much has arrived"; `received_qty` is only its whole-unit view.
- **`partially_received` is a PO status, with `short_closed` per line.** `PO_TRANSITIONS` gained
  `approved -> partially_received` and `partially_received -> received`; a partially received PO cannot be
  cancelled (stock arrived against it), and the way to stop expecting the rest is `close_short` on the
  lines. Rejected: a `closed`-with-shortage transition (it would hide which lines were short).
- **Container units convert at the document, not the bucket.** `uom_conversions` is applied where a
  container unit appears on a document — the receipt line and the PO line — through exact `Fraction`
  arithmetic that refuses a non-whole result; a bucket is always in the item's stock unit, so the bucket
  key and the ledger never learn about boxes. A unit with a fixed registry conversion is refused a per-item
  row (one source for one fact), and a ratio cannot be deleted or changed under an open order that uses it.

Also: **a quality-tracked item is received straight into `QUALITY_HOLD`** (never `AVAILABLE` then moved), and
the single-shot receive endpoint keeps its answers — it is a wrapper, not a second implementation — and,
because it posts stock, demands the same two warehouse keys as a goods receipt (`receipt.write` and
`stock.receive`, ORG) on top of `purchase_order.write`.

## 14. Phase 3: what the movements and control slice decided

**Added 2026-10-05, with Phase 3 ([movements.md](../inventory/movements.md)).** Five choices a later phase
will lean on:

- **A transfer is a document over two kernel moves; stock in transit lives at the destination.** Dispatch is
  one `relocation` into the `IN_TRANSIT` bucket at the *destination* warehouse's default bin, receipt a
  `relocation` out of it, a shortage an `adjustment` out of it — no new move kind (§12's rule, again), no
  new table of in-flight quantities. Put at the destination, "what is on its way to me" is a query on that
  warehouse's own locations, and `IN_TRANSIT` stays outside on-hand as §3 says. The cost is that a
  warehouse needs a default bin to receive a dispatch; every warehouse gets one at creation.
- **Approval is routed by quantity, per line, in the item's own unit.** A threshold per organization
  (`inventory_control_settings`) decides: none set → a direct sign-off by a *different* person holding
  `inventory.transfer.approve` (transfers) or no approval (adjustments, preserving every existing client);
  set and under it → automatic; at or over → the approval service (ADR-0006), by events in both directions,
  the product applying the answer in its own transaction under its own state machine. A value threshold
  needs costing (Phase 8) and is deferred; summing quantities across units was rejected as meaningless.
- **Approved adjustments post as the system, under a deterministic key, inside a savepoint.** The decision
  can arrive twice, late, or after the stock has gone; the key makes a double delivery post once, the
  state check makes a late one a no-op, and the savepoint turns "the stock can no longer take it" into a
  recorded `FAILED` (with the kernel's reason, and `failed` on the return leg) instead of a poisoned
  consumer transaction.
- **Expiry is a release, not a second way to close a reservation.** The sweep calls `ledger.release`
  (key `expire:<id>`) like a person would, then stamps `expired_at`; nothing else writes
  `reserved_milli`. It discovers organizations on the outbox role with a column-level grant on exactly
  `(organization_id, status, expires_at)`.
- **Stocktake was not rebuilt.** `app/counting` (ADR-0026) is the stocktake; its manager review remains its
  own permission-gated approval. Routing a large variance through the approval service and auto-adjusting
  under a threshold is the extension left, and nothing was built that would compete with it.

## Consequences

- `operations` becomes the largest service; the module split is required, not optional.
- The milli-unit quantity and the ledger bucket key cannot change after go-live.
- Sorting the item list by `on_hand` is a correlated aggregate; at 100k+ SKUs it needs an
  item-level rollup (Phase 10 performance work), not a second writable source of truth.
- Sales dispatch depends on a sales-order source (storefront orders today); CRM sales orders
  integrate by event when they exist.
