"""Writes for production orders.

**Stock moves only through the kernel.** Nothing here touches `inventory_balances`,
`inventory_ledger` or `inventory_reservations` directly; every quantity that physically moves is
a `ledger.post_move`, `ledger.reserve` or `ledger.release` call in the request's transaction,
so over-issue is impossible under concurrency by the same row locks every other stock write uses.

The life of material, in kernel terms (status in capitals):

    reserve   AVAILABLE bucket gains a reservation (nothing moves)
    issue     relocation  AVAILABLE(-q, draws the reservation)  ->  WIP(+q), same lot/roll
    output    issue       WIP(-consumed)            receipt  FG(+good) at the output location
    wastage   issue       WIP(-lost)   (reason production_waste, by type)
    return    relocation  WIP(-q)  ->  AVAILABLE(+q) at the chosen location

**Lock order.** The order row first (so two actions on one order queue), then its material and
pick rows, then whatever the kernel locks (balances, reservations). Two *different* orders
competing for the same stock meet only in the kernel, on the balance row.

**Arithmetic is the domain's.** Required quantities and the cumulative standard consumption for
a running total of good output come from `domain.required_milli`/`theoretical_milli`: integers,
one rounding, always up.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.inventory import ledger
from app.inventory.ledger import InsufficientStock, MoveLine
from app.inventory.quantity import format_qty, is_discrete
from app.manufacturing import common, domain, selectors
from app.manufacturing.models import (
    Bom,
    BomSubstitute,
    ProductionGenealogy,
    ProductionMaterial,
    ProductionOrder,
    ProductionOutput,
    ProductionPick,
    ProductionWastage,
    WastageThreshold,
)
from app.manufacturing.schemas import (
    CompleteIn,
    IssueIn,
    MaterialSubstituteIn,
    OutputIn,
    ProductionOrderIn,
    ProductionOrderPatch,
    ReserveIn,
    ReturnIn,
    WastageIn,
    WastageThresholdIn,
)
from app.models import InventoryBalance, InventoryReservation
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem

_REF = "production_order"


class InsufficientWip(ConflictProblem):
    """Output or wastage asked for more material than the order has in WIP. Carries the numbers,
    like `InsufficientStock`, so the screen can say which material and by how much."""

    problem_type = "insufficient-wip"
    title = "Not enough material in WIP"


@dataclass(slots=True)
class Shortage:
    material: ProductionMaterial
    covered_milli: int

    @property
    def short_milli(self) -> int:
        return max(self.material.required_milli - self.covered_milli, 0)


@dataclass(slots=True)
class ActionResult:
    order: ProductionOrder
    replayed: bool = False
    shortages: list[Shortage] = field(default_factory=list)


# ------------------------------------------------------------------ locking and guards


async def lock_order(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> ProductionOrder:
    order = await selectors.get_order(session, org_id, order_id, for_update=True)
    if order is None:
        raise NotFoundProblem("That production order is not part of this organization")
    return order


def _require_status(order: ProductionOrder, *allowed: str, doing: str) -> None:
    if order.status not in allowed:
        raise ConflictProblem(
            f"Cannot {doing}: order {order.number} is {order.status} (needs {' or '.join(allowed)})"
        )


def _transition(order: ProductionOrder, ctx: ScopeContext, to: str) -> str:
    if not domain.can_transition_production_order(order.status, to):
        raise ConflictProblem(f"Order {order.number} cannot move {order.status} -> {to}")
    before = order.status
    order.status = to
    order.updated_by = common.user_ref(ctx)
    order.updated_at = datetime.now(UTC)
    return before


def _key(order: ProductionOrder, op: str, key: str | None, n: int = 0) -> str | None:
    """The kernel idempotency key for the n-th move of one HTTP request, when it has one."""
    if key is None:
        return None
    return f"mo:{order.id}:{op}:{key}:{n}"[:128]


def _materials_by_id(materials: list[ProductionMaterial]) -> dict[uuid.UUID, ProductionMaterial]:
    return {m.id: m for m in materials}


async def _open_reservation_milli(
    session: AsyncSession, org_id: uuid.UUID, reservation_id: uuid.UUID
) -> int:
    row = (
        await session.execute(
            select(
                InventoryReservation.status,
                InventoryReservation.quantity_milli,
                InventoryReservation.consumed_milli,
                InventoryReservation.released_milli,
                InventoryReservation.transferred_milli,
            ).where(
                InventoryReservation.organization_id == org_id,
                InventoryReservation.id == reservation_id,
            )
        )
    ).one_or_none()
    if row is None or row.status != "active":
        return 0
    return int(
        max(row.quantity_milli - row.consumed_milli - row.released_milli - row.transferred_milli, 0)
    )


async def _sync_reservations(
    session: AsyncSession,
    org_id: uuid.UUID,
    picks: list[ProductionPick],
    materials: dict[uuid.UUID, ProductionMaterial],
) -> None:
    """Make each pick's `reserved_milli` equal the kernel's open remainder. A reservation can be
    released from outside (the reservations API), and the order must not keep counting stock the
    kernel no longer holds for it."""
    for pick in picks:
        if pick.reservation_id is None:
            continue
        open_milli = await _open_reservation_milli(session, org_id, pick.reservation_id)
        if open_milli != pick.reserved_milli:
            material = materials[pick.material_id]
            material.reserved_milli = max(
                material.reserved_milli + open_milli - pick.reserved_milli, 0
            )
            pick.reserved_milli = open_milli
            if open_milli == 0:
                pick.reservation_id = None


async def _load(
    session: AsyncSession, org_id: uuid.UUID, order: ProductionOrder
) -> tuple[list[ProductionMaterial], list[ProductionPick]]:
    materials = await selectors.materials_of(session, org_id, order.id, for_update=True)
    picks = await selectors.picks_of(session, org_id, order.id, for_update=True)
    await _sync_reservations(session, org_id, picks, _materials_by_id(materials))
    return materials, picks


def _merge(lines: list[MoveLine]) -> list[MoveLine]:
    """One line per bucket (the kernel refuses a bucket twice in one move): sum those that share
    one. Two lines that each draw a *different* reservation cannot be summed."""
    merged: dict[tuple[str, ...], MoveLine] = {}
    for line in lines:
        k = line.bucket()
        have = merged.get(k)
        if have is None:
            merged[k] = line
            continue
        if have.reservation_id != line.reservation_id:
            raise ValidationProblem(
                "Two lines draw the same stock on different reservations — issue them separately"
            )
        merged[k] = MoveLine(
            item_id=line.item_id,
            location_id=line.location_id,
            quantity_milli=have.quantity_milli + line.quantity_milli,
            status=line.status,
            lot_id=line.lot_id,
            roll_id=line.roll_id,
            reservation_id=line.reservation_id,
        )
    return list(merged.values())


# ------------------------------------------------------------------ create / edit


async def _next_seq(session: AsyncSession, org_id: uuid.UUID) -> int:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"mo-seq:{org_id}"}
    )
    return (
        await session.execute(
            select(func.coalesce(func.max(ProductionOrder.seq), 0)).where(
                ProductionOrder.organization_id == org_id
            )
        )
    ).scalar_one() + 1


def _material_required(m: ProductionMaterial, planned_milli: int) -> int:
    return domain.required_milli(
        component_milli=m.bom_quantity_milli,
        output_milli=m.output_milli,
        planned_milli=planned_milli,
        scrap_bps=m.scrap_bps,
        ratio_milli=m.ratio_milli,
        discrete=m.discrete,
    )


async def create_order(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: ProductionOrderIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> tuple[ProductionOrder, bool]:
    org_id = ctx.organization_id
    claim = await common.claim(
        session, ctx, key, scope="production_order.create", payload=payload.model_dump(mode="json")
    )
    if claim.replay and claim.result_id is not None:
        replayed = await selectors.get_order(session, org_id, claim.result_id)
        if replayed is not None:
            return replayed, True

    bom: Bom | None
    if payload.bom_id is not None:
        bom = await selectors.get_bom(session, org_id, payload.bom_id)
    else:
        assert payload.item_id is not None
        bom = await selectors.get_bom_for_item(session, org_id, payload.item_id)
    if bom is None:
        raise NotFoundProblem("No BOM found for that request in this organization")
    if payload.item_id is not None and payload.item_id != bom.item_id:
        raise ValidationProblem("item_id is not the item that BOM produces")
    if not bom.is_active:
        raise ConflictProblem(f"BOM {bom.code} is inactive")
    item = await common.require_item(session, org_id, bom.item_id)
    planned = common.qty(payload.quantity, item.uom)
    await common.require_location(session, org_id, payload.output_location_id)
    if payload.wip_location_id is not None:
        await common.require_location(session, org_id, payload.wip_location_id)
    common.check_window(payload.planned_start, payload.due_date, what="due")
    resolved_on = payload.planned_start or common.today()

    versions = (await selectors.versions_of(session, org_id, [bom.id]))[bom.id]
    if payload.bom_version_id is not None:
        version = next((v for v in versions if v.id == payload.bom_version_id), None)
        if version is None:
            raise NotFoundProblem("That BOM version is not part of this BOM")
        if version.status != "PUBLISHED":
            raise ConflictProblem(
                f"Version {version.version_no} is {version.status}; only a published version "
                "can be produced from"
            )
    else:
        version = selectors.current_version(versions, resolved_on)
        if version is None:
            raise ConflictProblem(
                f"BOM {bom.code} has no published version in force on {resolved_on.isoformat()}"
            )

    components = [
        c
        for c in (await selectors.components_of(session, org_id, [version.id]))[version.id]
        if domain.in_window(resolved_on, c.effective_from, c.effective_to)
    ]
    if not components:
        raise ConflictProblem(
            f"Version {version.version_no} has no component in force on {resolved_on.isoformat()}"
        )

    seq = await _next_seq(session, org_id)
    order = ProductionOrder(
        organization_id=org_id,
        seq=seq,
        number=f"MO-{seq:05d}",
        bom_id=bom.id,
        bom_version_id=version.id,
        item_id=item.id,
        status="DRAFT",
        planned_quantity_milli=planned,
        produced_quantity_milli=0,
        output_location_id=payload.output_location_id,
        wip_location_id=payload.wip_location_id,
        planned_start=payload.planned_start,
        due_date=payload.due_date,
        resolved_on=resolved_on,
        notes=payload.notes,
        created_by=common.user_ref(ctx),
        updated_by=common.user_ref(ctx),
    )
    session.add(order)
    await session.flush()

    items = await selectors.items_by_id(session, org_id, {c.component_item_id for c in components})
    for line_no, comp in enumerate(components, start=1):
        facts = items.get(comp.component_item_id)
        if facts is None or facts.deleted:
            raise ConflictProblem(
                f"Component on line {comp.line_no} refers to an item that no longer exists"
            )
        material = ProductionMaterial(
            organization_id=org_id,
            production_order_id=order.id,
            bom_component_id=comp.id,
            line_no=line_no,
            component_item_id=comp.component_item_id,
            item_id=comp.component_item_id,
            bom_quantity_milli=comp.quantity_milli,
            output_milli=version.output_quantity_milli,
            scrap_bps=comp.scrap_bps,
            ratio_milli=1000,
            discrete=is_discrete(facts.uom),
            required_milli=0,
            reserved_milli=0,
            issued_milli=0,
            consumed_milli=0,
            returned_milli=0,
            wasted_milli=0,
            threshold_alerted=False,
            created_by=common.user_ref(ctx),
            updated_by=common.user_ref(ctx),
        )
        material.required_milli = _material_required(material, planned)
        session.add(material)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="production_order.created",
        entity="production_order",
        entity_id=order.id,
        after={
            "number": order.number,
            "bom_id": str(bom.id),
            "bom_version_id": str(version.id),
            "version_no": version.version_no,
            "item_id": str(item.id),
            "planned_quantity": format_qty(planned),
            "materials": len(components),
        },
        request=request,
    )
    claim.done(order.id)
    return order, False


async def update_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    payload: ProductionOrderPatch,
    *,
    request: Request | None = None,
) -> ProductionOrder:
    org_id = ctx.organization_id
    order = await lock_order(session, org_id, order_id)
    _require_status(order, "DRAFT", doing="edit it")
    before = {
        "planned_quantity": format_qty(order.planned_quantity_milli),
        "output_location_id": str(order.output_location_id),
        "wip_location_id": str(order.wip_location_id) if order.wip_location_id else None,
        "planned_start": order.planned_start,
        "due_date": order.due_date,
        "notes": order.notes,
    }
    fields = payload.model_fields_set
    if "quantity" in fields and payload.quantity is not None:
        item = await common.require_item(session, org_id, order.item_id)
        order.planned_quantity_milli = common.qty(payload.quantity, item.uom)
        for material in await selectors.materials_of(session, org_id, order.id, for_update=True):
            material.required_milli = _material_required(material, order.planned_quantity_milli)
    if "output_location_id" in fields and payload.output_location_id is not None:
        await common.require_location(session, org_id, payload.output_location_id)
        order.output_location_id = payload.output_location_id
    if "wip_location_id" in fields:
        if payload.wip_location_id is not None:
            await common.require_location(session, org_id, payload.wip_location_id)
        order.wip_location_id = payload.wip_location_id
    if "planned_start" in fields:
        order.planned_start = payload.planned_start
    if "due_date" in fields:
        order.due_date = payload.due_date
    if "notes" in fields and payload.notes is not None:
        order.notes = payload.notes
    common.check_window(order.planned_start, order.due_date, what="due")
    order.updated_by = common.user_ref(ctx)
    order.updated_at = datetime.now(UTC)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="production_order.updated",
        entity="production_order",
        entity_id=order.id,
        before=before,
        after={
            "planned_quantity": format_qty(order.planned_quantity_milli),
            "output_location_id": str(order.output_location_id),
            "wip_location_id": str(order.wip_location_id) if order.wip_location_id else None,
            "planned_start": order.planned_start,
            "due_date": order.due_date,
            "notes": order.notes,
        },
        request=request,
    )
    return order


async def set_material_substitute(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    material_id: uuid.UUID,
    payload: MaterialSubstituteIn,
    *,
    request: Request | None = None,
) -> ProductionOrder:
    """Switch a material to one of its BOM line's declared substitutes (or back). Only while
    nothing of the material has been reserved or issued: after that, the stock already moved is
    in the old item's unit and the requirement would stop meaning one thing."""
    org_id = ctx.organization_id
    order = await lock_order(session, org_id, order_id)
    _require_status(order, "DRAFT", "APPROVED", doing="change a material")
    materials = await selectors.materials_of(session, org_id, order.id, for_update=True)
    material = _materials_by_id(materials).get(material_id)
    if material is None:
        raise NotFoundProblem("That material is not part of this production order")
    if material.reserved_milli or material.issued_milli:
        raise ConflictProblem(
            "Material already has stock reserved or issued — return it before substituting"
        )
    before = {"item_id": str(material.item_id), "ratio_milli": material.ratio_milli}
    if (
        payload.substitute_item_id is None
        or payload.substitute_item_id == material.component_item_id
    ):
        material.item_id = material.component_item_id
        material.ratio_milli = 1000
    else:
        sub = (
            await session.execute(
                select(BomSubstitute).where(
                    BomSubstitute.organization_id == org_id,
                    BomSubstitute.bom_component_id == material.bom_component_id,
                    BomSubstitute.substitute_item_id == payload.substitute_item_id,
                )
            )
        ).scalar_one_or_none()
        if sub is None:
            raise NotFoundProblem("That item is not a declared substitute for this BOM line")
        facts = await common.require_item(session, org_id, sub.substitute_item_id)
        material.item_id = sub.substitute_item_id
        material.ratio_milli = sub.ratio_milli
        material.discrete = is_discrete(facts.uom)
    if payload.substitute_item_id is None:
        component = await common.require_item(session, org_id, material.component_item_id)
        material.discrete = is_discrete(component.uom)
    material.required_milli = _material_required(material, order.planned_quantity_milli)
    material.updated_at = datetime.now(UTC)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="production_material.substituted",
        entity="production_material",
        entity_id=material.id,
        before=before,
        after={"item_id": str(material.item_id), "ratio_milli": material.ratio_milli},
        request=request,
    )
    return order


