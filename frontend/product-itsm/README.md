# product-itsm — Atmora IT

The standalone **ITSM** product app: an IT service desk covering incidents, service requests
(the joiners-movers-leavers flows) and the asset CMDB. Vite · React 19 · TypeScript (strict) ·
Tailwind v4, on the shared Atmora violet "Console" design system.

## Screens

- **Overview** (`/`) — KPI cards (open incidents, SLA compliance, requests awaiting approval,
  assets under management) with sparklines and delta pills, an incident-volume-over-time chart,
  breakdowns by priority and category, and a "needs attention" queue of P1s and SLA breaches.
- **Incidents** (`/incidents`) — a searchable, filterable, sortable table (ID, summary, priority
  P1–P4, status, assignee, SLA state, updated) with row drill-down.
- **Incident detail** (`/incidents/:id`) — the activity / worklog thread plus a properties panel
  (priority, status, SLA clock, category, assignee, affected asset).
- **Service requests** (`/requests`, also `/catalog`) — the service catalog of request types over
  a requests table (requester, type, status: awaiting approval / in progress / fulfilled).
- **Assets (CMDB)** (`/assets`) and **asset detail** (`/assets/:tag`) — the configuration-item
  register with type / status / owner / warranty, drill-down and the incidents linked to each CI.

## Fixtures-first

The incident / request / asset services are still skeletons, so `USE_FIXTURES` defaults to
**true**: the whole app renders from `src/api/fixtures.ts` with no gateway and no login. Every
client function already targets the same-origin `/api/v1/...` prefix the gateway will serve, so
going live is a flag flip:

```sh
VITE_USE_FIXTURES=false pnpm dev
```

## Develop

```sh
pnpm install
pnpm dev         # http://localhost:4350
pnpm typecheck   # tsc --noEmit, strict
pnpm lint        # eslint
pnpm build       # tsc + vite build
```

Node ≥ 22.
