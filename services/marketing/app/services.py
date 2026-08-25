"""Writes for the marketing service.

Business rules, audit rows and outbox rows — all inside one transaction. Nothing here opens or
commits: a route with a principal takes its session from `get_tenant_session`, which opens the
request's one transaction and binds it to the caller's organization first (ADR-0002 §1), so the
state change, its audit entry and any event it emits all commit or all roll back together.

Events are staged into the outbox, never published inline. Publishing before the commit is
durable is the bug the outbox exists to prevent (ADR-0000).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import Audience, AuditLog, Campaign, OutboxEvent, RevenueSpendPoint
from app.schemas import AudienceIn, CampaignIn
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

# Event names. `marketing.*` because these are the product's own, not the platform's.
EVT_AUDIENCE_CREATED = "marketing.analytics.audience.created"
EVT_CAMPAIGN_CREATED = "marketing.analytics.campaign.created"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


async def create_audience(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: AudienceIn,
    *,
    request: Request | None = None,
) -> Audience:
    """Create a saved audience segment. Names are unique per organization."""
    if await selectors.get_audience_by_name(session, ctx.organization_id, payload.name):
        raise ConflictProblem(f"An audience named {payload.name!r} already exists")

    audience = Audience(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        name=payload.name,
        size=payload.size,
        criteria=payload.criteria,
        defined_on=payload.created_at,
    )
    session.add(audience)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="audience.created",
        entity="audience",
        entity_id=audience.id,
        after={"name": audience.name, "size": audience.size},
        request=request,
    )
    _publish(
        session,
        organization_id=ctx.organization_id,
        event_type=EVT_AUDIENCE_CREATED,
        aggregate_type="audience",
        aggregate_id=audience.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "audience_id": str(audience.id),
            "name": audience.name,
            "size": audience.size,
        },
        actor=_actor(ctx),
    )
    return audience


async def create_campaign(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: CampaignIn,
    *,
    request: Request | None = None,
) -> Campaign:
    """Create a campaign, after checking its audience belongs to the same organization.

    The audience check is application-level and tenant-scoped: naming an audience id from another
    organization must read as "not found here", never resolve — the same reason the employee
    service checks a reporting manager in code rather than trusting a bare foreign key.
    """
    audience = await selectors.get_audience(session, ctx.organization_id, payload.audience_id)
    if audience is None:
        raise NotFoundProblem("That audience is not part of this organization")

    campaign = Campaign(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        name=payload.name,
        channel=payload.channel,
        status=payload.status,
        currency=payload.currency.upper(),
        budget_minor=payload.budget_minor,
        spent_minor=payload.spent_minor,
        revenue_minor=payload.revenue_minor,
        pipeline_minor=payload.pipeline_minor,
        leads=payload.leads,
        conversions=payload.conversions,
        goal=payload.goal,
        audience_id=payload.audience_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        owner=payload.owner,
    )
    session.add(campaign)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="campaign.created",
        entity="campaign",
        entity_id=campaign.id,
        after={
            "name": campaign.name,
            "channel": campaign.channel,
            "status": campaign.status,
            "budget_minor": campaign.budget_minor,
        },
        request=request,
    )
    _publish(
        session,
        organization_id=ctx.organization_id,
        event_type=EVT_CAMPAIGN_CREATED,
        aggregate_type="campaign",
        aggregate_id=campaign.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "campaign_id": str(campaign.id),
            "name": campaign.name,
            "channel": campaign.channel,
            "status": campaign.status,
            "audience_id": str(campaign.audience_id),
        },
        actor=_actor(ctx),
    )
    return campaign


async def upsert_revenue_point(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    position: int,
    label: str,
    revenue_minor: int,
    spend_minor: int,
    currency: str = "INR",
) -> RevenueSpendPoint:
    """Create or update one point on the org's monthly revenue-vs-spend series.

    Keyed on `(organization_id, position)` so a re-run updates the period in place rather than
    duplicating it — the series is a maintained rollup, not an append-only ledger. Used by the
    demo seed; there is no HTTP write for it, because the series is a derived headline the team
    maintains out of band rather than a per-request mutation.
    """
    existing = await selectors.get_revenue_point(session, ctx.organization_id, position)
    if existing is not None:
        existing.label = label
        existing.revenue_minor = revenue_minor
        existing.spend_minor = spend_minor
        existing.currency = currency.upper()
        existing.updated_by = str(ctx.principal.sub)
        return existing

    point = RevenueSpendPoint(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        position=position,
        label=label,
        revenue_minor=revenue_minor,
        spend_minor=spend_minor,
        currency=currency.upper(),
    )
    session.add(point)
    await session.flush()
    return point


def _publish(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    payload: dict[str, Any],
    actor: dict[str, Any],
) -> None:
    """Stage a domain event in the caller's transaction. Kept as a one-liner wrapper so the two
    write paths above read the same way."""
    publish_event(
        session,
        OutboxEvent,
        organization_id=organization_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        actor=actor,
    )


__all__ = [
    "EVT_AUDIENCE_CREATED",
    "EVT_CAMPAIGN_CREATED",
    "create_audience",
    "create_campaign",
    "upsert_revenue_point",
]
