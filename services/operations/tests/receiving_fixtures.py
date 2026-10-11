"""Shared set-up for the receiving tests (inventory Phase 2): the textile organization from
`inventory_fixtures`, with a supplier and three items that exercise every receiving rule —

* `fabric` (metres): lot- and roll-tracked, 2 % tolerance — roll receipts and the limit;
* `dye` (kilograms): quality-tracked — receipts land in `QUALITY_HOLD`;
* `buttons` (pieces): `box = 12 pcs` conversion and a 5 % tolerance — exact-math receipts —

plus a second bin in the weaving warehouse and helpers to raise approved purchase orders through
the real service path. Everything is built through the service layer, so every test starts from
state the application itself produced.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors as po_selectors
from app import services as po_services
from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.models import InventoryItem, PurchaseOrderLine, Warehouse
from app.receiving import conversions
from app.receiving.schemas import UomConversionIn
from tests.inventory_fixtures import Textile, build_textile
from tests.tracking_fixtures import API

__all__ = ["API", "RWorld", "build_rworld", "make_po"]

REQ_READ = "operations.procurement.requisition.read:ORG"
REQ_READ_OWN = "operations.procurement.requisition.read:OWN"
REQ_WRITE = "operations.procurement.requisition.write:ORG"
REQ_WRITE_OWN = "operations.procurement.requisition.write:OWN"
REQ_APPROVE = "operations.procurement.requisition.approve:ORG"
RECEIPT_READ = "operations.procurement.receipt.read:ORG"
RECEIPT_WRITE = "operations.procurement.receipt.write:ORG"
STOCK_RECEIVE = "operations.inventory.stock.receive:ORG"
PO_READ = "operations.procurement.purchase_order.read:ORG"
PO_WRITE = "operations.procurement.purchase_order.write:ORG"
PO_APPROVE = "operations.procurement.purchase_order.approve:ORG"
ITEM_READ = "operations.inventory.item.read:ORG"
ITEM_WRITE = "operations.inventory.item.write:ORG"
STOCK_READ = "operations.inventory.stock.read:ORG"
RECEIVE_PERMS = (RECEIPT_WRITE, STOCK_RECEIVE)
ALL_RECEIVING_PERMS = (
    REQ_READ,
    REQ_WRITE,
    REQ_APPROVE,
    RECEIPT_READ,
    RECEIPT_WRITE,
    STOCK_RECEIVE,
    PO_READ,
    PO_WRITE,
    PO_APPROVE,
    ITEM_READ,
    ITEM_WRITE,
    STOCK_READ,
)


@dataclass
class RWorld:
    t: Textile
    org_id: uuid.UUID
    supplier_id: uuid.UUID
    bin_b: uuid.UUID
    box_conversion_id: uuid.UUID
    skus: dict[str, uuid.UUID] = field(default_factory=dict)  # sku -> item id

    @property
    def fabric(self) -> uuid.UUID:
        return self.t.fabric

    @property
    def dye(self) -> uuid.UUID:
        return self.t.dye

    @property
    def buttons(self) -> uuid.UUID:
        return self.t.buttons


def future(days: int = 7) -> date:
    return date.today() + timedelta(days=days)


async def build_rworld(session: AsyncSession, org_id: uuid.UUID) -> RWorld:
    t = await build_textile(session, org_id)
    async with session.begin():
        await session.execute(
            update(InventoryItem)
            .where(InventoryItem.organization_id == org_id, InventoryItem.id == t.fabric)
            .values(track_lot=True, track_roll=True, receipt_tolerance_bp=200)
        )
        await session.execute(
            update(InventoryItem)
            .where(InventoryItem.organization_id == org_id, InventoryItem.id == t.dye)
            .values(track_quality=True)
        )
        await session.execute(
            update(InventoryItem)
            .where(InventoryItem.organization_id == org_id, InventoryItem.id == t.buttons)
            .values(receipt_tolerance_bp=500)
        )
        warehouse = await inv_selectors.get_warehouse_by_name(session, org_id, "Weaving Store")
        assert isinstance(warehouse, Warehouse)
        bin_b = await inv_services.create_location(
            session, t.ctx, warehouse, kind="bin", code="BIN-B", name="Bin B"
        )
        supplier = await po_services.create_supplier(
            session,
            t.ctx,
            code="SUP-TEX",
            name="Textile Mills Ltd",
            category="raw_materials",
            status="active",
            currency="INR",
        )
        buttons = await inv_selectors.get_inventory_item(session, org_id, t.buttons)
        assert buttons is not None
        conversion, _uom = await conversions.create_conversion(
            session, t.ctx, buttons, UomConversionIn(uom="box", numerator=12, denominator=1)
        )
        bin_b_id, supplier_id, conversion_id = bin_b.id, supplier.id, conversion.id
    return RWorld(
        t=t,
        org_id=org_id,
        supplier_id=supplier_id,
        bin_b=bin_b_id,
        box_conversion_id=conversion_id,
        skus={"COT-180-NAVY": t.fabric, "DYE-RED-01": t.dye, "BTN-14MM": t.buttons},
    )


_counter = 0


async def make_po(
    session: AsyncSession,
    w: RWorld,
    lines: list[dict],
    *,
    status: str = "approved",
    currency: str = "INR",
    org_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """Raise a PO (default already approved — the approval path is `test_purchase_orders_write`'s)
    and return `(po_id, {sku: purchase_order_line_id})`. A line is
    `{"sku", "quantity" (int, in `uom`), "uom", "unit_price_minor"}`."""
    global _counter
    _counter += 1
    org = org_id or w.org_id
    async with session.begin():
        po = await po_services.create_purchase_order(
            session,
            w.t.ctx,
            number=f"PO-T-{uuid.uuid4().hex[:8].upper()}-{_counter}",
            supplier_id=w.supplier_id,
            status=status,
            warehouse="Weaving Store",
            raised_by="Tester",
            raised_by_id=uuid.uuid4(),
            currency=currency,
            created_date=date.today(),
            expected_date=future(),
            lines=lines,
        )
        po_lines = await po_selectors.lines_for(session, org, po.id)
        by_sku = {ln.sku: ln.id for ln in po_lines}
        po_id = po.id
    return po_id, by_sku


async def po_line_state(
    session: AsyncSession, org_id: uuid.UUID, line_id: uuid.UUID
) -> tuple[int, int, bool]:
    """`(received_milli, received_qty, short_closed)` of a PO line, read fresh."""
    from sqlalchemy import select

    async with session.begin():
        row = (
            await session.execute(
                select(
                    PurchaseOrderLine.received_milli,
                    PurchaseOrderLine.received_qty,
                    PurchaseOrderLine.short_closed,
                ).where(
                    PurchaseOrderLine.organization_id == org_id, PurchaseOrderLine.id == line_id
                )
            )
        ).one()
    return row[0], row[1], row[2]
