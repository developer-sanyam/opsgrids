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
from datetime import datetime

from sqlalchemy import Select, func, select, text, tuple_
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


# --------------------------------------------------------------------- the purge sweep
#
# The two reads below are the exception the house pattern (`approval/app/selectors.py`,
# `subscription/app/selectors.py`) always carves out for a cron discovery query: they run
# cross-tenant, on the BYPASSRLS outbox role, because finding out *which* organizations have
# assets due for purging has no tenant to bind while asking (ADR-0009 §8). Both are served by
# the column-level grant `migrations/env.py:grant_purge_sweep_reader` adds — `organization_id,
# id, deleted_at, purged_at` and nothing else, never `original_filename` or `storage_key` —
# rather than the outbox role's usual `SELECT, UPDATE` on `outbox` alone.

#: Every soft-deleted, not-yet-purged asset whose `deleted_at` has crossed the retention
#: cutoff, oldest first. No per-organization cap: unlike the SLA sweep's `approval_steps`
#: (many open steps can pile up per organization), a soft delete is one row per asset and the
#: volume a single tenant can accumulate is bounded by how many images they upload, not by a
#: retry ladder — the fairness problem the SLA sweep's `LATERAL` join solves does not arise
#: here. Matches ADR-0009 §12's index exactly: `(deleted_at) WHERE deleted_at IS NOT NULL AND
#: purged_at IS NULL`, deliberately with no `organization_id` leading column.
_DUE_PURGE_ASSETS_SQL = text(
    """
    SELECT organization_id, id
      FROM document.assets
     WHERE deleted_at IS NOT NULL
       AND purged_at IS NULL
       AND deleted_at <= :cutoff
     ORDER BY deleted_at
     LIMIT :limit
    """
)


async def due_purge_assets(
    session: AsyncSession, *, cutoff: datetime, limit: int = 200
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """`(organization_id, id)` for every asset the sweep should attempt to purge this tick.

    `cutoff` (`now - retention`) is computed once by the caller, in the discovery
    transaction, off the database's own clock, and passed in — one clock, and it is the
    database's, the same amendment ADR-0006 §8 makes for the SLA sweep. The per-asset action
    re-reads its own, deliberately later `now()` when it re-checks the row under the lock.
    """
    rows = (
        (await session.execute(_DUE_PURGE_ASSETS_SQL, {"cutoff": cutoff, "limit": limit}))
        .mappings()
        .all()
    )
    return [(row["organization_id"], row["id"]) for row in rows]


async def get_asset_for_purge_sweep(
    session: AsyncSession, organization_id: uuid.UUID, asset_id: uuid.UUID
) -> Asset | None:
    """The sweep's own lock: `SELECT ... FOR UPDATE SKIP LOCKED`, re-checked here rather than
    trusted from discovery's now-possibly-stale read. `None` covers "another replica's purge
    (or an ordinary `?purge=true` request) is mid-flight", "the row was purged already in the
    gap between discovery and here" and "the row was undeleted in that same gap" all at once —
    the caller treats every one of those as a skip, never an error, which is what makes two
    concurrent sweepers (and a sweeper racing an ordinary request) safe against the same row.

    Filters `deleted_at IS NOT NULL AND purged_at IS NULL` in the query itself, matching
    `get_job_for_firing`'s shape in `services/scheduler`; `app/services.purge_asset` still
    re-checks the retention window against its own, later `now()` after taking the lock,
    because *that* comparison depends on a clock reading the query cannot bake in.
    """
    stmt = (
        select(Asset)
        .where(
            Asset.organization_id == organization_id,
            Asset.id == asset_id,
            Asset.deleted_at.is_not(None),
            Asset.purged_at.is_(None),
        )
        .with_for_update(skip_locked=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def existing_asset_keys(
    session: AsyncSession, pairs: list[tuple[uuid.UUID, uuid.UUID]]
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """Given `(organization_id, id)` pairs parsed off object keys the orphan sweep listed,
    which ones have **any** row at all in `assets` — live, soft-deleted or already purged.

    Used only by the orphan sweep's own discovery (ADR-0009 §9 addendum), on the same
    BYPASSRLS, column-grant-limited connection `due_purge_assets` runs on. Selecting exactly
    `organization_id, id` keeps this inside `grant_purge_sweep_reader`'s grant — a query that
    reached for any other column would fail loudly with `42501` rather than quietly widening
    what a cross-tenant bypass role can read.
    """
    if not pairs:
        return set()
    stmt = select(Asset.organization_id, Asset.id).where(
        tuple_(Asset.organization_id, Asset.id).in_(pairs)
    )
    rows = (await session.execute(stmt)).all()
    return {(row.organization_id, row.id) for row in rows}
