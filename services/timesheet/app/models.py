"""Tables owned by the timesheet service — PulseBoard's weekly time grid.

Everything here lives in the `timesheet` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). The person behind a row is the project service's roster person, carried
as a plain UUID (`person_id`) plus the stable human handle (`person_ref`, `p01`) the SPA joins
on — never a cross-schema FK. Likewise `project_ref` on an entry names an engagement by its
code (`PB-01`) rather than by a foreign key into another schema.

Two tables, one concern — the weekly people-by-days grid and its utilization math:

* **timesheets** — one person's week: the Monday it starts, the approval `status`
  (`draft` / `submitted` / `approved` — the week moves through them as a unit, which is why
  status lives here and not per entry), and `capacity_minutes`, a snapshot of the person's
  weekly billable target *at the time of the week*. Capacity is snapshotted because it changes
  as people go part-time; utilization for a past week must divide by what capacity was then.
* **timesheet_entries** — one slot of work: a date inside the week, an optional engagement
  code, and the hours entered / billable. Hours are stored as **integer minutes** so no float
  ever touches the arithmetic; the API serialises them as decimal hours.

An entry's `billable_minutes` can never exceed its `entered_minutes` — a CHECK enforces the
invariant the SPA's grid assumes.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "timesheet"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Timesheet(Base, TenantBase):
    """One person's timesheet for one week.

    `person_id` is the roster person's UUID (the project service's `people.id`, a plain
    column) — it is also the OWN scope column, so a caller scoped to their own work sees their
    own weeks. `person_ref` (`p01`) is the wire identity the SPA's grid rows key on.
    `week_start` is always a Monday. `capacity_minutes` snapshots the person's weekly billable
    target for utilization math.
    """

    __tablename__ = "timesheets"

    #: The roster person's UUID — a plain cross-service reference, and the OWN scope column.
    person_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: The person's stable handle (`p01`) — the id the SPA joins rows to the roster with.
    person_ref: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The Monday this week starts on.
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    #: draft | submitted | approved — the week's approval state, moved as a unit.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    #: The person's weekly billable-target, snapshotted, in minutes.
    capacity_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=2400)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "person_ref", "week_start", name="uq_timesheets_org_person_week"
        ),
        Index("ix_timesheets_org_week", "organization_id", "week_start"),
        Index("ix_timesheets_org_person", "organization_id", "person_id"),
        Index("ix_timesheets_org_status", "organization_id", "status"),
    )


class TimesheetEntry(Base, TenantBase):
    """One slot of recorded work inside a week.

    `timesheet_id` is a real in-schema FK; the grid cell for a date is the sum of that date's
    entries, so several engagements on one day roll up naturally. `project_ref` names the
    engagement by its code (`PB-01`) — a plain string, because the engagement lives in another
    service's schema; null is legitimate for non-engagement time. Minutes, never float hours.
    """

    __tablename__ = "timesheet_entries"

    timesheet_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("timesheet.timesheets.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The date this entry is for — always inside its timesheet's week.
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: The engagement worked, by its code (`PB-01`). A plain string, never a cross-schema FK.
    project_ref: Mapped[str | None] = mapped_column(String(40))
    entered_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billable_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "billable_minutes >= 0 AND entered_minutes >= 0 "
            "AND billable_minutes <= entered_minutes",
            name="billable_within_entered",
        ),
        Index("ix_timesheet_entries_org_sheet", "organization_id", "timesheet_id"),
        Index("ix_timesheet_entries_org_date", "organization_id", "work_date"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
