# Employee service

The first HRMS module and the Employee Master of the OpsGrids platform. Owns the `employee`
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

## The hierarchy (ADR-0006, the approval engine)

Employee is the only writer of reporting lines. Two columns support that: `dotted_line_manager_id`
(a second, informational reporting line — stored, never routed on) and `hierarchy_version`
(`BIGINT`, bumped by the database trigger `trg_employees_hierarchy_version` whenever
`reporting_manager_id`, `dotted_line_manager_id`, `status`, `full_name`, `employee_no` or
`location_code` changes — never by application code, so a concurrent edit can never lose its
increment to a stale Python read).

Setting `reporting_manager_id` or `dotted_line_manager_id` refuses a change that would close a
reporting cycle, direct or indirect. The check takes `pg_advisory_xact_lock` on the organization
first, then walks the *reporting* chain upward from the proposed manager (a recursive CTE, depth
capped at 32) — the lock is what stops two concurrent edits on disjoint rows from each looking
acyclic and composing a cycle together on commit.

All three employee events (`onboarded` / `updated` / `transitioned`) carry the **full** hierarchy
state after the change, not a diff: `employee_id`, `reporting_manager_id`,
`dotted_line_manager_id`, `status`, `location_code`, `display_name`, `employee_no`,
`hierarchy_version`. A consumer that missed or reordered an event converges on the next one it
sees.

`GET /internal/v1/hierarchy?organization_id=&cursor=&limit=` — keyset-paginated (default 500,
capped at 1000), guarded by `require_service_token`, mounted at the root and excluded from the
public OpenAPI document. Serves approval-service's one-time backfill for organizations that
already had employees when approval shipped. Tenant-bound to the named organization only —
there is no cross-tenant listing here.

## Permissions and roles (`hrms` product)

- `hrms.people.employee.read` (default OWN, max ORG)
- `hrms.people.employee.write` (default OWN, max ORG; write routes floor at ORG)

Role templates: **HR_ADMIN** (read + write at ORG) and **EMPLOYEE** (read at OWN, default).
The catalogue is guarded so a template cannot hold another product's permission.

## Publishes

- `hrms.people.employee.onboarded` — a new employment record was created.
- `hrms.people.employee.updated` — plain employment fields changed.
- `hrms.people.employee.transitioned` — the lifecycle status moved.

Each carries the full hierarchy state described above (ADR-0006 §2).

## Consumes

_None yet._

## Tenancy

Connects as `opsgrids_employee` (NOBYPASSRLS). Row-level security is live on `employees`,
`audit_logs` and `outbox`; `processed_events` is exempt (no organization column). Every request
runs through the tenant chokepoint, which binds the transaction to the caller's organization.
