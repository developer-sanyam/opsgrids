"""Reads for the standup service. Visibility rules live here, in one place.

Every scoped list calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay. A list endpoint that builds
its own query instead of coming through a selector is how an organization leaks, so routers
never write SQL.

Standup scope is org-wide with an OWN reading of "my own check-ins" — the owner column is the
roster person's UUID (see `scope.py`). TEAM and LOCATION are not offered.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StandupEntry
from app.schemas import StandupEntryOut
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ entries


def entries_stmt(
    ctx: ScopeContext,
    *,
    entry_date: date | None = None,
    blocked_only: bool = False,
) -> Select[Any]:
    """Scope-filtered wall, in person-ref order (the wall's stable order for one day)."""
    stmt = select(StandupEntry).where(
        StandupEntry.organization_id == ctx.organization_id,
        StandupEntry.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=StandupEntry.person_id)
    if entry_date is not None:
        stmt = stmt.where(StandupEntry.entry_date == entry_date)
    if blocked_only:
        stmt = stmt.where(StandupEntry.blockers.is_not(None))
    return stmt.order_by(StandupEntry.person_ref.asc(), StandupEntry.entry_date.asc())


async def get_entry(
    session: AsyncSession, org_id: uuid.UUID, person_ref: str, entry_date: date
) -> StandupEntry | None:
    """One person's entry for one day, org-scoped. None on a tenant mismatch."""
    stmt = select(StandupEntry).where(
        StandupEntry.organization_id == org_id,
        StandupEntry.person_ref == person_ref,
        StandupEntry.entry_date == entry_date,
        StandupEntry.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def entries_for(
    session: AsyncSession, ctx: ScopeContext, entry_date: date
) -> list[StandupEntry]:
    """Every in-scope entry of one day's wall."""
    stmt = entries_stmt(ctx, entry_date=entry_date)
    return list((await session.execute(stmt)).scalars().all())


# ------------------------------------------------------------------ projections (ORM → wire)


def entry_out(entry: StandupEntry) -> StandupEntryOut:
    return StandupEntryOut(
        id=str(entry.id),
        person_id=entry.person_ref,
        date=entry.entry_date,
        yesterday=entry.yesterday,
        today=entry.today,
        blockers=entry.blockers,
    )
