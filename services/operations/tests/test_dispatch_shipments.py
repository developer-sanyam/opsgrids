"""The carrier handover and its own shipment lifecycle (ADR-0024 §6; `docs/fulfilment/plan.md`
F5) — the delivery/courier counterpart to `test_orders_handover.py`'s counter/walk-in path.

Drives a real order to `READY` through the real F2/F3 write paths (the same `ready_order` helper
F4's own tests use), then exercises `POST /dispatch/shipments`: the same three billing-gate
refusal conditions F4 already proves for the counter path, proven again here for the carrier
path (plan F5's own exit criterion — "the billing gate is genuinely enforced on the carrier path
too"), plus the dispatch `issue` move's own proof that it actually consumes the reservation
(ADR-0024 §6), every `shipment_transitions` edge, and negative-authz/cross-tenant coverage for
every new endpoint.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.dispatch import services as dispatch_services
from app.inventory.reconcile import reconcile_organization
from app.models import (
    FulfilmentSettings,
    InventoryBalance,
    InventoryReservation,
    Order,
    OrderTransition,
    OutboxEvent,
    Shipment,
    ShipmentTransition,
)
from app.orders import selectors as order_selectors
from platform_core.events import validate_event_schema
from tests.fulfilment_fixtures import (
    DISPATCH_READ_PERM,
    DISPATCH_WRITE_PERM,
    Floor,
    accept_order,
    build_floor,
    dispatch_order,
    packed_order,
    ready_order,
    transition_shipment,
)
from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

READ_PERM = "operations.orders.order.read:ORG"
EWAY_REQUESTED = "operations.dispatch.shipment.eway_requested"
#: `test_x.py`, `tests`, `operations`, `services`, ROOT — the same depth
#: `tests/test_orders_invoice_request.py` computes it at.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id, stock=300)


async def _seed_settings(
    session,
    org_id: uuid.UUID,
    *,
    invoice_timing: str = "on_delivery",
    payment_defaults: dict[str, str] | None = None,
    require_pickup_code: bool = False,
    default_tax_rate_pct: Decimal = Decimal("0"),
) -> None:
    """No endpoint creates `fulfilment_settings` yet — the same direct-model-insert shape
    `test_orders_handover.py::_seed_settings` uses."""
    async with session.begin():
        session.add(
            FulfilmentSettings(
                organization_id=org_id,
                warehouse_id=None,
                invoice_timing=invoice_timing,
                payment_defaults=payment_defaults or {"manual": "credit"},
                offer_timeout_seconds=300,
                offer_rounds=3,
                require_pickup_code=require_pickup_code,
                default_tax_rate_pct=default_tax_rate_pct,
            )
        )


async def _reload_order(session, org_id: uuid.UUID, order_id: uuid.UUID) -> Order:
    async with session.begin():
        order = await order_selectors.get_order(session, org_id, order_id)
        assert order is not None
    return order


async def _balance(session, org_id, item_id, location_id) -> tuple[int, int]:
    async with session.begin():
        row = (
            await session.execute(
                select(InventoryBalance.quantity_milli, InventoryBalance.reserved_milli).where(
                    InventoryBalance.organization_id == org_id,
                    InventoryBalance.inventory_item_id == item_id,
                    InventoryBalance.location_id == location_id,
                    InventoryBalance.status == "AVAILABLE",
                )
            )
        ).one_or_none()
    return (row[0], row[1]) if row else (0, 0)


async def _reservations_for_order(
    session, org_id: uuid.UUID, order_id: uuid.UUID
) -> list[InventoryReservation]:
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(InventoryReservation).where(
                        InventoryReservation.organization_id == org_id,
                        InventoryReservation.reference_type == "order",
                        InventoryReservation.reference_id == order_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def _assert_reconciles_clean(session, org_id) -> None:
    async with session.begin():
        report = await reconcile_organization(session, org_id)
    assert report.findings == [], [
        (f.kind, f.stored_milli, f.expected_milli) for f in report.findings
    ]


async def _get_shipment_for_order(session, org_id: uuid.UUID, order_id: uuid.UUID) -> Shipment:
    async with session.begin():
        row = (
            await session.execute(
                select(Shipment).where(
                    Shipment.organization_id == org_id, Shipment.order_id == order_id
                )
            )
        ).scalar_one()
    return row


class TestBillingGateRefusals:
    """The same three conditions F4 proves for the counter path (`test_orders_handover.py
    ::TestBillingGateRefusals`), proven again for the carrier path — plan F5's own exit
    criterion."""

    async def test_a_missing_invoice_is_refused(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id, invoice_timing="on_accept")
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(client, org_id, order_id, order.version)
        assert response.status_code == 409, response.text
        assert "invoice" in response.text.lower()

        after = await _reload_order(session, org_id, order_id)
        assert after.status == "READY"

    async def test_an_unpaid_prepaid_order_is_refused(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(
            session, org_id, invoice_timing="on_delivery", payment_defaults={"manual": "prepaid"}
        )
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(client, org_id, order_id, order.version)
        assert response.status_code == 409, response.text
        assert "paid" in response.text.lower()

    async def test_an_unverified_pickup_code_is_refused(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id, require_pickup_code=True)
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(client, org_id, order_id, order.version)
        assert response.status_code == 409, response.text
        assert "pickup code" in response.text.lower()

    async def test_no_shipment_row_survives_a_refused_gate(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id, invoice_timing="on_accept")
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(client, org_id, order_id, order.version)
        assert response.status_code == 409, response.text

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(Shipment).where(
                            Shipment.organization_id == org_id, Shipment.order_id == order_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert rows == []  # the whole transaction rolled back, not only the order write


class TestDispatchSuccess:
    async def test_dispatch_hands_over_and_delivers_the_order_in_one_call(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)
        ready_version = order.version

        response = await dispatch_order(
            client,
            org_id,
            order_id,
            ready_version,
            carrier_name="Acme Couriers",
            tracking_reference="TRK-9001",
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["order_id"] == str(order_id)
        assert body["status"] == "PICKUP_HANDOVER"
        assert body["carrier_name"] == "Acme Couriers"
        assert body["tracking_reference"] == "TRK-9001"

        after = await _reload_order(session, org_id, order_id)
        assert after.status == "DELIVERED"
        assert after.version == ready_version + 2  # HANDED_OVER, then DELIVERED

    async def test_dispatch_writes_two_order_transition_rows_in_one_call(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(client, org_id, order_id, order.version)
        assert response.status_code == 201, response.text

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(OrderTransition)
                        .where(
                            OrderTransition.organization_id == org_id,
                            OrderTransition.order_id == order_id,
                        )
                        .order_by(OrderTransition.at.asc())
                    )
                )
                .scalars()
                .all()
            )
        assert [(r.from_status, r.to_status) for r in rows[-2:]] == [
            ("READY", "HANDED_OVER"),
            ("HANDED_OVER", "DELIVERED"),
        ]

    async def test_a_shipment_created_event_is_published(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(client, org_id, order_id, order.version)
        shipment_id = response.json()["id"]

        async with session.begin():
            row = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == org_id,
                        OutboxEvent.event_type == dispatch_services.EVT_SHIPMENT_CREATED,
                        OutboxEvent.aggregate_id == uuid.UUID(shipment_id),
                    )
                )
            ).scalar_one()
        assert row.payload["order_id"] == str(order_id)
        assert row.payload["carrier_name"] == "Acme Couriers"


class TestReservationGenuinelyConsumed:
    """Plan F5 exit criterion #2: the kernel `issue` move genuinely consumes the reservation, not
    merely relocates it."""

    async def test_after_dispatch_the_reservation_is_consumed_not_transferred(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor, qty="10")
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(client, org_id, order_id, order.version)
        assert response.status_code == 201, response.text

        # `reference_type == "order"` carries down every hop of a transfer (ADR-0024 §6: "opens a
        # new reservation on the destination bucket ... carrying the source's reference_type/
        # reference_id"), so this order's own two reservations both show up here: the original,
        # and the staging reservation the pick opened from it.
        reservations = await _reservations_for_order(session, org_id, order_id)
        assert len(reservations) == 2
        original = next(r for r in reservations if r.transferred_from_reservation_id is None)
        assert original.status == "transferred"  # the pick moved it to staging
        assert original.consumed_milli == 0

        staging_reservation = next(
            r for r in reservations if r.transferred_from_reservation_id == original.id
        )
        assert staging_reservation.status == "consumed"
        assert staging_reservation.consumed_milli == 10_000
        remaining = (
            staging_reservation.quantity_milli
            - staging_reservation.consumed_milli
            - staging_reservation.released_milli
            - staging_reservation.transferred_milli
        )
        assert remaining == 0

        staging_qty, staging_reserved = await _balance(
            session, org_id, floor.shop.widget, floor.staging_location_id
        )
        assert (staging_qty, staging_reserved) == (0, 0)  # issued out entirely

        await _assert_reconciles_clean(session, org_id)

    async def test_the_full_pick_then_pack_chain_is_consumed_at_the_dock(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """The same proof, one hop further: an order packed to the dock before dispatch (ADR-0024
        §6's "pick and pack transfer a reservation; dispatch consumes it")."""
        await _seed_settings(session, org_id)
        order_id = await packed_order(client, org_id, session, floor, qty="10")
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(client, org_id, order_id, order.version)
        assert response.status_code == 201, response.text

        dock_qty, dock_reserved = await _balance(
            session, org_id, floor.shop.widget, floor.dock_location_id
        )
        assert (dock_qty, dock_reserved) == (0, 0)

        await _assert_reconciles_clean(session, org_id)


class TestNothingStagedIsRefused:
    async def test_dispatching_an_order_that_was_never_picked_is_refused(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "10"}],
        )  # ACCEPTED, never sent through pick — not READY, cannot even reach the gate
        response = await dispatch_order(client, org_id, order.id, order.version)
        assert response.status_code == 409


