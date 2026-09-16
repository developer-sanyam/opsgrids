# ADR-0006 — The approval engine: who may say yes, who says it when they do not, and how the answer gets back

**Status: Proposed 2026-09-16.** To be implemented in a new `approval` service, with supporting
changes in `employee-service` (hierarchy state on events, cycle check, backfill endpoint) and
`attendance-service` (regularisation requests and the decision consumer), across four slices.

Builds on [ADR-0000](0000-phase-0-decisions.md) §5 (permission grammar and the per-product
catalogue corollary), §6 (the scope ladder), §7 (the event envelope) and §10 (shared-schema tenancy
with RLS); on [ADR-0002](0002-row-level-security.md) (the transaction-local GUC, the bypass roles,
`processed_events` exempt from policy); on [ADR-0003](0003-surfaces-and-authorization-topology.md)
§3 (scope is enforced server-side, a negative-authz test per endpoint); and on
[ADR-0005](0005-dunning-and-grace.md) §6 (a due-work table drained by a sweep). Supersedes nothing.

**Depends on [ADR-0007](0007-permission-rollout-to-live-tenants.md)** (how a new permission reaches a
role template that already exists in a live tenant). Without it, §11's grants land in new
organizations only and every endpoint here returns 403 in every existing one.

> **Amended 2026-09-16 after design review, before merge.** This ADR is not yet implemented, so the
> amendments are made in place rather than as a superseding section. Every section changed carries an
> **Amended** note saying what moved and why. The companion
> [persistence design](../approval-engine-data-model.md) was verified against a real database and
> **wins where it and this ADR disagreed**; §2, §3 and §8 below are corrected to match it. The
> [screen specifications](../design/approval-engine-screens.md) §9 raised five contract dependencies
> and seven open questions; those that change the backend contract are resolved in §2, §3, §6, §7,
> §11 and §12. Three consequential edits fall outside this ADR's ownership and are listed in §14.

---

## Context

The Wave 0 exit criterion is a sentence with four nouns in it: *a tenant is provisioned, an org
hierarchy is loaded, and an approval routes with delegation and SLA escalation.* The first real
consumer is attendance regularisation. Today that is not an approval at all — `POST
/attendance/{id}/regularize` is a direct status change gated `hrms.time.attendance.write` at
`minimum=ORG`, and its docstring says so out loud: *"There is **no approval workflow engine in this
slice** (that is a later one)."* An employee who forgot to punch in cannot ask anyone for anything;
they ask HR in a chat message and HR edits the row.

Every product on the roadmap needs the same machine. Leave in HRMS, expense claims in finance,
purchase orders in operations, discount approvals in CRM. The shape is identical each time: a
request that belongs to a product, a person who asked, a chain of people who may answer, an
answer that must travel back to the product that owns the row.

What already exists, verified against the code rather than assumed:

| Fact | Evidence |
|---|---|
| The reporting line is `employee.employees.reporting_manager_id → employees.id`, with a self-reference check and nothing else — **no cycle check, no transitive walk** | `employee/app/services.py` `update_employee`: refuses `new_manager == employee.id`, then `_assert_manager_exists` |
| `account.memberships.manager_membership_id` is a **second, unsynchronised** manager field | `account/app/models.py`; ADR-0002 notes its index |
| The token's `sref` is `membership.employee_id` for **every** audience, not only HRMS | `auth/app/services.py:273` `subject_ref=membership.employee_id` |
| The gateway forwards **all** of a membership's grants regardless of the token's product | `account/app/api/internal.py` `membership_grants` — no audience filter |
| Employee events carry **no hierarchy** — `onboarded` has `status`, `updated` has a `changed[]` list of field names, `transitioned` has `from`/`to` | `employee/app/services.py`, the three `publish_event` calls |
| An envelope's `occurred_at` is the outbox row's `created_at`, a **server default of `now()`** — the *transaction start* time | `platform_core/events.py` `OutboxMixin.created_at`, `build_envelope` |
| `processed_events` has **no `organization_id`** and is deliberately un-policied | `platform_core/events.py` `ProcessedEventMixin`; ADR-0002 §5 |
| Role templates merge **per product** across services; a product's template can grant only its own catalogue | `tools/export_permissions.py`; ADR-0000 §5 corollary; `account/app/catalogue.py` `CORE_ROLE_TEMPLATES` |
| `services/notification` is a 171-line scaffold with no delivery | `services/notification/app/` |

The question this ADR settles is where the approval machine lives, who owns the hierarchy it routes
on, the exact rules it routes by, and what it does in each of the edge cases that a routing engine
is otherwise discovered to have in production.

---

## Decision

### 1. A separate platform service, `approval`, under product key `core`, integrated by events in both directions

`services/approval`, schema `approval`, permission prefix `core.approval.*`, gateway prefix
`/api/v1/approvals` on `app-gateway` with **no `product`** in `routes.json` — it is a platform
surface every customer has, like `/api/v1/members`. Four processes, the same shape as subscription:
`bin/api`, `bin/worker` (consumers), `bin/cron` (the SLA sweep), and the outbox relay.

Integration is **events in, events out**. A product publishes `<request_type>.requested` from its
own transaction; approval consumes it and opens a request. Approval publishes
`core.approval.request.decided`; the product consumes it and applies the outcome to its own row, in
its own transaction, under its own state machine. Neither side calls the other synchronously, and
neither side reads the other's schema.

Why a service and why events, in one sentence each: the request's *row* (the attendance day, the
leave balance, the purchase order) must stay with the product that owns its invariants, and the
*routing* (hierarchy, delegation, SLA, eligibility) must be one implementation, or five products will
ship five subtly different answers to "can my manager's delegate approve my request after my manager
was replaced?"

The attendance regularisation request is therefore **two rows in two services**: a
`regularization_requests` row in attendance (the product fact, with its own `pending|approved|
rejected|failed` status) and an `approval_requests` row in approval (the routing fact). They are
joined by `(organization_id, request_type, source_ref)`, never by a foreign key.

### 2. Employee owns the hierarchy; approval holds a read-only copy

**`employee-service` is the only writer of reporting lines.** Approval keeps `hierarchy_nodes` — one
row per employee: `employee_id`, `reporting_manager_id`, `dotted_line_manager_id`, `status`,
`display_name`, `employee_no`, `location_code`, `source_version`, `source_occurred_at` — fed by two
things only:

- **Full-state employee events.** All three employee events (`onboarded`, `updated`,
  `transitioned`) gain the complete hierarchy state of the employee *after* the change:
  `reporting_manager_id`, `dotted_line_manager_id`, `status`, `display_name`, `employee_no`,
  `location_code`, `hierarchy_version`. Full state, not a diff: a consumer that has missed or
  reordered an event converges on the next one it sees, and never needs to replay history.
- **A one-time internal backfill** for organizations that already have employees when approval
  ships (contract in §2.1 below).

**Amended — which columns the copy carries, and why two were added and one dropped.**

- **`display_name` and `employee_no` are added.** Approval stores people as UUIDs, and the screen
  specifications' **C1** is correct that this makes every approval screen unshippable: an ordinary
  approver holds `hrms.people.employee.read:OWN` and can resolve nobody but themselves, and a
  manager's TEAM scope reaches direct reports only — an escalated request's requester is often two
  levels down. So the name travels on the copy, fed by the same full-state events, and approval's
  responses return `*_name` fields beside every id (§11). Two concrete consumers today, not one: the
  inbox and the chain view. `display_name` is `employees.full_name`, which is **not** a sealed
  field — employee's PII envelope covers date of birth, personal contact, PAN, Aadhaar, UAN, ESI and
  bank details, and deliberately not the name (`employee/app/models.py`). Copying it moves no
  ciphertext and needs no unmask permission.
- **`department_id` is dropped.** It has no consumer. Approval would hold a bare `account.org_units`
  UUID it cannot render, and rendering it would need a *second* denormalised name field fed by a
  *third* service's events. The inbox's department sub-line is therefore not rendered in these
  slices. Adding it later is an additive event field and an additive column — the same cost as
  adding it now, paid only if someone asks.
- **`location_code` is kept**, because it now has a named consumer: it is a *code*, and the SPA
  resolves it against `GET /api/v1/locations` to render every instant in the right timezone
  (ADR-0000 *Also settled*; screens §2.1). A request renders in the requester's location, which is
  a fact only this copy has.

#### 2.1 The backfill's failure contract

**Amended — the backfill previously had an endpoint and no failure behaviour.** Timeouts, retry and
resume are architectural decisions, so they are made here.

**This is new surface on employee.** Verified: `services/employee/app/api/` contains only
`routes.py`, with one router and no `require_service_token` anywhere — employee has **no
`/internal/v1` router today**. One is added, shaped exactly like `account/app/api/internal.py`:
mounted at the root (not under `/api/v1`, so it stays out of the customer-facing OpenAPI document),
`include_in_schema=False` on the whole router rather than per route, and
`dependencies=[Depends(require_service_token)]` so a route added later cannot forget the guard.

`GET /internal/v1/hierarchy?organization_id=&cursor=&limit=` returns
`{items: [...], next_cursor: uuid | null}`.

