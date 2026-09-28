"""`POST /orders/{id}/accept` under real concurrency (ADR-0025 §1's guard; `docs/fulfilment/plan.md`
F2's own exit criterion).

`test_ledger_reserve_many.py::TestConcurrentAccept` already proves `reserve_many` itself under
concurrency, at the kernel level. This file proves the HTTP route wires it up correctly: 50
concurrent `accept` requests, through the real ASGI app, each its own connection and its own
transaction (no application-level lock, only `SELECT ... FOR UPDATE` and the kernel's own bucket
locks), racing for one order's worth of stock on a single SKU.

`TestSameOrderRace` (security review BLOCKER) races the *same* order against itself — two
concurrent accepts, and an accept against a cancel — the case `TestFiftyConcurrentAccepts` does
not cover (fifty *different* orders never contend the same row's lock). This is what proves
`get_order(for_update=True)`'s `populate_existing` fix and `accept_order`'s claimed-version check
actually stop a stale claim-step read from being written back over a row a concurrent transaction
already changed.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory.reconcile import reconcile_organization
from app.main import app
from app.models import SCHEMA, InventoryReservation, OrderTransition, OutboxEvent
from app.orders import selectors as order_selectors
from app.orders import services as order_services
from platform_core.auth import Scope, get_principal
from platform_core.db import get_session
from tests.helpers import ctx_for, principal_for
from tests.order_fixtures import ACCEPT_PERM, CANCEL_PERM, Shop, build_shop

pytestmark = pytest.mark.integration


@pytest.fixture
async def shop(session, org_id) -> Shop:
    return await build_shop(session, org_id, stock=5)


@pytest.fixture
async def maker(org_id):  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=60,
        max_overflow=0,
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def concurrent_client(maker):  # type: ignore[no-untyped-def]
    """Every request gets its **own** session from `maker`, exactly as `get_tenant_session` does
    against the process-wide engine in production — the opposite of the shared-session `client`
    fixture in `conftest.py`, which is right for sequential tests and wrong for this one."""

    async def _session_override():  # type: ignore[no-untyped-def]
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_session, None)


async def _seed_placed_orders(
    session: AsyncSession, org_id: uuid.UUID, shop: Shop, *, count: int, quantity: str
) -> list[uuid.UUID]:
    """`count` distinct `PLACED` orders, each wanting `quantity` of `shop.widget` — set up
    directly through the service layer (sequential, cheap), so only the `accept` race itself runs
    through HTTP."""
    write_ctx = ctx_for(org_id, "operations.orders.order.create", Scope.ORG)
    ids: list[uuid.UUID] = []
    for _ in range(count):
        async with session.begin():
            order = await order_services.create_order(
                session,
                write_ctx,
                channel="manual",
                warehouse_id=shop.warehouse_id,
                customer_id=None,
                customer_name="Race Customer",
                customer_phone="9000000000",
                currency="INR",
                lines=[{"inventory_item_id": shop.widget, "quantity": quantity}],
            )
            ids.append(order.id)
        async with session.begin():
            locked = await order_selectors.get_order(session, org_id, order.id, for_update=True)
            assert locked is not None
            await order_services.place_order(session, write_ctx, locked, expected_version=0)
    return ids


async def _race_accept(client: AsyncClient, principal, order_ids: list[uuid.UUID]) -> list[int]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:

        async def one(order_id: uuid.UUID) -> int:
            response = await client.post(
                f"/api/v1/operations/orders/{order_id}/accept", json={"version": 1}
            )
            return response.status_code

        return list(await asyncio.gather(*(one(oid) for oid in order_ids)))
    finally:
        app.dependency_overrides.pop(get_principal, None)


async def _assert_reconciles_clean(session, org_id) -> None:  # type: ignore[no-untyped-def]
    async with session.begin():
        report = await reconcile_organization(session, org_id)
    assert report.findings == [], [
        (f.kind, f.stored_milli, f.expected_milli) for f in report.findings
    ]


class TestFiftyConcurrentAccepts:
    async def test_exactly_one_of_fifty_orders_accepts(
        self, session, org_id, data, maker, concurrent_client, shop
    ) -> None:
        order_ids = await _seed_placed_orders(session, org_id, shop, count=50, quantity="5")
        principal = principal_for(org_id, [ACCEPT_PERM], subject_ref=data["subject_a"])

        statuses = await _race_accept(concurrent_client, principal, order_ids)

        assert statuses.count(200) == 1, statuses
        assert statuses.count(409) == 49, statuses

        async with session.begin():
            reservations = (
                (
                    await session.execute(
                        select(InventoryReservation).where(
                            InventoryReservation.organization_id == org_id,
                            InventoryReservation.reference_type == "order",
                            InventoryReservation.reference_id.in_(order_ids),
                            InventoryReservation.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(reservations) == 1
        assert reservations[0].quantity_milli == 5_000  # the whole 5 pcs, one order's worth

        await _assert_reconciles_clean(session, org_id)


class TestSameOrderRace:
    """The security review's BLOCKER: unlike `TestFiftyConcurrentAccepts`, every request here
    targets the *same* order, so both requests can take `get_order(for_update=True)`'s lock on the
    identical row — the only situation the stale-`populate_existing` hazard could ever bite."""

    async def test_two_concurrent_accepts_on_the_same_order_exactly_one_succeeds(
        self, session, org_id, data, maker, concurrent_client, shop
    ) -> None:
        order_ids = await _seed_placed_orders(session, org_id, shop, count=1, quantity="1")
        order_id = order_ids[0]
        principal = principal_for(org_id, [ACCEPT_PERM], subject_ref=data["subject_a"])

        statuses = await _race_accept(concurrent_client, principal, [order_id, order_id])

        assert statuses.count(200) == 1, statuses
        assert statuses.count(409) == 1, statuses

        async with session.begin():
            order = await order_selectors.get_order(session, org_id, order_id)
            assert order is not None
            final_status, final_version = order.status, order.version

            transitions = (
                (
                    await session.execute(
                        select(OrderTransition).where(
                            OrderTransition.organization_id == org_id,
                            OrderTransition.order_id == order_id,
                            OrderTransition.from_status == "PLACED",
                            OrderTransition.to_status == "ACCEPTED",
                        )
                    )
                )
                .scalars()
                .all()
            )
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == order_services.EVT_ORDER_ACCEPTED,
                            OutboxEvent.aggregate_id == order_id,
                        )
                    )
                )
                .scalars()
                .all()
            )

        # PLACED (version 1) -> ACCEPTED (version 2): bumped exactly once, not twice.
        assert final_status == "ACCEPTED"
        assert final_version == 2
        assert len(transitions) == 1
        assert len(events) == 1

        await _assert_reconciles_clean(session, org_id)

    async def test_cancel_racing_accept_on_the_same_order_exactly_one_wins(
        self, session, org_id, data, maker, concurrent_client, shop
    ) -> None:
        order_ids = await _seed_placed_orders(session, org_id, shop, count=1, quantity="1")
        order_id = order_ids[0]
        principal = principal_for(org_id, [ACCEPT_PERM, CANCEL_PERM], subject_ref=data["subject_a"])

        app.dependency_overrides[get_principal] = lambda: principal
        try:
            accept_response, cancel_response = await asyncio.gather(
                concurrent_client.post(
                    f"/api/v1/operations/orders/{order_id}/accept", json={"version": 1}
                ),
                concurrent_client.post(
                    f"/api/v1/operations/orders/{order_id}/cancel",
                    json={"version": 1, "reason": "racing the accept"},
                ),
            )
        finally:
            app.dependency_overrides.pop(get_principal, None)

        statuses = [accept_response.status_code, cancel_response.status_code]
        assert statuses.count(200) == 1, statuses
        assert statuses.count(409) == 1, statuses

        async with session.begin():
            order = await order_selectors.get_order(session, org_id, order_id)
            assert order is not None
            final_status = order.status

            accepted_events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == order_services.EVT_ORDER_ACCEPTED,
                            OutboxEvent.aggregate_id == order_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            cancelled_events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.event_type == order_services.EVT_ORDER_CANCELLED,
                            OutboxEvent.aggregate_id == order_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            active_reservations = (
                (
                    await session.execute(
                        select(InventoryReservation).where(
                            InventoryReservation.organization_id == org_id,
                            InventoryReservation.reference_type == "order",
                            InventoryReservation.reference_id == order_id,
                            InventoryReservation.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )

        # Exactly one of the two ever became a real commercial fact — never both, and never a
        # combination that leaves the order's own state inconsistent with what was emitted.
        if final_status == "ACCEPTED":
            assert accept_response.status_code == 200, accept_response.text
            assert cancel_response.status_code == 409, cancel_response.text
            assert len(accepted_events) == 1
            assert len(cancelled_events) == 0
            assert len(active_reservations) == 1  # accept's own line, actually reserved
        elif final_status == "CANCELLED":
            assert cancel_response.status_code == 200, cancel_response.text
            assert accept_response.status_code == 409, accept_response.text
            assert len(cancelled_events) == 1
            assert len(accepted_events) == 0
            assert active_reservations == []  # accept never got to reserve anything
        else:
            pytest.fail(f"order ended up {final_status!r}, neither ACCEPTED nor CANCELLED")

        await _assert_reconciles_clean(session, org_id)
