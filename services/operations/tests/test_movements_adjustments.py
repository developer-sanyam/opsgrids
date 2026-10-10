"""Adjustments with a reason and evidence, applied at once or held for the approval service
(inventory Phase 3) — and the approval service's answer applied through the real consumer.

Every posting is one `adjustment` move through `ledger.post_move` under a fixed idempotency key; a
redelivered decision cannot post twice, and the reconciler is clean after each.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import consumers
from app.config import settings
from app.inventory import ledger
from app.inventory.ledger import MoveLine
from app.models import AuditLog, InventoryMove
from platform_core.db import dispose_engine, init_engine
from platform_core.events import validate_event_schema
from tests.inventory_fixtures import TODAY, q
from tests.movements_fixtures import (
    CONTROL_CONFIGURE,
    STOCK_ADJUST,
    STOCK_READ,
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
A = "/operations/inventory/adjustments"


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


def body(w: World, delta: str = "-5", **extra) -> dict:  # type: ignore[type-arg]
    return {
        "inventory_item_id": str(w.t.dye),
        "location_id": str(w.t.weaving),
        "quantity_delta": delta,
        "reason": "damaged",
        **extra,
    }


async def configure(client, org_id, *, adjustment: str | None) -> None:  # type: ignore[no-untyped-def]
    resp = await call(
        client,
        mover(org_id, CONTROL_CONFIGURE),
        "PUT",
        "/operations/inventory/control-settings",
        {"adjustment_approval_threshold": adjustment},
    )
    assert resp.status_code == 200, resp.text


def envelope(event_type: str, org_id: uuid.UUID, payload: dict) -> dict:  # type: ignore[type-arg]
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "organization_id": str(org_id),
        "payload": {"organization_id": str(org_id), **payload},
    }


def decision(org_id, adjustment_id: str, outcome: str, request_id=None, **extra):  # type: ignore[no-untyped-def]
    return envelope(
        "core.approval.request.decided",
        org_id,
        {
            "request_id": str(request_id or uuid.uuid4()),
            "request_type": "operations.inventory.adjustment",
            "source_ref": adjustment_id,
            "outcome": outcome,
            "decided_by_name": "Meera Shah",
            "comment": "reviewed",
            **extra,
        },
    )


async def pending(client, org_id, w: World, delta: str = "-25"):  # type: ignore[no-untyped-def]
    """An adjustment held for approval: threshold 20, a 25 kg write-off with its evidence."""
    await configure(client, org_id, adjustment="20")
    clerk = mover(org_id, STOCK_ADJUST, employee=True)
    resp = await call(
        client, clerk, "POST", A, body(w, delta, evidence_document_id=str(uuid.uuid4()))
    )
    assert resp.status_code == 202, resp.text
    return resp.json(), clerk


class TestAppliedAtOnce:
    async def test_with_no_threshold_an_adjustment_applies_through_the_kernel(
        self, client, session, org_id, world
    ) -> None:
        clerk = mover(org_id, STOCK_ADJUST)
        resp = await call(client, clerk, "POST", A, body(world, "-5", note="split drum"))
        assert resp.status_code == 201, resp.text
        out = resp.json()
        assert out["status"] == "APPLIED" and out["number"] == "ADJ-000001"
        assert out["quantity_delta"] == "-5" and out["reason"] == "damaged"
        assert out["approval_required"] is False and out["applied_move_id"]
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("95")
        async with session.begin():
            move = (
                await session.execute(
                    select(InventoryMove).where(
                        InventoryMove.id == uuid.UUID(out["applied_move_id"])
                    )
                )
            ).scalar_one()
        assert (move.kind, move.reason, move.reference_type) == (
            "adjustment",
            "damaged",
            "adjustment_request",
        )
        await assert_reconciled(session, org_id)

    async def test_a_positive_adjustment_adds_stock(self, client, session, org_id, world) -> None:
        clerk = mover(org_id, STOCK_ADJUST)
        resp = await call(client, clerk, "POST", A, body(world, "2.5", reason="found"))
        assert resp.status_code == 201
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("102.5")

    async def test_under_the_threshold_it_still_applies_at_once(
        self, client, session, org_id, world
    ) -> None:
        await configure(client, org_id, adjustment="20")
        clerk = mover(org_id, STOCK_ADJUST)
        resp = await call(client, clerk, "POST", A, body(world, "-19.999"))
        assert resp.status_code == 201 and resp.json()["status"] == "APPLIED"

    async def test_more_than_is_there_is_refused_and_leaves_no_trace(
        self, client, session, org_id, world
    ) -> None:
        clerk = mover(org_id, STOCK_ADJUST)
        resp = await call(client, clerk, "POST", A, body(world, "-500"))
        assert resp.status_code == 409
        assert resp.json()["type"].endswith("insufficient-stock")
        listed = await call(client, mover(org_id, STOCK_READ), "GET", A)
        assert listed.json()["items"] == []
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("100")

    async def test_the_reason_is_a_known_code_and_the_quantity_a_nonzero_decimal_string(
        self, client, org_id, world
    ) -> None:
        clerk = mover(org_id, STOCK_ADJUST)
        for bad in (
            body(world, "-5", reason="whim"),
            body(world, "0"),
            body(world, "1.2345"),
            {**body(world), "quantity_delta": -5},
            {k: v for k, v in body(world).items() if k != "reason"},
        ):
            assert (await call(client, clerk, "POST", A, bad)).status_code == 422, bad

    async def test_a_tracked_item_must_name_its_lot_and_roll(self, client, org_id, world) -> None:
        clerk = mover(org_id, STOCK_ADJUST)
        resp = await call(
            client,
            clerk,
            "POST",
            A,
            {
                "inventory_item_id": str(world.t.fabric),
                "location_id": str(world.t.weaving),
                "quantity_delta": "-2",
                "reason": "damaged",
            },
        )
        assert resp.status_code == 422
        named = await call(
            client,
            clerk,
            "POST",
            A,
            {
                "inventory_item_id": str(world.t.fabric),
                "location_id": str(world.t.weaving),
                "quantity_delta": "-2.5",
                "reason": "damaged",
                "lot_id": str(world.lot),
                "roll_id": str(world.roll),
            },
        )
        assert named.status_code == 201, named.text

    async def test_a_retry_replays_and_posts_once(self, client, session, org_id, world) -> None:
        clerk = mover(org_id, STOCK_ADJUST)
        first = await call(client, clerk, "POST", A, body(world, "-5"), key="adj-1")
        again = await call(client, clerk, "POST", A, body(world, "-5"), key="adj-1")
        assert (first.status_code, again.status_code) == (201, 200)
        assert again.json()["id"] == first.json()["id"] and again.json()["replayed"] is True
        different = await call(client, clerk, "POST", A, body(world, "-6"), key="adj-1")
        assert different.status_code == 409
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("95")
        async with session.begin():
            moves = (
                await session.execute(
                    select(func.count())
                    .select_from(InventoryMove)
                    .where(InventoryMove.reference_type == "adjustment_request")
                )
            ).scalar_one()
        assert moves == 1

    async def test_the_applied_event_audit_and_contract(
        self, client, session, org_id, world
    ) -> None:
        clerk = mover(org_id, STOCK_ADJUST)
        resp = await call(client, clerk, "POST", A, body(world, "-5"))
        (event,) = await events(session, "operations.inventory.adjustment.applied")
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )
        assert event.payload["status"] == "APPLIED" and event.payload["approval_request_id"] is None
        async with session.begin():
            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "adjustment_request.applied",
                        AuditLog.entity_id == uuid.UUID(resp.json()["id"]),
                    )
                )
            ).scalar_one()
        assert audit.after["status"] == "APPLIED"


class TestHeldForApproval:
    async def test_at_the_threshold_it_waits_and_no_stock_moves(
        self, client, session, org_id, world
    ) -> None:
        out, clerk = await pending(client, org_id, world, "-25")
        assert out["status"] == "PENDING_APPROVAL" and out["approval_required"] is True
        assert out["applied_move_id"] is None
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("100")
        (event,) = await events(session, "operations.inventory.adjustment.requested")
        payload = dict(event.payload)
        validate_event_schema(event.event_type, 1, payload, contracts_root=CONTRACTS_ROOT)
        assert payload["employee_id"] == str(clerk.subject_ref)
        assert payload["summary"]["quantity_delta"] == "-25"
        assert payload["evidence_document_id"] == out["evidence_document_id"]
        # Exactly at the threshold counts as at-or-over.
        at = await call(
            client,
            clerk,
            "POST",
            A,
            body(world, "20", reason="found", evidence_document_id=str(uuid.uuid4())),
        )
        assert at.status_code == 202

    async def test_approval_needs_evidence_and_an_employee_record(
        self, client, org_id, world
    ) -> None:
        await configure(client, org_id, adjustment="20")
        clerk = mover(org_id, STOCK_ADJUST, employee=True)
        no_evidence = await call(client, clerk, "POST", A, body(world, "-25"))
        assert no_evidence.status_code == 422
        assert no_evidence.json()["errors"][0]["field"] == "evidence_document_id"
        unlinked = mover(org_id, STOCK_ADJUST)
        resp = await call(
            client,
            unlinked,
            "POST",
            A,
            body(world, "-25", evidence_document_id=str(uuid.uuid4())),
        )
        assert resp.status_code == 409 and "employee" in resp.text

    async def test_approved_by_the_service_the_stock_moves_exactly_once(
        self, client, session, org_id, world
    ) -> None:
        out, _clerk = await pending(client, org_id, world, "-25")
        request_id = uuid.uuid4()
        await consumers.approval_request_opened(
            envelope(
                "core.approval.request.opened",
                org_id,
                {
                    "request_id": str(request_id),
                    "request_type": "operations.inventory.adjustment",
                    "source_ref": out["id"],
                    "approver_name": "Meera Shah",
                },
            )
        )
        reader = mover(org_id, STOCK_READ)
        waiting = (await call(client, reader, "GET", f"{A}/{out['id']}")).json()
        assert waiting["approver_name"] == "Meera Shah"
        assert waiting["approval_request_id"] == str(request_id)

        approved = decision(org_id, out["id"], "approved", request_id)
        await consumers.approval_request_decided(approved)
        await consumers.approval_request_decided(approved)  # redelivery
        done = (await call(client, reader, "GET", f"{A}/{out['id']}")).json()
        assert done["status"] == "APPLIED" and done["decided_by_name"] == "Meera Shah"
        assert done["decision_comment"] == "reviewed" and done["applied_move_id"]
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("75")
        await assert_reconciled(session, org_id)

        # The return leg is published once, with the approval request it answers.
        applied = await events(session, "operations.inventory.adjustment.applied")
        assert len(applied) == 1
        validate_event_schema(
            applied[0].event_type, 1, dict(applied[0].payload), contracts_root=CONTRACTS_ROOT
        )
        assert (applied[0].payload["outcome"], applied[0].payload["decision"]) == (
            "applied",
            "approved",
        )
        assert applied[0].payload["approval_request_id"] == str(request_id)
        # A late contradictory decision cannot undo or repeat it.
        await consumers.approval_request_decided(decision(org_id, out["id"], "rejected"))
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("75")
        assert (await call(client, reader, "GET", f"{A}/{out['id']}")).json()["status"] == "APPLIED"

    async def test_a_rejection_closes_it_and_moves_nothing(
        self, client, session, org_id, world
    ) -> None:
        out, _ = await pending(client, org_id, world, "-25")
        await consumers.approval_request_decided(decision(org_id, out["id"], "rejected"))
        done = (await call(client, mover(org_id, STOCK_READ), "GET", f"{A}/{out['id']}")).json()
        assert done["status"] == "REJECTED" and done["applied_move_id"] is None
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("100")
        (event,) = await events(session, "operations.inventory.adjustment.applied")
        assert (event.payload["outcome"], event.payload["decision"]) == ("applied", "rejected")
        assert event.payload["status"] == "REJECTED"

    async def test_approved_but_the_stock_has_gone_is_recorded_as_failed(
        self, client, session, org_id, world
    ) -> None:
        out, _ = await pending(client, org_id, world, "-25")
        # Someone else takes almost everything while the request waits.
        async with session.begin():
            await ledger.post_move(
                session,
                world.t.ctx,
                kind="issue",
                lines=[MoveLine(world.t.dye, world.t.weaving, -q("95"))],
                occurred_on=TODAY,
            )
        await consumers.approval_request_decided(decision(org_id, out["id"], "approved"))
        done = (await call(client, mover(org_id, STOCK_READ), "GET", f"{A}/{out['id']}")).json()
        assert done["status"] == "FAILED"
        assert "available" in done["failure_reason"] or "on hand" in done["failure_reason"]
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("5")
        (event,) = await events(session, "operations.inventory.adjustment.applied")
        assert event.payload["outcome"] == "failed" and event.payload["status"] == "FAILED"
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )
        await assert_reconciled(session, org_id)

    async def test_cancelling_withdraws_it_and_a_late_decision_is_ignored(
        self, client, session, org_id, world
    ) -> None:
        out, clerk = await pending(client, org_id, world, "-25")
        resp = await call(client, clerk, "POST", f"{A}/{out['id']}:cancel", {"reason": "mistake"})
        assert resp.status_code == 200 and resp.json()["status"] == "CANCELLED"
        (event,) = await events(session, "operations.inventory.adjustment.cancelled")
        validate_event_schema(
            event.event_type, 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )
        assert event.payload["employee_id"] == str(clerk.subject_ref)
        await consumers.approval_request_decided(decision(org_id, out["id"], "approved"))
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("100")
        again = await call(client, clerk, "POST", f"{A}/{out['id']}:cancel", {})
        assert again.status_code == 409

    async def test_an_applied_adjustment_cannot_be_cancelled(self, client, org_id, world) -> None:
        clerk = mover(org_id, STOCK_ADJUST)
        done = await call(client, clerk, "POST", A, body(world, "-5"))
        resp = await call(client, clerk, "POST", f"{A}/{done.json()['id']}:cancel", {})
        assert resp.status_code == 409

    async def test_a_decision_naming_another_tenant_or_type_touches_nothing(
        self, client, session, org_id, world
    ) -> None:
        out, _ = await pending(client, org_id, world, "-25")
        forged = decision(org_id, out["id"], "approved")
        forged["organization_id"] = str(uuid.uuid4())
        await consumers.approval_request_decided(forged)
        await consumers.approval_request_decided(
            decision(org_id, out["id"], "approved", request_type="hrms.time.leave")
        )
        got = (await call(client, mover(org_id, STOCK_READ), "GET", f"{A}/{out['id']}")).json()
        assert got["status"] == "PENDING_APPROVAL"
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("100")


class TestListAndGate:
    async def test_list_filters_by_status_and_item(self, client, org_id, world) -> None:
        clerk = mover(org_id, STOCK_ADJUST)
        await call(client, clerk, "POST", A, body(world, "-1"))
        await call(client, clerk, "POST", A, body(world, "-2"))
        reader = mover(org_id, STOCK_READ)
        page = await call(client, reader, "GET", f"{A}?limit=1")
        assert len(page.json()["items"]) == 1 and page.json()["next_cursor"]
        applied = await call(client, reader, "GET", f"{A}?status=APPLIED&item_id={world.t.dye}")
        assert len(applied.json()["items"]) == 2
        assert (await call(client, reader, "GET", f"{A}?status=PENDING_APPROVAL")).json()[
            "items"
        ] == []

    async def test_the_older_item_level_route_cannot_skip_the_threshold(
        self, client, session, org_id, world
    ) -> None:
        item_route = f"/operations/inventory/{world.t.dye}/adjustments"
        clerk = mover(org_id, STOCK_ADJUST)
        # No threshold: unchanged behaviour.
        ok = await call(client, clerk, "POST", item_route, {"quantity_delta": -1, "reason": "lost"})
        assert ok.status_code == 201
        await configure(client, org_id, adjustment="20")
        small = await call(
            client, clerk, "POST", item_route, {"quantity_delta": -19, "reason": "lost"}
        )
        assert small.status_code == 201
        big = await call(
            client, clerk, "POST", item_route, {"quantity_delta": -20, "reason": "lost"}
        )
        assert big.status_code == 409
        assert "needs approval" in big.text
        count = await call(client, clerk, "POST", item_route, {"counted_on_hand": 10})
        assert count.status_code == 409
        assert (await bucket_qty(session, world, item=world.t.dye))[0] == q("80")
