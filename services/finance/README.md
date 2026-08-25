# Finance service

The accounts-receivable module of the Atmora platform, and the home of its GST compliance engine.
Owns the `finance` schema: customers, invoices and credit notes, invoice lines, payments and their
allocations, the GST rate schedule, the organization's GST registration, the e-invoice filing
ledger, the purchase book and the IMS reconciliation queue. Serves the `product-finance` SPA.

```bash
make run SERVICE=finance PORT=8171    # API
make worker SERVICE=finance           # event consumer — auto-files finalized documents
make relay SERVICE=finance            # outbox relay
```

## Scope

The finance SPA is read-only today (no create affordances), so most of the HTTP surface is the
reads it renders. The invoice and payment write path
(`services.create_customer/create_invoice/record_payment`) exists to finalize the append-only
documents and to drive the demo seed through the real, RLS-bound path.

Compliance is where the writes are. Two of them are automatic — filing an invoice and syncing a
supplier ledger — and two need a person: setting the organization's GST registration, and deciding
an inbound supplier document.

## Owns

- **`customers`** — the billed party (name, contact, GSTIN, city/state).
- **`invoices`** — a tax invoice or a credit note. Frozen at finalize; totals foot exactly
  (`subtotal = Σ line.amount`, `total = subtotal + tax`). A correction is a new `credit_note`
  document carrying `corrects_invoice_id`, never an edit. `amount_paid` is **derived** from the
  payment allocations, never stored, so the header cannot drift from the cash applied to it.
  Carries its own GST facts: place of supply, supply type, reverse charge, both GSTINs as
  snapshots, and the CGST/SGST/IGST split.
- **`invoice_lines`** — append-only; foot to the invoice subtotal; may be negative on a credit
  note. Each carries its HSN/SAC, its own rate, and its own share of the split.
- **`payments`** / **`payment_allocations`** — append-only; a payment splits across the invoices
  it settles.
- **`gst_registrations`** — the organization's own GSTIN, per state, one primary. Configuration,
  so mutable; what must not change is the GSTIN a filed invoice was filed under, which is a
  snapshot on the invoice rather than a join to here.
- **`einvoice_filings`** — one row per attempt to register a document with the IRP. **Append-only
  at the database**, by trigger.
- **`purchase_documents`** — what suppliers billed this organization; the counterpart an IMS
  document is matched against. Tax computed by `domain.split_gst`, never supplied.
- **`ims_documents`** — what suppliers *filed* against this organization, keyed on the portal's own
  row reference so a re-sync creates nothing and a genuine duplicate stays representable.
- **`ims_reconciliations`** — the computed suggestion per document, appended per run. Derived
  opinion, deliberately **not** frozen.
- **`ims_decisions`** — who accepted or rejected what, and when. **Append-only at the database**,
  by trigger, because this is the row that decides whether a credit is claimed.
- **`tax_rates`** — the GST rate schedule, versioned by `effective_from`. **Global / UNPOLICIED**
  (no `organization_id`): a rate band is platform vocabulary, the same for every tenant, not
  tenant data. Left out of row-level security alongside `processed_events`.

Money is integer minor units (paise) throughout; there are no floats and no `Numeric(_, 2)`.

## How a document gets filed

1. `create_invoice` finalizes a reportable document and stages
   `finance.compliance.invoice.filing_requested` **on the outbox, in the same transaction**. The
   relay sends it only after the invoice has committed.
2. `app/consumers.py` picks it up, binds the tenant GUC for the message, and calls
   `services.autofile_invoice`.
3. That asks `live_registration` **before** touching the portal — a document already carrying an
   IRN is never sent again — then files through `IrpClient` and appends exactly one attempt row.
4. Success persists the IRN, acknowledgement number and date, and the signed QR verbatim, and
   publishes `finance.compliance.invoice.filed`.
5. A rejection is terminal at once. An outage is retried on a capped exponential backoff up to
   `domain.MAX_FILING_ATTEMPTS`; when the budget is spent it becomes a terminal `failed` row
   carrying `retry_exhausted`, and the invoice read model reports `terminal: true`.

A registered IRN may be withdrawn within 24 hours of its acknowledgement. Past that the only
remedy is a credit note, and the read model says which of the two is available (`remedy`) and until
when (`cancellable_until`) rather than offering both.

## How a supplier document gets reconciled

