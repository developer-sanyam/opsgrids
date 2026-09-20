"""Reads for document. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters on `organization_id` explicitly.
Row-level security is the net, not the filter (CLAUDE.md), and a list endpoint that queries
directly instead of coming through a selector is how an organization leaks.

`ctx.apply()` is a no-op for these two permissions, because both are ceilinged at ORG and
there is no narrower rung to represent (ADR-0009 §4). It is called anyway, and that is not
ceremony: the ceiling is catalogue data a later edit can widen, and the day it widens this is
the line that has to mean something. `uploaded_by_user_id` is handed to it as the owner column
so a hypothetical OWN grant narrows to the caller's own uploads rather than silently matching
everything.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset
from platform_core.auth import ScopeContext
from platform_core.pagination import PageParams, paginate


@dataclass(frozen=True, slots=True)
class Usage:
    """What `SUM(size_bytes)` and `COUNT(*)` say over the organization's live assets.

    Soft-deleted rows are excluded, which is what makes "delete frees quota immediately" true.
    Real storage can therefore exceed the configured ceiling by up to one retention window's
    worth of deletions (ADR-0009 §8) — bought deliberately, because the alternative is a
    merchant who deletes images to free space and watches nothing happen for a month.
    """

    bytes_used: int
    asset_count: int


def _live(ctx: ScopeContext) -> Select[tuple[Asset]]:
    """Every read starts here: this organization, not soft-deleted, scope applied."""
    stmt = select(Asset).where(
        Asset.organization_id == ctx.organization_id,
        Asset.deleted_at.is_(None),
    )
    return ctx.apply(stmt, owner_col=Asset.uploaded_by_user_id)


async def list_assets(
    session: AsyncSession, ctx: ScopeContext, params: PageParams
) -> tuple[list[Asset], int]:
    """The editor's picker. Newest first, and never unbounded — `paginate` counts and pages
    in one transaction, so the total and the items cannot disagree under a concurrent write."""
    stmt = _live(ctx).order_by(Asset.created_at.desc(), Asset.id.desc())
    return await paginate(session, stmt, params)


async def get_asset(session: AsyncSession, ctx: ScopeContext, asset_id: uuid.UUID) -> Asset | None:
    """One live asset, or None. Returns None rather than raising on a cross-tenant id:
    confirming that an id exists in *some* organization is itself a disclosure."""
    stmt = _live(ctx).where(Asset.id == asset_id)
    return (await session.execute(stmt)).scalars().one_or_none()


async def get_asset_for_removal(
    session: AsyncSession, ctx: ScopeContext, asset_id: uuid.UUID
) -> Asset | None:
    """One asset **including a soft-deleted one**, or None.

    Separate from `get_asset` on purpose: the immediate-takedown path exists precisely for
    the merchant who removed the wrong file from their library an hour ago and has now
    realised it is still world-readable. If purge could only reach live rows, the one case
    ADR-0009 §8 built it for would be the one case it could not serve.
    """
    stmt = select(Asset).where(
        Asset.organization_id == ctx.organization_id,
        Asset.id == asset_id,
    )
    stmt = ctx.apply(stmt, owner_col=Asset.uploaded_by_user_id)
    return (await session.execute(stmt)).scalars().one_or_none()


async def usage(session: AsyncSession, ctx: ScopeContext) -> Usage:
    """Bytes and rows consumed by this organization's live assets.

    One query, both aggregates — and called inside the insert's own transaction so it
    serialises against a concurrent upload rather than racing it. Cheap at these row counts;
    when it stops being cheap it becomes a counter, not a second query.
    """
    stmt = (
        select(
            func.coalesce(func.sum(Asset.size_bytes), 0),
            func.count(),
        )
        .select_from(Asset)
        .where(
            Asset.organization_id == ctx.organization_id,
            Asset.deleted_at.is_(None),
        )
    )
    bytes_used, asset_count = (
        await session.execute(ctx.apply(stmt, owner_col=Asset.uploaded_by_user_id))
    ).one()
    return Usage(bytes_used=int(bytes_used), asset_count=int(asset_count))
