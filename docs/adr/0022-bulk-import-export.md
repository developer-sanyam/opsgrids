# ADR-0022: Bulk import and export from Excel

Status: accepted (2026-09-26). Amended for inventory data (items, locations, lots, rolls, opening
stock) by [ADR-0027](0027-inventory-scan-and-bulk-data.md): those imports are one server request,
all-or-nothing, not per row. Every other list is unchanged.

## Context

Customers arrive with their data in spreadsheets: an inventory list, a customer book, an employee
roster. Today each product has its own hand-rolled "Export selected" CSV (ten copies, no
formula-injection guard, current page only), and only Engage contacts and storefront postal codes
can be imported at all. Nobody can bulk-load companies, contacts, leads, deals, inventory items,
suppliers, warehouses, item categories, employees, assets or projects, and nobody can get a
starting sheet to fill in.

The owner asked for **one Import/Export button** on the list screens of the CRM, inventory and the
other tools, that reads an Excel sheet, and a **downloadable sample sheet**.

## Decision

1. **One button, one dialog, the same in every app.** `ImportExportButton` (in `@opsgrids/ui`) sits
   in the `PageHeader` actions of a list screen and opens a dialog with three things: *Download
   sample sheet*, *Import from Excel*, *Export to Excel*.
2. **The browser reads and writes the workbook; the server never sees the file.** The spreadsheet
   engine lives in `@opsgrids/client` (`src/dataexchange/`), loads `exceljs` on demand (about 250 KB
   gzip, only when someone opens the dialog), and hands the server ordinary JSON. The server keeps
   its existing validation, authorization, tenant scoping, audit rows and outbox events, because an
   import is *the existing create endpoint called once per row*. There is no new backend surface and
   no upload endpoint to attack.
3. **Import creates, it never overwrites.** A row the server refuses with a 409 that says the record
   already exists (or carries a `duplicate` field code) is reported as "skipped: already exists".
   Any other 409 (a warehouse with no default bin, an unknown pipeline stage) is a failure with the
   server's sentence, not a skip. There is no upsert: none of the create endpoints has one, and an
   accidental overwrite of a customer's data is worse than a skipped row. Re-uploading the same file
   is therefore safe and finishes the rows that failed, **except where the list has no natural key**
   (a lead without an email, a purchase order): there a re-upload adds the row again, which the
   entity's notes say.
4. **Each row is its own request, throttled.** The gateway allows 300 requests per user per minute,
   shared with everything else the user does. The runner sends one row at a time with a minimum
   interval of 350 ms (about 170 per minute), waits out a 429 (the window is 60 s) and retries that
   row up to three times, and can be cancelled. 429 is the only 4xx ever retried (the frontend rule
   is "never retry a 4xx" because a 403 or 422 does not fix itself; a 429 says *wait*, not *no*).
   Every other 4xx, and every 5xx, fails that row once with the server's sentence. The limit per
   file is **5000 rows** (about 30 minutes at that pace); it is there for the browser's sake
   (memory, main-thread parsing, a tab left open for the whole run), not the gateway's, since the
   pacing above already keeps one run comfortably under the per-user budget on its own. **A run
   that sends 300 requests or more — created, updated, skipped or failed; `unchanged` and
   `invalid` never reach the server — opens a 60-second cooldown** before another one can start,
   so a run after a run after a run does not spend every one of the account's shared requests a
   minute leaving nothing for anything else the person or a teammate does meanwhile. Reviewing a
   different file, downloading the sample sheet or the previous run's report, and closing the
   dialog are never blocked by it; only sending another run is. A server-side bulk endpoint is the
   follow-up if that is too slow; it would replace only the entity's `create` step, not the dialog
   or the engine.
5. **Per-row result, never all-or-nothing.** The outcome of every row is `created`, `skipped`,
   `failed` (the server refused it, with the server's own sentence) or `invalid` (caught before
   sending). The result can be downloaded as a workbook.
