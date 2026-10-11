"""Shared set-up for the lot / roll / stock-operation tests: the textile organization from
`inventory_fixtures`, with the fabric switched to lot and roll tracking, a second bin in the same
warehouse, one lot, and one 82.5 m / 14.8 kg roll with its opening stock — all built through the
real service paths, so every test starts from stock the kernel itself posted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import services as inv_services
from app.inventory import stock_ops
from app.inventory import tracking_services as tracking
from app.inventory.reconcile import reconcile_organization
from app.inventory.tracking_schemas import LotIn, OpeningLineIn, OpeningStockIn, RollIn
from app.models import InventoryBalance, InventoryItem, Warehouse
from tests.helpers import principal_for
from tests.inventory_fixtures import Textile, build_textile

API = "/api/v1"

LOT_WRITE = "operations.inventory.lot.write:ORG"
ROLL_WRITE = "operations.inventory.roll.write:ORG"
STOCK_READ = "operations.inventory.stock.read:ORG"
STOCK_TRANSFER = "operations.inventory.stock.transfer:ORG"
STOCK_ADJUST = "operations.inventory.stock.adjust:ORG"
LOCATION_WRITE = "operations.inventory.location.write:ORG"
STOCK_RESERVE = "operations.inventory.stock.reserve:ORG"
ALL_PERMS = (
    LOT_WRITE,
    ROLL_WRITE,
    STOCK_READ,
    STOCK_TRANSFER,
    STOCK_ADJUST,
    LOCATION_WRITE,
    STOCK_RESERVE,
)


@dataclass
class World:
    t: Textile
    org_id: uuid.UUID
    #: A second bin in the weaving warehouse (same warehouse as `t.weaving`).
    bin_b: uuid.UUID
    warehouse_weave: uuid.UUID
    #: The finishing warehouse (`WH-FIN`), whose default bin is `t.finishing`.
    warehouse_fin: uuid.UUID
    lot: uuid.UUID
    roll: uuid.UUID  # 82.5 m / 14.8 kg, opened into `t.weaving`
    lot_no: str
    roll_no: str


def principal(org_id: uuid.UUID, *perms: str):  # type: ignore[no-untyped-def]
    return principal_for(org_id, list(perms))


async def track_fabric(session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> None:
    async with session.begin():
        await session.execute(
            update(InventoryItem)
            .where(InventoryItem.organization_id == org_id, InventoryItem.id == item_id)
            .values(track_lot=True, track_roll=True)
        )


async def build_world(session: AsyncSession, org_id: uuid.UUID, *, open_roll: bool = True) -> World:
    t = await build_textile(session, org_id)
    await track_fabric(session, org_id, t.fabric)
    async with session.begin():
        warehouse = (
            await session.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == org_id, Warehouse.code == "WH-WEAVE"
                )
            )
        ).scalar_one()
        bin_b = await inv_services.create_location(
            session, t.ctx, warehouse, kind="bin", code="BIN-B", name="Bin B"
        )
        lot = await tracking.create_lot(
            session, t.ctx, LotIn(inventory_item_id=t.fabric, lot_no="LOT-A", supplier_lot_no="S-1")
        )
        roll = await tracking.create_roll(
            session,
            t.ctx,
            RollIn(
                inventory_item_id=t.fabric,
                lot_id=lot.id,
                roll_no="R-1",
                primary_qty="82.5",
                secondary_qty="14.8",
                secondary_uom="kg",
                width_mm=1500,
                gsm=180,
                shade="NAVY",
                grade="A",
            ),
        )
        if open_roll:
            await stock_ops.post_opening_stock(
                session,
                t.ctx,
                OpeningStockIn(
                    lines=[
                        OpeningLineIn(
                            inventory_item_id=t.fabric,
                            location_id=t.weaving,
                            quantity="82.5",
                            lot_id=lot.id,
                            roll_id=roll.id,
                        )
                    ]
                ),
                idempotency_key=None,
                occurred_on_explicit=False,
            )
        bin_b_id = bin_b.id
        warehouse_id = warehouse.id
        warehouse_fin_id = (
            await session.execute(
                select(Warehouse.id).where(
                    Warehouse.organization_id == org_id, Warehouse.code == "WH-FIN"
                )
            )
        ).scalar_one()
    assert isinstance(bin_b_id, uuid.UUID)
    return World(
        t=t,
        org_id=org_id,
        bin_b=bin_b_id,
        warehouse_weave=warehouse_id,
        warehouse_fin=warehouse_fin_id,
        lot=lot.id,
        roll=roll.id,
        lot_no="LOT-A",
        roll_no="R-1",
    )


async def bucket_qty(
    session: AsyncSession,
    w: World,
    *,
    location: uuid.UUID | None = None,
    status: str = "AVAILABLE",
    lot: uuid.UUID | None = None,
    roll: uuid.UUID | None = None,
    item: uuid.UUID | None = None,
) -> tuple[int, int]:
    """(quantity_milli, reserved_milli) of one bucket — (0, 0) when it has no row."""
    async with session.begin():
        row = (
            await session.execute(
                select(InventoryBalance.quantity_milli, InventoryBalance.reserved_milli).where(
                    InventoryBalance.organization_id == w.org_id,
                    InventoryBalance.inventory_item_id == (item or w.t.fabric),
                    InventoryBalance.location_id == (location or w.t.weaving),
                    InventoryBalance.status == status,
                    InventoryBalance.lot_id.is_not_distinct_from(lot),
                    InventoryBalance.roll_id.is_not_distinct_from(roll),
                )
            )
        ).one_or_none()
    return (row[0], row[1]) if row else (0, 0)


async def assert_reconciled(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Zero drift: every balance equals the sum of its ledger, every reserved figure its
    reservations."""
    async with session.begin():
        report = await reconcile_organization(session, org_id)
    assert report.clean, report.findings


async def make_foreign_world(admin_session: AsyncSession) -> World:
    """A complete second tenant — items, locations, a lot, a rolled-in stock — built through the
    BYPASSRLS session, so a test can name its ids from the first tenant's side."""
    return await build_world(admin_session, uuid.uuid4())