# ------------------------------------------------------------------ approve / start / cancel


def _started_payload(order: ProductionOrder) -> dict[str, Any]:
    return {
        "production_order_id": str(order.id),
        "number": order.number,
        "item_id": str(order.item_id),
        "bom_id": str(order.bom_id),
        "bom_version_id": str(order.bom_version_id),
        "planned_quantity": format_qty(order.planned_quantity_milli),
        "started_at": order.started_at.isoformat() if order.started_at else None,
    }


def _start(session: AsyncSession, ctx: ScopeContext, order: ProductionOrder) -> None:
    _transition(order, ctx, "IN_PROGRESS")
    order.started_at = datetime.now(UTC)
    common.emit(
        session,
        ctx,
        event_type=common.EVT_PRODUCTION_STARTED,
        aggregate_id=order.id,
        payload=_started_payload(order),
    )


async def approve_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> ActionResult:
    org_id = ctx.organization_id
    claim = await common.claim(
        session, ctx, key, scope=f"production_order.approve:{order_id}", payload={}
    )
    order = await lock_order(session, org_id, order_id)
    if claim.replay:
        return ActionResult(order, replayed=True)
    _transition(order, ctx, "APPROVED")
    order.approved_at = datetime.now(UTC)
    order.approved_by = common.user_ref(ctx)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="production_order.approved",
        entity="production_order",
        entity_id=order.id,
        before={"status": "DRAFT"},
        after={"status": "APPROVED"},
        request=request,
    )
    claim.done(order.id)
    return ActionResult(order)


