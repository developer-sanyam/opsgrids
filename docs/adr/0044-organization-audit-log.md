# ADR-0044: The organization audit log is read per service and merged in the dashboard

**Status:** Proposed (branch `feat/org-audit-log`) · **Date:** 2026-10-15 ·
**Changes:** `libs/platform-core/src/platform_core/audit_read.py` (+ `audit_read_testing.py`), `app/api/audit.py`
in nine services, both gateway route tables, `services/account/app/catalogue.py` (`ORG_ADMIN`),
`frontend/opsgrids-dashboard` (`/admin/audit`), `frontend/opsgrids-client` (`useAdminLinks`). No migration, no
event, no new container.

Builds on [ADR-0000](0000-phase-0-decisions.md) §10 (no cross-schema reads or foreign keys),
[ADR-0002](0002-row-level-security.md) (RLS is a net, the selector filter is the control) and
[ADR-0007](0007-permission-rollout-to-live-tenants.md) (new grants reach live tenants only by reconcile).

## Context

Customers had no audit-log page. #175 removed a dead "Audit Logs" link because no organization-scoped audit read
existed: the only read was console's operator-only `GET /admin/audit`. Audit rows are not central. Every service
writes `audit_logs` in its own schema (`platform_core.audit.AuditLogMixin`), in the same transaction as the change,
and PII reveals (`entity.sensitive_read`) are written by customer, lead, employee, operations, notification and
storefront, not by account.

## Options

1. **A central audit store fed by events or the outbox.** Every service would have to emit an audit event beside
   every audit row (hundreds of call sites), a consumer would have to project them, and a new table would duplicate
   what each schema already holds. It also adds a consumer to a box at its memory ceiling.
2. **One service reads the others' schemas.** Forbidden by ADR-0000 §10.
3. **Aggregate in the gateway.** The gateway owns no business logic and no tables, and fan-out with per-source
   cursors is business logic.
4. **Each service serves its own slice; the dashboard merges.** Chosen.

## Decision

- `platform_core.audit_read.build_audit_router(AuditLog, service=…, product=…)` mounts
  `GET /api/v1/audit/<service>` over the service's own `audit_logs`. It is read-only, keyset-paged on
  `(at, id)` newest first, and filters on `from`/`to` (instants with an offset), `actor_id`, `action` and `entity`.
- It filters on the caller's `organization_id` **in the query**. RLS is underneath as the net. The shared contract
  test runs the selector as the BYPASSRLS role to prove the filter alone keeps other organizations out.
- It requires `core.identity.audit.read` at ORG scope. This code already existed in the `core` catalogue ("Read the
  audit trail") and `ORG_OWNER` already held it. Services enforce a `core` code minted by account the same way billing
  enforces `core.billing.invoice.read`. `ORG_ADMIN` now holds it too. A `core` code cannot sit in a product's admin
  template without `cross_product`, and the log is organization-wide rather than per product, so the grant belongs in
  `core`'s own administrator role.
- It never returns `before`/`after`, `ip`, `user_agent`, `session_id` or the operator's identity string. Each entry
  carries the **names** of the fields it touched: keys whose value changed, filtered to identifier-shaped names
  because a key can be data. A reveal names the fields that were opened. `by_operator` says OpsGrids staff acted: only an
  `acting_as` with the `operator:` prefix counts. `acting_as` also names system and service actors (`lead`,
  `payment`, sweeps), and those read as system entries, not staff.
- Sources in this change: `account` (ungated) and `customer`, `deal`, `lead` (crm), `employee` (hrms),
  `operations`, `notification` (engage), `storefront` and `finance`. Each gateway prefix carries its product's
  gate, so an inactive product answers 402 and the dashboard skips it. `tests/infra/test_route_table_parity.py`
  holds the two route tables and the services' OpenAPI documents together.
- The dashboard (`src/lib/auditFeed.ts`) keeps one buffer and cursor per source. It tops every live source up to a
  page before taking the newest heads, so the merged order is exact and has no gaps. A failed source is named on
  the page and the rest still show.

## Consequences

- Adding a source takes three lines: `app/api/audit.py`, one router in `main.py`, and one prefix in each route
  table. It also needs `tests/test_org_audit_log.py`, which runs the shared contract.
- Second batch (`feat/org-audit-log-rest`): `approval`, `document`, `subscription`, `billing`, `payment` (ungated),
  `attendance`, `leave` (hrms), `project`, `timesheet`, `standup` (pulseboard), `projects`, `support`, `marketing`
  and `itsm`. `auth` is a source too, but only for its sign-in family (`only_actions`: password, OAuth and SSO
  sign-in, password change and its failure) and each row is *bare*: actor, action and time, with no entity id (a
  sign-in row's entity id is the session id), fields, reason or operator flag. Its MFA and step-up rows stay out.
  Not sources: `catalog` and `product` (their rows belong to the platform organization, not a customer's),
  `metering` (writes no audit rows) and `scheduler`, `config`, `eventbridge`, `quotation`, `console`.
  Sign-out, password-reset requests and failed sign-ins write no audit row today (events only; a failed sign-in
  has no organization), so they do not appear until `auth` writes them.
- A page costs one request per active source, about ten at most. That is acceptable for an admin screen. A central
  store would be the next step if the source count or volume grows.
- Existing organizations' `ORG_ADMIN` roles gain the grant only after `make edge-reconcile PRODUCTS=core` (dry run
  first, then `APPLY=1`). Owners already hold it.
