"""Tables owned by the approval service.

Everything here lives in the `approval` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). Persons are `employee_id` UUIDs throughout — approval has no notion of
a user or a membership (ADR-0006 §3).

**Scope of this slice.** Slice 1 landed `hierarchy_nodes` — the read-only copy of the reporting
line (ADR-0006 §2, data model §1.1) — and the platform mixins. Slice 2 adds the routing engine's
own four tables: `approval_policies`, `approval_requests`, `approval_steps` and `delegations`,
matching `docs/approval-engine-data-model.md` §1.2-§1.5 column-for-column, constraint-for-
constraint. The three guard triggers that make `approval_steps` append-only, `delegations`
revoke-only and `approval_requests` decide-once are database objects with no ORM representation
(SQLAlchemy has no notion of a trigger) — they are created directly in the migration and
documented there.

**Naming convention (data model §1.0.1).** `NAMING_CONVENTION` in `platform_core.models` keys a
`CheckConstraint`'s produced name on `name` alone: declare the *bare suffix* (`name="status"`)
and the convention prefixes it to `ck_<table>_status`. A `UNIQUE`, composite `ForeignKey` or
`Index` that also leads with `organization_id` must be named explicitly, or the convention's
single-column key collides with every other org-leading constraint on the same table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import OrgScopedMixin, TenantBase, TimestampMixin

SCHEMA = "approval"

#: The employment lifecycle states the copy carries (data model §1.1). Closed vocabulary,
#: mirrored from `employee.employees.status`; the CHECK below pins it at the database too.
STATUS_PROBATION = "probation"
STATUS_ACTIVE = "active"
STATUS_ON_NOTICE = "on_notice"
STATUS_EXITED = "exited"
HIERARCHY_STATUSES = (STATUS_PROBATION, STATUS_ACTIVE, STATUS_ON_NOTICE, STATUS_EXITED)


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class HierarchyNode(Base, OrgScopedMixin, TimestampMixin):
    """One employee's place in the reporting line, as employee-service last reported it.

    A read-only projection (ADR-0006 §2): the only writers are the hierarchy consumer and the
    one-time backfill, both gated by `source_version` — never a person, and never this service's
    own API. `employee.employees` is the source of truth; this table exists so the chain walk
    (data model §4.3, ADR-0006 §5) is one indexed lookup per rung instead of a cross-schema read
    or a per-request call into employee-service (both rejected in "What this rejects").

    `display_name` and `employee_no` travel on the copy — deliberately, and added after the
    original design — because approval otherwise stores people as bare UUIDs an ordinary
    approver cannot resolve (`hrms.people.employee.read:OWN` reaches only themselves).
    `display_name` is `employees.full_name`, which is outside employee's PII envelope, so
    copying it moves no ciphertext and needs no unmask permission. `location_code` is kept so a
    response can render an instant in the requester's zone; `department_id` is deliberately
    **not** copied — it has no consumer here (ADR-0006 §2).

    Ordering is a version, never a timestamp (ADR-0006 §2, "a version, not a timestamp"):
    `occurred_at` is the outbox row's `created_at`, a transaction-*start* time, and two
    concurrent edits can commit in the opposite order to the one they started in. The upsert
    (`app/services.py`) applies a row only when its `source_version` is strictly greater than
    the value stored here; equal is a duplicate, and a row with no version at all (an
    un-upgraded employee pod) is ignored rather than treated as version 0.

    Neither `reporting_manager_id` nor `dotted_line_manager_id` is a foreign key, even though
    the target lives in this same table: events arrive in any order, a report's `onboarded` can
    be consumed before their manager's, and a foreign key would reject that legitimate row and
    dead-letter it (data model §1.1). A dangling pointer is a fact the chain walk already
    handles — it simply ends, which routes to an unassigned step (ADR-0006 §4).
    """

    __tablename__ = "hierarchy_nodes"

    # OrgScopedMixin's own column carries `index=True`, which would emit a single-column
    # `ix_approval_hierarchy_nodes_organization_id`. Every table in this schema overrides that
    # default (data model §1.0): the composite primary key below already leads with
    # `organization_id`, so a duplicate leading-column index would cost write throughput and
    # serve no query.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, nullable=False
    )
    #: `employee.employees.id`, a plain UUID — never a cross-schema foreign key (ADR-0000 §10).
    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)

    #: Not a foreign key (see the class docstring). `NULL` for the top of a chain.
    reporting_manager_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Stored, never routed (ADR-0006 §2): no rule in this slice sends a step to a dotted-line
    #: manager. It exists so the chain endpoint can show it and label it as informational.
    dotted_line_manager_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: `employees.full_name`. Not a sealed field — see the class docstring.
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: `employees.employee_no`. Rendered beside the name to disambiguate two people who share it.
    employee_no: Mapped[str] = mapped_column(String(40), nullable=False)
    #: `account.locations.code`. The SPA resolves this against `GET /api/v1/locations`.
    location_code: Mapped[str | None] = mapped_column(String(40))
    #: The employee row's `hierarchy_version` this state came from. The ordering gate — never
    #: compared against `source_occurred_at`.
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Display and debugging only. **Never compared** (see the class docstring).
    source_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("status in ('probation','active','on_notice','exited')", name="status"),
        CheckConstraint("source_version >= 0", name="source_version_non_negative"),
        CheckConstraint(
            "reporting_manager_id is null or reporting_manager_id <> employee_id",
            name="manager_not_self",
        ),
        CheckConstraint(
            "dotted_line_manager_id is null or dotted_line_manager_id <> employee_id",
            name="dotted_not_self",
        ),
        CheckConstraint("length(btrim(display_name)) > 0", name="display_name_present"),
        CheckConstraint("length(btrim(employee_no)) > 0", name="employee_no_present"),
        # The inbox's route-(c) "abandoned approver" `EXISTS` (`app/selectors.py`'s
        # `_INBOX_CANDIDATES_CTE`) filters on exactly this predicate — measured at 11.6ms/516
        # buffers (`Seq Scan`) without it against 3.4ms/524 buffers (`Bitmap Index Scan`) with it,
        # on a 20,000-node organization with 3,200 open steps. No `status` column: the predicate
        # is the partial index's own `WHERE`, so using the index at all already implies it (data
        # model §1.0's reasoning for why the primary key above carries no redundant index).
        Index(
            "ix_hierarchy_nodes_org_exited",
            "organization_id",
            "employee_id",
            postgresql_where="status = 'exited'",
        ),
    )


class ApprovalPolicy(Base, OrgScopedMixin, TimestampMixin):
    """SLA policy for one request type (ADR-0006 §10, data model §1.2). Natural composite PK,
    same shape as `HierarchyNode` — this table has no reason to exist without an organization and
    a request type, so a surrogate id would only be a second way to name the same row.

    A missing row means the defaults (`enabled=True`, `sla_minutes=1440`); nothing seeds one per
    organization. Slice 2 writes `sla_minutes` on the row it creates (the floor from this
    migration's CHECK) but nothing yet *reads* it: every step in slices 2-3 opens with
    `due_at = NULL` regardless of policy (ADR-0006 §10 amended, task note 3) — the SLA sweep that
    would consume this value is slice 4.
    """

    __tablename__ = "approval_policies"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, nullable=False
    )
    request_type: Mapped[str] = mapped_column(String(120), primary_key=True)
    sla_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    __table_args__ = (
        # The floor is a CHECK, not an API rule (ADR-0006 §8, §10): no path — a PUT, a fixture, a
        # future bulk import — can write an SLA that lets one tenant manufacture overdue steps
        # faster than the sweep's per-organization cap can drain them.
        CheckConstraint("sla_minutes between 15 and 525600", name="sla_minutes_range"),
        CheckConstraint(
            r"request_type ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'",
            name="request_type_shape",
        ),
    )


class ApprovalRequest(Base, TenantBase):
    """The routing aggregate — one row per product request approval is routing (ADR-0006 §3,
    data model §1.3). Joined to the product's own row by `(organization_id, request_type,
    source_ref)`, never by a foreign key (ADR-0000 §10): the product row lives in another
    service's schema.

    `status` moves exactly once, away from `pending`; the write-once `application_*` trio moves
    exactly once more, after a decision, and only via the product's return event (ADR-0006 §12).
    Both rules are enforced twice — by the CHECKs below, which are what an `INSERT` cannot evade,
    and by `trg_approval_requests_decide_once` (created in a later migration), which is what an
    `UPDATE` cannot.
    """

    __tablename__ = "approval_requests"

    # OrgScopedMixin defaults to `index=True`, a single-column `ix_..._organization_id`.
    # Overridden here (data model §1.0): every index below already leads with `organization_id`,
    # so a duplicate leading-column index would cost write throughput on this service's
    # highest-write table and serve no query that isn't already served.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=False
    )

    request_type: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The product row's id — `attendance.regularization_requests.id` for this slice's one type.
    source_ref: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: The product's statement of whose request it is — never the caller's token (ADR-0006 §4).
    requester_employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: Structured, enumerable facts only — dates and closed vocabularies. No field a human can
    #: type into, so "no free text" is true by construction (ADR-0006 §3, amended).
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: The requester's free text (regularisation's `reason`), classified customer content rather
    #: than PII and deliberately unsealed (ADR-0006 §3). Never on a published event payload, an
    #: audit before/after blob or a log line — see `app/services.py` and `app/consumers.py`.
    request_note: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    #: Approval's own clock, not the producer's (ADR-0006 §8's "one clock, and it is the
    #: database's" — read via `SELECT now()` in the same transaction that inserts this row).
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by_employee_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Set only when eligibility came through route (a) on a delegated step (ADR-0006 §7).
    acting_for_employee_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    #: `applied|failed`. NULL until the product reports back (ADR-0006 §12) — not an error state,
    #: "not yet told" (data model's own reading, repeated so nobody re-derives it as a bug).
    application_state: Mapped[str | None] = mapped_column(String(20))
    application_failure_reason: Mapped[str | None] = mapped_column(String(500))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Both lead with `organization_id`; left to the convention both would be named
        # `uq_approval_requests_organization_id` and the second create would fail (data model
        # §1.0.1 rule 3).
        UniqueConstraint(
            "organization_id",
            "request_type",
            "source_ref",
            name="uq_approval_requests_org_type_source",
        ),
        # The target of `approval_steps`'s composite FK — an integrity index, not a query index;
        # without it a step could name a request in another tenant and nothing would object.
        UniqueConstraint("organization_id", "id", name="uq_approval_requests_org_id"),
        CheckConstraint(
            "(status = 'pending') = (decided_at is null) "
            "and (status = 'pending') = (decided_by_employee_id is null)",
            name="decided_iff_not_pending",
        ),
        CheckConstraint(
            "acting_for_employee_id is null or decided_by_employee_id is not null",
            name="acting_for_needs_decider",
        ),
        CheckConstraint(
            "decided_by_employee_id is null or decided_by_employee_id <> requester_employee_id",
            name="decider_not_requester",
        ),
        CheckConstraint("decided_at is null or decided_at >= opened_at", name="decided_after_open"),
        CheckConstraint("status in ('pending','approved','rejected')", name="status"),
        CheckConstraint("jsonb_typeof(summary) = 'object'", name="summary_is_object"),
        CheckConstraint("not jsonb_exists(summary, 'reason')", name="summary_no_free_text"),
        CheckConstraint(
            "request_note is null or length(btrim(request_note)) > 0", name="request_note_present"
        ),
        CheckConstraint(
            r"request_type ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'",
            name="request_type_shape",
        ),
        CheckConstraint("application_state in ('applied','failed')", name="application_state"),
        CheckConstraint(
            "(application_state is null) = (applied_at is null)",
            name="applied_at_iff_application_state",
        ),
        CheckConstraint(
            "(application_failure_reason is not null) "
            "= (coalesce(application_state, '') = 'failed')",
            name="application_failure_reason_iff_failed",
        ),
        CheckConstraint(
            "application_state is null or status <> 'pending'", name="application_needs_decision"
        ),
        CheckConstraint(
            "applied_at is null or applied_at >= decided_at", name="applied_after_decided"
        ),
        # Pattern 1 (inbox): the pending side of the steps-first UNION join — partial, so it
        # stays small regardless of how large the decided history grows (data model §2.1).
        Index(
            "ix_approval_requests_org_pending",
            "organization_id",
            "opened_at",
            postgresql_where="status = 'pending'",
        ),
        # Pattern 5 (My requests, OWN).
        Index(
            "ix_approval_requests_org_requester_opened",
            "organization_id",
            "requester_employee_id",
            "opened_at",
        ),
        # Pattern 6 (My requests, ORG / the ORG request list).
        Index("ix_approval_requests_org_opened", "organization_id", "opened_at"),
        # Pattern 18 (data model §2.2a): decisions that never reached the product. Armed from
        # slice 2 — unlike the SLA/overdue alarms, it is meaningful the moment the first decision
        # is taken (data model §7's timing note), so it lands with the table rather than in
        # slice 4. `INCLUDE` keeps the alarm's own read index-only.
        Index(
            "ix_approval_requests_unapplied",
            "decided_at",
            postgresql_include=("organization_id", "id", "request_type"),
            postgresql_where="status <> 'pending' and application_state is null",
        ),
    )


class ApprovalStep(Base, TenantBase):
    """Append-only routing history (ADR-0006 §3, data model §1.4). The database — not this
    class — is what makes it append-only: the app role holds no `DELETE`, and
    `trg_approval_steps_append_only` (created in a later migration) permits exactly one kind of
    `UPDATE`, `outcome='open'` closing with `closed_at` set, every other column unchanged.

    **Invariant: a pending request has exactly one open step**, enforced by
    `uq_approval_steps_open_per_request` below — the concurrency control for the whole service.
    """

    __tablename__ = "approval_steps"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=False
    )
    request_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: NULL = unassigned. Never a foreign key — see `HierarchyNode`'s docstring on why the copy's
    #: own manager pointers aren't either.
    approver_employee_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: The represented original approver — set iff `delegation_id` is (ADR-0006 §5).
    acting_for_employee_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    delegation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: NULL in slices 2-3, always (ADR-0006 §10 amended) — no SLA clock exists yet to set it.
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="open")

    __table_args__ = (
        # Named after the *pointer* column, not `organization_id` (data model §1.0.1 rule 4) —
        # left unnamed both composite FKs on this table would generate
        # `fk_approval_steps_organization_id_<table>` and collide.
        ForeignKeyConstraint(
            ["organization_id", "request_id"],
            ["approval_requests.organization_id", "approval_requests.id"],
            name="fk_approval_steps_request_id_approval_requests",
        ),
        ForeignKeyConstraint(
            ["organization_id", "delegation_id"],
            ["delegations.organization_id", "delegations.id"],
            name="fk_approval_steps_delegation_id_delegations",
        ),
        CheckConstraint("level between 1 and 33", name="level_range"),
        CheckConstraint(
            "outcome in ('open','delegated','escalated','approved','rejected')", name="outcome"
        ),
        CheckConstraint(
            "reason in ('assigned','delegated','escalated','unassigned')", name="reason"
        ),
        CheckConstraint(
            "(approver_employee_id is null) = (reason = 'unassigned')",
            name="unassigned_iff_no_approver",
        ),
        CheckConstraint(
            "approver_employee_id is not null or due_at is null", name="unassigned_has_no_due"
        ),
        CheckConstraint(
            "(reason = 'delegated') = (delegation_id is not null)", name="delegated_iff_delegation"
        ),
        CheckConstraint(
            "(delegation_id is null) = (acting_for_employee_id is null)",
            name="acting_for_iff_delegation",
        ),
        CheckConstraint(
            "acting_for_employee_id is null or acting_for_employee_id <> approver_employee_id",
            name="acting_for_not_approver",
        ),
        CheckConstraint("(outcome = 'open') = (closed_at is null)", name="closed_iff_not_open"),
        CheckConstraint("closed_at is null or closed_at >= opened_at", name="closed_after_open"),
        CheckConstraint(
            "approver_employee_id is not null or outcome in ('open','approved','rejected')",
            name="unassigned_outcomes",
        ),
        # The invariant — a pending request has exactly one open step — and the decision path's
        # "find the open step" lookup (pattern 8). A plain `UniqueConstraint` cannot take a
        # `WHERE`, so the partial uniqueness the data model specifies is a `postgresql_where`
        # `Index`, unique=True, rather than a `UniqueConstraint`.
        Index(
            "uq_approval_steps_open_per_request",
            "organization_id",
            "request_id",
            unique=True,
            postgresql_where="outcome = 'open'",
        ),
        # Pattern 1 (inbox): caller as direct or delegated approver.
        Index("ix_approval_steps_org_approver", "organization_id", "approver_employee_id"),
        # Pattern 1 (inbox): caller as the represented original (route b).
        Index("ix_approval_steps_org_acting_for", "organization_id", "acting_for_employee_id"),
        # Pattern 2: the ORG unassigned queue.
        Index(
            "ix_approval_steps_unassigned_open",
            "organization_id",
            "opened_at",
            postgresql_where="outcome = 'open' and approver_employee_id is null",
        ),
        # Pattern 7: request detail / history, in opened order.
        Index("ix_approval_steps_org_request", "organization_id", "request_id", "opened_at"),
    )


class Delegation(Base, TenantBase):
    """Who stands in for whom, and when (ADR-0006 §5, data model §1.5). Not written by any
    endpoint in this slice — the CRUD surface (`POST/GET /delegations`, the picker) ships in
    slice 3 — but the table exists now because routing consults it: applying an *already
    existing* delegation at step open (ADR-0006 §5) is this slice's job, even though nothing yet
    lets a caller create one through the API. Slice 2's fixtures create rows directly.

    `trg_delegations_revoke_only` (created in a later migration) is the append-only sibling for
    this table: the app role may set exactly `revoked_at`/`revoked_by_employee_id`, once.
    """

    __tablename__ = "delegations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=False
    )
    delegator_employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    delegate_employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_employee_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        # The target of `approval_steps`'s composite FK, same reason as
        # `uq_approval_requests_org_id`.
        UniqueConstraint("organization_id", "id", name="uq_delegations_org_id"),
        CheckConstraint("delegate_employee_id <> delegator_employee_id", name="not_self"),
        CheckConstraint("ends_at > starts_at", name="window_forward"),
        CheckConstraint(
            "(revoked_at is null) = (revoked_by_employee_id is null)", name="revoked_pair"
        ),
        # The convention has no key for `ExcludeConstraint` at all (data model §1.0.1 rule 6): an
        # unnamed one keeps `name=None`, and Postgres invents a name that matches nothing here.
        # `btree_gist` is already installed by `tools/dev-bootstrap/init-db.sql`, so no extension
        # DDL is needed in the migration — only the constraint.
        ExcludeConstraint(
            ("organization_id", "="),
            ("delegator_employee_id", "="),
            (text("tstzrange(starts_at, ends_at, '[)')"), "&&"),
            where=text("revoked_at is null"),
            name="ex_delegations_no_overlap",
            using="gist",
        ),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe.

    Unused by this slice — the hierarchy copy publishes nothing of its own — but created now
    because every service carries the mixin from its first migration (data model §1.6), and
    slice 2's `core.approval.request.*` events land in the same table.
    """


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once.

    Deliberately un-policied (data model §1.6, ADR-0002 §5): it has no `organization_id`, and a
    tenant policy on it would make every redelivery lookup return nothing, silently reprocessing
    every redelivered event.
    """


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
