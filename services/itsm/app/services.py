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

from sqlalchemy import Integer, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import (
    Asset,
    AuditLog,
    CatalogItem,
    Incident,
    IncidentWorklog,
    OutboxEvent,
    ServiceRequest,
)
from app.schemas import (
    AssetIn,
    AssetUpdate,
    CatalogItemUpdate,
    IncidentIn,
    IncidentStatusIn,
    IncidentUpdate,
    RequestDecisionIn,
    RequestPatch,
    ServiceRequestIn,
    WorklogIn,
)
from platform_core.audit import record_audit
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import ConflictProblem, ForbiddenProblem, NotFoundProblem
from platform_core.events import actor_for, publish_event

# Event names. `itsm.*` because these are the product's own, not the platform's.
EVT_INCIDENT_RAISED = "itsm.service.incident.raised"
EVT_INCIDENT_WORKLOGGED = "itsm.service.incident.worklog_added"
EVT_INCIDENT_UPDATED = "itsm.service.incident.updated"
EVT_INCIDENT_STATUS_CHANGED = "itsm.service.incident.status_changed"
EVT_INCIDENT_DELETED = "itsm.service.incident.deleted"
EVT_REQUEST_RAISED = "itsm.service.request.raised"
EVT_REQUEST_DECIDED = "itsm.service.request.decided"
EVT_REQUEST_FULFILLED = "itsm.service.request.fulfilled"
EVT_REQUEST_UPDATED = "itsm.service.request.updated"
EVT_REQUEST_DELETED = "itsm.service.request.deleted"
EVT_ASSET_CREATED = "itsm.cmdb.asset.created"
EVT_ASSET_UPDATED = "itsm.cmdb.asset.updated"
EVT_ASSET_DELETED = "itsm.cmdb.asset.deleted"

#: Server-authored worklog display name. The platform gives no name claim on `Principal` (see
#: `WorklogIn`'s docstring), so system-generated entries — the ones a lifecycle endpoint writes,
#: never the client — use a fixed label rather than guessing at one.
_SYSTEM_AUTHOR = "System"

#: SLA target hours by priority — used to stamp `sla_due_at` when an incident is raised.
_SLA_TARGET_HOURS = {"P1": 2, "P2": 4, "P3": 8, "P4": 24}


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
        actor=actor_for(ctx),
    )
    return incident


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    """Transaction-scoped, keyed lock — releases automatically at commit or rollback. Mirrors
    `services/support/app/services.py::_advisory_lock`."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


async def next_incident_number(session: AsyncSession, org_id: uuid.UUID) -> str:
    """The next `INC-####` for this organization — max existing suffix + 1, floor 4801, under an
    advisory lock keyed by the organization so two concurrent creates cannot compute the same
    number and collide on `uq_incidents_org_number`."""
    await _advisory_lock(session, f"itsm.incidents:{org_id}")
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
    and open the thread with a first note. New incidents start `new` and `on_track`.

    `assignee` requires ORG scope, same floor `update_incident` holds — an OWN-scoped caller
    must not open an already-assigned ticket any more than they may reassign one afterwards."""
    if payload.assignee and ctx.scope is not Scope.ORG:
        raise ForbiddenProblem(
            "Setting an assignee requires ORG scope on itsm.service.incident.write"
        )

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
        actor=actor_for(ctx),
    )
    return entry


def _system_worklog(
    ctx: ScopeContext, incident: Incident, *, kind: str, body: str, at: datetime
) -> IncidentWorklog:
    """One system-written worklog entry — used by the lifecycle writers below, never by a
    client. `author` is the fixed `_SYSTEM_AUTHOR` label; see `WorklogIn`'s docstring for why the
    platform has nothing better to put there."""
    return IncidentWorklog(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        incident_id=incident.id,
        author=_SYSTEM_AUTHOR,
        body=body,
        kind=kind,
        at=at,
    )


