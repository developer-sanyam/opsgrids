"""Writes for the deal service.

Every function follows the platform shape: change the row, record the audit entry, stage
the event — **all inside one transaction**. `get_tenant_session` opens the transaction and
does not commit for the route, so a crash between the state change and the event cannot
leave one without the other, and a rolled-back write never leaves an audit row claiming it
happened. Events are staged into the outbox, never published inline (ADR-0000).

Activities are **append-only**: `log_activity` inserts, and there is deliberately no
update or delete — a database trigger backs that up so the timeline is a record, not a
draft. Logging against a deal also advances the deal's `last_activity_at`, which is the
staleness clock the Overview reads.

Stage moves go through `move_stage`, which enforces `domain.can_transition`: open deals
move freely and close either way; a closed deal is a record and moves nowhere.

These writers drive both the public write routes (`app/api/routes.py`) and the demo seed,
which goes through the same real, RLS-bound path (the finance-service precedent), so
every seeded row lands with an audit row and an outbox event exactly as the live product
produces them. Delete is soft: `deleted_at` is set, never removed — deleting a deal must
never cascade into its (append-only) activities.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import Activity, AuditLog, Deal, OutboxEvent
from app.schemas import DealPatch
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem
from platform_core.events import publish_event
from platform_core.models import utcnow

# Event names. `crm.*` because these are the product's own, not the platform's.
EVT_DEAL_CREATED = "crm.pipeline.deal.created"
EVT_DEAL_UPDATED = "crm.pipeline.deal.updated"
EVT_DEAL_DELETED = "crm.pipeline.deal.deleted"
EVT_DEAL_STAGE_CHANGED = "crm.pipeline.deal.stage_changed"
EVT_ACTIVITY_LOGGED = "crm.pipeline.activity.logged"

#: Where a deal's probability lands when a stage move does not say otherwise — the
#: board's defaults, matching the fixture book's typical values per stage.
STAGE_DEFAULT_PROBABILITY: dict[str, int] = {
    "lead": 20,
    "qualified": 40,
    "proposal": 55,
    "negotiation": 70,
    "closed_won": 100,
    "closed_lost": 0,
}


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


# ------------------------------------------------------------------ deals


async def create_deal(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    name: str,
    company_id: uuid.UUID,
    owner_id: uuid.UUID,
    value_minor: int,
    close_date: date,
    currency: str = "INR",
    stage: str = "lead",
    probability: int | None = None,
    primary_contact_id: uuid.UUID | None = None,
    source: str = "",
    last_activity_at: datetime | None = None,
    deal_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    request: Request | None = None,
) -> Deal:
    """The core deal writer. Inserts the deal, records the audit row and stages the
    `created` event — all in the caller's transaction. `value_minor` is integer minor
    units of `currency`; no float is accepted anywhere on this path."""
    if stage not in domain.STAGE_ORDER:
        raise ConflictProblem(f"{stage!r} is not a pipeline stage")
    if value_minor < 0:
        raise ConflictProblem("A deal's value cannot be negative")
    if await selectors.get_deal_by_name(session, ctx.organization_id, name):
        raise ConflictProblem(f"A deal named {name!r} already exists")

    deal = Deal(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        name=name,
        company_id=company_id,
        primary_contact_id=primary_contact_id,
        value_minor=value_minor,
        currency=currency.upper(),
        stage=stage,
        owner_id=owner_id,
        probability=(probability if probability is not None else STAGE_DEFAULT_PROBABILITY[stage]),
        close_date=close_date,
        source=source,
        last_activity_at=last_activity_at,
    )
    if deal_id is not None:
        deal.id = deal_id
    if created_at is not None:
        deal.created_at = created_at
        deal.updated_at = created_at
    session.add(deal)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="deal.created",
        entity="deal",
        entity_id=deal.id,
        after={"name": name, "stage": stage, "value_minor": value_minor, "currency": deal.currency},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DEAL_CREATED,
        aggregate_type="deal",
        aggregate_id=deal.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "deal_id": str(deal.id),
            "name": name,
            "stage": stage,
            "value_minor": value_minor,
            "currency": deal.currency,
            "owner_id": str(owner_id),
        },
        actor=_actor(ctx),
    )
    return deal


async def update_deal(
    session: AsyncSession,
    ctx: ScopeContext,
    deal: Deal,
    payload: DealPatch,
    *,
    request: Request | None = None,
) -> Deal:
    """Patch the plain deal fields. Deliberately never `stage` — `DealPatch` has no such
    field, so the transition rule in `move_stage` cannot be bypassed by a patch.
    `company_id` / `primary_contact_id` are cross-schema plain UUIDs and are not verified
    here for the same reason `create_deal` does not verify them. `owner_id` is different:
    it is the OWN-scope column, so re-checking the *current* owner on the route and then
    applying a *new* one unchecked would let an own-scoped rep hand their own deal to
    anyone — `ctx.assert_can_touch` re-applies the caller's scope to the incoming value
    exactly as `create_deal`'s route does, so an OWN-scoped caller may only reassign to
    themselves."""
    changes = payload.model_dump(exclude_unset=True)

    if "owner_id" in changes:
        ctx.assert_can_touch(owner_id=changes["owner_id"])

    if "name" in changes and changes["name"] != deal.name:
        existing = await selectors.get_deal_by_name(session, ctx.organization_id, changes["name"])
        if existing is not None and existing.id != deal.id:
            raise ConflictProblem(f"A deal named {changes['name']!r} already exists")

    if "value_minor" in changes and changes["value_minor"] < 0:
        raise ConflictProblem("A deal's value cannot be negative")

    if "currency" in changes:
        changes["currency"] = changes["currency"].upper()

    before = {field: getattr(deal, field) for field in changes}
    for field, value in changes.items():
        setattr(deal, field, value)
    deal.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="deal.updated",
        entity="deal",
        entity_id=deal.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DEAL_UPDATED,
        aggregate_type="deal",
        aggregate_id=deal.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "deal_id": str(deal.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return deal


async def delete_deal(
    session: AsyncSession,
    ctx: ScopeContext,
    deal: Deal,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a deal. Never a SQL DELETE: `activities.deal_id` is a real FK
    `ON DELETE CASCADE`, and a hard delete would cascade into the append-only timeline and
    hit its trigger. A soft delete leaves the activities exactly where they are."""
    before_deleted_at = deal.deleted_at
    deal.deleted_at = utcnow()
    deal.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="deal.deleted",
        entity="deal",
        entity_id=deal.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": deal.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DEAL_DELETED,
        aggregate_type="deal",
        aggregate_id=deal.id,
        payload={"organization_id": str(ctx.organization_id), "deal_id": str(deal.id)},
        actor=_actor(ctx),
    )


