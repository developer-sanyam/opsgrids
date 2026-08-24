"""Writes for the itsm service.

Every function follows the platform shape: change the row, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens the transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one without
the other, and a rolled-back write never leaves an audit row claiming it happened.

Events are staged into the outbox, never published inline (ADR-0000). Incident worklogs are
**append-only**: `add_worklog` inserts, and there is deliberately no update or delete — a database
trigger backs that up so the thread is a record, not a draft.

These writers serve both the HTTP surface (`raise_incident`, `add_worklog`, `create_asset`,
`create_request`) and the demo seed, which calls the lower-level `create_incident` /
`create_request` / `create_asset` / `create_catalog_item` with explicit ids and timestamps so
the live screens mirror the fixtures the user already approved.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import (
    Asset,
    AuditLog,
    CatalogItem,
    Incident,
    IncidentWorklog,
    OutboxEvent,
    ServiceRequest,
)
from app.schemas import AssetIn, IncidentIn, WorklogIn
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

# Event names. `itsm.*` because these are the product's own, not the platform's.
EVT_INCIDENT_RAISED = "itsm.service.incident.raised"
EVT_INCIDENT_WORKLOGGED = "itsm.service.incident.worklog_added"
EVT_REQUEST_RAISED = "itsm.service.request.raised"
EVT_ASSET_CREATED = "itsm.cmdb.asset.created"

#: SLA target hours by priority — used to stamp `sla_due_at` when an incident is raised.
_SLA_TARGET_HOURS = {"P1": 2, "P2": 4, "P3": 8, "P4": 24}


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


# ------------------------------------------------------------------ incidents


async def create_incident(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    number: str,
    summary: str,
    description: str = "",
    priority: str = "P3",
    status: str = "new",
    category: str = "software",
    requester: str,
    requester_id: uuid.UUID | None = None,
    assignee: str = "",
    assignee_id: uuid.UUID | None = None,
    affected_asset_id: uuid.UUID | None = None,
    sla_state: str = "on_track",
    sla_due_at: datetime | None = None,
    sla_breached: bool = False,
    sla_minutes_remaining: int | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    worklog: Sequence[Mapping[str, Any]] = (),
    request: Request | None = None,
) -> Incident:
    """The core incident writer. Inserts the incident and its worklog thread, records the audit
    row and stages the `raised` event — all in the caller's transaction.

    `worklog` is a sequence of `{author, body, kind, at}` mappings appended in order.
    """
    if await selectors.get_incident(session, ctx.organization_id, number):
        raise ConflictProblem(f"An incident numbered {number!r} already exists")

    incident = Incident(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        number=number,
        summary=summary,
        description=description,
        priority=priority,
        status=status,
        category=category,
        requester=requester,
        requester_id=requester_id,
        assignee=assignee,
        assignee_id=assignee_id,
        affected_asset_id=affected_asset_id,
        sla_state=sla_state,
        sla_due_at=sla_due_at,
        sla_breached=sla_breached,
        sla_minutes_remaining=sla_minutes_remaining,
    )
    if created_at is not None:
        incident.created_at = created_at
    if updated_at is not None:
        incident.updated_at = updated_at
    session.add(incident)
    await session.flush()

    for entry in worklog:
        session.add(
            IncidentWorklog(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                incident_id=incident.id,
                author=entry["author"],
                body=entry["body"],
                kind=entry.get("kind", "note"),
                at=entry["at"],
            )
        )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="incident.raised",
        entity="incident",
        entity_id=incident.id,
        after={"number": number, "priority": priority, "status": status, "category": category},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INCIDENT_RAISED,
        aggregate_type="incident",
        aggregate_id=incident.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "incident_id": str(incident.id),
            "number": number,
            "priority": priority,
            "status": status,
            "category": category,
        },
        actor=_actor(ctx),
    )
    return incident


async def next_incident_number(session: AsyncSession, org_id: uuid.UUID) -> str:
    """The next `INC-####` for this organization — max existing suffix + 1, floor 4801."""
    stmt = select(func.max(cast(func.substring(Incident.number, 5), Integer))).where(
        Incident.organization_id == org_id, Incident.number.like("INC-%")
    )
    top = (await session.execute(stmt)).scalar_one_or_none()
    return f"INC-{max(int(top or 0), 4800) + 1}"


async def raise_incident(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: IncidentIn,
    *,
    request: Request | None = None,
) -> Incident:
    """The HTTP raise path: generate a number, resolve the affected asset, stamp the SLA clock
    and open the thread with a first note. New incidents start `new` and `on_track`."""
    affected_asset_id: uuid.UUID | None = None
    if payload.affected_asset_tag:
        asset = await selectors.get_asset(session, ctx.organization_id, payload.affected_asset_tag)
        if asset is None:
            raise NotFoundProblem("That asset is not part of this organization")
        affected_asset_id = asset.id

    now = datetime.now(UTC)
    hours = _SLA_TARGET_HOURS.get(payload.priority, 8)
    due = now + timedelta(hours=hours)
    number = await next_incident_number(session, ctx.organization_id)

    return await create_incident(
        session,
        ctx,
        number=number,
        summary=payload.summary,
        description=payload.description,
        priority=payload.priority,
        status="new",
        category=payload.category,
        requester=payload.requester,
        requester_id=ctx.principal.subject_ref,
        assignee=payload.assignee,
        affected_asset_id=affected_asset_id,
        sla_state="on_track",
        sla_due_at=due,
        sla_breached=False,
        sla_minutes_remaining=hours * 60,
        worklog=[
            {
                "author": payload.requester,
                "body": f"Raised as {payload.priority}.",
                "kind": "note",
                "at": now,
            }
        ],
        request=request,
    )


async def add_worklog(
    session: AsyncSession,
    ctx: ScopeContext,
    incident: Incident,
    payload: WorklogIn,
    *,
    at: datetime | None = None,
    request: Request | None = None,
) -> IncidentWorklog:
    """Append one entry to an incident's thread. Append-only: this inserts and never updates,
    and a database trigger refuses UPDATE/DELETE on the table so that is a guarantee."""
    entry = IncidentWorklog(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        incident_id=incident.id,
        author=payload.author,
        body=payload.body,
        kind=payload.kind,
        at=at or datetime.now(UTC),
    )
    session.add(entry)
    incident.updated_by = str(ctx.principal.sub)
    incident.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="incident.worklog_added",
        entity="incident",
        entity_id=incident.id,
        after={"kind": payload.kind, "author": payload.author},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INCIDENT_WORKLOGGED,
        aggregate_type="incident",
        aggregate_id=incident.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "incident_id": str(incident.id),
            "number": incident.number,
            "kind": payload.kind,
        },
        actor=_actor(ctx),
    )
    return entry


# ------------------------------------------------------------------ assets


async def create_asset(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    tag: str,
    name: str,
    asset_type: str,
    status: str = "in_stock",
    owner: str | None = None,
    owner_id: uuid.UUID | None = None,
    location: str = "",
    serial: str = "",
    purchased_at: Any = None,
    warranty_expires_at: Any = None,
    cost_minor: int = 0,
    cost_currency: str = "INR",
    request: Request | None = None,
) -> Asset:
    """Create a CMDB asset. `cost_minor` is integer minor units in `cost_currency`."""
    if await selectors.get_asset(session, ctx.organization_id, tag):
        raise ConflictProblem(f"An asset tagged {tag!r} already exists")

    asset = Asset(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        tag=tag,
        name=name,
        asset_type=asset_type,
        status=status,
        owner=owner,
        owner_id=owner_id,
        location=location,
        serial=serial,
        purchased_at=purchased_at,
        warranty_expires_at=warranty_expires_at,
        cost_minor=cost_minor,
        cost_currency=cost_currency.upper(),
    )
    session.add(asset)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="asset.created",
        entity="asset",
        entity_id=asset.id,
        after={"tag": tag, "type": asset_type, "status": status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ASSET_CREATED,
        aggregate_type="asset",
        aggregate_id=asset.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "asset_id": str(asset.id),
            "tag": tag,
            "type": asset_type,
        },
        actor=_actor(ctx),
    )
    return asset


async def create_asset_from(
    session: AsyncSession, ctx: ScopeContext, payload: AssetIn, *, request: Request | None = None
) -> Asset:
    """HTTP convenience over `create_asset`."""
    return await create_asset(
        session,
        ctx,
        tag=payload.tag,
        name=payload.name,
        asset_type=payload.type,
        status=payload.status,
        owner=payload.owner,
        location=payload.location,
        serial=payload.serial,
        purchased_at=payload.purchased_at,
        warranty_expires_at=payload.warranty_expires_at,
        cost_minor=payload.cost_minor,
        cost_currency=payload.cost_currency,
        request=request,
    )


# ------------------------------------------------------------------ catalog & requests


async def create_catalog_item(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    name: str,
    category: str,
    description: str = "",
    sla_days: int = 1,
    requests_30d: int = 0,
    request: Request | None = None,
) -> CatalogItem:
    """Create a service-catalog offering. Reference data for the request surface."""
    if await selectors.get_catalog_item(session, ctx.organization_id, code):
        raise ConflictProblem(f"A catalog item coded {code!r} already exists")

    item = CatalogItem(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=code,
        name=name,
        category=category,
        description=description,
        sla_days=sla_days,
        requests_30d=requests_30d,
    )
    session.add(item)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="catalog.item_created",
        entity="catalog_item",
        entity_id=item.id,
        after={"code": code, "category": category},
        request=request,
    )
    return item


async def create_request(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    number: str,
    catalog_item_code: str,
    category: str | None = None,
    requester: str,
    requester_id: uuid.UUID | None = None,
    subject: str,
    status: str = "approval",
    assignee: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    request: Request | None = None,
) -> ServiceRequest:
    """Raise a service request against a catalog item. Validates the catalog code within the
    organization and denormalises the category from it when not supplied."""
    if await selectors.get_request(session, ctx.organization_id, number):
        raise ConflictProblem(f"A request numbered {number!r} already exists")

    item = await selectors.get_catalog_item(session, ctx.organization_id, catalog_item_code)
    if item is None:
        raise NotFoundProblem(f"No catalog item {catalog_item_code!r} in this organization")

    req = ServiceRequest(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        number=number,
        catalog_item_code=catalog_item_code,
        category=category or item.category,
        requester=requester,
        requester_id=requester_id,
        subject=subject,
        status=status,
        assignee=assignee,
    )
    if created_at is not None:
        req.created_at = created_at
    if updated_at is not None:
        req.updated_at = updated_at
    session.add(req)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="request.raised",
        entity="request",
        entity_id=req.id,
        after={"number": number, "catalog_item_code": catalog_item_code, "status": status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_REQUEST_RAISED,
        aggregate_type="request",
        aggregate_id=req.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "request_id": str(req.id),
            "number": number,
            "catalog_item_code": catalog_item_code,
            "status": status,
        },
        actor=_actor(ctx),
    )
    return req