async def start_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> ActionResult:
    org_id = ctx.organization_id
    claim = await common.claim(
        session, ctx, key, scope=f"production_order.start:{order_id}", payload={}
    )
    order = await lock_order(session, org_id, order_id)
    if claim.replay:
        return ActionResult(order, replayed=True)
    _start(session, ctx, order)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="production_order.started",
        entity="production_order",
        entity_id=order.id,
        before={"status": "APPROVED"},
        after={"status": "IN_PROGRESS"},
        request=request,
    )
    claim.done(order.id)
    return ActionResult(order)


async def _release_reservations(
    session: AsyncSession,
    ctx: ScopeContext,
    picks: list[ProductionPick],
    materials: dict[uuid.UUID, ProductionMaterial],
    *,
    request: Request | None,
) -> int:
    released = 0
    for pick in picks:
        if pick.reservation_id is not None and pick.reserved_milli > 0:
            await ledger.release(session, ctx, pick.reservation_id, request=request)
            materials[pick.material_id].reserved_milli = max(
                materials[pick.material_id].reserved_milli - pick.reserved_milli, 0
            )
            released += pick.reserved_milli
            pick.reserved_milli = 0
            pick.reservation_id = None
    return released


async def cancel_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    *,
    reason: str,
    key: str | None = None,
    request: Request | None = None,
) -> ActionResult:
    org_id = ctx.organization_id
    claim = await common.claim(
        session,
        ctx,
        key,
        scope=f"production_order.cancel:{order_id}",
        payload={"reason": reason},
    )
    order = await lock_order(session, org_id, order_id)
    if claim.replay:
        return ActionResult(order, replayed=True)
    if not domain.can_transition_production_order(order.status, "CANCELLED"):
        raise ConflictProblem(f"Order {order.number} is {order.status} and cannot be cancelled")
    materials, picks = await _load(session, org_id, order)
    in_wip = sum(selectors.wip_of(p) for p in picks)
    if in_wip:
        raise ConflictProblem(
            f"{format_qty(in_wip)} of material is still in WIP — return it to stock, or "
            "complete the order and scrap it, before cancelling"
        )
    released = await _release_reservations(
        session, ctx, picks, _materials_by_id(materials), request=request
    )
    before = _transition(order, ctx, "CANCELLED")
    order.cancelled_at = datetime.now(UTC)
    order.cancel_reason = reason
    await session.flush()
    common.audit(
        session,
        ctx,
        action="production_order.cancelled",
        entity="production_order",
        entity_id=order.id,
        before={"status": before},
        after={"status": "CANCELLED", "released_reservations": format_qty(released)},
        reason=reason or None,
        request=request,
    )
    claim.done(order.id)
    return ActionResult(order)


# ------------------------------------------------------------------ reserve