async def move_stage(
    session: AsyncSession,
    ctx: ScopeContext,
    deal: Deal,
    *,
    new_stage: str,
    probability: int | None = None,
    request: Request | None = None,
) -> Deal:
    """Move a deal to another stage, under the transition rule in `domain.can_transition`:
    open deals move freely between open stages and close either way; a closed deal is a
    record and moves nowhere. Closing won pins probability to 100, lost to 0, unless the
    caller says otherwise for an open stage."""
    if not domain.can_transition(deal.stage, new_stage):
        raise ConflictProblem(f"A deal in {deal.stage!r} cannot move to {new_stage!r}")

    before = deal.stage
    deal.stage = new_stage
    deal.probability = (
        probability if probability is not None else STAGE_DEFAULT_PROBABILITY[new_stage]
    )
    deal.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="deal.stage_changed",
        entity="deal",
        entity_id=deal.id,
        before={"stage": before},
        after={"stage": new_stage, "probability": deal.probability},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DEAL_STAGE_CHANGED,
        aggregate_type="deal",
        aggregate_id=deal.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "deal_id": str(deal.id),
            "from": before,
            "to": new_stage,
        },
        actor=_actor(ctx),
    )
    return deal


# ------------------------------------------------------------------ activities


async def log_activity(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    activity_type: str,
    subject: str,
    owner_id: uuid.UUID,
    body: str | None = None,
    deal_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    direction: str | None = None,
    occurred_at: datetime | None = None,
    activity_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> Activity:
    """Append one entry to the timeline. Append-only: this inserts and never updates, and
    a database trigger refuses UPDATE/DELETE on the table so that is a guarantee.

    A `deal_id` is validated within the organization — the FK would catch a bad id, but a
    cross-tenant guess must read as not-found, not as a constraint error — and the deal's
    `last_activity_at` staleness clock advances (monotonically) to `occurred_at`.
    """
    at = occurred_at or datetime.now(UTC)

    deal: Deal | None = None
    if deal_id is not None:
        deal = await selectors.get_deal(session, ctx.organization_id, deal_id)
        if deal is None:
            raise NotFoundProblem("That deal is not part of this organization")

    activity = Activity(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        activity_type=activity_type,
        subject=subject,
        body=body,
        deal_id=deal.id if deal else None,
        company_id=company_id,
        contact_id=contact_id,
        owner_id=owner_id,
        occurred_at=at,
        direction=direction,
    )
    if activity_id is not None:
        activity.id = activity_id
    session.add(activity)

    if deal is not None and (deal.last_activity_at is None or at > deal.last_activity_at):
        deal.last_activity_at = at
        deal.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="activity.logged",
        entity="activity",
        entity_id=activity.id,
        after={
            "type": activity_type,
            "subject": subject,
            "deal_id": str(deal_id) if deal_id else None,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ACTIVITY_LOGGED,
        aggregate_type="activity",
        aggregate_id=activity.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "activity_id": str(activity.id),
            "type": activity_type,
            "deal_id": str(deal_id) if deal_id else None,
            "contact_id": str(contact_id) if contact_id else None,
            "occurred_at": at.isoformat(),
        },
        actor=_actor(ctx),
    )
    return activity
