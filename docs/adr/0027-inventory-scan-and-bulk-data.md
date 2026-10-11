# ADR-0027 — Inventory scan, labels, and all-or-nothing bulk data

Status: accepted (2026-10-05). Inventory Phase 9. Amends [ADR-0022](0022-bulk-import-export.md)
for **inventory data** (items, locations, lots, rolls, opening stock); every other list keeps
ADR-0022 as written.

## Context

ADR-0022 made Excel import a **browser** job: the page reads the workbook and calls the existing
create endpoint once per row, with a per-row result (`created`, `skipped`, `failed`) and an
explicit "never all-or-nothing". That is right for a customer book or a supplier list, where a
half-loaded file is a to-do list and re-uploading finishes the job.

The inventory plan (`docs/inventory/plan.md`, Phase 9) asks for the opposite for stock data:
*preview, row errors, duplicate detection, **all-or-nothing commit***. The reasons are specific to
inventory:

- **Opening stock moves the books.** A file that posts 400 of 500 lines and stops leaves a ledger
  that matches neither the spreadsheet nor the shelf, and "finish the rest" is no longer a safe
  retry: the lines that landed would be posted twice.
- **Order matters across rows and tables.** A zone before its bins, a lot before its rolls, an item
  before its stock. A per-row importer that fails row 3 produces rows 4..n that reference something
  that never got created, each failing for a reason that is a consequence, not a cause.
- **A half import is hard to see.** There is no "which rows are in" view for a ledger.

ADR-0022 §2 and §5 therefore cannot hold here: all-or-nothing needs one transaction, and one
transaction needs a server endpoint that sees the whole file. ADR-0022 §4's pacing (350 ms per row,
a 5000-row/30-minute run) is also unnecessary once the work is one request.

Phase 9 also adds scanning (items, lots, rolls, locations), labels, and mobile entry points.

## Decision

### 1. Inventory imports are one server request, one transaction

`POST /operations/inventory/data/import/{entity}` with `entity` in `items`, `locations`, `lots`,
`rolls`, `opening_stock`. The browser still reads the CSV/XLSX (ADR-0022 §2: parsing, formula
safety and file limits stay in the client), but sends **JSON rows of strings**; the server never
receives a file, so there is still no upload endpoint to attack.

The endpoint **is the existing create paths, called once per row, inside one transaction**:
`create_inventory_item`, `create_location`, `tracking.create_lot` / `create_roll`, and
`stock_ops.post_opening_stock`. Every rule, audit row and outbox event those paths already own
applies unchanged, and **stock only ever moves through `post_move`**. The import adds only the
file-shaped part: reading a row, naming its problems by row and column, detecting duplicates, and
refusing to commit unless every row is good.

Mechanics: each row runs in its own SAVEPOINT (so a bad row reports without poisoning the next, and
the whole file is checked in one pass); the rows as a whole run in an outer SAVEPOINT that is
**rolled back** unless `mode=commit` and no row failed. A **preview** is therefore exact — it ran
the real writes, then undid them — and a refused commit leaves no trace: no items, no ledger rows,
no audit rows, no outbox events.

### 2. Duplicates

- **In the file:** the same natural key twice (SKU, `warehouse/code`, lot number, roll number, an
  opening-stock bucket), or the same barcode twice, is always an error on the later row.
- **Against existing data:** an error by default (`on_duplicate=fail`). `on_duplicate=skip` leaves
  the existing record alone, reports it as skipped, and commits the rest. **Import creates, it
  never overwrites** (ADR-0022 §3 stands) — there is no update mode for inventory data.
- **Opening stock** is refused for any bucket that already holds stock (item x location x status
  x lot x roll). Opening stock exists to fill an empty bucket; re-importing a stock export must
  not double the books. (The export of `stock` is shaped like an opening-stock file on purpose and
  is refused wholesale — a test pins it.)

### 3. Idempotency and the record of what was imported

A commit needs an `Idempotency-Key`. `inventory_import_runs` (append-only: UPDATE and DELETE revoked
from the app role, RLS) holds one row per **committed** import: key, a hash of (entity, duplicate
policy, rows), counts, and the response, written in the same transaction. The same key with the
same file replays that response (`replayed: true`, nothing added); the same key with a different
file is a 409. A refused or previewed import writes nothing, so a corrected file commits under the
same key. The kernel moves carry derived keys too, belt and braces. Imports into one organization
serialize on a transaction-scoped advisory lock: two concurrent files would otherwise pass their
duplicate checks side by side and both commit.

### 4. Permissions: two new, neither a bypass

- `operations.inventory.data.import` — **dangerous** (one request creates thousands of records).
  Held by `OPERATIONS_ADMIN` through `all()`; nobody else by default.
- `operations.inventory.data.export` — held additionally by `OPERATIONS_MANAGER`.

Both are ORG-only. Neither grants more than the entity's own right: an import additionally requires
the entity's write permission (`item.write`, `location.write`, `lot.write`, `roll.write`,
`stock.adjust`), an export additionally the list's read permission (`item.read`, `location.read`,
`stock.read`), each checked at ORG. Authorization stays server-side; the page hides what would only
be refused. Every export writes an audit row.

### 5. Exports are server-side JSON/CSV, and are not an update format