async def _free_buckets(
    session: AsyncSession,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    location_id: uuid.UUID | None,
    lot_id: uuid.UUID | None,
    roll_id: uuid.UUID | None,
) -> list[tuple[uuid.UUID, uuid.UUID | None, uuid.UUID | None, int]]:
    free = InventoryBalance.quantity_milli - InventoryBalance.reserved_milli
    stmt = select(
        InventoryBalance.location_id,
        InventoryBalance.lot_id,
        InventoryBalance.roll_id,
        free.label("free"),
    ).where(
        InventoryBalance.organization_id == org_id,
        InventoryBalance.inventory_item_id == item_id,
        InventoryBalance.status == "AVAILABLE",
        free > 0,
    )
    if location_id is not None:
        stmt = stmt.where(InventoryBalance.location_id == location_id)
    if lot_id is not None:
        stmt = stmt.where(InventoryBalance.lot_id == lot_id)
    if roll_id is not None:
        stmt = stmt.where(InventoryBalance.roll_id == roll_id)
    rows = (await session.execute(stmt.order_by(free.desc(), InventoryBalance.location_id))).all()
    return [(r.location_id, r.lot_id, r.roll_id, int(r.free)) for r in rows]


async def _bucket_free(
    session: AsyncSession,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    location_id: uuid.UUID,
    lot_id: uuid.UUID | None,
    roll_id: uuid.UUID | None,
) -> int:
    """Unreserved AVAILABLE stock in exactly one bucket, read now."""
    value = (
        await session.execute(
            select(InventoryBalance.quantity_milli - InventoryBalance.reserved_milli).where(
                InventoryBalance.organization_id == org_id,
                InventoryBalance.inventory_item_id == item_id,
                InventoryBalance.location_id == location_id,
                InventoryBalance.status == "AVAILABLE",
                InventoryBalance.lot_id.is_not_distinct_from(lot_id),
                InventoryBalance.roll_id.is_not_distinct_from(roll_id),
            )
        )
    ).scalar_one_or_none()
    return int(value or 0)


def _find_pick(
    picks: list[ProductionPick],
    material_id: uuid.UUID,
    location_id: uuid.UUID,
    lot_id: uuid.UUID | None,
    roll_id: uuid.UUID | None,
) -> ProductionPick | None:
    return next(
        (
            p
            for p in picks
            if p.material_id == material_id
            and p.source_location_id == location_id
            and p.lot_id == lot_id
            and p.roll_id == roll_id
        ),
        None,
    )


def _new_pick(
    ctx: ScopeContext,
    order: ProductionOrder,
    material: ProductionMaterial,
    location_id: uuid.UUID,
    lot_id: uuid.UUID | None,
    roll_id: uuid.UUID | None,
) -> ProductionPick:
    return ProductionPick(
        organization_id=ctx.organization_id,
        production_order_id=order.id,
        material_id=material.id,
        item_id=material.item_id,
        source_location_id=location_id,
        wip_location_id=order.wip_location_id or location_id,
        lot_id=lot_id,
        roll_id=roll_id,
        reservation_id=None,
        reserved_milli=0,
        issued_milli=0,
        consumed_milli=0,
        returned_milli=0,
        wasted_milli=0,
        created_by=common.user_ref(ctx),
        updated_by=common.user_ref(ctx),
    )


async def _reserve_into_pick(
    session: AsyncSession,
    ctx: ScopeContext,
    order: ProductionOrder,
    material: ProductionMaterial,
    picks: list[ProductionPick],
    *,
    location_id: uuid.UUID,
    lot_id: uuid.UUID | None,
    roll_id: uuid.UUID | None,
    quantity_milli: int,
    key: str | None,
    n: int,
    request: Request | None,
) -> None:
    """Reserve `quantity_milli` more of one bucket for `material`. A pick holds one kernel
    reservation, so topping one up is release-then-reserve of the combined figure — in a
    savepoint, so a refusal leaves the old reservation exactly as it was."""
    pick = _find_pick(picks, material.id, location_id, lot_id, roll_id)
    created = pick is None
    if pick is None:
        pick = _new_pick(ctx, order, material, location_id, lot_id, roll_id)
    total = pick.reserved_milli + quantity_milli
    async with session.begin_nested():
        if pick.reservation_id is not None and pick.reserved_milli > 0:
            await ledger.release(session, ctx, pick.reservation_id, request=request)
        made = await ledger.reserve(
            session,
            ctx,
            item_id=material.item_id,
            location_id=location_id,
            quantity_milli=total,
            lot_id=lot_id,
            roll_id=roll_id,
            reference_type=_REF,
            reference_id=order.id,
            idempotency_key=_key(order, "reserve", key, n),
            request=request,
        )
    pick.reservation_id = made.reservation.id
    pick.reserved_milli = total
    material.reserved_milli += quantity_milli
    if created:
        session.add(pick)
        picks.append(pick)
    await session.flush()


