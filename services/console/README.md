# Operator console service

Part of the OpsGrids platform. Owns the `console` schema. Port **8170**.

The operator console's read model of commerce. It folds the commerce transition-event stream into
cross-tenant projections and serves the staff read API on the **admin gateway** — MRR,
trial-conversion, churn, per-org commercial state — with overrides that delegate to the owning
service. It never writes another service's schema (ADR-0003 §2, ADR-0004 §4).

```bash
make run SERVICE=console PORT=8170    # API
make worker SERVICE=console           # event consumer (folds the transition stream)
make relay SERVICE=console            # outbox relay (console publishes nothing today)
```

## Owns

Three **projections**, all tenant-scoped and RLS-policied, written by the consumer one organization
at a time (tenant GUC per message) and read cross-tenant on the SELECT-only BYPASSRLS
`opsgrids_console_directory` role:

- **`org_commercial_state`** — per-org snapshot: `mrr_minor`, `currency`, `active_products`,
  `subscription_states`, `trial_ends_at`, `locked`. MRR is the running sum of *active*
  subscriptions at their pinned plan-version price.
- **`revenue_events`** — **append-only** transition log; the time series MRR, trial-conversion and
  churn are computed from (`REVOKE UPDATE, DELETE`, re-applied in `migrations/env.py`).
- **`usage_summary`** — per-org, per-meter, per-period usage rollup.

## Consumes

Coupled to the producing services by **routing key and documented payload only — never an import**,
so it builds and runs before those services' PRs are merged.

- `commerce.subscription.{trial_started,activated,renewed,upgraded,downgraded,past_due,suspended,cancelled}`
  → `org_commercial_state` + `revenue_events`. Drives MRR (pinned price resolved from catalog's
  `GET /internal/v1/plan-versions/{id}`), trial-conversion, churn, and `locked` on suspend.
- `commerce.invoice.{finalized,voided}` → `revenue_events`.
- `commerce.credit.{granted,consumed,reversed}` → `revenue_events` (the comp/credit view).
- `commerce.usage.recorded` → `usage_summary`.

## Serves (admin gateway, `staff_only`)

- `GET /api/v1/admin/organizations` — every org's state, with platform MRR/trial/churn metrics.
- `GET /api/v1/admin/subscriptions`, `GET /api/v1/admin/usage`, `GET /api/v1/admin/audit`.
- `POST /api/v1/admin/overrides` — **delegates** to the owning service's `/internal/v1` under a
  service token (suspend → subscription; comp grant → billing); records a local audit row only.

`require_operator` is defence in depth behind the gateway's staff gate: a non-operator-audience
token gets 404, the same answer an unknown path gives.
