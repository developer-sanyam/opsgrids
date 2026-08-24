# Attendance service

Part of the Atmora platform. Owns the `attendance` schema. A process in the `hrms` product,
alongside employee-service. Runs on `:8121` (see `services/app-gateway/routes.json`).

```bash
make run SERVICE=attendance PORT=8121    # API
make worker SERVICE=attendance           # event consumer
make relay SERVICE=attendance            # outbox relay
```

## Owns

- **`shifts`** — an organization's shift definitions (a named window with a grace period).
- **`geofences`** — the allowed area for a field punch at a location, as a centre
  latitude/longitude and a radius in metres. Containment is a PostGIS `ST_DWithin` query at
  check-in, not an ORM geometry type.
- **`attendance_records`** — one person's attendance for one day (check-in/out, source, work
  mode, status). At most one row per employee per work date. `employee_id` is a plain UUID into
  `employee.employees.id` — no cross-schema foreign key.

## Scope (product `hrms`)

`OWN → AttendanceRecord.employee_id`, `LOCATION → AttendanceRecord.location_code`. **TEAM is
deferred**: a manager reading their team's attendance needs the reporting hierarchy, which lives
in employee-service and will arrive as an employee-events projection in a later slice. Until
then a TEAM-scoped read is refused.

## Publishes

- `hrms.time.attendance.checked_in` / `.checked_out` / `.regularized`
- `hrms.time.shift.created` / `.updated`
- `hrms.time.geofence.created` / `.updated`

## Consumes

_None yet. The employee-events projection that unlocks TEAM scope lands in a later slice._

## Out of scope for this slice

The approval-workflow engine (regularisation here is a direct, audited status change) and the
field-attendance anti-spoofing stack beyond the distance check (device, IP, mock-location).
