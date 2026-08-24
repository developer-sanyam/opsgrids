# product-crm

The standalone **CRM** product app — **Atmora Revenue** (ADR-0001). Vite + React + TypeScript +
Tailwind CSS v4. Visual language is the shared "Console" system used across every Atmora product:
Manrope, a single violet accent (`#6C4CF1`) spent sparingly, big-radius white cards on a light-grey
ground, a grouped left sidebar with an account switcher and ⌘K search, and a full light/dark theme
(toggle in the sidebar).

## Run it

```bash
pnpm --filter @atmora/product-crm dev        # http://localhost:4340
pnpm --filter @atmora/product-crm build
pnpm --filter @atmora/product-crm typecheck
pnpm --filter @atmora/product-crm lint
```

Dev serves on **:4340** (`--strictPort`), clear of the marketing site on 4300 and the HRMS product
on 4310. In dev, Vite proxies `/api/*` to the gateway on **:8100** (`server.proxy` in
`vite.config.ts`), so the browser makes only same-origin calls and CORS never enters the picture.

## Fixtures-first

The CRM service is a skeleton on this branch, so the app is **fixtures-first**: it renders the whole
experience from `src/api/fixtures.ts` with no gateway and no login (`USE_FIXTURES` defaults to
`true`). The typed client in `src/api/client.ts` already wires the live gateway paths
(`/api/v1/deals`, `/companies`, `/contacts`, `/activities`), so going live later is a single flag
flip: set `VITE_USE_FIXTURES=false`.

Money is stored and passed everywhere as an integer count of **minor units** (paise) plus an ISO
currency — `src/lib/format.ts` is the only place it becomes a human string.

## Screens

- **Overview** — four KPI cards (Pipeline value, Open deals, Win rate, Activities this week) with
  sparklines and delta pills; a pipeline-value-over-time area chart; a deals-by-stage funnel; a
  "needs attention" list (overdue, closing soon, going stale); and a recent-activity timeline. The
  KPI headlines, funnel and attention list are derived live from the deal list; the historical
  trend and the activities-this-week count are representative and carry a `SampleChip`.
- **Deals** — a kanban pipeline board (Lead → Qualified → Proposal → Negotiation → Closed Won /
  Closed Lost) of grab-styled, draggable-looking cards showing company, value, owner and close
  date, with a Board / List toggle, plus search, stage and owner filters.
- **Deal detail** — stage, value, owner, close date, probability and source, with the full activity
  timeline and the linked contacts.
- **Companies / Contacts** — cross-linked, searchable, filterable and sortable tables with status
  pills; companies drill into a detail page (deals, activity, contacts), contacts expand in place.
- **Activities** — a filterable timeline of calls, emails, meetings, notes and tasks.

## Structure

Mirrors `product-hrms`: `src/ui/*` (shared component set), `src/auth/*` (AuthProvider / RequireAuth
/ login, token in memory + sessionStorage only), `src/api/*` (typed client with a fixtures layer),
`src/data/*` (pure rollups, stage metadata and id resolution) and `src/routes/*` (the screens).
