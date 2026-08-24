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
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
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
        # scan the whole platform's rows and discard almost all of them (ADR-0002).
        UniqueConstraint("organization_id", "code", name="uq_shifts_org_code"),
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
        UniqueConstraint("organization_id", "location_code", name="uq_geofences_org_location"),
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


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
