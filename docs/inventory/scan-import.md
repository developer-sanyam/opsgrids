# Scan, labels, import and export (Phase 9)

Decisions: [ADR-0027](../adr/0027-inventory-scan-and-bulk-data.md) (amends ADR-0022 for inventory
data). Migration `d9a4c6e1b3f7`. This is the reference; the ADR is the why.

## The label

Every label carries two codes of the **same** value:

| Code | Reads on | Carries |
|---|---|---|
| QR | phone cameras, every scanner | `og:<type>:<code>` |
| Code 128 | handheld lasers | the plain `<code>` |

`<type>` is `item`, `lot`, `roll` or `location`. `<code>` is the record's printed code: its
`barcode` if set, else SKU / lot number / roll number; a location's `barcode`. Printable ASCII, 64
characters at most. `og:roll:A:B:C` is a roll coded `A:B:C` (split at the first two colons only).

Print views: `/inventory/labels/item|lot|roll|location` (a warehouse is chosen for locations; a
location with no barcode has no label to print). Each view lists up to 100 records (searchable),
lets you untick, and prints through the browser. A record whose code is outside printable ASCII
is flagged instead of printed blank.

### Barcode columns

| Table | Column | Unique |
|---|---|---|
| `inventory_items` | `barcode` | per organization, live items only |
| `lots` | `barcode` | per organization |
| `rolls` | `barcode` | per organization |
| `locations` | `barcode` (existing) | per organization, live locations only |

Settable on create and patch (`barcode` on the item, lot and roll payloads; a duplicate is a 409
naming the field). `null` means "scan the natural code". A patch cannot clear a barcode
(`null` is ignored; send the new code).

## Resolving a scan

`GET /operations/inventory/scan/{payload}` — `operations.inventory.stock.read` (ORG).

```json
{
  "payload": "og:roll:R-1",
  "type": "roll",
  "entity": {"type": "roll", "id": "…", "code": "R-1", "title": "R-1",
             "subtitle": "COT-180 · LOT-A", "uom": "m", "on_hand_qty": "82.5",
             "inventory_item_id": "…", "sku": "COT-180", "lot_id": "…", "lot_no": "LOT-A",
             "roll_id": "…", "track_lot": true, "track_roll": true, "status": "ACTIVE"},
  "allowed_actions": ["view", "transfer", "status_change"]
}
```

| Status | Meaning |
|---|---|
| 200 | resolved |
| 404 | no such code in this organization (or in another one — never a 403) |
| 409 | the code matches two records (or two kinds of record): scan the printed label |
| 422 | not something a label could carry (`og:box:x`, non-ASCII, empty, > 64 chars) |
| 403 | no `stock.read` |

Lookup order: with a type, by `barcode` or natural key within that type; bare, across every type.
The entity carries no cost, price or supplier data.

`allowed_actions` — `view` always; then by permission and kind of record:

| Action | Item | Lot | Roll | Location | Needs |
|---|---|---|---|---|---|
| `transfer`, `status_change` | x | x | x | | `stock.transfer` |
| `issue` | x | | x | | `stock.adjust` |
| `receive` | x | | | x | `purchase_order.write` |
| `pick` | x | | | | `fulfilment.task.execute` |
| `count` | | | | x | `count.execute` |
| `inspect` | x | x | x | | `quality.inspection.inspect` (not yet in the catalogue) |

## The mobile screen (`/inventory/scan`)

1. **Scan or type.** The manual field is always on screen and focused; a handheld scanner types into
   it. The camera is opt-in ("Use camera"): `BarcodeDetector` (QR + Code 128) where the browser has
   it, else a JavaScript QR decoder. The screen says which. Blocked camera, no camera and an
   insecure origin each have their own message and leave typing working.
2. **See what it is** — title, kind, exact on-hand quantity.
3. **Do something.** Only actions that are both allowed by the server and buildable here:
   - *Move* — pick the bucket (item x location x status x lot x roll), scan or type the destination
     bin (same warehouse; another warehouse is refused with the reason), confirm the quantity
     (defaults to all that is free). `POST /operations/inventory/stock/transfers`, with an
     `Idempotency-Key`.
   - *Hold or release* — `POST /operations/inventory/stock/status-changes`, same shape.
   - *Receive* → `/receiving`, `/goods-receipts`, else `/purchase-orders`; *Pick* →
     `/fulfilment/tasks`; *Count* → `/counting`; *Inspect* → `/quality/inspections`; *Issue* →
     `/inventory/issue` or `/manufacturing/orders`; *Details* → the item or the location tree.
     Each appears **only if the router mounts that route** (the first candidate that exists wins).
     On this branch Receive, Pick and Count link; Inspect and Issue do not appear.

## Import

