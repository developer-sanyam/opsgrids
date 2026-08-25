"""Reads for the marketing service. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay.

Marketing data is org-wide analytics rather than per-person records, so the product's scope
resolver maps every rung to the campaign row itself and reads run at ORG. `ctx.apply` is still
the enforcement point: an ORG grant passes the statement through unchanged, and any narrower
grant without a subject reference matches nothing rather than leaking — the safe direction.

A list endpoint that builds its own query instead of coming through a selector is how an
organization leaks, so routers never write SQL.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audience, Campaign, RevenueSpendPoint
from platform_core.auth import ScopeContext


def campaigns_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    channel: str | None = None,
    status: str | None = None,
    audience_id: uuid.UUID | None = None,
) -> Select[Any]:
    """Scope-filtered campaign list. `ctx.apply` is the enforcement point.

    Search matches campaign name, goal or owner. The result orders by name so the table is stable
    between renders.
    """
    stmt = select(Campaign).where(
        Campaign.organization_id == ctx.organization_id,
        Campaign.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Campaign.id)

    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Campaign.name).like(needle),
                func.lower(Campaign.goal).like(needle),
                func.lower(Campaign.owner).like(needle),
            )
        )
    if channel:
        stmt = stmt.where(Campaign.channel == channel)
    if status:
        stmt = stmt.where(Campaign.status == status)
    if audience_id:
        stmt = stmt.where(Campaign.audience_id == audience_id)

    return stmt.order_by(Campaign.name)


async def list_campaigns(ctx: ScopeContext, session: AsyncSession) -> list[Campaign]:
    """Every in-scope campaign for the organization, unpaginated — what the rollups fold over."""
    stmt = campaigns_stmt(ctx).order_by(Campaign.name)
    return list((await session.execute(stmt)).scalars().all())


async def get_campaign(
    session: AsyncSession, org_id: uuid.UUID, campaign_id: uuid.UUID
) -> Campaign | None:
    """Org-scoped fetch by id. Returns None on a tenant mismatch or a soft-deleted row —
    confirming an id exists in *some* organization is itself a disclosure."""
    stmt = select(Campaign).where(
        Campaign.id == campaign_id,
        Campaign.organization_id == org_id,
        Campaign.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def audiences_stmt(ctx: ScopeContext, *, q: str | None = None) -> Select[Any]:
    """Scope-filtered audience list, ordered by name."""
    stmt = select(Audience).where(
        Audience.organization_id == ctx.organization_id,
        Audience.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Audience.id)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Audience.name).like(needle),
                func.lower(Audience.criteria).like(needle),
            )
        )
    return stmt.order_by(Audience.name)


async def get_audience(
    session: AsyncSession, org_id: uuid.UUID, audience_id: uuid.UUID
) -> Audience | None:
    """Org-scoped fetch by id."""
    stmt = select(Audience).where(
        Audience.id == audience_id,
        Audience.organization_id == org_id,
        Audience.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_audience_by_name(
    session: AsyncSession, org_id: uuid.UUID, name: str
) -> Audience | None:
    """Looked up within one organization — an audience name is unique per org, not globally."""
    stmt = select(Audience).where(
        Audience.organization_id == org_id,
        Audience.name == name,
        Audience.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def revenue_series(session: AsyncSession, org_id: uuid.UUID) -> list[RevenueSpendPoint]:
    """The org-wide monthly revenue-vs-spend series, in chronological order."""
    stmt = (
        select(RevenueSpendPoint)
        .where(
            RevenueSpendPoint.organization_id == org_id,
            RevenueSpendPoint.deleted_at.is_(None),
        )
        .order_by(RevenueSpendPoint.position)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_revenue_point(
    session: AsyncSession, org_id: uuid.UUID, position: int
) -> RevenueSpendPoint | None:
    """One series point by its chronological position — the idempotency key the seed upserts on."""
    stmt = select(RevenueSpendPoint).where(
        RevenueSpendPoint.organization_id == org_id,
        RevenueSpendPoint.position == position,
        RevenueSpendPoint.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()
