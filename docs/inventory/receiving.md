# Receiving and purchase (Phase 2)

Decisions: [ADR-0014](../adr/0014-inventory-and-manufacturing.md) §13. Ledger and kernel:
[ledger.md](ledger.md). Plan: [plan.md](plan.md) § Phase 2. Code: `services/operations/app/receiving/`
(`domain`, `receipts`, `requisitions`, `conversions`, `selectors`, `schemas`, `api`), migration
`c5e91a7d3b28`.

The flow:

```
purchase requisition  --approve-->  convert  -->  purchase order (draft)  --approve-->  PO
PO (approved / partially_received)  --goods receipt-->  stock + lots + rolls  -->  PO partially_received / received
```

Nothing here writes a quantity. A goods receipt is **one `receipt` move through `post_move`**
(`reference_type='goods_receipt'`, `reference_id=<receipt id>`), the kernel's only writer.

## Purchase requisitions

An internal request to buy: lines of (item, quantity in the item's stock unit, optional suggested
supplier, optional estimated unit price), raised by anyone with `requisition.write`.

```
DRAFT --submit--> SUBMITTED --approve--> APPROVED --convert--> CONVERTED
  |                  |  \--reject--> REJECTED        (terminal: raise a new one)
  \------ cancel ----+-- cancel ---- APPROVED --cancel--> CANCELLED
```

`REQUISITION_TRANSITIONS` in `domain.py` is the map; every other move is a 409, including a no-op.
A decision locks the requisition row first and re-reads the status under the lock, so two people
deciding the same requisition cannot both succeed. `approve` and `reject` need
`requisition.approve` (ORG-floored — a requester cannot approve their own request); `reject`
requires a reason.

