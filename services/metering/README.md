# Metering service

Part of the Atmora platform. Owns the `metering` schema — the **append-only usage sink** of
commerce (ADR-0004 §7). Product services record consumption here from the moment they exist, with
`is_billable=false`; billing starts reading a meter only once a plan version in `catalog` prices
it. A meter reading not taken is gone, so recording begins on day one and billing is a later,
separate decision made in the catalogue — never a change here.

```bash
make run SERVICE=metering PORT=8162    # API
make worker SERVICE=metering           # event consumer (none registered yet)
make relay SERVICE=metering            # outbox relay
```

## Owns

- **`usage_events`** — the append-only meter readings (`organization_id`, `meter_key`,
  `subject_ref`, `quantity`, `unit`, `occurred_at`, `is_billable`, `idempotency_key`). Never
  updated or deleted: `UPDATE`/`DELETE` are revoked from the app role at the database (a table
  privilege, like `audit_logs`), and a correction is a new compensating event. Idempotent ingest
  rests on the unique `(organization_id, idempotency_key)`.
- **`meters`** — the global, un-policied reference catalogue of *what a meter is* (`meter_key`,
  `product_key`, `unit`, `description`). Platform vocabulary, read by every tenant, seeded by a
  migration — like `product.products` and `catalog.plans` (ADR-0002 §5).
- **`usage_rollups`** — tenant-scoped, **rebuildable** aggregates derived from `usage_events`. A
  cache of the append-only log, never a second source of truth; can be dropped and recomputed.

## Endpoints

- **Internal (service-token, root-mounted):** `POST /internal/v1/usage` — batch ingest, idempotent
  on `idempotency_key`. Product services emit here server-to-server; a batch is grouped by
  organization and each group is written under its own tenant GUC.
- **Customer (product gateway `/api/v1/usage`):** `GET /api/v1/usage` — read-only current-period
  usage for the caller's organization, one line per meter. Guarded by
  `core.product.activation.read`; the period is the UTC calendar month until a subscription's own
  window is wired in.

## Publishes

- **`commerce.usage.recorded`** — one thin event per newly-recorded usage event
  (`organization_id`, `usage_event_id`, `meter_key`, `subject_ref`, `quantity`, `unit`,
  `is_billable`). A retry (duplicate idempotency key) stages no second event. Billing subscribes
  only once a plan prices the meter.

## Consumes

_None yet._
