"""Hardening of the Phase 3 movement paths: reservation key namespace, transfer shortage as a
controlled write-off, receipt replay checks, lock ordering, threshold units, report truncation."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, func, select

from app.config import settings
from app.inventory import ledger
from app.models import AdjustmentRequest, AuditLog, InventoryMove, InventoryReservation
from app.movements import domain
from app.movements import selectors as sel
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from tests.helpers import ctx_for
from tests.inventory_fixtures import q
from tests.movements_fixtures import (
    CONTROL_CONFIGURE,
    STOCK_READ,
    STOCK_RESERVE,
    TRANSFER_DISPATCH,
    TRANSFER_RECEIVE,
    World,
    build_world,
    call,
    in_transit_total,
    mover,
    receive_stock,
)
from tests.test_movements_transfers import approved, dispatched, dye_line
from tests.tracking_fixtures import assert_reconciled

pytestmark = pytest.mark.integration

R = "/operations/inventory/reservations"
T = "/operations/inventory/transfers"


@pytest.fixture
async def world(session, org_id) -> World:
    w = await build_world(session, org_id)
    await receive_stock(session, w, w.t.dye, w.t.weaving, "100")
    return w


@pytest.fixture(autouse=True)
async def _engine():  # type: ignore[no-untyped-def]
    init_engine(settings())
    yield
    await dispose_engine()


def res_body(w: World, quantity: str = "5", **extra) -> dict:  # type: ignore[type-arg]
    return {
        "inventory_item_id": str(w.t.dye),
        "location_id": str(w.t.weaving),
        "quantity": quantity,
        **extra,
    }


async def configure(client, org_id, *, adjustment=None, transfer=None):  # type: ignore[no-untyped-def]
    resp = await call(
        client,
        mover(org_id, CONTROL_CONFIGURE),
        "PUT",
        "/operations/inventory/control-settings",
        {"adjustment_approval_threshold": adjustment, "transfer_approval_threshold": transfer},
    )
    assert resp.status_code == 200, resp.text


class TestReservationKeys:
    async def test_a_client_key_cannot_collide_with_the_orders_key_namespace(
        self, client, session, org_id, world
    ) -> None:
        raw = f"order:{uuid.uuid4()}:accept:v1:{uuid.uuid4()}"
        holder = mover(org_id, STOCK_RESERVE)
        first = await call(client, holder, "POST", R, res_body(world), key=raw)
        assert first.status_code == 201, first.text
        async with session.begin():
            stored = (
                await session.execute(
                    select(InventoryReservation.idempotency_key).where(
                        InventoryReservation.organization_id == org_id
                    )
                )
            ).scalar_one()
        assert stored != raw
        # Another holder's use of the same string is their own key, not a replay of the first.
        other = await call(
            client, mover(org_id, STOCK_RESERVE), "POST", R, res_body(world), key=raw
        )
        assert other.status_code == 201 and other.json()["id"] != first.json()["id"]
        # The same holder retrying still replays.
        again = await call(client, holder, "POST", R, res_body(world), key=raw)
        assert again.json()["id"] == first.json()["id"] and again.json()["replayed"] is True

    @pytest.mark.parametrize("kind", ["order", "production_order"])
    async def test_a_manual_reservation_cannot_claim_an_order_reference(
        self, client, org_id, world, kind
    ) -> None:
        resp = await call(
            client,
            mover(org_id, STOCK_RESERVE),
            "POST",
            R,
            res_body(world, reference_type=kind, reference_id=str(uuid.uuid4())),
        )
        assert resp.status_code == 422, resp.text

    async def test_a_reservation_needs_the_reserve_permission(self, client, org_id, world) -> None:
        resp = await call(client, mover(org_id, STOCK_READ), "POST", R, res_body(world))
        assert resp.status_code == 403


def receive_body(body, short, received="0", **extra):  # type: ignore[no-untyped-def]
    return {
        "lines": [
            {
                "line_id": body["lines"][0]["id"],
                "received_quantity": received,
                "shortage_quantity": short,
            }
        ],
        **extra,
    }


class TestTransferShortage:
    async def test_the_dispatcher_cannot_receive_their_own_transfer(
        self, client, org_id, world
    ) -> None:
        body = await approved(client, org_id, world, dye_line(world, "40"))
        both = mover(org_id, TRANSFER_DISPATCH, TRANSFER_RECEIVE)
        sent = await call(client, both, "POST", f"{T}/{body['id']}:dispatch", {})
        assert sent.status_code == 200, sent.text
        resp = await call(
            client, both, "POST", f"{T}/{body['id']}:receive", receive_body(sent.json(), "0", "40")
        )
        assert resp.status_code == 409, resp.text

    async def test_a_shortage_at_the_threshold_is_held_for_approval_not_written_off(
        self, client, session, org_id, world
    ) -> None:
        await configure(client, org_id, adjustment="10")
        body = await dispatched(client, org_id, world, dye_line(world, "40"))
        receiver = mover(org_id, TRANSFER_RECEIVE, employee=True)
        no_evidence = await call(
            client, receiver, "POST", f"{T}/{body['id']}:receive", receive_body(body, "10", "30")
        )
        assert no_evidence.status_code == 422, no_evidence.text
        ok = await call(
            client,
            receiver,
            "POST",
            f"{T}/{body['id']}:receive",
            receive_body(body, "10", "30", evidence_document_id=str(uuid.uuid4())),
        )
        assert ok.status_code == 200, ok.text
        async with session.begin():
            adj = (
                await session.execute(
                    select(AdjustmentRequest).where(AdjustmentRequest.organization_id == org_id)
                )
            ).scalar_one()
            shortage_moves = (
                await session.execute(
                    select(func.count())
                    .select_from(InventoryMove)
                    .where(
                        InventoryMove.organization_id == org_id,
                        InventoryMove.reference_type == "stock_transfer_shortage",
                    )
                )
            ).scalar_one()
        assert (adj.status, adj.quantity_delta_milli, adj.stock_status) == (
            "PENDING_APPROVAL",
            -q("10"),
            "IN_TRANSIT",
        )
        assert shortage_moves == 0
        # The 10 is still in the in-transit bucket until someone approves its write-off.
        assert await in_transit_total(session, org_id, world.t.dye) == q("10")
        await assert_reconciled(session, org_id)

    async def test_a_small_shortage_posts_directly_and_is_audited(
        self, client, session, org_id, world
    ) -> None:
        await configure(client, org_id, adjustment="10")
        body = await dispatched(client, org_id, world, dye_line(world, "40"))
        resp = await call(
            client,
            mover(org_id, TRANSFER_RECEIVE),
            "POST",
            f"{T}/{body['id']}:receive",
            receive_body(body, "2", "38"),
        )
        assert resp.status_code == 200, resp.text
        assert await in_transit_total(session, org_id, world.t.dye) == 0
        async with session.begin():
            audited = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "stock_transfer.shortage_written_off",
                    )
                )
            ).scalar_one()
        assert audited == 1

    async def test_a_long_client_key_is_accepted_and_hashed(self, client, org_id, world) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "40"))
        resp = await call(
            client,
            mover(org_id, TRANSFER_RECEIVE),
            "POST",
            f"{T}/{body['id']}:receive",
            receive_body(body, "0", "10"),
            key="k" * 120,
        )
        assert resp.status_code == 200, resp.text

    async def test_a_receipt_replay_must_match_body_and_caller(self, client, org_id, world) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "40"))
        receiver = mover(org_id, TRANSFER_RECEIVE)
        path = f"{T}/{body['id']}:receive"
        first = await call(client, receiver, "POST", path, receive_body(body, "0", "10"), key="r1")
        assert first.status_code == 200
        same = await call(client, receiver, "POST", path, receive_body(body, "0", "10"), key="r1")
        assert same.status_code == 200 and same.json()["replayed"] is True
        other_body = await call(
            client, receiver, "POST", path, receive_body(body, "0", "11"), key="r1"
        )
        assert other_body.status_code == 409
        other_actor = await call(
            client,
            mover(org_id, TRANSFER_RECEIVE),
            "POST",
            path,
            receive_body(body, "0", "10"),
            key="r1",
        )
        assert other_actor.status_code == 409

    async def test_a_dispatch_replay_with_a_different_expected_date_is_refused(
        self, client, org_id, world
    ) -> None:
        body = await approved(client, org_id, world, dye_line(world, "10"))
        keeper = mover(org_id, TRANSFER_DISPATCH)
        path = f"{T}/{body['id']}:dispatch"
        first = await call(client, keeper, "POST", path, {"expected_on": "2030-01-01"}, key="d1")
        assert first.status_code == 200
        bad = await call(client, keeper, "POST", path, {"expected_on": "2031-01-01"}, key="d1")
        assert bad.status_code == 409


class TestThresholdUnits:
    def test_a_threshold_is_compared_in_the_dimensions_base_unit(self) -> None:
        # 5000 g is 5 kg: under a 10 kg threshold, where raw milli-units (5_000_000) said over.
        assert domain.over_threshold(10_000, [domain.to_base_milli(5_000_000, "g")]) is False
        assert domain.over_threshold(10_000, [domain.to_base_milli(10_000_000, "g")]) is True
        assert domain.to_base_milli(-1_000, "kg") == 1_000
        # An unknown unit has no known size: compared as it is.
        assert domain.to_base_milli(7_000, "widget") == 7_000


class TestLockOrder:
    async def test_item_rows_are_locked_in_id_order(self, session, org_id, world) -> None:
        seen: list[str] = []

        def spy(conn, cursor, statement, *_):  # type: ignore[no-untyped-def]
            seen.append(statement)

        sync_engine = session.bind.sync_engine  # type: ignore[union-attr]
        event.listen(sync_engine, "before_cursor_execute", spy)
        try:
            async with session.begin():
                await ledger._items(session, org_id, [world.t.dye, world.t.buttons])
        finally:
            event.remove(sync_engine, "before_cursor_execute", spy)
        locking = [s for s in seen if "FOR SHARE" in s and "inventory_items" in s]
        assert locking and all("ORDER BY" in s for s in locking)


class TestInTransitTruncation:
    async def test_a_cut_report_says_so(self, client, org_id, world, session) -> None:
        await receive_stock(session, world, world.t.buttons, world.t.weaving, "50")
        await dispatched(client, org_id, world, dye_line(world, "5"))
        buttons = {
            "inventory_item_id": str(world.t.buttons),
            "from_location_id": str(world.t.weaving),
            "quantity": "5",
        }
        await dispatched(client, org_id, world, buttons)
        reader = mover(org_id, STOCK_READ)
        cut = await call(client, reader, "GET", "/operations/inventory/in-transit?limit=1")
        assert cut.status_code == 200 and len(cut.json()) == 1
        assert cut.headers["x-result-truncated"] == "true"
        whole = await call(client, reader, "GET", "/operations/inventory/in-transit")
        assert len(whole.json()) == 2 and "x-result-truncated" not in whole.headers


class TestOwnerColumns:
    def test_transfers_and_adjustments_compare_scope_to_the_employee_column(self) -> None:
        ctx = ctx_for(
            uuid.uuid4(),
            "operations.inventory.stock.read",
            Scope.OWN,
            subject_ref=uuid.uuid4(),
        )
        transfers_sql = str(sel.transfers_stmt(ctx).compile()).split("WHERE")[1]
        assert "requested_by_employee_id" in transfers_sql and "created_by" not in transfers_sql
        adjustments_sql = str(sel.adjustments_stmt(ctx).compile()).split("WHERE")[1]
        assert "requested_by_employee_id" in adjustments_sql
        reservations_sql = str(sel.reservations_stmt(ctx).compile()).split("WHERE")[1]
        assert "created_by" not in reservations_sql