async def update_incident(
    session: AsyncSession,
    ctx: ScopeContext,
    incident: Incident,
    payload: IncidentUpdate,
    *,
    request: Request | None = None,
) -> Incident:
    """PATCH the mutable incident fields. `priority` and `assignee` require ORG scope — see
    `IncidentUpdate`'s docstring — checked here rather than at the route, because the same
    endpoint also carries summary/description/category/affected-asset edits, which an OWN-scoped
    caller may make on their own ticket. Every meaningful change appends one system worklog row,
    so the thread stays the record of what happened rather than the edit form."""
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return incident
    if ("priority" in changes or "assignee" in changes) and ctx.scope is not Scope.ORG:
        raise ForbiddenProblem(
            "Changing priority or assignee requires ORG scope on itsm.service.incident.write"
        )

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    now = datetime.now(UTC)
    entries: list[IncidentWorklog] = []

    if "summary" in changes:
        before["summary"] = incident.summary
        incident.summary = changes["summary"]
        after["summary"] = incident.summary
        entries.append(_system_worklog(ctx, incident, kind="note", body="Summary updated.", at=now))

    if "description" in changes:
        before["description"] = incident.description
        incident.description = changes["description"]
        after["description"] = incident.description
        entries.append(
            _system_worklog(ctx, incident, kind="note", body="Description updated.", at=now)
        )

    if "category" in changes:
        before["category"] = incident.category
        incident.category = changes["category"]
        after["category"] = incident.category
        entries.append(
            _system_worklog(
                ctx, incident, kind="note", body=f"Category changed to {incident.category}.", at=now
            )
        )

    if "priority" in changes:
        before["priority"] = incident.priority
        incident.priority = changes["priority"]
        after["priority"] = incident.priority
        # Re-stamp the SLA due time off the same clock `raise_incident` uses — this is the one
        # recompute the audit asks for; the general "never recomputed" gap stays open.
        hours = _SLA_TARGET_HOURS.get(incident.priority, 8)
        incident.sla_due_at = incident.created_at + timedelta(hours=hours)
        incident.sla_minutes_remaining = hours * 60
        entries.append(
            _system_worklog(
                ctx,
                incident,
                kind="status",
                body=f"Priority {before['priority']} → {after['priority']}.",
                at=now,
            )
        )

    if "assignee" in changes:
        before["assignee"] = incident.assignee
        incident.assignee = changes["assignee"] or ""
        after["assignee"] = incident.assignee
        entries.append(
            _system_worklog(
                ctx,
                incident,
                kind="assignment",
                body=f"Assigned to {incident.assignee}." if incident.assignee else "Unassigned.",
                at=now,
            )
        )

    if "affected_asset_tag" in changes:
        tag = changes["affected_asset_tag"]
        tags = await selectors.asset_tag_map(
            session, ctx.organization_id, [incident.affected_asset_id]
        )
        before["affected_asset_tag"] = selectors.resolve_tag(tags, incident.affected_asset_id)
        if tag is None:
            incident.affected_asset_id = None
        else:
            asset = await selectors.get_asset(session, ctx.organization_id, tag)
            if asset is None:
                raise NotFoundProblem("That asset is not part of this organization")
            incident.affected_asset_id = asset.id
        after["affected_asset_tag"] = tag
        entries.append(
            _system_worklog(
                ctx,
                incident,
                kind="note",
                body=f"Affected asset set to {tag}." if tag else "Affected asset cleared.",
                at=now,
            )
        )

    for entry in entries:
        session.add(entry)
    incident.updated_by = str(ctx.principal.sub)
    incident.updated_at = now
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="incident.updated",
        entity="incident",
        entity_id=incident.id,
        before=before,
        after=after,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INCIDENT_UPDATED,
        aggregate_type="incident",
        aggregate_id=incident.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "incident_id": str(incident.id),
            "number": incident.number,
            "changed": sorted(changes),
        },
        actor=actor_for(ctx),
    )
    return incident


