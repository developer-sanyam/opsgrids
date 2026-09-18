"""Tables owned by the console service — the operator's read model of commerce.

Console owns no commercial truth. Every table here is a **projection** folded from the commerce
transition-event stream (ADR-0004 §4): subscription lifecycle, invoices, credit and usage. The
operator console reads these instead of polling five services' live tables, which is the payoff
of every commercial transition being a domain event from the first subscription — MRR, churn and
trial-conversion are answerable historically, not only from the day someone started recording.

The tenancy classification is a security decision written out per table (ADR-0002 §5). All three
projections are **tenant-scoped and RLS-policied**: the consumer writes them one organization at a
time as the NOBYPASSRLS app role with the tenant GUC set per message, so a fold for org A can never
write org B's row. The operator's cross-tenant read does not go through the app role at all — it
runs on the SELECT-only BYPASSRLS directory role (`opsgrids_console_directory`), the named,
auditable bypass ADR-0003 §2 requires. That the projections are RLS-real is what makes the bypass
meaningful, and both halves are covered by a test.

`revenue_events` is additionally **append-only** at the database (a `REVOKE UPDATE, DELETE` on the
app role, the same tool `audit_logs` and metering's `usage_events` use): the time series MRR, churn
and trial-conversion are computed from must never be rewritten — a correction is a new row
(ADR-0000 §9).

The platform tables (`outbox`, `audit_logs`, `processed_events`) come from the scaffold.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "console"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class OrgCommercialState(Base, TenantBase):
    """One organization's current commercial snapshot — the row the operator list renders.

    Folded from the subscription transition stream. `mrr_minor` is the running sum of this
    organization's *active* subscriptions priced at their pinned plan version (ADR-0004 §4): a
    trial, a past-due or a cancelled subscription contributes nothing; only `active` does. The
    per-product detail lives in `subscription_states` so the fold can recompute the total after
    any one product moves, and derive the previous state of a subscription (the transition's
    *from* state, which the thin event payload does not carry) to classify churn and conversion.

    Money is integer minor units with an ISO currency (ADR-0000 §8) — never a float. One currency
    per organization for now (the commerce plan's simplifying default), though the column carries
    a currency per amount so multi-currency is a later decision, not a schema change.
    """

    __tablename__ = "org_commercial_state"

    #: Running sum of active subscriptions' pinned prices. Recomputed on every subscription
    #: transition from `subscription_states`, so it is always consistent with the detail.
    mrr_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: The organization's billing currency, taken from its subscriptions' pinned prices.
    currency: Mapped[str | None] = mapped_column(String(3))

    #: Product keys currently entitled — `active` or a live `trial`. What the operator sees as
    #: "what this customer has turned on".
    active_products: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    #: Per-product subscription detail, keyed by `product_key`:
    #: ``{"hrms": {"state": "active", "plan_version_id": "...", "amount_minor": 50000,
    #: "currency": "INR"}}``. The fold reads the existing entry to learn a transition's *from*
    #: state, then overwrites it with the *to* state.
    subscription_states: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: The nearest trial end across this organization's trialling subscriptions, or null when
    #: none are trialling. A convenience for the operator list; the authority is the subscription
    #: service.
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: True while any of the organization's subscriptions is suspended (ADR-0004 §5: a suspension
    #: de-entitles). Set when a `suspended` transition arrives, cleared when nothing is suspended.
    locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        # One snapshot per organization. The fold upserts on this, so a redelivered or a
        # second-product event updates the one row rather than creating a rival.
        UniqueConstraint("organization_id", name="uq_org_commercial_state_organization_id"),
    )


class RevenueEvent(Base, TenantBase):
    """A single commercial transition, appended — the time series metrics are computed from.

    **Append-only** (`REVOKE UPDATE, DELETE` on the app role, see the append-only migration):
    MRR history, churn and trial-conversion read off these rows, so a rewrite would be a rewrite
    of the revenue record. A correction is a new, compensating row (ADR-0000 §9, ADR-0004 §3).

    `kind` is the source event type (`commerce.subscription.activated`, `commerce.invoice.finalized`
    `commerce.credit.granted`, …). `meta` carries the classification the metrics need — for a
    subscription transition, the `from_state`/`to_state` the fold derived and the `product_key` —
    so trial-conversion (`trial → active` over `trial` starts) and churn (`active → cancelled`)
    are a query over `kind` and `meta`, not a second set of counters to keep accurate.
    """

    __tablename__ = "revenue_events"

    #: The source event type, verbatim — the routing key this row was folded from.
    kind: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The amount this transition moves, in integer minor units. Signed: a credit reversal or a
    #: void is negative. Zero for a state change that moves no money on its own (e.g. a trial
    #: start), which still matters as a counted transition.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str | None] = mapped_column(String(3))
    #: When the transition happened, from the event envelope's `occurred_at` — not when it was
    #: folded here. Metrics are cut on this.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: Classification for the metrics: `from_state`, `to_state`, `product_key`, `plan_version_id`,
    #: and whatever a non-subscription event carries (invoice number, credit reason).
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        # Every index leads with `organization_id`: the RLS qual is ANDed into every query, so an
        # index that did not lead with the tenant column would scan the platform and discard most
        # of it (ADR-0002). The metrics scan by kind within a time window.
        Index("ix_revenue_events_org_kind_occurred", "organization_id", "kind", "occurred_at"),
        Index("ix_revenue_events_org_occurred", "organization_id", "occurred_at"),
    )


class UsageSummary(Base, TenantBase):
    """A per-org, per-meter, per-period usage rollup — folded from `commerce.usage.recorded`.

    Derived and rebuildable, like metering's own rollups: it holds the summed quantity the operator
    usage view renders, upserted as usage events arrive. The unique key is
    `(organization_id, meter_key, period)`, one row per meter per period.
    """

    __tablename__ = "usage_summary"

    meter_key: Mapped[str] = mapped_column(String(60), nullable=False)
    #: The period bucket this quantity aggregates, e.g. `2026-08` — a coarse month key derived
    #: from the event's `occurred_at`. Coarse on purpose: the operator view is a trend, not a bill.
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Running sum of `quantity` over every usage event on this meter in the period.
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "meter_key", "period", name="uq_usage_summary_org_meter_period"
        ),
        Index("ix_usage_summary_org_meter", "organization_id", "meter_key"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe.

    Console currently publishes nothing — it is a projection consumer — but the table and relay are
    kept from the scaffold so the shape is uniform and `/health/outbox` stays honest.
    """


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once, so every fold is guarded by this."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what. An operator override writes one here, bound to the target organization —
    the record the `/admin/audit` view reads back across tenants."""