6. **The sample sheet is built from the same column definitions as the importer**, so it cannot
   drift: header row (required columns marked `*`), realistic example rows, an **Instructions**
   sheet (every column: required, format, allowed values, example, plus the entity's rules), and a
   **Lists** sheet holding the values that are valid *for this organisation right now* (owners,
   warehouses, categories, suppliers). Dropdowns are attached to enum and lookup columns. Example
   rows left in the file are recognised and skipped, not imported.
7. **Export is the whole filtered list, not the visible page.** It pages the list endpoint (size 200,
   up to 10 000 rows) with the screen's current filters, so it returns only what the caller's scope
   already lets them read. The columns are the import columns plus read-only ones (ids, timestamps,
   masked values), which an import ignores. An export is a report and a starting layout, not an
   update format: **a row that still carries its Record ID is refused on import**, with an
   explanation ("clear the Record ID to add it as a new record"). An export edited and uploaded
   again must never add every record a second time, above all for lists whose natural key is
   masked on export (leads). Updating them from a sheet is the opt-in update mode in the addendum below.

## Rules that are not style preferences

- **Never reveal masked data.** Export writes what the list endpoint returns: masked phone and
  email stay masked. It never calls a reveal or unmask endpoint. A masked column is `readOnly`.
- **Formula injection.** Every string is written as a string cell (never a formula). A cell whose
  text starts with `=`, `+`, `-` or `@` is written as text; reading a formula cell takes its cached
  result and never evaluates it.
- **Text stays text.** SKU, codes, employee numbers, GSTIN, HSN and phone numbers are read and
  written as text so Excel cannot drop a leading zero or a `+`. Phone numbers must carry a country
  code; the importer never guesses one.
- **Money is exact.** A money cell is a decimal in *major* units plus an ISO currency, converted to
  integer minor units with the currency's own exponent (JPY 0, KWD 3, default 2) by string
  arithmetic. No floats. The two hard-coded 2-decimal `parseMinor` helpers in the CRM and
  inventory apps are not used.
- **Dates** are ISO `YYYY-MM-DD` after reading; an Excel date serial is converted, never trusted as
  text.
- **Limits are enforced before anything is sent:** file up to 5 MB, `.xlsx` only, up to 5000 data
  rows, up to 60 columns. The workbook's ZIP directory is read *before* the spreadsheet library is
  even loaded and the file is refused if it would unpack to more than 40 MB, holds an entry over
  25 MB or one expanding more than 100:1, has more than 2000 entries, or uses ZIP64: a small file
  must not be able to exhaust the browser's memory. Cell text is trimmed; control characters are rejected, except that a
  column marked `multiline` (a note, a summary) may hold a tab or a line break. A name or code with
  a hidden line break would become a different record from the one it looks like.
- **Authorization stays on the server.** The button hides Import for a user without the create
  permission and Export for a user without read, but that is a courtesy: a row the server refuses
  (403, for example an owner the caller may not assign) is reported as failed with the server's
  sentence.
- **Reference values are resolved by name, once, before the run** (owner email, company name,
  warehouse name, category code), from full lists (all pages), not from the 200-row directories the
  screens use. An unresolved name is an `invalid` row, not a request.

## Not in scope, and why

- **Purchase orders** (no natural key, so a re-import would duplicate them; header and lines),
  **invoices** (issuing stages a real IRP filing), **support tickets** (server-allocated numbers and
  SLA clocks): not importable.
- **Updating existing records** from a sheet: not in the first release (no upsert exists); added as an opt-in mode, see the addendum.
- **CSV and legacy `.xls`**: `.xlsx` only in this release; the dialog tells the user to "Save as
  Excel Workbook".
- **Support agents**: follow-up (ORG-scope floors). Finance customers were added on 2026-10-15
  (`feat/finance-customer-import`): GSTIN is the natural key (a duplicate is a 409 "already exists",
  so skipped); an unregistered name already on file is refused before sending; opening balances are
  not imported because Finance has no general ledger to post them to.

## Consequences

- One new runtime dependency, `exceljs` (MIT, moderate advisory in a transitive `uuid` that this
  usage cannot reach; the audit gate fails on high and critical).
- `@opsgrids/ui` stays presentational (no client import, no spreadsheet code); the app wires the
  hook from `@opsgrids/client` to the component.
- The hand-rolled CSV "Export selected" buttons stay for now (selection-scoped); they are a separate
  cleanup.

## Addendum (2026-09-27): update mode

The owner asked for bulk *update*. The dialog gains a choice, **Add new records only** (the default,
unchanged) or **Add new and update existing**, on lists whose entity declares an `update` spec.

- **Matching.** A row that carries the list's **id column** (the id an export writes, under the
  list's own header: "Company ID", "Deal ID"; every sentence says that header) updates that record;
  an id no record has is refused (it may be deleted, another organisation's, or outside the records
  the caller can see). A row without one matches on the list's unique key where it has one (SKU,
  supplier/warehouse/category/project code, employee number, asset tag, a deal's or company's name, a
  contact's email) **by the rule the server uses for that key** (`UpdateSpec.keyMatch`): `exact`
  (trim only, the default) for every key the server compares exactly, `caseless` (trim and ignore
  capitals, keep inner spaces) only for company names. Under `exact` a key stored exactly is found
  (so both `AB-1` and `ab-1` can exist and each row finds its own), two records with that exact key
  refuse the row, and a key that no record has exactly but that differs from a stored one only in
  capitals or spacing is refused with the stored spelling named, since it could be a new record or
  that one. No match means a new record. Lists with no usable key (leads, whose email is sealed)
  update by id alone, and the preview counts the rows that have no id and will be added as new.
- **A blank cell leaves the field alone.** `[clear]` empties a field, only where that field can be
  emptied and using the value the endpoint accepts (`""` or `null`, which differ per endpoint; the
  survey found `null` answered with a 409 or a 500 on several). A sheet with only some columns
  filled in can therefore never wipe data. `[clear]` is refused, with a sentence, when adding: it
  would otherwise create a record literally named "[clear]".
- **Only fields that differ are sent**, from a read of the whole list (all pages, keyed by id,
  refused beyond 10 000 records like every reference list). A row that changes nothing makes no
  request, so it writes no audit row and no event (the update endpoints record both even for an
  unchanged PATCH). Text is compared with the stored value trimmed and with one kind of line ending
  (a stored "Acme " is not a rename of an untouched "Acme"); a field may supply its own comparison.
  The preview shows, per row, whether it will create, update (and which columns) or change nothing,
  and which stored record it names ("Existing record", from `UpdateSpec.recordLabel`, never a sealed
  value), so an id column that slipped by one row is seen before anything is written. The Import
  button says the split ("Update 3, add 2").
- **The export carries a baseline, so an old file cannot put other people's changes back.** An export
  fills every cell, so a cell the user never touched still holds the value of the export; if a
  colleague changed that field afterwards, comparing it with the record as it is *now* reads it as an
  edit and sends it. An export of a list that can be updated by id therefore also writes a very hidden
  sheet (`OpsGrids baseline`: a marker, **format 2**, the list's id, the export time, a random
  per-file salt, the column keys, and per exported record its id in the clear and one cell of hashes,
  one per editable column). **The sheet holds no cell value**: a user who deletes a sensitive column,
  or its rows, before sharing the file leaves nothing of them behind in it. The import only ever asks
  whether a cell equals what was exported, which equality of hashes answers. A hash is SHA-256 (the
  browser's WebCrypto, no dependency) of the JSON `[salt, column key, record id, cell]`, cut to 80
  bits and written as 14 characters of base64. The column key and the record id are inside the hashed
  text, so equal values in different columns or records have different hashes, and the salt makes a
  hash from one file mean nothing in another. The cell is read the same way when written and when it
  comes back (dates, money, yes/no, text with leading zeros, line breaks, control characters
  stripped). Eighty bits because a baseline is a hash for *every* cell of *every* exported record and
  a file over 5 MB is refused: 10 000 records of 20 columns add about 2.3 MB to an export.
  In update mode a row's cell whose hash matches is untouched and is treated as blank *before
  anything else is judged*, so it is never sent, never looked up and never refused (an owner or
  warehouse label that has since become ambiguous does not refuse a row that did not change it). A
  cell that was edited behaves as before; `[clear]` never equals a baseline; a hash that was altered
  or a salt that was changed only makes cells count as edited. The reader takes the sheet behind the
  ZIP and size guards, hashes only the cells the file's rows hold (at most the import's row and column
  limits), and ignores the sheet, with a notice, when it is not this list's, is of another format
  (**a format-1 file, whose baseline held the values themselves, is not used**), has no salt, a column
  list that is not distinct names, a record without an id, or more records than an export writes. A
  record whose hashes are missing or malformed simply offers none. A file with no usable baseline (a
  sample sheet, a round trip through an app that drops the sheet, rows matched by key alone) is
  compared as it always was, and the preview says so. Hashing 10 000 x 20 cells takes about 1.2 s and
  runs in groups that yield to the page every 8 ms, so it never freezes it; `crypto.subtle` exists only
  over HTTPS or on localhost, and anywhere else an export is written without a baseline and a
  baseline in a file is ignored.
  The baseline is **security-neutral**: it can only make the import ignore a cell the user sees in
  their own sheet; it can never add a value, name a record, or widen what may be written. Every write
  is still authorised by the server. What it reveals beyond the record ids is that a guessed value
  can be confirmed: a salted hash cannot be reversed, but someone holding the file can test
  candidates, which is practical for a cell with few possible values (yes/no, a status, a small
  number) and not for free text. It shows no pattern (equal values hash differently) and no more
  than the cell would if the column had been kept.
- **The data is read again where it matters.** A file opened again is compared with a fresh read of
  the records, not the one an earlier file used (it may have been edited in Excel for an hour). When
  Import is pressed on records older than two minutes they are read again and the same file planned
  again: if every row still does what the user reviewed the import runs on the fresh plan; if any
  row's action or changes differ, the new preview is shown with a notice and nothing is sent.
- **Immutable fields are never sent** (SKU, codes, employee number, asset tag, a currency the list
  cannot change): a cell that disagrees with the stored value refuses the row rather than being
  silently ignored, since two of the services silently drop unknown keys and answer 200.
- **Special cases run as extra steps:** a deal's stage through its own stage endpoint (which resets
  the probability), a contact's phone through its write-only endpoint (a non-blank Phone cell is one
  call, because the sealed number cannot be compared).
  Never updatable from a sheet: lead email and phone (no endpoint), employee PII, employee status and
  exit date, lead source, a lead's conversion, and a lead's status: it is a read-only column, so an
  edit to it is ignored, not applied through the lead's transitions (there is no such step). A cell
  filled in a column an update refuses is refused; a read-only column an export fills is named on the
  Instructions sheet ("Ignored when updating: ...") and never read.
