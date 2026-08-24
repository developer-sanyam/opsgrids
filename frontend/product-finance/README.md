# product-finance

**Atmora Finance** — invoices, payments and books that reconcile themselves.

A standalone Vite + React + TypeScript SPA for the Atmora suite. It reuses the shared violet
"Console" design system (Manrope, one violet accent, light/dark via `data-theme`) so it feels like
one product with the rest of the suite; only the screens and content are finance-specific.

## Screens

- **Overview** — KPI cards (revenue, outstanding receivables, overdue, cash) with sparklines and
  delta pills; a cashflow inflow/outflow area chart; an AR aging breakdown; and a "needs attention"
  queue of overdue invoices.
- **Invoices** — the append-only invoice register with search, status filter, sort and drill-down.
  Corrections are issued as **credit notes**, never edits.
- **Invoice detail** — line items that foot exactly to the total, the billed customer, payment
  status, and a record-payment affordance.
- **Payments** — received payments with method and their allocation to invoices.
- **Ledger & Reports** — a P&L / trial-balance summary with a CSV export.
- **Customers** — each account's receivable position and standing.

## Money

Every amount is an **integer number of minor units** (paise) in a single ISO currency (INR). No
floats, no `Numeric(_, 2)`; the app divides by 100 only for display, through `src/lib/money.ts`.
Figures render with tabular, monospaced digits so rupee columns rule up to the paisa.

## Running

Node ≥ 22.

```bash
pnpm install
pnpm dev        # http://localhost:4360
pnpm typecheck  # tsc --noEmit (strict)
pnpm lint
pnpm build
```

## Data

Fixtures-first: `finance-service` is a skeleton, so `USE_FIXTURES` defaults to **true** and the
whole app renders from `src/api/fixtures.ts` with no login. The typed client's live branches are
already wired to the gateway (`/api/v1/...`); set `VITE_USE_FIXTURES=false` to switch over once the
service ships.
