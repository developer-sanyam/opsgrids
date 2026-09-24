# @opsgrids/opsgrids-internal — operator console

The vendor's cross-tenant control panel (ADR-0001 `opsgrids-internal`, ADR-0003 §2). It talks **only**
to `admin-gateway` on the **operator** audience — never a product SPA — and reads commercial data
across every customer while driving the customer lifecycle.

**Fixtures-first.** `VITE_USE_FIXTURES` defaults on, so the whole console renders from
`src/api/fixtures/` with no gateway while the console backend (ADR-0004 P6b) is built. Set
`VITE_USE_FIXTURES=false` to call the documented `/api/v1/admin/*` routes; the client keeps the same
shapes, so the switch is a flag, not a rewrite.

## Screens

- **Overview** — MRR + trend, active accounts, trials, trial→paid conversion, churn, locked/suspended.
- **Accounts** — cross-tenant directory with status, MRR and drill-down.
- **Account detail** — subscriptions, entitlements, usage, invoices, credit ledger, per-product
  client pricing (set a price, apply it now or from the next period, price history), and the
  operator actions (suspend / reactivate, comp grant).
- **Subscriptions** — every subscription across tenants with lifecycle state and next renewal.
- **Plans & Pricing** — the immutable catalog, and creating an audience-of-one bespoke price.
- **Grants & Credits** — the credit-ledger view: who's on comp access and when it lapses.
- **Audit** — the operator action log.

## Commands

```bash
pnpm --filter @opsgrids/opsgrids-internal dev        # http://localhost:4330
pnpm --filter @opsgrids/opsgrids-internal typecheck
pnpm --filter @opsgrids/opsgrids-internal lint
pnpm --filter @opsgrids/opsgrids-internal build
```

Shares the violet design family with the product apps (Manrope, accent `#6C4CF1`), tuned for a dense
control panel: a monospaced face for identifiers and money, lifecycle status families, and an
"Internal" wordmark so staff never confuse it with a customer product.
