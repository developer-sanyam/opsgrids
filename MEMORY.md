# MEMORY.md — state and architecture log

The durable record of **where the system stands, what was decided, and what broke before**.
Append; do not rewrite history. A wrong entry gets a dated correction underneath, not a deletion.
What is in flight this week belongs in [`HANDOVER.md`](HANDOVER.md); the target design belongs in
[`SPEC.md`](SPEC.md).

Never record secrets, `.env` values, IP addresses, ssh key paths or customer data here.

---

## 1. Current architecture (as of 2026-10-15)

- **Production = `main` @ `1dbeebd4`** (deployed 2026-10-15 in three batches, PRs #148–#170; the
  previous release was `d73bb9e4`, 2026-10-12). No open PRs. Pre-deploy backups exist on the box
  only: `BACKUP_S3_*` is empty, so there is no off-box copy.
- 34 service directories, 2 of them gateways; 3 are still skeletons (`config`, `eventbridge`,
  `quotation`). 13 permission manifests, 34 OpenAPI documents.
- Shared Python helpers now live in `platform-core` (#166, #169): `platform_core.catalogue`
  (permission-catalogue classes), `GuardedSweep` (every hand-written cron sweep), `supervise`,
  `db.constraint_name`/`sqlstate`, `pagination` keyset cursors, `auth.column_scope_resolver`,
  `events.actor_for`/`parse_envelope_dt`, `validation.reject_explicit_null`, `dns.answer_text`.
- Frontend shells are shared: `ProductShell`, `ProductLoginScreen` (`opsgrids-ui`) and
  `ProductAuthProvider` (`opsgrids-client`) replace the per-app copies. On a phone every app gets a
  bottom nav (destinations and quick actions per product), sheets for dialogs and menus, and
  card-style tables.
- Layout sweep (`frontend/opsgrids-visual/sweep/`) opens every route of 14 apps in sample mode; a
  developer tool, not CI. Visual baselines: 402 stories + 7 app pages, 2,454 PNGs, darwin only.
- One Azure VM runs everything (ADR-0012/0013): ~36 containers, Compose profiles per product,
  Caddy, one shared `edge-relay`, consumers folded into API processes. The box is RAM-bound and at
  its owner-accepted ceiling (`EDGE_BUDGET_ACCEPTED_MB=3945`); **no new container without an owner
  decision on the ceiling.**
- GitHub Actions is unavailable (account billing) — CI and the Deploy workflow do not run. Deploys
  are manual `make deploy` by, or with explicit approval of, the owner.
- 15 web bundles published from `main`: dashboard, internal, buyer, worker, and one per product app.
  `ci-plan.sh` now plans the worker and internal bundles too (#156).

## 2. Decisions

The ADRs are authoritative ([`docs/adr/`](docs/adr/), index in [`docs/decisions.md`](docs/decisions.md)).
The ones that bite most often in day-to-day work:

| ADR | Decision in one line |
|---|---|
| 0000 | Eleven irreversible choices: token shape, permission grammar, money as minor units, tenancy model |
| 0001 | One SPA per surface; apps never fork a shared component |
| 0002 | Forced RLS on a transaction-local GUC — a net, not the control |
| 0004 | Commerce is five services: catalog (immutable) / subscription (mutable pointer) / metering / billing / payment |
| 0007 | New permission codes reach live tenants only via `edge-reconcile`, which also grants new admin roles to owners |
| 0012/0013 | One box, not a cluster; products behind Compose profiles |
| 0017 | `lead` joins `crm`; `pipeline`/`activity` services deleted — `deal` owns `crm.pipeline.*` |
| 0021 | Consent is an append-only ledger; every send path asks one decision function |
| 0023 | Revealing PII needs a short-lived `otp` step-up token |
| 0035 | List price INR 30,000/month or 3,00,000/year for every product |
| 0036 | Test orgs are removed by an owner-run tool, never an in-app delete |
| 0038 | CRM programme D-1…D-41 — owner accepted every default on 2026-10-11 |
| 0040 | CI shards service tests by item on a tuned throwaway Postgres |

Recorded outside an ADR:

- **Mobile (2026-10-14, #157).** `DataTable` renders cards below 768px by default; opt out with
  `mobileCard="table"`. Text fields are 16px below 768px (iOS zooms smaller ones). Compact controls
  get a 44px target with the `hit-44` class. `PageHeader` folds `secondaryActions` into a menu on a
  phone. `StickyActionBar` is the one bottom action bar (#167). `FormDialog`, `Drawer`, `Menu` and
  `CommandMenu` become sheets that clear the notch.
- **Worker task display fields (2026-10-14, #159, ADR-0024 addendum).** Order number, bin code and
  item name on fulfilment tasks and packages are resolved server-side inside `operations`, and only
  for a caller with the matching org-wide read or a live offer on that task. No permission or role
  was widened, so there is no reconcile step.
- **Marketing site.** The owner chose a "Liquid Glass" look with real product screenshots. It lives in
  the separate marketing repository, not here; this entry records the decision, not the code.

- Commit messages and PRs name no AI assistant.
- Tests run on real PostgreSQL, never SQLite.
- Each service is tested and typechecked in its own process (`import app` is ambiguous).
- Only `.claude/settings.json` is tracked under `.claude/`; the rest of the harness stays local.
- PRs always target `main`, never another PR's branch (2026-10-10: a stacked PR merged into a dead
  docs branch, and another auto-closed when its base was deleted).
- Risky behaviour ships dark first: `log_only` modes (`ORDER_INVOICING_MODE`,
  `CRM_CONVERSION_CLEANUP_MODE`) that the owner flips after reading the logs.

## 3. Bugs fixed — and the lesson each one left

Full write-ups for the early ones: [`docs/discoveries.md`](docs/discoveries.md). Every one of them
passed a green test suite; most were contract failures between two components that stubs hid.

| When | Bug | Lesson / guard |
|---|---|---|
| Phase 0 | account-service served none of its internal endpoints | E2E suite boots real services; stubs encode beliefs |
| Phase 0 | Revocation silently did nothing | Permission version bumps on every grant-changing path |
| Phase 0 | `staff_only` parsed and never enforced | Staff routes answer 404; tested at the gateway |
| Phase 0 | RLS inert for three independent reasons; GUC did not survive its transaction | NOBYPASSRLS roles, FORCE RLS, transaction-local GUC |
| Phase 0 | No service loaded its config file | `.env` found by walking up to the root `pyproject.toml` |
| 2026-09-24 | MinIO OOM-looped at 128 MiB, thrashing swap | `mem_limit 256m` + `GOMEMLIMIT`; every container has a memory fuse |
| 2026-09-24 | `compose up` never removes containers whose profile went inactive; health then fails | Stop *and* `docker rm` retired containers |
| 2026-09-24 | Staff console built on fixtures crashed on real API shapes | Build consoles against the real endpoint shape, not a fixture |
| 2026-09-25 | Owner lacked a new admin role after a manifest change | Reconciler now grants new admin roles to owners (#61) |
| 2026-09-26 | Domains page: "No route for /api/v1/domains" | `tests/infra/test_frontend_paths_are_routed.py` — every SPA path needs a prod route |
| 2026-09-26 | `notification` crash-looped in prod: code found `libs/contracts` via `Path(__file__).parents[3]`, which does not exist in the image | Never read repo-relative paths at runtime — the image holds only `services/<name>/`; a green health gate is not a working service (#67) |
| 2026-09-27 | WhatsApp transient send failures never retried; no number `register` call | Fixed in #70; design and audit in `docs/whatsapp/` |
| 2026-10-06 | Admin console published from a stale branch while reported live | `make edge-web` builds the local checkout — check out the deployed commit, then grep the published bundle |
| 2026-10-10 | CRM 360 view called `/deals` instead of `/api/v1/deals` | Fixed in R1.2a (#122) with an internal-client guard |
| 2026-10-11 | Deploy stopped at migrate: two Alembic heads in `notification` | Single-head test per service (#133) |
| 2026-10-12 | IDOR on several routes | Fixed in #136; every endpoint needs a negative-authz test |
| 2026-10-12 | Six orders stuck in `on_accept` | Runbook `docs/runbooks/2026-10-12-unblock-on-accept-orders.md` |
| 2026-10-14 | Engage and Operations screens showed "Request failed with 500" in sample mode | The sample-data fixtures lacked those endpoints, not the API. A sampleMode guard test now fails when a screen calls an endpoint no fixture serves (#149, #153) |
| 2026-10-14 | `AppShell` bottom padding never applied | An unlayered CSS rule beat the layered utility. Spacing the shell owns must not be overridable by an unlayered rule (#150) |
| 2026-10-14 | Money shown ÷100 for JPY and KWD; instants rendered in UTC or an unlabelled host zone | Formatters use the currency's own exponent and the viewer's zone, and name it (#156) |
| 2026-10-14 | `SENTRY_DSN` and OTLP settings were declared, passed to every service and required by preflight, but read by nothing | Removed; `docs/operations.md` says error tracking is not wired. A test pins it (#156) |
| 2026-10-14 | `ci-plan.sh` never planned the worker and internal bundles | Added to the app list; the plan test pins the full list (#156) |
| 2026-10-14 | `ENGAGE_TRACKING_SECRET` set on the box had no effect | Compose never forwarded it to notification; it does now (#156) |
| 2026-10-14 | Sweep failure backoff waited a full poll interval before the first retry | Backoff starts at `min(poll_interval, max_backoff)` (#156) |
| 2026-10-14 | `make boundaries` failed for every service except `operations` | The shared `.importlinter` held operations-only contracts. They moved to `.importlinter.operations`; a `tests/infra` check keeps the shared contracts identical (#166) |
| 2026-10-14 | `product-engage` import cycles | Request shapes moved into leaf `types` modules (#167) |
| 2026-10-14 | Sticky `FormDialog` footer sat 16px above the scroll body's edge, so fields showed beneath it | `-bottom-4` makes it flush (#170) |

## 4. Known sharp edges

- `make secrets` flags quoted hex ids and password-like words in docs — write Alembic revision ids bare.
- Local service tests need a reachable Redis; `.env.example` points at 6380 — set `REDIS_URL` if yours differs.
- Auth tests 401 when the worktree `.env` sets `ADMIN_GATEWAY_HOP_KEY`.
- The dev Postgres container mounts `init-db.sql` from the checkout that created it; deleting that
  worktree breaks the container — recreate it from the main checkout.
- `e2e/edge_*` scripts create organizations in production. Do not run them against prod.
- A SPA fallback returns 200 for any path; `curl` proves nothing about a bundle.
- Never `alembic downgrade` past a migration once data that depends on it exists (put-away captures,
  phone-only contacts, captured leads).

## Log

Add one dated line per architecture change, decision, or fixed bug that a future session needs.

- 2026-10-14 — SPEC.md, MEMORY.md, HANDOVER.md added; `.claude/settings.json` tracked as the project permission policy.
- 2026-10-14 — Finance prints a GST tax invoice and credit note at `/invoices/:id/print` (#151); Operations generates `ITM-000001`-style item codes when the SKU is blank, per organization (#152).
- 2026-10-14 — Mobile-responsive shared UI and the layout sweep (#155, #157, #161, #163, #164); worker app shows real order numbers, bins and items (#159); audit fixes (#156); dead code removed (#158).
- 2026-10-14 — Duplicate helpers consolidated into `platform-core`, `tools/`, `tests/infra` and `e2e` (#165–#169); visual baselines regenerated (#170).
- 2026-10-15 — Production deployed to `main` @ `1dbeebd4` in three batches. Docs refreshed: HANDOVER, MEMORY, SPEC, `docs/operations.md`, `docs/production-readiness.md`; `docs/what-was-built.md`, `docs/theme-guide.md` and `docs/architecture.html` marked historical.
- 2026-10-15 — Organization audit log (ADR-0044, `feat/org-audit-log`): each service serves its own `audit_logs` at `/api/v1/audit/<service>` through `platform_core.audit_read` (field names, never values); the dashboard merges them at `/admin/audit`; `ORG_ADMIN` gains `core.identity.audit.read`, so live orgs need `make edge-reconcile PRODUCTS=core`.