async def reserve_materials(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    payload: ReserveIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> ActionResult:
    org_id = ctx.organization_id
    claim = await common.claim(
        session,
        ctx,
        key,
        scope=f"production_order.reserve:{order_id}",
        payload=payload.model_dump(mode="json"),
    )
    order = await lock_order(session, org_id, order_id)
    if claim.replay:
        return ActionResult(order, replayed=True)
    _require_status(order, "APPROVED", "IN_PROGRESS", doing="reserve material")
    materials, picks = await _load(session, org_id, order)
    by_id = _materials_by_id(materials)

    def open_need(m: ProductionMaterial) -> int:
        return max(m.required_milli - m.reserved_milli - (m.issued_milli - m.returned_milli), 0)

    requests: list[
        tuple[ProductionMaterial, uuid.UUID | None, uuid.UUID | None, uuid.UUID | None, int]
    ]
    requests = []
    if payload.lines is None:
        requests = [(m, None, None, None, open_need(m)) for m in materials if open_need(m)]
    else:
        seen: set[uuid.UUID] = set()
        for line in payload.lines:
            material = by_id.get(line.material_id)
            if material is None:
                raise NotFoundProblem("That material is not part of this production order")
            facts = await common.require_item(session, org_id, material.item_id)
            if line.material_id in seen:
                raise ValidationProblem("Name each material at most once per request")
            seen.add(line.material_id)
            wanted = common.qty(line.quantity, facts.uom) if line.quantity else open_need(material)
            if wanted > open_need(material):
                raise ConflictProblem(
                    f"{facts.sku}: {format_qty(wanted)} exceeds the open need of "
                    f"{format_qty(open_need(material))}"
                )
            if line.location_id is not None:
                await common.require_location(session, org_id, line.location_id)
            requests.append((material, line.location_id, line.lot_id, line.roll_id, wanted))

    shortages: list[Shortage] = []
    n = 0
    for material, location_id, lot_id, roll_id, wanted in requests:
        if wanted <= 0:
            continue
        explicit = payload.lines is not None and location_id is not None
        if explicit:
            assert location_id is not None
            await _reserve_into_pick(
                session, ctx, order, material, picks,
                location_id=location_id, lot_id=lot_id, roll_id=roll_id,
                quantity_milli=wanted, key=key, n=n, request=request,
            )  # fmt: skip
            n += 1
            continue
        left = wanted
        for loc, b_lot, b_roll, free in await _free_buckets(
            session,
            org_id,
            material.item_id,
            location_id=None,
            lot_id=lot_id,
            roll_id=roll_id,
        ):
            if left <= 0:
                break
            # The free figure was read before any lock. Another request may have taken some of
            # it since: on a refusal, read what is free *now* and take that instead, so a race
            # costs this order only what the winner actually took.
            for _attempt in range(3):
                take = min(free, left)
                try:
                    await _reserve_into_pick(
                        session, ctx, order, material, picks,
                        location_id=loc, lot_id=b_lot, roll_id=b_roll,
                        quantity_milli=take, key=key, n=n, request=request,
                    )  # fmt: skip
                except InsufficientStock:
                    n += 1
                    free = await _bucket_free(session, org_id, material.item_id, loc, b_lot, b_roll)
                    if free <= 0:
                        break
                    continue
                n += 1
                left -= take
                break
        if left > 0:
            shortages.append(Shortage(material, material.required_milli - open_need(material)))

    if shortages:
        items = await selectors.items_by_id(
            session, org_id, {s.material.item_id for s in shortages}
        )
        common.emit(
            session,
            ctx,
            event_type=common.EVT_MATERIAL_SHORTAGE,
            aggregate_id=order.id,
            payload={
                "production_order_id": str(order.id),
                "number": order.number,
                "shortages": [
                    {
                        "material_id": str(s.material.id),
                        "item_id": str(s.material.item_id),
                        "sku": items[s.material.item_id].sku if s.material.item_id in items else "",
                        "required": format_qty(s.material.required_milli),
                        "covered": format_qty(s.covered_milli),
                        "short": format_qty(s.short_milli),
                    }
                    for s in shortages
                ],
            },
        )
    common.audit(
        session,
        ctx,
        action="production_order.reserved",
        entity="production_order",
        entity_id=order.id,
        after={
            "reserved": {str(m.id): format_qty(m.reserved_milli) for m in materials},
            "short_materials": [str(s.material.id) for s in shortages],
        },
        request=request,
    )
    claim.done(order.id)
    return ActionResult(order, shortages=shortages)


# ------------------------------------------------------------------ issue


@dataclass(slots=True)
class _IssueTarget:
    material: ProductionMaterial
    pick: ProductionPick
    new: bool
    quantity_milli: int


async def issue_materials(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    payload: IssueIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> ActionResult:
    org_id = ctx.organization_id
    claim = await common.claim(
        session,
        ctx,
        key,
        scope=f"production_order.issue:{order_id}",
        payload=payload.model_dump(mode="json"),
    )
    order = await lock_order(session, org_id, order_id)
    if claim.replay:
        return ActionResult(order, replayed=True)
    _require_status(order, "APPROVED", "IN_PROGRESS", doing="issue material")
    materials, picks = await _load(session, org_id, order)
    by_id = _materials_by_id(materials)
    pick_by_id = {p.id: p for p in picks}
    items = await selectors.items_by_id(session, org_id, {m.item_id for m in materials})

    targets: list[_IssueTarget] = []
    if payload.lines is None:
        for reserved in picks:
            if reserved.reserved_milli > 0:
                targets.append(
                    _IssueTarget(
                        by_id[reserved.material_id], reserved, False, reserved.reserved_milli
                    )
                )
        if not targets:
            raise ConflictProblem(
                "Nothing is reserved to issue — reserve material first, or name the stock to issue"
            )
    else:
        used: set[Any] = set()
        for line in payload.lines:
            pick: ProductionPick
            if line.pick_id is not None:
                known = pick_by_id.get(line.pick_id)
                if known is None:
                    raise NotFoundProblem("That pick is not part of this production order")
                pick = known
                material = by_id[pick.material_id]
                new = False
            else:
                assert line.material_id is not None and line.location_id is not None
                found = by_id.get(line.material_id)
                if found is None:
                    raise NotFoundProblem("That material is not part of this production order")
                material = found
                await common.require_location(session, org_id, line.location_id)
                existing = _find_pick(
                    picks, material.id, line.location_id, line.lot_id, line.roll_id
                )
                new = existing is None
                pick = existing or _new_pick(
                    ctx, order, material, line.location_id, line.lot_id, line.roll_id
                )
            facts = items.get(material.item_id)
            uom = facts.uom if facts else ""
            if line.quantity is not None:
                quantity = common.qty(line.quantity, uom)
            elif pick.reserved_milli > 0:
                quantity = pick.reserved_milli
            else:
                raise ValidationProblem("Give a quantity for stock that is not reserved")
            ident = (
                pick.id
                if not new
                else (material.id, pick.source_location_id, pick.lot_id, pick.roll_id)
            )
            if ident in used:
                raise ValidationProblem("Name each stock bucket at most once per issue")
            used.add(ident)
            targets.append(_IssueTarget(material, pick, new, quantity))

    totals: dict[uuid.UUID, int] = {}
    for t in targets:
        totals[t.material.id] = totals.get(t.material.id, 0) + t.quantity_milli
    if not payload.over_issue:
        for material_id, total in totals.items():
            material = by_id[material_id]
            net = material.issued_milli - material.returned_milli
            if net + total > material.required_milli:
                facts = items.get(material.item_id)
                raise ConflictProblem(
                    f"{facts.sku if facts else 'Material'}: issuing {format_qty(total)} on top of "
                    f"{format_qty(net)} already issued exceeds the requirement of "
                    f"{format_qty(material.required_milli)} (set over_issue to allow it)"
                )

    lines: list[MoveLine] = []
    for t in targets:
        draws = t.quantity_milli if t.pick.reserved_milli > 0 else 0
        lines.append(
            MoveLine(
                item_id=t.material.item_id,
                location_id=t.pick.source_location_id,
                quantity_milli=-t.quantity_milli,
                status="AVAILABLE",
                lot_id=t.pick.lot_id,
                roll_id=t.pick.roll_id,
                reservation_id=t.pick.reservation_id if draws else None,
            )
        )
        lines.append(
            MoveLine(
                item_id=t.material.item_id,
                location_id=t.pick.wip_location_id,
                quantity_milli=t.quantity_milli,
                status="WIP",
                lot_id=t.pick.lot_id,
                roll_id=t.pick.roll_id,
            )
        )
    await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=_merge(lines),
        occurred_on=common.today(),
        reason="production_issue",
        reference_type=_REF,
        reference_id=order.id,
        idempotency_key=_key(order, "issue", key),
        occurred_on_explicit=False,
        request=request,
    )

    if order.status == "APPROVED":
        _start(session, ctx, order)
    for t in targets:
        if t.new:
            session.add(t.pick)
            picks.append(t.pick)
        drawn = min(t.quantity_milli, t.pick.reserved_milli)
        t.pick.reserved_milli -= drawn
        if t.pick.reserved_milli == 0 and drawn:
            t.pick.reservation_id = None
        t.pick.issued_milli += t.quantity_milli
        t.pick.updated_at = datetime.now(UTC)
        t.material.reserved_milli = max(t.material.reserved_milli - drawn, 0)
        t.material.issued_milli += t.quantity_milli
        t.material.updated_at = datetime.now(UTC)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="production_order.materials_issued",
        entity="production_order",
        entity_id=order.id,
        after={
            "lines": [
                {
                    "material_id": str(t.material.id),
                    "pick_id": str(t.pick.id),
                    "location_id": str(t.pick.source_location_id),
                    "lot_id": str(t.pick.lot_id) if t.pick.lot_id else None,
                    "roll_id": str(t.pick.roll_id) if t.pick.roll_id else None,
                    "quantity": format_qty(t.quantity_milli),
                }
                for t in targets
            ]
        },
        request=request,
    )
    claim.done(order.id)
    return ActionResult(order)