| Decision | Value | Why |
|---|---|---|
| Pagination | **Keyset** on `(organization_id, id)`, `WHERE organization_id = :org AND id > :cursor ORDER BY id LIMIT :limit` | Served by `uq_employees_org_id`, which the data model adds anyway as the composite FK target (0.44 ms per 500 rows, verified). `platform_core.pagination` is offset-based; an offset page drifts if a row is inserted mid-run, which silently skips employees |
| Page size | **500**, `limit` capped at 1000 | 100 pages for the largest realistic tenant (50k employees) |
| Timeout | **10 s** total per page, 2 s connect | `subscription/app/catalog.py` uses 5 s for a single-row read; a 500-row page is heavier |
| Retry | **5 attempts per page**, exponential backoff 1·2·4·8·16 s with full jitter, on connection errors, timeouts and 5xx **only** | A 4xx is never retried: a 401 means the audience or secret is wrong and retrying repeats the same mistake; a 422 means a malformed cursor |
| Service token | **Minted per HTTP request, inside the page loop** | `service_token_ttl_seconds` defaults to **60** (`platform_core/config.py:138`). A token minted once at the start of a 200-page run expires after the first minute and every later page 401s — and by the retry rule above, correctly does not retry. This is the trap that makes per-request minting a decision rather than a detail; `HttpPlanCatalog` already mints per call for the same reason |
| Pacing | 100 ms between pages | A backfill must not saturate employee's pool (`db_pool_size` 10 + `db_max_overflow` 5) |
| Transaction | **One per page**, on approval's side | A page either applies wholly or not at all; the upsert is version-gated, so a re-applied page is a no-op |
| Resume | **Re-run from the start** by default; `--cursor <uuid>` resumes from a printed cursor | The upsert is idempotent and version-gated (data model §1.1), so a second full pass converges to the same state. On failure the runner exits non-zero and prints the last successfully applied cursor. **No progress table**: a one-time job does not earn a table the service carries forever, and the alternative to a table is one printed line |

**When employee is slow, failing, or wrong.** The backfill is an operator job, not a request path, so
its degradation is simply "it fails, is re-run, and nothing customer-facing changes". The
customer-facing degradation while the copy is incomplete is already decided in §4: a requester with
no node opens an **unassigned** step that HR can answer. If employee returns a node whose
`hierarchy_version` is absent — an un-upgraded pod — the row is **ignored**, never treated as
version 0, which would let a stale state overwrite a fresh one.

**`account.memberships.manager_membership_id` is not the source**, and approval never reads it. It is
a second manager field with no synchronisation; routing on it would mean the chain an HR admin edits
on the employee page is not the chain approvals follow. It is left alone — neither deleted nor
synced — and a future ADR decides its fate.

**The dotted-line manager is stored, not routed.** It exists on the copy so the chain endpoint can
show it and so a later matrix-approval rule has the data without a second backfill. No rule in this
ADR sends a step to a dotted-line manager.

**Employee adds a cycle check.** Setting `reporting_manager_id` or `dotted_line_manager_id` walks the
proposed manager's chain upward inside the same transaction and refuses the change with a business
rule problem if it reaches the employee. Approval's walks are still defensive (§5), because the copy
can be transiently inconsistent between two events even when the source never is.

**Amended — the check takes an advisory lock first.** It is one recursive statement with a depth cap
of 32 and the SQL-standard `CYCLE` clause (data model §4.3), preceded in the same transaction by

```sql
SELECT pg_advisory_xact_lock(hashtextextended('employee.hierarchy:' || :organization_id::text, 0));
```

Without it the check is best-effort, and the gap is not theoretical: two concurrent edits on
*disjoint* rows can each walk a chain that is acyclic at read time and still compose a cycle on
commit (`A.mgr = B` and `C.mgr = D` closing a loop through `B..C`). Row locks on the two rows
involved do not prevent it, because neither transaction ever touches the other's row. The lock is
per organization and transaction-scoped, so it serialises hierarchy edits within a tenant and
nothing else; hierarchy edits are rare enough that the contention is not worth measuring.

#### Ordering the copy: a version, not a timestamp

The brief proposed ordering hierarchy events by `occurred_at`. **That is not safe, and this ADR
replaces it.** `occurred_at` is `outbox.created_at`, a server default of `now()`, which in Postgres
is the *transaction start* time. Two concurrent edits of the same employee can start in one order
and commit in the other: transaction A starts at 10:00:00.100, transaction B at 10:00:00.200, B
commits first, A waits on B's row lock and then commits the later state, stamped with the *earlier*
time. An "ignore older than stored" rule on `occurred_at` would then discard the newest state
forever.

Therefore: **`employees.hierarchy_version BIGINT NOT NULL DEFAULT 0`**, bumped **by a `BEFORE UPDATE`
trigger** whenever any hierarchy field changes. Approval applies an event or a backfill row **if and
only if its `hierarchy_version` is greater than the stored `source_version`**; equal is a duplicate
and is dropped. `source_occurred_at` is kept for display and debugging, never compared.

**Amended — the bump is a trigger, not a service-side increment.** This ADR originally said
`hierarchy_version = hierarchy_version + 1` "in the same `UPDATE`". The persistence design proved
that is a lost update, and it wins: `update_employee` and `transition_status` mutate ORM attributes
on a row loaded by an earlier `SELECT`, and **there is no `with_for_update` anywhere in
`services/employee/app`** (verified — the grep returns nothing). A Python-side `+= 1` therefore
computes from a stale read: two concurrent edits both read 5 and both write 6. Approval's gate is
strictly `>`, so the second state is dropped **permanently** and the copy stays stale until some
later unrelated edit bumps the version again — exactly the silent divergence the version exists to
prevent.

The trigger (`employee.bump_hierarchy_version`, data model §4.2) compares the hierarchy tuple
`IS DISTINCT FROM` its old value and increments only on a real change. The row lock the `UPDATE`
itself takes serialises the increment, so monotonicity needs no application discipline — including
from a bulk import, a backfill script or a `psql` session, none of which would remember. It also
makes the column non-writable from outside: a client-supplied value is ignored. The service reads
the value back (`RETURNING` or a refresh) to put it on the event.

The backfill carries the same `hierarchy_version`, so the backfill and the live event stream can run
at the same time without coordination. The one ordering requirement is operational: **the approval
worker's queue must be declared and bound before the backfill starts**, so that no event published
during the backfill window is lost to an unbound queue.

The backfill endpoint takes an explicit `organization_id` and runs under that tenant's GUC on the app
role. There is no cross-tenant listing on employee, and this ADR does not create one: widening a
BYPASSRLS surface for a one-time job is the wrong trade (ADR-0002 §2). The runner is a one-shot
command (`bin/backfill-hierarchy --org <id> …`), idempotent and re-runnable per organization.

### 3. The tables

All carry `organization_id NOT NULL`; all have `ENABLE` + `FORCE ROW LEVEL SECURITY` with a
`tenant_isolation` policy using `approval.current_org()` in both `USING` and `WITH CHECK` (ADR-0002
§3); every index leads with `organization_id`. No cross-schema foreign keys. Persons are
`employee_id` UUIDs throughout — approval has no notion of a user or membership.

| Table | Holds | Mutability |
|---|---|---|
| `hierarchy_nodes` | The copy (§2). PK `(organization_id, employee_id)` | Upserted by consumer and backfill only |
| `approval_policies` | `request_type`, `sla_minutes`, `enabled`. PK `(organization_id, request_type)` | Edited by `PUT`, audited |
| `approval_requests` | `id`, `request_type`, `source_ref`, `requester_employee_id`, `summary` JSONB, `request_note`, `status pending\|approved\|rejected`, `decided_by_employee_id`, `acting_for_employee_id`, `decided_at`, `comment`, `opened_at`, `application_state applied\|failed` (NULL until the product reports back, §12), `application_failure_reason`, `applied_at`. `UNIQUE (organization_id, request_type, source_ref)` | Status moves once, from `pending`; `application_*` set once, after |
| `approval_steps` | `id`, `request_id`, `level`, `approver_employee_id` (NULL = unassigned), `acting_for_employee_id`, `delegation_id`, `reason assigned\|delegated\|escalated\|unassigned`, `opened_at`, `due_at`, `closed_at`, `outcome open\|delegated\|escalated\|approved\|rejected` | **Append-only**; see below |
| `delegations` | `id`, `delegator_employee_id`, `delegate_employee_id`, `starts_at`, `ends_at`, `created_by_employee_id`, `revoked_at`, `revoked_by_employee_id` | Only `revoked_at`/`revoked_by` may be set, once |
| `outbox`, `processed_events`, `audit_logs` | The platform mixins | `processed_events` is un-policied (ADR-0002 §5) |

**`approval_steps` is append-only, and the rule is enforced by the database, not by convention.**
The app role holds no `DELETE`. A `BEFORE UPDATE` trigger permits exactly one kind of update: from
`outcome = 'open'` to any other outcome, setting `closed_at` in the same statement, with every other
column unchanged. Rerouting is never an edit — it is closing one step and inserting the next.

This exposed a contradiction in the brief, resolved here: rule 4 said a decision "closes all
non-terminal steps", listing `delegated` and `escalated` among the eligible outcomes, while the table
rule said outcomes transition only from `open`. **`delegated` and `escalated` are terminal step
outcomes.** They stop a step from being the *current* one; they do not stop the people on it from
being eligible, because eligibility (§6) is derived from the *request* being pending, not from the
step being open.

**Invariant: a pending request has exactly one `open` step.** Enforced by a partial unique index
`(organization_id, request_id) WHERE outcome = 'open'`. Opening, delegation and escalation all close
the current step and insert its successor in one transaction. A decided request has none.

`delegations` carries a GiST exclusion constraint: no two non-revoked rows for the same
`(organization_id, delegator_employee_id)` with overlapping `tstzrange(starts_at, ends_at)`. A
`CHECK` enforces `delegate_employee_id <> delegator_employee_id` and `ends_at > starts_at`.

**Amended — `closed` is not a step outcome.** The table above previously listed it, inherited from
the brief's rule 4. The persistence design's `ck_approval_steps_outcome` excludes it and is right:
the invariant is that a pending request has exactly **one** `open` step (below), so a decision has
exactly one step to close and there is never a set of "other non-terminal steps" to sweep into a
`closed` state. `delegated` and `escalated` are already terminal. Removing the value removes a state
nothing could ever write.

