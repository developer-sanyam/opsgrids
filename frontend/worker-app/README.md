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

## Storage put-away

`/putaway` (`src/routes/Putaway.tsx`) moves stock from a **receiving or staging** bin into its
storage bin, recorded by scanning the destination bin's label. It is not ADR-0025's "putaway task"
(the order-cancel reversal). Gated on `operations.inventory.stock.transfer:ORG`, which
`OPERATIONS_WORKER` holds; the screen reads stock with `stock.read`, which it also holds, and a
worker needs no `location.read` because the server resolves the scanned label.

- **The bin is the location of record.** The worker's GPS fix is audit evidence only. One
  `getCurrentPosition` request (high accuracy, 5 s) is made when they confirm; denied, timed out,
  unsupported, offline or unusable all post without it and say so. It is sent as integer
  micro-degrees (`Math.round(degrees * 1e6)`) in the request body and nowhere else — never logged,
  never rendered, never stored beyond the queue entry that has not synced yet.
- **Its own queue** (`src/lib/putawayQueue.ts`, not `scanQueue`, which is for `scan_events` only).
  Confirming freezes the request — body, a random `Idempotency-Key`, `captured_at` — into
  `localStorage` *before* sending, and every resend is that same request. `occurred_on` is the
  exception to "the phone dates it": the server refuses a date after its own today, so while the
  phone is online it is left out and the server dates the put-away itself (a phone with a wrong
  clock is never refused); confirmed offline, the UTC date is frozen into the entry. What happens
  to an answer is decided by its status (`classifyFailure` in `src/lib/putawaySync.ts`):
  - **Retried under the same key, left exactly as it was:** no answer at all (a dropped
    connection, our own 30 s timeout, a body that would not parse), any 5xx, **408** and **429**
    (the server asking for another go, not refusing the put-away). A network failure ends the
    pass; a 5xx moves on to the next entry.
  - **Parked, never sent again:** every other 4xx — 404 unknown bin, 409 reserved stock or a key
    reused for a different body, 422 a rule or the schema, 403. Retrying cannot change the answer
    (`frontend/CLAUDE.md`: never retry a 4xx). The entry stays on the Put away screen with the
    server's reason and a Discard, its GPS fix is dropped, and the queue carries on. The one entry
    the worker is watching is refused straight back to the screen instead — unless they have left
    it by the time the answer comes, in which case it is parked like the rest and the attention
    banner says so.
  - **401:** the session is gone; the entry is untouched, the pass stops, and it syncs when the
    same person signs back in.
  - **Whose words:** the server's own sentence, except a date after the server's today (the
    phone's clock is ahead: it says so) and a 422 from the schema, whose generic "Request payload
    failed validation" is replaced by the server's message for each field that failed.

  The drainer (`PutawayQueueProvider`, mounted in `App.tsx`'s `AppShell`) runs on mount, on
  reconnect, by hand and on a backing-off timer, one pass at a time, and re-reads the queue before
  every send. Entries belong to the signed-in user and organization and replay only for them;
  signing out purges the queue (asking first if put-aways would be lost), stops a running pass from
  sending what was purged, and purges again once the session has ended. With `localStorage`
  unavailable — or once a write to it fails — the queue lives in memory for the session and the
  screen says closing the app would lose what is waiting. When a write fails the older copy storage
  still holds is cleared, best effort, rather than left to bring back entries that have synced and
  GPS fixes that were dropped after a reload.
- **The stock list outlives one put-away.** It is held above the form, so *Put away more* asks the
  server for fresh quantities when online and, if that fails, keeps the last list that loaded
  (marked as possibly out of date) rather than blocking the next line. Quantities waiting in the
  queue are always taken out of it, so the same stock is not offered twice.
- **Only receiving and staging stock is offered.** Put-away can only start there (spec decision
  D1), and `GET /inventory/stock` carries the kind of each row's location as `location_kind`, so
  the picker lists the rows whose kind is `receiving` or `staging` and nothing else — never a guess
  from what a bin's code looks like. If the list has stock but none of it qualifies, the picker
  says so and that stock elsewhere is moved with a bin transfer. A row with no kind (a server that
  predates the field) is not ruled out, because the app cannot tell: it is offered, and the
  server's 422 still has the last word.
- **The picker is a list of buttons, not radios.** Choosing stock moves the worker on to the next
  step, and a radio group selects on the arrow keys — which would commit the first row a keyboard
  or switch user merely moved past. Tab walks the rows; Enter or Space chooses.
- **This is not offline-first.** There is no service worker: the app has to be open and loaded for
  anything to sync, and a phone that loses signal before first load cannot open it at all. The
  Put away screen is also downloaded the first time it opens (`lazyScreen`), so without a signal it
  may not load; it then says so, in the screen's place and with the shell and the queue drainer
  still running, and *Try again* asks for it again. It cannot open until the signal is back.
- A suggested bin comes from `GET /putaway/suggest`, which only offers bins that have a barcode. It
  carries the bin's code and its barcode. The server resolves a scanned label by barcode alone, so
  the amber "different bin" confirmation compares the scanned label with the barcode only — a bin's
  code is not a label. (A suggestion with a `null` barcode, from a server that predates that rule,
  still says it cannot be scanned, and nothing a worker scans matches it.) The server's own
  `matched_suggestion` is what is recorded.
- **A put-away waits for the suggestion to settle.** The amber check and `suggested_location_id`
  both depend on it, so *Put away* is held, saying it is waiting for the suggested bin, until the
  request has either loaded or failed. If it failed the worker has been told so and the put-away
  goes without one; *Try again* holds it again until that request settles.

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
