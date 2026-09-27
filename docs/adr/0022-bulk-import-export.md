# ADR-0022: Bulk import and export from Excel

Status: accepted (2026-09-26)

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
   Every other 4xx, and every 5xx, fails that row once with the server's sentence. The limit per file is **1000 rows** (about six minutes). A server-side
   bulk endpoint is the follow-up if that is too slow; it would replace only the entity's `create`
   step, not the dialog or the engine.
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
   masked on export (leads). Updating existing records from a sheet is out of scope.

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
- **Limits are enforced before anything is sent:** file up to 5 MB, `.xlsx` only, up to 1000 data
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
- **Updating existing records** from a sheet: no upsert exists. Follow-up if wanted.
- **CSV and legacy `.xls`**: `.xlsx` only in this release; the dialog tells the user to "Save as
  Excel Workbook".
- **Finance customers and Support agents**: follow-up (GSTIN checksum, ORG-scope floors).

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

- **Matching.** A row that carries a **Record ID** (the id an export writes) updates that record; an
  ID no record has is refused ("that Record ID is not in this list"). A row without one matches on the
  list's unique key where it has one (SKU, supplier/warehouse/category/project code, employee number,
  asset tag), ignoring case and spacing; no match means a new record; two records with that key means
  the row is refused. Lists with no usable key (leads, whose email is sealed) update by Record ID only.
- **A blank cell leaves the field alone.** `[clear]` empties a field, only where that field can be
  emptied and using the value the endpoint accepts (`""` or `null`, which differ per endpoint; the
  survey found `null` answered with a 409 or a 500 on several). A sheet with only some columns
  filled in can therefore never wipe data.
- **Only fields that differ are sent**, from a fresh load of the whole list (all pages, keyed by id,
  refused beyond 10 000 records like every reference list). A row that changes nothing makes no
  request, so it writes no audit row and no event (the update endpoints record both even for an
  unchanged PATCH). The preview shows, per row, whether it will create, update (and which columns) or
  change nothing.
- **Immutable fields are never sent** (SKU, codes, employee number, asset tag, a currency the list
  cannot change): a cell that disagrees with the stored value refuses the row rather than being
  silently ignored, since two of the services silently drop unknown keys and answer 200.
- **Special cases run as extra steps:** a deal's stage through its own stage endpoint (which resets
  the probability), a contact's phone through its write-only endpoint (a non-blank Phone cell is one
  call, because the sealed number cannot be compared), a lead's status only through legal transitions.
  Never updatable from a sheet: lead email and phone (no endpoint), employee PII, employee status and
  exit date, lead source, a lead's conversion.
- **A 409 is a failure in update mode**, never "skipped": "a company with that name already exists" on
  a rename is a collision the user must see.
- **Results** gain `updated` and `unchanged` beside created / skipped / failed / invalid.
- **One row per record.** A second row that resolves to a record an earlier row already updates is
  refused ("This row updates the same record as row N"), so the outcome never depends on row order.
- **A row found by its key names the record by that cell.** Its key cell is taken as the stored
  key, so a difference in capitals or spacing is not read as a rename or as a "different" immutable
  code. A rename needs the Record ID, which is what makes the row unambiguous.
- **A partial sheet is valid in update mode.** Required columns are enforced only for rows that
  create; a file with just Record ID and one column updates that column and nothing else. Add mode
  still requires every required column.
- **Every request is its own step.** A row that changes fields and then a stage or a phone is two
  requests, each spaced by the runner and each retried on its own after a 429. If a later step
  fails, the row is reported failed with "The earlier changes to this row were saved.", never as if
  nothing had happened.
- **What this cannot protect against**, because the endpoints have no version or ETag: a concurrent edit
  to the same field between the load and the send is overwritten (last writer wins, per field sent).
  The dialog says so, and loads the records as late as it can. A warehouse or supplier rename also
  rewrites the name on every item and purchase order that uses it, silently on the server, so an
  inventory sheet that names a warehouse should be run after, not before, a warehouse rename.

## Known limits and server follow-ups (found by the pre-release audits)

None blocks this release; each is a pre-existing server behaviour the import can hit sooner than a
person typing one record at a time.

- **List ordering is not unique** on several endpoints (companies/contacts/deals/suppliers/item
  categories/employees order by a name, not name plus id), so offset paging can repeat one row and
  skip another. The client copes (it de-duplicates by id, reads again, and refuses a list that is
  still short of the server's total, so a lookup is never silently partial) but the cure is an `id`
  tie-break in those selectors.
- **Case-insensitive uniqueness is missing** for contact and lead emails (leads match on a blind
  index of the exact string), deal names and warehouse names/category codes. The import keeps an
  email's local part as typed to match the server, and refuses a name or email that differs from an
  existing one only in capitals where it can see the existing records (contacts, warehouses,
  categories). Leads cannot be checked (their emails are masked on read).
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
