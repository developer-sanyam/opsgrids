# Support service

Part of the Atmora platform. Owns the `support` schema — the shared support desk behind the
`support` product (`frontend/product-support`).

```bash
make run SERVICE=support PORT=8180     # API (gateway routes /api/v1/support here)
make worker SERVICE=support            # event consumer
make relay SERVICE=support             # outbox relay
```

## Owns

- **`tickets`** — one support request and its lifecycle (`new → open → pending → on_hold →
  solved`, with reopen). Carries the requester snapshot, priority, channel, the assigned agent's
  public ref, tags, and the **real SLA state** the UI labels: `sla_due` and `sla_breached`.
- **`ticket_messages`** — the conversation thread. **Append-only**: a correction is a new
  message, never an edit or delete.
- **`agents`** — the people who work the queue, with their rolling desk metrics (open load,
  median first response, CSAT, solved-in-7-days).
- **`sla_policies`** — the one SLA policy per organization, with per-priority targets.

Every table carries `organization_id` and is protected by row-level security (the tenant-GUC
policy); `processed_events` is deliberately unpolicied. There is no money in this domain.

### API (under the gateway's `/api/v1/support` prefix)

| Method & path | Returns |
|---|---|
| `GET /support/tickets` | `Page<Ticket>` — search (`q`), filter (`status`, `priority`, `assignee`=`unassigned`\|ref), `sort`, pagination |
| `GET /support/tickets/{number}` | one `Ticket` with its thread |
| `GET /support/agents` | `Agent[]` |
| `GET /support/sla-policy` | the `SlaPolicy` |
| `GET /support/overview` | KPIs (open, breaching, first-response median, CSAT), 7-day volume, priority & status breakdowns |
| `POST /support/tickets/{number}/reply` | append a message (append-only), reopening a solved ticket |
| `POST /support/tickets/{number}/assign` | assign or unassign |

Reads need `support.desk.ticket.read`; writes need `support.desk.ticket.write`. The queue is
shared, so both floor at **ORG** scope — per-assignee narrowing is a query filter, not a
security boundary.

Demo data mirroring the SPA fixtures: `tools/demo/seed_support.py` (+ `support_fixtures.json`).

## Publishes

- `support.desk.ticket.created` — a ticket was opened.
- `support.desk.ticket.replied` — a message was appended to a thread.
- `support.desk.ticket.assigned` — a ticket's assignee changed.

## Consumes

_None yet._
