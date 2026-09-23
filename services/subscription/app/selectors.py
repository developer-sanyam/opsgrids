"""Reads for subscription. Visibility rules live here, in one place.

Every table here is tenant-scoped with row-level security, so the database enforces isolation as
a net — but the query still filters, because RLS is the net and not the licence to skip the
filter (ADR-0002). A subscription is an account-level resource: it belongs to the organization,
not to one user, so the scope floor is `ORG` and `ctx.apply` is a no-op that the list endpoint
still routes through, on principle, with a negative-authz test behind it.

The sweeps' discovery reads — `due_dunning_cycles`, `due_billing_subscriptions`,
`stale_pending_periods` — and the `billing_run_health` probe are the reads here with no tenant in
them. Each runs on the BYPASSRLS outbox role, returns ids or counts and nothing else, and the
exception is stated at each function rather than assumed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain import PERMANENT_AFTER_ATTEMPTS
from app.models import (
    DUNNING_OPEN,
    LIFECYCLE_KINDS,
    PERIOD_KIND_CLOSE,
    PERIOD_KIND_PRORATION,
    PERIOD_KIND_TRIAL_CONVERSION,
    PERIOD_PENDING,
    PERIOD_VOID_PENDING,
    STATE_ACTIVE,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    STATE_TRIAL,
    BillingPeriod,
    DunningCycle,
    Subscription,
)
from platform_core.auth import ScopeContext


def _with_items(stmt: Select[Any]) -> Select[Any]:
    return stmt.options(selectinload(Subscription.items))


async def list_subscriptions(session: AsyncSession, ctx: ScopeContext) -> list[Subscription]:
    """Every subscription this organization holds.

    Scoped through `ctx.apply` for the single enforcement point the convention requires; at the
    `ORG` floor these routes run under, that is the identity filter, and the tenant binding plus
    RLS is what actually isolates one organization from another.
    """
    stmt = ctx.apply(
        _with_items(select(Subscription).order_by(Subscription.product_key)),
        owner_col=Subscription.organization_id,
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_subscription(
    session: AsyncSession, subscription_id: uuid.UUID
) -> Subscription | None:
    """One subscription by id, with its items.

    No explicit organization filter is needed to keep tenants apart: the row is invisible under
    RLS unless the transaction is bound to its organization, so another org's id simply resolves
    to None — a 404, never a disclosure.
    """
    stmt = _with_items(select(Subscription).where(Subscription.id == subscription_id))
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_product(
    session: AsyncSession, organization_id: uuid.UUID, product_key: str
) -> Subscription | None:
    """The organization's subscription for one product, if any — the uniqueness the subscribe
    path checks before creating a rival row."""
    stmt = _with_items(
        select(Subscription).where(
            Subscription.organization_id == organization_id,
            Subscription.product_key == product_key,
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ----------------------------------------------------------------------- dunning (ADR-0005)


async def get_open_cycle(session: AsyncSession, subscription_id: uuid.UUID) -> DunningCycle | None:
    """The live dunning cycle for a subscription, if one is running.

    A partial unique index guarantees there is at most one, so this is `scalar_one_or_none` and
    not "the most recent" — "the most recent open one" would be a query written around a race
    the schema already forbids. Invisible under RLS unless the transaction is bound to the
    owning organization, like every other read here.
    """
    stmt = select(DunningCycle).where(
        DunningCycle.subscription_id == subscription_id,
        DunningCycle.state == DUNNING_OPEN,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def open_cycles_by_subscription(
    session: AsyncSession, subscription_ids: list[uuid.UUID]
) -> dict[uuid.UUID, DunningCycle]:
    """Open cycles for a page of subscriptions, keyed by subscription — one query, not N.

    The read routes call this to attach the grace window to what they return; an empty input is
    answered without touching the database, so a customer with no subscriptions costs nothing.
    """
    if not subscription_ids:
        return {}
    stmt = select(DunningCycle).where(
        DunningCycle.subscription_id.in_(subscription_ids),
        DunningCycle.state == DUNNING_OPEN,
    )
    return {c.subscription_id: c for c in (await session.execute(stmt)).scalars().all()}


async def lock_open_cycle(session: AsyncSession, cycle_id: uuid.UUID) -> DunningCycle | None:
    """Claim one open cycle for this transaction, or return None if another sweep holds it.

    `FOR UPDATE SKIP LOCKED`, the same claim the outbox relay uses, so several sweep replicas
    can drain the same due set without two of them retrying — or suspending — one subscription
    twice. The caller must re-check the cycle is still due after claiming it: skipping a locked
    row is how replicas divide the work, re-checking is how a row that was already dealt with in
    the moment between discovery and claim is not dealt with again.
    """
    stmt = (
        select(DunningCycle)
        .where(DunningCycle.id == cycle_id, DunningCycle.state == DUNNING_OPEN)
        .with_for_update(skip_locked=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def due_dunning_cycles(
    session: AsyncSession, *, now: datetime, limit: int = 100
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Every open cycle whose next action has fallen due, as `(organization_id, cycle_id)`.

    **A cross-tenant read, like the billing run's two below.** It exists
    to discover *which* organizations have dunning work, so there is no tenant to bind while
    asking — exactly the outbox relay's shape, and it runs on the same BYPASSRLS role
    (`get_outbox_sessionmaker`). As the NOBYPASSRLS app role it would return zero rows, log
    nothing, and every past-due subscription in the platform would sit in grace forever: the same
    silent stall ADR-0002 records for the relay, in a path where the failure mode is free service.

    It returns ids and nothing else on purpose. Every *decision* and every *write* that follows
    happens back on the app role inside `session_scope(organization_id=...)`, under the tenant
    policy — the bypass buys discovery, never authority.
    """
    stmt = (
        select(DunningCycle.organization_id, DunningCycle.id)
        .where(
            DunningCycle.state == DUNNING_OPEN,
            DunningCycle.next_action_at.is_not(None),
            DunningCycle.next_action_at <= now,
        )
        .order_by(DunningCycle.next_action_at)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


# ------------------------------------------------------ the billing run (ADR-0005 addendum)

#: States whose period the billing run rolls; `trial` is discovered by its end instead.
_ROLLING_STATES = (STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED)


def _blocked_by_claim(wall: datetime) -> Any:
    """A lifecycle claim for the subscription is leased (a call is out) or backing off.

    Compared against the **wall clock**, never the run's business `now`: a lease is about how
    long a process may be mid-call, which a synthetic instant says nothing about.
    """
    return (
        select(BillingPeriod.id)
        .where(
            BillingPeriod.organization_id == Subscription.organization_id,
            BillingPeriod.subscription_id == Subscription.id,
            BillingPeriod.kind.in_(LIFECYCLE_KINDS),
            BillingPeriod.state == PERIOD_PENDING,
            or_(BillingPeriod.lease_until > wall, BillingPeriod.next_attempt_at > wall),
        )
        .exists()
    )


def _retry_at() -> Any:
    """When the subscription's pending lifecycle claim may next be tried, if it has one."""
    return (
        select(func.max(BillingPeriod.next_attempt_at))
        .where(
            BillingPeriod.organization_id == Subscription.organization_id,
            BillingPeriod.subscription_id == Subscription.id,
            BillingPeriod.kind.in_(LIFECYCLE_KINDS),
            BillingPeriod.state == PERIOD_PENDING,
        )
        .scalar_subquery()
    )


async def due_billing_subscriptions(
    session: AsyncSession, *, now: datetime, wall: datetime, limit: int = 100
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Every subscription the billing run can work on now, as `(organization_id, id)`.

    **Cross-tenant, on the BYPASSRLS outbox role**, for the reason `due_dunning_cycles` gives: it
    exists to find *which* organizations have work, so there is no tenant to bind while asking.
    Due is two shapes, each on its own partial index: a trial whose end has passed, and an active,
    past-due or suspended subscription whose period has closed. A cancelled subscription matches
    neither — it is never invoiced.

    **A subscription whose claim is leased or backing off is not returned at all** (`NOT EXISTS`),
    and the rest are ordered by when each next becomes workable — its retry time if it has failed
    before, otherwise when it fell due. Without both, a batch of permanently failing rows at the
    head of the order would be returned, skipped and returned again every pass, and a healthy
    subscription behind them would never be reached.

    Ids only, and it reads only the columns the outbox role is granted. The state, the period and
    the pin are re-read, locked, under the tenant binding before anything is decided.
    """
    trial_due = and_(
        Subscription.state == STATE_TRIAL,
        Subscription.trial_ends_at.is_not(None),
        Subscription.trial_ends_at <= now,
    )
    period_due = and_(
        Subscription.state.in_(_ROLLING_STATES),
        Subscription.current_period_end.is_not(None),
        Subscription.current_period_end <= now,
    )
    due_at = case(
        (Subscription.state == STATE_TRIAL, Subscription.trial_ends_at),
        else_=Subscription.current_period_end,
    )
    stmt = (
        select(Subscription.organization_id, Subscription.id)
        .where(or_(trial_due, period_due), ~_blocked_by_claim(wall))
        .order_by(func.coalesce(_retry_at(), due_at), Subscription.id)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


async def actionable_periods(
    session: AsyncSession, *, wall: datetime, limit: int = 100
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Rows that are their own work, as `(organization_id, period_id)`: a pending proration or
    closing period, or an invoice to void — none leased or backing off. Cross-tenant on the
    outbox role like the discovery above; ordered oldest first."""
    stmt = (
        select(BillingPeriod.organization_id, BillingPeriod.id)
        .where(
            or_(
                and_(
                    BillingPeriod.state == PERIOD_PENDING,
                    BillingPeriod.kind.in_((PERIOD_KIND_PRORATION, PERIOD_KIND_CLOSE)),
                ),
                BillingPeriod.state == PERIOD_VOID_PENDING,
            ),
            or_(BillingPeriod.lease_until.is_(None), BillingPeriod.lease_until <= wall),
            or_(BillingPeriod.next_attempt_at.is_(None), BillingPeriod.next_attempt_at <= wall),
        )
        .order_by(BillingPeriod.created_at, BillingPeriod.id)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


async def stale_pending_periods(
    session: AsyncSession, *, wall: datetime, limit: int = 100
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Lifecycle claims nobody is working on: `pending`, lease lapsed, not backing off.

    A claim whose subscription is still due is picked up again by `due_billing_subscriptions`;
    this read exists for the other kind — one whose subscription moved on (cancelled between
    claim and record) and so will never be rediscovered.
    """
    stmt = (
        select(BillingPeriod.organization_id, BillingPeriod.id)
        .where(
            BillingPeriod.state == PERIOD_PENDING,
            BillingPeriod.kind.in_(LIFECYCLE_KINDS),
            or_(BillingPeriod.lease_until.is_(None), BillingPeriod.lease_until <= wall),
            or_(BillingPeriod.next_attempt_at.is_(None), BillingPeriod.next_attempt_at <= wall),
        )
        .order_by(BillingPeriod.created_at)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


async def lock_subscription(
    session: AsyncSession,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
    *,
    skip_locked: bool,
) -> Subscription | None:
    """One subscription with its items, held `FOR UPDATE` for this transaction.

    `skip_locked=True` is the claim's shape — replicas divide the due set instead of queueing on
    it. The record step blocks instead: it already owns a claim. Filtered on `organization_id` as
    well as bound by RLS: the policy is the net, not the filter.
    """
    stmt = _with_items(
        select(Subscription).where(
            Subscription.organization_id == organization_id,
            Subscription.id == subscription_id,
        )
    ).with_for_update(skip_locked=skip_locked, of=Subscription)
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_billing_period(
    session: AsyncSession,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
    kind: str,
    period_start: datetime,
) -> BillingPeriod | None:
    """The claim row for one subscription period of one kind, held `FOR UPDATE`."""
    stmt = (
        select(BillingPeriod)
        .where(
            BillingPeriod.organization_id == organization_id,
            BillingPeriod.subscription_id == subscription_id,
            BillingPeriod.kind == kind,
            BillingPeriod.period_start == period_start,
        )
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_billing_period_by_id(
    session: AsyncSession,
    organization_id: uuid.UUID,
    period_id: uuid.UUID,
    *,
    skip_locked: bool = False,
) -> BillingPeriod | None:
    stmt = (
        select(BillingPeriod)
        .where(BillingPeriod.organization_id == organization_id, BillingPeriod.id == period_id)
        .with_for_update(skip_locked=skip_locked)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def pending_conversion(
    session: AsyncSession, organization_id: uuid.UUID, subscription_id: uuid.UUID
) -> BillingPeriod | None:
    """The trial conversion claimed and not yet recorded for a subscription, held `FOR UPDATE` —
    what a conversion charge that failed before the record step is written onto."""
    stmt = (
        select(BillingPeriod)
        .where(
            BillingPeriod.organization_id == organization_id,
            BillingPeriod.subscription_id == subscription_id,
            BillingPeriod.kind == PERIOD_KIND_TRIAL_CONVERSION,
            BillingPeriod.state == PERIOD_PENDING,
        )
        .order_by(BillingPeriod.period_start.desc())
        .limit(1)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def billing_run_health(
    session: AsyncSession, *, now: datetime, overdue_after: timedelta
) -> dict[str, Any]:
    """The billing run's vital signs, platform-wide. **Counts and instants only.**

    Cross-tenant on the outbox role, reading only its granted columns: the probe that exists to
    notice a stalled run must not be blind to the rows a stall leaves behind.

    * `overdue` — subscriptions due for longer than `overdue_after`. The run polls every few
      minutes, so a non-zero value means it is off, not running, or not keeping up. **Alert.**
    * `pending` / `failing` / `oldest_pending_seconds` — claims not yet recorded, those whose
      last attempt failed, and how long the oldest has waited.
    * `failing_permanent` — claims failing on a data defect, or failing for the fifth time or
      more: a person has to fix something. **Alert.**
    * `void_pending` — invoices for superseded periods still waiting to be voided.
    * `invoiced_24h` / `skipped_24h` / `last_recorded_at` — what it has actually done.
    """
    cutoff = now - overdue_after
    overdue = (
        await session.execute(
            select(func.count(Subscription.id)).where(
                or_(
                    and_(
                        Subscription.state == STATE_TRIAL,
                        Subscription.trial_ends_at <= cutoff,
                    ),
                    and_(
                        Subscription.state.in_(_ROLLING_STATES),
                        Subscription.current_period_end <= cutoff,
                    ),
                )
            )
        )
    ).scalar_one()
    pending, failing, permanent, oldest = (
        await session.execute(
            select(
                func.count(BillingPeriod.id),
                func.count(BillingPeriod.id).filter(BillingPeriod.last_error.is_not(None)),
                func.count(BillingPeriod.id).filter(
                    or_(
                        BillingPeriod.permanent_failure.is_(True),
                        BillingPeriod.attempts >= PERMANENT_AFTER_ATTEMPTS,
                    )
                ),
                func.min(BillingPeriod.created_at),
            ).where(BillingPeriod.state == PERIOD_PENDING)
        )
    ).one()
    void_pending = (
        await session.execute(
            select(func.count(BillingPeriod.id)).where(BillingPeriod.state == PERIOD_VOID_PENDING)
        )
    ).scalar_one()
    since = now - timedelta(hours=24)
    invoiced, skipped, last = (
        await session.execute(
            select(
                func.count(BillingPeriod.id).filter(BillingPeriod.invoice_id.is_not(None)),
                func.count(BillingPeriod.id).filter(BillingPeriod.invoice_id.is_(None)),
                func.max(BillingPeriod.recorded_at),
            ).where(BillingPeriod.recorded_at >= since)
        )
    ).one()
    return {
        "overdue": int(overdue or 0),
        "pending": int(pending or 0),
        "failing": int(failing or 0),
        "failing_permanent": int(permanent or 0),
        "void_pending": int(void_pending or 0),
        "oldest_pending_seconds": (now - oldest).total_seconds() if oldest else 0.0,
        "invoiced_24h": int(invoiced or 0),
        "skipped_24h": int(skipped or 0),
        "last_recorded_at": last.isoformat() if last else None,
    }
