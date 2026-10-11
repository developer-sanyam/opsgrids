"""Reads for the timesheet service. Visibility rules live here, in one place.

Every scoped list calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay. A list endpoint that builds
its own query instead of coming through a selector is how an organization leaks, so routers
never write SQL.

Timesheet scope is org-wide with an OWN reading of "my own weeks" — the owner column is the
roster person's UUID (see `scope.py`). TEAM and LOCATION are not offered.

This module also holds the ORM→schema projections: the grid row (seven cells, one per day,
each the sum of that day's entries) is assembled here so routes stay thin, and the entries of a
week's rows are fetched in one query rather than one per row.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain
from app.models import Timesheet, TimesheetEntry
from app.schemas import TimesheetCellOut, TimesheetEntryOut, TimesheetRowOut
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ timesheets


def timesheets_stmt(
    ctx: ScopeContext,
    *,
    week_start: date | None = None,
    week_start_in: list[date] | None = None,
    status: str | None = None,
) -> Select[Any]:
    """Scope-filtered week list, in person-ref order (the grid's stable row order)."""
    stmt = select(Timesheet).where(
        Timesheet.organization_id == ctx.organization_id,
        Timesheet.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Timesheet.person_id)
    if week_start is not None:
        stmt = stmt.where(Timesheet.week_start == week_start)
    if week_start_in is not None:
        stmt = stmt.where(Timesheet.week_start.in_(week_start_in))
    if status:
        stmt = stmt.where(Timesheet.status == status)
    return stmt.order_by(Timesheet.person_ref.asc(), Timesheet.week_start.asc())


async def weeks_in_range(
    session: AsyncSession, ctx: ScopeContext, week_starts: list[date]
) -> list[Timesheet]:
    """Every in-scope row across several weeks, in **one** query — the batched form `weeks_for`
    would otherwise need one call per week for (the utilization series is `weeks_for` called in
    a loop, which is exactly the N+1 CLAUDE.md forbids)."""
    if not week_starts:
        return []
    stmt = timesheets_stmt(ctx, week_start_in=week_starts)
    return list((await session.execute(stmt)).scalars().all())


async def get_week(
    session: AsyncSession, org_id: uuid.UUID, person_ref: str, week_start: date
) -> Timesheet | None:
    """One person's week, org-scoped. None on a tenant mismatch or a soft-deleted row."""
    stmt = select(Timesheet).where(
        Timesheet.organization_id == org_id,
        Timesheet.person_ref == person_ref,
        Timesheet.week_start == week_start,
        Timesheet.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def person_pair_conflicts(
    session: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID, person_ref: str
) -> bool:
    """True when a stored week already binds `person_ref` to another person, or `person_id` to
    another ref. A write that trusts one half of the pair for authorization and the other for
    lookup must refuse a pair the rows disagree with."""
    stmt = (
        select(Timesheet.id)
        .where(
            Timesheet.organization_id == org_id,
            Timesheet.deleted_at.is_(None),
            or_(
                and_(Timesheet.person_ref == person_ref, Timesheet.person_id != person_id),
                and_(Timesheet.person_id == person_id, Timesheet.person_ref != person_ref),
            ),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def weeks_for(session: AsyncSession, ctx: ScopeContext, week_start: date) -> list[Timesheet]:
    """Every in-scope row of one week's grid."""
    stmt = timesheets_stmt(ctx, week_start=week_start)
    return list((await session.execute(stmt)).scalars().all())


async def entries_by_sheet(
    session: AsyncSession, org_id: uuid.UUID, sheet_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[TimesheetEntry]]:
    """The entries of many weeks in one query, grouped by timesheet id."""
    if not sheet_ids:
        return {}
    stmt = (
        select(TimesheetEntry)
        .where(
            TimesheetEntry.organization_id == org_id,
            TimesheetEntry.timesheet_id.in_(sheet_ids),
            TimesheetEntry.deleted_at.is_(None),
        )
        .order_by(TimesheetEntry.work_date.asc())
    )
    out: dict[uuid.UUID, list[TimesheetEntry]] = {}
    for entry in (await session.execute(stmt)).scalars().all():
        out.setdefault(entry.timesheet_id, []).append(entry)
    return out


# ------------------------------------------------------------------ projections (ORM → wire)


def row_out(sheet: Timesheet, entries: list[TimesheetEntry]) -> TimesheetRowOut:
    """The SPA's grid row: seven cells, Monday first, each day the sum of its entries."""
    entered: dict[date, int] = {}
    billable: dict[date, int] = {}
    for entry in entries:
        entered[entry.work_date] = entered.get(entry.work_date, 0) + entry.entered_minutes
        billable[entry.work_date] = billable.get(entry.work_date, 0) + entry.billable_minutes

    cells = [
        TimesheetCellOut(
            date=day,
            entered_hours=domain.hours(entered.get(day, 0)),
            billable_hours=domain.hours(billable.get(day, 0)),
        )
        for day in domain.week_dates(sheet.week_start)
    ]
    entry_items = [
        TimesheetEntryOut(
            date=entry.work_date,
            project_ref=entry.project_ref,
            entered_hours=domain.hours(entry.entered_minutes),
            billable_hours=domain.hours(entry.billable_minutes),
        )
        for entry in entries
    ]
    return TimesheetRowOut(
        person_id=sheet.person_ref,
        week_start=sheet.week_start,
        status=sheet.status,
        cells=cells,
        entries=entry_items,
    )


# ------------------------------------------------------------------ week totals


def week_totals(
    sheets: list[Timesheet], entries: dict[uuid.UUID, list[TimesheetEntry]]
) -> tuple[int, int, int]:
    """(entered, billable, capacity) minutes across one week's rows."""
    entered = sum(e.entered_minutes for rows in entries.values() for e in rows)
    billable = sum(e.billable_minutes for rows in entries.values() for e in rows)
    capacity = sum(s.capacity_minutes for s in sheets)
    return entered, billable, capacity
