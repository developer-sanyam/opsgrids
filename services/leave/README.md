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
- `hrms.time.leave.cancelled` — consumed by `approval`, not by this service: it withdraws a
  still-`pending` request's open step (`approval.services.withdraw_request`) so nobody is
  offered a decision on a request the employee already pulled back.

## Consumes

- `hrms.people.employee.onboarded` / `.updated` / `.transitioned` — maintains
  `employee_directory` (reporting manager, status), version-gated the same way approval's
  `hierarchy_nodes` copy is.
- `core.approval.request.opened` / `.decided`, filtered to `request_type = "hrms.time.leave"` —
  applies routing/decision outcomes to `leave_requests` and posts the ledger consumption or
  reversal.

## TEAM scope: what it actually shows, and why

`hrms.time.leave_request.read` at `TEAM` filters on `LeaveRequest.reporting_manager_id`, a
**snapshot** of `EmployeeDirectory.reporting_manager_id` taken once, at the moment the employee
applied — never re-read live (`app/models.py`'s `LeaveRequest` docstring; the same "a request
routes on the chain as it stood when it opened" principle ADR-0006 §9 states for approval's own
steps). Two consequences worth stating plainly, so they are a documented tradeoff rather than a
surprise found in production:

- **A manager who has since stopped managing that employee still sees the old request.** If Alice
  reported to Priya when she applied and is reassigned to a different manager the next day,
  Priya's TEAM-scoped read still reaches that one request — it is frozen at apply time, exactly
  like the approval routing it mirrors. A reorg does not retroactively rewrite who can see what
  was already asked.
- **The reverse gap: a newly-assigned manager cannot see a report's older requests.** The new
  manager's TEAM scope only reaches requests raised *after* the reassignment (whichever
  `reporting_manager_id` was current at each one's own apply time), not the report's whole
  history.
- **The copy can lag the source.** `EmployeeDirectory` is fed by employee's own lifecycle events,
  asynchronously; a request applied for in the window between a manager change and this
  service's consumer catching up snapshots whichever manager the copy still shows — the same
  eventually-consistent tradeoff `approval.hierarchy_nodes` already accepts (ADR-0006 §2), not a
  new one this service introduces.

None of this affects ORG-scoped reads (HR always sees every request, current org-wide) or the
requester's own OWN-scoped read of their own history. It is a property of the TEAM rung alone,
and the fix — if one is ever wanted — is a live join against `EmployeeDirectory` at read time
instead of a snapshot, at the cost of the query no longer being a single indexed filter on
`leave_requests` itself. Left as a snapshot for this slice, matching the pattern already proven
correct for approval's own routing.
