# CLAUDE.md — OpsGrids Platform

One ecosystem, many products. Python microservices behind two gateways, one PostgreSQL cluster
with a schema per service, RabbitMQ for domain events.

**Read `docs/adr/0000-phase-0-decisions.md` before changing anything structural.** Those eleven
decisions are load-bearing and several are irreversible once customer data exists.

## Session files

| File | Purpose | When |
|---|---|---|
| `CLAUDE.md` | Rules, commands, conventions (this file) | Always loaded |
| [`HANDOVER.md`](HANDOVER.md) | What is active, blocked, and next | **Read first every session; rewrite before ending** |
| [`SPEC.md`](SPEC.md) | Products, system design, API surface, features being built | Before designing or adding an endpoint |
| [`MEMORY.md`](MEMORY.md) | Current architecture, decisions, bugs already fixed | Before debugging; append when you fix or decide something |
| `.claude/settings.json` | Tool permissions: secrets denied, prod commands ask | Tracked; change by PR |
| `frontend/CLAUDE.md` | Frontend-specific rules | Before touching `frontend/` |

Precedence when they disagree: code > ADRs > `SPEC.md` > `MEMORY.md` > `HANDOVER.md`. Fix the stale one.

## Layout

```
services/         one directory per microservice; each owns a Postgres schema
libs/platform-core   the shared library every service depends on
libs/contracts    JSON Schema for every event and API — the polyglot contract
frontend/         product SPAs, admin console, super-admin console
infrastructure/   the production edge: compose, Caddy, runbook (ADR-0012). Not Helm/kustomize —
                  production is one VM, chosen deliberately over a cluster nobody operates.
tools/            dev-bootstrap, generators, seeds
docs/adr/         architecture decision records
```

## Rules that are not style preferences

These cause data corruption, audit failure, or an unshippable migration — not messy code.

- **Every table carries `organization_id`.** Every query filters on it. RLS enforces it as a net;
  do not treat RLS as permission to skip the filter.
- **The tenant GUC is transaction-local** (`set_config('app.organization_id', …, true)`). Setting
  it per connection is a cross-tenant leak — the pool reuses connections across requests.
- **The outbox relay and consumers need `BYPASSRLS`** (or a per-message GUC). Getting this wrong
  silently stops event processing: nothing errors, the outbox just fills (the relay runs as
  `opsgrids_outbox`, which may read and update `outbox` and nothing else).
- **No cross-schema foreign keys.** This is what keeps a later database-per-service split a config
  change rather than a rewrite. `org_id` is a plain indexed UUID, not an FK.
- **Enqueue background work only after commit** — via the outbox, never inline.
- **Money is integer minor units** with an ISO currency. No floats, no `Numeric(_, 2)`.
- **Invoices, usage events, and the credit ledger are append-only.** Corrections are new rows.
- **Plan versions are immutable** and subscriptions pin to a version.
- **Only `auth-service` holds a private signing key.** Everything else verifies via JWKS.
- **A product's admin role is built from that product's own catalogue**, never "all permissions".
- **PII is encrypted and masked by default**; unmasking needs its own permission **and writes an
  audit row every time**.
- **Authorization is server-side.** A UI gate hides a control; it does not protect data. Every
  list endpoint injects scope into the query, and every endpoint needs a negative-authz test.

## Service anatomy

Every service is the same shape. Learn one, navigate all of them.

| Module | Holds |
|---|---|
| `app/main.py` | `create_app()` call. No logic. |
| `app/config.py` | `Settings(ServiceSettings)` — fixes `service_name`, `db_schema`. |
| `app/models.py` | SQLAlchemy tables on a schema-scoped `Base`. |
| `app/schemas.py` | Pydantic contracts — this is the OpenAPI surface. |
| `app/api/` | Routers. Each route declares its permission. |
| `app/selectors.py` | **Reads.** Every scoped query lives here and calls `ctx.apply()`. |
| `app/services.py` | **Writes.** Rules, transactions, audit rows, outbox rows. |
| `app/domain.py` | Pure functions, no I/O. Only where real algorithms exist. |
| `app/consumers.py` | Event handlers. Idempotent. Own process. |
| `app/relay.py` | Outbox relay. Own process. |

`bin/api`, `bin/worker`, `bin/cron` are the three entrypoints.

**`import app` is ambiguous across services** — each exposes a top-level `app`, so one process can
only ever hold one. Test and typecheck loop per service; do not "simplify" that back into a single
invocation.

## Commands

```bash
make install      # uv sync
make infra        # postgres+postgis, redis, rabbitmq, minio, mailhog
make migrate      # alembic upgrade for every service
make check        # lint + typecheck + test — the fast path
make gate         # check + audit, licences, secrets, hygiene, boundaries, contracts, smoke — what CI runs
make new-service NAME=foo   # scaffold from the template

# one service at a time
make run SERVICE=account PORT=8111
make makemigration SERVICE=account M="add memberships"
cd services/<name> && uv run pytest tests -q   # each service in its own process
make migration-check                       # model changed without a migration?
make openapi                               # regenerate contracts after an API/permission change

# frontend (Node ≥22, pnpm)
make frontend-install && make frontend-gate   # typecheck, lint, boundaries, test, build, audit, licences
```

`make help` lists every target. Tests need real PostgreSQL (never SQLite) and a reachable Redis —
set `REDIS_URL` if yours is not on 6380.

## How work is done here

- **Inspect before changing:** the service, its ADRs, contracts, permissions, schema, migrations
  and tests. Significant features get requirements + acceptance criteria + a phased plan first.
- **Bugs:** reproduce with a failing regression test first when practical.
- **Evidence only:** never claim a check passed without having run it; never weaken a test or
  bypass a gate to get green.
- **Contracts move with code:** an API or permission change regenerates `libs/contracts`
  (`make openapi`); a new event type adds its schema under `libs/contracts/events/`.
- **Finish with a six-part report:** what changed and why · files/services affected · checks run
  with results · security/migration/compatibility risks · remaining work and release readiness ·
  whether any production action was taken.

## Production — owner-gated

Production is one VM at `edge.opsgrids.com`; see `infrastructure/edge/RUNBOOK.md`.

- **No deploy, migration, restart, reconcile `APPLY=1`, `.env` edit, or prod DB read/write without
  explicit approval of that specific action.** "Implement / ship / finish" is not approval.
  Stop at the step before and report readiness.
- Never print or paste `.env` values, keys, or customer data. Never run `e2e/edge_*` against prod —
  they create organizations.
- Deploy order: `make deploy … REF=<sha>` (preflight → build → validate → migrate → up → health),
  then `make edge-reconcile PRODUCTS=…` (dry run first) for new permission codes, then
  `make edge-web APP=<app>` per changed bundle **from a checkout of the deployed commit** —
  `edge-web` builds the local tree, not a ref.
- The box is at its memory ceiling. A new container or process needs an owner decision on
  `EDGE_BUDGET_ACCEPTED_MB`; prefer folding consumers into the API process.
- Risky behaviour ships behind a `log_only` mode the owner flips after reading the logs.

## Git conventions

- **Commit messages must not mention Claude, Claude Code, or any AI assistant.** No generated-by
  footers, no co-author trailers. Write as the engineer of record would.
- Short-lived `feat/*` branches, PR + review, squash merge.
- **Every PR targets `main`** — never another PR's branch. Rebase the next release onto `main`
  after each merge. Never push to `main`, never force-push.
- The owner merges and deploys. Record review verdicts as PR comments.
- Never commit `.env` or secrets.