async def transition_incident_status(
    session: AsyncSession,
    ctx: ScopeContext,
    incident: Incident,
    payload: IncidentStatusIn,
    *,
    request: Request | None = None,
) -> Incident:
    """Move an incident through `domain.INCIDENT_TRANSITIONS`. ORG scope only — see
    `IncidentStatusWrite` in `routes.py`; an ITSM_USER holding `incident.write` at OWN must not
    resolve or reprioritise their own ticket. Landing on resolved/closed marks the SLA clock met
    (unless it was already breached) rather than recomputing it — the live countdown itself stays
    a stamped-once value; see the open item on SLA recomputation."""
    if not domain.can_transition_incident(incident.status, payload.status):
        raise ConflictProblem(
            f"Incident {incident.number} is {incident.status} and cannot move to {payload.status}"
        )

    before_status = incident.status
    now = datetime.now(UTC)
    incident.status = payload.status

    if payload.status in ("resolved", "closed"):
        if incident.sla_state != "breached":
            incident.sla_state = "met"
        incident.sla_minutes_remaining = None

    kind = "resolution" if payload.status == "resolved" else "status"
    body = f"Status changed: {before_status} → {payload.status}."
    if payload.note:
        body = f"{body} {payload.note}"
    session.add(_system_worklog(ctx, incident, kind=kind, body=body, at=now))

    incident.updated_by = str(ctx.principal.sub)
    incident.updated_at = now
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="incident.status_changed",
        entity="incident",
        entity_id=incident.id,
        before={"status": before_status},
        after={"status": incident.status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INCIDENT_STATUS_CHANGED,
        aggregate_type="incident",
        aggregate_id=incident.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "incident_id": str(incident.id),
            "number": incident.number,
            "from": before_status,
            "to": incident.status,
        },
        actor=actor_for(ctx),
    )
    return incident


async def delete_incident(
    session: AsyncSession,
    ctx: ScopeContext,
    incident: Incident,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete (withdraw) an incident. There is no hard delete: `incident_worklogs.incident_id`
    is `ON DELETE CASCADE` and the append-only trigger refuses the cascaded DELETE, so a hard
    delete fails at the database (`Must not be built`). ORG scope only — see `routes.py`."""
    before_deleted_at = incident.deleted_at
    incident.deleted_at = datetime.now(UTC)
    incident.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="incident.deleted",
        entity="incident",
        entity_id=incident.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": incident.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INCIDENT_DELETED,
        aggregate_type="incident",
        aggregate_id=incident.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "incident_id": str(incident.id),
            "number": incident.number,
        },
        actor=actor_for(ctx),
    )


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
        actor=actor_for(ctx),
    )
    return asset


async def create_asset_from(
    session: AsyncSession, ctx: ScopeContext, payload: AssetIn, *, request: Request | None = None
) -> Asset:
    """HTTP convenience over `create_asset`. `owner_id` is set from the caller: there is no
    people directory to resolve the free-text `owner` display name against, so the creator is
    the OWN-scope owner until the asset is reassigned — without this, an OWN-scoped caller could
    never see an asset they had just created over the API."""
    return await create_asset(
        session,
        ctx,
        tag=payload.tag,
        name=payload.name,
        asset_type=payload.type,
        status=payload.status,
        owner=payload.owner,
        owner_id=ctx.principal.subject_ref,
        location=payload.location,
        serial=payload.serial,
        purchased_at=payload.purchased_at,
        warranty_expires_at=payload.warranty_expires_at,
        cost_minor=payload.cost_minor,
        cost_currency=payload.cost_currency,
        request=request,
    )


async def update_asset(
    session: AsyncSession,
    ctx: ScopeContext,
    asset: Asset,
    payload: AssetUpdate,
    *,
    request: Request | None = None,
) -> Asset:
    """PATCH the mutable asset fields — including `status`, so this is also how an asset is
    retired. `tag` cannot appear in `AssetUpdate`, so it is never in `changes`."""
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return asset

    before: dict[str, Any] = {}
    for field, value in changes.items():
        column = "asset_type" if field == "type" else field
        before[field] = getattr(asset, column)
        if field == "type":
            asset.asset_type = value
        elif field == "cost_currency":
            asset.cost_currency = value.upper()
        else:
            setattr(asset, field, value)
    asset.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="asset.updated",
        entity="asset",
        entity_id=asset.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ASSET_UPDATED,
        aggregate_type="asset",
        aggregate_id=asset.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "asset_id": str(asset.id),
            "tag": asset.tag,
            "changed": sorted(changes),
        },
        actor=actor_for(ctx),
    )
    return asset


async def delete_asset(
    session: AsyncSession,
    ctx: ScopeContext,
    asset: Asset,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a CMDB asset. Refused (409) while an incident is still open against it: a
    hard delete would null real incident history (`Must not be built`), and deleting an asset
    still being worked is a data-hygiene mistake, not a lifecycle event. Retire it instead."""
    open_map = await selectors.open_incident_numbers(session, ctx.organization_id)
    open_numbers = open_map.get(asset.id, [])
    if open_numbers:
        raise ConflictProblem(
            f"{asset.tag} has {len(open_numbers)} open incident(s) against it "
            f"({', '.join(open_numbers)}); resolve them or retire the asset instead of deleting it"
        )

    before_deleted_at = asset.deleted_at
    asset.deleted_at = datetime.now(UTC)
    asset.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="asset.deleted",
        entity="asset",
        entity_id=asset.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": asset.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ASSET_DELETED,
        aggregate_type="asset",
        aggregate_id=asset.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "asset_id": str(asset.id),
            "tag": asset.tag,
        },
        actor=actor_for(ctx),
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


