# product-marketing

The standalone **Marketing** product app (ADR-0001) — campaigns measured against revenue rather
than clicks. Vite + React + TypeScript + Tailwind CSS v4. Shares the suite-wide "Console" visual
language: Manrope, a single violet accent (`#6C4CF1`) spent sparingly, big-radius white cards on a
light-grey ground, a grouped left sidebar with an account switcher and ⌘K search, and a full
light/dark theme (toggle in the sidebar). The semantic palette doubles as the channel palette
(Email / Social / Search / Events) in charts — never the accent.

## Run it

```bash
pnpm --filter @atmora/product-marketing dev        # http://localhost:4340
pnpm --filter @atmora/product-marketing build
pnpm --filter @atmora/product-marketing typecheck
pnpm --filter @atmora/product-marketing lint
```

Dev serves on **:4340** (`--strictPort`), clear of the marketing website (4300) and product-hrms
(4310). In dev, Vite proxies `/api/*` to the gateway on **:8100** so the browser makes only
same-origin calls and CORS never enters the picture.

## Screens

- **Overview** — four KPI cards (Revenue attributed, Pipeline generated, Cost per lead, ROAS) with
  sparklines and good-vs-bad delta pills, a revenue-vs-spend area/line chart, a by-channel revenue
  breakdown, and a "needs attention" list of live campaigns below the 2× ROAS floor.
- **Campaigns** — a sortable, filterable table (name + channel, status, budget vs spent, leads,
  revenue, ROAS) with search, channel and status filters, and click-through to each campaign.
- **Campaign detail** — metric tiles (revenue, pipeline, leads, deals, ROAS, cost per lead), a
  weekly revenue-vs-spend chart, budget/goal, and the audience it targets with a used-by cross-link.
- **Audiences** — segment cards with size, criteria, campaigns-used-by and revenue influenced,
  cross-linking to the campaigns that target each segment.
- **Channels** — blended totals plus a per-channel table (spend, leads, CAC, revenue, ROAS).
- **Insights / Workspace** — Reports, Automations, Integrations, Settings and Help are routed
  "coming soon" placeholders reachable from the sidebar.

## Data — fixtures-first

The typed client lives in [`src/api/`](src/api). The marketing service is still a skeleton, so the
app renders entirely from [`src/api/fixtures.ts`](src/api/fixtures.ts) by default — 14 campaigns
across four channels and six audiences, with money as integer minor units (paise). All ratios
(ROAS, cost-per-lead, CAC) are derived once in [`src/data/derive.ts`](src/data/derive.ts).

```ts
// src/api/client.ts — fixtures unless explicitly pointed at the live gateway
export const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES !== "false";
```

Set `VITE_USE_FIXTURES=false` once the service ships; every function then calls the gateway at the
same-origin `/api/v1/marketing/*` prefix with the session bearer token — wiring to live data is a
single flag flip.

## Auth

Sign-in lives in [`src/auth/`](src/auth): `AuthProvider` owns the session, `useAuth` reads it, and
`RequireAuth` gates the app. The access token is held **in memory** and mirrored to
`sessionStorage`, never `localStorage`; the refresh token is an httpOnly cookie. In fixtures mode a
stand-in identity bypasses the login screen so the whole app renders offline.

## Components

A local set under [`src/ui/`](src/ui): `Button`, `Pill`, `Card`, `Tag`, `Avatar`, `SearchInput`,
`PageHeader`, `DataTable`, `KpiCard`, `DeltaPill`, `Sparkline`, `CampaignStatusPill`,
`ChannelBadge`, `BudgetBar`, `StatTile`, `RevenueSpendChart`, `ChannelBreakdown`, `NeedsAttention`,
`CampaignPerformanceChart`, and shared `Skeleton` / `EmptyState` / `ErrorState` / `SectionHeader`.
Charts are hand-rolled inline SVG — no charting dependency.

> **Follow-up:** these should **graduate to the shared `atmora-ui` package** — ADR-0001 forbids an
> app forking shared components long-term. They live here only for this first product slice.