**`summary` and `request_note`: the snapshot, split by classification.**

**Amended — the original text said `summary` carries the requester's `reason` and "must never
contain PII". That rule is unenforceable as written**, and CLAUDE.md requires PII to be encrypted and
masked rather than promised about. So the snapshot is split by what can actually be guaranteed:

- **`summary` JSONB** holds only *structured, enumerable* facts from the requesting event — for
  regularisation `date`, `from`, `to`. Every value is a date or a member of a closed status
  vocabulary. "No PII" here is true by construction, not by discipline, because there is no field a
  human can type into.
- **`request_note`** holds the requester's free text (regularisation's `reason`, ≤500 characters), in
  its own column, classified as **customer content authored by the requester about their own working
  day** — not as a PII field.

**Why it is not sealed.** Sealing it would be security theatre with a real cost. The same text is
already stored in plaintext in `attendance.regularization_requests.reason` (`varchar(500)`, with only
a non-blank CHECK), so sealing the copy protects nothing while the original sits in the clear.
Worse, `platform_core.pii` exists to make unmasking *rare and audited* — one audit row per unmask,
every time (ADR-0000 *Also settled*). This text must be read by every approver on every inbox render;
that is an audit row per page view, which floods the trail that PII auditing depends on and makes the
signal unreadable. A control that must be bypassed to render a list is not a control.

**What is enforced instead**, as four rules a reviewer can check:

1. `request_note` never appears in a **published event payload** — not in `opened`, `escalated` or
   `decided` (§12). Events cross services and land in a broker.
2. `request_note` never appears in an **audit `before`/`after` blob** or in a log line — the same
   rule employee applies to sealed fields, which record field *names* only.
3. `request_note` is returned **only** to a principal who is the requester, appears on a step of the
   request (§11's amended detail rule — which includes anyone currently eligible and anyone who was
   asked earlier), or holds `read:ORG` — never on any unauthenticated or cross-tenant surface.
4. The cross-tenant sessions — the sweep and the alarms — **cannot read it at all.** The outbox role
   holds column-level `SELECT` and nothing wider: **seven** columns of `approval_steps`
   (`organization_id`, `request_id`, `id`, `outcome`, `approver_employee_id`, `due_at`, `opened_at`)
   and **six content-free** columns of `approval_requests` (`organization_id`, `id`, `request_type`,
   `status`, `decided_at`, `application_state`). `summary` and `request_note` are refused with
   `42501`, verified against the running database (data model §3.3).

**Amended — this rule previously said the outbox role holds *no* grant on `approval_requests`.**
That was both unimplementable and the wrong way to state the property. §12's "approved but never
applied" alarm is cross-tenant by nature, so it runs on the outbox role and must read
`approval_requests`; under the original wording it was refused with `42501` and could never have run
at all. The six columns above are what it needs and no more — every one an id, an enum or a
timestamp.

The property being defended was never "this role touches that table nowhere". It is: **the bypass
surface may read routing and lifecycle facts, never customer content.** A column holding something a
person typed, or quoting it, is never granted to the outbox role; ids, enums, timestamps and counters
may be. That is a test a reviewer can apply to the next column somebody adds — which "no grant on
that table" was not, because the first legitimate cross-tenant read of the table would have forced
someone to choose between a broken alarm and a `GRANT SELECT` on everything.

Neither field is kept in sync with the product row after the request opens.

### 4. Opening a request (rule 1)

On `<request_type>.requested`, inside one tenant-bound transaction:

1. `INSERT … ON CONFLICT (organization_id, request_type, source_ref) DO NOTHING`. A producer that
   re-publishes the same request under a new event id opens nothing twice; `processed_events` covers
   redelivery of the same event id.
2. **Level-1 approver** = the requester's `reporting_manager_id`, walking upward past any manager
   whose node has `status = 'exited'`, and past the requester themselves.
3. **If the walk finds nobody** — the requester has no manager, every manager above is exited, the
   chain contains a cycle (§5), **or the requester has no `hierarchy_nodes` row at all** — the step
   opens **unassigned**: `approver_employee_id = NULL`, `reason = 'unassigned'`, `due_at = NULL`.
   It is decided by holders of `core.approval.request.decide:ORG` (§6).
4. Otherwise delegation is applied (§5), and the step opens with `reason = 'assigned'` (or
   `'delegated'`), `level = 1`, `due_at = opened_at + sla_minutes`.
5. Audit row `approval.request.opened`; publish `core.approval.request.opened`.

**A requester missing from the copy is not retried.** It happens when the backfill has not reached
that organization yet or an `onboarded` event is lagging. Refusing or redelivering would dead-letter
the request after one retry (`EventConsumer` redelivers once), which silently loses it. An unassigned
step routed to HR is recoverable by a human; a dead-lettered request is not. The audit row records
`requester_not_in_hierarchy` so the population is findable. Rule 6 still applies: the request is not
re-routed when the node arrives later.

**The requester is always the product's statement of whose request it is**, not the caller's token.
For regularisation it is the attendance record's `employee_id`, and attendance only lets an employee
request against their own record. A caller with no `sref` cannot create the source request in the
first place.

### 5. Delegation (rule 2) and the chain walk

**A delegation is active at instant `t`** when `revoked_at IS NULL AND starts_at <= t < ends_at`.

**At step open**, if the chosen approver `A` has an active delegation to `D`, and `D` is not the
request's requester, and `D`'s node is not `exited`: the step's `approver_employee_id = D`,
`acting_for_employee_id = A`, `delegation_id` = that delegation, `reason = 'delegated'`. Otherwise the
step goes to `A` with no delegation.

- **Not transitive.** If `D` has also delegated, the step still goes to `D`. One hop, always.
- **Delegate is the requester → no delegation for this step.** The step goes to `A`. The alternative
  — skipping to `A`'s manager — would let a delegation silently change who approves a request.
- **The original approver stays eligible.** `A` can still decide the request (§6).

**Creating a delegation re-points the delegator's currently open steps** in the same transaction:
for each pending request whose open step has `approver_employee_id = delegator` and no
`acting_for_employee_id`, and whose requester is not the delegate, the step closes with
`outcome = 'delegated'` and a successor step opens with `reason = 'delegated'`, the same `level`,
`approver = delegate`, `acting_for = delegator`, `delegation_id` set, and **the predecessor's
`due_at` carried over unchanged**. Re-pointing does not restart the SLA clock; if it did,
creating and revoking delegations would be a way to never escalate. Only delegations whose
`starts_at <= now` re-point at creation. A future-dated delegation affects only steps opened
after it starts, because nothing re-points it when it starts.

**Revocation and expiry never re-route.** Consistent with rule 6, ending a delegation creates no
steps. What it changes is **eligibility**: a delegate is eligible through a delegated step only while
that step's `delegation_id` is active *at the moment of the decision*. Revoke the delegation and the
delegate loses the power to decide immediately. The delegator never lost it, and the step still
escalates on SLA from the delegator. No request is stranded, and no revoked delegate keeps authority.

**Delegation validation at creation**: the delegate must have a `hierarchy_nodes` row whose status is
not `exited`. `ends_at` must be in the future. The overlap constraint covers the rest.

**The chain walk** (used by opening, escalation and `GET /chain`) is **a recursive CTE in
`app/selectors.py`**, not a traversal in Python.

**Amended — the original wording, "one function in `app/domain.py`, pure over a mapping of nodes",
implied loading every node in the organization to answer one routing question.** For a 4,000-person
tenant that is 4,000 rows per opened request and per escalation, on the hot path of a consumer. The
walk returns at most 33 rows and the database can do it in one statement:

```sql
WITH RECURSIVE up (employee_id, reporting_manager_id, status, display_name, depth) AS (
        SELECT h.employee_id, h.reporting_manager_id, h.status, h.display_name, 1
          FROM approval.hierarchy_nodes h
         WHERE h.organization_id = :organization_id
           AND h.employee_id = :start_employee_id
    UNION ALL
        SELECT h.employee_id, h.reporting_manager_id, h.status, h.display_name, up.depth + 1
          FROM up
          JOIN approval.hierarchy_nodes h
            ON h.organization_id = :organization_id
           AND h.employee_id = up.reporting_manager_id
         WHERE up.depth < 32
) CYCLE employee_id SET is_cycle USING path
SELECT * FROM up ORDER BY depth;
```

The same shape as employee's own cycle check (data model §4.3), for the same reason and with the
same depth cap of 32. Three properties, each deliberate:

- **The `CYCLE` clause terminates the walk in the database.** The source refuses cycles (§2), but the
  copy is two events wide and can be transiently cyclic while an `A→B` and a `B→A` correction are in
  flight in opposite order. An infinite loop in a consumer is not an acceptable way to discover that,
  and an application-side visited set is a discipline the next author can forget. A row with
  `is_cycle` true, or `depth` reaching 32, ends the walk as *chain exhausted*, logged as a warning
  with the organization and the nodes on the path.
- **The walk does not stop at an exited node, it walks past it.** Skipping is a property of
  *choosing* an approver, not of traversal: a chain `requester → exited manager → director` must
  reach the director. So the CTE returns every rung in order, and a small pure function in
  `app/domain.py` — `pick_approver(rungs, requester_id)` — takes the first rung that is neither
  `exited` nor the requester. That function stays pure and stays trivially testable over a list of
  at most 33 items, which is what `domain.py` is for; what it is not is a substitute for a query.
- **It is one indexed lookup per rung** against `pk_hierarchy_nodes` (8 rungs measured at 2.4 ms,
  data model §2 pattern 12).

`GET /chain/{employee_id}` returns the same rungs with `display_name`, and marks skipped exited
rungs so the shape of a chain is explainable rather than mysterious.

