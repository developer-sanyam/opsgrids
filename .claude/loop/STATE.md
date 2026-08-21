# Loop state

Product: Atmora — one organization, one account, one user directory, many independently
activatable products (HRMS, CRM, PulseBoard, Sales CRM, Project Management, IT Management,
Marketing, Finance, Operations)
Started: 2026-08-21 — ledger opened after the fact; the platform predates it, see Slices
Stack: Python 3.13, FastAPI, SQLAlchemy 2.0 async, Alembic, asyncpg, PostgreSQL 16 + PostGIS,
Redis, RabbitMQ, MinIO, uv workspace. Frontend: Next.js 16 (marketing), Vite + React (apps)
Tenancy: shared schema + `organization_id` on every row + Postgres RLS — never per-tenant DB
Auth: global user + per-org membership; EdDSA (Ed25519) + JWKS, per-audience tokens carrying
no permission list, `pv` claim checked at the gateway
Billing: none built. Planned dual — Razorpay domestic, Stripe international, by org country.
**Razorpay adapter first**: it is the more constrained provider, and an interface designed
Stripe-first produces abstractions Razorpay cannot satisfy, discovered after ten call sites
Deploy: Kubernetes, Helm + kustomize — **local only for now. Nothing is pushed, and the repo
has no remote configured.**

## Assumptions
- The reference architecture is Zopping (~45 Go services, two gateways, org-scoped rows in a
  shared schema, events), translated to Python and to business software. User-confirmed.
- PulseBoard's live data migrates in with preserved UUIDs; its frontend survives as a client.
  Every other existing codebase is a specification to re-implement, not code to port. Confirmed.
- CRM is greenfield and owns Customer/Contact. Confirmed.

## Gate commands
install: `make install` | typecheck: `make typecheck` | lint: `make lint` |
test: `make test` (needs `make infra`) | e2e: `make e2e` | migrate: `make migrate` and
`make migration-check` | build: none yet | everything CI runs: `make check`

Row 6 (production build) has nothing to run here. Report it N/A with the reason, never green.

## Slices

Delivery-plan phases 0–2 are complete. This ledger was opened afterwards, so the first rows
are recorded retrospectively from git history rather than having been run through the loop.

| # | Slice | Status | Rounds | Commit | Notes |
|---|-------|--------|--------|--------|-------|
| — | Platform foundation — platform-core, dev-bootstrap, CI, service generator | DONE | — | 659ea5b, 90ed777 | retrospective |
| — | Marketing website + frontend workspace | DONE | — | c788ab0 | Next.js 16, 9-product catalogue |
| 0 | account-service — orgs, units, locations, users, memberships, roles, permissions, invitations | DONE | — | 4210187, cf858eb | 46 tests; the tenancy and identity foundation |
| 1 | auth-service — credentials, sessions, refresh rotation, MFA, SSO config, signing keys | DONE | — | a6e3548 | 41 tests; sole holder of a private key |
| 2 | Gateways — app-gateway (product) and admin-gateway (operator) | DONE | 1 | ca58de3 | 46 tests; found duplicated correlation headers and unenforced per-route `staff_only` |
| 3a | Internal API + permission-version bump | DONE | 1 | 15d6ec0 | 17 new tests. Closed two gaps found while scoping slice 3 — see Findings |
| 3c | Row-level security — **ADR written, awaiting review** | BLOCKED | 0 | | docs/adr/0002-row-level-security.md. Implementation deliberately not started. Three independent bypasses found; steps 0–2 are prerequisites with no user-visible payoff |
| 3b | End-to-end journey suite | TODO | 0 | | closes gate row 8 — cross-service, real DB, real tokens. 3a was its prerequisite: the journey could not have passed |
| 4 | product-service — registry, per-org activation, entitlements, marketplace catalogue | TODO | 0 | | makes the gateway's entitlement gate a real registry rather than a token claim |
| 5 | config-service — per-org settings, integration secrets, toggles | TODO | 0 | | |
| 6 | Platform services — document, notification, audit, scheduler, eventbridge | TODO | 0 | | every later product gets files, email, audit and cron without building them |
| 7 | HRMS rebuild — employee, attendance, leave, payroll, recruit | TODO | 0 | | fix the known timezone defect during the rebuild rather than porting it |
| 8 | PulseBoard rebuild + data migration | TODO | 0 | | **riskiest slice** — live customer data; needs a rehearsed rollback, not a written one |
| 9 | CRM greenfield — customer, sales, marketing, support | TODO | 0 | | |
| 10 | Billing — catalog, subscription, metering, billing, payment | TODO | 0 | | Razorpay adapter first |
| 11 | Ecosystem surface — super-admin console, marketplace, workflow, analytics | TODO | 0 | | |

Status: TODO / IN PROGRESS / DONE / BLOCKED

## Findings

Three gaps surfaced while scoping the end-to-end suite. All three had passing unit tests
on both sides, because each side stubbed the other — the argument for slice 3b.

- **[FIXED, uncommitted] No `/internal/v1/*` endpoints were served.** auth-service called
  `/users/by-email` and `/users/{id}`; both gateways called
  `/memberships/{id}/grants`. account-service implemented none of them. In a real
  deployment sign-in could not resolve a user and the gateway failed closed with 503 on
  every authenticated request — the platform could not serve one real request end to end.
  Fixed by `services/account/app/api/internal.py`.
- **[FIXED, uncommitted] The permission version was never bumped.**
  `bump_permission_version` existed, the gateway checked `pv` on every request, and
  `set_member_roles` claimed in its own docstring to bump — but nothing called it, so the
  staleness check was unreachable and a revoked role stayed usable until the token
  expired. Now bumped on all four grant-changing paths: role change, member revocation,
  role-grant edit, and a location/department/manager move.
- **[OPEN — ADR-0002 proposed] Row-level security is designed but not implemented.**
  Investigation found it is inert for **three independent reasons**, not one: no policies
  exist, the app connects as a **superuser** (which bypasses RLS unconditionally — `FORCE`
  cannot override it), and `set_tenant` has **no callers**, so the tenant GUC is never set.
  Enabling policies alone would be a silent no-op; wiring the GUC alone would return zero
  rows for every query. Also found: a **fourth** cross-tenant path,
  `POST /api/v1/invitations/accept`, which is public and writes — correcting the claim in
  `internal.py` that there are only three. See docs/adr/0002-row-level-security.md. ADR-0000 and the README
  both commit to shared schema + `organization_id` + RLS, and no migration issues
  `ENABLE ROW LEVEL SECURITY` or `CREATE POLICY`. Isolation currently rests entirely on
  every query carrying the right filter — the thing RLS exists to stop depending on. The
  plan's own acceptance gate (§9.1, the generated cross-org suite) cannot pass
  meaningfully without it. **This should be its own slice, before any product service
  adds more tables.**

## Deferred MINOR findings
- none recorded

## Blockers
- `/loop` itself cannot run in the session that installed it: `.claude/` was created after
  the session started, so neither the command nor the agent types resolve. Restart first.

## Decisions
- 2026-08 Eleven irreversible platform decisions -> docs/adr/0000-phase-0-decisions.md
- 2026-08 One standalone frontend app per surface -> docs/adr/0001-frontend-topology.md