class TestAuthzAndTenancy:
    async def test_a_caller_without_dispatch_write_is_forbidden(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(
            client, org_id, order_id, order.version, perms=[DISPATCH_READ_PERM]
        )
        assert response.status_code == 403

    async def test_an_own_scope_grant_is_forbidden(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(
            client,
            org_id,
            order_id,
            order.version,
            perms=["operations.dispatch.shipment.write:OWN"],
        )
        assert response.status_code == 403

    async def test_cross_tenant_dispatch_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)

        other_org = uuid.uuid4()
        principal = principal_for(other_org, [DISPATCH_WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/dispatch/shipments",
                json={
                    "order_id": str(order_id),
                    "order_version": order.version,
                    "carrier_name": "Acme Couriers",
                },
            )
        assert response.status_code == 404

    async def test_a_stale_version_is_conflict(self, client, org_id, session, floor: Floor) -> None:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(client, org_id, order_id, order.version + 5)
        assert response.status_code == 409

    async def test_a_reader_cannot_list_shipments_without_read(
        self, client, org_id, session, floor: Floor
    ) -> None:
        principal = principal_for(org_id, [], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get("/api/v1/operations/dispatch/shipments")
        assert response.status_code == 403

    async def test_cross_tenant_shipment_read_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)
        response = await dispatch_order(client, org_id, order_id, order.version)
        shipment_id = response.json()["id"]

        other_org = uuid.uuid4()
        principal = principal_for(other_org, [DISPATCH_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            read = await client.get(f"/api/v1/operations/dispatch/shipments/{shipment_id}")
        assert read.status_code == 404


class TestShipmentLifecycle:
    """Every `shipment_transitions` edge exercised over HTTP; every non-edge answers 409."""

    async def _dispatched(self, client, org_id, session, floor: Floor) -> str:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor)
        order = await _reload_order(session, org_id, order_id)
        response = await dispatch_order(client, org_id, order_id, order.version)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    async def test_pickup_handover_to_in_transit_to_delivered(
        self, client, org_id, session, floor: Floor
    ) -> None:
        shipment_id = await self._dispatched(client, org_id, session, floor)

        in_transit = await transition_shipment(client, org_id, shipment_id, "in-transit")
        assert in_transit.status_code == 200, in_transit.text
        assert in_transit.json()["status"] == "IN_TRANSIT"

        delivered = await transition_shipment(client, org_id, shipment_id, "deliver")
        assert delivered.status_code == 200, delivered.text
        assert delivered.json()["status"] == "DELIVERED"
        assert delivered.json()["delivered_at"] is not None

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(ShipmentTransition)
                        .where(
                            ShipmentTransition.organization_id == org_id,
                            ShipmentTransition.shipment_id == uuid.UUID(shipment_id),
                        )
                        .order_by(ShipmentTransition.at.asc())
                    )
                )
                .scalars()
                .all()
            )
        assert [(r.from_status, r.to_status) for r in rows] == [
            ("PICKUP_HANDOVER", "IN_TRANSIT"),
            ("IN_TRANSIT", "DELIVERED"),
        ]

    async def test_pickup_handover_to_failed_to_in_transit_to_delivered(
        self, client, org_id, session, floor: Floor
    ) -> None:
        shipment_id = await self._dispatched(client, org_id, session, floor)

        failed = await transition_shipment(
            client, org_id, shipment_id, "fail", body={"reason": "recipient unavailable"}
        )
        assert failed.status_code == 200, failed.text
        assert failed.json()["status"] == "FAILED"
        assert failed.json()["failure_reason"] == "recipient unavailable"

        retried = await transition_shipment(client, org_id, shipment_id, "in-transit")
        assert retried.status_code == 200, retried.text

        delivered = await transition_shipment(client, org_id, shipment_id, "deliver")
        assert delivered.status_code == 200, delivered.text
        assert delivered.json()["status"] == "DELIVERED"

    async def test_pickup_handover_to_returned(self, client, org_id, session, floor: Floor) -> None:
        shipment_id = await self._dispatched(client, org_id, session, floor)
        response = await transition_shipment(
            client, org_id, shipment_id, "return", body={"reason": "address refused"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "RETURNED"

    async def test_in_transit_to_returned(self, client, org_id, session, floor: Floor) -> None:
        shipment_id = await self._dispatched(client, org_id, session, floor)
        await transition_shipment(client, org_id, shipment_id, "in-transit")
        response = await transition_shipment(
            client, org_id, shipment_id, "return", body={"reason": "customer refused"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "RETURNED"

    async def test_fail_requires_a_reason(self, client, org_id, session, floor: Floor) -> None:
        shipment_id = await self._dispatched(client, org_id, session, floor)
        response = await transition_shipment(client, org_id, shipment_id, "fail", body={})
        assert response.status_code == 422

    @pytest.mark.parametrize(
        ("action", "body"),
        [
            ("deliver", {}),
            ("in-transit", {}),
            ("fail", {"reason": "x"}),
            ("return", {"reason": "x"}),
        ],
    )
    async def test_delivered_is_terminal(
        self, client, org_id, session, floor: Floor, action: str, body: dict
    ) -> None:
        shipment_id = await self._dispatched(client, org_id, session, floor)
        await transition_shipment(client, org_id, shipment_id, "in-transit")
        await transition_shipment(client, org_id, shipment_id, "deliver")

        response = await transition_shipment(client, org_id, shipment_id, action, body=body)
        assert response.status_code == 409, response.text

    async def test_pickup_handover_direct_to_delivered_is_conflict(
        self, client, org_id, session, floor: Floor
    ) -> None:
        shipment_id = await self._dispatched(client, org_id, session, floor)
        response = await transition_shipment(client, org_id, shipment_id, "deliver")
        assert response.status_code == 409

    async def test_returned_is_terminal(self, client, org_id, session, floor: Floor) -> None:
        shipment_id = await self._dispatched(client, org_id, session, floor)
        await transition_shipment(client, org_id, shipment_id, "return", body={"reason": "x"})

        response = await transition_shipment(client, org_id, shipment_id, "in-transit")
        assert response.status_code == 409

    async def test_a_caller_without_write_cannot_transition(
        self, client, org_id, session, floor: Floor
    ) -> None:
        shipment_id = await self._dispatched(client, org_id, session, floor)
        principal = principal_for(org_id, [DISPATCH_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/dispatch/shipments/{shipment_id}/in-transit", json={}
            )
        assert response.status_code == 403

    async def test_cross_tenant_transition_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        shipment_id = await self._dispatched(client, org_id, session, floor)
        other_org = uuid.uuid4()
        principal = principal_for(other_org, [DISPATCH_WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/dispatch/shipments/{shipment_id}/in-transit", json={}
            )
        assert response.status_code == 404


class TestEwayRequestThreshold:
    """`app.dispatch.services._EWAY_VALUE_THRESHOLD_MINOR` mirrors
    `services/finance/app/domain.py::EWAY_THRESHOLD_MINOR` (₹50,000) — a coarse pre-filter, never
    a second implementation of finance's own `requires_eway_bill`."""

    async def test_a_small_order_emits_no_eway_request(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        order_id = await ready_order(client, org_id, session, floor, qty="1")  # 500.00 INR
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(client, org_id, order_id, order.version)
        assert response.status_code == 201, response.text

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == dispatch_services.EVT_SHIPMENT_EWAY_REQUESTED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert rows == []

    async def test_an_order_over_the_threshold_emits_an_eway_request(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id)
        # 101 widgets @ 500.00 INR = 50,500.00 INR — strictly over ₹50,000.
        order_id = await ready_order(client, org_id, session, floor, qty="101")
        order = await _reload_order(session, org_id, order_id)

        response = await dispatch_order(client, org_id, order_id, order.version)
        assert response.status_code == 201, response.text
        shipment_id = response.json()["id"]

        async with session.begin():
            row = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == org_id,
                        OutboxEvent.event_type == dispatch_services.EVT_SHIPMENT_EWAY_REQUESTED,
                        OutboxEvent.aggregate_id == uuid.UUID(shipment_id),
                    )
                )
            ).scalar_one()
        payload = dict(row.payload)
        validate_event_schema(EWAY_REQUESTED, 1, payload, contracts_root=CONTRACTS_ROOT)

        assert payload["order_id"] == str(order_id)
        assert payload["consignment_value_minor"] == 50_500_00
        assert payload["currency"] == "INR"
        assert payload["carrier_name"] == "Acme Couriers"
        assert "dispatched_at" in payload
