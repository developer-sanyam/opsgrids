"""HTTP surface for the itsm service.

Mounted behind the gateway's `/api/v1/itsm` prefix, so every route path here begins `/itsm/...`.
The read endpoints are the priority — they are what flips the ITSM SPA from fixtures to live —
and each returns exactly the shape `frontend/product-itsm/src/api/types.ts` declares.

Every route declares the permission it needs and is guarded by `require_product("itsm")` as
defence in depth behind the gateway's own entitlement gate. Reads go through `selectors`, writes
through `services`; a router never builds its own query. Every route takes the tenant-bound
session and opens no transaction of its own — the chokepoint owns it.

Object-level scope is re-applied on every detail route: filtering the list and forgetting the
detail is the named vulnerability, so a caller who can only list their own tickets is refused
another's by number.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import (
    AssetIn,
    AssetOut,
    CatalogItemOut,
    IncidentIn,
    IncidentOut,
    OverviewOut,
    ServiceInfo,
    ServiceRequestOut,
    WorklogIn,
)
from platform_core.auth import ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the ITSM scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers ITSM scope questions.
scope.register()

router = APIRouter(tags=["itsm"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `itsm` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("itsm"))

IncidentRead = Annotated[ScopeContext, Depends(require("itsm.service.incident.read"))]
IncidentWrite = Annotated[ScopeContext, Depends(require("itsm.service.incident.write"))]
RequestRead = Annotated[ScopeContext, Depends(require("itsm.service.request.read"))]
AssetRead = Annotated[ScopeContext, Depends(require("itsm.cmdb.asset.read"))]
AssetWrite = Annotated[ScopeContext, Depends(require("itsm.cmdb.asset.write"))]


@router.get("/itsm/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


# ------------------------------------------------------------------ incidents


@router.get(
    "/itsm/incidents",
    response_model=Page[IncidentOut],
    summary="Incidents in the service desk",
    dependencies=[Entitled],
)
async def list_incidents(
    session: SessionDep,
    ctx: IncidentRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Number, summary, assignee or requester")] = None,
    priority: Annotated[str | None, Query()] = None,
    incident_status: Annotated[str | None, Query(alias="status")] = None,
    sort: Annotated[str | None, Query(description="updated | priority | created")] = None,
) -> Page[IncidentOut]:
    stmt = selectors.incidents_stmt(ctx, q=q, priority=priority, status=incident_status, sort=sort)
    rows, total = await paginate(session, stmt, params)
    tags = await selectors.asset_tag_map(
        session, ctx.organization_id, [r.affected_asset_id for r in rows]
    )
    items = [
        selectors.incident_out(r, asset_tag=selectors.resolve_tag(tags, r.affected_asset_id))
        for r in rows
    ]
    return Page.build(items, total=total, params=params)


@router.post(
    "/itsm/incidents",
    response_model=IncidentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Raise an incident",
    dependencies=[Entitled],
)
async def raise_incident(
    payload: IncidentIn, session: SessionDep, ctx: IncidentWrite, request: Request
) -> IncidentOut:
    incident = await services.raise_incident(session, ctx, payload, request=request)
    worklog = await selectors.worklogs_for(session, ctx.organization_id, incident.id)
    tags = await selectors.asset_tag_map(session, ctx.organization_id, [incident.affected_asset_id])
    return selectors.incident_out(
        incident, asset_tag=selectors.resolve_tag(tags, incident.affected_asset_id), worklog=worklog
    )


@router.get(
    "/itsm/incidents/{number}",
    response_model=IncidentOut,
    summary="One incident, with its worklog thread and affected asset",
    dependencies=[Entitled],
)
async def read_incident(number: str, session: SessionDep, ctx: IncidentRead) -> IncidentOut:
    incident = await selectors.get_incident(session, ctx.organization_id, number)
    if incident is None:
        raise NotFoundProblem("That incident is not part of this organization")
    ctx.assert_can_touch(owner_id=incident.requester_id)
    worklog = await selectors.worklogs_for(session, ctx.organization_id, incident.id)
    tags = await selectors.asset_tag_map(session, ctx.organization_id, [incident.affected_asset_id])
    return selectors.incident_out(
        incident, asset_tag=selectors.resolve_tag(tags, incident.affected_asset_id), worklog=worklog
    )


@router.post(
    "/itsm/incidents/{number}/worklog",
    response_model=IncidentOut,
    summary="Append an entry to an incident's thread (append-only)",
    dependencies=[Entitled],
)
async def add_worklog(
    number: str,
    payload: WorklogIn,
    session: SessionDep,
    ctx: IncidentWrite,
    request: Request,
) -> IncidentOut:
    incident = await selectors.get_incident(session, ctx.organization_id, number)
    if incident is None:
        raise NotFoundProblem("That incident is not part of this organization")
    ctx.assert_can_touch(owner_id=incident.requester_id)
    await services.add_worklog(session, ctx, incident, payload, request=request)
    worklog = await selectors.worklogs_for(session, ctx.organization_id, incident.id)
    tags = await selectors.asset_tag_map(session, ctx.organization_id, [incident.affected_asset_id])
    return selectors.incident_out(
        incident, asset_tag=selectors.resolve_tag(tags, incident.affected_asset_id), worklog=worklog
    )


# ------------------------------------------------------------------ service requests & catalog


@router.get(
    "/itsm/service-requests",
    response_model=Page[ServiceRequestOut],
    summary="Service requests (the JML and fulfilment flows)",
    dependencies=[Entitled],
)
async def list_requests(
    session: SessionDep,
    ctx: RequestRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Number, requester or subject")] = None,
    request_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[ServiceRequestOut]:
    stmt = selectors.requests_stmt(ctx, q=q, status=request_status)
    rows, total = await paginate(session, stmt, params)
    names = await selectors.catalog_name_map(session, ctx.organization_id)
    items = [
        selectors.request_out(r, catalog_name=names.get(r.catalog_item_code, r.catalog_item_code))
        for r in rows
    ]
    return Page.build(items, total=total, params=params)


@router.get(
    "/itsm/catalog",
    response_model=Page[CatalogItemOut],
    summary="The service catalog",
    dependencies=[Entitled],
)
async def list_catalog(
    session: SessionDep, ctx: RequestRead, params: PageQuery
) -> Page[CatalogItemOut]:
    stmt = selectors.catalog_stmt(ctx.organization_id)
    rows, total = await paginate(session, stmt, params)
    return Page.build([selectors.catalog_out(i) for i in rows], total=total, params=params)


# ------------------------------------------------------------------ assets (CMDB)


@router.get(
    "/itsm/assets",
    response_model=Page[AssetOut],
    summary="CMDB assets",
    dependencies=[Entitled],
)
async def list_assets(
    session: SessionDep,
    ctx: AssetRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Tag, name, owner or serial")] = None,
    asset_type: Annotated[str | None, Query(alias="type")] = None,
    asset_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[AssetOut]:
    stmt = selectors.assets_stmt(ctx, q=q, asset_type=asset_type, status=asset_status)
    rows, total = await paginate(session, stmt, params)
    open_map = await selectors.open_incident_numbers(session, ctx.organization_id)
    items = [selectors.asset_out(a, open_numbers=open_map.get(a.id, [])) for a in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/itsm/assets",
    response_model=AssetOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a CMDB asset",
    dependencies=[Entitled],
)
async def create_asset(
    payload: AssetIn, session: SessionDep, ctx: AssetWrite, request: Request
) -> AssetOut:
    asset = await services.create_asset_from(session, ctx, payload, request=request)
    return selectors.asset_out(asset)


@router.get(
    "/itsm/assets/{tag}",
    response_model=AssetOut,
    summary="One CMDB asset, with the incidents open against it",
    dependencies=[Entitled],
)
async def read_asset(tag: str, session: SessionDep, ctx: AssetRead) -> AssetOut:
    asset = await selectors.get_asset(session, ctx.organization_id, tag)
    if asset is None:
        raise NotFoundProblem("That asset is not part of this organization")
    ctx.assert_can_touch(owner_id=asset.owner_id)
    open_map = await selectors.open_incident_numbers(session, ctx.organization_id)
    return selectors.asset_out(asset, open_numbers=open_map.get(asset.id, []))


# ------------------------------------------------------------------ overview


@router.get(
    "/itsm/overview",
    response_model=OverviewOut,
    summary="Service-desk overview rollups",
    dependencies=[Entitled],
)
async def overview(session: SessionDep, ctx: IncidentRead) -> OverviewOut:
    incidents = await selectors.all_incidents(session, ctx)
    requests = await selectors.all_requests(session, ctx)
    assets = await selectors.all_assets(session, ctx)
    return OverviewOut(
        open_incidents=domain.open_incident_count(incidents),
        sla_compliance_pct=domain.sla_compliance_pct(incidents),
        requests_awaiting_approval=domain.requests_awaiting_approval(requests),
        assets_under_management=domain.assets_under_management(assets),
        volume=domain.volume_series(incidents),
        by_priority=domain.breakdown_by_priority(incidents),
        by_category=domain.breakdown_by_category(incidents),
    )
