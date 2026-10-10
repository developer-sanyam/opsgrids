# Customer service

The account-book half of the **CRM** product ("OpsGrids Revenue"): companies, contacts, the rep
directory and the Customer 360. It owns the `customer` schema and is one of three CRM processes,
beside `deal` (the pipeline) and `lead` (intake). What it holds, serves, publishes and consumes is
written down once, in [`docs/crm.md`](../../docs/crm.md); this file only says how to run it.

## Run

```bash
make infra                              # postgres, redis, rabbitmq, minio, mailhog (once)
make migrate                            # every service's migrations
make run SERVICE=customer PORT=8130     # the API; the gateway routes /api/v1/customers and
                                        # /api/v1/contacts here
make relay SERVICE=customer             # the outbox relay: its own process, always
make worker SERVICE=customer            # the event consumer, standalone
```

On the edge there is no separate worker container: the consumer runs inside the API process when
`RUN_CONSUMERS_IN_API` is on, and `make worker` is the rollback shape. It has a single binding;
`docs/crm.md` §2 says which.

## Settings that matter

Each setting is read from the environment under its upper-cased name (`app/config.py`).

| Setting | Default | What it does |
|---|---|---|
| `RUN_CONSUMERS_IN_API` | `false` | runs the consumer inside the API process (compose turns it on) |
| `DEAL_INTERNAL_URL` | `http://localhost:8131` | where the Customer 360 asks deal for opportunities |
| `NOTIFICATION_INTERNAL_URL` | `http://localhost:8151` | where it asks notification for channel standing |
| `CUSTOMER360_UPSTREAM_TIMEOUT_SECONDS` | `2.0` | the budget of each 360 section and the ceiling of the whole fan-out |
| `REVEAL_BURST_LIMIT` | `30` | real phone numbers one user may reveal per window; shared with `lead` |
| `REVEAL_BURST_WINDOW_SECONDS` | `600` | the length of that window |
| `REVEAL_DAILY_LIMIT` | `200` | real phone numbers one user may reveal per UTC day |

## Verify

Tests and mypy run per service, because `import app` is ambiguous across services:

```bash
cd services/customer
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
[`libs/contracts/openapi/customer.json`](../../libs/contracts/openapi/customer.json).
