"""Reads for the deal service. Visibility rules live here, in one place.

Every scoped list calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay. A list endpoint that
builds its own query instead of coming through a selector is how an organization leaks,
so routers never write SQL.

Pipeline scope is a shared board with an OWN reading of "the deals I run" — the owner
column is the rep id. TEAM and LOCATION are not offered (see `scope.py`); a selector
passes only `owner_col`, and `ScopeContext.apply` refuses those rungs rather than
widening.

This module also holds the ORM→schema projections, because shaping a row for the wire is
part of reading it, so routes stay thin.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, Deal
from app.schemas import ActivityOut, DealOut
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ deals


def deals_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    stage: str | None = None,
    owner_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
) -> Select[Any]:
    """Scope-filtered deal list. `ctx.apply` is the enforcement point. The search matches
    the deal name — the field the SPA filters on; `owner_id` narrows to one rep's deals
    the way the board's owner dropdown does, and `company_id` to one account's deals (both
    *filters* within scope, never a widening) — the index `ix_deals_org_company` backs the
    latter, so a company's deals card stops fetching the whole board client-side."""
    stmt = select(Deal).where(
        Deal.organization_id == ctx.organization_id,
        Deal.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Deal.owner_id)

    if q:
        stmt = stmt.where(func.lower(Deal.name).like(f"%{q.lower()}%"))
    if stage:
        stmt = stmt.where(Deal.stage == stage)
    if owner_id is not None:
        stmt = stmt.where(Deal.owner_id == owner_id)
    if company_id is not None:
        stmt = stmt.where(Deal.company_id == company_id)

    # Biggest first, then newest — the order the board sorts its columns in.
    return stmt.order_by(Deal.value_minor.desc(), Deal.created_at.desc())


async def get_deal(session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID) -> Deal | None:
    """Org-scoped fetch by id. None on a tenant mismatch or a soft-deleted row —
    confirming an id exists in *some* organization is itself a disclosure."""
    stmt = select(Deal).where(
        Deal.id == deal_id,
        Deal.organization_id == org_id,
        Deal.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_deal_by_id_any_state(
    session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID
) -> Deal | None:
    """Org-scoped fetch by id, soft-deleted or not — for the lead-conversion idempotency
    check, which must recognise "this id was already used" even after the row it named was
    deleted, rather than attempting a second `INSERT` with a primary key Postgres already
    holds (`api/internal.py`'s `convert_lead`, the same reasoning as customer-service's
    `get_company_by_id_any_state`)."""
    stmt = select(Deal).where(Deal.id == deal_id, Deal.organization_id == org_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_deal_by_name(session: AsyncSession, org_id: uuid.UUID, name: str) -> Deal | None:
    stmt = select(Deal).where(
        Deal.name == name,
        Deal.organization_id == org_id,
        Deal.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def all_deals(
    session: AsyncSession, ctx: ScopeContext, *, owner_id: uuid.UUID | None = None
) -> list[Deal]:
    """Every in-scope deal — powers the summary rollups. `owner_id` narrows further, exactly as
    the list endpoint's own filter does (see `deals_stmt`)."""
    return list((await session.execute(deals_stmt(ctx, owner_id=owner_id))).scalars().all())


# ------------------------------------------------------------------ activities


def activities_stmt(
    ctx: ScopeContext,
    *,
    deal_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    activity_type: str | None = None,
    owner_id: uuid.UUID | None = None,
) -> Select[Any]:
    """Scope-filtered timeline, newest first — the order every timeline renders in."""
    stmt = select(Activity).where(
        Activity.organization_id == ctx.organization_id,
        Activity.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Activity.owner_id)

    if deal_id is not None:
        stmt = stmt.where(Activity.deal_id == deal_id)
    if company_id is not None:
        stmt = stmt.where(Activity.company_id == company_id)
    if contact_id is not None:
        stmt = stmt.where(Activity.contact_id == contact_id)
    if activity_type:
        stmt = stmt.where(Activity.activity_type == activity_type)
    if owner_id is not None:
        stmt = stmt.where(Activity.owner_id == owner_id)

    return stmt.order_by(Activity.occurred_at.desc())


async def all_activities(
    session: AsyncSession, ctx: ScopeContext, *, owner_id: uuid.UUID | None = None
) -> list[Activity]:
    """Every in-scope activity — powers the summary's activities-this-week figure."""
    return list((await session.execute(activities_stmt(ctx, owner_id=owner_id))).scalars().all())


# ------------------------------------------------------------------ projections (ORM → wire)


def deal_out(deal: Deal) -> DealOut:
    return DealOut(
        id=str(deal.id),
        name=deal.name,
        company_id=str(deal.company_id),
        primary_contact_id=str(deal.primary_contact_id) if deal.primary_contact_id else None,
        value_minor=deal.value_minor,
        currency=deal.currency,
        stage=deal.stage,
        owner_id=str(deal.owner_id),
        probability=deal.probability,
        close_date=deal.close_date,
        created_at=deal.created_at,
        last_activity_at=deal.last_activity_at,
        source=deal.source,
    )


def activity_out(activity: Activity) -> ActivityOut:
    return ActivityOut(
        id=str(activity.id),
        type=activity.activity_type,
        subject=activity.subject,
        body=activity.body,
        deal_id=str(activity.deal_id) if activity.deal_id else None,
        company_id=str(activity.company_id) if activity.company_id else None,
        contact_id=str(activity.contact_id) if activity.contact_id else None,
        owner_id=str(activity.owner_id),
        occurred_at=activity.occurred_at,
        direction=activity.direction,
    )
