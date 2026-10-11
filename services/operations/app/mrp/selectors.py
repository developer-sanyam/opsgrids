"""Reads for MRP.

Two kinds, kept apart on purpose:

* **Scoped reads** (`*_stmt`, `get_*`) take the caller's `ScopeContext`, call `ctx.apply()` **and**
  filter `organization_id` explicitly — RLS is the net, not the filter (CLAUDE.md). Every list and
  detail endpoint reads through these.
* **Planning inputs** (`collect_*`, `supply_for`, `load_boms`) take an organization id: they feed a
  run that is executing for that organization, whether in a request or in the worker (which has no
  caller). Each is one set-based query per kind of row, never one per item.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.quantity import format_qty, is_discrete
from app.manufacturing import domain as mfg_domain
from app.manufacturing import selectors as mfg_selectors
from app.manufacturing.models import Bom, BomComponent, ProductionMaterial, ProductionOrder
from app.manufacturing.selectors import ItemFacts, items_by_id
from app.models import (
    InventoryBalance,
    InventoryItem,
    InventoryReservation,
    Order,
    OrderLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
)
from app.mrp import domain
from app.mrp.models import MrpDemand, MrpRun, MrpShortage, MrpShortageLine
from app.mrp.schemas import (
    MrpDemandOut,
    MrpRunOut,
    MrpShortageDetailOut,
    MrpShortageLineOut,
    MrpShortageOut,
)
from app.receiving import selectors as receiving_selectors
from platform_core.auth import ScopeContext

#: Sales orders that still owe stock: placed and not yet picked to READY (READY and later have
#: their goods staged or gone; DRAFT is not a commitment).
OPEN_ORDER_STATUSES = ("PLACED", "ACCEPTED", "IN_FULFILMENT", "ON_HOLD")
#: A production order that still plans to consume material and produce output.
OPEN_PRODUCTION_STATUSES = ("DRAFT", "APPROVED", "IN_PROGRESS")
#: A purchase order that is committed and not yet all in: `approved` and, once receiving has
#: started, `partially_received` (a draft is not a promise; `received` has nothing left to come).
OPEN_PO_STATUSES = ("approved", "partially_received")

_CHUNK = 2000


def _chunks(ids: Iterable[uuid.UUID], size: int = _CHUNK) -> list[list[uuid.UUID]]:
    ordered = sorted(set(ids))
    return [ordered[i : i + size] for i in range(0, len(ordered), size)]


# ------------------------------------------------------------------ scoped reads


def runs_stmt(
    ctx: ScopeContext, *, run_status: str | None = None, q: str | None = None
) -> Select[Any]:
    stmt = select(MrpRun).where(MrpRun.organization_id == ctx.organization_id)
    stmt = ctx.apply(stmt, owner_col=None)
    if run_status:
        stmt = stmt.where(MrpRun.status == run_status)
    if q:
        stmt = stmt.where(
            or_(MrpRun.number.ilike(f"%{q.strip()}%"), MrpRun.notes.ilike(f"%{q.strip()}%"))
        )
    return stmt.order_by(MrpRun.seq.desc())


async def get_run(
    session: AsyncSession, ctx: ScopeContext, run_id: uuid.UUID, *, for_update: bool = False
) -> MrpRun | None:
    stmt = select(MrpRun).where(MrpRun.id == run_id, MrpRun.organization_id == ctx.organization_id)
    stmt = ctx.apply(stmt, owner_col=None)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


def demands_stmt(
    ctx: ScopeContext, run_id: uuid.UUID, *, source_type: str | None = None
) -> Select[Any]:
    stmt = select(MrpDemand).where(
        MrpDemand.organization_id == ctx.organization_id, MrpDemand.run_id == run_id
    )
    stmt = ctx.apply(stmt, owner_col=None)
    if source_type:
        stmt = stmt.where(MrpDemand.source_type == source_type)
    return stmt.order_by(MrpDemand.source_type, MrpDemand.source_ref, MrpDemand.id)


def shortages_stmt(
    ctx: ScopeContext,
    run_id: uuid.UUID,
    *,
    action: str | None = None,
    requested: bool | None = None,
    q: str | None = None,
) -> Select[Any]:
    stmt = select(MrpShortage).where(
        MrpShortage.organization_id == ctx.organization_id, MrpShortage.run_id == run_id
    )
    stmt = ctx.apply(stmt, owner_col=None)
    if action:
        stmt = stmt.where(MrpShortage.action == action)
    if requested is True:
        stmt = stmt.where(MrpShortage.requisition_request_id.is_not(None))
    elif requested is False:
        stmt = stmt.where(MrpShortage.requisition_request_id.is_(None))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            MrpShortage.item_id.in_(
                select(InventoryItem.id).where(
                    InventoryItem.organization_id == ctx.organization_id,
                    or_(InventoryItem.sku.ilike(like), InventoryItem.name.ilike(like)),
                )
            )
        )
    return stmt.order_by(MrpShortage.level, MrpShortage.item_id)


async def get_shortage(
    session: AsyncSession,
    ctx: ScopeContext,
    run_id: uuid.UUID,
    shortage_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> MrpShortage | None:
    stmt = select(MrpShortage).where(
        MrpShortage.id == shortage_id,
        MrpShortage.run_id == run_id,
        MrpShortage.organization_id == ctx.organization_id,
    )
    stmt = ctx.apply(stmt, owner_col=None)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def shortages_by_ids(
    session: AsyncSession, ctx: ScopeContext, run_id: uuid.UUID, ids: Sequence[uuid.UUID] | None
) -> list[MrpShortage]:
    """The run's shortages (all, or exactly `ids`), row-locked in a fixed order so two
    concurrent requisition requests queue instead of both seeing a shortage as un-requested."""
    stmt = select(MrpShortage).where(
        MrpShortage.organization_id == ctx.organization_id, MrpShortage.run_id == run_id
    )
    stmt = ctx.apply(stmt, owner_col=None)
    if ids is not None:
        stmt = stmt.where(MrpShortage.id.in_(ids))
    stmt = stmt.order_by(MrpShortage.id).with_for_update()
    return list((await session.execute(stmt)).scalars().all())


async def items_already_requested(
    session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID, item_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Items another run's shortage has already asked purchasing for, where that request is still
    on its way: no requisition has been made from it yet, or the one made (it carries the request
    id in `mrp_request_id`, see `services.requisition_for_request`) is not cancelled, rejected or
    converted into a purchase order."""
    ids = set(item_ids)
    if not ids:
        return set()
    closed = (
        select(PurchaseRequisition.id)
        .where(
            PurchaseRequisition.organization_id == org_id,
            PurchaseRequisition.status.in_(("CANCELLED", "REJECTED", "CONVERTED")),
            PurchaseRequisition.mrp_request_id == MrpShortage.requisition_request_id,
        )
        .exists()
    )
    rows = await session.execute(
        select(MrpShortage.item_id).where(
            MrpShortage.organization_id == org_id,
            MrpShortage.run_id != run_id,
            MrpShortage.item_id.in_(ids),
            MrpShortage.requisition_request_id.is_not(None),
            ~closed,
        )
    )
    return {r for (r,) in rows}


