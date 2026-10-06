"""Opening stock, bin-to-bin transfer and status change over HTTP (inventory Phase 1, I4) — every
one through `post_move`, every one idempotent, and the reconciler clean after each.

Authorization and cross-tenant behaviour of these routes is `test_tracking_authz.py`'s; this file
is what they *do*.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.inventory import ledger
from app.inventory.quantity import format_qty
from app.models import AuditLog, InventoryMove, LedgerEntry, OutboxEvent, RollEvent
from platform_core.events import validate_event_schema
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import q
from tests.tracking_fixtures import (
    API,
    STOCK_ADJUST,
    STOCK_READ,
    STOCK_TRANSFER,
    World,
    assert_reconciled,
    bucket_qty,
    build_world,
    make_foreign_world,
)

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"
OPENING = "/operations/inventory/stock/opening"
TRANSFERS = "/operations/inventory/stock/transfers"
STATUS = "/operations/inventory/stock/status-changes"


@pytest.fixture
async def world(session, org_id) -> World:
    return await build_world(session, org_id)


async def post(client, org_id, perm, path, body, key=None):  # type: ignore[no-untyped-def]
    headers = {"Idempotency-Key": key} if key else {}
    with as_principal(principal_for(org_id, [perm])):
        return await client.post(API + path, json=body, headers=headers)


def opening_body(w: World, **line) -> dict:  # type: ignore[type-arg]
    return {
        "lines": [
            {
                "inventory_item_id": str(w.t.dye),
                "location_id": str(w.t.weaving),
                "quantity": "12.5",
                **line,
            }
        ]
    }


async def count(session, model, org_id) -> int:  # type: ignore[no-untyped-def]
    async with session.begin():
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(model).where(model.organization_id == org_id)
                )
            ).scalar_one()
        )


class TestOpeningStock:
    async def test_posts_decimal_stock_per_location_lot_and_roll(
        self, client, session, org_id, world
    ) -> None:
        w = world
        body = {
            "lines": [
                {
                    "inventory_item_id": str(w.t.dye),
                    "location_id": str(w.t.weaving),
                    "quantity": "12.5",
                },
                {
                    "inventory_item_id": str(w.t.dye),
                    "location_id": str(w.bin_b),
                    "quantity": "0.125",
                    "status": "QUALITY_HOLD",
                },
            ]
        }
        response = await post(client, org_id, STOCK_ADJUST, OPENING, body)
        assert response.status_code == 201, response.text
        out = response.json()
        assert out["kind"] == "opening" and out["replayed"] is False
        assert sorted(line["quantity"] for line in out["lines"]) == ["0.125", "12.5"]
        assert await bucket_qty(session, w, item=w.t.dye) == (q("12.5"), 0)
        assert await bucket_qty(
            session, w, item=w.t.dye, location=w.bin_b, status="QUALITY_HOLD"
        ) == (
            125,
            0,
        )
        await assert_reconciled(session, org_id)

    async def test_a_tracked_item_must_say_which_lot_and_roll(self, client, org_id, world) -> None:
        body = opening_body(world, inventory_item_id=str(world.t.fabric), quantity="10")
        response = await post(client, org_id, STOCK_ADJUST, OPENING, body)
        assert response.status_code == 422
        assert response.json()["errors"][0]["field"] == "lot_id"

    async def test_an_untracked_item_refuses_a_lot(self, client, org_id, world) -> None:
        response = await post(
            client, org_id, STOCK_ADJUST, OPENING, opening_body(world, lot_id=str(world.lot))
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("quantity", ["1.2345", "-3", "0", "abc", "1e3", ""])
    async def test_malformed_quantities_are_refused_not_rounded(
        self, client, session, org_id, world, quantity
    ) -> None:
        response = await post(
            client, org_id, STOCK_ADJUST, OPENING, opening_body(world, quantity=quantity)
        )
        assert response.status_code == 422
        assert await bucket_qty(session, world, item=world.t.dye) == (0, 0)

    async def test_a_json_number_is_refused_never_coerced(self, client, org_id, world) -> None:
        response = await post(
            client, org_id, STOCK_ADJUST, OPENING, opening_body(world, quantity=12.5)
        )
        assert response.status_code == 422

    async def test_a_discrete_unit_refuses_a_fraction(self, client, org_id, world) -> None:
        response = await post(
            client,
            org_id,
            STOCK_ADJUST,
            OPENING,
            opening_body(world, inventory_item_id=str(world.t.buttons), quantity="1.5"),
        )
        assert response.status_code == 422

    async def test_unknown_fields_are_refused(self, client, org_id, world) -> None:
        body = opening_body(world)
        body["lines"][0]["on_hand"] = 5
        assert (await post(client, org_id, STOCK_ADJUST, OPENING, body)).status_code == 422

    async def test_a_retry_with_the_same_key_posts_once(
        self, client, session, org_id, world
    ) -> None:
        body = opening_body(world)
        first = await post(client, org_id, STOCK_ADJUST, OPENING, body, key="open-1")
        again = await post(client, org_id, STOCK_ADJUST, OPENING, body, key="open-1")
        assert (first.status_code, again.status_code) == (201, 200)
        assert again.json()["replayed"] is True
        assert again.json()["move_id"] == first.json()["move_id"]
        assert await bucket_qty(session, world, item=world.t.dye) == (q("12.5"), 0)
        audits = await count(session, AuditLog, org_id)
        # Retrying a third time stages no further audit rows or events.
        await post(client, org_id, STOCK_ADJUST, OPENING, body, key="open-1")
        assert await count(session, AuditLog, org_id) == audits

    async def test_the_same_key_for_a_different_request_is_a_conflict(
        self, client, org_id, world
    ) -> None:
        await post(client, org_id, STOCK_ADJUST, OPENING, opening_body(world), key="open-2")
        other = await post(
            client, org_id, STOCK_ADJUST, OPENING, opening_body(world, quantity="3"), key="open-2"
        )
        assert other.status_code == 409

    async def test_another_tenants_lot_roll_or_location_is_404(
        self, client, session, admin_session, org_id, world
    ) -> None:
        foreign = await make_foreign_world(admin_session)
        for field, value in (
            ("lot_id", foreign.lot),
            ("roll_id", foreign.roll),
            ("location_id", foreign.t.weaving),
        ):
            body = opening_body(
                world,
                inventory_item_id=str(world.t.fabric),
                lot_id=str(world.lot),
                roll_id=str(world.roll),
            )
            body["lines"][0][field] = str(value)
            response = await post(client, org_id, STOCK_ADJUST, OPENING, body)
            assert response.status_code == 404, (field, response.text)
        await assert_reconciled(session, org_id)

    async def test_the_roll_gets_an_opening_event_an_audit_row_and_a_valid_event(
        self, session, org_id, world
    ) -> None:
        async with session.begin():
            events = (
                (await session.execute(select(RollEvent).where(RollEvent.roll_id == world.roll)))
                .scalars()
                .all()
            )
            outbox = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == "operations.inventory.stock.opening_posted"
                        )
                    )
                )
                .scalars()
                .all()
            )
            audits = (
                await session.execute(
                    select(AuditLog.action).where(AuditLog.action == "stock.opening_posted")
                )
            ).all()
        assert sorted(e.kind for e in events) == ["CREATED", "OPENING"]
        assert len(outbox) == 1 and len(audits) == 1
        validate_event_schema(
            outbox[0].event_type, 1, dict(outbox[0].payload), contracts_root=CONTRACTS_ROOT
        )


class TestTransfer:
    def body(self, w: World, quantity: str = "30", **extra) -> dict:  # type: ignore[type-arg]
        return {
            "inventory_item_id": str(w.t.fabric),
            "from_location_id": str(w.t.weaving),
            "to_location_id": str(w.bin_b),
            "quantity": quantity,
            "lot_id": str(w.lot),
            "roll_id": str(w.roll),
            **extra,
        }

    async def test_moves_stock_bin_to_bin_and_keeps_the_total(
        self, client, session, org_id, world
    ) -> None:
        w = world
        response = await post(client, org_id, STOCK_TRANSFER, TRANSFERS, self.body(w, "30.25"))
        assert response.status_code == 201, response.text
        out = response.json()
        assert (out["kind"], out["reference_type"]) == ("relocation", "bin_transfer")
        assert sorted(line["quantity"] for line in out["lines"]) == ["-30.25", "30.25"]
        src = await bucket_qty(session, w, lot=w.lot, roll=w.roll)
        dst = await bucket_qty(session, w, location=w.bin_b, lot=w.lot, roll=w.roll)
        assert (format_qty(src[0]), format_qty(dst[0])) == ("52.25", "30.25")
        await assert_reconciled(session, org_id)

    async def test_the_roll_records_a_moved_event_and_the_event_matches_its_schema(
        self, client, session, org_id, world
    ) -> None:
        await post(client, org_id, STOCK_TRANSFER, TRANSFERS, self.body(world))
        async with session.begin():
            kinds = (
                (
                    await session.execute(
                        select(RollEvent.kind).where(RollEvent.roll_id == world.roll)
                    )
                )
                .scalars()
                .all()
            )
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "operations.inventory.stock.transferred"
                    )
                )
            ).scalar_one()
        assert "MOVED" in kinds
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )

    async def test_a_retry_replays_and_moves_nothing_more(
        self, client, session, org_id, world
    ) -> None:
        body = self.body(world, "10")
        first = await post(client, org_id, STOCK_TRANSFER, TRANSFERS, body, key="t-1")
        again = await post(client, org_id, STOCK_TRANSFER, TRANSFERS, body, key="t-1")
        assert (first.status_code, again.status_code) == (201, 200)
        assert again.json()["move_id"] == first.json()["move_id"]
        assert (
            await bucket_qty(session, world, location=world.bin_b, lot=world.lot, roll=world.roll)
        )[0] == q("10")
        different = await post(
            client, org_id, STOCK_TRANSFER, TRANSFERS, self.body(world, "11"), key="t-1"
        )
        assert different.status_code == 409
        await assert_reconciled(session, org_id)

    async def test_more_than_is_there_is_refused_with_the_numbers(
        self, client, session, org_id, world
    ) -> None:
        response = await post(client, org_id, STOCK_TRANSFER, TRANSFERS, self.body(world, "82.501"))
        assert response.status_code == 409
        detail = response.json()["errors"][0]
        assert detail["on_hand"] == "82.5" or "82.5" in response.text
        assert (await bucket_qty(session, world, lot=world.lot, roll=world.roll))[0] == q("82.5")

    async def test_reserved_stock_cannot_be_transferred_and_the_reservation_is_named(
        self, client, session, org_id, world
    ) -> None:
        w = world
        async with session.begin():
            made = await ledger.reserve(
                session,
                w.t.ctx,
                item_id=w.t.fabric,
                location_id=w.t.weaving,
                quantity_milli=q("60"),
                lot_id=w.lot,
                roll_id=w.roll,
                reference_type="sales_order",
                reference_id=uuid.uuid4(),
            )
            reservation_id = made.reservation.id
        # 22.5 is free, so 22.5 may move; 22.6 may not.
        ok = await post(client, org_id, STOCK_TRANSFER, TRANSFERS, self.body(w, "22.5"))
        assert ok.status_code == 201, ok.text
        refused = await post(client, org_id, STOCK_TRANSFER, TRANSFERS, self.body(w, "0.001"))
        assert refused.status_code == 409
        reservations = refused.json()["errors"][0]["reservations"]
        assert [r["reservation_id"] for r in reservations] == [str(reservation_id)]
        await assert_reconciled(session, org_id)

    async def test_a_different_warehouse_is_refused(self, client, session, org_id, world) -> None:
        body = self.body(world, to_location_id=str(world.t.finishing))
        response = await post(client, org_id, STOCK_TRANSFER, TRANSFERS, body)
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "different_warehouse"
        assert (await bucket_qty(session, world, lot=world.lot, roll=world.roll))[0] == q("82.5")

    async def test_same_location_and_float_quantities_are_refused(
        self, client, org_id, world
    ) -> None:
        same = self.body(world, to_location_id=str(world.t.weaving))
        assert (await post(client, org_id, STOCK_TRANSFER, TRANSFERS, same)).status_code == 422
        floaty = self.body(world, quantity=10.5)
        assert (await post(client, org_id, STOCK_TRANSFER, TRANSFERS, floaty)).status_code == 422

    async def test_the_off_site_statuses_are_not_transferable_here(
        self, client, org_id, world
    ) -> None:
        body = self.body(world, status="IN_TRANSIT")
        assert (await post(client, org_id, STOCK_TRANSFER, TRANSFERS, body)).status_code == 422


class TestStatusChange:
    def body(self, w: World, quantity: str = "20", **extra) -> dict:  # type: ignore[type-arg]
        return {
            "inventory_item_id": str(w.t.fabric),
            "location_id": str(w.t.weaving),
            "from_status": "AVAILABLE",
            "to_status": "QUALITY_HOLD",
            "quantity": quantity,
            "lot_id": str(w.lot),
            "roll_id": str(w.roll),
            **extra,
        }

    async def test_available_to_quality_hold_is_a_paired_move(
        self, client, session, org_id, world
    ) -> None:
        w = world
        response = await post(client, org_id, STOCK_TRANSFER, STATUS, self.body(w, "20.5"))
        assert response.status_code == 201, response.text
        lines = response.json()["lines"]
        assert sorted(line["quantity"] for line in lines) == ["-20.5", "20.5"]
        assert {line["status"] for line in lines} == {"AVAILABLE", "QUALITY_HOLD"}
        available = await bucket_qty(session, w, lot=w.lot, roll=w.roll)
        held = await bucket_qty(session, w, status="QUALITY_HOLD", lot=w.lot, roll=w.roll)
        assert (format_qty(available[0]), format_qty(held[0])) == ("62", "20.5")
        # Physical stock did not change: the item's on-hand is still the whole 82.5.
        async with session.begin():
            total = (
                await session.execute(
                    select(func.sum(LedgerEntry.quantity_milli)).where(
                        LedgerEntry.organization_id == org_id,
                        LedgerEntry.inventory_item_id == w.t.fabric,
                    )
                )
            ).scalar_one()
        assert total == q("82.5")
        await assert_reconciled(session, org_id)

    async def test_the_hold_can_be_released_again(self, client, session, org_id, world) -> None:
        await post(client, org_id, STOCK_TRANSFER, STATUS, self.body(world, "20"))
        back = self.body(world, "20", from_status="QUALITY_HOLD", to_status="AVAILABLE")
        with as_principal(principal_for(org_id, [STOCK_TRANSFER, STOCK_ADJUST])):
            released = await client.post(API + STATUS, json=back)
        assert released.status_code == 201
        assert (await bucket_qty(session, world, lot=world.lot, roll=world.roll))[0] == q("82.5")
        await assert_reconciled(session, org_id)

    @pytest.mark.parametrize(
        "hold", ["REJECTED", "DAMAGED", "QUARANTINE", "QUALITY_HOLD", "BLOCKED"]
    )
    @pytest.mark.parametrize("usable", ["AVAILABLE", "WIP"])
    async def test_a_worker_cannot_release_stock_out_of_a_hold(
        self, client, session, org_id, world, hold, usable
    ) -> None:
        """`stock.transfer` alone moves stock INTO a hold; out of one it also needs `stock.adjust`
        (ADR-0014 §12) — or a worker could launder rejected stock back into sellable stock."""
        into = self.body(world, "20", to_status=hold)
        assert (await post(client, org_id, STOCK_TRANSFER, STATUS, into)).status_code == 201
        out = self.body(world, "20", from_status=hold, to_status=usable)
        refused = await post(client, org_id, STOCK_TRANSFER, STATUS, out)
        assert refused.status_code == 403
        assert "stock.adjust" in refused.text
        held = await bucket_qty(session, world, status=hold, lot=world.lot, roll=world.roll)
        assert held[0] == q("20")
        with as_principal(principal_for(org_id, [STOCK_TRANSFER, STOCK_ADJUST])):
            allowed = await client.post(API + STATUS, json=out)
        assert allowed.status_code == 201
        after = await bucket_qty(session, world, status=hold, lot=world.lot, roll=world.roll)
        assert after[0] == 0
        await assert_reconciled(session, org_id)

    async def test_moving_between_holds_needs_only_the_transfer_permission(
        self, client, session, org_id, world
    ) -> None:
        await post(client, org_id, STOCK_TRANSFER, STATUS, self.body(world, "20"))
        across = self.body(world, "20", from_status="QUALITY_HOLD", to_status="REJECTED")
        assert (await post(client, org_id, STOCK_TRANSFER, STATUS, across)).status_code == 201
        await assert_reconciled(session, org_id)

    async def test_stock_out_of_a_reserved_bucket_is_refused_with_the_reservation(
        self, client, session, org_id, world
    ) -> None:
        w = world
        async with session.begin():
            made = await ledger.reserve(
                session,
                w.t.ctx,
                item_id=w.t.fabric,
                location_id=w.t.weaving,
                quantity_milli=q("80"),
                lot_id=w.lot,
                roll_id=w.roll,
            )
            reservation_id = made.reservation.id
        response = await post(client, org_id, STOCK_TRANSFER, STATUS, self.body(w, "10"))
        assert response.status_code == 409
        assert response.json()["errors"][0]["reservations"][0]["reservation_id"] == str(
            reservation_id
        )
        await assert_reconciled(session, org_id)

    async def test_same_status_and_off_site_statuses_are_refused(
        self, client, org_id, world
    ) -> None:
        same = self.body(world, to_status="AVAILABLE")
        assert (await post(client, org_id, STOCK_TRANSFER, STATUS, same)).status_code == 422
        away = self.body(world, to_status="AT_JOB_WORKER")
        assert (await post(client, org_id, STOCK_TRANSFER, STATUS, away)).status_code == 422

    async def test_a_retry_replays(self, client, session, org_id, world) -> None:
        first = await post(client, org_id, STOCK_TRANSFER, STATUS, self.body(world), key="s-1")
        again = await post(client, org_id, STOCK_TRANSFER, STATUS, self.body(world), key="s-1")
        assert (first.status_code, again.status_code) == (201, 200)
        held = await bucket_qty(
            session, world, status="QUALITY_HOLD", lot=world.lot, roll=world.roll
        )
        assert held[0] == q("20")
        event_count = await count(session, InventoryMove, org_id)
        assert event_count >= 2
        await assert_reconciled(session, org_id)

    async def test_the_event_matches_its_schema(self, client, session, org_id, world) -> None:
        await post(client, org_id, STOCK_TRANSFER, STATUS, self.body(world))
        async with session.begin():
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "operations.inventory.stock.status_changed"
                    )
                )
            ).scalar_one()
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )


class TestReadsSeeTheMoves:
    async def test_the_ledger_and_stock_reads_show_what_was_posted(
        self, client, session, org_id, world
    ) -> None:
        await post(client, org_id, STOCK_TRANSFER, TRANSFERS, TestTransfer().body(world, "10"))
        with as_principal(principal_for(org_id, [STOCK_READ])):
            ledger_page = await client.get(
                API + "/operations/inventory/ledger", params={"roll_id": str(world.roll)}
            )
            stock = await client.get(
                API + "/operations/inventory/stock", params={"roll_id": str(world.roll)}
            )
        kinds = [row["kind"] for row in ledger_page.json()["items"]]
        assert kinds == ["relocation", "relocation", "opening"]
        assert sorted(r["on_hand_qty"] for r in stock.json()["items"]) == ["10", "72.5"]
