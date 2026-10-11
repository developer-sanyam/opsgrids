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
from tests.helpers import as_principal, ctx_for, principal_for
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
DISPATCH_READ_PERM = "operations.dispatch.shipment.read:ORG"
DISPATCH_WRITE_PERM = "operations.dispatch.shipment.write:ORG"


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
    session: AsyncSession,
    org_id: uuid.UUID,
    shop: Shop,
    *,
    lines: list[dict],
    customer_id: uuid.UUID | None = None,
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
            customer_id=customer_id,
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


# ------------------------------------------------------------------ carrying an order to READY
# over HTTP (shared by the F4 billing-gate tests: `test_orders_handover.py`,
# `test_orders_invoice_request.py`, `test_orders_invoice_issued_consumer.py`'s own siblings)


async def create_tasks(client, org_id: uuid.UUID, order: Order) -> list[dict]:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            "/api/v1/operations/fulfilment/tasks",
            json={"order_id": str(order.id), "order_version": order.version},
        )
    assert response.status_code == 201, response.text
    return response.json()


async def offer_task(client, org_id: uuid.UUID, task_id: str, worker_id: uuid.UUID) -> dict:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/fulfilment/tasks/{task_id}/offers",
            json={"worker_id": str(worker_id)},
        )
    assert response.status_code == 201, response.text
    return response.json()


async def accept_offer(  # type: ignore[no-untyped-def]
    client, org_id: uuid.UUID, offer_id: str, worker_employee_id: uuid.UUID
) -> dict:
    principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=worker_employee_id)
    with as_principal(principal):
        response = await client.post(f"/api/v1/operations/fulfilment/offers/{offer_id}/accept")
    assert response.status_code == 200, response.text
    return response.json()


async def confirm_pick(  # type: ignore[no-untyped-def]
    client, org_id: uuid.UUID, task_id: str, line_id: str, worker_employee_id: uuid.UUID
) -> dict:
    principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=worker_employee_id)
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/fulfilment/tasks/{task_id}/lines/{line_id}/pick", json={}
        )
    assert response.status_code == 200, response.text
    return response.json()


async def ready_order(  # type: ignore[no-untyped-def]
    client,
    org_id: uuid.UUID,
    session: AsyncSession,
    floor: Floor,
    *,
    qty: str = "10",
    customer_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Accepts a one-line order and drives it, through the real F3 write paths (one task, one
    offer, one pick confirmation), all the way to `READY` — the single-task shape every F4
    billing-gate test starts from. Returns the order id; re-read it (`app.orders.selectors
    .get_order`) for its current `version`/facts, since the `Order` object `accept_order` handed
    back does not see writes an HTTP call made on a different session."""
    order = await accept_order(
        session,
        org_id,
        floor.shop,
        lines=[{"inventory_item_id": floor.shop.widget, "quantity": qty}],
        customer_id=customer_id,
    )
    tasks = await create_tasks(client, org_id, order)
    task = tasks[0]
    offer = await offer_task(client, org_id, task["id"], floor.worker_id)
    await accept_offer(client, org_id, offer["id"], floor.worker_employee_id)
    line_id = task["lines"][0]["id"]
    await confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)
    return order.id


async def confirm_package(  # type: ignore[no-untyped-def]
    client,
    org_id: uuid.UUID,
    order_id: uuid.UUID,
    *,
    destination_location_id: uuid.UUID,
    pick_task_line_id: str,
    quantity: str,
) -> dict:
    principal = principal_for(org_id, [PACK_EXECUTE_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            "/api/v1/operations/fulfilment/packages",
            json={
                "order_id": str(order_id),
                "destination_location_id": str(destination_location_id),
                "lines": [{"pick_task_line_id": pick_task_line_id, "quantity": quantity}],
            },
        )
    assert response.status_code == 201, response.text
    return response.json()


async def packed_order(  # type: ignore[no-untyped-def]
    client, org_id: uuid.UUID, session: AsyncSession, floor: Floor, *, qty: str = "10"
) -> uuid.UUID:
    """`ready_order`, then packs the whole picked line into `floor.dock_location_id` — the F3
    step `ready_order` deliberately stops short of (`Package`'s own docstring: picking alone
    already reaches `READY`, ADR-0025 §1). F5's dispatch tests start from here when they need to
    prove the *full* pick-then-pack transfer chain (ADR-0024 §6), not only the pick half.
    Returns the order id."""
    order = await accept_order(
        session,
        org_id,
        floor.shop,
        lines=[{"inventory_item_id": floor.shop.widget, "quantity": qty}],
    )
    tasks = await create_tasks(client, org_id, order)
    task = tasks[0]
    offer = await offer_task(client, org_id, task["id"], floor.worker_id)
    await accept_offer(client, org_id, offer["id"], floor.worker_employee_id)
    line_id = task["lines"][0]["id"]
    await confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)
    await confirm_package(
        client,
        org_id,
        order.id,
        destination_location_id=floor.dock_location_id,
        pick_task_line_id=line_id,
        quantity=qty,
    )
    return order.id


# ------------------------------------------------------------------ dispatch (Fulfilment plan F5)


async def dispatch_order(  # type: ignore[no-untyped-def]
    client,
    org_id: uuid.UUID,
    order_id: uuid.UUID,
    order_version: int,
    *,
    carrier_name: str = "Acme Couriers",
    tracking_reference: str | None = "TRK-0001",
    payment_confirmed: bool = False,
    perms: list[str] | None = None,
):
    principal = principal_for(
        org_id, perms if perms is not None else [DISPATCH_WRITE_PERM], subject_ref=uuid.uuid4()
    )
    with as_principal(principal):
        return await client.post(
            "/api/v1/operations/dispatch/shipments",
            json={
                "order_id": str(order_id),
                "order_version": order_version,
                "carrier_name": carrier_name,
                "tracking_reference": tracking_reference,
                "payment_confirmed": payment_confirmed,
            },
        )


async def transition_shipment(  # type: ignore[no-untyped-def]
    client, org_id: uuid.UUID, shipment_id: str, action: str, *, body: dict | None = None
):
    principal = principal_for(org_id, [DISPATCH_WRITE_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/dispatch/shipments/{shipment_id}/{action}", json=body or {}
        )
