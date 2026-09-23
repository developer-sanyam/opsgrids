"""Tables owned by the leave service — leave types, the append-only balance ledger, leave
requests, per-organization settings, and a thin read-only copy of the employee hierarchy.

Everything here lives in the `leave` schema and nothing outside it may hold a foreign key into
it (ADR-0000 §10). Every UUID that names a row in another service's schema —
`employee_id`, `leave_type_id`'s sibling references aside (those stay in this schema) — is a
plain column, never a constraint that crosses a schema boundary.

**Leave requests route through the approval engine (ADR-0006), the same shape attendance's
`RegularizationRequest` already uses**: this service owns the product fact (`LeaveRequest`, its
own `pending|approved|rejected|cancelled|failed` status), approval owns the routing fact, and the
two are joined only by `(organization_id, request_type, source_ref)` — never a foreign key.

**Balances are an append-only ledger** (CLAUDE.md): `LeaveLedgerEntry` is never updated or
deleted (enforced by a table-privilege revoke, the same mechanism `metering.usage_events` and
`billing.audit_logs` use — see the `ledger append-only` migration and `migrations/env.py`), and a
balance is always `sum(delta_half_days)` over the rows for one employee and leave type, never a
stored running total. A grant, an accrual, a consumption and a reversal are each a new row.

**Half-day granularity, stored as integers** (CLAUDE.md: no floats). A full day is two
half-days; `LeaveRequest.total_half_days` and every `LeaveLedgerEntry.delta_half_days` count in
that unit.

**The timezone defect this rebuild is written not to repeat.** The version of this platform
being replaced derived a day from `datetime.utcnow().date()` on the server — visible today in
`attendance.services.check_in`'s `work_date = datetime.now(UTC).date()`, which mis-dates a punch
made near midnight in any timezone behind or ahead of UTC. `LeaveRequest.start_date` and
`end_date` are never derived that way: they are calendar dates the employee names explicitly (a
date picker, resolved against the location's timezone at the edge — ADR-0000 "Also settled" —
never the server's clock), and nothing in this schema computes one from a UTC instant. The one
place this service's own code *does* need "today" — refusing to withdraw a leave that has
already run its course, `services.cancel_leave_request` — resolves it from `LeaveSettings.timezone`
via `domain.today_in`, never from a bare `datetime.now(UTC).date()`. See `domain.py`.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import OrgScopedMixin, TenantBase, TimestampMixin

SCHEMA = "leave"

#: The employment lifecycle states the hierarchy copy carries — mirrored from
#: `employee.employees.status` / `approval.hierarchy_nodes.status` (data model §1.1). Closed
#: vocabulary, pinned at the database by the CHECK below.
DIRECTORY_STATUSES = ("probation", "active", "on_notice", "exited")

LEAVE_REQUEST_STATUSES = ("pending", "approved", "rejected", "cancelled", "failed")

LEDGER_ENTRY_TYPES = ("grant", "accrual", "consumption", "reversal", "adjustment")
#: Entry types that must name the request they belong to. A grant or accrual is not about any
#: one request; a consumption and its reversal always are.
LEDGER_ENTRY_TYPES_WITH_REQUEST = ("consumption", "reversal")


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class LeaveSettings(Base, OrgScopedMixin, TimestampMixin):
    """One row per organization: the timezone "today" is resolved in for this service's one
    clock-dependent rule (`services.cancel_leave_request`; see the module docstring).

    A missing row means the default (`timezone = "Asia/Kolkata"`, matching
    `account.organizations.timezone`'s own default) — the same "missing row is the default,
    created on first PUT" shape `approval.approval_policies` uses (ADR-0006 §10). No provisioning
    hook writes one per organization; that would be a second source of truth for a default that
    already lives in code.

    **Deliberately org-wide, not per-location, in this slice.** ADR-0000 says a timestamp is
    "converted at the edge using the relevant location's timezone", and the fullest reading of
    that would resolve each employee's own location. Getting there needs a location's timezone
    resolved cross-service (`account.locations` is not reachable from here without a new
    internal endpoint that does not exist yet), which is a real slice of work on `account`, not
    this one. One organization-wide timezone is coarser but is resolved entirely from data this
    service already owns, is correct for the overwhelming majority of tenants (one country, one
    office timezone), and is never browser-supplied — which is the property ADR-0000 actually
    requires. Per-location precision is a documented follow-up once `account` exposes a
    timezone-by-location read.
    """

    __tablename__ = "leave_settings"

    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")


class EmployeeDirectory(Base, OrgScopedMixin, TimestampMixin):
    """A thin, read-only copy of the reporting line — just enough to scope a leave request to a
    manager's team and to know whether an employee has exited. Fed by the same three employee
    lifecycle events approval's `hierarchy_nodes` consumes (`app/consumers.py`), and versioned
    the same way (ADR-0006 §2): applied if and only if `source_version` is strictly greater than
    what is stored.

    Deliberately narrower than approval's copy: no `dotted_line_manager_id`, `display_name`,
    `employee_no` or `location_code`, because nothing here renders a name or resolves a
    timezone from it (see `LeaveSettings`). Adding a field later is additive on both the event
    (already carried) and this table.

    Composite primary key, no surrogate `id` — the same shape `approval.hierarchy_nodes` and
    `approval.approval_policies` use for a natural-key reference table (data model §1.0), and
    the same reason: there is nothing to soft-delete here, so `TenantBase` is not mixed in.
    """

    __tablename__ = "employee_directory"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, index=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    #: Plain UUID, never a foreign key even within this schema — the copy is fed by events that
    #: can arrive in any order, so a report's row may exist before their manager's (ADR-0006 §2,
    #: data model §1.1's identical note on `hierarchy_nodes.reporting_manager_id`).
    reporting_manager_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(f"status in {DIRECTORY_STATUSES!r}", name="status"),
        CheckConstraint("source_version >= 0", name="source_version_non_negative"),
        CheckConstraint(
            "reporting_manager_id is null or reporting_manager_id <> employee_id",
            name="manager_not_self",
        ),
        # Serves the "which of my reports have a leave balance" read (a manager holding
        # `hrms.time.leave.request.read` at TEAM), and the abandoned-approver-style exited check
        # if a later slice needs one.
        Index(
            "ix_employee_directory_org_manager",
            "organization_id",
            "reporting_manager_id",
            "employee_id",
        ),
    )


class LeaveType(Base, TenantBase):
    """An organization's leave type: casual, sick, earned, and so on. Org-wide reference data —
    everyone reads it, only an administrator writes it — the same shape `attendance.Shift`
    follows for the identical reason.

    The accrual rule is kept deliberately simple (this slice's stated scope): an annual quota, an
    optional flat monthly accrual, and an optional carry-forward cap, all in half-days. None of
    the three is enforced by a cron in this slice — there is no scheduled job that posts a grant
    or an accrual row, and no year-end carry-forward sweep. An administrator posts a grant
    through `POST /leave/balances/grant` (idempotent), which is exactly the ledger write a future
    cron would make; adding the cron is additive, not a schema change.
    """

    __tablename__ = "leave_types"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    annual_quota_half_days: Mapped[int | None] = mapped_column(Integer)
    monthly_accrual_half_days: Mapped[int | None] = mapped_column(Integer)
    carry_forward_cap_half_days: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint(
            "annual_quota_half_days is null or annual_quota_half_days >= 0",
            name="annual_quota_non_negative",
        ),
        CheckConstraint(
            "monthly_accrual_half_days is null or monthly_accrual_half_days >= 0",
            name="monthly_accrual_non_negative",
        ),
        CheckConstraint(
            "carry_forward_cap_half_days is null or carry_forward_cap_half_days >= 0",
            name="carry_forward_cap_non_negative",
        ),
        # Partial on `deleted_at is null`, the same shape `attendance.Shift`'s
        # `uq_shifts_org_code` uses and for the same reason: a retired code is free to be
        # reused. `selectors.get_leave_type_by_code`'s duplicate check filters the same way.
        Index(
            "uq_leave_types_org_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at is null"),
        ),
    )


class LeaveRequest(Base, TenantBase):
    """One employee's ask for leave over a date range — the product fact, joined to its
    `approval.approval_requests` counterpart only by `(organization_id, request_type,
    source_ref)`, never a foreign key (ADR-0006 §1).

    `start_date`/`end_date` are plain calendar dates, named by the employee — never derived from
    a UTC instant (see the module docstring). `total_half_days` is computed once, at apply time,
    by the pure function `domain.count_half_days`, and stored: it is what the balance check and
    every screen reads, so it must not silently drift if a later change touched how weekends are
    counted.

    `reporting_manager_id` is a **snapshot** of `EmployeeDirectory.reporting_manager_id` at the
    moment the request was raised — never re-read live — which is what lets `scope.py`'s TEAM
    resolver be a plain column comparison, and is the same "a request routes on the chain as it
    stood when it opened, hierarchy changes never re-route" principle ADR-0006 §9 states for
    approval's own steps applied to this service's one denormalized copy of it. `NULL` means the
    requester had no directory row yet when they applied (the copy had not caught up) — an HR
    administrator (`read:ORG`) still sees the request; a manager (`read:TEAM`) will not, until a
    later employee event repopulates the copy. This mirrors ADR-0006 §4's identical tradeoff for
    a requester missing from the hierarchy copy.

    The `.opened`/`.decided` fields below are populated late, from approval's own events, the
    same shape and the same "tolerates arriving in either order, no-ops once the row is no longer
    pending" discipline as `attendance.RegularizationRequest` (see that model's docstring and
    this service's `app/consumers.py`).
    """

    __tablename__ = "leave_requests"

    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leave.leave_types.id"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    half_day_start: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    half_day_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    total_half_days: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    #: pending | approved | rejected | cancelled | failed. `failed` is what "the approval landed
    #: but consumption was no longer legal" looks like — mirrors
    #: `attendance.RegularizationRequest.status`'s identical use of the word.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    #: Snapshot at apply time — see the class docstring.
    reporting_manager_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    approval_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approver_employee_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    approver_name: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(Text)
    decided_by_name: Mapped[str | None] = mapped_column(String(200))
    failure_reason: Mapped[str | None] = mapped_column(String(500))

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        CheckConstraint(f"status in {LEAVE_REQUEST_STATUSES!r}", name="status"),
        CheckConstraint("end_date >= start_date", name="end_after_start"),
        CheckConstraint("total_half_days > 0", name="total_half_days_positive"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_present"),
        # A single-day request cannot be a half-day at the start but a full day at the end (or
        # vice versa) — there is only one day. `domain.count_half_days` enforces the same rule
        # in Python before the row is ever built; this is the database-level backstop.
        CheckConstraint(
            "start_date <> end_date or half_day_start = half_day_end",
            name="single_day_half_flags_agree",
        ),
        # Not a full equality with `status in ('approved', 'rejected')`, unlike
        # `attendance.RegularizationRequest`'s identical-looking constraint — leave adds
        # `cancelled` as a status reachable from *either* side of a decision: a `pending`
        # request withdrawn before anyone decided it carries no `decided_at` at all, while one
        # cancelled *after* being approved keeps the `decided_at` its approval stamped. The one
        # fact that always holds regardless of that history is the left-to-right half: a request
        # still `pending` has never been decided.
        CheckConstraint(
            "status <> 'pending' or decided_at is null",
            name="pending_has_no_decision",
        ),
        CheckConstraint(
            "(cancelled_at is not null) = (status = 'cancelled')",
            name="cancelled_iff_cancelled_status",
        ),
        CheckConstraint(
            "(status = 'failed') = (failure_reason is not null)",
            name="failure_reason_iff_failed",
        ),
        # No two pending-or-approved requests for the same employee may cover overlapping
        # calendar dates — enforced at the database, not only as a friendly pre-check in
        # `services.py`, the same `btree_gist` exclusion shape `approval.delegations` uses for
        # its own overlap rule (data model §1.5). `btree_gist` is already installed by
        # `tools/dev-bootstrap/init-db.sql`.
        ExcludeConstraint(
            ("organization_id", "="),
            ("employee_id", "="),
            (text("daterange(start_date, end_date, '[]')"), "&&"),
            where=text("status in ('pending','approved')"),
            name="ex_leave_requests_no_overlap",
            using="gist",
        ),
        Index(
            "ix_leave_requests_org_employee_created",
            "organization_id",
            "employee_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_leave_requests_org_manager_created",
            "organization_id",
            "reporting_manager_id",
            text("created_at DESC"),
        ),
    )


class LeaveLedgerEntry(Base, TenantBase):
    """One posting to one employee's balance for one leave type. **Append-only**: `UPDATE` and
    `DELETE` are revoked from the application role at the database (the `ledger append-only`
    migration and `migrations/env.py`, the same mechanism `metering.usage_events` uses), so a
    correction is always a new, compensating row — never an edit (CLAUDE.md).

    A balance is `sum(delta_half_days)` over the rows for one `(organization_id, employee_id,
    leave_type_id)` — never a stored running total, so there is nothing to drift out of step with
    its own history.

    `idempotency_key` makes a client-retried grant safe: the same key posted twice is the same
    row, not two (`uq_leave_ledger_entries_org_idempotency_key`, partial on the key being
    present — a `null` key never collides with another `null` key).
    """

    __tablename__ = "leave_ledger_entries"

    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leave.leave_types.id"), nullable=False
    )
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Positive credits the balance (grant, accrual, reversal); negative debits it (consumption).
    delta_half_days: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Set iff `entry_type` is `consumption` or `reversal` — the leave request this posting
    #: belongs to. A real foreign key: `leave_requests` is in this schema.
    leave_request_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("leave.leave_requests.id")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        CheckConstraint(f"entry_type in {LEDGER_ENTRY_TYPES!r}", name="entry_type"),
        CheckConstraint("delta_half_days <> 0", name="delta_half_days_non_zero"),
        CheckConstraint(
            f"(entry_type in {LEDGER_ENTRY_TYPES_WITH_REQUEST!r}) = (leave_request_id is not null)",
            # Kept short deliberately: `ck_leave_ledger_entries_` is already 24 bytes, and
            # Postgres truncates an identifier at 63 — a longer suffix here produced a name
            # Postgres silently truncated-and-hashed at creation, which then read as permanent
            # drift to `alembic check` (data model precedent: approval's own §1.0.1 rule 7).
            name="request_id_required",
        ),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_leave_ledger_entries_org_idempotency_key",
        ),
        Index(
            "ix_leave_ledger_entries_org_employee_type",
            "organization_id",
            "employee_id",
            "leave_type_id",
        ),
        Index(
            "ix_leave_ledger_entries_org_request",
            "organization_id",
            "leave_request_id",
        ),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
