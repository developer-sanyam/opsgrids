"""Reads for subscription. Visibility rules live here, in one place.

Every table here is tenant-scoped with row-level security, so the database enforces isolation as
a net — but the query still filters, because RLS is the net and not the licence to skip the
filter (ADR-0002). A subscription is an account-level resource: it belongs to the organization,
not to one user, so the scope floor is `ORG` and `ctx.apply` is a no-op that the list endpoint
still routes through, on principle, with a negative-authz test behind it.

`due_dunning_cycles` is the one read here with no tenant in it, and the exception is stated at
the function rather than assumed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import DUNNING_OPEN, DunningCycle, Subscription
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

    **The one cross-tenant read in this service, and the only one that will ever be.** It exists
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
