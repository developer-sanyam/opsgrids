# Insights service

Part of the OpsGrids platform. Owns the `insights` schema. The cross-product projection behind
the role dashboards ([ADR-0020](../../docs/adr/0020-insights-projection.md)) — a read-only,
per-organization history of what the source services only ever hold as current state.

A **leaf**: no service ever calls it, and it reads no other schema directly, only each source's
own `/internal/v1` export route under a service token it mints itself. Only the SPAs read its
public API, so its failure degrades dashboards and nothing else.

```bash
make run SERVICE=insights PORT=8110    # API
make worker SERVICE=insights           # the event consumer, the snapshot sweep and the backfill
                                        # runner, together (app/worker.py)
make relay SERVICE=insights            # outbox relay (kept for /health/outbox parity; insights
                                        # publishes nothing)
```

On the edge box these three loops fold into the API process itself
(`RUN_CONSUMERS_IN_API`, `app/main.py`) instead of running as a separate `insights-worker`
container — see `docs/adr/0020-insights-projection.md` §1.

`bin/backfill` triggers the resumable runner by hand: `bin/backfill` (sync `org_profile` once),
`bin/backfill --org <id>` (a manual run for every registered entity), `bin/backfill --verify` (a
per-organization report).

## Owns

- `org_profile` — one row per organization: timezone, currency, fiscal year, folded from
  `core.org.*` and account's organization export.
- `daily_snapshots`, `metric_coverage` — captured state and per-metric history coverage; the
  machinery I2-I9's own entity facts and snapshot captures plug into.
- `backfill_runs` — the resumable backfill runner's own bookkeeping (`app/backfill.py`).
- `outbox`, `processed_events`, `audit_logs` — the platform scaffold. `outbox` stays empty
  forever (insights publishes nothing); `audit_logs` too (no user-initiated write).

I2-I9 each add their own `*_facts` table(s): `invoice_facts`/`payment_facts`/`purchase_facts`
(I2, finance), `deal_facts` (I3), `lead_facts`/`company_facts` (I4), `purchase_order_facts` (I5,
operations), `task_facts` (I6, projects), `enquiry_facts`/`enquiry_line_facts` (I7, storefront),
and the HR facts (I9, optional). See `docs/insights/data-model.md`.

## API

`GET /api/v1/insights/{summary,series,activity,rankings}` — see `docs/insights/api.md`. Every
widget is gated per request by `app/access.py:gate_*`, never by a route-level permission: a
single call can span every product the caller holds. I1 ships the full response contract; every
metric that passes its gate reads `"unavailable"` until its own slice lands a fact table.

## Publishes

Nothing. `outbox`/`app/relay.py` exist only for `/health/outbox` parity with every other service.

## Consumes

| Routing key | Slice | Folds into |
|---|---|---|
| `core.org.created` | I1 | `org_profile` |
| `core.org.updated` | I1 | `org_profile` |

I2-I9 each bind their own product's routing keys — see `docs/insights/events.md`.
