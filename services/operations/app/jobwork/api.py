"""HTTP surface for job work (Inventory plan Phase 6), mounted beside the main operations router
(`app/main.py`).

Every route declares the permission it needs, is behind `require_product("operations")`, runs in
the tenant-bound request transaction (`get_tenant_session` — the chokepoint, no route opens its
own), reads through `selectors` and writes through `services`. Every permission is floored at ORG:
a job-work order is a shared operational document with no per-person owner, so an OWN reading
would mean nothing and is refused rather than silently widened.

A request naming another tenant's order, material or challan is a 404 that does not say the row
exists elsewhere — the services look everything up under the caller's `organization_id`.

`Idempotency-Key` is **required** on the writes that move stock or create a numbered document
(create, issue, receive) and honoured, when sent, on approve / cancel / close.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobwork import domain, selectors, services
from app.jobwork.schemas import (
    ChallanEwayIn,
    ChallanOut,
    JobWorkIssueIn,
    JobWorkOrderDetailOut,
    JobWorkOrderIn,
    JobWorkOrderOut,
    JobWorkOrderPatch,
    JobWorkReceiveIn,
    JobWorkTransitionIn,
    ReconciliationOut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

router = APIRouter(tags=["operations-jobwork"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
Entitled = Depends(require_product("operations"))

JobWorkRead = Annotated[
    ScopeContext, Depends(require("operations.jobwork.order.read", minimum=Scope.ORG))
]
JobWorkWrite = Annotated[
    ScopeContext, Depends(require("operations.jobwork.order.write", minimum=Scope.ORG))
]
JobWorkIssue = Annotated[
    ScopeContext, Depends(require("operations.jobwork.order.issue", minimum=Scope.ORG))
]
JobWorkReceive = Annotated[
    ScopeContext, Depends(require("operations.jobwork.order.receive", minimum=Scope.ORG))
]
#: Approving commits the business to sending material out; closing settles it. Both are the
#: sign-off steps, so they share the permission — a person who can only `write` drafts cannot
#: approve their own order or close one with a gap in it.
JobWorkApprove = Annotated[
    ScopeContext, Depends(require("operations.jobwork.order.approve", minimum=Scope.ORG))
]

OptionalKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="Retrying with the same key replays the first result; reusing it for a "
        "different request is a 409.",
    ),
]
RequiredKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        description="Required. Retrying with the same key replays the first result instead of "
        "acting twice; reusing it for a different request is a 409.",
    ),
]


async def _detail(
    session: AsyncSession, ctx: ScopeContext, order_id: uuid.UUID
) -> JobWorkOrderDetailOut:
    order = await selectors.get_order(session, ctx.organization_id, order_id)
    if order is None:
        raise NotFoundProblem("That job-work order is not part of this organization")
    return await selectors.order_detail(session, ctx.organization_id, order, today=domain.ist_today())


# ------------------------------------------------------------------ orders


@router.get(
    "/operations/jobwork/orders",
    response_model=Page[JobWorkOrderOut],
    summary="Job-work orders",
    dependencies=[Entitled],
)
async def list_orders(
    session: SessionDep,
    ctx: JobWorkRead,
    params: PageQuery,
    order_status: Annotated[str | None, Query(alias="status")] = None,
    supplier_id: Annotated[uuid.UUID | None, Query()] = None,
    overdue: Annotated[bool, Query(description="Only orders overdue at the vendor")] = False,
    search: Annotated[str | None, Query(max_length=60)] = None,
) -> Page[JobWorkOrderOut]:
    today = domain.ist_today()
    stmt = selectors.orders_stmt(
        ctx,
        status=order_status,
        supplier_id=supplier_id,
        overdue_on=today if overdue else None,
        search=search,
    )
    rows, total = await paginate(session, stmt, params)
    totals = await selectors.order_totals(session, ctx.organization_id, [r.id for r in rows])
    names = await selectors.supplier_names(
        session, ctx.organization_id, [r.supplier_id for r in rows]
    )
    out = [
        selectors.order_out(
            row,
            supplier_name=names.get(row.supplier_id, ""),
            totals=totals.get(row.id, (0, 0, 0)),
            today=today,
        )
        for row in rows
    ]
    return Page.build(out, total=total, params=params)


@router.post(
    "/operations/jobwork/orders",
    response_model=JobWorkOrderDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Draft a job-work order — nothing moves until it is approved and issued",
    dependencies=[Entitled],
)
async def create_order(
    payload: JobWorkOrderIn,
    session: SessionDep,
    ctx: JobWorkWrite,
    request: Request,
    response: Response,
    idempotency_key: RequiredKey,
) -> JobWorkOrderDetailOut:
    order, replayed = await services.create_order(
        session,
        ctx,
        supplier_id=payload.supplier_id,
        process=payload.process,
        due_date=payload.due_date,
        source_warehouse_id=payload.source_warehouse_id,
        receive_location_id=payload.receive_location_id,
        qc_required=payload.qc_required,
        notes=payload.notes,
        materials=payload.materials,
        idempotency_key=idempotency_key,
        request=request,
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    return await _detail(session, ctx, order.id)


@router.get(
    "/operations/jobwork/orders/{order_id}",
    response_model=JobWorkOrderDetailOut,
    summary="One job-work order with its materials, receipts, challans and reconciliation",
    dependencies=[Entitled],
)
async def read_order(
    order_id: uuid.UUID, session: SessionDep, ctx: JobWorkRead
) -> JobWorkOrderDetailOut:
    return await _detail(session, ctx, order_id)


@router.patch(
    "/operations/jobwork/orders/{order_id}",
    response_model=JobWorkOrderDetailOut,
    summary="Edit a draft order's header",
    dependencies=[Entitled],
)
async def update_order(
    order_id: uuid.UUID,
    payload: JobWorkOrderPatch,
    session: SessionDep,
    ctx: JobWorkWrite,
    request: Request,
) -> JobWorkOrderDetailOut:
    fields = payload.model_dump(exclude_unset=True, exclude={"expected_version", "clear_due_date"})
    await services.update_order(
        session,
        ctx,
        order_id,
        expected_version=payload.expected_version,
        fields=fields,
        clear_due_date=payload.clear_due_date,
        request=request,
    )
    return await _detail(session, ctx, order_id)


@router.post(
    "/operations/jobwork/orders/{order_id}:approve",
    response_model=JobWorkOrderDetailOut,
    summary="Approve a draft order (DRAFT -> APPROVED)",
    dependencies=[Entitled],
)
async def approve_order(
    order_id: uuid.UUID,
    session: SessionDep,
    ctx: JobWorkApprove,
    request: Request,
    payload: JobWorkTransitionIn | None = None,
    idempotency_key: OptionalKey = None,
) -> JobWorkOrderDetailOut:
    await services.approve_order(
        session,
        ctx,
        order_id,
        reason=payload.reason if payload else "",
        idempotency_key=idempotency_key,
        request=request,
    )
    return await _detail(session, ctx, order_id)


@router.post(
    "/operations/jobwork/orders/{order_id}:cancel",
    response_model=JobWorkOrderDetailOut,
    summary="Cancel an order that has not been issued yet",
    dependencies=[Entitled],
)
async def cancel_order(
    order_id: uuid.UUID,
    session: SessionDep,
    ctx: JobWorkWrite,
    request: Request,
    payload: JobWorkTransitionIn | None = None,
    idempotency_key: OptionalKey = None,
) -> JobWorkOrderDetailOut:
    await services.cancel_order(
        session,
        ctx,
        order_id,
        reason=payload.reason if payload else "",
        idempotency_key=idempotency_key,
        request=request,
    )
    return await _detail(session, ctx, order_id)


@router.post(
    "/operations/jobwork/orders/{order_id}:issue",
    response_model=JobWorkOrderDetailOut,
    summary="Send the materials to the vendor — moves stock to AT_JOB_WORKER and cuts the challan",
    dependencies=[Entitled],
)
async def issue_order(
    order_id: uuid.UUID,
    session: SessionDep,
    ctx: JobWorkIssue,
    request: Request,
    idempotency_key: RequiredKey,
    payload: JobWorkIssueIn | None = None,
) -> JobWorkOrderDetailOut:
    body = payload or JobWorkIssueIn()
    await services.issue_order(
        session,
        ctx,
        order_id,
        vehicle_no=body.vehicle_no,
        transporter_id=body.transporter_id,
        distance_km=body.distance_km,
        inter_state=body.inter_state,
        note=body.note,
        idempotency_key=idempotency_key,
        request=request,
    )
    return await _detail(session, ctx, order_id)


@router.post(
    "/operations/jobwork/orders/{order_id}:receive",
    response_model=JobWorkOrderDetailOut,
    summary="Take material back from the vendor, in whole or in part",
    dependencies=[Entitled],
)
async def receive_order(
    order_id: uuid.UUID,
    payload: JobWorkReceiveIn,
    session: SessionDep,
    ctx: JobWorkReceive,
    request: Request,
    idempotency_key: RequiredKey,
) -> JobWorkOrderDetailOut:
    await services.receive_order(
        session,
        ctx,
        order_id,
        lines=payload.lines,
        received_on=payload.received_on,
        note=payload.note,
        idempotency_key=idempotency_key,
        request=request,
    )
    return await _detail(session, ctx, order_id)


@router.get(
    "/operations/jobwork/orders/{order_id}/reconciliation",
    response_model=ReconciliationOut,
    summary="Sent / received / rejected / shortage, and whether documents and ledger agree",
    dependencies=[Entitled],
)
async def read_reconciliation(
    order_id: uuid.UUID, session: SessionDep, ctx: JobWorkRead
) -> ReconciliationOut:
    order = await selectors.get_order(session, ctx.organization_id, order_id)
    if order is None:
        raise NotFoundProblem("That job-work order is not part of this organization")
    materials = await selectors.materials_for(session, ctx.organization_id, order.id)
    result, _ledger = await selectors.reconciliation(session, ctx.organization_id, order, materials)
    return selectors.reconciliation_out(
        order, materials, result, skus_by_item={str(m.item_id): m.sku for m in materials}
    )


@router.post(
    "/operations/jobwork/orders/{order_id}:close",
    response_model=JobWorkOrderDetailOut,
    summary="Close an order — refused unless the reconciliation balances",
    dependencies=[Entitled],
)
async def close_order(
    order_id: uuid.UUID,
    session: SessionDep,
    ctx: JobWorkApprove,
    request: Request,
    payload: JobWorkTransitionIn | None = None,
    idempotency_key: OptionalKey = None,
) -> JobWorkOrderDetailOut:
    await services.close_order(
        session,
        ctx,
        order_id,
        reason=payload.reason if payload else "",
        idempotency_key=idempotency_key,
        request=request,
    )
    return await _detail(session, ctx, order_id)


# ------------------------------------------------------------------ challans


@router.get(
    "/operations/jobwork/challans/{challan_id}",
    response_model=ChallanOut,
    summary="One delivery challan (the print view reads this)",
    dependencies=[Entitled],
)
async def read_challan(challan_id: uuid.UUID, session: SessionDep, ctx: JobWorkRead) -> ChallanOut:
    challan = await selectors.get_challan(session, ctx.organization_id, challan_id)
    if challan is None:
        raise NotFoundProblem("That challan is not part of this organization")
    order = await selectors.get_order(session, ctx.organization_id, challan.order_id)
    return selectors.challan_out(challan, order_number=order.number if order else "")


@router.post(
    "/operations/jobwork/challans/{challan_id}:request-eway",
    response_model=ChallanOut,
    summary="Give the transport facts and ask finance to consider an e-way bill for a challan",
    dependencies=[Entitled],
)
async def request_challan_eway(
    challan_id: uuid.UUID,
    payload: ChallanEwayIn,
    session: SessionDep,
    ctx: JobWorkIssue,
    request: Request,
) -> ChallanOut:
    challan = await services.request_eway(
        session,
        ctx,
        challan_id,
        distance_km=payload.distance_km,
        vehicle_no=payload.vehicle_no,
        transporter_id=payload.transporter_id,
        request=request,
    )
    order = await selectors.get_order(session, ctx.organization_id, challan.order_id)
    return selectors.challan_out(challan, order_number=order.number if order else "")
