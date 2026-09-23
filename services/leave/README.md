# Leave service

Part of the OpsGrids platform (HRMS product). Owns the `leave` schema.

```bash
make run SERVICE=leave PORT=8122    # API
make worker SERVICE=leave           # event consumer
make relay SERVICE=leave            # outbox relay
```

## Owns

- `leave_types` — an organization's leave types (code, name, paid/unpaid, annual quota,
  optional monthly accrual, optional carry-forward cap — all in half-days).
- `leave_ledger_entries` — the append-only balance ledger. A balance is always the sum of
  every posting for one `(employee, leave type)`; grants, accruals, consumption and reversals
  are each a new row. `UPDATE`/`DELETE` are revoked from the app role at the database.
- `leave_requests` — one employee's ask for leave over a date range. Routes through the shared
  `approval` service (ADR-0006), the same way `attendance.regularization_requests` does.
- `leave_settings` — one row per organization: the IANA timezone `domain.today_in` resolves
  "today" against, for the one rule in this service that needs a clock
  (`services.cancel_leave_request`'s "already ended" check).
- `employee_directory` — a thin, read-only copy of the reporting line (reporting manager,
  employment status), fed by employee-service's lifecycle events. Used to scope leave requests
  to a manager's team.

## Publishes

- `hrms.time.leave_type.created` / `.updated` / `.deleted`
- `hrms.time.leave_balance.granted`
- `hrms.time.leave_settings.updated`
- `hrms.time.leave.requested` — approval's consumer opens a `hrms.time.leave` request from this.
- `hrms.time.leave.applied` — this service's return leg once approval decides, mirroring
  `hrms.time.regularization.applied`.
- `hrms.time.leave.cancelled`

## Consumes

- `hrms.people.employee.onboarded` / `.updated` / `.transitioned` — maintains
  `employee_directory` (reporting manager, status), version-gated the same way approval's
  `hierarchy_nodes` copy is.
- `core.approval.request.opened` / `.decided`, filtered to `request_type = "hrms.time.leave"` —
  applies routing/decision outcomes to `leave_requests` and posts the ledger consumption or
  reversal.
