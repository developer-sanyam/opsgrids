"""Shared set-up for the order-engine tests: a small shop built through the real write paths —
one warehouse, two priced items and one unpriced one. Plain module, not conftest, for the same
reason `helpers.py` and `inventory_fixtures.py` are: importing *from* conftest is fragile,
importing from here is not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import services as inv_services
from platform_core.auth import Scope, ScopeContext
from tests.helpers import ctx_for

READ_PERM = "operations.orders.order.read:ORG"
CREATE_PERM = "operations.orders.order.create:ORG"
WRITE_PERM = "operations.orders.order.write:ORG"
ACCEPT_PERM = "operations.orders.order.accept:ORG"
CANCEL_PERM = "operations.orders.order.cancel:ORG"
ALL_PERMS = [READ_PERM, CREATE_PERM, WRITE_PERM, ACCEPT_PERM, CANCEL_PERM]


@dataclass
class Shop:
    org_id: uuid.UUID
    ctx: ScopeContext
    warehouse_id: uuid.UUID
    widget: uuid.UUID  # WGT-001, priced 500.00, pcs
    gizmo: uuid.UUID  # GZM-002, priced 250.00, pcs
    unpriced: uuid.UUID  # NOP-003, no selling price, pcs


async def build_shop(session: AsyncSession, org_id: uuid.UUID, *, stock: int = 100) -> Shop:
    ctx = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
    async with session.begin():
        warehouse = await inv_services.create_warehouse(
            session, ctx, code="WH-SHOP", name="Shop Store"
        )
        widget = await inv_services.create_inventory_item(
            session,
            ctx,
            sku="WGT-001",
            name="Widget",
            category="components",
            warehouse="Shop Store",
            uom="pcs",
            on_hand=stock,
            selling_price_minor=500_00,
            selling_price_currency="INR",
        )
        gizmo = await inv_services.create_inventory_item(
            session,
            ctx,
            sku="GZM-002",
            name="Gizmo",
            category="components",
            warehouse="Shop Store",
            uom="pcs",
            on_hand=stock,
            selling_price_minor=250_00,
            selling_price_currency="INR",
        )
        unpriced = await inv_services.create_inventory_item(
            session,
            ctx,
            sku="NOP-003",
            name="No price yet",
            category="components",
            warehouse="Shop Store",
            uom="pcs",
            on_hand=stock,
        )
    return Shop(
        org_id=org_id,
        ctx=ctx,
        warehouse_id=warehouse.id,
        widget=widget.id,
        gizmo=gizmo.id,
        unpriced=unpriced.id,
    )
