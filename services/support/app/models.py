"""Tables owned by the support service — the support desk.

Everything here lives in the `support` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). The link to another service (an agent's account membership, say) is a
plain UUID, never a constraint that crosses a schema boundary. Foreign keys **within** this
schema — a message to its ticket — are allowed and used, because they never cross the boundary.

Shape of this slice: the shared support queue. A `Ticket` is the unit of work; its
conversation lives in append-only `TicketMessage` rows; `Agent` is the person who works the
queue; `SlaPolicy` is the one governing policy per organization. There is no money anywhere in
this domain, so the integer-minor-units rule has nothing to bind to here.

The public identifiers the product's SPA already uses — `TK-4001` for a ticket, `ag-01` for an
agent, `m1` for a message — are carried as their own human-facing columns (`number`, `ref`)
rather than as primary keys. The primary key stays a UUID like every other table, and the
human code is unique *within an organization*, because two tenants each number their tickets
from their own sequence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "support"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Agent(Base, TenantBase):
    """A person who works the support queue.

    The performance figures — `open_load`, `avg_first_response_min`, `csat_pct`, `solved_7d` —
    are a rolling projection maintained by the desk, stored rather than derived: the trailing
    windows they summarise (a week of first-response medians, of CSAT ratings) span far more
    than the live ticket rows, so recomputing them from the current queue would understate
    them. They are refreshed as tickets move; the queue is not their source of truth.
    """

    __tablename__ = "agents"

    #: The public agent id the SPA uses, e.g. ``ag-01``. Unique within an organization.
    ref: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    #: agent | senior_agent | team_lead.
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="agent")
    team: Mapped[str] = mapped_column(String(80), nullable=False, default="Tier 1")

    #: Currently-assigned open tickets.
    open_load: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Median first-response time over the trailing week, in minutes.
    avg_first_response_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Customer-satisfaction score over the trailing week, 0-100.
    csat_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Tickets solved in the trailing seven days.
    solved_7d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        # Every index leads with `organization_id`: under row-level security the policy qual is
        # ANDed into every query, so an index that did not lead with the tenant column would
        # scan the whole platform's rows and discard almost all of them (ADR-0002).
        Index("ix_agents_org_ref", "organization_id", "ref"),
        UniqueConstraint("organization_id", "ref", name="uq_agents_org_ref"),
    )


class Ticket(Base, TenantBase):
    """One support request and its lifecycle.

    `number` is the human ticket code (``TK-4001``), unique per organization and the id the SPA
    puts in its URLs. `assignee_ref` holds the *agent's* public ref (``ag-04``) rather than an
    agent UUID: it is the value the product's contract returns as `assignee_id`, and the queue
    filters on it directly. It is a soft reference — an agent's disappearance leaves the ticket
    unassigned rather than dangling — so it is deliberately not a foreign key.

    The SLA fields are real, stored state, never a colour computed on the client: `sla_due` is
    the current clock's deadline (null once the ticket is solved or has no live target) and
    `sla_breached` records whether a target has been missed and *stays* true through resolution,
    so a solved ticket still reports the miss. The UI labels both.
    """

    __tablename__ = "tickets"

    #: Human ticket code, e.g. ``TK-4001``. Unique within an organization; the SPA's `id`.
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)

    #: The requester is denormalised onto the ticket — a contact snapshot, not a link to a
    #: customer record. `requester_kind` distinguishes an external customer from internal staff
    #: sharing the one queue.
    requester_name: Mapped[str] = mapped_column(String(160), nullable=False)
    requester_email: Mapped[str] = mapped_column(String(254), nullable=False)
    requester_company: Mapped[str] = mapped_column(String(200), nullable=False)
    requester_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="customer")

    #: urgent | high | normal | low.
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    #: new | open | pending | on_hold | solved. `solved` is terminal for this slice.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    #: email | chat | web | phone | api.
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="email")

    #: The assigned agent's public ref (``ag-04``), or null when unassigned. A soft reference,
    #: not a foreign key: it is the SPA's `assignee_id` and the queue's assignee filter.
    assignee_ref: Mapped[str | None] = mapped_column(String(40))

    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    #: When the first agent reply went out, or null while the clock still runs.
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Deadline for the currently-open SLA clock; null once solved or with no live target.
    sla_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: True once an SLA target has been missed; stays true through resolution.
    sla_breached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: CSAT left on a solved ticket: good | bad, or null.
    satisfaction: Mapped[str | None] = mapped_column(String(8))

    __table_args__ = (
        Index("ix_tickets_org_status", "organization_id", "status"),
        Index("ix_tickets_org_priority", "organization_id", "priority"),
        Index("ix_tickets_org_assignee", "organization_id", "assignee_ref"),
        UniqueConstraint("organization_id", "number", name="uq_tickets_org_number"),
    )


class TicketMessage(Base, TenantBase):
    """One message in a ticket's conversation thread. **Append-only.**

    The thread is the audit trail of the conversation: a message is inserted and never updated
    or deleted. Nothing in `services.py` mutates a row here, and the write path exposes only an
    append. `internal` marks an agent-only note the requester never sees.

    The link to `tickets` **is** a foreign key: it stays inside the `support` schema, and only
    cross-schema foreign keys are banned (ADR-0000 §10).
    """

    __tablename__ = "ticket_messages"

    #: The ticket this message belongs to. A real foreign key — same schema, so it is allowed.
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("support.tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The message's per-ticket code, e.g. ``m1`` — the SPA's message `id`.
    ref: Mapped[str] = mapped_column(String(40), nullable=False)
    #: customer | agent | system.
    author_type: Mapped[str] = mapped_column(String(20), nullable=False)
    author_name: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: An internal note is visible to agents only, never the requester.
    internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: When the message was sent — the domain timestamp the thread is ordered by, distinct from
    #: the row's `created_at`.
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_ticket_messages_org_ticket", "organization_id", "ticket_id"),
        UniqueConstraint("ticket_id", "ref", name="uq_ticket_messages_ticket_ref"),
    )


class SlaPolicy(Base, TenantBase):
    """The one SLA policy governing an organization's queue.

    A single row per organization. `targets` is the per-priority first-response and resolution
    windows, carried as JSON because they are read and written whole, never queried into.
    """

    __tablename__ = "sla_policies"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    business_hours: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    coverage: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: List of {priority, first_response_min, resolution_min}.
    targets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        # One policy per organization, enforced in the database.
        UniqueConstraint("organization_id", name="uq_sla_policies_org"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
