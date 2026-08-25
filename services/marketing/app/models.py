"""Tables owned by the marketing service — campaigns, audiences and the revenue series.

Everything here lives in the `marketing` schema and nothing outside it may hold a foreign
key into it (ADR-0000 §10). The link to account-service (an owner, an audience) is a plain
value or a same-schema foreign key, never a constraint that crosses a schema boundary.

Money is an **integer count of minor units** (paise for INR) on every amount column, per the
platform money rule (ADR-0000 §8). The columns are `BigInteger`: an org-level month of
attributed revenue runs to hundreds of lakh of rupees — billions of paise — which overflows a
32-bit integer, so every money column is 64-bit to leave no cliff.

Ratios the UI shows — ROAS, cost-per-lead, CAC — are **never stored**. They are pure functions
of the amounts and counts here (`app.domain`), computed in exactly one place so a rounding or
divide-by-zero choice cannot drift between the campaign row, the channel rollup and the
overview headline.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "marketing"

#: The delivery channels a campaign runs on. Platform vocabulary, not tenant data — stored as a
#: short string and constrained in the Pydantic layer (`schemas.Channel`) rather than in its own
#: reference table, because the set is fixed and every tenant shares it.
CHANNELS = ("email", "social", "search", "events")

#: A campaign's lifecycle state. Same reasoning as `CHANNELS`: a fixed, shared vocabulary.
CAMPAIGN_STATUSES = ("draft", "live", "paused", "ended")


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Audience(Base, TenantBase):
    """A saved audience segment campaigns can target.

    An audience is org-wide marketing data, not a per-person record: it names a segment
    (`Enterprise India`) and carries its reachable `size` and a one-line human `criteria`
    summary. How many campaigns *use* an audience is derived from the campaign list, never
    stored here — a denormalised counter is one more thing to keep true.

    `defined_on` is the date the segment was created in the marketing tool, surfaced to the UI
    as `created_at`. It is distinct from the row's own `created_at` (when this record was
    written), which the `TenantBase` timestamp mixin already carries.
    """

    __tablename__ = "audiences"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    #: Reachable people in the segment.
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: A one-line human summary of the segment's criteria.
    criteria: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    #: When the segment was defined — the date the UI shows as the audience's `created_at`.
    defined_on: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (
        # Every index leads with `organization_id`: under row-level security the policy qual is
        # ANDed into every query, so an index that did not lead with the tenant column would scan
        # the whole platform's rows and discard almost all of them (ADR-0002).
        Index("ix_audiences_org_name", "organization_id", "name"),
        UniqueConstraint("organization_id", "name", name="uq_audiences_org_name"),
    )


class Campaign(Base, TenantBase):
    """One marketing campaign — the row every screen in the product is built from.

    Budget, spend, attributed revenue and generated pipeline are all **minor units** of
    `currency`; `leads` and `conversions` are counts. The Overview KPIs, the Channels rollup and
    the campaign detail all sum or ratio these fields, which is why the derived numbers live in
    `app.domain` and not in a column here.

    `audience_id` is a **same-schema** foreign key into `audiences` — allowed, because only
    *cross*-schema foreign keys are banned (ADR-0000 §10). It is how the detail screen resolves a
    campaign's audience in one payload, and how the audiences screen counts "used by N campaigns".
    """

    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: email | social | search | events. Constrained in the schema layer.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: draft | live | paused | ended. Constrained in the schema layer.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")

    #: ISO-4217 currency for every money column on this row.
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: Approved budget, minor units.
    budget_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Spend to date, minor units.
    spent_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Closed-won revenue attributed to the campaign, minor units.
    revenue_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Open pipeline (opportunity value) generated, minor units.
    pipeline_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    #: Qualified leads captured.
    leads: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Deals attributed (won) — drives per-channel CAC.
    conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: The primary objective, e.g. "Enterprise pipeline".
    goal: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: The audience this campaign targets. A same-schema foreign key — the one link the product
    #: needs to resolve, and it never leaves the `marketing` schema.
    audience_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketing.audiences.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Flight window. `end_date` is null while a campaign is open-ended or still a draft.
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    #: Owner, shown in the campaign header. A display name, not an identity link.
    owner: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    __table_args__ = (
        Index("ix_campaigns_org_status", "organization_id", "status"),
        Index("ix_campaigns_org_channel", "organization_id", "channel"),
        Index("ix_campaigns_org_audience", "organization_id", "audience_id"),
        Index("ix_campaigns_org_name", "organization_id", "name"),
    )


class RevenueSpendPoint(Base, TenantBase):
    """One point on the org-wide revenue-vs-spend time series behind the Overview chart.

    This is a monthly rollup the marketing team maintains as a headline series — attributed
    revenue against total spend — not something re-derivable from the campaign flight windows,
    so it is stored. `position` gives the series a stable chronological order independent of the
    label text (`Jan`, `Feb`, …). Both amounts are minor units of `currency`.
    """

    __tablename__ = "revenue_spend_points"

    #: Short axis label, e.g. "Jan".
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Chronological position in the series, so the chart never reshuffles between renders.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: Attributed revenue for the period, minor units.
    revenue_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Total spend for the period, minor units.
    spend_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        Index("ix_revenue_spend_points_org_position", "organization_id", "position"),
        # One row per period per organization, enforced in the database so a re-seed updates
        # rather than duplicates.
        UniqueConstraint(
            "organization_id", "position", name="uq_revenue_spend_points_org_position"
        ),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
