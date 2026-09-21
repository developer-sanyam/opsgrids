"""Tables owned by the attendance service — the daily attendance record, shift definitions
and the field-punch geo-fences.

Everything here lives in the `attendance` schema and nothing outside it may hold a foreign
key into it (ADR-0000 §10). The link to employee-service — `attendance_records.employee_id` —
is a plain UUID, never a constraint that crosses a schema boundary; referential integrity for
it is the nightly orphan check. The one foreign key that *is* declared, `attendance_records.
shift_id → shifts.id`, stays inside this schema, which is exactly what the rule permits.

**Geo-fencing is a query, not app-side maths** (CLAUDE.md). A geofence keeps its centre as plain
`Numeric` latitude/longitude and a radius in metres — no PostGIS geometry column, no
geoalchemy2. The distance test is a raw `ST_DWithin(...::geography, ...)` executed in
`services.check_in`, so the platform gains the PostGIS answer without a new dependency or a
geometry type to migrate.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "attendance"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Shift(Base, TenantBase):
    """An organization's shift definition — a named window with a grace period.

    Shifts are org-level reference data, not personal: everyone in the organization reads them,
    only an administrator writes them, which is why the catalogue floors both scopes at ORG.
    `code` is the human-facing handle ("GEN", "NIGHT") and is unique within an organization, not
    globally — two customers may both call a shift "GEN".
    """

    __tablename__ = "shifts"

    #: Human-facing code such as "GEN". Unique within an organization.
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    #: Minutes a check-in may lag `start_time` before it counts as late. Zero by default.
    grace_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: A night shift crosses midnight, so `end_time` is chronologically *before* `start_time`.
    #: Stored rather than derived, so the intent is explicit rather than inferred from the times.
    is_night: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        # Every index leads with `organization_id`: under row-level security the policy qual is
        # ANDed into every query, so an index that did not lead with the tenant column would
        # scan the whole platform's rows and discard almost all of them (ADR-0002). Partial on
        # `deleted_at is null` (migration `f4c1a9b3d2e7`) so a retired code is free to be reused —
        # "I deleted it and now cannot recreate it" is a support ticket waiting to happen. The
        # duplicate check in `services.create_shift` already filters `deleted_at IS NULL`
        # (`selectors.get_shift_by_code`), so it agrees with this index's condition exactly.
        Index(
            "uq_shifts_org_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at is null"),
        ),
    )


class Geofence(Base, TenantBase):
    """The allowed area for a field punch at one location — a circle on the earth.

    Kept as centre latitude/longitude and a radius in metres rather than a PostGIS geometry
    column: the containment test is a query (`ST_DWithin` over `geography`) run at check-in, so
    there is nothing geometric to store or migrate here. `location_code` is the same code a
    field punch and an employee record carry, unique per organization.
    """

    __tablename__ = "geofences"

    #: The location this fence guards — an `account.locations.code`, carried as the code rather
    #: than an id because that is what a punch and LOCATION scope compare against. Unique per org.
    location_code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Centre of the allowed circle. `Numeric`, never a float — a coordinate is exact data, and a
    #: geometry column would drag in geoalchemy2 for no gain when the test is a raw SQL query.
    center_lat: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    center_lng: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    #: Radius of the allowed circle, in metres.
    radius_m: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        # Partial on `deleted_at is null` (migration `f4c1a9b3d2e7`) — see `Shift`'s identical
        # note. `services.create_geofence`'s duplicate check already filters `deleted_at IS NULL`
        # (`selectors.get_geofence_by_location`), so it agrees with this index's condition.
        Index(
            "uq_geofences_org_location",
            "organization_id",
            "location_code",
            unique=True,
            postgresql_where=text("deleted_at is null"),
        ),
    )


class AttendanceRecord(Base, TenantBase):
    """One person's attendance for one day — at most one row per employee per work date.

    `employee_id` points at `employee.employees.id` as a **plain UUID with no cross-schema
    foreign key** (ADR-0000 §10); it is also the OWN/subject dimension, since a caller punches
    their own attendance and the token's `subject_ref` is their employee id. `shift_id` is a
    real foreign key because `shifts` lives in this same schema.
    """

    __tablename__ = "attendance_records"

    #: The employee this day belongs to. A plain UUID into `employee.employees.id`, never a
    #: foreign key across the schema boundary. This is the OWN scope column: a caller's own
    #: `subject_ref` is their employee id.
    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)

    check_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: web | mobile | biometric — where the punch came from.
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    #: The location fix at each punch, when one was supplied. `Numeric`, never a float — see
    #: `Geofence`. Null for an office or remote punch that carries no coordinates.
    check_in_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    check_in_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    check_out_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    check_out_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))

    #: The location this punch is attributed to — an `account.locations.code`. Carried as the
    #: code because LOCATION scope compares codes, and it is what a geofence is keyed by.
    location_code: Mapped[str | None] = mapped_column(String(40))

    #: The shift this day was worked against. A real foreign key: `shifts` is in this schema, so
    #: the constraint does not cross a boundary. Nullable — a punch need not name a shift.
    shift_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("attendance.shifts.id")
    )

    #: office | field | remote | hybrid (plan B.16). `field` is the mode a geofence guards.
    work_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="office")

    #: present | half_day | absent | regularized. Transitions are governed by the state machine
    #: in `domain.py`; this column only stores the current state.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="present")
    notes: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        # One record per person per day, enforced in the database rather than by convention.
        UniqueConstraint(
            "organization_id",
            "employee_id",
            "work_date",
            name="uq_attendance_records_org_employee_date",
        ),
        # Every index leads with `organization_id`, for the row-level-security reason above.
        Index("ix_attendance_records_org_date", "organization_id", "work_date"),
        Index(
            "ix_attendance_records_org_employee_date",
            "organization_id",
            "employee_id",
            "work_date",
        ),
        Index("ix_attendance_records_org_location", "organization_id", "location_code"),
    )


class RegularizationRequest(Base, TenantBase):
    """A self-service ask for a correction to one day's attendance (ADR-0006).

    The product fact, joined to its `approval.approval_requests` counterpart only by
    `(organization_id, request_type, source_ref)` — never a foreign key, since a constraint may
    not cross a schema boundary (ADR-0000 §10, CLAUDE.md). `approval_request_id` is a plain
    nullable `uuid` populated late, from whichever of approval's two events this row sees first —
    `core.approval.request.opened` or `core.approval.request.decided`, both handled in
    `app.consumers` — a convenience for rendering and support, not the authoritative link (data
    model §5).

    `.opened` and `.decided` populate disjoint fields and both tolerate arriving in either order:
    `.opened` sets `approval_opened_at`, `approver_employee_id`, `approver_name` and
    `approval_request_id` (if not already set); `.decided` sets `decided_at`, `status`,
    `decision_comment`, `decided_by_name` and `approval_request_id` (if not already set). Both
    consumers act only while `status == 'pending'` — a `.opened` that arrives after `.decided` (or
    a replay of either) finds the row already settled and is a no-op, never an overwrite of a
    terminal state (ADR-0006 §12).

    Approval's own published payloads now carry a display name alongside each id — `.opened`'s
    `approver_name` beside `approver_employee_id`, `.decided`'s `decided_by_name` beside
    `decided_by_employee_id` — closing the gap this docstring used to describe (approval used to
    publish ids only; the "Waiting on {Name}" screen could not render a name from the id alone).
    Both name fields are optional on the wire and stored as `null`, never raised on, when the key
    is absent from a payload — an event published before approval's change landed, or replayed
    from the outbox — or when the payload itself carries `null` (an unassigned step for
    `approver_name`; a decider outside approval's hierarchy copy, which is legitimate for an
    org-level decision, for `decided_by_name`). See the consumer module's docstring.

    `attendance_id` is the one foreign key in this table that stays inside the schema, so it is
    left to the naming convention — see `docs/approval-engine-data-model.md` §5. No guard trigger
    here: the row is a small mutable product fact, and the consumer's conditional
    `UPDATE ... WHERE status = 'pending'` plus `processed_events` gives the idempotency the guard
    triggers in `approval` exist to enforce there instead.
    """

    __tablename__ = "regularization_requests"

    #: `OrgScopedMixin.organization_id` carries `index=True` by default — a single-column index
    #: right for a table with no other tenant-leading index yet. This one has two
    #: (`uq_regularization_requests_pending_per_attendance` and both composite indexes below), so
    #: the bare column index is redundant scan surface that serves no query in `selectors.py`.
    #: Overridden here, not in the mixin: every other table in this service still wants its
    #: default. `approval`'s own schema deliberately drops the same default for this reason
    #: (data model §1.0).
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=False
    )

    #: The day being corrected. A real foreign key: `attendance_records` is in this schema.
    attendance_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("attendance.attendance_records.id"), nullable=False
    )
    #: Who is asking. A plain UUID into `employee.employees.id`, never a cross-schema FK — the
    #: same convention `AttendanceRecord.employee_id` follows. Always the attendance record's own
    #: employee: the product's statement of whose request it is, not the caller's token.
    employee_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: present | half_day | absent | regularized — validated against `domain.ALLOWED_TRANSITIONS`
    #: from the record's *current* status, both at request time and again when the decision lands.
    requested_status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    #: pending | approved | rejected | failed. `failed` is what the transition being illegal by
    #: the time a decision lands looks like — HR overrode the day directly in the meantime.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    #: Populated late, from approval's own events. Nothing here blocks on it being set.
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: When `core.approval.request.opened` was applied to this row (the envelope's own
    #: `occurred_at`, not a value approval's payload carries directly — that event has no
    #: `opened_at` field of its own, unlike `.decided`'s `decided_at`). Null until that event
    #: lands; the pending screen's "waiting" state is `status == 'pending' and
    #: approval_opened_at is None` versus `... is not None`, which is what tells "no routing
    #: decision known yet" apart from "routed, and here is (or is not) an approver".
    approval_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The step's routed approver at open, from `.opened`'s own `approver_employee_id` — a plain
    #: UUID into `employee.employees.id`, never a cross-schema FK, the same convention every other
    #: employee reference in this table follows. Null both before `.opened` arrives *and* when the
    #: step opened unassigned (ADR-0006 §4); `approval_opened_at` is what tells those two apart.
    approver_employee_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: The routed approver's display name, from `.opened`'s own `approver_name` — approval's fix
    #: for the contract gap this table's docstring used to describe (both events used to carry
    #: employee ids only). Copied verbatim, the same `String(200)` width `employee.employees.
    #: full_name` and approval's own `hierarchy_nodes.display_name` already use for the same
    #: value — it is outside employee's PII envelope there (approval's `HierarchyNode` docstring),
    #: so this copy needs no unmask permission either, the same "already in the clear" reasoning
    #: `reason`/`decision_comment` below follow. Null whenever `approver_employee_id` is null
    #: (unassigned step) *and* whenever approval published the event without the key at all — an
    #: event from before approval's change lands, or a replay of one — never raised on, just
    #: stored as null (`consumers.py`).
    approver_name: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The decider's own free text from `.decided`'s `comment` field, carried verbatim — the same
    #: "stored in the clear, because the source text already is" reasoning `reason` above follows
    #: (data model §5): approval stores its own copy (`approval_requests.comment`) as plaintext
    #: `Text` too, so sealing this copy would protect nothing. Null when the decider left no
    #: comment; approval's own `comment` field is optional (ADR-0006 §7).
    decision_comment: Mapped[str | None] = mapped_column(Text)
    #: The decider's display name, from `.decided`'s own `decided_by_name` — the `.decided` half
    #: of the same contract fix `approver_name` above documents. Copied verbatim at `String(200)`,
    #: the same reasoning. Null when the decider is not in approval's hierarchy copy at all — a
    #: legitimate org-level decider, not an error — and null whenever the key is simply absent
    #: from the payload (an event published before approval's change lands, or a replay);
    #: `consumers.py` never raises on its absence.
    decided_by_name: Mapped[str | None] = mapped_column(String(200))
    failure_reason: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        CheckConstraint(
            "requested_status in ('present','half_day','absent','regularized')",
            name="requested_status",
        ),
        CheckConstraint(
            "status in ('pending','approved','rejected','failed')",
            name="status",
        ),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_present"),
        CheckConstraint(
            "(status = 'pending') = (decided_at is null)",
            name="decided_iff_not_pending",
        ),
        CheckConstraint(
            "(status = 'failed') = (failure_reason is not null)",
            name="failure_reason_iff_failed",
        ),
        # One pending request per attendance record — a second insert while one is already
        # pending gets 23505, and a new request is allowed once the first is decided (data
        # model §5). Not a UniqueConstraint: it is partial, so it is declared as an Index.
        # `deleted_at is null` matches `selectors.get_pending_regularization`'s own filter: a
        # soft-deleted pending row is invisible everywhere else in this service, so it must not
        # be able to collide here either — without this, a soft-deleted pending row would turn
        # the selector's friendly `ConflictProblem` into a raw `23505` the caller never expects
        # (the selector would report "no pending request" while the insert still failed).
        Index(
            "uq_regularization_requests_pending_per_attendance",
            "organization_id",
            "attendance_id",
            unique=True,
            postgresql_where=text("status = 'pending' and deleted_at is null"),
        ),
        Index(
            "ix_regularization_requests_org_employee_created",
            "organization_id",
            "employee_id",
            text("created_at DESC"),
        ),
        # Serves `selectors.get_latest_regularization`/`get_latest_regularizations`, which filter
        # on `(organization_id, attendance_id[, created_at desc])` — nothing else did (the
        # pending-only partial index above does not cover the general case, and the composite
        # above this one is keyed by `employee_id`, not `attendance_id`). Added once the table's
        # measured plan at 10,001 rows (183 buffers, 4.3ms for a 25-row page) showed the growth
        # this table only ever does — every correction ever requested, never purged.
        Index(
            "ix_regularization_requests_org_attendance_created",
            "organization_id",
            "attendance_id",
            text("created_at DESC"),
        ),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