# ------------------------------------------------------------------ return


async def return_materials(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    payload: ReturnIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> ActionResult:
    org_id = ctx.organization_id
    claim = await common.claim(
        session,
        ctx,
        key,
        scope=f"production_order.return:{order_id}",
        payload=payload.model_dump(mode="json"),
    )
    order = await lock_order(session, org_id, order_id)
    if claim.replay:
        return ActionResult(order, replayed=True)
    _require_status(order, "IN_PROGRESS", doing="return material")
    materials, picks = await _load(session, org_id, order)
    by_id = _materials_by_id(materials)
    pick_by_id = {p.id: p for p in picks}
    items = await selectors.items_by_id(session, org_id, {m.item_id for m in materials})
    plan: list[tuple[ProductionPick, uuid.UUID, int]] = []
    seen: set[uuid.UUID] = set()
    for line in payload.lines:
        pick = pick_by_id.get(line.pick_id)
        if pick is None:
            raise NotFoundProblem("That pick is not part of this production order")
        if pick.id in seen:
            raise ValidationProblem("Name each pick at most once per request")
        seen.add(pick.id)
        wip = selectors.wip_of(pick)
        facts = items.get(pick.item_id)
        quantity = common.qty(line.quantity, facts.uom if facts else "") if line.quantity else wip
        if quantity > wip:
            raise InsufficientWip(
                f"{facts.sku if facts else 'Material'}: only {format_qty(wip)} is in WIP; "
                f"cannot return {format_qty(quantity)}",
                errors=[{"pick_id": str(pick.id), "in_wip": format_qty(wip)}],
            )
        if quantity <= 0:
            raise ValidationProblem("Nothing in WIP to return for that pick")
        target = line.location_id or pick.source_location_id
        await common.require_location(session, org_id, target)
        plan.append((pick, target, quantity))
    await _post_returns(session, ctx, order, plan, by_id, key=key, request=request)
    common.audit(
        session,
        ctx,
        action="production_order.materials_returned",
        entity="production_order",
        entity_id=order.id,
        after={
            "lines": [
                {"pick_id": str(p.id), "location_id": str(loc), "quantity": format_qty(q)}
                for p, loc, q in plan
            ]
        },
        request=request,
    )
    claim.done(order.id)
    return ActionResult(order)


async def _post_returns(
    session: AsyncSession,
    ctx: ScopeContext,
    order: ProductionOrder,
    plan: list[tuple[ProductionPick, uuid.UUID, int]],
    by_id: dict[uuid.UUID, ProductionMaterial],
    *,
    key: str | None,
    request: Request | None,
) -> None:
    lines: list[MoveLine] = []
    for pick, target, quantity in plan:
        lines.append(
            MoveLine(
                item_id=pick.item_id,
                location_id=pick.wip_location_id,
                quantity_milli=-quantity,
                status="WIP",
                lot_id=pick.lot_id,
                roll_id=pick.roll_id,
            )
        )
        lines.append(
            MoveLine(
                item_id=pick.item_id,
                location_id=target,
                quantity_milli=quantity,
                status="AVAILABLE",
                lot_id=pick.lot_id,
                roll_id=pick.roll_id,
            )
        )
    await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=_merge(lines),
        occurred_on=common.today(),
        reason="production_return",
        reference_type=_REF,
        reference_id=order.id,
        idempotency_key=_key(order, "return", key),
        occurred_on_explicit=False,
        request=request,
    )
    for pick, _target, quantity in plan:
        pick.returned_milli += quantity
        pick.updated_at = datetime.now(UTC)
        material = by_id[pick.material_id]
        material.returned_milli += quantity
        material.updated_at = datetime.now(UTC)
    await session.flush()


# ------------------------------------------------------------------ output


def _wip_picks(picks: list[ProductionPick], material_id: uuid.UUID) -> list[ProductionPick]:
    return [p for p in picks if p.material_id == material_id and selectors.wip_of(p) > 0]


async def record_output(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    payload: OutputIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> ActionResult:
    org_id = ctx.organization_id
    claim = await common.claim(
        session,
        ctx,
        key,
        scope=f"production_order.output:{order_id}",
        payload=payload.model_dump(mode="json"),
    )
    order = await lock_order(session, org_id, order_id)
    if claim.replay:
        return ActionResult(order, replayed=True)
    _require_status(order, "IN_PROGRESS", doing="record output")
    item = await common.require_item(session, org_id, order.item_id)
    good = common.qty(payload.quantity, item.uom)
    location_id = payload.location_id or order.output_location_id
    await common.require_location(session, org_id, location_id)
    materials, picks = await _load(session, org_id, order)
    items = await selectors.items_by_id(session, org_id, {m.item_id for m in materials})

    total_good = order.produced_quantity_milli + good
    draws: list[tuple[ProductionMaterial, ProductionPick, int]] = []
    short: list[dict[str, str]] = []
    for material in materials:
        need = max(
            domain.theoretical_milli(
                component_milli=material.bom_quantity_milli,
                output_milli=material.output_milli,
                good_milli=total_good,
                ratio_milli=material.ratio_milli,
                discrete=material.discrete,
            )
            - material.consumed_milli,
            0,
        )
        if need == 0:
            continue
        wip = [(str(p.id), selectors.wip_of(p)) for p in _wip_picks(picks, material.id)]
        taken, shortfall = domain.allocate_fifo(wip, need)
        if shortfall:
            facts = items.get(material.item_id)
            short.append(
                {
                    "material_id": str(material.id),
                    "sku": facts.sku if facts else "",
                    "needed": format_qty(need),
                    "in_wip": format_qty(need - shortfall),
                    "short": format_qty(shortfall),
                }
            )
            continue
        by_pick = {str(p.id): p for p in picks}
        draws.extend((material, by_pick[pid], q) for pid, q in taken)
    if short:
        raise InsufficientWip(
            "Not enough material in WIP for that output: "
            + "; ".join(f"{s['sku']} short by {s['short']}" for s in short),
            errors=short,
        )

    consume_move = None
    if draws:
        consume_move = await ledger.post_move(
            session,
            ctx,
            kind="issue",
            lines=_merge(
                [
                    MoveLine(
                        item_id=pick.item_id,
                        location_id=pick.wip_location_id,
                        quantity_milli=-q,
                        status="WIP",
                        lot_id=pick.lot_id,
                        roll_id=pick.roll_id,
                    )
                    for _m, pick, q in draws
                ]
            ),
            occurred_on=common.today(),
            reason="production_consume",
            reference_type=_REF,
            reference_id=order.id,
            idempotency_key=_key(order, "output", key, 0),
            occurred_on_explicit=False,
            request=request,
        )
    receipt = await ledger.post_move(
        session,
        ctx,
        kind="receipt",
        lines=[
            MoveLine(
                item_id=order.item_id,
                location_id=location_id,
                quantity_milli=good,
                status=payload.status,
                lot_id=payload.lot_id,
                roll_id=payload.roll_id,
            )
        ],
        occurred_on=common.today(),
        reason="production_output",
        reference_type=_REF,
        reference_id=order.id,
        idempotency_key=_key(order, "output", key, 1),
        occurred_on_explicit=False,
        request=request,
    )

    output = ProductionOutput(
        organization_id=org_id,
        production_order_id=order.id,
        item_id=order.item_id,
        quantity_milli=good,
        location_id=location_id,
        status=payload.status,
        lot_id=payload.lot_id,
        roll_id=payload.roll_id,
        note=payload.note,
        consume_move_id=consume_move.move.id if consume_move else None,
        receipt_move_id=receipt.move.id,
        created_by=common.user_ref(ctx),
    )
    session.add(output)
    await session.flush()
    for material, pick, q in draws:
        pick.consumed_milli += q
        pick.updated_at = datetime.now(UTC)
        material.consumed_milli += q
        material.updated_at = datetime.now(UTC)
        session.add(
            ProductionGenealogy(
                organization_id=org_id,
                production_order_id=order.id,
                output_id=output.id,
                parent_item_id=pick.item_id,
                parent_lot_id=pick.lot_id,
                parent_roll_id=pick.roll_id,
                child_item_id=order.item_id,
                child_lot_id=payload.lot_id,
                child_roll_id=payload.roll_id,
                quantity_milli=q,
            )
        )
    order.produced_quantity_milli = total_good
    order.updated_at = datetime.now(UTC)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="production_order.output_recorded",
        entity="production_output",
        entity_id=output.id,
        after={
            "production_order_id": str(order.id),
            "quantity": format_qty(good),
            "location_id": str(location_id),
            "lot_id": str(payload.lot_id) if payload.lot_id else None,
            "consumed": [
                {"pick_id": str(p.id), "item_id": str(p.item_id), "quantity": format_qty(q)}
                for _m, p, q in draws
            ],
        },
        request=request,
    )
    claim.done(order.id)
    return ActionResult(order)