async def update_catalog_item(
    session: AsyncSession,
    ctx: ScopeContext,
    item: CatalogItem,
    payload: CatalogItemUpdate,
    *,
    request: Request | None = None,
) -> CatalogItem:
    """PATCH a catalog item. `code` cannot appear in `CatalogItemUpdate` — it is the API identity
    and the join key `requests.catalog_item_code` reads through, so it is immutable. No outbox
    event: catalog items are reference data, like `create_catalog_item` before this."""
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return item

    before = {field: getattr(item, field) for field in changes}
    for field, value in changes.items():
        setattr(item, field, value)
    item.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="catalog.item_updated",
        entity="catalog_item",
        entity_id=item.id,
        before=before,
        after=changes,
        request=request,
    )
    return item


async def delete_catalog_item(
    session: AsyncSession,
    ctx: ScopeContext,
    item: CatalogItem,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete (retire) a catalog item. Existing requests keep rendering: `catalog_name_map`
    only feeds live items, so `request_out`'s `names.get(code, code)` falls back to the bare code
    for a retired one rather than 404ing the request row."""
    before_deleted_at = item.deleted_at
    item.deleted_at = datetime.now(UTC)
    item.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="catalog.item_deleted",
        entity="catalog_item",
        entity_id=item.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": item.deleted_at},
        request=request,
    )


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
        actor=actor_for(ctx),
    )
    return req


async def next_request_number(session: AsyncSession, org_id: uuid.UUID) -> str:
    """The next `REQ-####` for this organization — max existing suffix + 1, floor 2050, mirroring
    `next_incident_number`, under the same kind of organization-keyed advisory lock."""
    await _advisory_lock(session, f"itsm.requests:{org_id}")
    stmt = select(func.max(cast(func.substring(ServiceRequest.number, 5), Integer))).where(
        ServiceRequest.organization_id == org_id, ServiceRequest.number.like("REQ-%")
    )
    top = (await session.execute(stmt)).scalar_one_or_none()
    return f"REQ-{max(int(top or 0), 2050) + 1}"


async def raise_request(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: ServiceRequestIn,
    *,
    request: Request | None = None,
) -> ServiceRequest:
    """The HTTP raise path: generate a number and record the caller as the requester (OWN scope
    reads what a person raised, the same reading `raise_incident` uses)."""
    number = await next_request_number(session, ctx.organization_id)
    return await create_request(
        session,
        ctx,
        number=number,
        catalog_item_code=payload.catalog_item_id,
        requester=payload.requester,
        requester_id=ctx.principal.subject_ref,
        subject=payload.subject,
        status="approval",
        request=request,
    )


_DECISION_PAST: dict[str, str] = {"approve": "approved", "reject": "rejected"}


async def decide_request(
    session: AsyncSession,
    ctx: ScopeContext,
    req: ServiceRequest,
    payload: RequestDecisionIn,
    *,
    request: Request | None = None,
) -> ServiceRequest:
    """Approve or reject a request awaiting approval. Legal only from `approval`. ORG scope
    only — see `RequestDecisionWrite` in `routes.py`; a requester must not approve their own
    request. `note` is stored as `decision_note` regardless of the decision, so an approval can
    carry a note too, though only a rejection requires one."""
    if req.status != "approval":
        raise ConflictProblem(
            f"Request {req.number} is {req.status} and cannot be {_DECISION_PAST[payload.decision]}"
        )
    if req.requester_id == ctx.principal.subject_ref:
        raise ForbiddenProblem("You cannot decide your own request")

    before_status = req.status
    req.status = "in_progress" if payload.decision == "approve" else "rejected"
    req.decision_note = payload.note
    req.updated_by = str(ctx.principal.sub)
    # Set explicitly rather than left to the column's `onupdate=func.now()`: the server-computed
    # value is unknown to the ORM until reloaded, which would expire the attribute and crash the
    # very next synchronous access (`ServiceRequestOut.updated_at`) with `MissingGreenlet`.
    req.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="request.decided",
        entity="request",
        entity_id=req.id,
        before={"status": before_status},
        after={
            "status": req.status,
            "decision": payload.decision,
            "decision_note": req.decision_note,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_REQUEST_DECIDED,
        aggregate_type="request",
        aggregate_id=req.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "request_id": str(req.id),
            "number": req.number,
            "decision": payload.decision,
            "status": req.status,
        },
        actor=actor_for(ctx),
    )
    return req


async def fulfil_request(
    session: AsyncSession,
    ctx: ScopeContext,
    req: ServiceRequest,
    *,
    request: Request | None = None,
) -> ServiceRequest:
    """`in_progress -> fulfilled`, clearing the assignee — the fulfilment step has nobody left to
    hold the next action. ORG scope only; the requester must not fulfil their own request either,
    same reasoning as `decide_request` — see `RequestWriteOrg`'s docstring in `routes.py`."""
    if req.status != "in_progress":
        raise ConflictProblem(f"Request {req.number} is {req.status} and cannot be fulfilled")
    if req.requester_id == ctx.principal.subject_ref:
        raise ForbiddenProblem("You cannot fulfil your own request")

    before_status = req.status
    req.status = "fulfilled"
    req.assignee = None
    req.updated_by = str(ctx.principal.sub)
    req.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="request.fulfilled",
        entity="request",
        entity_id=req.id,
        before={"status": before_status},
        after={"status": req.status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_REQUEST_FULFILLED,
        aggregate_type="request",
        aggregate_id=req.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "request_id": str(req.id),
            "number": req.number,
        },
        actor=actor_for(ctx),
    )
    return req


