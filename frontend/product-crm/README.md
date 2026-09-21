# product-crm

The standalone **CRM** product app — **OpsGrids Revenue** (ADR-0001). Vite + React + TypeScript +
Tailwind CSS v4. Visual language is the shared "Console" system used across every OpsGrids product:
Manrope, a single violet accent (`#6C4CF1`) spent sparingly, big-radius white cards on a light-grey
ground, a grouped left sidebar with an account switcher and ⌘K search, and a full light/dark theme
(toggle in the sidebar).

## Run it

```bash
pnpm --filter @opsgrids/product-crm dev        # http://localhost:4340
pnpm --filter @opsgrids/product-crm build
pnpm --filter @opsgrids/product-crm typecheck
pnpm --filter @opsgrids/product-crm lint
```

Dev serves on **:4340** (`--strictPort`), clear of the marketing site on 4300 and the HRMS product
on 4310. In dev, Vite proxies `/api/*` to the gateway on **:8100** (`server.proxy` in
`vite.config.ts`), so the browser makes only same-origin calls and CORS never enters the picture.
Point the app at a live stack and sign in with real credentials — the app is **live by default**.

## Data

`src/api/client.ts` calls the real backend for this product: `services/customer`
(`/api/v1/customers`, `/api/v1/contacts`) and `services/deal` (`/api/v1/deals`,
`/api/v1/deals/activities`) — both mounted by the gateway with no `crm` product segment. A
"company" in this product is a `customer` row in the customer service; `src/api/mappers.ts` is the
one explicit, typed seam where that mapping happens (`src/api/wire.ts` carries the gateway's own
shapes). The rep directory is `GET /customers/owners`, not a standalone `/owners` endpoint — there
isn't one, and neither is there a standalone `/activities` endpoint; the timeline lives with the
pipeline at `/deals/activities`. `src/data/DirectoryProvider.tsx` loads the owner / company /
contact directory once per session and every screen that resolves a `*_id` to a display name reads
it through `useDirectory()`.

```ts
// src/api/client.ts
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";
```

Set `VITE_USE_FIXTURES=true` to render the whole app from `src/api/fixtures.ts` with no gateway and
no login — offline design work only; it is compiled out of a production build (`import.meta.env.DEV`
is always `false` there), so a stray env var can never ship fabricated data.

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
/ login, token in memory + sessionStorage only), `src/api/*` (typed client, the gateway's wire
shapes in `wire.ts`, the mappers in `mappers.ts`, and a fixtures layer), `src/data/*` (pure rollups,
stage metadata, id-resolution helpers and the `DirectoryProvider` that feeds them) and
`src/routes/*` (the screens).
