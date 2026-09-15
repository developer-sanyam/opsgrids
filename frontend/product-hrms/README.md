# product-hrms

The standalone **HRMS** product app (ADR-0001). Vite + React + TypeScript + Tailwind CSS v4.
Visual language is the "Console" system: Manrope, a single violet accent (`#6C4CF1`) spent
sparingly, big-radius white cards on a light-grey ground, a grouped left sidebar with an account
switcher and ⌘K search, and a full light/dark theme (toggle in the sidebar).

## Run it

```bash
pnpm --filter @opsgrids/product-hrms dev        # http://localhost:4310
pnpm --filter @opsgrids/product-hrms build
pnpm --filter @opsgrids/product-hrms typecheck
pnpm --filter @opsgrids/product-hrms lint
```

Dev serves on **:4310** (`--strictPort`), clear of the marketing site on 4300. In dev, Vite
proxies `/api/*` to the gateway on **:8100** (`server.proxy` in `vite.config.ts`), so the browser
makes only same-origin calls and CORS never enters the picture. Point the app at a live stack and
sign in with real credentials — the app is **live by default**.

## Screens

- **Dashboard** — a CRM-style layout: a breadcrumb + title bar with search / date / period /
  Add Employee, a row of four KPI cards (icon, sparkline, big value, delta pill), then Headcount
  by Department (live bars), a Headcount Trend area chart (sample) and a Workforce Mix donut
  (live), and finally the live Employees table (search / filter / sort / export + row selection)
  beside a Recent Activity timeline and Tasks Today checklist (both sample). Live widgets derive
  from `GET /api/v1/employees`; attendance/trend/activity/task widgets are sample and carry a
  `SampleChip`.
- **Employees** — directory with search (seeded from the `?q=` param), department filter chips,
  status filter, a responsive card grid, and "load more".
- **Attendance** — date picker + week strip, present/late/absent summary tiles, the day's punch
  table (check-in/out, hours, source, status), and a Field-punches panel.
- **Records / Workspace** — Notifications, Tasks, Departments, Locations, Reports, Integrations,
  Team Members, Settings and Help are routed "coming soon" placeholders reachable from the sidebar.

## Auth

Sign-in lives in [`src/auth/`](src/auth). `AuthProvider` owns the session; `useAuth` reads it;
`RequireAuth` gates the app and bounces to `/login` when there is no token.

- **Login** — `POST /api/v1/auth/login` `{ email, password }`. A `complete` result carries the
  access token; an `org_selection_required` result with one org auto-selects (via
  `POST /api/v1/auth/select-org`) and a picker renders when there is more than one.
- **Token** — held in memory and mirrored to `sessionStorage` (survives a tab reload), **never**
  `localStorage`. The refresh token is an httpOnly cookie the browser holds. Every gateway call
  carries `Authorization: Bearer …`; a `401` clears the token and returns to `/login`.
- **Identity** — `GET /api/v1/auth/me` populates the nav avatar (name, email, org) and the
  dashboard greeting. **Sign out** (avatar menu) clears the session.

## Data — live by default

The typed client lives in [`src/api/`](src/api). Every request goes to the same-origin `/api/v1`
prefix (proxied to the gateway in dev) through [`http.ts`](src/api/http.ts), which attaches the
bearer token and decodes RFC 7807 `problem+json` errors. `EmployeeOut` mirrors
`services/employee/app/schemas.py` field-for-field.

```ts
// src/api/client.ts
export const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES === "true";
```

- **Live (default)** — `listEmployees` / `getDashboardStats` / `getRecentJoiners` call the real
  gateway. `GET /api/v1/employees` returns a paginated `{ items, total, … }`; the directory,
  the **Total Employees** stat and **Recent Joiners** are live. `department_id` is an unresolved
  account-service UUID, so the team label falls back to the employee's `job_title`.
- **Sample** — the **attendance service isn't wired to this org yet**, so `listAttendance` and the
  attendance-derived widgets (Present Today, On Leave, the weekly chart, today's punches) render
  representative data and carry a **"Sample data"** chip when running live.
- **`VITE_USE_FIXTURES=true`** — the whole UI renders from `src/api/fixtures.ts` with no login,
  for offline design work.

## Components

A local set under [`src/ui/`](src/ui): `Button`, `Pill`, `Card`, `StatCard`, `StatusPill`
(attendance + employee), `Avatar`, `Tag`, `SearchInput`, `TopNav`, `PageHeader`, `DataTable`,
`MiniBarChart` / `Sparkline`, `AttendanceBarChart`, `AssistantPanel`, `SampleChip`, and shared
`Skeleton` / `EmptyState` / `ErrorState` / `SectionHeader`. Charts are hand-rolled inline SVG —
no charting dependency.

> **Follow-up:** these should **graduate to the shared `opsgrids-ui` package** — ADR-0001 forbids
> an app forking shared components long-term. They live here only for this first product slice.

## Deferred (next slices)

- **Attendance service** — wire live punches so the attendance screen and its widgets drop the
  "Sample data" chip.
- **Department resolution** — resolve `department_id` against account-service instead of leaning
  on `job_title` for the team label.
- **`opsgrids-ui` graduation** — move `src/ui/*` into the shared design-system package.
- **Leave & Payroll** — currently routed placeholders.
