"""Reservations over HTTP (inventory Phase 3): create for an order, a production order or by hand,
list and read them, release (the existing route), and expiry through the sweep — every promise made
and given back by `ledger.reserve` / `ledger.release`, the reconciler clean after each.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory import ledger
from app.inventory.ledger import MoveLine
from app.models import SCHEMA, AuditLog, InventoryReservation
from app.movements import reservations as reservation_service
from app.movements.sweep import ReservationExpirySweep
from platform_core.db import TENANT_GUC
from platform_core.events import validate_event_schema
from tests.inventory_fixtures import TODAY, q
from tests.movements_fixtures import (
    STOCK_READ,
    STOCK_RESERVE,
    World,
    build_world,
    call,
    events,
    mover,
    receive_stock,
)
from tests.tracking_fixtures import assert_reconciled, bucket_qty

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"
R = "/operations/inventory/reservations"


@pytest.fixture
async def world(session, org_id) -> World:
    w = await build_world(session, org_id)
    await receive_stock(session, w, w.t.dye, w.t.weaving, "100")
    return w


def body(w: World, quantity: str = "30", **extra) -> dict:  # type: ignore[type-arg]
    return {
        "inventory_item_id": str(w.t.dye),
        "location_id": str(w.t.weaving),
        "quantity": quantity,
        **extra,
    }


class TestCreate:
    async def test_a_manual_reservation_promises_stock_without_moving_it(
        self, client, session, org_id, world
    ) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        resp = await call(client, holder, "POST", R, body(world, "30", note="for the Surat order"))
        assert resp.status_code == 201, resp.text
        out = resp.json()
        assert (out["status"], out["quantity"], out["remaining"]) == ("active", "30", "30")
        assert out["reference_type"] == "manual" and out["note"] == "for the Surat order"
        assert out["sku"] == "DYE-RED-01" and out["location_code"] == "DEFAULT"
        # Reserved, not moved: on hand is whole, available is not.
        assert await bucket_qty(session, world, item=world.t.dye) == (q("100"), q("30"))
        await assert_reconciled(session, org_id)

    async def test_the_manual_route_refuses_to_stand_in_for_an_order(
        self, client, org_id, world
    ) -> None:
        # Order and production-order reservations are made by their own modules; a hand-made one
        # may not claim their reference (it would block the order's handover).
        holder = mover(org_id, STOCK_RESERVE)
        for kind in ("order", "production_order"):
            resp = await call(
                client,
                holder,
                "POST",
                R,
                body(world, "10", reference_type=kind, reference_id=str(uuid.uuid4())),
            )
            assert resp.status_code == 422, resp.text

    async def test_more_than_is_available_is_refused_with_the_numbers(
        self, client, session, org_id, world
    ) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        await call(client, holder, "POST", R, body(world, "80"))
        resp = await call(client, holder, "POST", R, body(world, "30"))
        assert resp.status_code == 409
        assert resp.json()["type"].endswith("insufficient-stock")
        assert await bucket_qty(session, world, item=world.t.dye) == (q("100"), q("80"))

    async def test_quantities_are_exact_decimal_strings(self, client, org_id, world) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        ok = await call(client, holder, "POST", R, body(world, "0.125"))
        assert ok.status_code == 201 and ok.json()["quantity"] == "0.125"
        for bad in ("0", "-1", "1.2345"):
            assert (await call(client, holder, "POST", R, body(world, bad))).status_code == 422
        floaty = {**body(world), "quantity": 2.5}
        assert (await call(client, holder, "POST", R, floaty)).status_code == 422

    async def test_a_discrete_unit_refuses_a_fraction(self, client, session, org_id, world) -> None:
        await receive_stock(session, world, world.t.buttons, world.t.weaving, "50")
        holder = mover(org_id, STOCK_RESERVE)
        resp = await call(
            client,
            holder,
            "POST",
            R,
            {
                "inventory_item_id": str(world.t.buttons),
                "location_id": str(world.t.weaving),
                "quantity": "2.5",
            },
        )
        assert resp.status_code == 422

    async def test_a_tracked_item_must_name_its_lot_and_roll(self, client, org_id, world) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        bare = await call(
            client,
            holder,
            "POST",
            R,
            {
                "inventory_item_id": str(world.t.fabric),
                "location_id": str(world.t.weaving),
                "quantity": "10",
            },
        )
        assert bare.status_code == 422
        named = await call(
            client,
            holder,
            "POST",
            R,
            {
                "inventory_item_id": str(world.t.fabric),
                "location_id": str(world.t.weaving),
                "quantity": "10",
                "lot_id": str(world.lot),
                "roll_id": str(world.roll),
            },
        )
        assert named.status_code == 201, named.text
        assert named.json()["roll_id"] == str(world.roll)

    async def test_the_expiry_must_be_in_the_future_and_timezone_aware(
        self, client, org_id, world
    ) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        naive = (datetime.now() + timedelta(hours=1)).replace(tzinfo=None).isoformat()
        assert (
            await call(client, holder, "POST", R, body(world, "1", expires_at=past))
        ).status_code == 422
        assert (
            await call(client, holder, "POST", R, body(world, "1", expires_at=naive))
        ).status_code == 422
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        ok = await call(client, holder, "POST", R, body(world, "1", expires_at=future))
        assert ok.status_code == 201 and ok.json()["expires_at"] is not None

    async def test_a_retried_create_replays_and_reserves_once(
        self, client, session, org_id, world
    ) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        first = await call(client, holder, "POST", R, body(world, "30"), key="res-1")
        again = await call(client, holder, "POST", R, body(world, "30"), key="res-1")
        assert (first.status_code, again.status_code) == (201, 200)
        assert again.json()["id"] == first.json()["id"] and again.json()["replayed"] is True
        different = await call(client, holder, "POST", R, body(world, "31"), key="res-1")
        assert different.status_code == 409
        assert await bucket_qty(session, world, item=world.t.dye) == (q("100"), q("30"))

    async def test_a_creation_is_audited_and_its_event_matches_the_contract(
        self, client, session, org_id, world
    ) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        made = await call(client, holder, "POST", R, body(world, "5", expires_at=future))
        async with session.begin():
            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "inventory_reservation.created",
                        AuditLog.entity_id == uuid.UUID(made.json()["id"]),
                    )
                )
            ).scalar_one()
        assert audit.after["quantity"] == "5"
        (event,) = await events(session, "operations.inventory.stock.reserved")
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )


class TestListAndRead:
    async def test_list_filter_and_page(self, client, session, org_id, world) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        order_id = uuid.uuid4()
        for n in range(3):
            await call(client, holder, "POST", R, body(world, str(n + 1)))
        async with session.begin():  # the orders module's own door: the kernel
            await ledger.reserve(
                session,
                world.t.ctx,
                item_id=world.t.dye,
                location_id=world.t.weaving,
                quantity_milli=q("9"),
                reference_type="order",
                reference_id=order_id,
            )
        reader = mover(org_id, STOCK_READ)
        page = await call(client, reader, "GET", f"{R}?limit=3")
        assert len(page.json()["items"]) == 3 and page.json()["next_cursor"]
        rest = await call(client, reader, "GET", f"{R}?limit=3&cursor={page.json()['next_cursor']}")
        assert len(rest.json()["items"]) == 1
        by_ref = await call(client, reader, "GET", f"{R}?reference_id={order_id}")
        assert [i["quantity"] for i in by_ref.json()["items"]] == ["9"]
        manual = await call(client, reader, "GET", f"{R}?reference_type=manual")
        assert len(manual.json()["items"]) == 3
        by_item = await call(client, reader, "GET", f"{R}?item_id={world.t.dye}&status=active")
        assert len(by_item.json()["items"]) == 4
        assert (await call(client, reader, "GET", f"{R}?status=released")).json()["items"] == []

    async def test_read_one_and_release_through_the_existing_route(
        self, client, session, org_id, world
    ) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        made = (await call(client, holder, "POST", R, body(world, "30"))).json()
        reader = mover(org_id, STOCK_READ)
        got = await call(client, reader, "GET", f"{R}/{made['id']}")
        assert got.status_code == 200 and got.json()["remaining"] == "30"
        part = await call(client, holder, "POST", f"{R}/{made['id']}:release", {"quantity": "10"})
        assert part.status_code == 200
        after = (await call(client, reader, "GET", f"{R}/{made['id']}")).json()
        assert (after["status"], after["released"], after["remaining"]) == ("active", "10", "20")
        full = await call(client, holder, "POST", f"{R}/{made['id']}:release")
        assert full.status_code == 200
        done = (await call(client, reader, "GET", f"{R}/{made['id']}")).json()
        assert (done["status"], done["remaining"]) == ("released", "0")
        assert await bucket_qty(session, world, item=world.t.dye) == (q("100"), 0)
        await assert_reconciled(session, org_id)

    async def test_an_unknown_reservation_is_a_404(self, client, org_id) -> None:
        reader = mover(org_id, STOCK_READ)
        assert (await call(client, reader, "GET", f"{R}/{uuid.uuid4()}")).status_code == 404


class TestExpiry:
    async def _lapse(self, session, org_id, reservation_id: str) -> None:  # type: ignore[no-untyped-def]
        """Move a reservation's expiry into the past, as the passing of time would.

        On the database's clock: the sweep's discovery compares with `now()`, so a past computed
        from this process's clock is not past to it when the two clocks disagree.
        """
        async with session.begin():
            await session.execute(
                update(InventoryReservation)
                .where(
                    InventoryReservation.organization_id == org_id,
                    InventoryReservation.id == uuid.UUID(reservation_id),
                )
                .values(expires_at=func.now() - timedelta(minutes=5))
            )

    async def test_a_lapsed_reservation_is_released_and_a_live_one_is_not(
        self, client, session, org_id, world
    ) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        lapsing = (await call(client, holder, "POST", R, body(world, "30", expires_at=soon))).json()
        living = (await call(client, holder, "POST", R, body(world, "20", expires_at=soon))).json()
        forever = (await call(client, holder, "POST", R, body(world, "10"))).json()
        await self._lapse(session, org_id, lapsing["id"])
        assert await bucket_qty(session, world, item=world.t.dye) == (q("100"), q("60"))

        async with session.begin():
            result = await reservation_service.expire_due(session, org_id)
        assert (result.due, result.expired, result.skipped) == (1, 1, 0)

        reader = mover(org_id, STOCK_READ)
        gone = (await call(client, reader, "GET", f"{R}/{lapsing['id']}")).json()
        assert (gone["status"], gone["remaining"], gone["released"]) == ("released", "0", "30")
        assert gone["expired_at"] is not None
        for still in (living, forever):
            assert (await call(client, reader, "GET", f"{R}/{still['id']}")).json()[
                "status"
            ] == "active"
        # The 30 is free again — and nothing else moved.
        assert await bucket_qty(session, world, item=world.t.dye) == (q("100"), q("30"))
        await assert_reconciled(session, org_id)

        # A second pass finds nothing, and the events match their contracts.
        async with session.begin():
            again = await reservation_service.expire_due(session, org_id)
        assert again.due == 0
        (expired,) = await events(session, "operations.inventory.reservation.expired")
        validate_event_schema(
            expired.event_type, 1, dict(expired.payload), contracts_root=CONTRACTS_ROOT
        )
        released = await events(session, "operations.inventory.stock.released")
        assert released
        for event in released:
            validate_event_schema(
                event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
            )

    async def test_a_reservation_released_by_someone_else_is_not_reported_expired(
        self, client, session, org_id, world, monkeypatch
    ) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        made = (await call(client, holder, "POST", R, body(world, "30", expires_at=soon))).json()
        await self._lapse(session, org_id, made["id"])
        real = ledger.release

        async def user_wins_the_race(sess, ctx, reservation_id, **kw):  # type: ignore[no-untyped-def]
            # A person releases between the sweep's SELECT and its lock.
            await real(sess, world.t.ctx, reservation_id)
            return await real(sess, ctx, reservation_id, **kw)

        monkeypatch.setattr(ledger, "release", user_wins_the_race)
        async with session.begin():
            result = await reservation_service.expire_due(session, org_id)
        assert (result.expired, result.skipped) == (0, 1)
        assert await events(session, "operations.inventory.reservation.expired") == []
        reader = mover(org_id, STOCK_READ)
        got = (await call(client, reader, "GET", f"{R}/{made['id']}")).json()
        assert got["expired_at"] is None

    async def test_the_free_stock_can_be_reserved_again_after_expiry(
        self, client, session, org_id, world
    ) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        first = (await call(client, holder, "POST", R, body(world, "100", expires_at=soon))).json()
        blocked = await call(client, holder, "POST", R, body(world, "1"))
        assert blocked.status_code == 409
        await self._lapse(session, org_id, first["id"])
        async with session.begin():
            await reservation_service.expire_due(session, org_id)
        assert (await call(client, holder, "POST", R, body(world, "100"))).status_code == 201

    async def test_a_partly_consumed_reservation_releases_only_what_is_still_open(
        self, client, session, org_id, world
    ) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        made = (await call(client, holder, "POST", R, body(world, "30", expires_at=soon))).json()
        async with session.begin():
            await ledger.post_move(
                session,
                world.t.ctx,
                kind="issue",
                lines=[
                    MoveLine(
                        world.t.dye,
                        world.t.weaving,
                        -q("10"),
                        reservation_id=uuid.UUID(made["id"]),
                    )
                ],
                occurred_on=TODAY,
            )
        await self._lapse(session, org_id, made["id"])
        async with session.begin():
            result = await reservation_service.expire_due(session, org_id)
        assert result.expired == 1
        out = (await call(client, mover(org_id, STOCK_READ), "GET", f"{R}/{made['id']}")).json()
        assert (out["consumed"], out["released"], out["status"]) == ("10", "20", "consumed")
        assert await bucket_qty(session, world, item=world.t.dye) == (q("90"), 0)
        await assert_reconciled(session, org_id)

    async def test_the_sweep_finds_due_organizations_and_releases_for_each_under_its_tenant(
        self, session, org_id, world
    ) -> None:
        """The real sweep: discovery on the outbox role, work on the app role under the tenant
        policy — and an organization with nothing due is untouched."""
        # Seed a due reservation straight through the kernel (no HTTP client needed here).
        async with session.begin():
            made = await ledger.reserve(
                session,
                world.t.ctx,
                item_id=world.t.dye,
                location_id=world.t.weaving,
                quantity_milli=q("25"),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            rid = made.reservation.id
        await self._lapse(session, org_id, str(rid))

        cfg = settings()
        outbox = create_async_engine(cfg.outbox_dsn)
        app_engine = create_async_engine(
            cfg.app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
        )
        app_maker = async_sessionmaker(app_engine, expire_on_commit=False)

        @asynccontextmanager
        async def work_scope(organization_id):  # type: ignore[no-untyped-def]
            async with app_maker() as s, s.begin():
                await s.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', '{organization_id}', true)")
                )
                yield s

        sweep = ReservationExpirySweep(
            discovery_factory=async_sessionmaker(outbox, expire_on_commit=False),
            work_scope=work_scope,
        )
        try:
            report = await sweep.tick()
        finally:
            await outbox.dispose()
            await app_engine.dispose()
        assert report.failed == 0
        assert report.expired >= 1
        async with session.begin():
            row = (
                await session.execute(
                    select(InventoryReservation)
                    .where(InventoryReservation.id == rid)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
        assert row.status == "released" and row.expired_at is not None
        await assert_reconciled(session, org_id)
