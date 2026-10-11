"""The MVP release's own exit criterion (`docs/fulfilment/plan.md` F5): "the phone/manual/sales/
walk-in order journey passes end to end with manual worker assignment: an order is placed (F2),
reserved, picked and packed by a worker who received a manual offer (F3), invoiced and paid at
the counter where applicable (F4), handed to a carrier or collected, and the customer is notified
at each step (F5)."

A real, end-to-end HTTP test — every step drives the actual router (`POST`/response, not a direct
service-layer call), proving the whole chain holds together across F2-F5, not only each phase in
isolation. Notification's own event registration is a separate follow-up (this slice's report);
what this test proves is that every event a notification consumer would need is actually
published along the way.

Two variants of the same chain from `ACCEPTED` onward, both real: the counter/walk-in handover
(F4) and the carrier dispatch (F5) — the two ways ADR-0025 §1's billing gate is reached.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Order, OutboxEvent
from app.orders import selectors as order_selectors
from tests.fulfilment_fixtures import (
    Floor,
    accept_offer,
    build_floor,
    confirm_package,
    confirm_pick,
    create_tasks,
    dispatch_order,
    offer_task,
    transition_shipment,
)
from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

CREATE_PERM = "operations.orders.order.create:ORG"
WRITE_PERM = "operations.orders.order.write:ORG"
ACCEPT_PERM = "operations.orders.order.accept:ORG"
HAND_OVER_PERM = "operations.orders.order.hand_over:ORG"
SETTINGS_PERM = "operations.fulfilment.settings.write:ORG"


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id, stock=100)


async def _put_settings(client, org_id: uuid.UUID, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    """Settings through the real API (INVENTORY-02), never a direct insert."""
    payload = {
        "invoice_timing": "on_delivery",
        "payment_defaults": {},
        "offer_timeout_seconds": 300,
        "offer_rounds": 3,
        "require_pickup_code": False,
        "default_tax_rate_pct": "0",
        "auto_assignment_enabled": False,
        "task_sla_minutes": 60,
        "alert_email": None,
        "alert_phone": None,
        **overrides,
    }
    principal = principal_for(org_id, [SETTINGS_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.put("/api/v1/operations/fulfilment/settings", json=payload)
    assert response.status_code == 200, response.text


async def _create_order(client, org_id: uuid.UUID, floor: Floor, *, qty: str) -> dict:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [CREATE_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            "/api/v1/operations/orders",
            json={
                "channel": "manual",
                "warehouse_id": str(floor.shop.warehouse_id),
                "customer_name": "Asha Verma",
                "customer_phone": "9999900000",
                "currency": "INR",
                "lines": [{"inventory_item_id": str(floor.shop.widget), "quantity": qty}],
            },
        )
    assert response.status_code == 201, response.text
    return response.json()


async def _place(client, org_id: uuid.UUID, order_id: str, version: int) -> dict:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [WRITE_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/orders/{order_id}/place", json={"version": version}
        )
    assert response.status_code == 200, response.text
    return response.json()


async def _accept(client, org_id: uuid.UUID, order_id: str, version: int) -> dict:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [ACCEPT_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/orders/{order_id}/accept", json={"version": version}
        )
    assert response.status_code == 200, response.text
    return response.json()


async def _hand_over(  # type: ignore[no-untyped-def]
    client, org_id: uuid.UUID, order_id: str, version: int, *, payment_confirmed: bool
) -> dict:
    principal = principal_for(org_id, [HAND_OVER_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/orders/{order_id}/hand-over",
            json={"version": version, "payment_confirmed": payment_confirmed},
        )
    assert response.status_code == 200, response.text
    return response.json()


async def _reload(session, org_id: uuid.UUID, order_id: uuid.UUID) -> Order:
    async with session.begin():
        order = await order_selectors.get_order(session, org_id, order_id)
        assert order is not None
    return order


async def _events_for(session, org_id: uuid.UUID, order_id: uuid.UUID) -> set[str]:
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(OutboxEvent.event_type).where(
                        OutboxEvent.organization_id == org_id, OutboxEvent.aggregate_id == order_id
                    )
                )
            )
            .scalars()
            .all()
        )
    return set(rows)


async def _drive_to_ready_and_packed(  # type: ignore[no-untyped-def]
    client, org_id: uuid.UUID, floor: Floor, *, qty: str
) -> tuple[str, str]:
    """`create -> place -> accept` (F2) over real HTTP, then one manually-offered pick task
    through to a confirmed pick and a confirmed package (F3), over real HTTP throughout. Returns
    `(order_id, order lines' final line_id)` — callers reload the order for its post-pack
    version."""
    created = await _create_order(client, org_id, floor, qty=qty)
    order_id = created["id"]
    placed = await _place(client, org_id, order_id, created["version"])
    accepted = await _accept(client, org_id, order_id, placed["version"])
    assert accepted["status"] == "ACCEPTED"

    # `create_tasks` only reads `.id`/`.version` off its `order` argument — a `SimpleNamespace`
    # stands in for the real `Order` row, which this HTTP-only chain never loads.
    order_stub = SimpleNamespace(id=order_id, version=accepted["version"])
    tasks = await create_tasks(client, org_id, order_stub)
    task = tasks[0]
    offer = await offer_task(client, org_id, task["id"], floor.worker_id)
    await accept_offer(client, org_id, offer["id"], floor.worker_employee_id)
    line_id = task["lines"][0]["id"]
    await confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)
    await confirm_package(
        client,
        org_id,
        uuid.UUID(order_id),
        destination_location_id=floor.dock_location_id,
        pick_task_line_id=line_id,
        quantity=qty,
    )
    return order_id, line_id


class TestFullJourneyCounterHandover:
    """create -> place -> accept -> pick task -> offer -> accept offer -> confirm pick -> pack ->
    counter hand-over -> delivered, entirely over HTTP, on a fresh organization's **default**
    settings (INVENTORY-02: the default used to require an invoice nothing could create, so every
    handover was a 409). `manual` defaults to the cash term, so the counter attests payment."""

    async def test_the_whole_chain_holds_together(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order_id, _line_id = await _drive_to_ready_and_packed(client, org_id, floor, qty="7")

        order = await _reload(session, org_id, uuid.UUID(order_id))
        assert order.status == "READY"
        assert order.settings_snapshot["source"] == "default"

        delivered = await _hand_over(
            client, org_id, order_id, order.version, payment_confirmed=True
        )
        assert delivered["status"] == "DELIVERED"

        after = await _reload(session, org_id, uuid.UUID(order_id))
        assert after.status == "DELIVERED"

        events = await _events_for(session, org_id, uuid.UUID(order_id))
        assert "operations.orders.order.placed" in events
        assert "operations.orders.order.accepted" in events
        assert "operations.orders.order.handed_over" in events


class TestFullJourneyCarrierDispatch:
    """The same chain, ending at F5's carrier handover instead of the counter — dispatch creates
    the shipment, the order reaches `DELIVERED` in the same call (ADR-0025 §1), and the shipment's
    own lifecycle (a genuinely separate record) is driven on to `DELIVERED` too."""

    async def test_the_whole_chain_holds_together(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order_id, _line_id = await _drive_to_ready_and_packed(client, org_id, floor, qty="7")

        order = await _reload(session, org_id, uuid.UUID(order_id))
        assert order.status == "READY"
        assert order.settings_snapshot["source"] == "default"

        response = await dispatch_order(
            client,
            org_id,
            uuid.UUID(order_id),
            order.version,
            carrier_name="Acme Couriers",
            tracking_reference="TRK-E2E-1",
            payment_confirmed=True,
        )
        assert response.status_code == 201, response.text
        shipment = response.json()
        assert shipment["status"] == "PICKUP_HANDOVER"

        order_after_dispatch = await _reload(session, org_id, uuid.UUID(order_id))
        assert order_after_dispatch.status == "DELIVERED"  # the order's own commercial status

        in_transit = await transition_shipment(client, org_id, shipment["id"], "in-transit")
        assert in_transit.status_code == 200, in_transit.text
        assert in_transit.json()["status"] == "IN_TRANSIT"

        delivered = await transition_shipment(client, org_id, shipment["id"], "deliver")
        assert delivered.status_code == 200, delivered.text
        assert delivered.json()["status"] == "DELIVERED"
        assert delivered.json()["delivered_at"] is not None

        events = await _events_for(session, org_id, uuid.UUID(order_id))
        assert "operations.orders.order.placed" in events
        assert "operations.orders.order.accepted" in events
        assert "operations.orders.order.handed_over" in events


class TestFullJourneyWithSettingsFromTheApi:
    """An administrator puts `manual` orders on credit terms through the settings API; the next
    order is accepted under that row and handed over with no payment attested."""

    async def test_the_configured_policy_is_the_one_the_order_runs_under(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _put_settings(client, org_id, payment_defaults={"manual": "credit"})
        order_id, _line_id = await _drive_to_ready_and_packed(client, org_id, floor, qty="3")

        order = await _reload(session, org_id, uuid.UUID(order_id))
        assert order.settings_snapshot["source"] == "organization"

        delivered = await _hand_over(
            client, org_id, order_id, order.version, payment_confirmed=False
        )
        assert delivered["status"] == "DELIVERED"
