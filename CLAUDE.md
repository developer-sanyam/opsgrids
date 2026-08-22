# CLAUDE.md — Atmora Platform

One ecosystem, many products. Python microservices behind two gateways, one PostgreSQL cluster
with a schema per service, RabbitMQ for domain events.

**Read `docs/adr/0000-phase-0-decisions.md` before changing anything structural.** Those eleven
decisions are load-bearing and several are irreversible once customer data exists.

## Layout

```
services/         one directory per microservice; each owns a Postgres schema
libs/platform-core   the shared library every service depends on
libs/contracts    JSON Schema for every event and API — the polyglot contract
frontend/         product SPAs, admin console, super-admin console
integrations/     per-partner and per-customer integration services
infrastructure/   Helm charts, kustomize overlays
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
  silently stops event processing: nothing errors, the outbox just fills.
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
make check        # lint + typecheck + test — what CI runs
make new-service NAME=foo   # scaffold from the template
```

## Git conventions

- **Commit messages must not mention Claude, Claude Code, or any AI assistant.** No generated-by
  footers, no co-author trailers. Write as the engineer of record would.
- Short-lived `feat/*` branches, PR + review, squash merge.
- Never commit `.env` or secrets.
