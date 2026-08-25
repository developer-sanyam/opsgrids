"""Tables owned by the standup service — PulseBoard's daily check-ins.

Everything here lives in the `standup` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). The person behind an entry is the project service's roster person,
carried as a plain UUID (`person_id`) plus the stable human handle (`person_ref`, `p01`) the
SPA joins on — never a cross-schema FK.

One table: **standup_entries** — one person's check-in for one day: what happened yesterday,
what is planned today, and anything blocking them. `blockers` is null when nothing blocks;
the flagged state the SPA renders is exactly `blockers is not null`, so there is no separate
boolean to drift out of sync.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "standup"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class StandupEntry(Base, TenantBase):
    """One person's standup for one day.

    `person_id` is the roster person's UUID (the project service's `people.id`, a plain
    column) — it is also the OWN scope column. `person_ref` (`p01`) is the wire identity the
    SPA joins to the roster. One entry per person per day, enforced.
    """

    __tablename__ = "standup_entries"

    #: The roster person's UUID — a plain cross-service reference, and the OWN scope column.
    person_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: The person's stable handle (`p01`) — the id the SPA joins entries to the roster with.
    person_ref: Mapped[str] = mapped_column(String(40), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    yesterday: Mapped[str] = mapped_column(Text, nullable=False, default="")
    today: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Null when nothing blocks — the flagged state is `blockers is not null`.
    blockers: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "person_ref", "entry_date", name="uq_standup_entries_org_person_day"
        ),
        Index("ix_standup_entries_org_date", "organization_id", "entry_date"),
        Index("ix_standup_entries_org_person", "organization_id", "person_id"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
