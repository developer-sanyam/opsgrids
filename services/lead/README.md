# Lead service

The intake half of the **CRM** product ("OpsGrids Revenue"): leads, the assignment rule, and the
conversion of a lead into a company, a contact and a deal. It owns the `lead` schema and is one of
three CRM processes, beside `customer` (the account book) and `deal` (the pipeline). What it holds,
the routes it serves, the events it publishes and the permissions it declares are written down once,
in [`docs/crm.md`](../../docs/crm.md); this file only says how to run it.

## Run

```bash
make infra                          # postgres, redis, rabbitmq, minio, mailhog (once)
make migrate                        # every service's migrations
make run SERVICE=lead PORT=8135     # the API; the gateway routes /api/v1/leads here
make relay SERVICE=lead             # the outbox relay: its own process, always
```

Lead consumes no events: there is no `app/consumers.py` and no worker entrypoint. A conversion is
synchronous and needs `customer` and `deal` running, because `:convert` calls both over the internal
API.

## Settings that matter

Each setting is read from the environment under its upper-cased name (`app/config.py`).

| Setting | Default | What it does |
|---|---|---|
| `CUSTOMER_INTERNAL_URL` | `http://localhost:8130` | where `:convert` asks customer for the company and contact, and where owner ids are validated |
| `CUSTOMER_TIMEOUT_SECONDS` | `5.0` | the budget of each call to customer |
| `DEAL_INTERNAL_URL` | `http://localhost:8131` | where `:convert` asks deal for the deal |
| `DEAL_TIMEOUT_SECONDS` | `5.0` | the budget of each call to deal |
| `REVEAL_BURST_LIMIT` | `30` | real phone numbers or emails one user may reveal per window; shared with `customer` |
| `REVEAL_BURST_WINDOW_SECONDS` | `600` | the length of that window |
| `REVEAL_DAILY_LIMIT` | `200` | real values one user may reveal per UTC day |

## Verify

Tests and mypy run per service, because `import app` is ambiguous across services:

```bash
cd services/lead
uv run pytest tests -q -p no:cacheprovider
uv run mypy app
```

## Demo data

There is no lead seed: `tools/demo/seed_crm.py` seeds customer and deal only. When you create leads
by hand, point `DATABASE_URL` at an isolated development database such as `opsgrids_wt`
(`make e2e-db` creates it), never at `opsgrids` or the box.

## Read next

[`docs/crm.md`](../../docs/crm.md) ·
[ADR-0017](../../docs/adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md) ·
[ADR-0038](../../docs/adr/0038-crm-programme-decisions.md) · the contract,
[`libs/contracts/openapi/lead.json`](../../libs/contracts/openapi/lead.json).
