"""Writes for the timesheet service.

Every function follows the platform shape: change the row, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens the transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one
without the other, and a rolled-back write never leaves an audit row claiming it happened.

Events are staged into the outbox, never published inline (ADR-0000).

The one writer records a whole week — header plus entries — because the grid edits and the
demo seed both speak in weeks, and the approval status moves as a unit. Re-recording an
existing week replaces its entries inside the same transaction, so a half-written week can
never be observed.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import AuditLog, OutboxEvent, Timesheet, TimesheetEntry
from app.schemas import TimesheetWeekIn
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, ForbiddenProblem, ValidationProblem
from platform_core.events import publish_event

# Event names. `pulseboard.*` because these are the product's own, not the platform's.
EVT_WEEK_RECORDED = "pulseboard.time.timesheet.recorded"
EVT_STATUS_CHANGED = "pulseboard.time.timesheet.status_changed"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


async def record_week(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    person_id: uuid.UUID,
    person_ref: str,
    week_start: date,
    status: str = "draft",
    capacity_minutes: int = 2400,
    entries: list[tuple[date, str | None, int, int]] | None = None,
    request: Request | None = None,
) -> Timesheet:
    """Record (or re-record) one person's week: the header and its entries, atomically.

    `entries` is `(work_date, project_ref, entered_minutes, billable_minutes)` — minutes,
    never float hours. Every date must fall inside the week; billable can never exceed
    entered (the database CHECK backs the same rule). Re-recording replaces the entries.

    `status` applies to a new week only (the demo seed creates weeks mid-flow); the HTTP path
    always records drafts, and a week already submitted or approved is a 409 until reopened.
    """
    if week_start.weekday() != 0:
        raise ValidationProblem("week_start must be a Monday")
    week = set(domain.week_dates(week_start))
    for work_date, _ref, entered, billable in entries or []:
        if work_date not in week:
            raise ValidationProblem(f"{work_date} is outside the week of {week_start}")
        if billable > entered:
            raise ValidationProblem("billable hours cannot exceed entered hours")

    # The caller's authorization was checked against `person_id`, but the sheet is found by
    # `person_ref`: a pair the stored weeks disagree with would write someone else's week.
    if await selectors.person_pair_conflicts(session, ctx.organization_id, person_id, person_ref):
        raise ValidationProblem("person_id and person_ref name different people")

    sheet = await selectors.get_week(session, ctx.organization_id, person_ref, week_start)
    if sheet is None:
        sheet = Timesheet(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            person_id=person_id,
            person_ref=person_ref,
            week_start=week_start,
            status=status,
            capacity_minutes=capacity_minutes,
        )
        session.add(sheet)
        await session.flush()
        action = "timesheet.recorded"
    else:
        # Status only moves through `set_status`; a submitted or approved week is frozen until
        # it is reopened, so re-recording keeps the week a draft.
        if sheet.status != "draft":
            raise ConflictProblem(
                f"The week is {sheet.status}; reopen it before recording it again"
            )
        status = sheet.status
        sheet.capacity_minutes = capacity_minutes
        sheet.updated_by = str(ctx.principal.sub)
        await session.execute(
            delete(TimesheetEntry).where(
                TimesheetEntry.organization_id == ctx.organization_id,
                TimesheetEntry.timesheet_id == sheet.id,
            )
        )
        action = "timesheet.rerecorded"

    total_entered = 0
    total_billable = 0
    for work_date, project_ref, entered, billable in entries or []:
        session.add(
            TimesheetEntry(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                timesheet_id=sheet.id,
                work_date=work_date,
                project_ref=project_ref,
                entered_minutes=entered,
                billable_minutes=billable,
            )
        )
        total_entered += entered
        total_billable += billable
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="timesheet",
        entity_id=sheet.id,
        after={
            "person_ref": person_ref,
            "week_start": week_start.isoformat(),
            "status": status,
            "entered_minutes": total_entered,
            "billable_minutes": total_billable,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_WEEK_RECORDED,
        aggregate_type="timesheet",
        aggregate_id=sheet.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "timesheet_id": str(sheet.id),
            "person_id": str(person_id),
            "person_ref": person_ref,
            "week_start": week_start.isoformat(),
            "status": status,
            "billable_minutes": total_billable,
        },
        actor=_actor(ctx),
    )
    return sheet


async def record_week_from(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: TimesheetWeekIn,
    *,
    request: Request | None = None,
) -> Timesheet:
    """HTTP convenience over `record_week`, converting wire hours to minutes."""
    try:
        person_id = uuid.UUID(payload.person_id)
    except ValueError as exc:
        raise ValidationProblem("person_id must be a UUID") from exc
    # The object-level check: an OWN-scoped caller records their own week only.
    ctx.assert_can_touch(owner_id=person_id)
    return await record_week(
        session,
        ctx,
        person_id=person_id,
        person_ref=payload.person_ref,
        week_start=payload.week_start,
        capacity_minutes=domain.minutes(payload.capacity_hours),
        entries=[
            (
                e.date,
                e.project_ref,
                domain.minutes(e.entered_hours),
                domain.minutes(e.billable_hours),
            )
            for e in payload.entries
        ],
        request=request,
    )


async def set_status(
    session: AsyncSession,
    ctx: ScopeContext,
    sheet: Timesheet,
    target_status: str,
    *,
    request: Request | None = None,
) -> Timesheet:
    """Submit or approve one person's week — only `status` moves, the entries are untouched.

    The move must follow `domain.can_transition` (forward-only, plus a reopen back to
    `draft`); anything else is a 409. Approving your own sheet is refused separately, and
    always — holding `time.timesheet.write` on your own row lets you submit it, but never
    approve it, whatever scope that write was granted at.
    """
    ctx.assert_can_touch(owner_id=sheet.person_id)
    if not domain.can_transition(sheet.status, target_status):
        raise ConflictProblem(f"Cannot move a timesheet from {sheet.status!r} to {target_status!r}")
    if target_status == "approved" and sheet.person_id == ctx.principal.subject_ref:
        raise ForbiddenProblem("You cannot approve your own timesheet")

    before_status = sheet.status
    sheet.status = target_status
    sheet.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="timesheet.status_changed",
        entity="timesheet",
        entity_id=sheet.id,
        before={"status": before_status},
        after={"status": target_status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_STATUS_CHANGED,
        aggregate_type="timesheet",
        aggregate_id=sheet.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "timesheet_id": str(sheet.id),
            "person_id": str(sheet.person_id),
            "person_ref": sheet.person_ref,
            "week_start": sheet.week_start.isoformat(),
            "status": target_status,
        },
        actor=_actor(ctx),
    )
    return sheet
