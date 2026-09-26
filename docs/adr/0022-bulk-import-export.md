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
3. **Import creates, it never overwrites.** A row whose key already exists (HTTP 409) is reported as
   "skipped: already exists". There is no upsert: none of the create endpoints has one, and an
   accidental overwrite of a customer's data is worse than a skipped row. Re-uploading the same file
   is therefore safe and finishes the rows that failed.
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
   already lets them read. The columns are the import columns plus read-only ones (ids, timestamps),
   so the file can be edited and re-imported (read-only columns are ignored on import).

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
  rows, up to 60 columns. Cell text is trimmed and control characters are rejected.
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
