# Employee service

The first HRMS module and the Employee Master of the Atmora platform. Owns the `employee`
schema. This is the canonical record of who works for an organization; account-service's
`membership.employee_id` points at an `employees.id` here, by plain UUID with no cross-schema
foreign key.

```bash
make run SERVICE=employee PORT=8120    # API
make worker SERVICE=employee           # event consumer (none registered this slice)
make relay SERVICE=employee            # outbox relay
```

## Scope of this slice — employment record only, no sensitive PII

The platform rule "PII is encrypted and masked by default" is load-bearing, and there is **no
PII envelope helper in platform-core yet**. So this first slice deliberately stores only
low-sensitivity employment and org data — name, employee number, designation, department,
location, employment type, work mode, joining date, reporting line and lifecycle status.

The sensitive PII — **date of birth, personal contact details, PAN / Aadhaar / UAN / ESI, bank
details and documents** — is **deferred to slice 2**, which lands the platform PII envelope
(encryption at rest, masking by default, an audit row on every unmask). None of those fields
exist in the schema or the API contract yet, on purpose.

## Owns

- **`employees`** — one row per person per organization: the employment core and the lifecycle
  status. `organization_id` on every row, indexed tenant-first; soft-deleted, never hard.

The lifecycle is a small state machine (`app/domain.py`):
`probation → active`, `probation → exited`, `active → on_notice`, `active → exited`,
`on_notice → active`, `on_notice → exited`. `exited` is terminal.

## Permissions and roles (`hrms` product)

- `hrms.people.employee.read` (default OWN, max ORG)
- `hrms.people.employee.write` (default OWN, max ORG; write routes floor at ORG)

Role templates: **HR_ADMIN** (read + write at ORG) and **EMPLOYEE** (read at OWN, default).
The catalogue is guarded so a template cannot hold another product's permission.

## Publishes

- `hrms.people.employee.onboarded` — a new employment record was created.
- `hrms.people.employee.updated` — plain employment fields changed.
- `hrms.people.employee.transitioned` — the lifecycle status moved.

## Consumes

_None yet._

## Tenancy

Connects as `atmora_employee` (NOBYPASSRLS). Row-level security is live on `employees`,
`audit_logs` and `outbox`; `processed_events` is exempt (no organization column). Every request
runs through the tenant chokepoint, which binds the transaction to the caller's organization.
