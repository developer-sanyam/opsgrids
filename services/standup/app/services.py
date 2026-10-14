"""Writes for the standup service.

Every function follows the platform shape: change the row, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens the transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one
without the other, and a rolled-back write never leaves an audit row claiming it happened.

Events are staged into the outbox, never published inline (ADR-0000).

A standup is the day's current word, not an append-only ledger: posting again for the same
person and day replaces the entry (and says so in the audit trail). An entry whose blockers go
from null to text is the flag the Overview surfaces.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import AuditLog, OutboxEvent, StandupEntry
from app.schemas import StandupEntryIn
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ValidationProblem
from platform_core.events import actor_for, publish_event

# Event names. `pulseboard.*` because these are the product's own, not the platform's.
EVT_ENTRY_POSTED = "pulseboard.standup.entry.posted"


async def post_entry(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    person_id: uuid.UUID,
    person_ref: str,
    entry_date: date,
    yesterday: str = "",
    today: str = "",
    blockers: str | None = None,
    request: Request | None = None,
) -> StandupEntry:
    """Post (or repost) one person's check-in for one day. Reposting replaces the entry."""
    # The caller's authorization was checked against `person_id`, but the entry is found by
    # `person_ref`: a pair the stored entries disagree with would replace someone else's.
    if await selectors.person_pair_conflicts(session, ctx.organization_id, person_id, person_ref):
        raise ValidationProblem("person_id and person_ref name different people")
    entry = await selectors.get_entry(session, ctx.organization_id, person_ref, entry_date)
    if entry is None:
        entry = StandupEntry(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            person_id=person_id,
            person_ref=person_ref,
            entry_date=entry_date,
            yesterday=yesterday,
            today=today,
            blockers=blockers,
        )
        session.add(entry)
        await session.flush()
        action = "standup.posted"
    else:
        entry.yesterday = yesterday
        entry.today = today
        entry.blockers = blockers
        entry.updated_by = str(ctx.principal.sub)
        await session.flush()
        action = "standup.reposted"

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="standup_entry",
        entity_id=entry.id,
        after={
            "person_ref": person_ref,
            "date": entry_date.isoformat(),
            "blocked": blockers is not None,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ENTRY_POSTED,
        aggregate_type="standup_entry",
        aggregate_id=entry.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "entry_id": str(entry.id),
            "person_id": str(person_id),
            "person_ref": person_ref,
            "date": entry_date.isoformat(),
            "blocked": blockers is not None,
        },
        actor=actor_for(ctx),
    )
    return entry


async def post_entry_from(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: StandupEntryIn,
    *,
    request: Request | None = None,
) -> StandupEntry:
    """HTTP convenience over `post_entry`."""
    try:
        person_id = uuid.UUID(payload.person_id)
    except ValueError as exc:
        raise ValidationProblem("person_id must be a UUID") from exc
    # The object-level check: an OWN-scoped caller posts their own check-in only.
    ctx.assert_can_touch(owner_id=person_id)
    return await post_entry(
        session,
        ctx,
        person_id=person_id,
        person_ref=payload.person_ref,
        entry_date=payload.date,
        yesterday=payload.yesterday,
        today=payload.today,
        blockers=payload.blockers,
        request=request,
    )