- **A 409 is a failure in update mode**, never "skipped": "a company with that name already exists" on
  a rename is a collision the user must see.
- **Results** gain `updated` and `unchanged` beside created / skipped / failed / invalid.
- **One row per record.** A second row that resolves to a record an earlier row already updates is
  refused ("This row updates the same record as row N"), so the outcome never depends on row order.
- **A row found by its key names the record by that cell.** Its key cell is taken as the stored
  key, so where the rule ignores capitals (company names) a difference in capitals is not read as a
  rename; a row whose only difference is that says so and sends nothing. A rename, or a change to
  only the capitals of a name, needs the id, which is what makes the row unambiguous. A row that names
  its record by id has its key cell compared with the same rule as any cell.
- **A partial sheet is valid in update mode.** Required columns are enforced only for rows that
  create; a file with just Record ID and one column updates that column and nothing else. Add mode
  still requires every required column.
- **Every request is its own step.** A row that changes fields and then a stage or a phone is two
  requests, each spaced by the runner and each retried on its own after a 429. If a later step
  fails, the row is reported failed with what was and was not saved ("Saved: Name, Owner. Not saved:
  Stage."), never as if nothing had happened.
- **What this cannot protect against**, because the endpoints have no version or ETag: a cell the user
  *deliberately edited* is written even if a colleague changed that field after the export (last
  writer wins, per field sent). The window starts at the **export**, not when the file is read; the
  baseline narrows what it can touch to the cells the user changed, and the dialog says so. A
  warehouse or supplier rename also rewrites the name on every item and purchase order that uses it,
  silently on the server, so an inventory sheet that names a warehouse should be run after, not
  before, a warehouse rename.

## Known limits and server follow-ups (found by the pre-release audits)

None blocks this release; each is a pre-existing server behaviour the import can hit sooner than a
person typing one record at a time.

- **List ordering is not unique** on several endpoints (companies/contacts/deals/suppliers/item
  categories/employees order by a name, not name plus id), so offset paging can repeat one row and
  skip another. The client copes (it de-duplicates by id, reads again, and refuses a list that is
  still short of the server's total, so a lookup is never silently partial) but not always: with more
  than 200 records and a name shared across a page boundary (the employees list is the likely one)
  the read fails every time ("changed while it was being read") and the list can be neither imported
  into nor updated. The cure is a backend follow-up: an `id` tie-break (`.order_by(name, id)`) in those
  selectors and in `platform_core`'s paging.
- **Case-insensitive uniqueness is missing** for contact and lead emails (leads match on a blind
  index of the exact string), deal names and warehouse names/category codes. The import keeps an
  email's local part as typed to match the server, and refuses a name or email that differs from an
  existing one only in capitals where it can see the existing records (contacts, warehouses,
  categories). Leads cannot be checked (their emails are masked on read).
- **Key case rules differ by list, and the import follows each.** Company names are compared without
  capitals by the server; deal names, SKUs, supplier/warehouse/category codes, employee numbers,
  asset tags and project codes are compared exactly, so `AB-1` and `ab-1` can both exist. An update
  matches each the way the server does (`keyMatch`), which is why it refuses, rather than matches, a
  key in other capitals. If the server later folds case for one of them, its spec must say
  `caseless` or the import will refuse rows the server would accept.
- **Files without a baseline** (a sample sheet, a file an editor saved without the sheet, rows matched
  by key with no id) get no protection from an old cell: every filled cell is compared with the
  record as it is now, and a cell copied from an old export puts its old value back. The preview says
  so. The same files meet a second limit: a lookup label (an owner, a warehouse, a location) that is
  ambiguous or has since disappeared refuses the row even where the cell did not change, since
  without a baseline the engine cannot tell it was untouched.
- **An employee reporting-line cycle** is refused by the server with an error that carries raw ids
  (UUIDs) rather than names; the import shows the server's sentence as it is.
- **Int and bigint columns without bounds:** `reorder_point` (int4, multiplied by 1000 in the
  low-stock query) and `team_size` are capped in the sheet; `opening_on_hand` times an unbounded unit
  value can overflow the default stock-value sort. These should be validated in the schemas.
- **The duplicate 409 is an existence oracle** across the organisation for a user limited to their
  own records (pre-existing; an import makes it faster to use) and the deal service does not verify
  `company_id` or `primary_contact_id` (the sheet does, the API does not).
- **Same-file references** (an employee's manager listed above their reports, a child category below
  its parent) are not resolved within one run: the manager or parent must already exist, so a first
  roster import needs one run per management level; re-running is safe because existing rows are
  skipped.
- **Reference lists are read whole, up to 10 000 records** (20 000 for the contact-email check). An
  organisation with more cannot import into a list that needs one (contacts, deals) in this release;
  the dialog says so instead of reading a partial list and refusing real records.
- **Very large workbooks** are parsed on the main thread. The ZIP guard bounds the work; parsing in
  a Web Worker with a timeout is the follow-up.
- **Money above 2^53 minor units** and non-2-decimal currencies in the ITSM and Projects apps' own
  forms were displayed with two decimals; those two apps now use the platform's exponent table.
