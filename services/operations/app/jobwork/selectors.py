"""Reads for job work (Inventory plan Phase 6). Every query filters on `organization_id`
explicitly — RLS is the net, not the filter (CLAUDE.md) — and every single-row read by id returns
`None` for another tenant's row, which the router turns into a 404 that never says the row exists
elsewhere.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.quantity import format_qty
from app.jobwork import domain
from app.jobwork.schemas import (
    ChallanLineOut,
    ChallanOut,
    JobWorkMaterialOut,
    JobWorkOrderDetailOut,
    JobWorkOrderOut,
    JobWorkReceiptOut,
    ReconciliationDriftOut,
    ReconciliationLineOut,
    ReconciliationOut,
)
from app.models import (
    Challan,
    InventoryItem,
    InventoryMove,
    JobWorkMaterial,
    JobWorkOrder,
    JobWorkReceipt,
    LedgerEntry,
    Supplier,
)

REFERENCE_TYPE = "job_work_order"

# ------------------------------------------------------------------ orders


def orders_stmt(
    org_id: uuid.UUID,
    *,
    status: str | None = None,
    supplier_id: uuid.UUID | None = None,
    overdue_on: date | None = None,
    search: str | None = None,
) -> Select[Any]:
    """`overdue_on` (a date, normally today) narrows to orders still at the vendor whose due date
    is before it — the same test `domain.is_overdue` applies per row, here in SQL."""
    stmt = select(JobWorkOrder).where(
        JobWorkOrder.organization_id == org_id, JobWorkOrder.deleted_at.is_(None)
    )
    if status is not None:
        stmt = stmt.where(JobWorkOrder.status == status)
    if supplier_id is not None:
        stmt = stmt.where(JobWorkOrder.supplier_id == supplier_id)
    if overdue_on is not None:
        stmt = stmt.where(
            JobWorkOrder.status.in_(("ISSUED", "PARTIALLY_RECEIVED")),
            JobWorkOrder.due_date.is_not(None),
            JobWorkOrder.due_date < overdue_on,
        )
    if search:
        stmt = stmt.where(JobWorkOrder.number.ilike(f"%{search.strip()}%"))
    return stmt.order_by(JobWorkOrder.created_at.desc(), JobWorkOrder.id)


async def get_order(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID, *, for_update: bool = False
) -> JobWorkOrder | None:
    stmt = select(JobWorkOrder).where(
        JobWorkOrder.id == order_id,
        JobWorkOrder.organization_id == org_id,
        JobWorkOrder.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_order_by_key(
    session: AsyncSession, org_id: uuid.UUID, idempotency_key: str
) -> JobWorkOrder | None:
    stmt = select(JobWorkOrder).where(
        JobWorkOrder.organization_id == org_id, JobWorkOrder.idempotency_key == idempotency_key
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ materials


async def materials_for(
    session: AsyncSession,
    org_id: uuid.UUID,
    order_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> list[JobWorkMaterial]:
    """Ordered by id, always — the lock order every writer of materials uses, so two receipts that
    touch overlapping materials queue instead of deadlocking."""
    stmt = (
        select(JobWorkMaterial)
        .where(
            JobWorkMaterial.organization_id == org_id,
            JobWorkMaterial.order_id == order_id,
            JobWorkMaterial.deleted_at.is_(None),
        )
        .order_by(JobWorkMaterial.id)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    rows = list((await session.execute(stmt)).scalars().all())
    return sorted(rows, key=lambda m: m.line_no) if not for_update else rows


async def order_totals(
    session: AsyncSession, org_id: uuid.UUID, order_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int, int]]:
    """`order_id -> (material_count, sent_milli, outstanding_milli)` for a page of orders — one
    grouped query, never one per row. Mixed units add up only as a count of milli, which is why
    the list shows these as plain figures, not as a quantity in any one unit."""
    if not order_ids:
        return {}
    stmt = (
        select(
            JobWorkMaterial.order_id,
            func.count(),
            func.coalesce(func.sum(JobWorkMaterial.sent_milli), 0),
            func.coalesce(
                func.sum(
                    JobWorkMaterial.sent_milli
                    - JobWorkMaterial.received_milli
                    - JobWorkMaterial.rejected_milli
                    - JobWorkMaterial.shortage_milli
                ),
                0,
            ),
        )
        .where(
            JobWorkMaterial.organization_id == org_id,
            JobWorkMaterial.order_id.in_(order_ids),
            JobWorkMaterial.deleted_at.is_(None),
        )
        .group_by(JobWorkMaterial.order_id)
    )
    return {
        row[0]: (int(row[1]), int(row[2]), int(row[3]))
        for row in (await session.execute(stmt)).all()
    }


# ------------------------------------------------------------------ receipts and challans


async def receipts_for(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> list[JobWorkReceipt]:
    stmt = (
        select(JobWorkReceipt)
        .where(JobWorkReceipt.organization_id == org_id, JobWorkReceipt.order_id == order_id)
        .order_by(JobWorkReceipt.created_at, JobWorkReceipt.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def receipts_by_key(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID, idempotency_key: str
) -> list[JobWorkReceipt]:
    stmt = (
        select(JobWorkReceipt)
        .where(
            JobWorkReceipt.organization_id == org_id,
            JobWorkReceipt.order_id == order_id,
            JobWorkReceipt.idempotency_key == idempotency_key,
        )
        .order_by(JobWorkReceipt.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def challans_for(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> list[Challan]:
    stmt = (
        select(Challan)
        .where(
            Challan.organization_id == org_id,
            Challan.order_id == order_id,
            Challan.deleted_at.is_(None),
        )
        .order_by(Challan.issued_at, Challan.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_challan(
    session: AsyncSession, org_id: uuid.UUID, challan_id: uuid.UUID, *, for_update: bool = False
) -> Challan | None:
    stmt = select(Challan).where(
        Challan.id == challan_id, Challan.organization_id == org_id, Challan.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_challan_by_key(
    session: AsyncSession, org_id: uuid.UUID, idempotency_key: str
) -> Challan | None:
    stmt = select(Challan).where(
        Challan.organization_id == org_id, Challan.idempotency_key == idempotency_key
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ names


async def supplier_names(
    session: AsyncSession, org_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    wanted = set(ids)
    if not wanted:
        return {}
    stmt = select(Supplier.id, Supplier.name).where(
        Supplier.organization_id == org_id, Supplier.id.in_(wanted)
    )
    return {row.id: row.name for row in (await session.execute(stmt)).all()}


async def get_supplier(
    session: AsyncSession, org_id: uuid.UUID, supplier_id: uuid.UUID
) -> Supplier | None:
    stmt = select(Supplier).where(
        Supplier.id == supplier_id,
        Supplier.organization_id == org_id,
        Supplier.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def items_by_id(
    session: AsyncSession, org_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, InventoryItem]:
    wanted = set(ids)
    if not wanted:
        return {}
    stmt = select(InventoryItem).where(
        InventoryItem.organization_id == org_id,
        InventoryItem.id.in_(wanted),
        InventoryItem.deleted_at.is_(None),
    )
    return {item.id: item for item in (await session.execute(stmt)).scalars().all()}


# ------------------------------------------------------------------ reconciliation


async def ledger_at_vendor(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> dict[str, int]:
    """Per item, the net of the `AT_JOB_WORKER` ledger rows posted under this order — positive
    while stock is out at the vendor. It is a sum over the **append-only ledger**, not a read of
    a balance: balances are shared by every order that sends the same item from the same bin."""
    stmt = (
        select(
            LedgerEntry.inventory_item_id, func.coalesce(func.sum(LedgerEntry.quantity_milli), 0)
        )
        .join(InventoryMove, InventoryMove.id == LedgerEntry.move_id)
        .where(
            LedgerEntry.organization_id == org_id,
            InventoryMove.organization_id == org_id,
            InventoryMove.reference_type == REFERENCE_TYPE,
            InventoryMove.reference_id == order_id,
            LedgerEntry.status == "AT_JOB_WORKER",
        )
        .group_by(LedgerEntry.inventory_item_id)
    )
    return {str(row[0]): int(row[1]) for row in (await session.execute(stmt)).all()}


def figures(materials: Sequence[JobWorkMaterial]) -> list[domain.MaterialFigures]:
    return [
        domain.MaterialFigures(
            item_id=str(m.item_id),
            sent=m.sent_milli,
            received=m.received_milli,
            rejected=m.rejected_milli,
            shortage=m.shortage_milli,
        )
        for m in materials
    ]


async def reconciliation(
    session: AsyncSession,
    org_id: uuid.UUID,
    order: JobWorkOrder,
    materials: Sequence[JobWorkMaterial],
) -> tuple[domain.ReconciliationResult, dict[str, int]]:
    ledger = await ledger_at_vendor(session, org_id, order.id)
    return domain.reconcile(figures(materials), ledger), ledger


# ------------------------------------------------------------------ serialisers


def material_out(m: JobWorkMaterial) -> JobWorkMaterialOut:
    return JobWorkMaterialOut(
        id=str(m.id),
        line_no=m.line_no,
        item_id=str(m.item_id),
        sku=m.sku,
        uom=m.uom,
        lot_id=str(m.lot_id) if m.lot_id else None,
        roll_id=str(m.roll_id) if m.roll_id else None,
        location_id=str(m.location_id),
        quantity=format_qty(m.quantity_milli),
        sent=format_qty(m.sent_milli),
        received=format_qty(m.received_milli),
        rejected=format_qty(m.rejected_milli),
        shortage=format_qty(m.shortage_milli),
        outstanding=format_qty(
            m.sent_milli - m.received_milli - m.rejected_milli - m.shortage_milli
        ),
        unit_value_minor=m.unit_value_minor,
        currency=m.currency,
    )


def receipt_out(r: JobWorkReceipt, *, sku: str) -> JobWorkReceiptOut:
    return JobWorkReceiptOut(
        id=str(r.id),
        receipt_no=r.receipt_no,
        material_id=str(r.material_id),
        sku=sku,
        received=format_qty(r.received_milli),
        rejected=format_qty(r.rejected_milli),
        shortage=format_qty(r.shortage_milli),
        location_id=str(r.location_id),
        good_status=r.good_status,
        qc_handoff=r.qc_handoff,
        received_on=r.received_on,
        note=r.note,
        created_by=r.created_by,
        created_at=r.created_at,
    )


def challan_out(c: Challan, *, order_number: str) -> ChallanOut:
    return ChallanOut(
        id=str(c.id),
        order_id=str(c.order_id),
        order_number=order_number,
        challan_number=c.challan_number,
        kind="DELIVERY",
        issued_at=c.issued_at,
        supplier_id=str(c.supplier_id),
        supplier_name=c.supplier_name,
        process=c.process,
        lines=[ChallanLineOut(**line) for line in c.lines],
        total_value_minor=c.total_value_minor,
        currency=c.currency,
        vehicle_no=c.vehicle_no,
        transporter_id=c.transporter_id,
        distance_km=c.distance_km,
        eway_status=c.eway_status,
        eway_bill_no=c.eway_bill_no,
        eway_requested_at=c.eway_requested_at,
    )


def reconciliation_out(
    order: JobWorkOrder,
    materials: Sequence[JobWorkMaterial],
    result: domain.ReconciliationResult,
    *,
    skus_by_item: dict[str, str],
) -> ReconciliationOut:
    return ReconciliationOut(
        order_id=str(order.id),
        order_status=order.status,
        issued=order.issued_at is not None,
        lines=[
            ReconciliationLineOut(
                material_id=str(m.id),
                sku=m.sku,
                uom=m.uom,
                sent=format_qty(m.sent_milli),
                received=format_qty(m.received_milli),
                rejected=format_qty(m.rejected_milli),
                shortage=format_qty(m.shortage_milli),
                outstanding=format_qty(
                    m.sent_milli - m.received_milli - m.rejected_milli - m.shortage_milli
                ),
            )
            for m in materials
        ],
        total_sent=format_qty(result.total_sent),
        total_received=format_qty(result.total_received),
        total_rejected=format_qty(result.total_rejected),
        total_shortage=format_qty(result.total_shortage),
        total_outstanding=format_qty(result.total_outstanding),
        documents_settled=result.documents_settled,
        ledger_consistent=result.ledger_consistent,
        drift=[
            ReconciliationDriftOut(
                item_id=item_id,
                sku=skus_by_item.get(item_id, ""),
                documents=format_qty(documents),
                ledger=format_qty(ledger),
            )
            for item_id, (documents, ledger) in sorted(result.drift.items())
        ],
        balanced=result.balanced and order.issued_at is not None,
    )


def order_out(
    order: JobWorkOrder,
    *,
    supplier_name: str,
    totals: tuple[int, int, int],
    today: date,
) -> JobWorkOrderOut:
    material_count, sent, outstanding = totals
    return JobWorkOrderOut(
        id=str(order.id),
        number=order.number,
        supplier_id=str(order.supplier_id),
        supplier_name=supplier_name,
        process=order.process,
        status=order.status,
        due_date=order.due_date,
        is_overdue=domain.is_overdue(order.status, order.due_date, today),
        days_overdue=domain.days_overdue(order.status, order.due_date, today),
        qc_required=order.qc_required,
        notes=order.notes,
        version=order.version,
        source_warehouse_id=str(order.source_warehouse_id) if order.source_warehouse_id else None,
        receive_location_id=str(order.receive_location_id) if order.receive_location_id else None,
        material_count=material_count,
        total_sent=format_qty(sent),
        total_outstanding=format_qty(outstanding),
        issued_at=order.issued_at,
        closed_at=order.closed_at,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


async def order_detail(
    session: AsyncSession, org_id: uuid.UUID, order: JobWorkOrder, *, today: date
) -> JobWorkOrderDetailOut:
    materials = await materials_for(session, org_id, order.id)
    receipts = await receipts_for(session, org_id, order.id)
    challans = await challans_for(session, org_id, order.id)
    names = await supplier_names(session, org_id, [order.supplier_id])
    result, _ledger = await reconciliation(session, org_id, order, materials)
    sku_by_material = {m.id: m.sku for m in materials}
    skus_by_item = {str(m.item_id): m.sku for m in materials}
    sent = sum(m.sent_milli for m in materials)
    outstanding = sum(
        m.sent_milli - m.received_milli - m.rejected_milli - m.shortage_milli for m in materials
    )
    base = order_out(
        order,
        supplier_name=names.get(order.supplier_id, ""),
        totals=(len(materials), sent, outstanding),
        today=today,
    )
    return JobWorkOrderDetailOut(
        **base.model_dump(),
        materials=[material_out(m) for m in materials],
        receipts=[receipt_out(r, sku=sku_by_material.get(r.material_id, "")) for r in receipts],
        challans=[challan_out(c, order_number=order.number) for c in challans],
        reconciliation=reconciliation_out(order, materials, result, skus_by_item=skus_by_item),
    )