### 6. Eligibility (rule 3)

A caller may decide a request only if **all** of the following hold:

1. The request is `pending`.
2. The caller has a `subject_ref`. **A membership with no employee link can decide nothing, including
   unassigned steps.** `decided_by_employee_id` is `NOT NULL`, and "the requester is never eligible"
   is only a guarantee if every decider is an identifiable employee.
3. The caller's `subject_ref` is **not** the `requester_employee_id`. This is checked first and
   overrides every other route to eligibility, including `decide:ORG`.
4. At least one of:
   - **(a) assigned**: some step of the request with outcome `open`, `delegated` or `escalated` has
     `approver_employee_id = subject_ref`, and either the step has no `delegation_id` or that
     delegation is active now;
   - **(b) represented**: some step of the request with outcome `open`, `delegated` or `escalated`
     has `acting_for_employee_id = subject_ref`;
   - **(c) unassigned or abandoned**: the request's `open` step has `approver_employee_id IS NULL`
     **or** its approver's `hierarchy_nodes` row has `status = 'exited'`, and the caller holds
     `core.approval.request.decide` at scope `ORG`.

**Amended — routes (a) and (b) additionally require that the caller's own node is not `exited`, and
route (c) widens to cover a step held by an exited approver.** The original §9 dismissed this with
"they cannot sign in, so it is moot". That is unverified and appears to be false:
`employee.employees.status` and `account.memberships.status` live in different services, and **no
wiring between them exists** — nothing in `account/app/consumers.py` binds
`hrms.people.employee.transitioned` (it binds product activation and the entitling subscription
events, and nothing else). An employee marked `exited` in HRMS therefore keeps an active membership
and a valid token until somebody deactivates the membership by hand. Eligibility cannot rest on an
assumption about sign-in; it is enforced here, on the copy, where the fact actually lives.

The two halves belong together. Excluding exited approvers alone would strand a request whose only
eligible person has left, in the one case where escalation cannot rescue it: escalation is disabled
for the type (`enabled = false`, so `due_at IS NULL`, §10) or the step is the last rung. Widening
route (c) to treat such a step as answerable by `decide:ORG` holders gives HR a path without
re-routing anything, so rule 6 (§9) still holds: the step keeps its approver, its history and its
`due_at`; only *who may answer it* changes. The cost is one join to `hierarchy_nodes` on the
eligibility check and one extra `UNION` branch in the inbox's unassigned arm.

Consequences of this definition, stated so nobody has to derive them:

- **The original approver stays eligible after delegation** (b), **and after escalation** (a on the
  escalated step). Escalation widens the set of people who can answer; it never removes the one who
  was asked first.
- **`decide:ORG` does not let HR decide an assigned request.** It covers unassigned steps and nothing
  else. The HR escape hatch for a stuck request is the product's own override — for attendance, the
  existing direct `POST /attendance/{id}/regularize` — which stays audited in its own domain. A
  second, cross-product "approve anything" switch would put an ORG-wide bypass on every future
  approval type the moment it ships.

### 7. The decision (rule 4)

`POST /api/v1/approvals/requests/{id}/decision` `{outcome: approve|reject, comment?}` — one
transaction:

**Amended — the comment stays optional for both outcomes, and that is now a stated server rule.**
The screen specifications' **O1** asked whether a rejection should require a reason. It must not be
a client-only rule: a required field the server does not enforce drifts the moment a second client
exists, and a mobile app that skips it would produce rejections the web UI claims are impossible. So
either the server requires it or nobody does, and this ADR chooses nobody: an approver rejecting
"you already took this as leave" in person and clicking Reject is a legitimate flow, and a mandatory
field in that case produces `"."` rather than information. The UI nudges — it autofocuses the comment
on reject and says the requester will see it — and that is where the pressure belongs. Making it
mandatory later is a one-line server change plus a schema note, and it would be an amendment to this
section rather than a UI decision.

1. `SELECT … FOR UPDATE` on the request row. **Lock order is always request, then steps**, in the
   decision path and the sweep alike, so the two cannot deadlock.
2. Re-evaluate §6 under the lock. Not pending → **409 `already decided`**, with the recorded outcome.
   First decision wins; a second decider gets a clear answer, not a silent overwrite.
3. The single `open` step closes with `outcome = approved | rejected`. There is exactly one (§3). Which
   step made the decider eligible does not matter for the outcome: history steps are terminal and
   stay as they were.
4. The request gets `status`, `decided_by_employee_id = subject_ref`, `decided_at`, `comment`, and
   `acting_for_employee_id`: the employee the decider stood in for when their eligibility came
   **only** through route (a) on a delegated step; NULL when they decided in their own right (as an
   assigned approver, as the represented original, or via `decide:ORG`).
5. Audit row `approval.request.decided`; publish `core.approval.request.decided`.

### 8. SLA escalation (rule 5)

The sweep is ADR-0005 §6's shape, applied to steps.

- **Discovery** on the BYPASSRLS outbox engine (`get_outbox_sessionmaker`, asserted by
  `assert_outbox_role` at startup outside local): select `(organization_id, request_id, step_id)`
  for steps with `outcome = 'open' AND approver_employee_id IS NOT NULL AND due_at <= now()`, ordered
  by `due_at`, **reading a slice of 500 and acting on at most 100, with a cap of 20 steps per
  organization per tick**. The outbox role gets column-level `SELECT` on **seven** columns of
  `approval_steps` and six content-free columns of `approval_requests` (§3), and nothing more. It
  decides nothing and writes nothing.

  **Amended — seven columns, not six.** The discovery query above uses six; the seventh, `opened_at`,
  is load-bearing for the *other* probe. The primary stuck-request alarm is "oldest **open** step",
  and it has to cover unassigned steps — the population that most needs an operator, since nothing
  escalates them (`ck_approval_steps_unassigned_has_no_due` makes `due_at` NULL for them by
  constraint). With no `due_at`, `opened_at` is the only clock such a step has. Granting six columns
  meant the alarm could not read the column it orders by: refused with `42501`, **failing closed** —
  reporting nothing while the process runs and looks healthy, which is the exact failure mode this
  ADR's Consequences section says these alarms exist to prevent.

  **Amended — a flat "first 100 by `due_at` platform-wide" is not fair.** One tenant that sets
  `sla_minutes` to its minimum and raises a few hundred requests produces overdue steps faster than
  a tick can drain them, and because the ordering is global and their steps are always the oldest,
  they occupy every batch — every other tenant's escalations stop, silently, with no error and no
  alarm (the sweep is running and doing work). Two bounds fix it, and both are needed:

  - **A per-organization cap of 20 per tick**, applied over the ordered 500-row slice. The slice is
    still served index-only by `ix_approval_steps_sla_due` (partial, 1.4 MB against a 208 MB table),
    and capping is a counter over rows already in hand, so it costs nothing. A single tenant can
    therefore take at most 20% of a tick, and a tenant with 1,000 overdue steps drains over ~50
    minutes at the 60 s poll while everyone else is served every tick.
  - **A floor of `sla_minutes >= 15`**, enforced by the policy CHECK rather than by the API, so no
    path can write a 1-minute SLA. Fifteen minutes is the smallest value that is an approval rather
    than a formality, and it matches the lower bound the settings screen already validates. Without
    the floor, the per-tenant cap merely slows the pathological tenant down; with it, the
    pathological case cannot be configured in the first place.

  The two together bound the worst case: a tenant cannot manufacture overdue steps faster than
  `employees / 15 minutes`, and cannot consume more than a fifth of any tick regardless.
- **Action**, per step, in `session_scope(organization_id=…)` on the app role: lock the request
  `FOR UPDATE SKIP LOCKED` (held means a decision or another replica is mid-flight, so skip). Then
  re-read the step and re-check `request.status = 'pending'`, `outcome = 'open'`,
  `approver_employee_id IS NOT NULL`, `due_at <= now`. Any failure is a skip. This re-check under the
  lock is what makes concurrent sweeps idempotent: the second replica finds the step already
  `escalated`.
- **Escalate**: close the step `outcome = 'escalated'`. The walk starts from the step's **original
  approver**, which is `acting_for_employee_id` if set, else `approver_employee_id`. The delegate's
  manager is never the next rung, because the delegate is not in the requester's line. Next approver
  = nearest non-exited manager above it, skipping the requester (§5). Apply delegation (§5) to that
  approver. Insert the successor with `level + 1`, `reason = 'escalated'` (or `'delegated'` when a
  delegation applied at this rung), `due_at = now + sla_minutes`.
- **Chain exhausted** → the successor is unassigned (`approver NULL`, `due_at NULL`,
  `reason = 'unassigned'`). **Unassigned steps never escalate**, so the ladder terminates by
  construction.
- Audit row `approval.step.escalated`; publish `core.approval.request.escalated`.

**Why `now + sla` here, when ADR-0005 forbids `now + interval`.** Dunning's ladder is a promise
anchored at one instant; an approval SLA is a per-rung allowance for a *new person* who has only just
been asked. A sweep that runs late does delay the next rung, and each rung gets its full window,
which is the fair reading for the person now holding the request. The drift is bounded by the poll
interval (**60 s**), which is below the minute granularity SLAs are expressed in.

**Clocks. Amended — there is one clock, and it is the database's.**

Every SLA instant (`opened_at`, `due_at`, `closed_at`, `decided_at`, `applied_at`, and every
delegation-activity comparison) is **read from PostgreSQL, once per transaction**, as a plain
`SELECT now()` at the top of the unit of work, then threaded through every decision in that
transaction.