`POST /operations/inventory/data/import/{entity}` — `entity` in `items`, `locations`, `lots`,
`rolls`, `opening_stock`. Permissions: `operations.inventory.data.import` (dangerous) **and** the
entity's write permission (`item.write`, `location.write`, `lot.write`, `roll.write`,
`stock.adjust`), both at ORG. `GET …/{entity}/template` lists the columns.

```json
{"mode": "preview" | "commit", "on_duplicate": "fail" | "skip",
 "rows": [{"sku": "FAB-001", "name": "…", …}], "source_name": "items.csv"}
```

- Every cell is a string (a JSON number is a 422). A blank cell is `""` or absent.
- Up to 5000 rows; unknown column names are a 422 naming them.
- `preview` returns the per-row result and every error; it writes nothing (it runs the real writes
  and rolls them back). `commit` needs `Idempotency-Key`.
- A commit with any error answers **422** with every row error in `errors[]`
  (`{row, field, code, message}`; `row` is 1-based over the data rows) and commits **nothing**.
- A clean commit answers 200: `committed: true`, `run_id`, counts. The same key + same rows
  replays it (`replayed: true`); same key + different rows is a 409.

### Columns

| Entity | Required | Optional |
|---|---|---|
| `items` | `sku`, `name`, `category`, `warehouse`, `uom` | `reorder_point`, `unit_value_minor`, `currency`, `hsn_code`, `track_lot`, `track_roll`, `track_expiry`, `track_quality`, `is_active`, `item_category_code`, `barcode`, `selling_price_minor`, `selling_price_currency` |
| `locations` | `warehouse_code`, `kind`, `code` | `name` (default: the code), `parent_code` (earlier in the file is fine), `barcode`, `pick_sequence`, `storage_type`, `max_weight_grams`, `max_volume_milli` |
| `lots` | `sku` | `lot_no` (generated), `supplier_lot_no`, `manufactured_on`, `received_on`, `expires_on` (YYYY-MM-DD), `quality_status`, `barcode` |
| `rolls` | `sku`, `primary_qty` | `roll_no` (generated), `lot_no`, `secondary_qty`, `secondary_uom`, `width_mm`, `gsm`, `shade`, `grade`, `barcode` |
| `opening_stock` | `sku`, `warehouse_code`, `location_code`, `quantity` | `lot_no`, `roll_no`, `status`, `unit_cost_minor`, `unit_cost_currency` |

Money is integer minor units (`12500`, never `125.00`); quantities are decimal strings with up to
three places (`82.5`; `1e3`, `-4`, `0` and `1.2345` are refused, never rounded). Cross-entity
references are by natural key (SKU, warehouse code, lot number), never by id.

### Row error codes

`required`, `invalid_integer`, `invalid_boolean`, `invalid_date`, `invalid_value` (the field's own
validation, sentence from the schema), `unknown_sku`, `unknown_warehouse`, `unknown_location`,
`unknown_lot`, `unknown_roll`, `unknown_parent`, `unknown_category`, `lot_item_mismatch`,
`roll_item_mismatch`, `roll_lot_mismatch`, `already_exists` (the record is there; an error unless
`on_duplicate=skip`; for opening stock, the bucket already holds stock), `duplicate_in_file`
(always an error), `duplicate` (a barcode already used by another record), `not_tracked`,
`kernel_refused` (a posting the stock kernel refused, with its sentence), `conflict`. A failure the
write path raises keeps that path's own field and code.

### What an import writes

For each created record: the same audit row and outbox event the single-record endpoint writes.
Opening stock goes through `stock_ops.post_opening_stock` — **one `opening` move per 100 lines**,
each through `post_move` (bucket locks, idempotency, reconciler-clean). One `inventory_import_runs`
row and one audit row (`inventory.import.committed`) and one event
(`operations.inventory.import.committed`) per committed import.

The browser reads the file (CSV, or the first sheet of an XLSX; a formula cell contributes its
cached result and is never evaluated; a date becomes `YYYY-MM-DD`) and shows the preview; **Import**
is enabled only for a preview with no errors. The sample sheet is built from the server's template,
so it cannot drift.

## Export

`GET /operations/inventory/data/export/{entity}[?format=csv]` — `entity` in `items`, `locations`,
`lots`, `rolls`, `stock`, `ledger`. `operations.inventory.data.export` **and** the list's read
permission (`item.read` / `location.read` / `stock.read`). Up to 50,000 rows (`truncated: true`
beyond). Import columns first, then read-only ones (`id`, `on_hand_qty`, …). CSV cells are never
formulas (apostrophe-prefixed unless a plain decimal). Writes an `inventory.data.exported` audit row.
The page `/inventory/import` downloads each as a CSV.

## Deferred

BOM-line import (needs Phase 5's tables); updating existing records from a sheet; a barcode field
in the item form; a goods-receipt and a quality-inspection screen for the scan flow to link to
(Phases 2 and 4); removing the per-row import definitions the inventory list screens still use.
