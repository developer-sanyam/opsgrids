# Billing service

Part of the Atmora platform. Owns the `billing` schema — the **financial record of truth**
(ADR-0004 §1, §3).

```bash
make run SERVICE=billing PORT=8160    # API
make worker SERVICE=billing           # event consumer
make relay SERVICE=billing            # outbox relay
```

## Owns

- **`invoices`** — one per organization, written straight to `finalized` and **frozen** from
  that moment. A `BEFORE UPDATE` trigger permits only the single legal transition
  `finalized → void`; `DELETE` is revoked. The money and lines a customer was billed against can
  never be silently re-priced (ADR-0000 §9).
- **`invoice_lines`** — append-only (`UPDATE`/`DELETE` revoked). The lines foot **exactly** to
  their invoice's `total_minor` (`platform_core.money.Money.allocate`, largest-remainder). Each
  keeps the `plan_version_id` it was priced against, so an invoice stays reconstructable years
  later (ADR-0004 §2). A correction is a new invoice, never an edited line.
- **`credit_ledger`** — append-only, **balance derived not stored**. A grant, a consumption, a
  reversal and an expiry are all rows; the balance available at an instant is a `SUM` over the
  rows whose `[valid_from, valid_until)` window covers it (ADR-0004 §3). A lapsed grant drops
  out of the balance on its own — nothing runs to expire it.
- **`tax_rates`** — global, un-policied platform reference data, versioned by `effective_from`.
  The table is modelled; jurisdiction resolution is deferred (ADR-0004 "Open: Tax").

Money is integer minor units with an ISO-4217 currency — **no floats, no `Numeric` on any money
column** (ADR-0000 §8). Isolation is row-level security on every tenant table, and the
append-only / freeze guarantees are enforced below the app role (privilege revokes + trigger),
not by trusting the application.

### Scope boundary (this PR)

Billing owns the invoice + credit record and all its money mechanics. It does **not** read
catalog or metering here: `finalize` takes **explicit line items**. Assembling priced lines from
catalog prices and metering usage — a "billing run" — is a deliberately deferred concern, left as
a typed seam (`app/domain.py::PricingSource`, with the `ExplicitPricingSource` implementation
that exists today). Nothing here imports catalog or metering.

## Publishes

- `commerce.invoice.finalized` — a finalized invoice was created.
- `commerce.invoice.voided` — a finalized invoice was voided.
- `commerce.credit.granted` — a credit-ledger grant (comp access / goodwill) was added.
- `commerce.credit.consumed` — credit was spent (a negative row).
- `commerce.credit.reversed` — an earlier ledger entry was reversed (a negating row).

## Consumes

_Nothing yet. The billing-run orchestration that consumes catalog + metering is deferred._

## HTTP surface

- Customer (`/api/v1/billing`, product gateway): `GET /invoices`, `GET /invoices/{id}`,
  `GET /credit-balance?currency=` (the windowed SUM). Reads only.
- Internal (`/internal/v1`, service token, excluded from OpenAPI):
  `POST /invoices:finalize`, `POST /invoices/{id}:void`, `POST /credit`,
  `POST /credit:consume`, `POST /credit:reverse`. Each binds the tenant from the request body.