The original wording — "approval's own clock" — left two clocks in the system and did not say so.
`opened_at`, `due_at` and `closed_at` would have come from the **app pod** (`datetime.now(UTC)`,
which is what every other service in this repo does), while `created_at` and `updated_at` come from
the **database** (`server_default=func.now()` on the platform mixins). Those are different machines,
and the rows are written by *different* app pods: the API pod opens and decides, the cron pod
escalates. The persistence design then puts CHECK constraints across the boundary —
`ck_approval_steps_closed_after_open` compares `closed_at` with `opened_at`, and
`ck_approval_requests_decided_after_open` compares `decided_at` with `opened_at`. A cron pod whose
clock is 200 ms behind the API pod that opened the step closes it "before" it opened, and the insert
fails with a constraint violation that reads like a logic bug and is actually NTP drift. The failure
is rare, non-reproducible and lands in the escalation path, which is the worst place in this design
to have a heisenbug.

`now()` is `transaction_timestamp()` — stable for the whole transaction, and **exactly** the value
the mixins' `server_default` writes — so app-supplied and database-supplied instants in one row are
guaranteed consistent and the constraints become unfalsifiable rather than probabilistic. The cost is
one round trip per transaction (sub-millisecond, on a connection already open). In the sweep,
discovery evaluates `due_at <= now()` **in the database** on the discovery transaction, and each
action transaction reads its own `now()` — deliberately a later instant, which is correct, because
the re-check under the lock must ask whether the step is *still* due now.

Approval still never compares its clock with a foreign `occurred_at`, and `opened_at` is still when
approval opened the request rather than when the product published the event — so consumer lag does
not eat into an approver's SLA, and cross-service skew cannot make a step due early.

### 9. Hierarchy changes never re-route (rule 6)

An open step keeps its approver when the hierarchy copy changes underneath it: when a manager is
replaced, exits, or the requester moves department. A request routes on the chain as it stood when
each step opened. The alternative makes a request's approver a function of when you look, turns a
routine reorg into a mass reassignment nobody reviewed, and would need a re-route sweep with its own
race against decisions. **The cost is accepted and bounded:** a step whose approver has exited
simply escalates on SLA, and the walk skips exited managers when it does.

**Amended — an exited approver is excluded from eligibility, rather than assumed to be locked out.**
This section previously read "the original approver's eligibility is unaffected by their exit. They
cannot sign in, so it is moot." That claim was never verified and does not hold: employee status and
membership status are owned by two different services with no wiring between them (§6), so an
`exited` employee can still hold an active membership, a valid token and — under the original
rule — the power to approve. §6 now excludes them and hands the step to `decide:ORG` holders
instead. Rule 6 is untouched: nothing re-routes, and the step keeps its approver, its `due_at` and
its place in the history.

A requester who exits while a request is pending does not cancel it. It remains decidable, and the
product decides what an approval means for a departed employee's row.

### 10. Policy

`approval_policies` governs **SLA only**. A missing row means the defaults: `enabled = true`,
`sla_minutes = 1440`. The row is created on first `PUT`. **`enabled = false` turns off escalation
for that request type** (steps open with `due_at = NULL`). **It never turns off approval.** No
configuration auto-approves: auto-approval of a self-service request is a way to let every employee
regularise their own attendance, reachable by one checkbox. A product that does not want a request
approved should not publish `.requested`.

**Amended — `sla_minutes` has a floor of 15**, enforced by the CHECK constraint
(`sla_minutes BETWEEN 15 AND 525600`) rather than by the API, so no path can write a value that
starves the sweep (§8).

**Amended — slices 2 and 3 open every step with `due_at = NULL`, and the sweep does not run until
slice 4.** This is not a workaround; it is the only coherent staging. Slice 2 routes and decides
requests, and slice 4 brings escalation, the policy surface and the due/overdue display. If slice 2
stamped a `due_at` it could not act on, every request raised in the intervening weeks would become
an overdue open step, the *oldest overdue open step* liveness alarm (see Consequences) would fire
continuously and correctly, and the only available response would be to ignore it — which is how a
team learns to ignore that alarm permanently. Opening with `due_at = NULL` is a legal, already-modelled
state: it is exactly the shape `enabled = false` produces, and `ck_approval_steps_unassigned_has_no_due`
is written as an implication rather than an equivalence precisely so an assigned step may have no due
date. Slice 4 turns escalation on for steps opened from then; steps opened earlier keep `due_at = NULL`
and never escalate, consistent with this section's rule that a policy edit applies only to steps
opened after it. The alarm is armed in slice 4, with the feature that answers it.