**Convert** (`requisition.write` **and** `purchase_order.write`) creates **one draft purchase
order** with the supplier you name, a line per requisition line, through the PO module's own
writer (`raise_purchase_order`: the supplier must be active, the number is server-assigned, the
supplier's open-PO count is bumped). Line prices come from `prices[]` (by line id), else the
requisition's estimate, else 0 — a draft PO is where the buyer finishes pricing. The PO then goes
through the ordinary `purchase_order.approve` gate. A PO line is a whole number of units, so a
requisition line with a fractional quantity is refused at conversion with the line named (422
`not_whole`); the requisition stays APPROVED.

## Goods receipts

`POST /operations/goods-receipts` with an `Idempotency-Key` — one delivery against one purchase
order (approved or partially received), any number of its lines. A PO line may appear more than
once in one receipt (each with its own lot or location). Needs **both** `procurement.receipt.write`
and `inventory.stock.receive` (ORG-floored).

### The decision, per line

The quantity entered (`quantity` in `uom`, default the item's stock unit) is converted to the stock
unit exactly (below). With `ordered` (the PO line's quantity in the stock unit), `prior` (what
earlier receipts took) and the item's tolerance in basis points:

```
limit      = ordered + floor(ordered * tolerance_bp / 10_000)
cumulative = prior + received
cumulative > limit                      -> 422 over_receipt (nothing is written)
cumulative == ordered                   -> EXACT
cumulative >  ordered (<= limit)        -> OVER
cumulative <  ordered, close_short      -> SHORT   (the line stops holding the PO open)
cumulative <  ordered                   -> PARTIAL (more is expected)
```

All integer arithmetic: tolerance is stored as integer basis points (`inventory_items.
receipt_tolerance_bp`, wire `receipt_tolerance_pct` as a string with at most two decimals), the limit
is floored so a tolerance never allows a single milli-unit more than it says, and 5 % of 82.5 m is
exactly 4.125 m. The default tolerance is 0: exactly the ordered quantity.

`close_short` with `"0"` closes a line without receiving anything. A closed line takes no further
receipt (409). `close_short` on a line that is already complete is a 422.

Every receipt line snapshots `ordered`, `prior`, `cumulative`, the tolerance and the outcome, so
the audit of an over- or short-receipt never depends on what the item or PO says later.

### Purchase-order status

After every receipt the PO's status is recomputed from all its lines (`domain.po_status_after`):
every line satisfied (received at least the ordered quantity, or closed short) -> `received`;
otherwise anything received or closed short -> `partially_received`; otherwise unchanged. The edges
are in `PO_TRANSITIONS`: `approved -> partially_received | received | cancelled`,
`partially_received -> received` (a partially received PO cannot be cancelled: stock already
arrived against it; close the remaining lines short instead). Becoming `received` stamps
`received_date`, decrements the supplier's open-PO count once, and emits the existing
`purchase_order.received` event (now from whichever path completes the order).
`PurchaseOrderLine.received_milli` is the exact cumulative figure; `received_qty` stays as its
whole-unit view (`received_milli // 1000`); the PO line payload carries both
(`received_exact` is a decimal string).

### Unit conversion (the deferred `uom_conversions` work)

`1 box = 12 pcs` is a row in `uom_conversions` (`numerator/denominator`, kept in lowest terms), per
item, managed by `GET/POST /operations/inventory/{item_id}/uom-conversions` and `PATCH/DELETE
/operations/inventory/uom-conversions/{id}` (`inventory.item.read/write`). Conversion order
(`domain.to_stock_milli`): same unit -> identity; an item ratio -> `milli * num / den` through
`Fraction`; else a fixed registry conversion within one dimension (`cm` -> `m`); else **refused**
(422). A result that is not a whole milli-unit — or, for a discrete stock unit like `pcs`, a whole
unit — is refused, never rounded. `3 box` of a 12-piece item is `36 pcs`; `0.5 box` is refused
because a box is a discrete unit and `0.1 box` would be 1.2 pieces.

The same conversion expresses a PO line ordered in boxes in stock units (so `ordered` above is in
pieces), and the line's price per box becomes a price per piece rounded half-to-even once to a
minor unit (`unit_cost_per_stock_unit`). A unit with a fixed conversion to the stock unit is refused
a row (two sources for one fact). A row cannot be deleted while an open PO orders in that unit, nor
its ratio changed once such a PO has received against it. A posted receipt stores both what was
entered and what it converted to, so changing a ratio later does not rewrite history.

### Lots, rolls, quality hold, location

* **Lot-tracked item**: stock goes into a lot — an existing one (`lot: {lot_id}`; it must be the
  same item's) or a new one from the line's `lot` fields (`lot_no` generated if omitted,
  `supplier_lot_no`, `manufactured_on`, `expires_on` — required on a new lot when the item tracks
  expiry). A lot-tracked item with no `lot` gets a generated lot. The lot's source is
  `goods_receipt`/<receipt id>; `received_on` is the receipt date.
* **Roll-tracked item**: `rolls[]` is required (`primary_qty` in the stock unit, optional measured
  `secondary_qty`/`secondary_uom`, width, GSM, shade, grade) and must add up to the line's quantity
  **exactly** (422 `rolls_do_not_add_up`). Each roll is its own bucket and ledger line.
* **Quality hold**: an item with `track_quality` — or any line with `hold: true` — is received into
  `QUALITY_HOLD`, not `AVAILABLE`, and its new lot is `PENDING`, not `APPROVED`. Releasing it is
  Phase 4.
* **Location**: per line (`location_id`), defaulting to the item's home bin; another tenant's
  location is a 404. A tracking field sent for an item that does not track it is a 422 `not_tracked`.
* **Service lines** (a PO line whose SKU is not a stocked item): accounted against the PO line,
  no stock, no location, lot or rolls (422 `not_stocked` if sent).
* Two lines landing in one bucket are merged into one move line (the kernel allows each bucket once
  per move): quantities add and the cost is the quantity-weighted mean, half-to-even.

### Idempotency, concurrency, atomicity

* The header (`goods_receipts`) carries the client's key under a unique index and is inserted
  `ON CONFLICT DO NOTHING` **before** any lot, roll or ledger row. Two simultaneous requests with
  one key cannot both post: the loser waits for the winner's commit and replays it (200,
  `replayed: true`). The same key with a different body is a 409 `idempotency_key_reused`.
* **Lock order**: the PO row, then its lines (by id), then the items (by id), then the kernel's
  bucket locks. Two receipts against one PO line serialise on the PO; the second reads the first's
  cumulative figure (the locked reads use `populate_existing`, so a row the route had already loaded
  is re-read under the lock). Tests: 10 simultaneous receipts of 15 against 100 ordered -> exactly 6
  post; four of 30 against 100 with 5 % tolerance -> exactly 3.
* One transaction: a refused line (422/409) writes nothing — no receipt, lot, roll or ledger row.
* `goods_receipts` and `goods_receipt_lines` are append-only (`UPDATE`/`DELETE` revoked, re-revoked
  in `migrations/env.py` after every grant re-application). Corrections are a later document.

## The single-shot receive is a wrapper

`POST /operations/purchase-orders/{id}/receive` still exists with the same permission (`purchase_order.
write`), the same answers (a draft is refused with the same message, `unmatched_skus` lists service
lines) and the same legacy `purchase_order.received` event. Underneath it is one goods receipt of
**every line's outstanding quantity** (`receipts.receive_whole_purchase_order`); a lot-/roll-tracked
item gets a generated lot and one roll of the whole quantity, and a quality-tracked item is received
into `QUALITY_HOLD`. `receive_into_home` is gone. It does not require the new receiving permissions —
see risks in the Phase 2 report; the permission model is unchanged for existing clients.

## Permissions (operations catalogue)

| Code | Scope | Held by |
|---|---|---|
| `procurement.requisition.read` | OWN..ORG | OPERATIONS_ADMIN, OPERATIONS_MANAGER (ORG), BUYER (ORG), OPERATIONS_USER (OWN) |
| `procurement.requisition.write` | OWN..ORG | as above (raise, edit, submit, cancel; convert also needs `purchase_order.write`) |
| `procurement.requisition.approve` | ORG | OPERATIONS_ADMIN, OPERATIONS_MANAGER |
| `procurement.receipt.read` | ORG | ADMIN, MANAGER, BUYER, OPERATIONS_WORKER |
| `procurement.receipt.write` | ORG | ADMIN, MANAGER, OPERATIONS_WORKER |
| `inventory.stock.receive` | ORG | ADMIN, MANAGER, OPERATIONS_WORKER |

New role `BUYER`: raises/edits POs and requisitions org-wide, keeps suppliers, reads receipts and
stock; approves nothing and posts no receipts. `OPERATIONS_WORKER` gains goods-in (reads POs, posts
receipts). Rollout to live tenants per ADR-0007 is in `infrastructure/edge/RUNBOOK.md` (Inventory
Phase 2).

## Events (contracts in `libs/contracts/events/`)

`operations.stock.received` (one per receipt: lines with item, location, status, lot, roll ids,
quantities, outcome, `move_id`), `operations.procurement.purchase_requisition.created | submitted |
approved | rejected | converted | cancelled`. The `purchase_order.received` contract gained an optional
`quantity_exact` beside the whole-unit `quantity`. The kernel's own `operations.inventory.stock.posted`
still fires for the move; lot and roll creation emit `lot.created` / `roll.created`.

## API

| Route | Permission |
|---|---|
| `GET/POST /operations/purchase-requisitions`, `GET/PATCH .../{id}` | `requisition.read` / `.write` |
| `POST .../{id}/submit`, `/cancel` | `requisition.write` |
| `POST .../{id}/approve`, `/reject` | `requisition.approve` |
| `POST .../{id}/convert` | `requisition.write` + `purchase_order.write` |
| `GET /operations/purchase-orders/{id}/receiving` | `receipt.read` |
| `POST /operations/goods-receipts` | `receipt.write` + `stock.receive`, `Idempotency-Key` |
| `GET /operations/goods-receipts`, `GET .../{id}` | `receipt.read` (cursor-paginated list) |
| `GET/POST /operations/inventory/{item_id}/uom-conversions`, `PATCH/DELETE /operations/inventory/uom-conversions/{id}` | `inventory.item.read` / `.write` |

Every route has a negative-authz test (none, all-but-the-required, an OWN grant where ORG is the
floor) and a cross-tenant 404 (or empty page) — `tests/test_receiving_authz.py`, driven by one
table that also asserts every route the phase adds has a row.

## Not in this slice

* Purchase-order quantities are whole numbers of units (an `Integer` column and `amount = quantity *
  unit_price`): fabric ordered as 82.5 m cannot be a PO line yet, and a requisition with a fractional
  quantity cannot be converted. Changing that is a PO-line migration with money implications.
* Receiving without a PO, returns to vendor, rejection/scrap of held stock (Phase 4), inspection
  release from `QUALITY_HOLD` (Phase 4), landed cost (Phase 8), GRN attachments (document service).
* Reversing a receipt: a correction is a later document (adjustment, Phase 3).
