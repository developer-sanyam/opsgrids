# Finance service

The accounts-receivable module of the Atmora platform. Owns the `finance` schema: customers,
invoices and credit notes, invoice lines, payments and their allocations, and the GST rate
schedule. Serves the `product-finance` SPA.

```bash
make run SERVICE=finance PORT=8171    # API
make worker SERVICE=finance           # event consumer (none registered this slice)
make relay SERVICE=finance            # outbox relay
```

## Scope of this slice — reads first

The finance SPA is read-only today (no create affordances), so the HTTP surface is the reads it
renders. The write path (`services.create_customer/create_invoice/record_payment`) exists to
finalize the append-only documents and to drive the demo seed through the real, RLS-bound path;
it is not yet exposed as public routes.

## Owns

- **`customers`** — the billed party (name, contact, GSTIN, city/state).
- **`invoices`** — a tax invoice or a credit note. Frozen at finalize; totals foot exactly
  (`subtotal = Σ line.amount`, `total = subtotal + tax`). A correction is a new `credit_note`
  document carrying `corrects_invoice_id`, never an edit. `amount_paid` is **derived** from the
  payment allocations, never stored, so the header cannot drift from the cash applied to it.
- **`invoice_lines`** — append-only; foot to the invoice subtotal; may be negative on a credit note.
- **`payments`** / **`payment_allocations`** — append-only; a payment splits across the invoices
  it settles.
- **`tax_rates`** — the GST rate schedule, versioned by `effective_from`. **Global / UNPOLICIED**
  (no `organization_id`): a rate band is platform vocabulary, the same for every tenant, not
  tenant data. Left out of row-level security alongside `processed_events`.

Money is integer minor units (paise) throughout; there are no floats and no `Numeric(_, 2)`.

## Endpoints (`/api/v1/finance/...`)

Match the SPA's `client.ts` call shapes:

| Method & path | Returns | Permission |
| --- | --- | --- |
| `GET /finance/invoices?q=&status=&sort=` | `Page<Invoice>` | `finance.billing.invoice.read` |
| `GET /finance/invoices/{id}` | `Invoice` | `finance.billing.invoice.read` |
| `GET /finance/payments` | `Page<Payment>` | `finance.billing.payment.read` |
| `GET /finance/customers?q=&sort=` | `Page<CustomerSummary>` | `finance.billing.customer.read` |
| `GET /finance/overview` | `OverviewSummary` | `finance.reports.summary.read` |
| `GET /finance/ledger` | `LedgerReport` | `finance.reports.ledger.read` |

`sort` for invoices: `issued_desc|issued_asc|amount_desc|amount_asc|due_asc`; for customers:
`receivables_desc|overdue_desc|name_asc`. The KPI spark-lines/deltas, six-month cashflow, cash
balance and trial-balance accounts are representative booked figures (see `app/domain.py`) that
mirror the SPA fixtures — the seam a real cash-ledger / GL integration replaces; the revenue,
receivables, overdue and AR-aging figures are derived live from the invoices and payments.

## Permissions and roles (`finance` product)

Finance is an **organization-level** function — a company's books have no per-user "own" reading —
so every permission defaults to ORG, tops out at ORG, and the routes floor at ORG.

- `finance.billing.invoice.read`, `finance.billing.payment.read`, `finance.billing.customer.read`
- `finance.reports.summary.read`, `finance.reports.ledger.read`

Role templates: **FINANCE_ADMIN** (every read at ORG) and **FINANCE_VIEWER** (default; the same
reads — the write surface is not public yet).

## Demo seed

`tools/demo/seed_finance.py` mirrors the SPA fixtures exactly (12 customers, 20 documents, 9
payments) into the "Atmora Demo" org via the real write path, so the live screens match the
approved fixtures. Idempotent. `DATABASE_URL` must point at `atmora_wt`.
