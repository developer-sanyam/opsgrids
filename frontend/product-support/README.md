# product-support

The standalone **Support** product app (ADR-0001). Vite + React + TypeScript + Tailwind CSS v4.
Visual language is the shared "Console" system: Manrope, a single violet accent (`#6C4CF1`) spent
sparingly, big-radius cards on a light-grey ground, a grouped left sidebar with an account switcher
and ⌘K search, and a full light/dark theme (toggle in the sidebar). Semantic colour — priority,
status and SLA health — is kept strictly separate from the accent and is always paired with a label.

Product line: **OpsGrids Support** — one queue for customers and staff, with SLAs that mean something.

## Run it

```bash
pnpm --filter @opsgrids/product-support dev        # http://localhost:4340
pnpm --filter @opsgrids/product-support build
pnpm --filter @opsgrids/product-support typecheck
pnpm --filter @opsgrids/product-support lint
```

Dev serves on **:4340** (`--strictPort`), clear of the marketing site (4300) and product-hrms (4310).

## Screens

- **Overview** — four KPI cards (Open tickets, Breaching SLA, First-response time, CSAT), each with
  an inline sparkline and a week-over-week delta pill; down-is-good KPIs (breaches, response time)
  invert the delta colour while the arrow still shows the real direction. Below: a ticket-volume
  chart (opened vs solved, last 7 days), breakdowns **by priority** and **by status**, and a
  **Needs attention** list — open tickets that are breached or due within the hour, most urgent first.
- **Queue** — the ticket table (subject + id, requester, priority, status, assignee, SLA and
  updated) with a full-text search, status / priority / assignee filters, three sort orders, and
  row drill-down. SLA state is always **labelled** ("Breached 2h ago", "Due in 28m", "Paused"),
  never conveyed by colour alone.
- **Ticket detail** — the conversation thread (customer, agent and system messages, with internal
  notes clearly set apart), a properties sidebar (requester, channel, priority, status, assignee,
  SLA, tags, timestamps), and a working reply box with an internal-note toggle.
- **SLA & Agents** — a team summary, the agents table (open load, average response, solved this
  week, CSAT), and the active SLA policy with its per-priority first-response and resolution targets.
- **Workspace** — Customers, Topics & Tags, Reports, Integrations, Team Members, Settings and Help
  are routed "coming soon" placeholders reachable from the sidebar.

## Auth

Sign-in lives in [`src/auth/`](src/auth). `AuthProvider` owns the session; `useAuth` reads it;
`RequireAuth` gates the app and bounces to `/login` when there is no token. The access token is held
in memory and mirrored to `sessionStorage` (never `localStorage`); the refresh token is an httpOnly
cookie. In fixtures mode the app renders with a stand-in agent identity and no login.

## Data — fixtures-first

The typed client lives in [`src/api/`](src/api). The support-service is a skeleton on this branch,
so the app is **fixtures-first**: everything renders from [`fixtures.ts`](src/api/fixtures.ts) with
no backend.

```ts
// src/api/client.ts
export const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES !== "false";
```

Each client function already keeps the shape of the live call it will make later — against the
same-origin `/api/v1/support/...` prefix that Vite proxies to the gateway (`server.proxy` in
`vite.config.ts`) — so wiring the real backend is a flag flip (`VITE_USE_FIXTURES=false`). SLA state
is derived against a fixed "now" (2026-08-24T14:30Z, see [`lib/sla.ts`](src/lib/sla.ts)) so the
countdowns render deterministically.

## Components

A local set under [`src/ui/`](src/ui): `Button`, `Pill`, `Card`, `Avatar`, `Tag`, `SearchInput`,
`KpiCard`, `Sparkline`, `DeltaPill`, `PageHeader`, `DataTable`, `PriorityPill` / `TicketStatusPill`,
`SlaPill`, `TicketVolumeChart`, `CategoryBreakdown`, `Sidebar`, `ThemeToggle`, and shared
`Skeleton` / `EmptyState` / `ErrorState` / `SectionHeader`. Charts are hand-rolled — no charting
dependency.

> **Follow-up:** these should **graduate to the shared `opsgrids-ui` package** — ADR-0001 forbids an
> app forking shared components long-term. They live here only for this first product slice.

## Deferred (next slices)

- **Support service** — wire live tickets, agents and the SLA policy so the app drops fixtures.
- **`opsgrids-ui` graduation** — move `src/ui/*` into the shared design-system package.
- **Customers, Topics, Reports** — currently routed placeholders.