`GET /operations/inventory/data/export/{entity}` for `items`, `locations`, `lots`, `rolls`, `stock`
and `ledger`, built from the same statements the list endpoints use (so an export cannot show a
caller more than the list would), capped at 50,000 rows with a `truncated` flag. Import columns
come first, read-only columns (ids, derived quantities) after; an import rejects them as unknown
columns, which is ADR-0022's "an export edited and uploaded again must never add every record a
second time" restated for a server that checks. Quantities are exact decimal strings, money integer
minor units. In CSV a cell starting `=`, `+`, `-`, `@`, tab or CR is prefixed with an apostrophe
unless it is a plain decimal number.

### 6. Barcodes, labels and the scan payload

- **Payload `og:<type>:<code>`**, `type` one of `item`, `lot`, `roll`, `location`. `code` is the
  entity's printed code: its `barcode` when it has one, otherwise its natural key (SKU, lot number,
  roll number; a location's barcode). Printable ASCII only (Code 128 subset B), at most 64
  characters. The text before the first two colons is the prefix; the rest, colons included, is the
  code. A scan with no `og:` prefix is a **bare code** (a legacy location label, a manufacturer's
  EAN) and is looked up across every type. `libs/contracts/scan-payload-vectors.json` is the
  contract: the Python and TypeScript implementations are both tested against it.
- **Columns.** `locations.barcode` already existed. This ADR adds `barcode` to `inventory_items`,
  `lots` and `rolls` (nullable `String(64)`, unique per organization where set; items ignore
  soft-deleted rows like their SKU index). A `NULL` barcode means "scan the natural code", which is
  already unique, so nothing is backfilled. A supplier's lot label or a mill's roll label can now be
  the code that resolves.
- **Resolution** (`GET /operations/inventory/scan?code=<payload>`, `stock.read`, ORG-floored) looks a code
  up by barcode **or** natural key within the type, organization-filtered; another tenant's code is
  a 404, never a 403. If a code matches **two different records** — a barcode equal to another
  item's SKU, a lot numbered like a roll scanned bare — it answers 409 and tells the person to scan
  the printed label; it never guesses, because a wrong guess puts stock in the wrong bin.
- **The response is slim on purpose:** what the thing is, the exact on-hand quantity, and the ids a
  next step needs — no cost, price or supplier data, since scanning needs `stock.read` and not
  `item.read`. `allowed_actions` lists what this caller's permissions allow for this kind of record;
  it is a UI affordance, never an authorization (every action's endpoint checks its own).
- **Labels** are client-rendered (`/inventory/labels/:type`, its own route outside the shell, the
  pattern of a zone's location labels): a QR of the payload and a Code 128 of the plain code, because
  a handheld laser reads the bar code and a phone reads the QR. The Code 128 encoder is ours and is
  checked against an independent encoder for every printable character.

### 7. Mobile scan flow: reuse, and never a dead link

`/inventory/scan` is the warehouse floor's entry: large touch targets, a manual-entry field that is
always present and focused (a handheld scanner types into it), and an opt-in camera. The camera
uses the browser's `BarcodeDetector` (QR and Code 128) where it exists, and a JavaScript QR decoder
(`jsqr`, loaded only then) where it does not; the screen says which codes this device can read. A
blocked, missing or insecure camera says why and leaves manual entry working.

Actions reuse existing endpoints: moving stock (`stock/transfers`) and holding or releasing it
(`stock/status-changes`) post through `post_move` with an `Idempotency-Key`. Receive, pick, count and
inspect **link into the screens that own them** — and only when this build mounts that route: the
router registers every route it mounts and the scan screen asks that registry, so when a later phase
adds `quality/inspections` the Inspect button appears with no change here, and until then it is not
drawn. An action needs both the server's permission to offer it and a route to land on.

## Consequences

- Two import models now coexist: ADR-0022's per-row browser import for every other list, and this
  all-or-nothing server import for inventory data. The inventory screens' existing "Import / Export"
  buttons (ADR-0022 engine) are unchanged; the new page is `/inventory/import`. Folding the
  inventory entities' per-row definitions away is a follow-up, deliberately not done here.
- The gateway caps a request body at 2 MiB, which a 5000-row JSON import can exceed. Both route
  tables (`services/app-gateway/routes.json`, `infrastructure/edge/routes.prod.json`) carry a
  longer-prefix entry for `/api/v1/operations/inventory/data/import` with an 8 MiB cap, and
  nothing else under `/api/v1/operations` gets it (`tests/infra/test_inventory_import_route.py`).
- A server import is bounded at 5000 rows per request. It is one request, so a gateway or proxy
  timeout is the limit to watch on the largest files (see the runbook entry).
- `inventory_import_runs` grows by one row per committed import and is never rewritten.
- Adding a column to the import means adding it in `bulk_import.COLUMNS` and a handler; the template
  endpoint and the sample sheet are built from the same definition and cannot drift.

## Not in this phase

- **BOM-line import**: manufacturing routes and tables are Phase 5; the import entity is added when
  that phase lands on the same branch.
- **Updating existing inventory records from a sheet**, and a barcode field in the item form (set
  through import or the API today).
- **Receive against a GRN, quality inspection** screens: Phase 2 and Phase 4. The scan screen links
  to them when they exist.
