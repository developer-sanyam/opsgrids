"""Tables owned by the deal service — the CRM pipeline.

Everything here lives in the `deal` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). Links to the account book (`company_id`, `primary_contact_id`)
and the rep directory (`owner_id`) are plain UUIDs into customer-service's rows — never a
constraint that crosses a schema boundary. Links **within** this schema
(`activities.deal_id`) are real foreign keys, because only cross-schema keys are banned.

Two concerns, one schema:

* **deals** — one opportunity moving through the six-stage pipeline (lead → qualified →
  proposal → negotiation → closed_won | closed_lost). `value_minor` is integer minor
  units in an ISO currency (ADR-0000 §8); no float ever touches money. `last_activity_at`
  is the staleness clock: it advances whenever an activity is logged against the deal, and
  the Overview's "needs attention" reading (stale / closing soon / overdue) derives from
  it plus `close_date`. Stage changes go through `services.move_stage`, which enforces the
  transition rules in `domain.py`.
* **activities** — the timeline: calls, emails, meetings, notes and tasks, **append-only**.
  A database trigger refuses UPDATE and DELETE (see the RLS migration), so the record of
  who touched a deal and when is a record, not a draft. An activity usually names a deal;
  it may instead (or also) name a company or contact, which is why those ids are nullable.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "deal"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Deal(Base, TenantBase):
    """One opportunity in the pipeline.

    `owner_id` is the rep who runs the deal — the scope column for OWN readings — and
    `company_id` / `primary_contact_id` point into the account book by plain UUID.
    `probability` is 0-100; `close_date` is the expected close (a past date on an open
    deal is overdue); `source` is where the deal came from ("Referral", "Inbound", …).
    `stage` is constrained by the service layer, not an enum column, so adding a stage is
    a vocabulary change rather than a migration.
    """

    __tablename__ = "deals"

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    #: The account this deal is with. Plain UUID into customer-service's companies.
    company_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: The main person on the deal, if one is named. Plain UUID into customer-service.
    primary_contact_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    value_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    #: lead | qualified | proposal | negotiation | closed_won | closed_lost.
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="lead")
    #: The rep who runs the deal — the OWN-scope column. Plain UUID into the rep directory.
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: Win likelihood, 0-100. 100 once won, 0 once lost.
    probability: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Expected close. Past dates on open deals read as overdue.
    close_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Where the deal came from: Referral | Inbound | Outbound | Event | Partner | …
    source: Mapped[str] = mapped_column(String(60), nullable=False, default="")

    #: The staleness clock — when the deal was last touched. Advanced by `log_activity`;
    #: NULL for a deal with no timeline yet.
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: When the deal actually closed — set by `move_stage` on a move into `closed_won` or
    #: `closed_lost` (a closed deal moves nowhere afterwards, so this is never cleared once
    #: set), or by `create_deal` for a deal created directly in a closed stage. `NULL` for an
    #: open deal, and `NULL` for a deal that closed before this column existed with no audit
    #: row the migration's own backfill could recover one from (ADR-0020 §3,
    #: `docs/insights/data-model.md` §3) — insights' won/lost coverage starts at go-live for
    #: that organization rather than guessing. Never the *expected* close: see `close_date`.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Every index leads with `organization_id`: the RLS policy qual is ANDed into every
        # query, so an index that did not lead with the tenant column would scan the whole
        # platform's rows and discard almost all of them (ADR-0002).
        Index("ix_deals_org_stage", "organization_id", "stage"),
        Index("ix_deals_org_owner", "organization_id", "owner_id"),
        Index("ix_deals_org_company", "organization_id", "company_id"),
        Index("ix_deals_org_close", "organization_id", "close_date"),
    )


class Activity(Base, TenantBase):
    """One entry on the timeline — **append-only**.

    Logged when a rep calls, emails, meets, notes or files a task against a deal (and, by
    extension, its company and contact). A database trigger (see the RLS migration)
    refuses UPDATE and DELETE, so the timeline is a record of what happened, not a
    document that can be quietly rewritten. There is no service or route that mutates one
    by design, and the trigger makes that a guarantee rather than a convention.
    """

    __tablename__ = "activities"

    #: call | email | meeting | note | task. Named `activity_type` because `type` shadows
    #: the builtin; the wire field is `type`, matching the SPA.
    activity_type: Mapped[str] = mapped_column(String(20), nullable=False, default="note")
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)

    #: The deal this entry belongs to, when it has one. A real FK — same schema — and the
    #: row is removed with its deal.
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("deal.deals.id", ondelete="CASCADE")
    )
    #: The account and person touched, when named. Plain UUIDs into customer-service.
    company_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: The rep who logged the touch — the OWN-scope column and the timeline's actor.
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: When the touch happened — carried explicitly so a backfilled timeline keeps real times.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: inbound | outbound for calls and emails; NULL for meetings, notes and tasks.
    direction: Mapped[str | None] = mapped_column(String(10))

    __table_args__ = (
        Index("ix_activities_org_deal", "organization_id", "deal_id", "occurred_at"),
        Index("ix_activities_org_contact", "organization_id", "contact_id"),
        Index("ix_activities_org_company", "organization_id", "company_id"),
        Index("ix_activities_org_occurred", "organization_id", "occurred_at"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
