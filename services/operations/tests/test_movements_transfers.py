"""Inter-warehouse transfers over HTTP (inventory Phase 3): the state machine, the IN_TRANSIT
bucket, partial receipt and shortage, direct and approval-service sign-off — every stock effect
through `post_move`, the reconciler clean after each.

Authorization and cross-tenant behaviour of every route is `test_movements_authz.py`'s; this file is
what they *do*.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app import consumers
from app.config import settings
from app.models import AuditLog, InventoryMove, LedgerEntry, RollEvent
from platform_core.db import dispose_engine, init_engine
from platform_core.events import validate_event_schema
from tests.inventory_fixtures import q
from tests.movements_fixtures import (
    CONTROL_CONFIGURE,
    STOCK_READ,
    STOCK_RESERVE,
    TRANSFER_APPROVE,
    TRANSFER_DISPATCH,
    TRANSFER_RECEIVE,
    TRANSFER_WRITE,
    World,
    build_world,
    call,
    events,
    in_transit_total,
    mover,
    receive_stock,
    transfer_body,
)
from tests.tracking_fixtures import assert_reconciled, bucket_qty

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"
T = "/operations/inventory/transfers"


@pytest.fixture
async def world(session, org_id) -> World:
    w = await build_world(session, org_id)
    await receive_stock(session, w, w.t.dye, w.t.weaving, "100")
    return w


@pytest.fixture(autouse=True)
async def _engine():  # type: ignore[no-untyped-def]
    """The approval-decision tests drive `app.consumers` handlers, which open their own
    `session_scope` on the process engine."""
    init_engine(settings())
    yield
    await dispose_engine()


def dye_line(w: World, quantity: str = "40", **extra) -> dict:  # type: ignore[type-arg]
    return {
        "inventory_item_id": str(w.t.dye),
        "from_location_id": str(w.t.weaving),
        "quantity": quantity,
        **extra,
    }


async def make(client, org_id, w: World, *lines, **extra):  # type: ignore[no-untyped-def]
    """A DRAFT as a transfer clerk."""
    clerk = mover(org_id, TRANSFER_WRITE)
    resp = await call(client, clerk, "POST", T, transfer_body(w, *lines, **extra))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def approved(client, org_id, w: World, *lines, requester=None, **extra):  # type: ignore[no-untyped-def]
    """A transfer taken DRAFT -> REQUESTED -> APPROVED (direct route: no threshold set)."""
    requester = requester or mover(org_id, TRANSFER_WRITE)
    created = await call(client, requester, "POST", T, transfer_body(w, *lines, **extra))
    assert created.status_code == 201, created.text
    tid = created.json()["id"]
    requested = await call(client, requester, "POST", f"{T}/{tid}:request")
    assert requested.status_code == 200, requested.text
    assert requested.json()["approval_route"] == "DIRECT"
    manager = mover(org_id, TRANSFER_APPROVE)
    done = await call(client, manager, "POST", f"{T}/{tid}:approve", {"comment": "ok"})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "APPROVED"
    return done.json()


async def dispatched(client, org_id, w, *lines, **extra):  # type: ignore[no-untyped-def]
    body = await approved(client, org_id, w, *lines, **extra)
    keeper = mover(org_id, TRANSFER_DISPATCH)
    resp = await call(client, keeper, "POST", f"{T}/{body['id']}:dispatch", {})
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestDraft:
    async def test_a_draft_is_numbered_and_carries_exact_quantities(
        self, client, org_id, world
    ) -> None:
        body = await make(client, org_id, world, dye_line(world, "12.5"))
        assert body["status"] == "DRAFT"
        assert body["number"] == "TRF-000001"
        (line,) = body["lines"]
        assert (line["quantity"], line["dispatched"], line["received"], line["in_transit"]) == (
            "12.5",
            "0",
            "0",
            "0",
        )
        assert [t["to_status"] for t in body["transitions"]] == ["DRAFT"]
        second = await make(client, org_id, world, dye_line(world, "1"))
        assert second["number"] == "TRF-000002"

    async def test_the_two_warehouses_must_differ(self, client, org_id, world) -> None:
        clerk = mover(org_id, TRANSFER_WRITE)
        resp = await call(
            client,
            clerk,
            "POST",
            T,
            transfer_body(world, dye_line(world), to_warehouse=world.warehouse_weave),
        )
        assert resp.status_code == 422

    async def test_a_line_must_leave_from_the_source_warehouse(self, client, org_id, world) -> None:
        clerk = mover(org_id, TRANSFER_WRITE)
        resp = await call(
            client,
            clerk,
            "POST",
            T,
            transfer_body(world, dye_line(world, from_location_id=str(world.t.finishing))),
        )
        assert resp.status_code == 422
        assert "source warehouse" in resp.text

    async def test_fractions_are_refused_on_a_discrete_unit(self, client, org_id, world) -> None:
        clerk = mover(org_id, TRANSFER_WRITE)
        resp = await call(
            client,
            clerk,
            "POST",
            T,
            transfer_body(
                world,
                {
                    "inventory_item_id": str(world.t.buttons),
                    "from_location_id": str(world.t.weaving),
                    "quantity": "2.5",
                },
            ),
        )
        assert resp.status_code == 422

    async def test_a_tracked_item_must_name_its_lot_and_roll(self, client, org_id, world) -> None:
        clerk = mover(org_id, TRANSFER_WRITE)
        resp = await call(
            client,
            clerk,
            "POST",
            T,
            transfer_body(
                world,
                {
                    "inventory_item_id": str(world.t.fabric),
                    "from_location_id": str(world.t.weaving),
                    "quantity": "10",
                },
            ),
        )
        assert resp.status_code == 422

    async def test_the_same_bucket_twice_is_refused(self, client, org_id, world) -> None:
        clerk = mover(org_id, TRANSFER_WRITE)
        resp = await call(
            client, clerk, "POST", T, transfer_body(world, dye_line(world), dye_line(world, "5"))
        )
        assert resp.status_code == 422

    async def test_a_retried_create_returns_the_same_draft(self, client, org_id, world) -> None:
        clerk = mover(org_id, TRANSFER_WRITE)
        body = transfer_body(world, dye_line(world))
        first = await call(client, clerk, "POST", T, body, key="c-1")
        again = await call(client, clerk, "POST", T, body, key="c-1")
        assert (first.status_code, again.status_code) == (201, 200)
        assert again.json()["id"] == first.json()["id"] and again.json()["replayed"] is True
        other = await call(
            client, clerk, "POST", T, transfer_body(world, dye_line(world, "3")), key="c-1"
        )
        assert other.status_code == 409

    async def test_a_draft_can_be_edited_and_a_requested_one_cannot(
        self, client, org_id, world
    ) -> None:
        draft = await make(client, org_id, world, dye_line(world))
        clerk = mover(org_id, TRANSFER_WRITE)
        patched = await call(
            client,
            clerk,
            "PATCH",
            f"{T}/{draft['id']}",
            {"note": "urgent", "lines": [dye_line(world, "7")]},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["note"] == "urgent"
        assert [ln["quantity"] for ln in patched.json()["lines"]] == ["7"]
        await call(client, clerk, "POST", f"{T}/{draft['id']}:request")
        locked = await call(client, clerk, "PATCH", f"{T}/{draft['id']}", {"note": "late"})
        assert locked.status_code == 409


class TestStateMachine:
    async def test_the_happy_path_moves_stock_out_into_transit_and_in(
        self, client, session, org_id, world
    ) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "40"))
        assert body["status"] == "IN_TRANSIT"
        assert (body["lines"][0]["dispatched"], body["lines"][0]["in_transit"]) == ("40", "40")
        # Out of the source immediately, into the destination's IN_TRANSIT bucket — and *not*
        # into the destination's physical stock.
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("60")
        assert (await bucket_qty(session, world, item=world.t.dye, location=world.t.finishing))[
            0
        ] == 0
        assert (
            await bucket_qty(
                session,
                world,
                item=world.t.dye,
                location=world.t.finishing,
                status="IN_TRANSIT",
            )
        )[0] == q("40")
        assert await in_transit_total(session, org_id, world.t.dye) == q("40")
        await assert_reconciled(session, org_id)

        keeper = mover(org_id, TRANSFER_RECEIVE)
        line_id = body["lines"][0]["id"]
        done = await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {"lines": [{"line_id": line_id, "received_quantity": "40"}]},
        )
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "RECEIVED"
        assert (await bucket_qty(session, world, item=world.t.dye, location=world.t.finishing))[
            0
        ] == q("40")
        assert await in_transit_total(session, org_id, world.t.dye) == 0
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("60")
        await assert_reconciled(session, org_id)
        trail = [t["to_status"] for t in done.json()["transitions"]]
        assert trail == ["DRAFT", "REQUESTED", "APPROVED", "IN_TRANSIT", "RECEIVED"]

    async def test_an_unrequested_draft_cannot_be_approved_or_dispatched(
        self, client, org_id, world
    ) -> None:
        draft = await make(client, org_id, world, dye_line(world))
        manager = mover(org_id, TRANSFER_APPROVE)
        keeper = mover(org_id, TRANSFER_DISPATCH)
        assert (
            await call(client, manager, "POST", f"{T}/{draft['id']}:approve")
        ).status_code == 409
        assert (
            await call(client, keeper, "POST", f"{T}/{draft['id']}:dispatch")
        ).status_code == 409

    async def test_a_requested_transfer_cannot_be_dispatched_before_approval(
        self, client, org_id, world
    ) -> None:
        clerk = mover(org_id, TRANSFER_WRITE)
        draft = await make(client, org_id, world, dye_line(world))
        await call(client, clerk, "POST", f"{T}/{draft['id']}:request")
        keeper = mover(org_id, TRANSFER_DISPATCH)
        resp = await call(client, keeper, "POST", f"{T}/{draft['id']}:dispatch")
        assert resp.status_code == 409

    async def test_a_transfer_with_no_lines_cannot_be_requested(
        self, client, session, org_id, world
    ) -> None:
        # Lines are mandatory at creation; this proves the request itself re-checks.
        draft = await make(client, org_id, world, dye_line(world))
        async with session.begin():
            from sqlalchemy import delete

            from app.models import StockTransferLine

            await session.execute(
                delete(StockTransferLine).where(
                    StockTransferLine.organization_id == org_id,
                    StockTransferLine.transfer_id == uuid.UUID(draft["id"]),
                )
            )
        clerk = mover(org_id, TRANSFER_WRITE)
        assert (await call(client, clerk, "POST", f"{T}/{draft['id']}:request")).status_code == 422

    async def test_the_requester_cannot_approve_their_own_transfer(
        self, client, org_id, world
    ) -> None:
        # One person holding both permissions: still not their own.
        both = mover(org_id, TRANSFER_WRITE, TRANSFER_APPROVE)
        created = await call(client, both, "POST", T, transfer_body(world, dye_line(world)))
        tid = created.json()["id"]
        await call(client, both, "POST", f"{T}/{tid}:request")
        resp = await call(client, both, "POST", f"{T}/{tid}:approve")
        assert resp.status_code == 409
        assert "requested" in resp.text

    async def test_a_direct_rejection_needs_a_reason_and_is_terminal(
        self, client, org_id, world
    ) -> None:
        clerk = mover(org_id, TRANSFER_WRITE)
        manager = mover(org_id, TRANSFER_APPROVE)
        draft = await make(client, org_id, world, dye_line(world))
        await call(client, clerk, "POST", f"{T}/{draft['id']}:request")
        assert (
            await call(client, manager, "POST", f"{T}/{draft['id']}:reject", {"comment": " "})
        ).status_code == 422
        rejected = await call(
            client, manager, "POST", f"{T}/{draft['id']}:reject", {"comment": "no budget"}
        )
        assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
        again = await call(client, manager, "POST", f"{T}/{draft['id']}:approve")
        assert again.status_code == 409

    async def test_cancel_before_dispatch_and_not_after(
        self, client, session, org_id, world
    ) -> None:
        clerk = mover(org_id, TRANSFER_WRITE)
        early = await make(client, org_id, world, dye_line(world))
        done = await call(
            client, clerk, "POST", f"{T}/{early['id']}:cancel", {"reason": "changed my mind"}
        )
        assert done.status_code == 200 and done.json()["status"] == "CANCELLED"
        assert done.json()["cancel_reason"] == "changed my mind"
        keeper = mover(org_id, TRANSFER_DISPATCH)
        assert (
            await call(client, keeper, "POST", f"{T}/{early['id']}:dispatch")
        ).status_code == 409

        flying = await dispatched(client, org_id, world, dye_line(world, "5"))
        late = await call(
            client, clerk, "POST", f"{T}/{flying['id']}:cancel", {"reason": "too late"}
        )
        assert late.status_code == 409
        assert "in transit" in late.text
        await assert_reconciled(session, org_id)

    async def test_dispatch_is_refused_when_the_stock_is_not_there(
        self, client, org_id, world
    ) -> None:
        body = await approved(client, org_id, world, dye_line(world, "500"))
        keeper = mover(org_id, TRANSFER_DISPATCH)
        resp = await call(client, keeper, "POST", f"{T}/{body['id']}:dispatch")
        assert resp.status_code == 409
        assert resp.json()["type"].endswith("insufficient-stock")

    async def test_reserved_stock_cannot_be_dispatched_and_the_refusal_names_the_reservation(
        self, client, session, org_id, world
    ) -> None:
        holder = mover(org_id, STOCK_RESERVE)
        reserved = await call(
            client,
            holder,
            "POST",
            "/operations/inventory/reservations",
            {
                "inventory_item_id": str(world.t.dye),
                "location_id": str(world.t.weaving),
                "quantity": "80",
            },
        )
        assert reserved.status_code == 201, reserved.text
        body = await approved(client, org_id, world, dye_line(world, "40"))
        keeper = mover(org_id, TRANSFER_DISPATCH)
        resp = await call(client, keeper, "POST", f"{T}/{body['id']}:dispatch")
        assert resp.status_code == 409
        assert (
            resp.json()["errors"][0]["reservations"][0]["reservation_id"] == (reserved.json()["id"])
        )
        assert await in_transit_total(session, org_id, world.t.dye) == 0

    async def test_a_retried_dispatch_with_a_key_is_answered_not_refused(
        self, client, session, org_id, world
    ) -> None:
        body = await approved(client, org_id, world, dye_line(world, "10"))
        keeper = mover(org_id, TRANSFER_DISPATCH)
        first = await call(client, keeper, "POST", f"{T}/{body['id']}:dispatch", {}, key="d-1")
        again = await call(client, keeper, "POST", f"{T}/{body['id']}:dispatch", {}, key="d-1")
        assert first.status_code == 200 and again.status_code == 200
        assert again.json()["replayed"] is True
        # Without a key the repeat is a plain conflict, and the stock moved exactly once.
        bare = await call(client, keeper, "POST", f"{T}/{body['id']}:dispatch", {})
        assert bare.status_code == 409
        assert await in_transit_total(session, org_id, world.t.dye) == q("10")
        await assert_reconciled(session, org_id)


class TestPartialReceiptAndShortage:
    async def test_partial_receipts_leave_the_rest_in_transit_until_settled(
        self, client, session, org_id, world
    ) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "40"))
        line_id = body["lines"][0]["id"]
        keeper = mover(org_id, TRANSFER_RECEIVE)

        first = await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {"lines": [{"line_id": line_id, "received_quantity": "25"}]},
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "IN_TRANSIT"
        assert first.json()["lines"][0]["in_transit"] == "15"
        assert await in_transit_total(session, org_id, world.t.dye) == q("15")
        assert (await bucket_qty(session, world, item=world.t.dye, location=world.t.finishing))[
            0
        ] == q("25")

        second = await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {"lines": [{"line_id": line_id, "received_quantity": "10", "shortage_quantity": "5"}]},
        )
        assert second.status_code == 200, second.text
        out = second.json()
        assert out["status"] == "RECEIVED"
        assert (out["lines"][0]["received"], out["lines"][0]["shortage"]) == ("35", "5")
        assert out["lines"][0]["in_transit"] == "0"
        # 100 owned before; 5 lost in transit; 60 left behind + 35 arrived.
        assert await in_transit_total(session, org_id, world.t.dye) == 0
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("60")
        assert (await bucket_qty(session, world, item=world.t.dye, location=world.t.finishing))[
            0
        ] == q("35")
        async with session.begin():
            kinds = (
                await session.execute(
                    select(InventoryMove.kind, InventoryMove.reference_type).where(
                        InventoryMove.organization_id == org_id,
                        InventoryMove.reference_id == uuid.UUID(body["id"]),
                    )
                )
            ).all()
        assert ("adjustment", "stock_transfer_shortage") in kinds
        assert sum(1 for _k, r in kinds if r == "stock_transfer_receipt") == 2
        await assert_reconciled(session, org_id)

    async def test_a_shortage_alone_closes_a_line(self, client, session, org_id, world) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "10"))
        line_id = body["lines"][0]["id"]
        keeper = mover(org_id, TRANSFER_RECEIVE)
        resp = await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {"lines": [{"line_id": line_id, "shortage_quantity": "10"}]},
        )
        assert resp.status_code == 200 and resp.json()["status"] == "RECEIVED"
        assert await in_transit_total(session, org_id, world.t.dye) == 0
        assert (await bucket_qty(session, world, item=world.t.dye, location=world.t.finishing))[
            0
        ] == 0
        await assert_reconciled(session, org_id)

    async def test_a_shortage_over_the_adjustment_threshold_is_not_booked_as_settled(
        self, client, session, org_id, world
    ) -> None:
        await TestApprovalThreshold()._configure(client, org_id, None, "5")
        body = await dispatched(client, org_id, world, dye_line(world, "10"))
        line_id = body["lines"][0]["id"]
        keeper = mover(org_id, TRANSFER_RECEIVE, employee=True)
        resp = await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {
                "lines": [{"line_id": line_id, "received_quantity": "5", "shortage_quantity": "5"}],
                "evidence_document_id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 200, resp.text
        out = resp.json()
        # The 5 kg are still in transit pending the adjustment's approval: the line is not
        # closed and the transfer is not complete.
        assert out["status"] == "IN_TRANSIT"
        assert (out["lines"][0]["received"], out["lines"][0]["shortage"]) == ("5", "0")
        assert await in_transit_total(session, org_id, world.t.dye) == q("5")
        await assert_reconciled(session, org_id)

    async def test_more_than_is_in_transit_is_refused_with_the_numbers(
        self, client, org_id, world
    ) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "10"))
        line_id = body["lines"][0]["id"]
        keeper = mover(org_id, TRANSFER_RECEIVE)
        resp = await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {"lines": [{"line_id": line_id, "received_quantity": "8", "shortage_quantity": "3"}]},
        )
        assert resp.status_code == 409
        assert "only 10" in resp.text

    async def test_a_receipt_into_another_warehouses_bin_is_refused(
        self, client, org_id, world
    ) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "10"))
        keeper = mover(org_id, TRANSFER_RECEIVE)
        resp = await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {
                "lines": [
                    {
                        "line_id": body["lines"][0]["id"],
                        "received_quantity": "10",
                        "to_location_id": str(world.bin_b),
                    }
                ]
            },
        )
        assert resp.status_code == 422

    async def test_an_empty_line_is_refused(self, client, org_id, world) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "10"))
        keeper = mover(org_id, TRANSFER_RECEIVE)
        resp = await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {"lines": [{"line_id": body["lines"][0]["id"]}]},
        )
        assert resp.status_code == 422

    async def test_a_retried_receipt_changes_nothing_more(
        self, client, session, org_id, world
    ) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "20"))
        line_id = body["lines"][0]["id"]
        keeper = mover(org_id, TRANSFER_RECEIVE)
        payload = {"lines": [{"line_id": line_id, "received_quantity": "8"}]}
        first = await call(client, keeper, "POST", f"{T}/{body['id']}:receive", payload, key="r-1")
        again = await call(client, keeper, "POST", f"{T}/{body['id']}:receive", payload, key="r-1")
        assert first.status_code == 200 and again.status_code == 200
        assert again.json()["replayed"] is True
        assert again.json()["lines"][0]["received"] == "8"
        assert await in_transit_total(session, org_id, world.t.dye) == q("12")
        await assert_reconciled(session, org_id)

    async def test_receiving_a_transfer_that_is_not_in_transit_is_a_conflict(
        self, client, org_id, world
    ) -> None:
        body = await approved(client, org_id, world, dye_line(world, "10"))
        keeper = mover(org_id, TRANSFER_RECEIVE)
        resp = await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {"lines": [{"line_id": str(uuid.uuid4()), "received_quantity": "1"}]},
        )
        assert resp.status_code == 409


class TestTrackedStock:
    async def test_a_roll_keeps_its_lot_and_roll_through_transit_and_leaves_a_trail(
        self, client, session, org_id, world
    ) -> None:
        line = {
            "inventory_item_id": str(world.t.fabric),
            "from_location_id": str(world.t.weaving),
            "quantity": "82.5",
            "lot_id": str(world.lot),
            "roll_id": str(world.roll),
        }
        body = await dispatched(client, org_id, world, line)
        assert (await bucket_qty(session, world, lot=world.lot, roll=world.roll))[0] == 0
        assert (
            await bucket_qty(
                session,
                world,
                location=world.t.finishing,
                status="IN_TRANSIT",
                lot=world.lot,
                roll=world.roll,
            )
        )[0] == q("82.5")
        keeper = mover(org_id, TRANSFER_RECEIVE)
        done = await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {"lines": [{"line_id": body["lines"][0]["id"], "received_quantity": "82.5"}]},
        )
        assert done.status_code == 200 and done.json()["status"] == "RECEIVED"
        assert (
            await bucket_qty(
                session, world, location=world.t.finishing, lot=world.lot, roll=world.roll
            )
        )[0] == q("82.5")
        async with session.begin():
            phases = sorted(
                e.detail["phase"]
                for e in (
                    await session.execute(
                        select(RollEvent).where(
                            RollEvent.organization_id == org_id,
                            RollEvent.roll_id == world.roll,
                            RollEvent.kind == "MOVED",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert phases == ["dispatch", "receive"]
        await assert_reconciled(session, org_id)

    async def test_two_lines_feeding_one_in_transit_bucket_are_folded(
        self, client, session, org_id, world
    ) -> None:
        # Same item, two source bins -> one IN_TRANSIT bucket at the destination's default bin.
        await receive_stock(session, world, world.t.dye, world.bin_b, "30")
        body = await dispatched(
            client,
            org_id,
            world,
            dye_line(world, "10"),
            dye_line(world, "20", from_location_id=str(world.bin_b)),
        )
        assert await in_transit_total(session, org_id, world.t.dye) == q("30")
        keeper = mover(org_id, TRANSFER_RECEIVE)
        done = await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {
                "lines": [
                    {"line_id": ln["id"], "received_quantity": ln["quantity"]}
                    for ln in body["lines"]
                ]
            },
        )
        assert done.status_code == 200 and done.json()["status"] == "RECEIVED"
        assert await in_transit_total(session, org_id, world.t.dye) == 0
        await assert_reconciled(session, org_id)


class TestInTransitReport:
    async def test_in_transit_and_physical_are_reported_separately_and_reconcile(
        self, client, session, org_id, world
    ) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "40"))
        reader = mover(org_id, STOCK_READ)
        resp = await call(client, reader, "GET", "/operations/inventory/in-transit")
        assert resp.status_code == 200
        (row,) = resp.json()
        assert row["sku"] == "DYE-RED-01" and row["warehouse_code"] == "WH-FIN"
        # 40 on its way to Finishing; none of it is Finishing's physical stock yet.
        assert (row["in_transit"], row["physical"]) == ("40", "0")
        # What the source warehouse still holds is its own, separate figure.
        src = await call(
            client,
            reader,
            "GET",
            f"/operations/inventory/in-transit?warehouse_id={world.warehouse_weave}",
        )
        assert src.json() == []
        keeper = mover(org_id, TRANSFER_RECEIVE)
        await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {"lines": [{"line_id": body["lines"][0]["id"], "received_quantity": "40"}]},
        )
        assert (await call(client, reader, "GET", "/operations/inventory/in-transit")).json() == []
        # Owned in total never changed: 60 + 40 physical, nothing in transit.
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("60")


class TestListAndRead:
    async def test_the_list_filters_and_pages(self, client, org_id, world) -> None:
        for n in range(3):
            await make(client, org_id, world, dye_line(world, str(n + 1)))
        reader = mover(org_id, STOCK_READ)
        page = await call(client, reader, "GET", f"{T}?limit=2")
        assert page.status_code == 200
        assert len(page.json()["items"]) == 2 and page.json()["next_cursor"]
        rest = await call(client, reader, "GET", f"{T}?limit=2&cursor={page.json()['next_cursor']}")
        assert len(rest.json()["items"]) == 1 and rest.json()["next_cursor"] is None
        drafts = await call(client, reader, "GET", f"{T}?status=DRAFT")
        assert len(drafts.json()["items"]) == 3
        none = await call(client, reader, "GET", f"{T}?status=RECEIVED")
        assert none.json()["items"] == []
        by_item = await call(client, reader, "GET", f"{T}?item_id={world.t.dye}")
        assert len(by_item.json()["items"]) == 3
        by_number = await call(client, reader, "GET", f"{T}?q=trf-000002")
        assert [i["number"] for i in by_number.json()["items"]] == ["TRF-000002"]

    async def test_a_malformed_cursor_is_a_422(self, client, org_id, world) -> None:
        reader = mover(org_id, STOCK_READ)
        assert (await call(client, reader, "GET", f"{T}?cursor=garbage")).status_code == 422


class TestApprovalThreshold:
    async def _configure(self, client, org_id, transfer: str | None, adjustment: str | None = None):  # type: ignore[no-untyped-def]
        admin = mover(org_id, CONTROL_CONFIGURE)
        resp = await call(
            client,
            admin,
            "PUT",
            "/operations/inventory/control-settings",
            {
                "transfer_approval_threshold": transfer,
                "adjustment_approval_threshold": adjustment,
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    async def test_under_the_threshold_a_transfer_is_approved_on_the_spot(
        self, client, org_id, world
    ) -> None:
        await self._configure(client, org_id, "50")
        clerk = mover(org_id, TRANSFER_WRITE)
        created = await call(client, clerk, "POST", T, transfer_body(world, dye_line(world, "10")))
        resp = await call(client, clerk, "POST", f"{T}/{created.json()['id']}:request")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "APPROVED"
        assert resp.json()["approval_route"] == "AUTO"
        assert resp.json()["approved_by"] == "auto"

    async def test_at_or_over_the_threshold_the_approval_service_decides(
        self, client, session, org_id, world
    ) -> None:
        await self._configure(client, org_id, "40")
        clerk = mover(org_id, TRANSFER_WRITE, employee=True)
        created = await call(client, clerk, "POST", T, transfer_body(world, dye_line(world, "40")))
        tid = created.json()["id"]
        resp = await call(client, clerk, "POST", f"{T}/{tid}:request")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "REQUESTED"
        assert resp.json()["approval_route"] == "SERVICE"
        assert resp.json()["awaiting_approval"] is True

        # A direct sign-off is refused: the approval service owns this decision.
        manager = mover(org_id, TRANSFER_APPROVE)
        assert (await call(client, manager, "POST", f"{T}/{tid}:approve")).status_code == 409
        assert (
            await call(client, manager, "POST", f"{T}/{tid}:reject", {"comment": "no"})
        ).status_code == 409

        (event,) = await events(session, "operations.inventory.transfer.requested")
        payload = dict(event.payload)
        validate_event_schema(event.event_type, 1, payload, contracts_root=CONTRACTS_ROOT)
        assert payload["approval_required"] is True
        assert payload["employee_id"] == str(clerk.subject_ref)
        assert payload["summary"]["largest_line"] == "40 kg DYE-RED-01"

    async def test_a_request_needing_approval_from_an_unlinked_account_is_refused(
        self, client, org_id, world
    ) -> None:
        await self._configure(client, org_id, "5")
        clerk = mover(org_id, TRANSFER_WRITE)  # no employee link
        created = await call(client, clerk, "POST", T, transfer_body(world, dye_line(world, "10")))
        resp = await call(client, clerk, "POST", f"{T}/{created.json()['id']}:request")
        assert resp.status_code == 409
        assert "employee" in resp.text

    async def test_the_approval_services_answer_applies_and_is_acknowledged(
        self, client, session, org_id, world
    ) -> None:
        await self._configure(client, org_id, "40")
        clerk = mover(org_id, TRANSFER_WRITE, employee=True)
        created = await call(client, clerk, "POST", T, transfer_body(world, dye_line(world, "40")))
        tid = created.json()["id"]
        await call(client, clerk, "POST", f"{T}/{tid}:request")
        request_id = uuid.uuid4()

        await consumers.approval_request_opened(
            _envelope(
                "core.approval.request.opened",
                org_id,
                {
                    "request_id": str(request_id),
                    "request_type": "operations.inventory.transfer",
                    "source_ref": tid,
                    "approver_name": "Meera Shah",
                },
            )
        )
        decided = _envelope(
            "core.approval.request.decided",
            org_id,
            {
                "request_id": str(request_id),
                "request_type": "operations.inventory.transfer",
                "source_ref": tid,
                "outcome": "approved",
                "decided_by_name": "Meera Shah",
                "comment": "fine",
            },
        )
        await consumers.approval_request_decided(decided)
        reader = mover(org_id, STOCK_READ)
        got = (await call(client, reader, "GET", f"{T}/{tid}")).json()
        assert got["status"] == "APPROVED"
        assert got["approver_name"] == "Meera Shah" and got["approval_request_id"] == str(
            request_id
        )
        assert got["decision_comment"] == "fine"

        # Redelivery of the same event, and a late decision, change nothing.
        await consumers.approval_request_decided(decided)
        await consumers.approval_request_decided(
            _envelope(
                "core.approval.request.decided",
                org_id,
                {**decided["payload"], "outcome": "rejected"},
            )
        )
        assert (await call(client, reader, "GET", f"{T}/{tid}")).json()["status"] == "APPROVED"

        applied = await events(session, "operations.inventory.transfer.applied")
        assert len(applied) == 1
        validate_event_schema(
            applied[0].event_type, 1, dict(applied[0].payload), contracts_root=CONTRACTS_ROOT
        )
        assert (applied[0].payload["outcome"], applied[0].payload["decision"]) == (
            "applied",
            "approved",
        )
        # And it can now be dispatched.
        keeper = mover(org_id, TRANSFER_DISPATCH)
        assert (await call(client, keeper, "POST", f"{T}/{tid}:dispatch")).status_code == 200

    async def test_a_rejection_from_the_approval_service_is_terminal(
        self, client, org_id, world
    ) -> None:
        await self._configure(client, org_id, "40")
        clerk = mover(org_id, TRANSFER_WRITE, employee=True)
        created = await call(client, clerk, "POST", T, transfer_body(world, dye_line(world, "45")))
        tid = created.json()["id"]
        await call(client, clerk, "POST", f"{T}/{tid}:request")
        await consumers.approval_request_decided(
            _envelope(
                "core.approval.request.decided",
                org_id,
                {
                    "request_id": str(uuid.uuid4()),
                    "request_type": "operations.inventory.transfer",
                    "source_ref": tid,
                    "outcome": "rejected",
                    "decided_by_name": "Meera Shah",
                    "comment": "not this quarter",
                },
            )
        )
        got = (await call(client, mover(org_id, STOCK_READ), "GET", f"{T}/{tid}")).json()
        assert got["status"] == "REJECTED" and got["decision_comment"] == "not this quarter"

    async def test_an_event_for_another_tenant_or_another_request_type_touches_nothing(
        self, client, org_id, world
    ) -> None:
        await self._configure(client, org_id, "40")
        clerk = mover(org_id, TRANSFER_WRITE, employee=True)
        created = await call(client, clerk, "POST", T, transfer_body(world, dye_line(world, "45")))
        tid = created.json()["id"]
        await call(client, clerk, "POST", f"{T}/{tid}:request")
        body = {
            "request_id": str(uuid.uuid4()),
            "request_type": "operations.inventory.transfer",
            "source_ref": tid,
            "outcome": "approved",
        }
        # The envelope says another tenant: refused, not trusted.
        forged = _envelope("core.approval.request.decided", uuid.uuid4(), body)
        forged["payload"]["organization_id"] = str(org_id)
        await consumers.approval_request_decided(forged)
        # Someone else's request type.
        await consumers.approval_request_decided(
            _envelope(
                "core.approval.request.decided",
                org_id,
                {**body, "request_type": "hrms.time.leave"},
            )
        )
        got = (await call(client, mover(org_id, STOCK_READ), "GET", f"{T}/{tid}")).json()
        assert got["status"] == "REQUESTED"

    async def test_changing_the_thresholds_is_audited(self, client, session, org_id) -> None:
        await self._configure(client, org_id, "40", "10")
        await self._configure(client, org_id, None, None)
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.action == "inventory_control_settings.updated",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2
        assert {r.after["transfer_approval_threshold_milli"] for r in rows} == {40_000, None}
        reader = mover(org_id, STOCK_READ)
        cleared = await call(client, reader, "GET", "/operations/inventory/control-settings")
        assert cleared.json()["transfer_approval_threshold"] is None

    async def test_a_zero_or_malformed_threshold_is_refused(self, client, org_id) -> None:
        admin = mover(org_id, CONTROL_CONFIGURE)
        for bad in ("0", "-5", "1.2345", "abc"):
            resp = await call(
                client,
                admin,
                "PUT",
                "/operations/inventory/control-settings",
                {"transfer_approval_threshold": bad},
            )
            assert resp.status_code == 422, bad


class TestEventsAndAudit:
    async def test_every_lifecycle_event_matches_its_committed_contract(
        self, client, session, org_id, world
    ) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "40"))
        keeper = mover(org_id, TRANSFER_RECEIVE)
        await call(
            client,
            keeper,
            "POST",
            f"{T}/{body['id']}:receive",
            {
                "lines": [
                    {
                        "line_id": body["lines"][0]["id"],
                        "received_quantity": "35",
                        "shortage_quantity": "5",
                    }
                ]
            },
        )
        cancelled = await make(client, org_id, world, dye_line(world, "1"))
        await call(
            client,
            mover(org_id, TRANSFER_WRITE),
            "POST",
            f"{T}/{cancelled['id']}:cancel",
            {"reason": "x"},
        )
        seen = set()
        for event_type in (
            "operations.inventory.transfer.requested",
            "operations.inventory.transfer.approved",
            "operations.inventory.transfer.dispatched",
            "operations.inventory.transfer.received",
            "operations.inventory.transfer.cancelled",
        ):
            found = await events(session, event_type)
            assert found, event_type
            for event in found:
                validate_event_schema(
                    event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
                )
                seen.add(event_type)
        assert len(seen) == 5
        # The kernel's own `stock.posted` accompanied each of the three moves.
        assert len(await events(session, "operations.inventory.stock.posted")) >= 4

    async def test_every_step_is_audited_and_the_transition_trail_is_append_only(
        self, client, session, org_id, world
    ) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "5"))
        async with session.begin():
            actions = {
                a
                for (a,) in (
                    await session.execute(
                        select(AuditLog.action).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.entity == "stock_transfer",
                            AuditLog.entity_id == uuid.UUID(body["id"]),
                        )
                    )
                ).all()
            }
        assert {
            "stock_transfer.created",
            "stock_transfer.requested",
            "stock_transfer.approved",
            "stock_transfer.dispatched",
        } <= actions
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError):
            async with session.begin():
                await session.execute(
                    text("UPDATE operations.stock_transfer_transitions SET note = 'x'")
                )

    async def test_the_ledger_rows_of_a_transfer_all_name_it(
        self, client, session, org_id, world
    ) -> None:
        body = await dispatched(client, org_id, world, dye_line(world, "5"))
        async with session.begin():
            moves = (
                (
                    await session.execute(
                        select(InventoryMove).where(
                            InventoryMove.organization_id == org_id,
                            InventoryMove.reference_type == "stock_transfer",
                        )
                    )
                )
                .scalars()
                .all()
            )
            (move,) = moves
            assert move.reference_id == uuid.UUID(body["id"])
            rows = (
                (await session.execute(select(LedgerEntry).where(LedgerEntry.move_id == move.id)))
                .scalars()
                .all()
            )
        assert sorted(r.quantity_milli for r in rows) == [-q("5"), q("5")]
        assert {r.status for r in rows} == {"AVAILABLE", "IN_TRANSIT"}


def _envelope(event_type: str, org_id: uuid.UUID, payload: dict) -> dict:  # type: ignore[type-arg]
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "organization_id": str(org_id),
        "payload": {"organization_id": str(org_id), **payload},
    }
