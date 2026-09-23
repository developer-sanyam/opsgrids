# Operations

Running the platform, and what to do when it misbehaves.

**Nothing is deployed.** There is no production environment, no Kubernetes cluster, no CI/CD
pipeline beyond test-running CI. This page describes operating the local stack and the
operational properties the services already have — written now because most of it is decided by
the code, not by the deployment.

---

## The stack

```bash
make infra        # Postgres, Redis, RabbitMQ, MinIO, MailHog
make infra-down   # stop
make infra-reset  # destroy volumes and recreate — destructive
```

Ports are offset from the defaults so this stack coexists with others: Postgres **5434**, Redis
**6380**, RabbitMQ **5673**, MinIO **9100**, MailHog **1026**.

## Processes per service

| Command | What it is |
|---|---|
| `make run SERVICE=x PORT=n` | The API |
| `make worker SERVICE=x` | Event consumer — **currently idles; no handlers registered** |
| `make relay SERVICE=x` | Outbox relay |

## Health

Every service exposes `/health` and `/health/ready`. Readiness checks **exactly the dependencies a service declares** — no more, no less. A service
declares them once, in `create_app`, and the lifespan opens the same set:

| Service | Declares |
|---|---|
| account, auth | database, redis |
| app-gateway, admin-gateway | redis only — they own no tables |

Redis is checked because it is a hard dependency of every permission-changing write and of the
gateway's rate limiter and staleness check. Before this, a service whose Redis was gone reported
**ready** and then raised on every role change.

## Things that fail loudly

Good news first. These will page you:

- **Redis unreachable at the gateway** — rate limiting and permission-version checks fail
  **closed**, so requests are refused with `503` rather than let through unchecked.
- **Permission bump with Redis down** — raises, so the role change fails rather than reporting
  success while the person keeps access.
- **Upstream unreachable** — the gateway returns a `503` problem document; a raw connection error
  never reaches a caller.
- **Permission resolution failing** — refused, never forwarded. Forwarding would hand the caller
  whatever a downstream service does when `x-perms` is absent.

## Things that fail silently — watch these

**A stalled outbox relay.** This is the one to instrument first. If the relay cannot see rows —
today only possible through misconfiguration, but guaranteed if it is ever run without
`BYPASSRLS` after row-level security lands — it selects zero rows, logs nothing, sleeps, and
loops. `attempts` never increments. `last_error` stays null. The process looks healthy
indefinitely while the outbox grows without bound and every downstream consumer starves.

**There is now a probe: `GET /health/outbox`**, on every service that owns an outbox. It counts through the same role the relay runs as — `opsgrids_outbox`, BYPASSRLS with SELECT and UPDATE on `outbox` and nothing else — because through the application role it would report zero for the same reason the relay stalls.

```json
{ "pending": 10, "oldest_seconds": 133.09 }
```

**Alert on `oldest_seconds`.** Pick the threshold from a normal day's baseline — take one now,
before row-level security work starts, because that is the change most likely to stall a relay.

It is deliberately **not** part of readiness. A backlog is not a reason to take the service out
of rotation: refusing traffic would stop new events being written without helping drain the ones
already queued, which makes the incident worse. It is a metric, not a health state.

**Empty results that look like normal domain state.** A tenancy misconfiguration produces zero
rows, and the API turns that into "Organization not found" or an empty page with `200`. There is
no log line that distinguishes it from a genuinely empty tenant. The mitigation in ADR-0002 is an
assertion, outside production, that the tenant context matches the principal.

**Stock that disagrees with its ledger.** `operations` keeps stock as an append-only ledger with
a balance per bucket as its locked projection (ADR-0014). The nightly `operations-cron`
reconciles them and **never repairs** what it finds. Alert on the log event
`inventory.reconcile.drift` (error level) and on `inventory.reconcile.organization_failed`; a
pass that checked nothing logs `organizations=0`, which on a box selling Inventory means the
discovery role lost its grant. Open findings are at `GET /api/v1/operations/inventory/discrepancies`
(permission `operations.inventory.stock.reconcile`). See [inventory/ledger.md](inventory/ledger.md).

**A dead-letter queue nobody watches.** Consumers retry a failed message once, then nack to a
DLQ. Real consumers ship today (product, subscription, payment, console), so this is **not**
moot: a dead-lettered event in the money path is an incident. `tools/dlq.py` peeks and replays
a queue's DLQ; queue-depth alerting on `*.dlq` is still to be wired into whatever scrapes
RabbitMQ.

## Logs

Structured, with a correlation id propagated from the gateway through every service. Search on
`correlation_id` to reconstruct one request across services.

Note the header-duplication defect fixed in [discoveries.md](discoveries.md): traces from before
that fix carry a doubled value.

## Secrets

`.env` is gitignored; `.env.example` carries the keys with dummy values. Signing keys are
generated by `make devkeys` into a gitignored directory.

**Two dev defaults must never reach a shared environment:**

- `internal_service_token` defaults to `dev-only-internal-token`. It guards the internal API,
  which reads across every tenant.
- The bootstrap Postgres password is `opsgrids`, and that role is a **superuser**.

## Database roles

Four, split by what each may touch (ADR-0002 §2). `opsgrids_admin` runs migrations and owns the schemas — BYPASSRLS, and nothing long-running connects as it. `opsgrids_account` and `opsgrids_auth` run the service APIs and consumers — NOBYPASSRLS, DML on their own schema only. `opsgrids_directory` serves the cross-tenant identity reads sign-in needs — BYPASSRLS, SELECT-only on seven tables. `opsgrids_outbox` runs the outbox relay and `/health/outbox` — BYPASSRLS, SELECT and UPDATE on each service's `outbox` and nothing else. The roles are created by `init-db.sql`; the table grants are applied by each service's `migrations/env.py` after every migration run.

## Backup and restore

Not configured, and not needed until something holds real data. It becomes mandatory before the
PulseBoard migration, which is the first slice that touches live customer data — and that slice
needs a **rehearsed** restore, not a documented one.

## Rollback

Migrations have `downgrade()`, and every one of them is exercised: `make migration-roundtrip`
takes each service to `head`, back to `base`, and up again, and CI runs it on every change.
A `downgrade()` that does not actually revert is found there rather than during an incident.
There is no deployment to roll back yet.

For the RLS work specifically, ADR-0002 records three rollback levers, cheapest first — the
fastest being `ALTER ROLE opsgrids_app BYPASSRLS`, one statement, no lock. Note that it is also a
permanent silent disable if somebody runs it and forgets, so it should be alerted on.
