# Approval service

Part of the OpsGrids platform. Owns the `approval` schema. A **platform** service (ADR-0006 §1):
no `product` key in `app-gateway`'s route table, because every customer has approvals the way
every customer has members — `/api/v1/approvals`, port **8152**.

```bash
make run SERVICE=approval PORT=8152    # API
make worker SERVICE=approval           # event consumer
make relay SERVICE=approval            # outbox relay
bin/backfill-hierarchy --org <id>      # one-time per-organization hierarchy backfill
```

## Slices

This is **slice 1** of four (ADR-0006). It lands the read-only copy of the reporting line and
the endpoint that walks it. It does **not** yet route a request, decide one, delegate, escalate
on SLA, or expose any policy — those are slices 2–4, on top of tables (`approval_requests`,
`approval_steps`, `delegations`, `approval_policies`) this slice does not create.

## Owns

- `hierarchy_nodes` — one row per employee, `(organization_id, employee_id)` primary key: the
  reporting line (`reporting_manager_id`), the informational dotted line
  (`dotted_line_manager_id`, never routed), lifecycle `status`, and the `display_name` /
  `employee_no` / `location_code` an approval screen needs to render a person without a second
  call to employee-service. Written only by the hierarchy consumer and the one-time backfill,
  version-gated on `source_version` — never by a person, and never through this service's own
  API.
- `outbox`, `processed_events`, `audit_logs` — the platform mixins, present from the first
  migration like every other service.

## Publishes

Nothing yet. The hierarchy consumer only writes `hierarchy_nodes`; it emits no event of its own.
`core.approval.request.opened` / `.escalated` / `.decided` land in slices 2–4.

## Consumes

- `hrms.people.employee.onboarded`, `.updated`, `.transitioned` — employee-service's full-state
  lifecycle events. Applied to `hierarchy_nodes` as a version-gated upsert: a row is written only
  when its `hierarchy_version` is strictly greater than what is stored, and a payload with no
  `hierarchy_version` at all (an un-upgraded employee pod) is ignored rather than treated as
  version 0 (ADR-0006 §2, §2.1). See `app/consumers.py`'s module docstring for the current state
  of that contract — employee-service's side of it is landing in parallel with this slice.

## Endpoints

| Route | Guard | Notes |
|---|---|---|
| `GET /api/v1/approvals/chain/{employee_id}` | `core.approval.request.read` — OWN reaches only the caller's own chain, ORG reaches any | The resolved chain: a recursive walk over `hierarchy_nodes`, depth-capped at 32, cycle-aware (ADR-0006 §5) |

## The backfill

`bin/backfill-hierarchy --org <id> [--cursor <uuid>]` — a one-shot, per-organization export from
employee-service's `GET /internal/v1/hierarchy` (ADR-0006 §2.1): keyset pagination, 500 rows per
page, 10 s total / 2 s connect timeout, 5 retries with full-jitter exponential backoff on
connection errors, timeouts and 5xx only (never a 4xx), a service token minted per HTTP request,
100 ms between pages, one transaction per page. No progress table — the upsert is idempotent and
version-gated, so re-running from the start converges to the same state; `--cursor` resumes from
a cursor the previous run printed. **The worker's queue must be bound before this runs** — the
backfill binds `approval.events`'s queue itself, over AMQP, before its first HTTP call, so a
hierarchy change published during the backfill window is never lost to an unbound topic exchange
(ADR-0006 §2).
