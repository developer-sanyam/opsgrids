# product-operations

The standalone **Operations** product app (ADR-0001) — procurement, inventory and the paperwork
between them. Vite + React + TypeScript + Tailwind CSS v4. Visual language is the shared "Console"
system: Manrope, a single violet accent (`#6C4CF1`) spent sparingly, big-radius cards on a
light-grey ground, a grouped left sidebar with an account switcher and ⌘K search, and a full
light/dark theme (toggle in the sidebar).

## Run it

```bash
pnpm --filter @opsgrids/product-operations dev        # http://localhost:4380
pnpm --filter @opsgrids/product-operations build
pnpm --filter @opsgrids/product-operations typecheck
pnpm --filter @opsgrids/product-operations lint
```

Dev serves on **:4380** (`--strictPort`), clear of the marketing site (4300) and product-hrms
(4310). In dev, Vite proxies `/api/*` to the gateway on **:8100** (`server.proxy` in
`vite.config.ts`), so the browser makes only same-origin calls and CORS never enters the picture.

## Fixtures-first

The Operations backend is still a skeleton, so the app is **fixtures-first**: `USE_FIXTURES`
defaults **true** and the whole UI renders from `src/api/fixtures.ts` with a stand-in identity and
no login. Flip it once the service ships:

```ts
// src/api/client.ts
export const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES !== "false";
```

Set `VITE_USE_FIXTURES=false` and every client function falls through to the gateway at
`/api/v1/{suppliers,purchase-orders,inventory}` with the bearer token from the session — wiring the
live backend is a flag flip, not a rewrite. All money is carried as **integer minor units** (paise)
with an ISO currency and only becomes a display string in `src/lib/format.ts`.

## Screens

- **Overview** — a breadcrumb + title bar with search / period / New PO, a row of four KPI cards
  (Open POs, Inventory value, Low-stock items, On-time delivery %), then Spend by Category (live
  bars), a Spend Over Time area chart, and a PO Status donut, and finally recent purchase orders
  beside a **Needs Attention** queue (low stock + late deliveries) and a Top Suppliers list. Every
  figure derives from the purchase-order / inventory / supplier payloads.
- **Purchase Orders** — a sortable, searchable table (PO number, supplier, items, total, status,
  expected date) with status filter chips and row drill-down.
- **PO detail** — line items footing to a total, a delivery-status banner (on-time / overdue /
  received), supplier and warehouse meta, notes, and a **Mark as received** affordance.
- **Inventory** — a stock table (SKU, item, warehouse, on-hand, reorder point, value) with
  labelled low-stock / out-of-stock flags and a low-stock quick filter.
- **Suppliers** — a sortable table (name, category, lead time, on-time %, spend, status) with
  drill-down to a supplier detail page (stat tiles, contact card, and that supplier's POs).
- **Operations / Workspace** — Receiving, Warehouses, Reports, Integrations, Team Members, Settings
  and Help are routed "coming soon" placeholders reachable from the sidebar.

## Auth

Sign-in lives in [`src/auth/`](src/auth). `AuthProvider` owns the session; `useAuth` reads it;
`RequireAuth` gates the app and bounces to `/login` when there is no token. The access token is
held **in memory** and mirrored to `sessionStorage` (never `localStorage`); the refresh token is an
httpOnly cookie. In fixtures mode the guard is bypassed with a stand-in identity.

## Components

A local set under [`src/ui/`](src/ui): `Button`, `Pill`, `Card`, `KpiCard`, `Sparkline`,
`DeltaPill`, `PageHeader`, `DataTable` (sortable + drill-down), `PoStatusPill` / `SupplierStatusPill`
/ `StockPill`, `SpendTrend`, `CategoryBreakdown`, `StatusMix`, `NeedsAttention`, `TopSuppliers`,
`Avatar`, `Tag`, `SearchInput`, `Sidebar`, `ThemeToggle`, and shared `Skeleton` / `EmptyState` /
`ErrorState` / `SectionHeader`. Charts are hand-rolled inline SVG — no charting dependency.

> **Follow-up:** these should **graduate to the shared `opsgrids-ui` package** — ADR-0001 forbids an
> app forking shared components long-term. They live here only for this first product slice.

## Deferred (next slices)

- **Operations service** — wire live suppliers, purchase orders and inventory (flip `USE_FIXTURES`).
- **Receiving** — post real goods-receipt notes that decrement stock.
- **`opsgrids-ui` graduation** — move `src/ui/*` into the shared design-system package.
