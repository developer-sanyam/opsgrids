"""Tables owned by the scheduler service.

Everything here lives in the `scheduler` schema and nothing outside it may hold a foreign
key into it.

One business table, `ScheduledJob` (ADR-0015): a producer asks, via
`platform.schedule.requested`, to have some other event of its own delivered no earlier than
`due_at`; this is the row that request becomes while it waits. `TenantBase` — row-level security
applies to it exactly as to every other tenant table, and the outbox role that discovers due rows
across every organization gets a narrow, column-level grant (`migrations/env.py`'s
`grant_sweep_reader`), never a blanket `SELECT`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "scheduler"

#: The two states a job passes through. No `cancelled` in this slice — nothing asks to withdraw a
#: schedule yet (ADR-0015, "Deliberately out of scope"); adding one is a column and a new query
#: branch, not a redesign.
STATUS_PENDING = "pending"
STATUS_FIRED = "fired"

STATUSES = (STATUS_PENDING, STATUS_FIRED)


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class ScheduledJob(Base, TenantBase):
    """One delayed delivery, waiting for `due_at`.

    `target_*` columns are literally the arguments `publish_event` would have taken, deferred:
    when `due_at` arrives, the sweep (`app/cron.py`) inserts a row on **this service's own
    outbox** naming `target_event_type` as the event type — and hence the topic-exchange routing
    key the relay publishes under — so the original producer's own queue binding receives it
    exactly as if the producer had published it directly. `scheduler` never has to know, or care,
    what the event means.
    """

    __tablename__ = "scheduled_jobs"

    #: The domain-level idempotency guard (ADR-0015 §2): a redelivered
    #: `platform.schedule.requested`, or two callers that reasonably compute the same key (the
    #: early-arrival defence in `finance/app/consumers.py` does this on purpose), must produce
    #: exactly one row, never two. Unique with `organization_id`, not globally — two different
    #: organizations may coincidentally choose the same key.
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)

    #: When the target event should be published. Compared against the database's own clock
    #: inside `app/selectors.py:due_jobs`, never a pod's.
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_PENDING)

    target_event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    target_aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_aggregate_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    target_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_actor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    #: The `platform.schedule.requested` event's own id — becomes the fired event's
    #: `causation_id`, so the provenance chain survives the wait (ADR-0000 §7).
    requested_event_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64))

    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The id of the outbox row this job produced when it fired — the audit trail's other half.
    fired_event_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_scheduled_jobs_org_idempotency"
        ),
        # Discovery's own index (`app/selectors.py:_DUE_JOBS_SQL`): pending rows ordered by when
        # they became due, and nothing else — a fired row never needs to be found this way again.
        Index(
            "ix_scheduled_jobs_pending_due",
            "due_at",
            postgresql_where="status = 'pending'",
        ),
        # The retention sweep's own index (`app/selectors.py:_STALE_FIRED_JOBS_SQL`): fired rows
        # ordered by how long ago they fired, and nothing else.
        Index(
            "ix_scheduled_jobs_fired_at",
            "fired_at",
            postgresql_where="status = 'fired'",
        ),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
