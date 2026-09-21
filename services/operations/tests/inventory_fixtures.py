"""Shared set-up for the ledger tests: a small textile organization built through the real
write paths, and the helpers that read its stock back from the database.

Plain module (not conftest) for the reason `helpers.py` gives: importing *from* conftest is
fragile, importing from here is not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.inventory.quantity import parse_qty
from app.models import InventoryBalance, LedgerEntry
from platform_core.auth import Scope, ScopeContext
from tests.helpers import ctx_for

TODAY = date(2026, 9, 22)


@dataclass
class Textile:
    org_id: uuid.UUID
    ctx: ScopeContext
    fabric: uuid.UUID  # COT-180-NAVY, metres
    dye: uuid.UUID  # DYE-RED-01, kilograms
    buttons: uuid.UUID  # BTN-14MM, pieces
    weaving: uuid.UUID  # default bin of WH-WEAVE
    finishing: uuid.UUID  # default bin of WH-FIN


async def build_textile(session: AsyncSession, org_id: uuid.UUID) -> Textile:
    """Two warehouses and three items in the three unit families, no stock yet."""
    ctx = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
    async with session.begin():
        weave = await inv_services.create_warehouse(
            session, ctx, code="WH-WEAVE", name="Weaving Store"
        )
        fin = await inv_services.create_warehouse(session, ctx, code="WH-FIN", name="Finishing")
        fabric = await inv_services.create_inventory_item(
            session,
            ctx,
            sku="COT-180-NAVY",
            name="Cotton 180 GSM, navy",
            category="raw_materials",
            warehouse="Weaving Store",
            uom="m",
            unit_value_minor=123_45,
        )
        dye = await inv_services.create_inventory_item(
            session,
            ctx,
            sku="DYE-RED-01",
            name="Reactive red dye",
            category="raw_materials",
            warehouse="Weaving Store",
            uom="kg",
        )
        buttons = await inv_services.create_inventory_item(
            session,
            ctx,
            sku="BTN-14MM",
            name="Button, 14 mm",
            category="components",
            warehouse="Weaving Store",
            uom="pcs",
        )
        weaving = await inv_selectors.default_location(session, org_id, weave.id)
        finishing = await inv_selectors.default_location(session, org_id, fin.id)
    assert weaving is not None and finishing is not None
    return Textile(
        org_id=org_id,
        ctx=ctx,
        fabric=fabric.id,
        dye=dye.id,
        buttons=buttons.id,
        weaving=weaving.id,
        finishing=finishing.id,
    )


def q(text: str) -> int:
    """A wire quantity in milli-units — `q("82.5")` is 82_500."""
    return parse_qty(text, allow_negative=True)


async def balance(
    session: AsyncSession,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    location_id: uuid.UUID,
    *,
    status: str = "AVAILABLE",
    roll_id: uuid.UUID | None = None,
) -> tuple[int, int]:
    """(quantity_milli, reserved_milli) of one bucket, read fresh; (0, 0) if it has no row."""
    async with session.begin():
        row = (
            await session.execute(
                select(InventoryBalance.quantity_milli, InventoryBalance.reserved_milli).where(
                    InventoryBalance.organization_id == org_id,
                    InventoryBalance.inventory_item_id == item_id,
                    InventoryBalance.location_id == location_id,
                    InventoryBalance.status == status,
                    InventoryBalance.lot_id.is_(None),
                    InventoryBalance.roll_id.is_not_distinct_from(roll_id),
                )
            )
        ).one_or_none()
    return (row[0], row[1]) if row else (0, 0)


async def ledger_total(session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> int:
    async with session.begin():
        return int(
            (
                await session.execute(
                    select(
                        cast(func.coalesce(func.sum(LedgerEntry.quantity_milli), 0), BigInteger)
                    ).where(
                        LedgerEntry.organization_id == org_id,
                        LedgerEntry.inventory_item_id == item_id,
                    )
                )
            ).scalar_one()
        )


async def ledger_rows(session: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> int:
    async with session.begin():
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(LedgerEntry)
                    .where(
                        LedgerEntry.organization_id == org_id,
                        LedgerEntry.inventory_item_id == item_id,
                    )
                )
            ).scalar_one()
        )