`POST /internal/v1/ims/sync` fetches a return period through `ImsClient`, stores what is new
(idempotent on the portal's reference), then re-runs the reconciliation. Each document gets a
suggestion **with its reason** — `matched`, `amount_mismatch` (with the delta, signed, in paise),
`supplier_unknown`, `no_purchase_record`, `duplicate`. Nothing is auto-accepted: the status stays
`pending` until somebody records a decision, because accepting claims the input tax credit on
somebody else's invoice.

## Endpoints (`/api/v1/finance/...`)

| Method & path | Returns | Permission |
| --- | --- | --- |
| `GET /finance/invoices?q=&status=&sort=` | `Page<Invoice>` | `finance.billing.invoice.read` |
| `GET /finance/invoices/{id}` | `Invoice` | `finance.billing.invoice.read` |
| `GET /finance/invoices/{id}/filings` | `EInvoiceFiling[]` | `finance.compliance.einvoice.read` |
| `GET /finance/gst/registration` | `GstRegistration` | `finance.compliance.registration.read` |
| `PUT /finance/gst/registration` | `GstRegistration` | `finance.compliance.registration.write` |
| `GET /finance/ims/documents?q=&status=&supplier_gstin=&period=&reason=&sort=` | `Page<ImsDocument>` | `finance.compliance.ims.read` |
| `GET /finance/ims/documents/{id}` | `ImsDocumentDetail` | `finance.compliance.ims.read` |
| `POST /finance/ims/documents/{id}/decisions` | `ImsDecision` | `finance.compliance.ims.decide` |
| `GET /finance/payments` | `Page<Payment>` | `finance.billing.payment.read` |
| `GET /finance/customers?q=&sort=` | `Page<CustomerSummary>` | `finance.billing.customer.read` |
| `GET /finance/overview` | `OverviewSummary` | `finance.reports.summary.read` |
| `GET /finance/ledger` | `LedgerReport` | `finance.reports.ledger.read` |

Internal (service token, excluded from the published contract):
`POST /internal/v1/einvoice/filings`, `POST /internal/v1/einvoice/filings:cancel`,
`POST /internal/v1/ims/sync`.

`sort` for invoices: `issued_desc|issued_asc|amount_desc|amount_asc|due_asc`; for customers:
`receivables_desc|overdue_desc|name_asc`; for the IMS queue:
`date_asc|date_desc|amount_desc|amount_asc` (oldest first by default — a queue is worked forward).
The KPI spark-lines/deltas, six-month cashflow, cash balance and trial-balance accounts are
representative booked figures (see `app/domain.py`) that mirror the SPA fixtures — the seam a real
cash-ledger / GL integration replaces; the revenue, receivables, overdue and AR-aging figures are
derived live from the invoices and payments.

## Permissions and roles (`finance` product)

Finance is an **organization-level** function — a company's books have no per-user "own" reading —
so every permission defaults to ORG, tops out at ORG, and the routes floor at ORG.

- `finance.billing.invoice.read`, `finance.billing.payment.read`, `finance.billing.customer.read`
- `finance.reports.summary.read`, `finance.reports.ledger.read`
- `finance.compliance.einvoice.read`, `finance.compliance.registration.read`,
  `finance.compliance.ims.read`
- `finance.compliance.registration.write` and `finance.compliance.ims.decide` — the two writes,
  both marked **dangerous**, because each decides money: which identity every future invoice is
  filed under, and whether the credit on a supplier's invoice is claimed.

Role templates: **FINANCE_ADMIN** (the whole catalogue at ORG) and **FINANCE_VIEWER** (default;
every read, including the supplier queue, and neither write).

## The two stubbed portals

`IrpClient` and `ImsClient` are `Protocol`s; `StubIrpClient` and `StubImsClient` are the
implementations this repository ships. Both are real everywhere except the wire — the IRN is the
government's own hash, the refusals carry the portal's numbered codes, and every fixture's tax
comes from `domain.split_gst` — and neither opens a socket or reads a credential, which a test
asserts. Selecting a real NIC or GSP client is a wiring change in `app/deps.py`.

## Demo seed

`tools/demo/seed_finance.py` mirrors the SPA fixtures exactly (12 customers, 21 documents, 9
payments) into the "Atmora Demo" org via the real write path, so the live screens match the
approved fixtures. Idempotent. `DATABASE_URL` must point at `atmora_wt`.
