"""HTTP surface for manufacturing, mounted behind the gateway's `/api/v1/operations` prefix.

Every route declares its permission, floored at ORG (BOMs and production orders are shared
operational documents — there is no meaningful OWN reading), and is guarded by
`require_product("operations")`. Reads go through `selectors`, writes through `services`; a
router never builds its own query. A row that is not the caller's organization's is a 404, never
a 403. Every mutating route accepts an `Idempotency-Key`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.quantity import QuantityError
from app.manufacturing import bom_services, common, domain, selectors, services
from app.manufacturing.models import ProductionOrder
from app.manufacturing.schemas import (
    BomComponentIn,
    BomComponentPatch,
    BomDetailOut,
    BomIn,
    BomOut,
    BomPatch,
    BomSubstituteIn,
    BomVersionDetailOut,
    BomVersionIn,
    BomVersionOut,
    BomVersionPatch,
    CompleteIn,
    ExplosionOut,
    IssueIn,
    MaterialSubstituteIn,
    OutputIn,
    ProductionActionOut,
    ProductionCancelIn,
    ProductionGenealogyOut,
    ProductionOrderDetailOut,
    ProductionOrderIn,
    ProductionOrderOut,
    ProductionOrderPatch,
    ProductionOutputOut,
    ProductionWastageOut,
    ReserveIn,
    ReturnIn,
    ShortageOut,
    VarianceOut,
    WastageIn,
    WastageThresholdIn,
    WastageThresholdOut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem, ValidationProblem
from platform_core.pagination import Page, PageQuery, paginate

router = APIRouter(tags=["manufacturing"], prefix="/operations/manufacturing")

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

BomRead = Annotated[
    ScopeContext, Depends(require("operations.manufacturing.bom.read", minimum=Scope.ORG))
]
BomWrite = Annotated[
    ScopeContext, Depends(require("operations.manufacturing.bom.write", minimum=Scope.ORG))
]
#: Publishing freezes a recipe that every later order is planned from — dangerous in the catalogue.
BomPublish = Annotated[
    ScopeContext, Depends(require("operations.manufacturing.bom.publish", minimum=Scope.ORG))
]
OrderRead = Annotated[
    ScopeContext,
    Depends(require("operations.manufacturing.production_order.read", minimum=Scope.ORG)),
]
OrderWrite = Annotated[
    ScopeContext,
    Depends(require("operations.manufacturing.production_order.write", minimum=Scope.ORG)),
]
#: Approving commits the plan (and later its stock) — dangerous in the catalogue.
OrderApprove = Annotated[
    ScopeContext,
    Depends(require("operations.manufacturing.production_order.approve", minimum=Scope.ORG)),
]
#: Reserve, issue, return, wastage: everything that moves material for an order.
OrderIssue = Annotated[
    ScopeContext,
    Depends(require("operations.manufacturing.production_order.issue", minimum=Scope.ORG)),
]
#: Output into finished goods, and closing the order.
OrderComplete = Annotated[
    ScopeContext,
    Depends(require("operations.manufacturing.production_order.complete", minimum=Scope.ORG)),
]


# ------------------------------------------------------------------ helpers


async def _bom_detail(session: AsyncSession, org_id: uuid.UUID, bom_id: uuid.UUID) -> BomDetailOut:
    bom = await bom_services.get_bom_or_404(session, org_id, bom_id)
    return await selectors.bom_detail_out(session, org_id, bom, today=common.today())


async def _version_detail(
    session: AsyncSession, org_id: uuid.UUID, bom_id: uuid.UUID, version_id: uuid.UUID
) -> BomVersionDetailOut:
    await bom_services.get_bom_or_404(session, org_id, bom_id)
    version = await bom_services.get_version_or_404(session, org_id, bom_id, version_id)
    return await selectors.version_detail_out(session, org_id, version)


async def _order_detail(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> ProductionOrderDetailOut:
    order = await selectors.get_order(session, org_id, order_id)
    if order is None:
        raise NotFoundProblem("That production order is not part of this organization")
    return await selectors.order_detail_out(session, org_id, order)


async def _action_out(
    session: AsyncSession, org_id: uuid.UUID, result: services.ActionResult
) -> ProductionActionOut:
    detail = await selectors.order_detail_out(session, org_id, result.order)
    items = await selectors.items_by_id(
        session, org_id, {s.material.item_id for s in result.shortages}
    )
    return ProductionActionOut(
        **detail.model_dump(),
        shortages=[
            ShortageOut(
                material_id=str(s.material.id),
                item_id=str(s.material.item_id),
                sku=items[s.material.item_id].sku if s.material.item_id in items else "",
                required=_fmt(s.material.required_milli),
                covered=_fmt(s.covered_milli),
                short=_fmt(s.short_milli),
            )
            for s in result.shortages
        ],
        replayed=result.replayed,
    )


def _fmt(milli: int) -> str:
    from app.inventory.quantity import format_qty

    return format_qty(milli)


async def _get_order_or_404(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> ProductionOrder:
    order = await selectors.get_order(session, org_id, order_id)
    if order is None:
        raise NotFoundProblem("That production order is not part of this organization")
    return order


# ------------------------------------------------------------------ BOMs


@router.get(
    "/boms",
    response_model=Page[BomOut],
    summary="Bills of materials",
    dependencies=[Entitled],
)
async def list_boms(
    session: SessionDep,
    ctx: BomRead,
    params: PageQuery,
    q: Annotated[str | None, Query(max_length=100)] = None,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> Page[BomOut]:
    stmt = selectors.boms_stmt(ctx, q=q, item_id=item_id, is_active=is_active)
    rows, total = await paginate(session, stmt, params)
    out = await selectors.bom_outs(session, ctx.organization_id, rows, today=common.today())
    return Page.build(out, total=total, params=params)


@router.post(
    "/boms",
    response_model=BomDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a BOM with its first draft version",
    dependencies=[Entitled],
)
async def create_bom(
    payload: BomIn,
    session: SessionDep,
    ctx: BomWrite,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> BomDetailOut:
    bom, replayed = await bom_services.create_bom(
        session, ctx, payload, key=idempotency_key, request=request
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    return await selectors.bom_detail_out(session, ctx.organization_id, bom, today=common.today())


@router.get(
    "/boms/{bom_id}",
    response_model=BomDetailOut,
    summary="One BOM and its versions",
    dependencies=[Entitled],
)
async def read_bom(bom_id: uuid.UUID, session: SessionDep, ctx: BomRead) -> BomDetailOut:
    return await _bom_detail(session, ctx.organization_id, bom_id)


@router.patch(
    "/boms/{bom_id}",
    response_model=BomDetailOut,
    summary="Rename, describe, or (de)activate a BOM",
    dependencies=[Entitled],
)
async def update_bom(
    bom_id: uuid.UUID,
    payload: BomPatch,
    session: SessionDep,
    ctx: BomWrite,
    request: Request,
) -> BomDetailOut:
    bom = await bom_services.update_bom(session, ctx, bom_id, payload, request=request)
    return await selectors.bom_detail_out(session, ctx.organization_id, bom, today=common.today())


@router.get(
    "/boms/{bom_id}/versions",
    response_model=list[BomVersionOut],
    summary="A BOM's versions, newest first",
    dependencies=[Entitled],
)
async def list_versions(
    bom_id: uuid.UUID, session: SessionDep, ctx: BomRead
) -> list[BomVersionOut]:
    detail = await _bom_detail(session, ctx.organization_id, bom_id)
    return detail.versions


@router.post(
    "/boms/{bom_id}/versions",
    response_model=BomVersionDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new draft version, optionally copying an earlier one",
    dependencies=[Entitled],
)
async def create_version(
    bom_id: uuid.UUID,
    payload: BomVersionIn,
    session: SessionDep,
    ctx: BomWrite,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> BomVersionDetailOut:
    version, replayed = await bom_services.create_version(
        session, ctx, bom_id, payload, key=idempotency_key, request=request
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    return await selectors.version_detail_out(session, ctx.organization_id, version)


@router.get(
    "/boms/{bom_id}/versions/{version_id}",
    response_model=BomVersionDetailOut,
    summary="One version with its components and substitutes",
    dependencies=[Entitled],
)
async def read_version(
    bom_id: uuid.UUID, version_id: uuid.UUID, session: SessionDep, ctx: BomRead
) -> BomVersionDetailOut:
    return await _version_detail(session, ctx.organization_id, bom_id, version_id)


@router.patch(
    "/boms/{bom_id}/versions/{version_id}",
    response_model=BomVersionDetailOut,
    summary="Edit a draft version's batch size, dates and notes (a published one is immutable)",
    dependencies=[Entitled],
)
async def update_version(
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: BomVersionPatch,
    session: SessionDep,
    ctx: BomWrite,
    request: Request,
) -> BomVersionDetailOut:
    version = await bom_services.update_version(
        session, ctx, bom_id, version_id, payload, request=request
    )
    return await selectors.version_detail_out(session, ctx.organization_id, version)


@router.delete(
    "/boms/{bom_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Discard a draft version",
    dependencies=[Entitled],
)
async def discard_version(
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    session: SessionDep,
    ctx: BomWrite,
    request: Request,
) -> Response:
    await bom_services.discard_version(session, ctx, bom_id, version_id, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/boms/{bom_id}/versions/{version_id}/components",
    response_model=BomVersionDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a component line to a draft version (refuses a BOM that would contain itself)",
    dependencies=[Entitled],
)
async def add_component(
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: BomComponentIn,
    session: SessionDep,
    ctx: BomWrite,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> BomVersionDetailOut:
    await bom_services.add_component(
        session, ctx, bom_id, version_id, payload, key=idempotency_key, request=request
    )
    return await _version_detail(session, ctx.organization_id, bom_id, version_id)


@router.patch(
    "/boms/{bom_id}/versions/{version_id}/components/{component_id}",
    response_model=BomVersionDetailOut,
    summary="Edit a component line of a draft version",
    dependencies=[Entitled],
)
async def update_component(
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    component_id: uuid.UUID,
    payload: BomComponentPatch,
    session: SessionDep,
    ctx: BomWrite,
    request: Request,
) -> BomVersionDetailOut:
    await bom_services.update_component(
        session, ctx, bom_id, version_id, component_id, payload, request=request
    )
    return await _version_detail(session, ctx.organization_id, bom_id, version_id)


@router.delete(
    "/boms/{bom_id}/versions/{version_id}/components/{component_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a component line from a draft version",
    dependencies=[Entitled],
)
async def remove_component(
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    component_id: uuid.UUID,
    session: SessionDep,
    ctx: BomWrite,
    request: Request,
) -> Response:
    await bom_services.remove_component(
        session, ctx, bom_id, version_id, component_id, request=request
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/boms/{bom_id}/versions/{version_id}/components/{component_id}/substitutes",
    response_model=BomVersionDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Declare a substitute for a component line of a draft version",
    dependencies=[Entitled],
)
async def add_substitute(
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    component_id: uuid.UUID,
    payload: BomSubstituteIn,
    session: SessionDep,
    ctx: BomWrite,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> BomVersionDetailOut:
    await bom_services.add_substitute(
        session,
        ctx,
        bom_id,
        version_id,
        component_id,
        payload,
        key=idempotency_key,
        request=request,
    )
    return await _version_detail(session, ctx.organization_id, bom_id, version_id)


@router.delete(
    "/boms/{bom_id}/versions/{version_id}/components/{component_id}/substitutes/{substitute_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a substitute from a component line of a draft version",
    dependencies=[Entitled],
)
async def remove_substitute(
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    component_id: uuid.UUID,
    substitute_id: uuid.UUID,
    session: SessionDep,
    ctx: BomWrite,
    request: Request,
) -> Response:
    await bom_services.remove_substitute(
        session, ctx, bom_id, version_id, component_id, substitute_id, request=request
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/boms/{bom_id}/versions/{version_id}/publish",
    response_model=BomVersionDetailOut,
    summary="Publish a draft version — it is immutable from then on",
    dependencies=[Entitled],
)
async def publish_version(
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    session: SessionDep,
    ctx: BomPublish,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> BomVersionDetailOut:
    version, _ = await bom_services.publish_version(
        session, ctx, bom_id, version_id, key=idempotency_key, request=request
    )
    return await selectors.version_detail_out(session, ctx.organization_id, version)


@router.post(
    "/boms/{bom_id}/versions/{version_id}/retire",
    response_model=BomVersionDetailOut,
    summary="Retire a published version so no new order is planned from it",
    dependencies=[Entitled],
)
async def retire_version(
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    session: SessionDep,
    ctx: BomPublish,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> BomVersionDetailOut:
    version = await bom_services.retire_version(
        session, ctx, bom_id, version_id, key=idempotency_key, request=request
    )
    return await selectors.version_detail_out(session, ctx.organization_id, version)


@router.get(
    "/boms/{bom_id}/versions/{version_id}/explosion",
    response_model=ExplosionOut,
    summary="Multi-level requirement for a quantity of the BOM's item",
    dependencies=[Entitled],
)
async def explode_version(
    bom_id: uuid.UUID,
    version_id: uuid.UUID,
    session: SessionDep,
    ctx: BomRead,
    quantity: Annotated[str, Query(pattern=r"^\d+(\.\d{1,3})?$")] = "1",
) -> ExplosionOut:
    org_id = ctx.organization_id
    bom = await bom_services.get_bom_or_404(session, org_id, bom_id)
    version = await bom_services.get_version_or_404(session, org_id, bom_id, version_id)
    item = await common.require_item(session, org_id, bom.item_id)
    try:
        return await selectors.explosion_out(
            session,
            org_id,
            bom,
            version,
            common.qty(quantity, item.uom),
            on=common.today(),
        )
    except (ValueError, QuantityError) as exc:
        raise ValidationProblem(str(exc)) from exc


# ------------------------------------------------------------------ production orders


@router.get(
    "/orders",
    response_model=Page[ProductionOrderOut],
    summary="Production orders",
    dependencies=[Entitled],
)
async def list_orders(
    session: SessionDep,
    ctx: OrderRead,
    params: PageQuery,
    order_status: Annotated[str | None, Query(alias="status")] = None,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=40)] = None,
) -> Page[ProductionOrderOut]:
    if order_status is not None and order_status not in domain.PRODUCTION_ORDER_STATUSES:
        raise ValidationProblem(f"{order_status!r} is not a production order status")
    stmt = selectors.orders_stmt(ctx, order_status=order_status, item_id=item_id, q=q)
    rows, total = await paginate(session, stmt, params)
    out = await selectors.order_outs(session, ctx.organization_id, rows)
    return Page.build(out, total=total, params=params)


@router.post(
    "/orders",
    response_model=ProductionOrderDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Plan a production order from a BOM (pins the BOM version in force)",
    dependencies=[Entitled],
)
async def create_order(
    payload: ProductionOrderIn,
    session: SessionDep,
    ctx: OrderWrite,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> ProductionOrderDetailOut:
    order, replayed = await services.create_order(
        session, ctx, payload, key=idempotency_key, request=request
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    return await selectors.order_detail_out(session, ctx.organization_id, order)


@router.get(
    "/orders/{order_id}",
    response_model=ProductionOrderDetailOut,
    summary="One production order with materials, picks, outputs, wastage and variance",
    dependencies=[Entitled],
)
async def read_order(
    order_id: uuid.UUID, session: SessionDep, ctx: OrderRead
) -> ProductionOrderDetailOut:
    return await _order_detail(session, ctx.organization_id, order_id)


@router.patch(
    "/orders/{order_id}",
    response_model=ProductionOrderDetailOut,
    summary="Edit a draft order",
    dependencies=[Entitled],
)
async def update_order(
    order_id: uuid.UUID,
    payload: ProductionOrderPatch,
    session: SessionDep,
    ctx: OrderWrite,
    request: Request,
) -> ProductionOrderDetailOut:
    order = await services.update_order(session, ctx, order_id, payload, request=request)
    return await selectors.order_detail_out(session, ctx.organization_id, order)


@router.post(
    "/orders/{order_id}/materials/{material_id}/substitute",
    response_model=ProductionOrderDetailOut,
    summary="Use a declared substitute for a material (or go back to the BOM's own item)",
    dependencies=[Entitled],
)
async def substitute_material(
    order_id: uuid.UUID,
    material_id: uuid.UUID,
    payload: MaterialSubstituteIn,
    session: SessionDep,
    ctx: OrderWrite,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> ProductionOrderDetailOut:
    order = await services.set_material_substitute(
        session, ctx, order_id, material_id, payload, key=idempotency_key, request=request
    )
    return await selectors.order_detail_out(session, ctx.organization_id, order)


@router.post(
    "/orders/{order_id}/approve",
    response_model=ProductionActionOut,
    summary="Approve a draft order (DRAFT -> APPROVED)",
    dependencies=[Entitled],
)
async def approve_order(
    order_id: uuid.UUID,
    session: SessionDep,
    ctx: OrderApprove,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> ProductionActionOut:
    result = await services.approve_order(
        session, ctx, order_id, key=idempotency_key, request=request
    )
    return await _action_out(session, ctx.organization_id, result)


@router.post(
    "/orders/{order_id}/start",
    response_model=ProductionActionOut,
    summary="Start production (APPROVED -> IN_PROGRESS); issuing material starts it too",
    dependencies=[Entitled],
)
async def start_order(
    order_id: uuid.UUID,
    session: SessionDep,
    ctx: OrderIssue,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> ProductionActionOut:
    result = await services.start_order(
        session, ctx, order_id, key=idempotency_key, request=request
    )
    return await _action_out(session, ctx.organization_id, result)


@router.post(
    "/orders/{order_id}/cancel",
    response_model=ProductionActionOut,
    summary="Cancel an order — releases its reservations; refused while material is in WIP",
    dependencies=[Entitled],
)
async def cancel_order(
    order_id: uuid.UUID,
    payload: ProductionCancelIn,
    session: SessionDep,
    ctx: OrderWrite,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> ProductionActionOut:
    result = await services.cancel_order(
        session, ctx, order_id, reason=payload.reason, key=idempotency_key, request=request
    )
    return await _action_out(session, ctx.organization_id, result)


@router.post(
    "/orders/{order_id}/reserve",
    response_model=ProductionActionOut,
    summary="Reserve the order's material from available stock; shortages raise an alert event",
    dependencies=[Entitled],
)
async def reserve_materials(
    order_id: uuid.UUID,
    payload: ReserveIn,
    session: SessionDep,
    ctx: OrderIssue,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> ProductionActionOut:
    result = await services.reserve_materials(
        session, ctx, order_id, payload, key=idempotency_key, request=request
    )
    return await _action_out(session, ctx.organization_id, result)


@router.post(
    "/orders/{order_id}/issue",
    response_model=ProductionActionOut,
    summary="Issue material to WIP (AVAILABLE -> WIP), optionally picking lots and rolls",
    dependencies=[Entitled],
)
async def issue_materials(
    order_id: uuid.UUID,
    payload: IssueIn,
    session: SessionDep,
    ctx: OrderIssue,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> ProductionActionOut:
    result = await services.issue_materials(
        session, ctx, order_id, payload, key=idempotency_key, request=request
    )
    return await _action_out(session, ctx.organization_id, result)


@router.post(
    "/orders/{order_id}/returns",
    response_model=ProductionActionOut,
    summary="Return unused WIP material to stock (WIP -> AVAILABLE)",
    dependencies=[Entitled],
)
async def return_materials(
    order_id: uuid.UUID,
    payload: ReturnIn,
    session: SessionDep,
    ctx: OrderIssue,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> ProductionActionOut:
    result = await services.return_materials(
        session, ctx, order_id, payload, key=idempotency_key, request=request
    )
    return await _action_out(session, ctx.organization_id, result)


@router.post(
    "/orders/{order_id}/outputs",
    response_model=ProductionActionOut,
    summary="Record good output — consumes WIP at standard and receives finished goods",
    dependencies=[Entitled],
)
async def record_output(
    order_id: uuid.UUID,
    payload: OutputIn,
    session: SessionDep,
    ctx: OrderComplete,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> ProductionActionOut:
    result = await services.record_output(
        session, ctx, order_id, payload, key=idempotency_key, request=request
    )
    return await _action_out(session, ctx.organization_id, result)


@router.post(
    "/orders/{order_id}/wastage",
    response_model=ProductionActionOut,
    summary="Write off material in WIP as wastage of a type; a threshold crossing raises an alert",
    dependencies=[Entitled],
)
async def record_wastage(
    order_id: uuid.UUID,
    payload: WastageIn,
    session: SessionDep,
    ctx: OrderIssue,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> ProductionActionOut:
    result = await services.record_wastage(
        session, ctx, order_id, payload, key=idempotency_key, request=request
    )
    return await _action_out(session, ctx.organization_id, result)


@router.post(
    "/orders/{order_id}/complete",
    response_model=ProductionActionOut,
    summary="Complete an order (IN_PROGRESS -> COMPLETED)",
    dependencies=[Entitled],
)
async def complete_order(
    order_id: uuid.UUID,
    payload: CompleteIn,
    session: SessionDep,
    ctx: OrderComplete,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> ProductionActionOut:
    result = await services.complete_order(
        session, ctx, order_id, payload, key=idempotency_key, request=request
    )
    return await _action_out(session, ctx.organization_id, result)


@router.get(
    "/orders/{order_id}/outputs",
    response_model=list[ProductionOutputOut],
    summary="An order's outputs",
    dependencies=[Entitled],
)
async def list_outputs(
    order_id: uuid.UUID, session: SessionDep, ctx: OrderRead
) -> list[ProductionOutputOut]:
    await _get_order_or_404(session, ctx.organization_id, order_id)
    return [
        selectors.output_out(o)
        for o in await selectors.outputs_of(session, ctx.organization_id, order_id)
    ]


@router.get(
    "/orders/{order_id}/wastage",
    response_model=list[ProductionWastageOut],
    summary="An order's wastage, by type",
    dependencies=[Entitled],
)
async def list_wastage(
    order_id: uuid.UUID, session: SessionDep, ctx: OrderRead
) -> list[ProductionWastageOut]:
    await _get_order_or_404(session, ctx.organization_id, order_id)
    return [
        selectors.wastage_out(w)
        for w in await selectors.wastage_of(session, ctx.organization_id, order_id)
    ]


@router.get(
    "/orders/{order_id}/genealogy",
    response_model=list[ProductionGenealogyOut],
    summary="Which input lots and rolls went into which output",
    dependencies=[Entitled],
)
async def list_genealogy(
    order_id: uuid.UUID, session: SessionDep, ctx: OrderRead
) -> list[ProductionGenealogyOut]:
    await _get_order_or_404(session, ctx.organization_id, order_id)
    return [
        selectors.genealogy_out(g)
        for g in await selectors.genealogy_of(session, ctx.organization_id, order_id)
    ]


@router.get(
    "/orders/{order_id}/variance",
    response_model=VarianceOut,
    summary="Standard vs actual consumption for what has been produced",
    dependencies=[Entitled],
)
async def read_variance(order_id: uuid.UUID, session: SessionDep, ctx: OrderRead) -> VarianceOut:
    return (await _order_detail(session, ctx.organization_id, order_id)).variance


# ------------------------------------------------------------------ wastage thresholds


@router.get(
    "/wastage-thresholds",
    response_model=list[WastageThresholdOut],
    summary="Configured wastage thresholds",
    dependencies=[Entitled],
)
async def list_thresholds(session: SessionDep, ctx: OrderRead) -> list[WastageThresholdOut]:
    rows = await selectors.thresholds(session, ctx.organization_id)
    items = await selectors.items_by_id(
        session, ctx.organization_id, [r.item_id for r in rows if r.item_id]
    )
    return [selectors.threshold_out(r, items) for r in rows]


@router.post(
    "/wastage-thresholds",
    response_model=WastageThresholdOut,
    summary="Set the wastage threshold for a product and/or wastage type",
    dependencies=[Entitled],
)
async def set_threshold(
    payload: WastageThresholdIn, session: SessionDep, ctx: BomWrite, request: Request
) -> WastageThresholdOut:
    row = await services.upsert_threshold(session, ctx, payload, request=request)
    items = await selectors.items_by_id(
        session, ctx.organization_id, [row.item_id] if row.item_id else []
    )
    return selectors.threshold_out(row, items)


@router.delete(
    "/wastage-thresholds/{threshold_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a wastage threshold",
    dependencies=[Entitled],
)
async def remove_threshold(
    threshold_id: uuid.UUID, session: SessionDep, ctx: BomWrite, request: Request
) -> Response:
    await services.delete_threshold(session, ctx, threshold_id, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
