"""Tables owned by the approval service.

Everything here lives in the `approval` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). Persons are `employee_id` UUIDs throughout — approval has no notion of
a user or a membership (ADR-0006 §3).

**Scope of this slice.** ADR-0006's persistence design (docs/approval-engine-data-model.md)
specifies six approval tables plus the three platform mixins. This slice lands exactly two
things: `hierarchy_nodes` — the read-only copy of the reporting line (ADR-0006 §2, data model
§1.1) — and the platform mixins (`outbox`, `processed_events`, `audit_logs`). `approval_policies`,
`approval_requests`, `approval_steps` and `delegations`, and the guard triggers that make the
last three append-only / write-once, land in slice 2 with the routing engine itself. There is
nothing to guard yet: the append-only and decide-once triggers exist to protect tables this
slice does not create.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import OrgScopedMixin, TimestampMixin

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
