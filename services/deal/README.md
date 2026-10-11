# Deal service

The pipeline half of the **CRM** product ("OpsGrids Revenue"): deals and the append-only activity
timeline. It owns the `deal` schema and is one of three CRM processes, beside `customer` (the
account book) and `lead` (intake). What it holds, the routes it serves, the events it publishes and
the permissions it declares are written down once, in [`docs/crm.md`](../../docs/crm.md); this file
only says how to run it.

## Run

```bash
make infra                          # postgres, redis, rabbitmq, minio, mailhog (once)
make migrate                        # every service's migrations
make run SERVICE=deal PORT=8131     # the API; the gateway routes /api/v1/deals here
make relay SERVICE=deal             # the outbox relay: its own process, always
make worker SERVICE=deal            # starts app/consumers.py
```

The consumer (`app/consumers.py`, queue `deal.events`) has one handler since CRM R2.4:
`crm.lead.conversion_aborted`, which soft-deletes an aborted conversion's pristine orphan deal
(ADR-0038 D-1) once `CRM_CONVERSION_CLEANUP_MODE=on`; the default `log_only` only logs. On the edge
box it runs inside the API process (`RUN_CONSUMERS_IN_API`); `make worker SERVICE=deal` runs it
standalone. `docs/crm.md` §10 has the detail.

## Settings

`app/config.py` adds nothing to the platform's shared settings, and deal calls no other service.

## Verify

Tests and mypy run per service, because `import app` is ambiguous across services:

```bash
cd services/deal
uv run pytest tests -q -p no:cacheprovider
uv run mypy app
```

## Demo data

`tools/demo/seed_crm.py` seeds customer and deal through the real write paths into a demo
organization (its header shows the arguments). Point `DATABASE_URL` at an isolated development
database such as `opsgrids_wt` (`make e2e-db` creates it), never at `opsgrids` or the box.

## Read next

[`docs/crm.md`](../../docs/crm.md) ·
[ADR-0017](../../docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md) ·
[ADR-0038](../../docs/adr/0038-crm-programme-decisions.md) · the contract,
[`libs/contracts/openapi/deal.json`](../../libs/contracts/openapi/deal.json).
