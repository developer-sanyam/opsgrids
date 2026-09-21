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

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.models import SCHEMA, ServiceRequest
from app.schemas import (
    AssetIn,
    AssetOut,
    AssetUpdate,
    CatalogItemIn,
    CatalogItemOut,
    CatalogItemUpdate,
    IncidentIn,
    IncidentOut,
    IncidentStatusIn,
    IncidentUpdate,
    OverviewOut,
    RequestDecisionIn,
    RequestPatch,
    ServiceInfo,
    ServiceRequestIn,
    ServiceRequestOut,
    WorklogIn,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
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
#: Status transitions and delete have no "own" reading — resolving, closing or withdrawing
#: someone else's incident is not something OWN scope should ever grant (the audit's key
#: warning: ITSM_USER holds `incident.write` at OWN and must not resolve or reprioritise their
#: own ticket, let alone anyone else's). `PATCH` stays on `IncidentWrite` because it also carries
#: summary/description edits an OWN caller may make on their own ticket; the ORG floor for its
#: `priority`/`assignee` fields is enforced in `services.update_incident`.
IncidentWriteOrg = Annotated[
    ScopeContext, Depends(require("itsm.service.incident.write", minimum=Scope.ORG))
]
RequestRead = Annotated[ScopeContext, Depends(require("itsm.service.request.read"))]
RequestWrite = Annotated[ScopeContext, Depends(require("itsm.service.request.write"))]
#: Approve, reject, fulfil, assign and delete: no "own" reading, same reasoning as
#: `IncidentWriteOrg` — a requester must not approve or fulfil their own request.
RequestWriteOrg = Annotated[
    ScopeContext, Depends(require("itsm.service.request.write", minimum=Scope.ORG))
]
#: Catalog administration. The catalog is the organization's reference data, not anyone's own
#: row, so it has its own ORG-only code rather than riding on the request write a requester
#: also holds.
CatalogWrite = Annotated[
    ScopeContext, Depends(require("itsm.service.catalog.write", minimum=Scope.ORG))
]
AssetRead = Annotated[ScopeContext, Depends(require("itsm.cmdb.asset.read"))]
AssetWrite = Annotated[ScopeContext, Depends(require("itsm.cmdb.asset.write"))]
#: Deleting CMDB infrastructure is an ORG action; editing one's own asset stays on `AssetWrite`.
AssetWriteOrg = Annotated[
    ScopeContext, Depends(require("itsm.cmdb.asset.write", minimum=Scope.ORG))
]


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
    # `kind` is forced to "note" here, whatever the client sent: status/assignment/sla/resolution
    # entries are system-written by the lifecycle endpoints only, so a caller cannot make the
    # thread claim a change — e.g. a resolution — that never happened (`Must not be built`).
    payload = payload.model_copy(update={"kind": "note"})
    await services.add_worklog(session, ctx, incident, payload, request=request)
    worklog = await selectors.worklogs_for(session, ctx.organization_id, incident.id)
    tags = await selectors.asset_tag_map(session, ctx.organization_id, [incident.affected_asset_id])
    return selectors.incident_out(
        incident, asset_tag=selectors.resolve_tag(tags, incident.affected_asset_id), worklog=worklog
    )


@router.patch(
    "/itsm/incidents/{number}",
    response_model=IncidentOut,
    summary="Edit an incident's properties, reassign it or reprioritise it",
    dependencies=[Entitled],
)
async def update_incident(
    number: str,
    payload: IncidentUpdate,
    session: SessionDep,
    ctx: IncidentWrite,
    request: Request,
) -> IncidentOut:
    incident = await selectors.get_incident(session, ctx.organization_id, number)
    if incident is None:
        raise NotFoundProblem("That incident is not part of this organization")
    ctx.assert_can_touch(owner_id=incident.requester_id)
    updated = await services.update_incident(session, ctx, incident, payload, request=request)
    worklog = await selectors.worklogs_for(session, ctx.organization_id, updated.id)
    tags = await selectors.asset_tag_map(session, ctx.organization_id, [updated.affected_asset_id])
    return selectors.incident_out(
        updated, asset_tag=selectors.resolve_tag(tags, updated.affected_asset_id), worklog=worklog
    )


@router.post(
    "/itsm/incidents/{number}/status",
    response_model=IncidentOut,
    summary="Move an incident through its lifecycle",
    dependencies=[Entitled],
)
async def change_incident_status(
    number: str,
    payload: IncidentStatusIn,
    session: SessionDep,
    ctx: IncidentWriteOrg,
    request: Request,
) -> IncidentOut:
    incident = await selectors.get_incident(session, ctx.organization_id, number)
    if incident is None:
        raise NotFoundProblem("That incident is not part of this organization")
    ctx.assert_can_touch(owner_id=incident.requester_id)
    updated = await services.transition_incident_status(
        session, ctx, incident, payload, request=request
    )
    worklog = await selectors.worklogs_for(session, ctx.organization_id, updated.id)
    tags = await selectors.asset_tag_map(session, ctx.organization_id, [updated.affected_asset_id])
    return selectors.incident_out(
        updated, asset_tag=selectors.resolve_tag(tags, updated.affected_asset_id), worklog=worklog
    )


@router.delete(
    "/itsm/incidents/{number}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw an incident (soft delete)",
    dependencies=[Entitled],
)
async def delete_incident(
    number: str, session: SessionDep, ctx: IncidentWriteOrg, request: Request
) -> Response:
    incident = await selectors.get_incident(session, ctx.organization_id, number)
    if incident is None:
        raise NotFoundProblem("That incident is not part of this organization")
    ctx.assert_can_touch(owner_id=incident.requester_id)
    await services.delete_incident(session, ctx, incident, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


async def _request_out(
    session: AsyncSession, ctx: ScopeContext, req: ServiceRequest
) -> ServiceRequestOut:
    names = await selectors.catalog_name_map(session, ctx.organization_id)
    return selectors.request_out(
        req, catalog_name=names.get(req.catalog_item_code, req.catalog_item_code)
    )


@router.post(
    "/itsm/service-requests",
    response_model=ServiceRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Raise a service request against the catalog",
    dependencies=[Entitled],
)
async def raise_request(
    payload: ServiceRequestIn, session: SessionDep, ctx: RequestWrite, request: Request
) -> ServiceRequestOut:
    req = await services.raise_request(session, ctx, payload, request=request)
    return await _request_out(session, ctx, req)


@router.get(
    "/itsm/service-requests/{number}",
    response_model=ServiceRequestOut,
    summary="One service request",
    dependencies=[Entitled],
)
async def read_request(number: str, session: SessionDep, ctx: RequestRead) -> ServiceRequestOut:
    req = await selectors.get_request(session, ctx.organization_id, number)
    if req is None:
        raise NotFoundProblem("That request is not part of this organization")
    ctx.assert_can_touch(owner_id=req.requester_id)
    return await _request_out(session, ctx, req)


@router.post(
    "/itsm/service-requests/{number}/decision",
    response_model=ServiceRequestOut,
    summary="Approve or reject a request awaiting approval",
    dependencies=[Entitled],
)
async def decide_request(
    number: str,
    payload: RequestDecisionIn,
    session: SessionDep,
    ctx: RequestWriteOrg,
    request: Request,
) -> ServiceRequestOut:
    req = await selectors.get_request(session, ctx.organization_id, number)
    if req is None:
        raise NotFoundProblem("That request is not part of this organization")
    updated = await services.decide_request(session, ctx, req, payload, request=request)
    return await _request_out(session, ctx, updated)


@router.post(
    "/itsm/service-requests/{number}/fulfil",
    response_model=ServiceRequestOut,
    summary="Mark an in-progress request fulfilled",
    dependencies=[Entitled],
)
async def fulfil_request(
    number: str, session: SessionDep, ctx: RequestWriteOrg, request: Request
) -> ServiceRequestOut:
    req = await selectors.get_request(session, ctx.organization_id, number)
    if req is None:
        raise NotFoundProblem("That request is not part of this organization")
    updated = await services.fulfil_request(session, ctx, req, request=request)
    return await _request_out(session, ctx, updated)


@router.patch(
    "/itsm/service-requests/{number}",
    response_model=ServiceRequestOut,
    summary="Assign (or unassign) a service request",
    dependencies=[Entitled],
)
async def update_request(
    number: str,
    payload: RequestPatch,
    session: SessionDep,
    ctx: RequestWriteOrg,
    request: Request,
) -> ServiceRequestOut:
    req = await selectors.get_request(session, ctx.organization_id, number)
    if req is None:
        raise NotFoundProblem("That request is not part of this organization")
    updated = await services.update_request(session, ctx, req, payload, request=request)
    return await _request_out(session, ctx, updated)


@router.delete(
    "/itsm/service-requests/{number}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a service request (soft delete)",
    dependencies=[Entitled],
)
async def delete_request(
    number: str, session: SessionDep, ctx: RequestWriteOrg, request: Request
) -> Response:
    req = await selectors.get_request(session, ctx.organization_id, number)
    if req is None:
        raise NotFoundProblem("That request is not part of this organization")
    await services.delete_request(session, ctx, req, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.post(
    "/itsm/catalog",
    response_model=CatalogItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a service-catalog offering",
    dependencies=[Entitled],
)
async def create_catalog_item(
    payload: CatalogItemIn, session: SessionDep, ctx: CatalogWrite, request: Request
) -> CatalogItemOut:
    item = await services.create_catalog_item(
        session,
        ctx,
        code=payload.id,
        name=payload.name,
        category=payload.category,
        description=payload.description,
        sla_days=payload.sla_days,
        request=request,
    )
    return selectors.catalog_out(item)


@router.patch(
    "/itsm/catalog/{code}",
    response_model=CatalogItemOut,
    summary="Edit a service-catalog offering",
    dependencies=[Entitled],
)
async def update_catalog_item(
    code: str,
    payload: CatalogItemUpdate,
    session: SessionDep,
    ctx: CatalogWrite,
    request: Request,
) -> CatalogItemOut:
    item = await selectors.get_catalog_item(session, ctx.organization_id, code)
    if item is None:
        raise NotFoundProblem("That catalog item is not part of this organization")
    updated = await services.update_catalog_item(session, ctx, item, payload, request=request)
    return selectors.catalog_out(updated)


@router.delete(
    "/itsm/catalog/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retire a service-catalog offering (soft delete)",
    dependencies=[Entitled],
)
async def delete_catalog_item(
    code: str, session: SessionDep, ctx: CatalogWrite, request: Request
) -> Response:
    item = await selectors.get_catalog_item(session, ctx.organization_id, code)
    if item is None:
        raise NotFoundProblem("That catalog item is not part of this organization")
    await services.delete_catalog_item(session, ctx, item, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.patch(
    "/itsm/assets/{tag}",
    response_model=AssetOut,
    summary="Edit a CMDB asset, including retiring it",
    dependencies=[Entitled],
)
async def update_asset(
    tag: str, payload: AssetUpdate, session: SessionDep, ctx: AssetWrite, request: Request
) -> AssetOut:
    asset = await selectors.get_asset(session, ctx.organization_id, tag)
    if asset is None:
        raise NotFoundProblem("That asset is not part of this organization")
    ctx.assert_can_touch(owner_id=asset.owner_id)
    updated = await services.update_asset(session, ctx, asset, payload, request=request)
    open_map = await selectors.open_incident_numbers(session, ctx.organization_id)
    return selectors.asset_out(updated, open_numbers=open_map.get(updated.id, []))


@router.delete(
    "/itsm/assets/{tag}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a CMDB asset (soft delete)",
    dependencies=[Entitled],
)
async def delete_asset(
    tag: str, session: SessionDep, ctx: AssetWriteOrg, request: Request
) -> Response:
    asset = await selectors.get_asset(session, ctx.organization_id, tag)
    if asset is None:
        raise NotFoundProblem("That asset is not part of this organization")
    await services.delete_asset(session, ctx, asset, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ overview


@router.get(
    "/itsm/overview",
    response_model=OverviewOut,
    summary="Service-desk overview rollups",
    dependencies=[Entitled],
)
async def overview(
    session: SessionDep,
    incident_ctx: IncidentRead,
    request_ctx: RequestRead,
    asset_ctx: AssetRead,
) -> OverviewOut:
    # Each count is gated on the read permission for what it counts — incident.read alone used
    # to gate the requests and assets rollups too, which would have handed their counts to a
    # caller who could not otherwise see a single request or asset.
    incidents = await selectors.all_incidents(session, incident_ctx)
    requests = await selectors.all_requests(session, request_ctx)
    assets = await selectors.all_assets(session, asset_ctx)
    return OverviewOut(
        open_incidents=domain.open_incident_count(incidents),
        sla_compliance_pct=domain.sla_compliance_pct(incidents),
        requests_awaiting_approval=domain.requests_awaiting_approval(requests),
        assets_under_management=domain.assets_under_management(assets),
        volume=domain.volume_series(incidents),
        by_priority=domain.breakdown_by_priority(incidents),
        by_category=domain.breakdown_by_category(incidents),
    )