async def update_request(
    session: AsyncSession,
    ctx: ScopeContext,
    req: ServiceRequest,
    payload: RequestPatch,
    *,
    request: Request | None = None,
) -> ServiceRequest:
    """PATCH the one field the JML flow lets an agent set directly outside the decision/fulfil
    endpoints. ORG scope only — see `RequestWriteOrg` in `routes.py`."""
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return req

    before = {"assignee": req.assignee}
    req.assignee = changes["assignee"]
    req.updated_by = str(ctx.principal.sub)
    req.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="request.updated",
        entity="request",
        entity_id=req.id,
        before=before,
        after={"assignee": req.assignee},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_REQUEST_UPDATED,
        aggregate_type="request",
        aggregate_id=req.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "request_id": str(req.id),
            "number": req.number,
            "changed": sorted(changes),
        },
        actor=actor_for(ctx),
    )
    return req


async def delete_request(
    session: AsyncSession,
    ctx: ScopeContext,
    req: ServiceRequest,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete (cancel) a service request. ORG scope only — see `routes.py`."""
    before_deleted_at = req.deleted_at
    req.deleted_at = datetime.now(UTC)
    req.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="request.deleted",
        entity="request",
        entity_id=req.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": req.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_REQUEST_DELETED,
        aggregate_type="request",
        aggregate_id=req.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "request_id": str(req.id),
            "number": req.number,
        },
        actor=actor_for(ctx),
    )
