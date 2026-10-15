# Operations

Running the platform, and what to do when it misbehaves.

**Production is live** on one Azure VM (ADR-0012, ADR-0013), running `main` @ `1dbeebd4` since
2026-10-15. It is Docker Compose with one profile per product, Caddy at the edge, and no cluster,
no staging environment and no second box. The procedures — deploy, roll back, restore, rotate,
every alert — are in [`infrastructure/edge/RUNBOOK.md`](../infrastructure/edge/RUNBOOK.md). This
page is the map: what runs, what fails loudly, what fails silently, and what is not covered.

Every production action (deploy, migrate, restart, `.env` edit, a read against the production
database) needs the owner's explicit approval of that action.

---

## Open risks

| Risk | State | Consequence |
|---|---|---|
| **No off-box backup** | `BACKUP_S3_*` is empty. `backup.sh` writes the dump to the box's disk (newest 7 kept) and then exits non-zero | Losing the disk loses the database and every backup. The provider's VM snapshot is the only other copy |
| **Memory budget over the box** | 3945 MB planned against MemTotal 3862 MB. Accepted by the owner through `EDGE_BUDGET_ACCEPTED_MB`; preflight refuses anything above that ceiling | Under load the box swaps, then the kernel kills something. No new container without an owner decision |
| **CI is not running** | GitHub Actions billing is blocked | Checks run locally (`make gate`); nothing blocks a merge automatically |
| **Payments in test mode** | Live provider keys are not on the box | No real money moves |
| **GST portal not connected** | e-Invoice calls are not live | Tax invoices print without IRN and QR |
| **No error tracking, tracing or metrics store** | See [Error tracking](#error-tracking) | An unhandled error reaches you only as an `error` log line and a 5xx in the access log |
| **No zero-downtime deploy** | Containers are recreated | A few seconds of downtime per changed container |
| **No log shipping** | Logs live in Docker's json-file driver | Logs vanish when a container is pruned |

## The local stack

```bash
make infra        # Postgres, Redis, RabbitMQ, MinIO, MailHog
make infra-down   # stop
make infra-reset  # destroy volumes and recreate — destructive
```

Ports are offset from the defaults so this stack coexists with others: Postgres **5434**, Redis
**6380**, RabbitMQ **5673**, MinIO **9100**, MailHog **1026**.

## Processes per service

Every service has `bin/api`. Most also have `bin/worker`, `bin/cron` and `bin/relay` (31, 29 and 32 of 34).

| Command | What it is |
|---|---|
| `make run SERVICE=x PORT=n` | The API |
| `make worker SERVICE=x` | Event consumers |
| `make relay SERVICE=x` | Outbox relay |
| `make cron SERVICE=x` | The service's scheduled sweep |

On the box the shape is different, to fit memory: consumers run inside the API process
(`RUN_CONSUMERS_IN_API`), most sweeps run inside it too (`RUN_SWEEPS_IN_API`), and one shared
`edge-relay` container drains every outbox as `opsgrids_outbox`.

## Health

Every service exposes `/health` and `/health/ready`. Readiness checks exactly the dependencies a
service declares, once, in `create_app`: its database, Redis, or both. The gateways declare Redis
only. A service whose Redis is gone reports not ready, because every permission-changing write and
the gateway's rate limiter depend on it.

`GET /health/outbox` exists on every service that owns an outbox. It counts through the same role
the relay runs as (`opsgrids_outbox`, BYPASSRLS, SELECT and UPDATE on `outbox` and nothing else),
because through the application role it would report zero for the same reason a relay stalls.

```json
{ "pending": 10, "oldest_seconds": 133.09 }
```

It is deliberately not part of readiness. A backlog is not a reason to take a service out of
rotation: refusing traffic stops new events being written and does not drain the old ones.

## Who tells you something is wrong

`ops-monitor` is one container. Every 60 seconds it checks outbox lag, DLQ depth, API readiness,
disk, backup freshness and certificate expiry. It emails `OPS_ALERT_EMAIL` after three consecutive
failures, reminds every six hours, and sends one email on recovery. A daily digest arrives only
when nothing is firing. **A day with neither a digest nor an alert means the monitor is stuck.**

If `OPS_ALERT_EMAIL` or `SMTP_HOST` is empty, it pages nobody. Firing checks are still logged as
`ops.alert.firing`. The alert table, with where to look for each, is in the RUNBOOK under
"Alerts and what each means".

```bash
make edge-status HOST=… CONFIRM=…                          # everything running, disk, ops-monitor /status
make edge-logs HOST=… CONFIRM=… SERVICE=ops-monitor        # its own log
```

## Things that fail loudly

- **Redis unreachable at the gateway.** Rate limiting and permission-version checks fail closed:
  requests get `503`, not a pass.
- **Permission bump with Redis down.** The role change raises. It does not report success while the
  person keeps access.
- **Upstream unreachable.** The gateway returns a `503` problem document.
- **Permission resolution failing.** Refused, never forwarded.
- **Preflight.** A missing `.env` key, a placeholder secret, a memory total above the accepted
  ceiling, or a dirty checkout stops the deploy before anything changes.

## Things that fail silently

**A stalled outbox relay.** If the relay cannot see rows (for example it ran without `BYPASSRLS`),
it selects zero rows, logs nothing and loops. `attempts` never increments. The outbox grows and
every consumer starves. The ops-monitor `outbox.<schema>` check exists for this. First look:
`make edge-logs SERVICE=edge-relay`, and `edge_relay.schema.failed` in the output.

**Empty results that look like normal state.** A tenancy misconfiguration returns zero rows, and
the API shows an empty page with `200` or "Organization not found". No log line distinguishes it
from an empty tenant.

**Stock that disagrees with its ledger.** `operations` keeps stock as an append-only ledger with a
balance per bucket as its projection (ADR-0014). The nightly reconcile sweep compares them and never
repairs. Alert on the log events `inventory.reconcile.drift` and
`inventory.reconcile.organization_failed`. A pass that logs `organizations=0` on a box selling
Inventory means the discovery role lost its grant. Open findings:
`GET /api/v1/operations/inventory/discrepancies`. See [inventory/ledger.md](inventory/ledger.md).

**Dark-launched switches.** `CRM_CONVERSION_CLEANUP_MODE` and `ORDER_INVOICING_MODE` are in
`log_only` in production. They log what they would do and change nothing. The owner reads the log
lines and then flips them.

**A dead-letter queue nobody reads.** Consumers retry once, then park the message on
`{queue}.dlq`. The ops-monitor `dlq.<queue>` check emails on depth. `tools/dlq.py` peeks and
replays. A dead-lettered event in the money path is an incident.

## Logs

Structured, with a correlation id propagated from the gateway through every service. Search on
`correlation_id` to follow one request. On the box: `make edge-logs HOST=… CONFIRM=… SERVICE=<name>`.
Traces from before the header-duplication fix in [discoveries.md](discoveries.md) carry a doubled
value.

## Error tracking

Error tracking is **not wired**. No Sentry or OpenTelemetry SDK ships in any service, so there is
no DSN or OTLP endpoint setting and nothing reports an exception off the box. An unhandled error
reaches you only as an `error`-level structured log line and a 5xx in the edge access log. The
`SENTRY_DSN` and OTLP settings were removed in #156 because nothing read them and they implied
otherwise. Adding real error tracking means an SDK dependency (memory the box does not currently
have), a setting, and a test that an exception reaches it.

## Secrets

`.env` is gitignored. `.env.example` carries the keys with dummy values; the production keys are in
`infrastructure/edge/.env.example`, each with its source and format. Signing keys are generated by
`make devkeys` for development. On the box, secrets live in `infrastructure/edge/.env` (mode 600)
and the key file it points at. No backup contains `.env`; the owner keeps an off-box copy.

Two development defaults must never reach a shared environment: `internal_service_token`
(`dev-only-internal-token`) and the bootstrap Postgres password. Production refuses the
`dev-only-` and `ci-only-` placeholders.

## Database roles

`opsgrids_admin` runs migrations and owns the schemas (BYPASSRLS, nothing long-running connects as
it). Each service connects as its own NOBYPASSRLS role with DML on its own schema only.
`opsgrids_directory` serves the cross-tenant identity reads sign-in needs (BYPASSRLS, SELECT-only on
a short list of tables). `opsgrids_outbox` runs the relay and `/health/outbox`. The roles are
created by `init-db.sql` and by the deploy's reconcile-roles step; table grants are applied by each
service's `migrations/env.py` after every migration run (ADR-0002 §2).

## Backup and restore

`backup.sh` runs nightly from cron on the box. It dumps the whole database (gzipped `pg_dump`) and
tars Caddy's data volume, plus MinIO's when the `storefront` profile is on. With `BACKUP_AGE_RECIPIENT`
set it encrypts with `age` before upload. **With `BACKUP_S3_*` empty it stops after the local copy
and exits non-zero.** That is the current state of production.

Pre-deploy backups for the 2026-10-15 release exist only on the box. A backup nobody has restored is
a hypothesis: the drill is `make edge-restore-drill HOST=… CONFIRM=…`, and it needs an off-box dump
to restore. The full procedure is the RUNBOOK's "Restore from backup".

## Rollback

`make edge-rollback HOST=… CONFIRM=… [TO=<sha>]` redeploys the previous ref that actually served. It
is a full deploy, so it takes as long as one. It does not downgrade migrations. Do not roll back past
the release that sealed operator MFA factors: MFA sign-in returns 500 on the older code. Bundles
roll back separately and fast: `make edge-web-rollback`. Details: RUNBOOK, "Roll back a bad deploy".

Migrations have `downgrade()`, and `make migration-roundtrip` exercises each one. Never downgrade past
a migration whose data already exists in production.
