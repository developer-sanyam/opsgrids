"""Shared set-up for the fulfilment tests: `tests.order_fixtures.build_shop`'s little shop, plus a
staging location, a dock, a worker, and a helper that accepts an order straight through the real
order-engine write paths (no HTTP layer — the same reasoning `tests/conftest.py`'s own `data`
fixture gives for calling `services.create_supplier` directly) so a pick-task test starts from a
real, reserved order rather than faking one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.fulfilment import services as fulfilment_services
from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.models import Order
from app.orders import selectors as order_selectors
from app.orders import services as order_services
from platform_core.auth import Scope
from tests.helpers import ctx_for
from tests.order_fixtures import Shop, build_shop

TASK_READ_PERM = "operations.fulfilment.task.read:ORG"
TASK_EXECUTE_PERM = "operations.fulfilment.task.execute:OWN"
TASK_ASSIGN_PERM = "operations.fulfilment.task.assign:ORG"
TASK_OVERRIDE_PERM = "operations.fulfilment.task.override:ORG"
WORKER_READ_PERM = "operations.fulfilment.worker.read:ORG"
WORKER_WRITE_PERM = "operations.fulfilment.worker.write:ORG"
WORKER_WRITE_OWN_PERM = "operations.fulfilment.worker.write:OWN"
WORKER_LOCATION_READ_PERM = "operations.fulfilment.worker.location_read:ORG"
PACK_READ_PERM = "operations.fulfilment.pack.read:ORG"
PACK_EXECUTE_PERM = "operations.fulfilment.pack.execute:ORG"


@dataclass
class Floor:
    org_id: uuid.UUID
    shop: Shop
    staging_location_id: uuid.UUID
    dock_location_id: uuid.UUID
    worker_employee_id: uuid.UUID
    worker_id: uuid.UUID


async def build_floor(session: AsyncSession, org_id: uuid.UUID, *, stock: int = 100) -> Floor:
    shop = await build_shop(session, org_id, stock=stock)
    loc_ctx = ctx_for(org_id, "operations.inventory.location.write", Scope.ORG)
    async with session.begin():
        warehouse = await inv_selectors.get_warehouse(session, org_id, shop.warehouse_id)
        assert warehouse is not None
        staging = await inv_services.create_location(
            session, loc_ctx, warehouse, kind="staging", code="STG-1", name="Staging"
        )
        dock = await inv_services.create_location(
            session, loc_ctx, warehouse, kind="dock", code="DOCK-1", name="Dock"
        )

    worker_employee_id = uuid.uuid4()
    worker_ctx = ctx_for(org_id, "operations.fulfilment.worker.write", Scope.ORG)
    async with session.begin():
        worker = await fulfilment_services.create_worker(
            session,
            worker_ctx,
            employee_id=worker_employee_id,
            warehouse_id=shop.warehouse_id,
            display_name="Asha Picker",
            is_active=True,
            skills=[],
            zone_location_ids=[],
        )
    return Floor(
        org_id=org_id,
        shop=shop,
        staging_location_id=staging.id,
        dock_location_id=dock.id,
        worker_employee_id=worker_employee_id,
        worker_id=worker.id,
    )


async def accept_order(
    session: AsyncSession, org_id: uuid.UUID, shop: Shop, *, lines: list[dict]
) -> Order:
    """`DRAFT -> PLACED -> ACCEPTED`, through the real order-engine write paths, so pick-task
    creation starts from a genuinely reserved order (the same reservations F3 must transfer).

    `lines` takes the same wire-ish shape `OrderIn.lines` does (`inventory_item_id` as a string,
    `quantity` as a `Qty` string) — the route parses those into real `uuid.UUID`/`OrderLineIn`
    values via Pydantic before ever calling `order_services.create_order`
    (`app/api/routes.py::create_order`'s own `line.model_dump()`); a direct-service-call fixture
    has no Pydantic layer to do that for it, so it does the same coercion by hand here instead of
    silently handing the service layer strings a real caller could never produce.
    """
    create_ctx = ctx_for(org_id, "operations.orders.order.create", Scope.ORG)
    write_ctx = ctx_for(org_id, "operations.orders.order.write", Scope.ORG)
    accept_ctx = ctx_for(org_id, "operations.orders.order.accept", Scope.ORG)
    coerced = [
        {**line, "inventory_item_id": uuid.UUID(str(line["inventory_item_id"]))} for line in lines
    ]

    async with session.begin():
        order = await order_services.create_order(
            session,
            create_ctx,
            channel="manual",
            warehouse_id=shop.warehouse_id,
            customer_id=None,
            customer_name="Asha Verma",
            customer_phone="9999900000",
            currency="INR",
            lines=coerced,
        )
    async with session.begin():
        locked = await order_selectors.get_order(session, org_id, order.id, for_update=True)
        assert locked is not None
        await order_services.place_order(session, write_ctx, locked, expected_version=0)

    accepted = await order_services.accept_order(session, accept_ctx, order.id, expected_version=1)
    return accepted
