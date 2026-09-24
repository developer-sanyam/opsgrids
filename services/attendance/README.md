# Attendance service

Part of the OpsGrids platform. Owns the `attendance` schema. A process in the `hrms` product,
alongside employee-service. Runs on `:8121` (see `services/app-gateway/routes.json`).

```bash
make run SERVICE=attendance PORT=8121    # API
make worker SERVICE=attendance           # event consumer
make relay SERVICE=attendance            # outbox relay
bin/backfill-team --org <id>             # one-time, per organization: seed the TEAM projection
```

## Owns

- **`shifts`** — an organization's shift definitions (a named window with a grace period).
- **`geofences`** — the allowed area for a field punch at a location, as a centre
  latitude/longitude and a radius in metres. Containment is a PostGIS `ST_DWithin` query at
  check-in, not an ORM geometry type.
- **`attendance_records`** — one person's attendance for one day (check-in/out, source, work
  mode, status). At most one row per employee per work date. `employee_id` is a plain UUID into
  `employee.employees.id` — no cross-schema foreign key.
- **`regularization_requests`** — a self-service ask to correct a day, routed through the
  approval engine (ADR-0006 §11).
- **`team_membership`** — attendance's own copy of the reporting line: one row per employee, the
  manager they report to now, their lifecycle status, and the `hierarchy_version` it came from.
  It is what TEAM scope reads (below).
- **`signal_settings`**, **`punch_signals`**, **`attendance_flags`** — the anti-spoofing stack
  (below).

## Scope (product `hrms`)

`OWN → AttendanceRecord.employee_id`, `LOCATION → AttendanceRecord.location_code`,
`TEAM → TeamMembership.manager_employee_id` — a manager's **current direct reports**.

The reporting line is employee-service's fact, and attendance neither reads that schema nor calls
it on the read path. A TEAM-scoped list joins `team_membership`, which is fed by:

1. the three `hrms.people.employee.*` events, each carrying the employee's full hierarchy state,
   applied through an upsert gated on `hierarchy_version` being strictly greater than the stored
   one (so redelivery, reordering and a backfill racing the live stream all converge); and
2. `bin/backfill-team --org <id>`, which pages employee's internal hierarchy export
   (`GET /internal/v1/hierarchy`, service token with the `org` claim bound) for organizations whose
   employees have not produced an event since the service started listening. Run it once per
   existing organization after the new worker is up; it binds the worker's queue before it reads
   anything, is idempotent, and resumes with `--cursor`.

What TEAM means here: a manager sees their reports and **not** their own record (as in
employee-service and `ScopeContext.apply`); an `exited` employee is in nobody's team (HR at ORG
still sees them); a manager change re-scopes every one of that employee's records at once, past
days included; a loop in the data is harmless because nothing walks the line. Until an
organization has been backfilled, a TEAM-scoped caller sees **nobody** — never everybody.

## Anti-spoofing signals (R22)

Observe-only and **off by default**: an organization with no settings has every rule off, and with
no rule on nothing is stored. A rule that fires writes a flag; it never refuses or alters a punch.

`POST /attendance/check-in` and `/check-out` accept optional client claims — `device_id_hash`,
`location_accuracy_m`, `mock_location` — and the server adds the client IP (the same source the
audit trail uses) and a coarse user-agent class. Unknown fields are refused. Four rules, each
switched on per organization by `PUT /attendance/signal-settings` (`hrms.time.shift.write:ORG`):

| Rule | Fires when |
|---|---|
| `mock_location` | the client says the fix came from a mock provider |
| `impossible_travel` | two consecutive located punches imply a speed above the organization's ceiling (haversine, less both fixes' reported accuracy) |
| `shared_device` | a different employee used the same device within the organization's window |
| `ip_outside_allowed` | an *office* punch came from outside every allowed CIDR range |

Flags are read at `GET /attendance/flags` (`hrms.time.attendance_flag.read`, floor TEAM), narrowed
exactly as the attendance record they describe is. **The IP address and device id are personal
data**: sealed at rest with `platform_core.pii`, masked in every response, revealed only by
`POST /attendance/signals/{id}/unmask` (`hrms.time.attendance.read_sensitive` — dangerous, and a
second factor), which writes an `entity.sensitive_read` audit row on every call.

Not built, deliberately: an organization setting that makes a chosen flag **block** a punch (it
needs an appeal path first); a retention or purge job for `punch_signals`; and any trust in the
client address beyond what the gateway establishes.

## Publishes

- `hrms.time.attendance.checked_in` / `.checked_out` / `.regularized`
- `hrms.time.regularization.requested` / `.applied`
- `hrms.time.shift.created` / `.updated` / `.deleted`
- `hrms.time.geofence.created` / `.updated` / `.deleted`

## Consumes

- `core.approval.request.opened` / `.decided` — the regularisation loop (ADR-0006 §12).
- `hrms.people.employee.onboarded` / `.updated` / `.transitioned` — the reporting line behind
  TEAM scope.