async def shortage_lines_of(
    session: AsyncSession, ctx: ScopeContext, shortage_id: uuid.UUID
) -> list[MrpShortageLine]:
    stmt = select(MrpShortageLine).where(
        MrpShortageLine.organization_id == ctx.organization_id,
        MrpShortageLine.shortage_id == shortage_id,
    )
    stmt = ctx.apply(stmt, owner_col=None)
    return list(
        (
            await session.execute(
                stmt.order_by(
                    MrpShortageLine.due_date.asc().nulls_last(),
                    MrpShortageLine.source_type,
                    MrpShortageLine.source_ref,
                    MrpShortageLine.id,
                )
            )
        )
        .scalars()
        .all()
    )


# ------------------------------------------------------------------ builders


def run_out(run: MrpRun) -> MrpRunOut:
    return MrpRunOut(
        id=str(run.id),
        number=run.number,
        status=run.status,
        execution=run.execution,
        plan_date=run.plan_date,
        sources=list(run.params.get("sources", [])),
        notes=run.notes,
        demand_count=run.demand_count,
        shortage_count=run.shortage_count,
        items_planned=run.items_planned,
        result_hash=run.result_hash,
        error=run.error,
        requested_by=run.requested_by,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


async def demand_outs(
    session: AsyncSession, org_id: uuid.UUID, rows: Sequence[MrpDemand]
) -> list[MrpDemandOut]:
    items = await items_by_id(session, org_id, {r.item_id for r in rows})
    out: list[MrpDemandOut] = []
    for r in rows:
        item = items.get(r.item_id)
        out.append(
            MrpDemandOut(
                id=str(r.id),
                source_type=r.source_type,
                source_id=str(r.source_id) if r.source_id else None,
                source_ref=r.source_ref,
                item_id=str(r.item_id),
                sku=item.sku if item else "",
                name=item.name if item else "",
                uom=item.uom if item else "",
                requested=format_qty(r.requested_milli),
                reserved=format_qty(r.reserved_milli),
                quantity=format_qty(r.quantity_milli),
                due_date=r.due_date,
                note=r.note,
            )
        )
    return out


def shortage_out(row: MrpShortage, items: dict[uuid.UUID, ItemFacts]) -> MrpShortageOut:
    item = items.get(row.item_id)
    free = max(0, row.on_hand_milli - row.reserved_milli)
    return MrpShortageOut(
        id=str(row.id),
        run_id=str(row.run_id),
        item_id=str(row.item_id),
        sku=item.sku if item else "",
        name=item.name if item else "",
        uom=item.uom if item else "",
        action=row.action,
        level=row.level,
        gross=format_qty(row.gross_milli),
        on_hand=format_qty(row.on_hand_milli),
        reserved=format_qty(row.reserved_milli),
        available=format_qty(free),
        open_po=format_qty(row.open_po_milli),
        open_production=format_qty(row.open_production_milli),
        supply=format_qty(free + row.open_po_milli + row.open_production_milli),
        shortage=format_qty(row.shortage_milli),
        need_by=row.need_by,
        requisition_request_id=str(row.requisition_request_id)
        if row.requisition_request_id
        else None,
        requisition_requested_at=row.requisition_requested_at,
        requisition_id=str(row.requisition_id) if row.requisition_id else None,
    )


async def shortage_outs(
    session: AsyncSession, org_id: uuid.UUID, rows: Sequence[MrpShortage]
) -> list[MrpShortageOut]:
    items = await items_by_id(session, org_id, {r.item_id for r in rows})
    return [shortage_out(r, items) for r in rows]


async def shortage_detail_out(
    session: AsyncSession, ctx: ScopeContext, row: MrpShortage
) -> MrpShortageDetailOut:
    lines = await shortage_lines_of(session, ctx, row.id)
    items = await items_by_id(
        session,
        ctx.organization_id,
        {row.item_id} | {ln.parent_item_id for ln in lines if ln.parent_item_id},
    )
    base = shortage_out(row, items)
    return MrpShortageDetailOut(
        **base.model_dump(),
        lines=[
            MrpShortageLineOut(
                source_type=ln.source_type,
                source_id=str(ln.source_id) if ln.source_id else None,
                source_ref=ln.source_ref,
                parent_item_id=str(ln.parent_item_id) if ln.parent_item_id else None,
                parent_sku=items[ln.parent_item_id].sku
                if ln.parent_item_id and ln.parent_item_id in items
                else None,
                quantity=format_qty(ln.quantity_milli),
                due_date=ln.due_date,
            )
            for ln in lines
        ],
    )


# ------------------------------------------------------------------ planning inputs: demand


@dataclass(frozen=True, slots=True)
class DemandRow:
    source_type: str
    source_id: uuid.UUID | None
    source_ref: str
    item_id: uuid.UUID
    requested_milli: int
    reserved_milli: int
    due_date: date | None = None
    note: str = ""

    @property
    def open_milli(self) -> int:
        return self.requested_milli - self.reserved_milli


async def count_open_demand(session: AsyncSession, org_id: uuid.UUID) -> tuple[int, int]:
    """(open sales-order lines, open production-order materials) — what decides sync vs worker."""
    lines = (
        await session.execute(
            select(func.count())
            .select_from(OrderLine)
            .join(Order, Order.id == OrderLine.order_id)
            .where(
                OrderLine.organization_id == org_id,
                OrderLine.deleted_at.is_(None),
                Order.organization_id == org_id,
                Order.deleted_at.is_(None),
                Order.status.in_(OPEN_ORDER_STATUSES),
            )
        )
    ).scalar_one()
    materials = (
        await session.execute(
            select(func.count())
            .select_from(ProductionMaterial)
            .join(
                ProductionOrder,
                ProductionOrder.id == ProductionMaterial.production_order_id,
            )
            .where(
                ProductionMaterial.organization_id == org_id,
                ProductionOrder.organization_id == org_id,
                ProductionOrder.deleted_at.is_(None),
                ProductionOrder.status.in_(OPEN_PRODUCTION_STATUSES),
            )
        )
    ).scalar_one()
    return int(lines), int(materials)


async def collect_sales_demand(session: AsyncSession, org_id: uuid.UUID) -> list[DemandRow]:
    """Open order lines, one row per (order, item): the lines' total less what the order has
    already reserved from stock (that part is not a new requirement — and the same reservation
    is already out of the supply side, so counting it again would double it)."""
    rows = (
        await session.execute(
            select(
                Order.id,
                Order.number,
                OrderLine.inventory_item_id,
                func.sum(OrderLine.quantity_milli),
            )
            .join(Order, Order.id == OrderLine.order_id)
            .where(
                OrderLine.organization_id == org_id,
                OrderLine.deleted_at.is_(None),
                Order.organization_id == org_id,
                Order.deleted_at.is_(None),
                Order.status.in_(OPEN_ORDER_STATUSES),
            )
            .group_by(Order.id, Order.number, OrderLine.inventory_item_id)
            .order_by(Order.number, OrderLine.inventory_item_id)
        )
    ).all()
    reserved = await _open_reservations(session, org_id, "order", {r[0] for r in rows})
    out: list[DemandRow] = []
    for order_id, number, item_id, total in rows:
        held = min(int(total), reserved.get((order_id, item_id), 0))
        out.append(DemandRow("SALES_ORDER", order_id, number, item_id, int(total), held))
    return [r for r in out if r.open_milli > 0]


async def _open_reservations(
    session: AsyncSession, org_id: uuid.UUID, reference_type: str, reference_ids: set[uuid.UUID]
) -> dict[tuple[uuid.UUID, uuid.UUID], int]:
    if not reference_ids:
        return {}
    out: dict[tuple[uuid.UUID, uuid.UUID], int] = defaultdict(int)
    for chunk in _chunks(reference_ids):
        rows = (
            await session.execute(
                select(
                    InventoryReservation.reference_id,
                    InventoryReservation.inventory_item_id,
                    func.sum(
                        InventoryReservation.quantity_milli
                        - InventoryReservation.consumed_milli
                        - InventoryReservation.released_milli
                        - InventoryReservation.transferred_milli
                    ),
                )
                .where(
                    InventoryReservation.organization_id == org_id,
                    InventoryReservation.status == "active",
                    InventoryReservation.reference_type == reference_type,
                    InventoryReservation.reference_id.in_(chunk),
                )
                .group_by(InventoryReservation.reference_id, InventoryReservation.inventory_item_id)
            )
        ).all()
        for ref, item, qty in rows:
            out[(ref, item)] += int(qty or 0)
    return out


async def collect_production_demand(session: AsyncSession, org_id: uuid.UUID) -> list[DemandRow]:
    """Open materials of open production orders: what each still needs (`required - issued`),
    less its open reservation. Output of those orders is supply, not demand (see `supply_for`)."""
    rows = (
        await session.execute(
            select(ProductionOrder, ProductionMaterial)
            .join(ProductionMaterial, ProductionMaterial.production_order_id == ProductionOrder.id)
            .where(
                ProductionOrder.organization_id == org_id,
                ProductionOrder.deleted_at.is_(None),
                ProductionOrder.status.in_(OPEN_PRODUCTION_STATUSES),
                ProductionMaterial.organization_id == org_id,
            )
            .order_by(ProductionOrder.seq, ProductionMaterial.line_no)
        )
    ).all()
    out: list[DemandRow] = []
    for order, material in rows:
        # material handed back to stock has to be issued again, so it is demand again
        requested = material.required_milli - material.issued_milli + material.returned_milli
        if requested <= 0:
            continue
        held = min(requested, material.reserved_milli)
        if requested - held <= 0:
            continue
        out.append(
            DemandRow(
                "PRODUCTION_ORDER",
                order.id,
                order.number,
                material.item_id,
                requested,
                held,
                order.due_date,
            )
        )
    return out


# ------------------------------------------------------------------ planning inputs: supply


async def supply_for(
    session: AsyncSession, org_id: uuid.UUID, item_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, domain.Supply]:
    """Per item: AVAILABLE on-hand and its open reservations (other statuses are not usable),
    approved-PO quantity still to come, and open production orders' output still to come."""
    on_hand: dict[uuid.UUID, int] = defaultdict(int)
    reserved: dict[uuid.UUID, int] = defaultdict(int)
    po: dict[uuid.UUID, int] = defaultdict(int)
    prod: dict[uuid.UUID, int] = defaultdict(int)
    for chunk in _chunks(item_ids):
        for item, qty, held in (
            await session.execute(
                select(
                    InventoryBalance.inventory_item_id,
                    func.sum(InventoryBalance.quantity_milli),
                    func.sum(InventoryBalance.reserved_milli),
                )
                .where(
                    InventoryBalance.organization_id == org_id,
                    InventoryBalance.status == "AVAILABLE",
                    InventoryBalance.inventory_item_id.in_(chunk),
                )
                .group_by(InventoryBalance.inventory_item_id)
            )
        ).all():
            on_hand[item] += int(qty or 0)
            reserved[item] += int(held or 0)
        po_rows = (
            await session.execute(
                select(InventoryItem, PurchaseOrderLine)
                .join(
                    PurchaseOrderLine,
                    (PurchaseOrderLine.sku == InventoryItem.sku)
                    & (PurchaseOrderLine.organization_id == org_id)
                    & PurchaseOrderLine.deleted_at.is_(None)
                    & PurchaseOrderLine.short_closed.is_(False),
                )
                .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
                .where(
                    InventoryItem.organization_id == org_id,
                    InventoryItem.deleted_at.is_(None),
                    InventoryItem.id.in_(chunk),
                    PurchaseOrder.organization_id == org_id,
                    PurchaseOrder.deleted_at.is_(None),
                    PurchaseOrder.status.in_(OPEN_PO_STATUSES),
                )
            )
        ).all()
        ratios = await receiving_selectors.conversions_for_items(
            session, org_id, {item.id for item, _ in po_rows}
        )
        for item, line in po_rows:
            ordered = receiving_selectors.ordered_milli(line, item.uom, ratios.get(item.id, {}))
            po[item.id] += max(ordered - line.received_milli, 0)
        for item, qty in (
            await session.execute(
                select(
                    ProductionOrder.item_id,
                    func.sum(
                        func.greatest(
                            ProductionOrder.planned_quantity_milli
                            - ProductionOrder.produced_quantity_milli,
                            0,
                        )
                    ),
                )
                .where(
                    ProductionOrder.organization_id == org_id,
                    ProductionOrder.deleted_at.is_(None),
                    ProductionOrder.status.in_(OPEN_PRODUCTION_STATUSES),
                    ProductionOrder.item_id.in_(chunk),
                )
                .group_by(ProductionOrder.item_id)
            )
        ).all():
            prod[item] += int(qty or 0)
    ids = set(on_hand) | set(po) | set(prod)
    return {i: domain.Supply(on_hand[i], min(reserved[i], on_hand[i]), po[i], prod[i]) for i in ids}


# ------------------------------------------------------------------ planning inputs: BOMs


async def load_boms(
    session: AsyncSession,
    org_id: uuid.UUID,
    roots: Iterable[uuid.UUID],
    on: date,
    choices: dict[uuid.UUID, uuid.UUID],
) -> dict[str, domain.MrpBom | None]:
    """The closure of BOMs in force on `on` under `roots`, a frontier at a time (three queries
    per BOM depth, not per item). An item with no live BOM, or none published in force on `on`,
    maps to `None` (planned as purchased). A component window narrows which lines apply. A
    substitute is used only where `choices[component] == substitute` and that line declares it."""
    out: dict[str, domain.MrpBom | None] = {}
    frontier = set(roots)
    while frontier:
        frontier = {i for i in frontier if str(i) not in out}
        if not frontier:
            break
        nxt: set[uuid.UUID] = set()
        for chunk in _chunks(frontier):
            boms = (
                (
                    await session.execute(
                        select(Bom).where(
                            Bom.organization_id == org_id,
                            Bom.deleted_at.is_(None),
                            Bom.is_active.is_(True),
                            Bom.item_id.in_(chunk),
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_item = {b.item_id: b for b in boms}
            versions = await mfg_selectors.versions_of(session, org_id, [b.id for b in boms])
            chosen = {
                b.item_id: v
                for b in boms
                if (v := mfg_selectors.current_version(versions.get(b.id, []), on)) is not None
            }
            comps = await mfg_selectors.components_of(
                session, org_id, [v.id for v in chosen.values()]
            )
            lines_by_item: dict[uuid.UUID, list[BomComponent]] = {}
            for item_id, version in chosen.items():
                lines_by_item[item_id] = [
                    c
                    for c in comps.get(version.id, [])
                    if mfg_domain.in_window(on, c.effective_from, c.effective_to)
                ]
            wanted_subs = [
                c.id
                for lines in lines_by_item.values()
                for c in lines
                if c.component_item_id in choices
            ]
            subs = await mfg_selectors.substitutes_of(session, org_id, wanted_subs)
            effective: dict[uuid.UUID, tuple[uuid.UUID, int]] = {}
            for lines in lines_by_item.values():
                for c in lines:
                    item, ratio = c.component_item_id, 1000
                    want = choices.get(c.component_item_id)
                    if want is not None:
                        match = next(
                            (s for s in subs.get(c.id, []) if s.substitute_item_id == want), None
                        )
                        if match is not None:
                            item, ratio = match.substitute_item_id, match.ratio_milli
                    effective[c.id] = (item, ratio)
            facts = await items_by_id(session, org_id, {i for i, _ in effective.values()})
            for item_id in chunk:
                in_force = chosen.get(item_id)
                bom = by_item.get(item_id)
                if bom is None or in_force is None:
                    out[str(item_id)] = None
                    continue
                mrp_lines = tuple(
                    domain.MrpLine(
                        component_item_id=str(effective[c.id][0]),
                        quantity_milli=c.quantity_milli,
                        scrap_bps=c.scrap_bps,
                        ratio_milli=effective[c.id][1],
                        discrete=is_discrete(facts[effective[c.id][0]].uom)
                        if effective[c.id][0] in facts
                        else False,
                    )
                    for c in lines_by_item[item_id]
                )
                out[str(item_id)] = domain.MrpBom(
                    str(item_id), in_force.output_quantity_milli, mrp_lines
                )
                nxt |= {uuid.UUID(ln.component_item_id) for ln in mrp_lines}
        frontier = nxt
    return out


async def declared_substitute_pairs(
    session: AsyncSession, org_id: uuid.UUID, pairs: Iterable[tuple[uuid.UUID, uuid.UUID]]
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """Which (component item, substitute item) pairs some BOM line really declares."""
    from app.manufacturing.models import BomSubstitute

    wanted = set(pairs)
    if not wanted:
        return set()
    rows = (
        await session.execute(
            select(BomComponent.component_item_id, BomSubstitute.substitute_item_id)
            .join(BomSubstitute, BomSubstitute.bom_component_id == BomComponent.id)
            .where(
                BomComponent.organization_id == org_id,
                BomSubstitute.organization_id == org_id,
                BomComponent.component_item_id.in_({c for c, _ in wanted}),
            )
        )
    ).all()
    return {(c, s) for c, s in rows} & wanted
