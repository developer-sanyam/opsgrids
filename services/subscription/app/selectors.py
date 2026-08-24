"""Reads for subscription. Visibility rules live here, in one place.

Every table here is tenant-scoped with row-level security, so the database enforces isolation as
a net — but the query still filters, because RLS is the net and not the licence to skip the
filter (ADR-0002). A subscription is an account-level resource: it belongs to the organization,
not to one user, so the scope floor is `ORG` and `ctx.apply` is a no-op that the list endpoint
still routes through, on principle, with a negative-authz test behind it.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Subscription
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