A policy edit applies to steps opened after it. Existing `due_at` values are stored and do not move
(ADR-0005 §3's discipline: do not move a promise already made).

### 11. Authorization

Permission codes, declared in `services/approval/app/catalogue.py` as a slice of the **`core`**
catalogue and exported to `libs/contracts/permissions/core.json`:

| Code | Scopes | Meaning |
|---|---|---|
| `core.approval.request.read` | OWN, ORG | OWN: requests I raised, requests I am eligible on, and my own chain. ORG: every request and any employee's chain |
| `core.approval.request.decide` | OWN, ORG | OWN: decide where §6 (a) or (b) makes me eligible. ORG: additionally decide unassigned steps (§6 c) |
| `core.approval.delegation.write` | OWN, ORG | OWN: create/revoke delegations where I am the delegator. ORG: for any delegator |
| `core.approval.policy.write` | ORG | Edit SLA policy. Guarded `minimum=Scope.ORG` |

No TEAM or LOCATION reading is defined: approval authority comes from the chain, not from scope.

**Role templates. Amended — the original plan could not reach a single existing customer, and the
fix is a platform capability, not an approval one.**

The original text assumed `ORG_OWNER` would pick these up "through `CORE.all(\"ORG\")`" and that a new
`APPROVAL_ADMIN` template would reach the people who need it. Verified against the code, neither
happens:

- `core` is installed **once, at organization creation**, from the in-code `CORE_REGISTRATION`
  (`account/app/services.py:123`) — never from `core.json`, which had **no reader at all** for an
  existing organization. (At review time the manifest's `sources` was `["account"]`, so approval's
  specs could not have reached a tenant through it either. Now that approval contributes its slice
  the manifest carries them, and ADR-0007's reconciler is what reads it — the manifest is a merge and
  a strict superset of the in-code registration, never its equal.)
- **`install_registration` skips any role code that already exists** (`if await
  get_role_by_code(...): continue`), so a live tenant's `MEMBER`, `ORG_ADMIN` and `ORG_OWNER` never
  gain a grant added later. `tools/wire_product.py` drives the same installer and inherits the same
  behaviour.
- The public roles API refuses to edit a system role, so nobody can patch it by hand either.

The consequence was not "HR is missing a role"; it was **every approval endpoint returning 403 to
every principal in every existing organization, and no holder anywhere for the unassigned queue** —
while new organizations worked perfectly, which is the worst way for this to present.

This is a platform gap that will recur on every catalogue change to every product, so it is settled
in its own decision: **[ADR-0007](0007-permission-rollout-to-live-tenants.md)**, which makes
installing a registration a *reconcile* (widen existing system roles, never narrow, never touch
customer-authored roles), run as a reviewed one-shot command in the deploy that ships the
permission, idempotent, audited, and bumping `pv` per organization only when something actually
changed. **This ADR depends on it.** The rollout order for the approval deploy is ADR-0007 §3's:
export the manifests, deploy approval, run `tools/reconcile_permissions.py core --all` (after
`--dry-run`), then enable the UI.

**Approval's own `catalogue.py` slices.** `services/approval/app/catalogue.py` declares a slice of
the **`core`** product — its four permissions and its share of four role templates. The exporter
merges slices across services by unioning grants per template code (`hrms.json` already does this
across `attendance` and `employee`), so `core.json`'s `sources` becomes `["account", "approval"]`.
Account never imports approval's catalogue; the manifest is the seam.

| Template | Approval's slice grants | Why |
|---|---|---|
| `MEMBER` (`is_default`) | `request.read:OWN`, `request.decide:OWN`, `delegation.write:OWN` | Everyone raises requests, decides what the chain sends them, and may hand over while away. Holding `decide:OWN` is not authority — §6's eligibility check is what decides |
| `ORG_ADMIN` | all four at ORG | The organization's administrator. SLA policy is org configuration, not billing, so it is in scope for the role defined as "people and structure, not billing" |
| `ORG_OWNER` | all four at ORG | Account's template is `CORE.all("ORG")` over account's own specs; approval's slice contributes its four. This is what guarantees **every organization ends ADR-0007's reconcile with at least one holder of `decide:ORG`**, so the unassigned queue always has an owner |
| `APPROVAL_ADMIN` (new) | all four at ORG, nothing else | "Approvals administrator". The brief's "HR admin gets ORG" still **cannot** be done as written — HRMS's `HR_ADMIN` is built from HRMS's catalogue and ADR-0000 §5's corollary forbids it granting `core.*` — so an HR administrator is given this alongside `HR_ADMIN`. The gateway already forwards core grants on an HRMS-audience token (§Context), so no token or gateway change is needed |

A brand-new template code *does* install correctly into a live tenant (only existing codes are
skipped), so `APPROVAL_ADMIN` lands everywhere on the reconcile — but **nobody holds it on day one**,
and it is deliberately not marked `is_admin`: core's admin template is `ORG_OWNER`, every `is_admin`
template is auto-granted to owners on installation, and a second one would be redundant. After the
merge `ORG_OWNER` holds 22 of core's grants against `APPROVAL_ADMIN`'s 4, so the exporter's
"admin-most template" rule keeps `ORG_OWNER` as core's admin with no annotation needed. An owner
hands `APPROVAL_ADMIN` to HR through the ordinary roles API.

**The attendance side: `hrms.time.attendance.request`.**

**Amended — the self-service endpoint needed a permission that does not exist.** The brief and the
screen specifications (**O7**) both use `hrms.time.attendance.request:OWN`, and verified against
`services/attendance/app/catalogue.py` the HRMS time catalogue has exactly four codes —
`attendance.read`, `attendance.write`, `shift.read`, `shift.write` — and none of them is it. It is
declared here rather than left to be inferred from a UI:

```
PermissionSpec("time", "attendance", "request",
               "Ask for a correction to your own attendance", "OWN", "OWN")
```

`max_scope` is **OWN**, not ORG. There is no meaningful "request a correction on behalf of everyone":
an administrator who wants to change someone's day uses the existing direct override
(`POST /attendance/{id}/regularize`, `write` at `minimum=ORG`), which is audited in its own domain
and is §6's designated escape hatch. Capping the ceiling at OWN makes "an ORG-wide request-for-others
power" unrepresentable rather than merely ungranted. It is added to the `EMPLOYEE` template at OWN
(and to `HR_ADMIN` at OWN, since an HR administrator is also an employee with their own attendance).

It reaches live tenants by exactly the same mechanism and has exactly the same failure without it:
`hrms` is installed per organization at product activation through `install_product_registration`,
which calls the same skipping installer, so a tenant that activated HRMS before this ships gains
nothing until `tools/reconcile_permissions.py hrms --all` runs. **Both reconciles belong in the same
deploy.**

**Holding `decide` is not enough.** Every member holds `decide:OWN`, so the permission check is only
the first gate. The eligibility check (§6) runs **in addition**, in `services.py` under the request
lock. It is never replaced by the permission and never by a UI gate.

Per endpoint:

| Endpoint | Guard | Object-level rule |
|---|---|---|
| `GET /inbox` | `decide` (any scope) | Pending requests where §6 holds for the caller; unassigned only with ORG. Requester's own excluded |
| `GET /requests` | `read` | OWN = `requester_employee_id = sref`; ORG = all. Via `ctx.apply()` |
| `GET /requests/{id}` | `read` | OWN: requester, **or anyone who appears on a step of this request** (`approver_employee_id` or `acting_for_employee_id`, any outcome), else 404; ORG: any. **Amended, see below** |
| `POST /requests/{id}/decision` | `decide` | §6 under lock; 403 when not eligible, 409 when decided |
| `GET /delegations` | `delegation.write` | OWN: delegator or delegate is `sref`; ORG: all |
| `POST /delegations` | `delegation.write` | OWN: `delegator_employee_id` must be `sref` (omitted → `sref`); another id needs ORG |
| `POST /delegations/{id}/revoke` | `delegation.write` | OWN: only if caller is the delegator; ORG: any |
| `GET /policies` | `read` (any scope) | Read-only, org-wide |
| `PUT /policies/{request_type}` | `policy.write`, `minimum=ORG` | — |
| `GET /chain/{employee_id}` | `read` | OWN: only `employee_id = sref`; ORG: any |
| `GET /delegate-candidates?q=` | `delegation.write` (any scope) | **New, amended in.** Non-exited `hierarchy_nodes` in the caller's organization, excluding the caller. `q` required, ≥2 characters, results capped at 20 |

**Amended — what the responses return, so the screens are shippable.** Approval holds UUIDs, and the
screen specifications' **C1** showed that returning them alone makes every screen render raw ids for
most callers (an ordinary approver's `hrms.people.employee.read:OWN` resolves nobody but themselves).
So, using the `display_name` now on the copy (§2):

- **Every id is accompanied by a name**: `requester_name`, `approver_name`, `acting_for_name`,
  `decided_by_name`, `delegator_name`, `delegate_name`. A name that cannot be resolved (no node yet)
  is `null` rather than absent, and the client renders its own fallback — the row stays operable.
- **`GET /inbox` returns `inbox_reason` per item**, one of `assigned | delegated_to_me |
  escalated_to_me | delegated_away | escalated_away | unassigned`, plus `from_name` where the reason
  names somebody (**C2**). This is derivable server-side from the steps the inbox already joins, and
  it is the field that tells an approver whether to act at all. Deriving it client-side would mean
  re-implementing §6 in the browser, which is the one place it must never live.
- **`POST /delegations` returns `repointed_count`** — how many open steps moved (**C4**). It is
  already computed in that transaction, so it is free. The matching per-row `open_request_count` on a
  delegation listing is **not** provided: it is a subquery per row against the largest table in the
  service (47 ms at 2,986 pending, data model §2 pattern 9) to render one line of copy. The screens
  already omit that line when the field is absent.

**Amended again — six response-shape questions the screen specifications raised (§9.2), settled here
because a response contract that omits a field is a decision whether or not anyone made it.**

- **`location_code` travels on every response that renders an instant** (**O4** — this blocked slice
  1). The copy holds this fact precisely so the edge can convert, and a contract that withholds it
  makes every timestamp fall back to organization time, silently, which is exactly what ADR-0000
  forbids. Following §2.1's mapping of *whose* location governs: `requester_location_code` on inbox
  items, `GET /requests` items and the request detail; `delegator_location_code` on delegation rows
  and on the create response; `location_code` for the subject employee on `GET /chain`. Not on
  `/policies` (durations have no zone) and not on `/delegate-candidates` (no instants).
  **When it is `null`** — an employee with no location, or a node the copy has not seen — the client
  falls back to the **organization** timezone **and labels it** ("Organization time (Asia/Kolkata)").
  The fallback is required to be visible: a silently wrong zone is worse than an obviously
  approximate one, because a due time is what an approver acts on. The browser's zone is never used.
  Approval returns the **code**, not the resolved IANA zone: the zone belongs to `account.locations`,
  the SPA already fetches and caches that list once per session, and copying a third service's fact
  onto this copy to save a cached lookup is how the department problem started.
- **`employee_no` accompanies the person being *identified*, not every person named** (**C6**). It
  travels with the **requester** on inbox items, `GET /requests` items and the detail; with each rung
  on `GET /chain`; and with each candidate on `/delegate-candidates`. It does **not** accompany
  approver, delegate or decider names in list rows. The rule behind the split: an employee number
  disambiguates the subject of a decision — two colleagues named the same, and an approver who
  approves the wrong person's correction is a real and unrecoverable error — whereas "who is holding
  it" is context, not identification. Adding it elsewhere later is additive.
- **`request_note` travels on the inbox list *and* the detail** (**C7**), and on nothing else. Triage
  is the inbox's entire purpose, and an approver who must open 25 rows to find the one they can
  answer will instead approve on the summary line alone. §3's rule 3 is satisfied by construction:
  the inbox only ever returns requests the caller is eligible on. The volume is bounded — the text
  is capped at 500 characters by attendance's own CHECK, so a 25-row page carries at most ~12 KB. It
  is **not** returned on `GET /requests` (the requester's own list), where nothing renders it.
- **`application_state` and `application_failure_reason` travel on `GET /requests` as well as the
  detail** (**C8**). "Approved but not applied" is most worth surfacing to the person who asked, and
  their own list is where they will look. The fields are on the row already, so this is no join and
  no cost. They are **not** added to the inbox: an inbox holds pending requests, and a pending
  request has no decision to apply, so the field would be `null` on every row forever.

**`GET /delegate-candidates` exists because the alternative was worse** (screens **O3**). The
delegation picker needs colleagues, and an ordinary employee holds `hrms.people.employee.read:OWN` —
so the picker is empty for exactly the people the feature is for. The two options were to widen an
HRMS employee-read scope for everybody, or to serve the lookup from data approval already holds. This
is the second: it reads `hierarchy_nodes`, returns `{employee_id, display_name, employee_no}` and
nothing else, needs no new employee scope, and exposes no PII (a name and a staff number, to a
colleague in the same tenant). It is gated on `delegation.write` rather than `read`, so it is
reachable only by someone who can actually create a delegation.

**Two contract dependencies belong to other services** and are named here so they are not discovered
mid-slice: `AttendanceOut.regularization {status, approval_request_id, requested_status,
waiting_on_name, decided_at, comment, failure_reason} | null` plus `allowed_transitions[]`
(attendance's surface, **C3** — `allowed_transitions` comes from the server's own
`ALLOWED_TRANSITIONS` so the dialog cannot duplicate a state machine), and `subject_ref` on `MeOut`
(auth's surface, **C5** — without it the SPA cannot tell which attendance rows are the caller's own,
so the correction control cannot be placed).

A detail endpoint that a caller cannot see returns **404, not 403**, so that request ids cannot be
probed. The decision endpoint returns **403** to a caller who can see the request but may not decide
it, because that caller already knows the request exists.

**Every endpoint carries a negative-authz test** (ADR-0003 §3). The ones this service specifically
needs:

- the requester holding `decide:ORG` cannot decide their own request, assigned or unassigned
- a `decide:OWN` holder who is not on the chain gets 403
- a `decide:ORG` holder cannot decide an assigned step
- a revoked or expired delegate cannot decide
- a caller with no `sref` cannot decide an unassigned step
- an OWN caller cannot read another employee's request or chain, or create or revoke another
  employee's delegation
- a cross-organization request id returns 404
- **an approver whose own node is `exited` cannot decide**, through any route (§6)
- **a `decide:ORG` holder *can* decide a step whose approver is exited**, and the audit row records it
- `GET /delegate-candidates` returns 403 without `delegation.write`, and never includes the caller,
  an exited node, or an employee from another organization
- `request_note` is absent from every published event payload and every audit row (§3)
- **a past approver can read a request they decided or were escalated past, and still cannot decide
  it** (§11's detail rule widened, §6 unchanged) — the read widens, the authority does not
- an employee who never appeared on a request's steps still gets 404 on it at OWN

### 12. Events and the `request_type` convention

**`request_type` is `<product>.<module>.<thing>`**, lowercase, three dot-separated segments. It is
the routing key of the requesting event without its `.requested` suffix: attendance publishes
`hrms.time.regularization.requested`, and the request type is `hrms.time.regularization`. Future
examples: `hrms.leave.application`, `operations.procurement.purchase_order`. The prefix is also the
product that must consume the decision.

**Consumed by approval** (all run on the app role, `session_scope(organization_id)` per message,
`already_processed` / `mark_processed` in the same transaction):

| Event | Payload approval relies on |
|---|---|
| `hrms.people.employee.onboarded` / `.updated` / `.transitioned` | existing fields **plus** `reporting_manager_id`, `dotted_line_manager_id`, `status`, `display_name`, `employee_no`, `location_code`, `hierarchy_version` (additive, per ADR-0000 §7). **Amended**: `display_name` and `employee_no` added, `department_id` dropped (§2) |
| `hrms.time.regularization.requested` | `organization_id`, `regularization_request_id` (→ `source_ref`), `attendance_id`, `employee_id` (→ requester); `date`, `current_status`, `requested_status` (→ `summary`); `reason` (→ `request_note`, §3) |
| `hrms.time.regularization.applied` | **Amended, new** (§12): `organization_id`, `regularization_request_id` (→ `source_ref`), `approval_request_id`, `request_type`, `outcome applied\|failed`, `failure_reason?` → `application_state` |

Approval binds each requesting event **explicitly**, with a per-type mapping in `consumers.py` from
payload to `(source_ref, requester_employee_id, summary)`. It does not wildcard-bind `#.requested`,
which would pull in events that are not approvals. There is one request type today, so a generic
payload contract would be an abstraction with one use case. **Revisit when the second request type
lands**: then standardise a common `approval` block in the payload and bind by convention.

**Published by approval**, all with `aggregate_type = "approval_request"` and `aggregate_id =
request_id`, each carrying `organization_id`, `request_id`, `request_type` and `source_ref` so that
every consumer can map the event back to its own row without a lookup:

| Event | Additional payload |
|---|---|
| `core.approval.request.opened` | `requester_employee_id`, `approver_employee_id` (null = unassigned), `acting_for_employee_id`, `due_at` |
| `core.approval.request.escalated` | `step_id`, `level`, `from_approver_employee_id`, `to_approver_employee_id` (null = unassigned), `acting_for_employee_id`, `due_at` |
| `core.approval.request.decided` | `outcome` (`approved`\|`rejected`), `decided_by_employee_id`, `acting_for_employee_id`, `comment`, `decided_at` |

JSON Schemas for all five payload shapes go in `libs/contracts`.

**How a product maps the decision back.** Attendance's consumer ignores any `request_type` it does
not own, then loads `regularization_requests` by `(organization_id, id = source_ref)` and records
`approval_request_id` from the event (it may already have it from `.opened`). It acts only if its
own row is still `pending`; otherwise the event is a no-op. That keeps a replay, or an `opened`
arriving after `decided`, harmless. On `approved` it applies the existing state-machine transition
with an audit row and `hrms.time.attendance.regularized`. If the transition has become illegal
(HR overrode the day directly in the meantime), the row goes to `failed` with a `failure_reason`, and
it does not raise. Raising would redeliver once and dead-letter a fact that is not an error. On
`rejected` the row goes to `rejected`.

**Amended — the product reports back, because otherwise approval says `approved` forever.**

The path above has a hole: when attendance records `failed`, approval still shows `approved`, for the
life of the record. The requester sees "Approved" in their request list and an uncorrected day on
their attendance record, and there is no surface anywhere — UI, query or alarm — that can find the
population where a decision never landed. "The product's status is the truth" (Consequences) is right
about which value to *render*, and no answer at all to *how anyone finds out*.

So the loop closes with a **return event**:

```
hrms.time.regularization.applied
  { organization_id, regularization_request_id (= source_ref), approval_request_id,
    request_type, outcome: applied | failed, failure_reason?, occurred_at }
```

Published by attendance from the same transaction that applies the decision. Approval consumes it
and sets `application_state`, `applied_at` and `application_failure_reason` on the request (§3).

**Amended — a rejection is reported too, and the whole return path ships in slice 2** (**O9**,
**O10**).

The event means *"I have applied your decision"*, not *"I corrected the day"*. So attendance
publishes it for **every** decision it consumes: an approval that lands is `applied`, an approval
whose transition has become illegal is `failed`, and a **rejection is `applied`** — the row moved to
`rejected`, which is the faithful application of that decision. The first draft of this section
implied rejections were silent, and that leaves the symmetric hole one level down: if attendance's
consumer never runs, a rejected request sits `rejected` in approval and `pending` in attendance
indefinitely, with nothing able to detect it. Reporting every decision closes both halves with one
binding.

**Slice 2**, with the consumer that applies the decision — not slice 4. The publisher is the same
transaction, the consumer is a few lines in approval, and deferring it would mean slices 2 and 3 show
a confident "Approved" that nothing can contradict. A return path that arrives two slices after the
claim it qualifies is a bug with a schedule.

- **One event with an `outcome` field, not two event types.** A separate `.apply_failed` would double
  the bindings, the JSON Schemas and the consumer branches for two halves of one fact — and approval
  needs the success case too, because "decided and applied" is what makes the *absence* of a report
  detectable.
- **It does not change `status`.** A decided request is frozen: first decision wins, enforced by
  `trg_approval_requests_decide_once` below the application. `application_state` is a separate,
  write-once trio, and the guard trigger is extended to permit exactly those three columns to move
  from NULL on an already-decided request — the same single-permitted-change shape the trigger family
  already uses for closing a step. Application is a fact *about* the decision, not a revision of it.
- **It gives the operator surface this ADR was otherwise missing.** Because every decision is
  reported, the probe is `status <> 'pending' AND application_state IS NULL` older than a few
  minutes — precisely "decisions that never reached the product", approvals and rejections alike — a
  liveness probe for the decision→product leg, to sit alongside the sweep and worker alarms in
  Consequences. Like every other cross-tenant probe it runs on the **outbox role**, which means it
  needs a real grant on `approval_requests` — six content-free columns, named in §3. The first draft
  of this ADR said that role held *no* grant on the table, which would have refused this alarm with
  `42501`: an alarm that fails closed on a permission error reports nothing and looks healthy.
  `application_state = 'failed'` is the population a human should look at, findable by a query
  rather than by a customer complaint. **`NULL` never means "something went wrong" on its
  own**: for the seconds between a decision and its report, and for any product that does not report
  back, it means "not yet told", which is why the probe is time-based.
- **Approval's own detail response carries it**, so the request screen can say "Approved, but the
  correction couldn't be applied: {reason}" rather than a confident "Approved" that contradicts the
  attendance row beside it.

A product that never publishes the return event is not broken — `application_state` stays NULL and
the alarm says so, which is the correct reading of "this product does not report back yet".

**No event for delegation re-pointing** in this ADR. It changes who holds a request but nobody
consumes that fact until notifications exist, and adding the event then is additive.

### 13. Audit

One audit row, in the same transaction as the change: `approval.request.opened`,
`approval.step.delegated` (one per re-pointed step), `approval.step.escalated`,
`approval.request.decided`, `approval.delegation.created`, `approval.delegation.revoked`,
`approval.policy.updated`. Sweep-driven rows have no human actor (`actor_id = NULL`,
`acting_as = "approval.sla_sweep"`, `reason = "sla_elapsed"`). Consumer-driven rows (open) carry
`actor_id = NULL` and `acting_as = "approval.consumer"`. A delegate's decision carries
`acting_as` = the represented employee id. The app role has no `UPDATE`/`DELETE` on `audit_logs`
(ADR-0002 step 0).

### 14. Deployment surface

**Amended — added. The service had no port, no route-table entry and no connection budget.**

| Decision | Value |
|---|---|
| Port | **8152** |
| `services/app-gateway/routes.json` | `{ "prefix": "/api/v1/approvals", "upstream": "http://localhost:8152" }` — **no `product` key** |
| `docs/architecture.md` port map | gains `approval :8152` on the PLATFORM line, beside `document :8150` and `notification :8151` |

**Why 8152.** The route table is authoritative for ports, and the bands it encodes are: `810x`
gateways, `8110–8119` identity and org (auth, account, product, config `8113`), `8120–8139` product
services, `8150–815x` platform, `8160–8169` money, `8170+` operator. Approval is a **platform**
service — a surface every customer has, with no entitlement, like `/api/v1/members` — so it belongs
beside `document` and `notification`, and 8152 is the next free slot there. Rejected: `8114`, because
the identity band is auth, account, product and config, and approval is none of those; and the
`8120+` product band, because putting a platform service among the HRMS services is exactly the
misreading that would later justify giving it a `product` key. Note the existing defect this avoids
repeating: **8170 is already claimed twice** (`projects` and `console`), which architecture.md records
as a live defect rather than a convention.

**No `product` in the route entry** is a decision, not an omission: a `product` key makes the gateway
demand an entitlement, and approvals must work for a customer who has bought nothing yet.

**Connection budget.** Four processes join the cluster per replica set: `bin/api`, `bin/worker`,
`bin/cron`, and the outbox relay. Defaults are `db_pool_size = 10` and `db_max_overflow = 5` per
process on the app engine, with the outbox engine fixed at 2 + 2. Taken as-is that is 45 app-role
connections plus 8 outbox-role connections — **53 per replica set, for one service**, against a
shared cluster. So the non-API processes are configured down:

| Process | App engine | Outbox engine | Rationale |
|---|---|---|---|
| `bin/api` | 10 + 5 | — | Concurrent HTTP; the default is the right shape |
| `bin/worker` | **4 + 2** | — | One consumer loop, one message at a time |
| `bin/cron` | **4 + 2** | 2 + 2 | One sweep pass; discovery on the outbox role, action on the app role |
| relay | — | 2 + 2 | One polling loop and a probe |

Ceiling: **~35 connections** per replica set. Stated here because the alternative is discovering it
as `FATAL: sorry, too many clients already` on the day a second replica is added — a failure that
presents as every service in the cluster degrading, not as approval misbehaving.

#### Consequential edits owned elsewhere

These amendments change documents this ADR does not own. They are listed so the divergence is
deliberate and short-lived, not discovered during implementation:

| Document | What must follow |
|---|---|
| `docs/approval-engine-data-model.md` §1.1 | `hierarchy_nodes` drops `department_id`, gains `display_name` and `employee_no` (§2) |
| `docs/approval-engine-data-model.md` §1.2 | `ck_approval_policies_sla_minutes_range` becomes `BETWEEN 15 AND 525600` (§8, §10) |
| `docs/approval-engine-data-model.md` §1.3 | `approval_requests` gains `request_note`, `application_state`, `application_failure_reason`, `applied_at` with their CHECKs; `summary` loses `reason` (§3, §12) |
| `docs/approval-engine-data-model.md` §1.4 | `trg_approval_requests_decide_once` permits the `application_*` trio to be set once on a decided request (§12) |
| `docs/architecture.md` port map | `approval :8152` on the PLATFORM line (§14) |
| `docs/design/approval-engine-screens.md` §4.A | the inbox's department sub-line is not available in these slices (§2); every other contract dependency it raised is resolved in §2, §3, §11 and §12 |
| `docs/design/approval-engine-screens.md` §9.2 | all six response-shape questions are now answered in §11 and §12: `location_code` on every instant-rendering response with a **labelled** org-time fallback (**O4**); `employee_no` with the requester, each chain rung and each candidate (**C6**); `request_note` on the inbox list and detail (**C7**, so the Reason column stays); `application_state` on `GET /requests` (**C8**, so "My requests" can show "Approved — not applied"); past participants may read a decided request (**O8**, so §4.B's 404 state and §3.2's note no longer apply to them); rejections are reported and the return path ships in **slice 2** (**O9**, **O10**, so §2.10 may render an application line for rejected requests) |

The persistence design is otherwise authoritative and was followed wherever it and this ADR
disagreed: the version trigger (§2), the advisory lock on the cycle check (§2), the step-outcome
vocabulary (§3) and the recursive walk (§5) are all its findings, adopted here.

---

## What this rejects

- **Synchronous API calls between products and approval.** Attendance calling `POST /approvals`
  inside its request transaction either commits a regularisation request whose approval never
  opened (call failed after commit) or holds a database transaction open across a network call
  (call before commit). Both are the failure the outbox exists to prevent (ADR-0000, *Also
  settled*). Approval calling back into attendance to apply a decision makes approval's availability
  a precondition of every product's writes, and gives approval write authority into another
  service's domain.
- **A platform-core library with per-service approval tables.** Five products would each carry
  their own `approval_steps` and delegations, and "who has delegated to whom" would live in five
  schemas. A delegation set once would have to be written five times, or it would silently apply
  only to leave and not to expenses. The routing rules would be one library at five different
  versions. The cross-product inbox, which is the thing an approver actually wants, would need a
  fan-out read across every service.
- **Routing on `account.memberships.manager_membership_id`.** It is not the field HR edits, and
  nothing keeps it in step with `employees.reporting_manager_id`.
- **Approval reading employee's schema, or calling employee per request, to walk the chain.** A
  cross-schema read forfeits the database-per-service split (ADR-0000 §10). A per-request call makes
  employee's availability a precondition of opening every approval, and gives routing no stable
  input to test against.
- **Ordering the hierarchy copy by `occurred_at`** (§2). Transaction-start timestamps do not order
  commits.
- **Re-routing open steps on hierarchy change, delegation revocation or policy edit** (§5, §9, §10).
- **Transitive delegation.** A chain of "out of office" rules could route a request to someone
  nobody chose, and could route it back to the requester.
- **`decide:ORG` as "approve anything"** (§6). The product's own audited override is the escape
  hatch.
- **Auto-approval as policy** (§10).
- **Retrying or dead-lettering a request whose requester is not in the copy** (§4).
- **A new cross-tenant bypass for the backfill** (§2).
- **Sealing the requester's free-text note** (§3). The same text sits in plaintext in attendance, and
  an audit row per inbox render destroys the trail that PII auditing depends on.
- **Copying `department_id` onto the hierarchy node** (§2). A UUID approval cannot render, with no
  consumer, that would need a second denormalised name from a third service to be useful.
- **A flat platform-wide `ORDER BY due_at` sweep batch** (§8) — it lets one tenant starve every other.
- **Walking the chain in Python over a loaded map of nodes** (§5) — 4,000 rows to answer a 33-row
  question, on a consumer's hot path.
- **A progress table for the one-time backfill** (§2.1). The upsert is idempotent and version-gated,
  so re-running is the resume mechanism; a permanent table for a one-time job is not.
- **A mandatory rejection comment enforced only in the UI** (§7).
- **Two event types for the product's apply/fail report** (§12).

## Consequences

- **The approval inbox is cross-product from day one.** A second request type adds a catalogue-free
  mapping entry and a consumer in the product. It adds no table, no endpoint and no routing code.
- **Hierarchy is eventually consistent in approval.** A manager change is visible to routing after
  the relay and the consumer have run (typically seconds). A request opened inside that window routes
  on the old chain and, by rule 6, stays there. That is accepted: the old manager is still a
  reasonable approver of a request raised before they were replaced, and SLA escalation bounds the
  harm.
- **Employee's events get heavier and its writes get a version column.** Five fields and a counter on
  three events, plus a migration for `dotted_line_manager_id` and `hierarchy_version`. It is additive
  on the envelope, so existing consumers are unaffected.
- **Two new operational loops with quiet failure modes.** A stopped sweep does not error; nothing
  ever escalates. A stopped worker does not error; requests never open, and employees see their
  regularisation sit at `pending` with no approver. Both need liveness alarms: the sweep on *oldest
  overdue open step*, the worker on queue depth, alongside the relay's outbox-lag probe.
- **An unassigned population exists and someone must own it.** Requests land there when a chain
  runs out, the copy is missing a node, or the open step's approver has exited (§6). `decide:ORG`
  holders see them in the inbox. The `ORG_OWNER` grant is what guarantees a holder in every
  organization — **and it only exists there once ADR-0007's reconcile has run.** Until then no
  organization has one, which is why that ADR is a dependency rather than a follow-up.
- **A decision that never reaches the product is now findable.** `application_state` (§12) turns
  "approved in approval, failed in attendance" from an invisible contradiction into a query and an
  alarm. The cost is a column trio, one consumed event and a loosened guard trigger on decided rows.
- **Slices 2 and 3 ship without a due date on any step**, and the overdue alarm is armed in slice 4
  with the escalation that answers it (§10).
- **Decisions are eventually applied.** "Approved" in approval precedes "regularized" in attendance
  by one relay and one consumer hop. The attendance row's own status is the product truth, and the
  UI shows that status rather than the approval status.
- **Steps are append-only history, and the request is the mutable aggregate**, the same split
  ADR-0005 draws between a cycle and its transitions.

## Deferred, with the reason each is safe to defer

- **Calendar-time SLA only.** `due_at = opened_at + sla_minutes`, weekends and holidays included.
  There is no holiday calendar in the platform yet. A request raised on Friday evening escalates on
  Saturday evening. When a calendar exists, only the `due_at` computation changes. The stored
  instant, the sweep and the discovery index do not.
- **No notification delivery.** Approval publishes `opened`, `escalated` and `decided`, and
  `services/notification` is an empty scaffold. Until a notification router exists, approvers find
  work by opening the inbox, and the SLA sweep is what keeps an unread request from stalling. The
  events are already shaped to carry what a notification needs.
- **Amount-based limits by level** (a manager may approve up to X, a director up to Y). This belongs
  with the first consumer that has an amount, the purchase order, and will be a policy extension
  with an ADR of its own. Regularisation has no amount, and designing limits against zero use cases
  would be guessing.
- **Cancellation.** A requester cannot withdraw a request, and a product cannot cancel one. The
  product-side `failed` path covers the case that matters now: the source row has moved on. Adding
  cancellation means a `cancelled` request status, a `.cancel_requested` event, and a race against a
  decision already under lock. That design needs a consumer that actually needs it.
- **Dotted-line and matrix routing** (§2), and **re-routing tools** for an operator who wants to move
  a stuck request. Both have their data in place and no rule yet.
- **The fate of `account.memberships.manager_membership_id`.**

## Revisit when

- A second request type ships: standardise the requesting-event payload (§12).
- A tenant's largest organization makes the per-request chain walk visible in latency. It is one
  indexed lookup per rung, at most 32: in that case materialise ancestor paths.
- Customers ask for SLA in working hours: a holiday calendar ADR.
- A real requirement for HR to take over an assigned request appears outside a product override.
  That would be a superseding decision on §6, not a quiet widening of `decide:ORG`.
- **An approver's inbox passes roughly 100k history steps**, which is the growth curve §2.1 of the
  persistence design says to watch — the inbox's cost is proportional to one approver's history, not
  to the table.
- **The per-tenant sweep cap of 20 becomes the binding constraint** for a legitimately large tenant
  (visible as steps escalating consistently later than `due_at + 60 s`). The answer then is a second
  sweep replica or a shorter poll, not a bigger per-tenant share.
- **Employee status and membership status get wired together.** §6's exclusion of exited approvers
  stays correct either way, but the reasoning in §9 would no longer rest on the absence of that path.
- **A second product needs `display_name` on its own copy of some other service's data.** Two
  denormalised-name copies is the point at which a shared read model deserves an ADR, rather than
  each service carrying its own.
