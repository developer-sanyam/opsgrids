"""Reads for receiving (inventory Phase 2): requisitions, goods receipts, the receiving view of a
purchase order, and the per-item unit conversions.

Every query filters `organization_id` explicitly (RLS is the net, not permission to skip the
filter — CLAUDE.md), and a row named by id that is not this organization's is simply "not found".
Requisitions are the one resource here with an OWN reading (the ones I raised), applied through
`ctx.apply`; receipts and conversions are org-wide reference/records.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import BigInteger, Select, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import quantity
from app.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryItem,
    Location,
    Lot,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    PurchaseRequisitionLine,
    Roll,
    Supplier,
    Uom,
    UomConversion,
    Warehouse,
)
from app.receiving import domain
from app.receiving.schemas import (
    GoodsReceiptLineOut,
    GoodsReceiptOut,
    GoodsReceiptSummaryOut,
    ReceiptRollOut,
    ReceivingContextOut,
    ReceivingConversionOut,
    ReceivingLineOut,
    RequisitionLineOut,
    RequisitionOut,
    RequisitionSummaryOut,
    UomConversionOut,
)
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ unit conversions


async def conversions_for_items(
    session: AsyncSession, org_id: uuid.UUID, item_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, dict[str, tuple[int, int]]]:
    """`{item_id: {canonical uom code: (numerator, denominator)}}` — the ratios `to_stock_milli`
    consumes. One query for every item asked about."""
    wanted = set(item_ids)
    if not wanted:
        return {}
    rows = await session.execute(
        select(
            UomConversion.inventory_item_id,
            Uom.code,
            UomConversion.numerator,
            UomConversion.denominator,
        )
        .join(Uom, Uom.id == UomConversion.uom_id)
        .where(
            UomConversion.organization_id == org_id,
            UomConversion.inventory_item_id.in_(wanted),
            Uom.organization_id == org_id,
        )
    )
    out: dict[uuid.UUID, dict[str, tuple[int, int]]] = {}
    for item_id, code, numerator, denominator in rows:
        out.setdefault(item_id, {})[quantity.canonical_uom_code(code)] = (numerator, denominator)
    return out


def conversion_out(row: UomConversion, uom: Uom, item: InventoryItem) -> UomConversionOut:
    return UomConversionOut(
        id=str(row.id),
        inventory_item_id=str(row.inventory_item_id),
        uom_id=str(row.uom_id),
        uom=uom.code,
        numerator=row.numerator,
        denominator=row.denominator,
        stock_uom=item.uom,
        created_at=row.created_at,
    )


async def list_conversions(
    session: AsyncSession, org_id: uuid.UUID, item: InventoryItem
) -> list[UomConversionOut]:
    rows = await session.execute(
        select(UomConversion, Uom)
        .join(Uom, Uom.id == UomConversion.uom_id)
        .where(
            UomConversion.organization_id == org_id,
            UomConversion.inventory_item_id == item.id,
            Uom.organization_id == org_id,
        )
        .order_by(Uom.code)
    )
    return [conversion_out(c, u, item) for c, u in rows]


async def get_conversion(
    session: AsyncSession, org_id: uuid.UUID, conversion_id: uuid.UUID, *, for_update: bool = False
) -> tuple[UomConversion, Uom, InventoryItem] | None:
    stmt = (
        select(UomConversion, Uom, InventoryItem)
        .join(Uom, Uom.id == UomConversion.uom_id)
        .join(InventoryItem, InventoryItem.id == UomConversion.inventory_item_id)
        .where(
            UomConversion.id == conversion_id,
            UomConversion.organization_id == org_id,
            Uom.organization_id == org_id,
            InventoryItem.organization_id == org_id,
            InventoryItem.deleted_at.is_(None),
        )
    )
    if for_update:
        # the item too: receipts lock it before reading ratios, so a change waits for them
        stmt = stmt.with_for_update(of=(UomConversion, InventoryItem))
    row = (await session.execute(stmt)).one_or_none()
    return (row[0], row[1], row[2]) if row else None


# ------------------------------------------------------------------ requisitions


def requisitions_stmt(
    ctx: ScopeContext,
    *,
    status: str | None = None,
    q: str | None = None,
    requested_by_id: uuid.UUID | None = None,
) -> Select[Any]:
    stmt = select(PurchaseRequisition).where(
        PurchaseRequisition.organization_id == ctx.organization_id,
        PurchaseRequisition.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=PurchaseRequisition.requested_by_id)
    if status:
        stmt = stmt.where(PurchaseRequisition.status == status)
    if requested_by_id is not None:
        stmt = stmt.where(PurchaseRequisition.requested_by_id == requested_by_id)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(PurchaseRequisition.number).like(needle),
                func.lower(PurchaseRequisition.requested_by).like(needle),
            )
        )
    return stmt.order_by(PurchaseRequisition.created_at.desc(), PurchaseRequisition.id.desc())


async def get_requisition(
    session: AsyncSession, org_id: uuid.UUID, requisition_id: uuid.UUID, *, for_update: bool = False
) -> PurchaseRequisition | None:
    stmt = select(PurchaseRequisition).where(
        PurchaseRequisition.id == requisition_id,
        PurchaseRequisition.organization_id == org_id,
        PurchaseRequisition.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def requisition_lines(
    session: AsyncSession, org_id: uuid.UUID, requisition_id: uuid.UUID
) -> list[PurchaseRequisitionLine]:
    rows = await session.execute(
        select(PurchaseRequisitionLine)
        .where(
            PurchaseRequisitionLine.organization_id == org_id,
            PurchaseRequisitionLine.requisition_id == requisition_id,
            PurchaseRequisitionLine.deleted_at.is_(None),
        )
        .order_by(PurchaseRequisitionLine.line_no)
    )
    return list(rows.scalars().all())


async def _warehouse_names(
    session: AsyncSession, org_id: uuid.UUID, ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await session.execute(
        select(Warehouse.id, Warehouse.name).where(
            Warehouse.organization_id == org_id, Warehouse.id.in_(wanted)
        )
    )
    return dict(rows.all())  # type: ignore[arg-type]


async def _po_numbers(
    session: AsyncSession, org_id: uuid.UUID, ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await session.execute(
        select(PurchaseOrder.id, PurchaseOrder.number).where(
            PurchaseOrder.organization_id == org_id, PurchaseOrder.id.in_(wanted)
        )
    )
    return dict(rows.all())  # type: ignore[arg-type]


async def requisition_out(
    session: AsyncSession, org_id: uuid.UUID, req: PurchaseRequisition
) -> RequisitionOut:
    lines = await requisition_lines(session, org_id, req.id)
    items = {
        i.id: i
        for i in (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.organization_id == org_id,
                    InventoryItem.id.in_({ln.inventory_item_id for ln in lines}),
                )
            )
        )
        .scalars()
        .all()
    }
    warehouses = await _warehouse_names(session, org_id, [req.warehouse_id])
    pos = await _po_numbers(session, org_id, [req.converted_po_id])
    return RequisitionOut(
        id=str(req.id),
        number=req.number,
        status=req.status,
        requested_by=req.requested_by,
        requested_by_id=str(req.requested_by_id) if req.requested_by_id else None,
        warehouse_id=str(req.warehouse_id) if req.warehouse_id else None,
        warehouse_name=warehouses.get(req.warehouse_id) if req.warehouse_id else None,
        needed_by=req.needed_by,
        reason=req.reason,
        decision_note=req.decision_note,
        submitted_at=req.submitted_at,
        decided_at=req.decided_at,
        converted_po_id=str(req.converted_po_id) if req.converted_po_id else None,
        converted_po_number=pos.get(req.converted_po_id) if req.converted_po_id else None,
        created_at=req.created_at,
        updated_at=req.updated_at,
        lines=[
            RequisitionLineOut(
                id=str(ln.id),
                line_no=ln.line_no,
                inventory_item_id=str(ln.inventory_item_id),
                sku=items[ln.inventory_item_id].sku,
                item_name=items[ln.inventory_item_id].name,
                uom=items[ln.inventory_item_id].uom,
                quantity=quantity.format_qty(ln.quantity_milli),
                suggested_supplier_id=(
                    str(ln.suggested_supplier_id) if ln.suggested_supplier_id else None
                ),
                est_unit_price_minor=ln.est_unit_price_minor,
                note=ln.note,
            )
            for ln in lines
        ],
    )


async def requisition_summaries(
    session: AsyncSession, org_id: uuid.UUID, rows: Sequence[PurchaseRequisition]
) -> list[RequisitionSummaryOut]:
    ids = [r.id for r in rows]
    counts: dict[uuid.UUID, int] = {}
    if ids:
        counts = dict(
            (
                await session.execute(
                    select(PurchaseRequisitionLine.requisition_id, func.count())
                    .where(
                        PurchaseRequisitionLine.organization_id == org_id,
                        PurchaseRequisitionLine.requisition_id.in_(ids),
                        PurchaseRequisitionLine.deleted_at.is_(None),
                    )
                    .group_by(PurchaseRequisitionLine.requisition_id)
                )
            ).all()  # type: ignore[arg-type]
        )
    warehouses = await _warehouse_names(session, org_id, [r.warehouse_id for r in rows])
    pos = await _po_numbers(session, org_id, [r.converted_po_id for r in rows])
    return [
        RequisitionSummaryOut(
            id=str(r.id),
            number=r.number,
            status=r.status,
            requested_by=r.requested_by,
            warehouse_name=warehouses.get(r.warehouse_id) if r.warehouse_id else None,
            needed_by=r.needed_by,
            line_count=counts.get(r.id, 0),
            converted_po_number=pos.get(r.converted_po_id) if r.converted_po_id else None,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ------------------------------------------------------------------ goods receipts


async def get_receipt(
    session: AsyncSession, org_id: uuid.UUID, receipt_id: uuid.UUID
) -> GoodsReceipt | None:
    return (
        await session.execute(
            select(GoodsReceipt).where(
                GoodsReceipt.id == receipt_id, GoodsReceipt.organization_id == org_id
            )
        )
    ).scalar_one_or_none()


async def get_receipt_by_key(
    session: AsyncSession, org_id: uuid.UUID, key: str
) -> GoodsReceipt | None:
    return (
        await session.execute(
            select(GoodsReceipt).where(
                GoodsReceipt.organization_id == org_id, GoodsReceipt.idempotency_key == key
            )
        )
    ).scalar_one_or_none()


def receipts_stmt(
    ctx: ScopeContext,
    *,
    purchase_order_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
    q: str | None = None,
) -> Select[Any]:
    stmt = select(GoodsReceipt).where(GoodsReceipt.organization_id == ctx.organization_id)
    stmt = ctx.apply(stmt, owner_col=None)
    if purchase_order_id is not None:
        stmt = stmt.where(GoodsReceipt.purchase_order_id == purchase_order_id)
    if supplier_id is not None:
        stmt = stmt.where(GoodsReceipt.supplier_id == supplier_id)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(GoodsReceipt.number).like(needle),
                func.lower(func.coalesce(GoodsReceipt.delivery_note, "")).like(needle),
            )
        )
    return stmt


async def receipt_summaries(
    session: AsyncSession, org_id: uuid.UUID, rows: Sequence[GoodsReceipt]
) -> list[GoodsReceiptSummaryOut]:
    if not rows:
        return []
    po_ids = {r.purchase_order_id for r in rows}
    sup_ids = {r.supplier_id for r in rows}
    pos = await _po_numbers(session, org_id, po_ids)
    suppliers: dict[uuid.UUID, str] = dict(
        (
            await session.execute(
                select(Supplier.id, Supplier.name).where(
                    Supplier.organization_id == org_id, Supplier.id.in_(sup_ids)
                )
            )
        ).all()  # type: ignore[arg-type]
    )
    counts: dict[uuid.UUID, int] = dict(
        (
            await session.execute(
                select(GoodsReceiptLine.goods_receipt_id, func.count())
                .where(
                    GoodsReceiptLine.organization_id == org_id,
                    GoodsReceiptLine.goods_receipt_id.in_([r.id for r in rows]),
                )
                .group_by(GoodsReceiptLine.goods_receipt_id)
            )
        ).all()  # type: ignore[arg-type]
    )
    return [
        GoodsReceiptSummaryOut(
            id=str(r.id),
            number=r.number,
            purchase_order_id=str(r.purchase_order_id),
            po_number=pos.get(r.purchase_order_id, ""),
            supplier_name=suppliers.get(r.supplier_id, ""),
            received_on=r.received_on,
            delivery_note=r.delivery_note,
            line_count=counts.get(r.id, 0),
            created_at=r.created_at,
        )
        for r in rows
    ]


def _format_optional(milli: int | None) -> str | None:
    return None if milli is None else quantity.format_qty(milli)


async def receipt_out(
    session: AsyncSession, org_id: uuid.UUID, receipt: GoodsReceipt, *, replayed: bool = False
) -> GoodsReceiptOut:
    """A receipt with its lines, every id resolved to something a person can read — one query per
    related table for the whole receipt, not per line."""
    lines = list(
        (
            await session.execute(
                select(GoodsReceiptLine)
                .where(
                    GoodsReceiptLine.organization_id == org_id,
                    GoodsReceiptLine.goods_receipt_id == receipt.id,
                )
                .order_by(GoodsReceiptLine.line_no)
            )
        )
        .scalars()
        .all()
    )
    po = (
        await session.execute(
            select(PurchaseOrder).where(
                PurchaseOrder.organization_id == org_id,
                PurchaseOrder.id == receipt.purchase_order_id,
            )
        )
    ).scalar_one()
    supplier_name = (
        await session.execute(
            select(Supplier.name).where(
                Supplier.organization_id == org_id, Supplier.id == receipt.supplier_id
            )
        )
    ).scalar_one_or_none() or ""
    po_lines = {
        pl.id: pl
        for pl in (
            await session.execute(
                select(PurchaseOrderLine).where(
                    PurchaseOrderLine.organization_id == org_id,
                    PurchaseOrderLine.id.in_({ln.purchase_order_line_id for ln in lines}),
                )
            )
        )
        .scalars()
        .all()
    }
    item_ids = {ln.inventory_item_id for ln in lines if ln.inventory_item_id}
    items = {
        i.id: i
        for i in (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.organization_id == org_id, InventoryItem.id.in_(item_ids)
                )
            )
        )
        .scalars()
        .all()
    }
    location_ids = {ln.location_id for ln in lines if ln.location_id}
    locations: dict[uuid.UUID, str] = {}
    if location_ids:
        locations = {
            lid: f"{wh}/{code}"
            for lid, wh, code in await session.execute(
                select(Location.id, Warehouse.code, Location.code)
                .join(Warehouse, Warehouse.id == Location.warehouse_id)
                .where(Location.organization_id == org_id, Location.id.in_(location_ids))
            )
        }
    lot_ids = {ln.lot_id for ln in lines if ln.lot_id}
    lot_nos: dict[uuid.UUID, str] = {}
    if lot_ids:
        lot_nos = dict(
            (
                await session.execute(
                    select(Lot.id, Lot.lot_no).where(
                        Lot.organization_id == org_id, Lot.id.in_(lot_ids)
                    )
                )
            ).all()  # type: ignore[arg-type]
        )
    roll_ids = {uuid.UUID(r) for ln in lines for r in ln.roll_ids}
    rolls: dict[uuid.UUID, Roll] = {}
    if roll_ids:
        rolls = {
            r.id: r
            for r in (
                await session.execute(
                    select(Roll).where(Roll.organization_id == org_id, Roll.id.in_(roll_ids))
                )
            )
            .scalars()
            .all()
        }
    return GoodsReceiptOut(
        id=str(receipt.id),
        number=receipt.number,
        purchase_order_id=str(receipt.purchase_order_id),
        po_number=po.number,
        po_status=po.status,
        supplier_id=str(receipt.supplier_id),
        supplier_name=supplier_name,
        received_on=receipt.received_on,
        delivery_note=receipt.delivery_note,
        note=receipt.note,
        status="POSTED",
        replayed=replayed,
        created_at=receipt.created_at,
        created_by=receipt.created_by,
        lines=[
            GoodsReceiptLineOut(
                id=str(ln.id),
                line_no=ln.line_no,
                purchase_order_line_id=str(ln.purchase_order_line_id),
                sku=po_lines[ln.purchase_order_line_id].sku,
                description=po_lines[ln.purchase_order_line_id].description,
                inventory_item_id=str(ln.inventory_item_id) if ln.inventory_item_id else None,
                stock_uom=items[ln.inventory_item_id].uom if ln.inventory_item_id else None,
                location_id=str(ln.location_id) if ln.location_id else None,
                location_label=locations.get(ln.location_id) if ln.location_id else None,
                stock_status=ln.stock_status,
                lot_id=str(ln.lot_id) if ln.lot_id else None,
                lot_no=lot_nos.get(ln.lot_id) if ln.lot_id else None,
                supplier_lot_no=ln.supplier_lot_no,
                rolls=[
                    ReceiptRollOut(
                        id=r,
                        roll_no=rolls[uuid.UUID(r)].roll_no,
                        primary_qty=quantity.format_qty(rolls[uuid.UUID(r)].primary_milli),
                        secondary_qty=_format_optional(rolls[uuid.UUID(r)].secondary_milli),
                        secondary_uom=rolls[uuid.UUID(r)].secondary_uom,
                    )
                    for r in ln.roll_ids
                    if uuid.UUID(r) in rolls
                ],
                entered_qty=quantity.format_qty(ln.entered_milli),
                entered_uom=ln.entered_uom,
                received_qty=quantity.format_qty(ln.received_milli),
                ordered_qty=quantity.format_qty(ln.ordered_milli),
                prior_received_qty=quantity.format_qty(ln.prior_received_milli),
                cumulative_qty=quantity.format_qty(ln.cumulative_milli),
                tolerance_pct=domain.format_tolerance_pct(ln.tolerance_bp),
                close_short=ln.close_short,
                outcome=ln.outcome,
                unit_cost_minor=ln.unit_cost_minor,
                unit_cost_currency=ln.unit_cost_currency,
            )
            for ln in lines
        ],
    )


# ------------------------------------------------------------------ the receiving view of a PO


def ordered_milli(line: PurchaseOrderLine, stock_uom: str, ratios: domain.Ratios) -> int:
    """A PO line's ordered quantity in the item's stock unit. A line priced and ordered in a unit
    with no known relation to the stock unit (an unregistered one a legacy PO used) is taken to be
    in the stock unit already — exactly what the single-shot receipt always assumed."""
    base = line.quantity * quantity.QTY_SCALE
    if not line.uom.strip():
        return base
    try:
        return domain.to_stock_milli(base, line.uom, stock_uom, ratios)
    except quantity.QuantityError:
        return base


async def receiving_context(
    session: AsyncSession, org_id: uuid.UUID, po: PurchaseOrder
) -> ReceivingContextOut:
    lines = list(
        (
            await session.execute(
                select(PurchaseOrderLine)
                .where(
                    PurchaseOrderLine.organization_id == org_id,
                    PurchaseOrderLine.purchase_order_id == po.id,
                    PurchaseOrderLine.deleted_at.is_(None),
                )
                .order_by(PurchaseOrderLine.created_at, PurchaseOrderLine.id)
            )
        )
        .scalars()
        .all()
    )
    skus = {ln.sku for ln in lines}
    items = {
        i.sku: i
        for i in (
            await session.execute(
                select(InventoryItem).where(
                    InventoryItem.organization_id == org_id,
                    InventoryItem.sku.in_(skus),
                    InventoryItem.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    ratios = await conversions_for_items(session, org_id, [i.id for i in items.values()])
    homes = {i.home_location_id for i in items.values() if i.home_location_id}
    labels: dict[uuid.UUID, str] = {}
    if homes:
        labels = {
            lid: f"{wh}/{code}"
            for lid, wh, code in await session.execute(
                select(Location.id, Warehouse.code, Location.code)
                .join(Warehouse, Warehouse.id == Location.warehouse_id)
                .where(Location.organization_id == org_id, Location.id.in_(homes))
            )
        }
    supplier_name = (
        await session.execute(
            select(Supplier.name).where(
                Supplier.organization_id == org_id, Supplier.id == po.supplier_id
            )
        )
    ).scalar_one_or_none() or po.supplier_name

    out: list[ReceivingLineOut] = []
    for ln in lines:
        item = items.get(ln.sku)
        stock_uom = item.uom if item else ln.uom
        item_ratios = ratios.get(item.id, {}) if item else {}
        ordered = ordered_milli(ln, stock_uom, item_ratios)
        tolerance = item.receipt_tolerance_bp if item else 0
        limit = domain.tolerance_limit_milli(ordered, tolerance)
        closed = ln.short_closed
        out.append(
            ReceivingLineOut(
                purchase_order_line_id=str(ln.id),
                sku=ln.sku,
                description=ln.description,
                inventory_item_id=str(item.id) if item else None,
                stock_uom=stock_uom or None,
                ordered_qty=quantity.format_qty(ordered),
                received_qty=quantity.format_qty(ln.received_milli),
                remaining_qty=quantity.format_qty(max(0, ordered - ln.received_milli)),
                max_receivable_qty=quantity.format_qty(
                    0 if closed else max(0, limit - ln.received_milli)
                ),
                tolerance_pct=domain.format_tolerance_pct(tolerance),
                short_closed=closed,
                track_lot=bool(item and item.track_lot),
                track_roll=bool(item and item.track_roll),
                track_expiry=bool(item and item.track_expiry),
                track_quality=bool(item and item.track_quality),
                default_location_id=(
                    str(item.home_location_id) if item and item.home_location_id else None
                ),
                default_location_label=(
                    labels.get(item.home_location_id) if item and item.home_location_id else None
                ),
                conversions=[
                    ReceivingConversionOut(uom=code, numerator=n, denominator=d)
                    for code, (n, d) in sorted(item_ratios.items())
                ],
            )
        )
    return ReceivingContextOut(
        purchase_order_id=str(po.id),
        po_number=po.number,
        po_status=po.status,
        warehouse=po.warehouse,
        supplier_name=supplier_name,
        can_receive=po.status in ("approved", "partially_received"),
        lines=out,
    )


async def next_sequence(session: AsyncSession, model: Any, org_id: uuid.UUID, prefix: str) -> int:
    """Next number for `{prefix}{seq}`: the max compared as an integer, because as text "9999"
    sorts above "10000". Call under the per-organization advisory lock."""
    current = (
        await session.execute(
            select(func.max(cast(func.substr(model.number, len(prefix) + 1), BigInteger))).where(
                model.organization_id == org_id, model.number.op("~")(f"^{prefix}[0-9]+$")
            )
        )
    ).scalar_one_or_none()
    return (current or 0) + 1
