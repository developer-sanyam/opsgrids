# worker-app

The Fulfilment F3 worker PWA — the picker/packer/dispatcher surface (`docs/fulfilment/plan.md`
F3, ADR-0024 §7). Vite + React + TypeScript + Tailwind CSS v4, the same "Console" design system
every other app in `frontend/` uses, mobile-first: a bottom navigation bar, large touch targets,
no desktop sidebar for the two screens that exist today.

## Run it

```bash
pnpm --filter @opsgrids/worker-app dev        # http://localhost:4440
pnpm --filter @opsgrids/worker-app build
pnpm --filter @opsgrids/worker-app typecheck
pnpm --filter @opsgrids/worker-app lint
pnpm --filter @opsgrids/worker-app test
```

Dev serves on **:4440** (`--strictPort`), clear of every other product app's port. In dev, Vite
proxies `/api/*` to the gateway on **:8100** (`server.proxy` in `vite.config.ts`), so the browser
makes only same-origin calls and CORS never enters the picture.

## Scope — this is a scaffold, not F3's finished frontend

`docs/fulfilment/plan.md` F3 ships pick tasks, offers, scans, exceptions and the pack/dispatch
confirmations against a backend (`app/fulfilment/`) that does not exist yet. This app is the
**app-shell infrastructure** that backend will be wired into, not a preview of its screens with
placeholder data standing in for real ones — this codebase's rule against fake production
functionality applies here as everywhere else. What is built:

- **Login** (`src/routes/Login.tsx`) — real, wired to the real `/auth/login`. In production every
  other product app hands sign-in to the dashboard instead (`RedirectToCentralLogin`); this app
  follows the same rule via `VITE_AUTH_ORIGIN`, same as `product-operations`.
- **Home** (`src/routes/Home.tsx`) — the task-list shell. `TaskListPanel`
  (`src/ui/TaskListPanel.tsx`) implements and unit-tests all three of `loading` / `empty` /
  `error`; there is no `populated` state yet because there is nothing to populate it with. `Home`
  currently renders a constant `status="empty"`, which doubles as the honest "coming soon"
  message — wiring the real endpoint later is a one-line change (`useAsync(loadMyTasks, …)`
  feeding `TaskListPanel`'s props), not a new component.
- **Profile** (`src/routes/Profile.tsx`) — real, name/email/role/organization from `GET
  /auth/me` (already loaded by `AuthProvider` at boot) and sign-out.

**Deliberately not built**: task detail, scan, pick confirmation, pack confirmation, exceptions,
and the offline scan queue. Each needs F3's actual API, which is being built separately; guessing
its shape here would be exactly the fabricated contract this brief warns against. There are no
route stubs for these either — nothing in the app can navigate to them yet (Home has no real task
rows to link from), so a stub route would be unreachable, speculative dead code rather than
"nav-shell completeness". `NotFoundPage` already answers any stray path.

## Offline

No offline-queue primitive exists yet anywhere in `@opsgrids/client` or `@opsgrids/ui` (checked
before starting this slice). The scan screen's local-queue-and-replay-on-reconnect requirement
(`docs/fulfilment/plan.md` F3) is real F3 scope, not scaffolding, and is left for that build.

## Auth

Sign-in lives in [`src/auth/`](src/auth) and reuses `@opsgrids/client` exactly like every other
product app: the access token lives in memory only (never `localStorage`), the refresh token is
an httpOnly cookie. Unlike `product-operations`, this app carries **no fixture identity** —
there is no local screen yet whose data a fixture would stand in for, so `AuthProvider` (this
app's own, in `src/auth/AuthProvider.tsx`) always talks to the real gateway.

## Design tokens

Colour, spacing, radius and shadow all come from `@opsgrids/ui`'s `console.css` tokens
(`frontend/CLAUDE.md`), lint-enforced by `opsgrids/tokens` the same as every other app. No raw
hex, no arbitrary Tailwind values, no one-off shadows or radii.