# ------------------------------------------------------------------ wastage


async def _check_threshold(
    session: AsyncSession,
    ctx: ScopeContext,
    order: ProductionOrder,
    material: ProductionMaterial,
    wastage_type: str,
) -> None:
    """Stage `wastage_threshold_exceeded` once, on the write that crosses the line. The line is
    the most specific configured threshold, else the BOM line's own scrap allowance, applied to
    the order's standard (no-scrap) need of the material."""
    org_id = ctx.organization_id
    rows = [
        (str(t.item_id) if t.item_id else None, t.wastage_type, t.max_bps)
        for t in await selectors.thresholds(session, org_id)
    ]
    configured = domain.pick_threshold(rows, item_id=str(order.item_id), wastage_type=wastage_type)
    scope_type, bps = configured if configured is not None else (None, material.scrap_bps)
    stmt = select(func.coalesce(func.sum(ProductionWastage.quantity_milli), 0)).where(
        ProductionWastage.organization_id == org_id,
        ProductionWastage.material_id == material.id,
    )
    if scope_type is not None:
        stmt = stmt.where(ProductionWastage.wastage_type == scope_type)
    wasted = int((await session.execute(stmt)).scalar_one())
    standard = domain.theoretical_milli(
        component_milli=material.bom_quantity_milli,
        output_milli=material.output_milli,
        good_milli=order.planned_quantity_milli,
        ratio_milli=material.ratio_milli,
        discrete=material.discrete,
    )
    allowed = domain.allowance_milli(standard, bps)
    if wasted <= allowed or material.threshold_alerted:
        return
    material.threshold_alerted = True
    common.emit(
        session,
        ctx,
        event_type=common.EVT_WASTAGE_THRESHOLD,
        aggregate_id=order.id,
        payload={
            "production_order_id": str(order.id),
            "number": order.number,
            "material_id": str(material.id),
            "item_id": str(material.item_id),
            "wastage_type": wastage_type,
            "wasted": format_qty(wasted),
            "allowed": format_qty(allowed),
            "threshold_percent": domain.format_bps(bps),
        },
    )


async def _scrap(
    session: AsyncSession,
    ctx: ScopeContext,
    order: ProductionOrder,
    material: ProductionMaterial,
    picks: list[ProductionPick],
    draws: list[tuple[ProductionPick, int]],
    *,
    wastage_type: str,
    note: str,
    key: str | None,
    n: int,
    request: Request | None,
) -> None:
    move = await ledger.post_move(
        session,
        ctx,
        kind="issue",
        lines=_merge(
            [
                MoveLine(
                    item_id=pick.item_id,
                    location_id=pick.wip_location_id,
                    quantity_milli=-q,
                    status="WIP",
                    lot_id=pick.lot_id,
                    roll_id=pick.roll_id,
                )
                for pick, q in draws
            ]
        ),
        occurred_on=common.today(),
        reason="production_waste",
        reference_type=_REF,
        reference_id=order.id,
        note=wastage_type,
        idempotency_key=_key(order, "wastage", key, n),
        occurred_on_explicit=False,
        request=request,
    )
    event_id = uuid.uuid4()
    for pick, q in draws:
        pick.wasted_milli += q
        pick.updated_at = datetime.now(UTC)
        material.wasted_milli += q
        session.add(
            ProductionWastage(
                organization_id=ctx.organization_id,
                production_order_id=order.id,
                material_id=material.id,
                pick_id=pick.id,
                event_id=event_id,
                item_id=pick.item_id,
                wastage_type=wastage_type,
                quantity_milli=q,
                location_id=pick.wip_location_id,
                lot_id=pick.lot_id,
                roll_id=pick.roll_id,
                note=note,
                move_id=move.move.id,
                created_by=common.user_ref(ctx),
            )
        )
    material.updated_at = datetime.now(UTC)
    await session.flush()
    await _check_threshold(session, ctx, order, material, wastage_type)


