# Atmora Platform

One organization → one account → one user directory → many products.

CRM, HRMS, PulseBoard and everything after them are modules of a single platform, not separate
applications. A customer activates what they need today and adds more later without a new account,
re-uploading their people, or migrating anything.

| Document | Read it for |
|---|---|
| [`docs/adr/0000-phase-0-decisions.md`](docs/adr/0000-phase-0-decisions.md) | The eleven decisions that are expensive or impossible to reverse |
| [`CLAUDE.md`](CLAUDE.md) | Rules that are not style preferences; service anatomy |
| `docs/adr/` | Why each significant decision was made |

## Quick start

Requires **Python 3.12+**, **uv**, **Docker**.

```bash
cp .env.example .env
make install      # uv sync
make infra        # postgres+postgis, redis, rabbitmq, minio, mailhog
make migrate      # every service's schema
make check        # lint + typecheck + test
```

## Shape

```
                    ┌──────────────────┬──────────────────┐
   product UIs ────▶│   app-gateway    │  admin-gateway   │◀──── operator console
   mobile           │      (BFF)       │  internal + ops  │      partner APIs
                    └────────┬─────────┴────────┬─────────┘
                             │ verify once, resolve org, inject tenant context
        ┌────────────────────┼────────────────────┬───────────────────┐
        ▼                    ▼                    ▼                   ▼
  Identity & Org         Money              People (HRMS)      Revenue (CRM)
  auth · account         catalog            employee           customer
  product · config       subscription       attendance         sales
                         metering           leave              marketing
                         billing            payroll            support
                         payment            recruit
        ▼                    ▼                    ▼                   ▼
  Work (PulseBoard)    Content & Comms     Platform & Ops
  project · timesheet  document · search   workflow · scheduler · bulk
  standup · quotation  notification        analytics · audit · integration
                       realtime            eventbridge · admin

  PostgreSQL (schema per service) · Redis · RabbitMQ · MinIO · PostGIS
```

Each service owns its schema, exposes REST, publishes domain events through a transactional
outbox, and consumes them idempotently. No service reads another's tables.

## Layout

```
services/         one directory per microservice
libs/platform-core   app factory, auth, DB+RLS, outbox, errors, audit, money
libs/contracts    JSON Schema for every event and API
frontend/         product SPAs and consoles
integrations/     partner and per-customer integrations
infrastructure/   Helm + kustomize
tools/            dev-bootstrap, generators, seeds
```

## Local ports

Gateways occupy `810x`; services `811x` and up, grouped by area. The authoritative list is
each gateway's `routes.json` — that file is what actually resolves a prefix to an upstream,
so a service that moves is one edit there rather than a hunt through code.

| Range | |
|---|---|
| 8100 / 8101 | `app-gateway` / `admin-gateway` |
| 8110–8119 | identity & org — auth `8110`, account `8111`, product `8112`, config `8113` |
| 8120–8139 | product services (HRMS, CRM, PulseBoard) |
| 8160–8169 | money — billing `8160`, payment `8161`, eventbridge `8163` |
| 8170+ | operator — admin `8170` |

Backing services are offset from the HRMS stack so both can run at once: Postgres `5434`,
Redis `6380`, RabbitMQ `5673`, MinIO `9100`, MailHog `1026`.

## Things that will bite you

- **`import app` is ambiguous.** Every service exposes a top-level `app`; one process holds one.
  Run, test and typecheck per service.
- **The tenant GUC must be transaction-local.** Per-connection is a cross-tenant leak.
- **Relays and consumers need `BYPASSRLS`.** Otherwise event processing stops silently.
- **Balances are derived, never stored.** Ledgers are append-only; a correction is a reversing row.
- **A UI gate is not enforcement.** Every paid endpoint carries a server-side entitlement check.
