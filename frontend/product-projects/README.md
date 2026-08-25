# @atmora/product-projects

Atmora Projects — plans, boards and workflows without the rest of the suite.

A standalone Vite + React + TypeScript SPA in the Atmora product family. It shares the suite's
violet "Console" design language (Manrope, a single violet accent, light + dark) with the other
product apps; only the navigation, screens and delivery-domain content differ.

## Screens

- **Overview** — KPI cards (active projects, on-time delivery, milestones due, resource
  utilization), a release burndown, the project-health breakdown, and a "needs attention" list of
  slipping and at-risk milestones.
- **Timeline** — a Gantt-style plan: one bar per project across a month scale, milestone diamonds,
  and a "Today" marker, scrolling horizontally with a pinned project column.
- **Board** — a workflow kanban (Backlog → In Progress → Review → Done); cards carry their project,
  assignee, priority, due date and estimate, and can be moved between stages.
- **Portfolio** — the projects table with search, health/phase filters and sort, plus a drill-down
  detail panel (budget, dates, team and milestones).
- **Milestones** — every milestone with owner, date, progress and status, filterable by status.

## Fixtures-first

The `projects` service backend is a skeleton, so the app renders entirely from
`src/api/fixtures.ts` by default. Flip `VITE_USE_FIXTURES=false` once the service ships and every
call switches to the gateway at `/api/v1/...` — no other code changes.

## Commands

```bash
pnpm install
pnpm dev         # http://localhost:4390
pnpm typecheck
pnpm lint
pnpm build
```

Node ≥ 22 is required.