async def record_wastage(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    payload: WastageIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> ActionResult:
    org_id = ctx.organization_id
    claim = await common.claim(
        session,
        ctx,
        key,
        scope=f"production_order.wastage:{order_id}",
        payload=payload.model_dump(mode="json"),
    )
    order = await lock_order(session, org_id, order_id)
    if claim.replay:
        return ActionResult(order, replayed=True)
    _require_status(order, "IN_PROGRESS", doing="record wastage")
    materials, picks = await _load(session, org_id, order)
    material = _materials_by_id(materials).get(payload.material_id)
    if material is None:
        raise NotFoundProblem("That material is not part of this production order")
    facts = await common.require_item(session, org_id, material.item_id)
    wasted = common.qty(payload.quantity, facts.uom)
    if payload.pick_id is not None:
        pick = next((p for p in picks if p.id == payload.pick_id), None)
        if pick is None or pick.material_id != material.id:
            raise NotFoundProblem("That pick is not part of this material")
        candidates = [pick]
    else:
        candidates = _wip_picks(picks, material.id)
    by_pick = {str(p.id): p for p in picks}
    taken, shortfall = domain.allocate_fifo(
        [(str(p.id), selectors.wip_of(p)) for p in candidates], wasted
    )
    if shortfall:
        in_wip = wasted - shortfall
        raise InsufficientWip(
            f"{facts.sku}: only {format_qty(in_wip)} is in WIP; cannot write off "
            f"{format_qty(wasted)}",
            errors=[{"material_id": str(material.id), "in_wip": format_qty(in_wip)}],
        )
    await _scrap(
        session,
        ctx,
        order,
        material,
        picks,
        [(by_pick[pid], q) for pid, q in taken],
        wastage_type=payload.wastage_type,
        note=payload.note,
        key=key,
        n=0,
        request=request,
    )
    common.audit(
        session,
        ctx,
        action="production_order.wastage_recorded",
        entity="production_order",
        entity_id=order.id,
        after={
            "material_id": str(material.id),
            "wastage_type": payload.wastage_type,
            "quantity": format_qty(wasted),
        },
        request=request,
    )
    claim.done(order.id)
    return ActionResult(order)


# ------------------------------------------------------------------ complete


async def complete_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    payload: CompleteIn,
    *,
    key: str | None = None,
    request: Request | None = None,
) -> ActionResult:
    org_id = ctx.organization_id
    claim = await common.claim(
        session,
        ctx,
        key,
        scope=f"production_order.complete:{order_id}",
        payload=payload.model_dump(mode="json"),
    )
    order = await lock_order(session, org_id, order_id)
    if claim.replay:
        return ActionResult(order, replayed=True)
    _require_status(order, "IN_PROGRESS", doing="complete it")
    if order.produced_quantity_milli <= 0:
        raise ConflictProblem("Nothing has been produced — record output, or cancel the order")
    materials, picks = await _load(session, org_id, order)
    by_id = _materials_by_id(materials)
    leftover = [p for p in picks if selectors.wip_of(p) > 0]
    if leftover and payload.leftover is None:
        items = await selectors.items_by_id(session, org_id, {p.item_id for p in leftover})
        raise ConflictProblem(
            "Material is still in WIP ("
            + ", ".join(
                f"{items[p.item_id].sku if p.item_id in items else '?'} "
                f"{format_qty(selectors.wip_of(p))}"
                for p in leftover
            )
            + ") — complete with leftover=return or leftover=scrap"
        )
    if leftover and payload.leftover == "return":
        await _post_returns(
            session,
            ctx,
            order,
            [(p, p.source_location_id, selectors.wip_of(p)) for p in leftover],
            by_id,
            key=key,
            request=request,
        )
    elif leftover:
        by_material: dict[uuid.UUID, list[ProductionPick]] = {}
        for p in leftover:
            by_material.setdefault(p.material_id, []).append(p)
        for n, (material_id, group) in enumerate(by_material.items()):
            await _scrap(
                session,
                ctx,
                order,
                by_id[material_id],
                picks,
                [(p, selectors.wip_of(p)) for p in group],
                wastage_type="LEFTOVER",
                note=payload.note or "Left in WIP at completion",
                key=key,
                n=n,
                request=request,
            )
    await _release_reservations(session, ctx, picks, by_id, request=request)
    _transition(order, ctx, "COMPLETED")
    order.completed_at = datetime.now(UTC)
    await session.flush()

    items = await selectors.items_by_id(session, org_id, {m.item_id for m in materials})
    variance = selectors.variance_lines(order, materials, items)
    common.emit(
        session,
        ctx,
        event_type=common.EVT_PRODUCTION_COMPLETED,
        aggregate_id=order.id,
        payload={
            "production_order_id": str(order.id),
            "number": order.number,
            "item_id": str(order.item_id),
            "bom_version_id": str(order.bom_version_id),
            "planned_quantity": format_qty(order.planned_quantity_milli),
            "produced_quantity": format_qty(order.produced_quantity_milli),
            "completed_at": order.completed_at.isoformat() if order.completed_at else None,
            "variance": [
                {
                    "material_id": v.material_id,
                    "item_id": v.item_id,
                    "standard": v.standard,
                    "actual": v.actual,
                    "variance": v.variance,
                }
                for v in variance.lines
            ],
        },
    )
    common.audit(
        session,
        ctx,
        action="production_order.completed",
        entity="production_order",
        entity_id=order.id,
        before={"status": "IN_PROGRESS"},
        after={
            "status": "COMPLETED",
            "produced_quantity": format_qty(order.produced_quantity_milli),
            "leftover": payload.leftover if leftover else None,
        },
        reason=payload.note or None,
        request=request,
    )
    claim.done(order.id)
    return ActionResult(order)


# ------------------------------------------------------------------ wastage thresholds


async def upsert_threshold(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: WastageThresholdIn,
    *,
    request: Request | None = None,
) -> WastageThreshold:
    org_id = ctx.organization_id
    if payload.item_id is not None:
        await common.require_item(session, org_id, payload.item_id)
    bps = common.pct(payload.max_percent, field="max_percent")
    existing = (
        await session.execute(
            select(WastageThreshold).where(
                WastageThreshold.organization_id == org_id,
                WastageThreshold.deleted_at.is_(None),
                WastageThreshold.item_id.is_not_distinct_from(payload.item_id),
                WastageThreshold.wastage_type.is_not_distinct_from(payload.wastage_type),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        before = {"max_bps": existing.max_bps}
        existing.max_bps = bps
        existing.updated_by = common.user_ref(ctx)
        existing.updated_at = datetime.now(UTC)
        row = existing
    else:
        before = None
        row = WastageThreshold(
            organization_id=org_id,
            item_id=payload.item_id,
            wastage_type=payload.wastage_type,
            max_bps=bps,
            created_by=common.user_ref(ctx),
            updated_by=common.user_ref(ctx),
        )
        session.add(row)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="wastage_threshold.set",
        entity="wastage_threshold",
        entity_id=row.id,
        before=before,
        after={
            "item_id": str(row.item_id) if row.item_id else None,
            "wastage_type": row.wastage_type,
            "max_bps": bps,
        },
        request=request,
    )
    return row


async def delete_threshold(
    session: AsyncSession,
    ctx: ScopeContext,
    threshold_id: uuid.UUID,
    *,
    request: Request | None = None,
) -> None:
    row = await selectors.get_threshold(session, ctx.organization_id, threshold_id)
    if row is None:
        raise NotFoundProblem("That threshold is not part of this organization")
    row.deleted_at = datetime.now(UTC)
    row.updated_by = common.user_ref(ctx)
    await session.flush()
    common.audit(
        session,
        ctx,
        action="wastage_threshold.removed",
        entity="wastage_threshold",
        entity_id=row.id,
        before={"max_bps": row.max_bps},
        request=request,
    )
