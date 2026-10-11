"""HTTP surface for MRP, mounted behind the gateway's `/api/v1/operations` prefix.

Every route declares its permission, floored at ORG (a plan is a shared operational document —
there is no meaningful OWN reading), and is guarded by `require_product("operations")`. Reads go
through `selectors` (which call `ctx.apply()`), writes through `services`. A run that is not the
caller's organization's is a 404, never a 403. Every mutating route accepts an `Idempotency-Key`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.mrp import models, selectors, services
from app.mrp.schemas import (
    DemandSourcesOut,
    MrpDemandOut,
    MrpRunCreatedOut,
    MrpRunIn,
    MrpRunOut,
    MrpShortageDetailOut,
    MrpShortageOut,
    RequisitionRequestIn,
    RequisitionRequestOut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem, ValidationProblem
from platform_core.pagination import Page, PageQuery, paginate

router = APIRouter(tags=["mrp"], prefix="/operations/manufacturing/mrp")

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
Entitled = Depends(require_product("operations"))

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="Retrying with the same key replays the first result instead of doing the "
        "work again; reusing it for a different request is a 409.",
    ),
]

MrpRead = Annotated[
    ScopeContext, Depends(require("operations.manufacturing.mrp.read", minimum=Scope.ORG))
]
MrpRunPermission = Annotated[
    ScopeContext, Depends(require("operations.manufacturing.mrp.run", minimum=Scope.ORG))
]
MrpRequisition = Annotated[
    ScopeContext, Depends(require("operations.manufacturing.mrp.requisition", minimum=Scope.ORG))
]


async def _run_or_404(session: AsyncSession, ctx: ScopeContext, run_id: uuid.UUID) -> models.MrpRun:
    run = await selectors.get_run(session, ctx, run_id)
    if run is None:
        raise NotFoundProblem("That MRP run is not part of this organization")
    return run


@router.get(
    "/demand-sources",
    response_model=DemandSourcesOut,
    summary="What a new run would plan from, and whether it would run now or in the worker",
    dependencies=[Entitled],
)
async def demand_sources(session: SessionDep, ctx: MrpRead) -> DemandSourcesOut:
    lines, materials = await selectors.count_open_demand(session, ctx.organization_id)
    limit = settings().mrp_sync_max_demands
    return DemandSourcesOut(
        sales_order_lines=lines,
        production_order_materials=materials,
        sync_limit=limit,
        mode_if_auto="worker" if lines + materials > limit else "sync",
    )


@router.get(
    "/runs",
    response_model=Page[MrpRunOut],
    summary="MRP runs, newest first",
    dependencies=[Entitled],
)
async def list_runs(
    session: SessionDep,
    ctx: MrpRead,
    params: PageQuery,
    run_status: Annotated[str | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=40)] = None,
) -> Page[MrpRunOut]:
    if run_status is not None and run_status not in models.RUN_STATUSES:
        raise ValidationProblem(f"{run_status!r} is not an MRP run status")
    rows, total = await paginate(
        session, selectors.runs_stmt(ctx, run_status=run_status, q=q), params
    )
    return Page.build([selectors.run_out(r) for r in rows], total=total, params=params)


@router.post(
    "/runs",
    response_model=MrpRunCreatedOut,
    status_code=status.HTTP_201_CREATED,
    responses={202: {"model": MrpRunCreatedOut, "description": "Queued for the worker"}},
    summary="Start an MRP run (inline when small, queued for the worker when large)",
    dependencies=[Entitled],
)
async def create_run(
    payload: MrpRunIn,
    session: SessionDep,
    ctx: MrpRunPermission,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> MrpRunCreatedOut:
    run, replayed = await services.create_run(
        session, ctx, payload, key=idempotency_key, request=request
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    elif run.status == "QUEUED":
        response.status_code = status.HTTP_202_ACCEPTED
    return MrpRunCreatedOut(**selectors.run_out(run).model_dump(), replayed=replayed)


@router.get(
    "/runs/{run_id}",
    response_model=MrpRunOut,
    summary="One MRP run",
    dependencies=[Entitled],
)
async def read_run(run_id: uuid.UUID, session: SessionDep, ctx: MrpRead) -> MrpRunOut:
    return selectors.run_out(await _run_or_404(session, ctx, run_id))


@router.get(
    "/runs/{run_id}/demands",
    response_model=Page[MrpDemandOut],
    summary="The demand a run planned from",
    dependencies=[Entitled],
)
async def list_demands(
    run_id: uuid.UUID,
    session: SessionDep,
    ctx: MrpRead,
    params: PageQuery,
    source_type: Annotated[str | None, Query()] = None,
) -> Page[MrpDemandOut]:
    await _run_or_404(session, ctx, run_id)
    if source_type is not None and source_type not in models.DEMAND_SOURCES:
        raise ValidationProblem(f"{source_type!r} is not a demand source")
    rows, total = await paginate(
        session, selectors.demands_stmt(ctx, run_id, source_type=source_type), params
    )
    out = await selectors.demand_outs(session, ctx.organization_id, rows)
    return Page.build(out, total=total, params=params)


@router.get(
    "/runs/{run_id}/shortages",
    response_model=Page[MrpShortageOut],
    summary="The shortage table of a run",
    dependencies=[Entitled],
)
async def list_shortages(
    run_id: uuid.UUID,
    session: SessionDep,
    ctx: MrpRead,
    params: PageQuery,
    action: Annotated[str | None, Query()] = None,
    requested: Annotated[bool | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=60)] = None,
) -> Page[MrpShortageOut]:
    await _run_or_404(session, ctx, run_id)
    if action is not None and action not in models.SHORTAGE_ACTIONS:
        raise ValidationProblem(f"{action!r} is not a shortage action")
    rows, total = await paginate(
        session,
        selectors.shortages_stmt(ctx, run_id, action=action, requested=requested, q=q),
        params,
    )
    out = await selectors.shortage_outs(session, ctx.organization_id, rows)
    return Page.build(out, total=total, params=params)


@router.get(
    "/runs/{run_id}/shortages/{shortage_id}",
    response_model=MrpShortageDetailOut,
    summary="One shortage with its pegging (which demand needs it, and through which parent)",
    dependencies=[Entitled],
)
async def read_shortage(
    run_id: uuid.UUID, shortage_id: uuid.UUID, session: SessionDep, ctx: MrpRead
) -> MrpShortageDetailOut:
    await _run_or_404(session, ctx, run_id)
    row = await selectors.get_shortage(session, ctx, run_id, shortage_id)
    if row is None:
        raise NotFoundProblem("That shortage is not part of this run")
    return await selectors.shortage_detail_out(session, ctx, row)


@router.post(
    "/runs/{run_id}/requisitions",
    response_model=RequisitionRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Request a purchase requisition for a run's purchased-item shortages",
    dependencies=[Entitled],
)
async def request_requisition(
    run_id: uuid.UUID,
    payload: RequisitionRequestIn,
    session: SessionDep,
    ctx: MrpRequisition,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> RequisitionRequestOut:
    out = await services.request_requisition(
        session, ctx, run_id, payload, key=idempotency_key, request=request
    )
    if out.replayed:
        response.status_code = status.HTTP_200_OK
    return out
