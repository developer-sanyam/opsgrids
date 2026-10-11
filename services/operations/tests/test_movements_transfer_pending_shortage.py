"""A transfer shortage at or over the adjustment threshold is reserved on its line until the
adjustment is decided: it cannot be received or reported short a second time, and the decision
settles the line (written off) or releases it (rejected / cancelled)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app import consumers
from app.config import settings
from app.models import AdjustmentRequest, InventoryBalance, StockTransfer, StockTransferLine
from platform_core.db import dispose_engine, init_engine
from tests import test_movements_transfers as base
from tests.inventory_fixtures import q
from tests.movements_fixtures import (
    STOCK_ADJUST,
    STOCK_READ,
    TRANSFER_RECEIVE,
    World,
    build_world,
    call,
    in_transit_total,
    mover,
    receive_stock,
)
from tests.test_movements_transfers import T, dispatched, dye_line
from tests.tracking_fixtures import assert_reconciled

pytestmark = pytest.mark.integration


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


def _decided(org_id, adjustment_id, outcome: str) -> dict:  # type: ignore[type-arg]
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "core.approval.request.decided",
        "organization_id": str(org_id),
        "payload": {
            "organization_id": str(org_id),
            "request_id": str(uuid.uuid4()),
            "request_type": "operations.inventory.adjustment",
            "source_ref": str(adjustment_id),
            "outcome": outcome,
            "decided_by_name": "Meera Shah",
            "comment": "reviewed",
        },
    }


async def _receive(client, org_id, tid, line_id, *, received="0", short="0", key=None):  # type: ignore[no-untyped-def]
    keeper = mover(org_id, TRANSFER_RECEIVE, employee=True)
    return await call(
        client,
        keeper,
        "POST",
        f"{T}/{tid}:receive",
        {
            "lines": [
                {"line_id": line_id, "received_quantity": received, "shortage_quantity": short}
            ],
            "evidence_document_id": str(uuid.uuid4()),
        },
        key=key,
    )


async def _pending_setup(client, session, org_id, world):  # type: ignore[no-untyped-def]
    """10 kg dispatched, threshold 5: 5 received, 5 reported short -> a pending adjustment."""
    await base.TestApprovalThreshold()._configure(client, org_id, None, "5")
    body = await dispatched(client, org_id, world, dye_line(world, "10"))
    tid, line_id = body["id"], body["lines"][0]["id"]
    resp = await _receive(client, org_id, tid, line_id, received="5", short="5")
    assert resp.status_code == 200, resp.text
    async with session.begin():
        adj = (await session.execute(select(AdjustmentRequest))).scalar_one()
    return tid, line_id, adj.id


async def _line(session, line_id) -> StockTransferLine:  # type: ignore[no-untyped-def]
    async with session.begin():
        return (
            await session.execute(
                select(StockTransferLine)
                .where(StockTransferLine.id == uuid.UUID(line_id))
                .execution_options(populate_existing=True)
            )
        ).scalar_one()


class TestPendingShortageIsReserved:
    async def test_the_same_units_cannot_be_reported_short_again(
        self, client, session, org_id, world
    ) -> None:
        tid, line_id, _adj = await _pending_setup(client, session, org_id, world)
        again = await _receive(client, org_id, tid, line_id, short="5", key="second")
        assert again.status_code == 409, again.text
        async with session.begin():
            count = len((await session.execute(select(AdjustmentRequest))).scalars().all())
        assert count == 1

    async def test_the_same_units_cannot_be_received_while_the_write_off_is_pending(
        self, client, session, org_id, world
    ) -> None:
        tid, line_id, _adj = await _pending_setup(client, session, org_id, world)
        again = await _receive(client, org_id, tid, line_id, received="5")
        assert again.status_code == 409, again.text

    async def test_approval_writes_the_shortage_off_and_completes_the_transfer(
        self, client, session, org_id, world
    ) -> None:
        tid, line_id, adj_id = await _pending_setup(client, session, org_id, world)
        await consumers.approval_request_decided(_decided(org_id, adj_id, "approved"))
        line = await _line(session, line_id)
        assert (line.received_milli, line.shortage_milli) == (q("5"), q("5"))
        assert line.pending_shortage_milli == 0
        got = (await call(client, mover(org_id, STOCK_READ), "GET", f"{T}/{tid}")).json()
        assert got["status"] == "RECEIVED"
        assert await in_transit_total(session, org_id, world.t.dye) == 0
        await assert_reconciled(session, org_id)

    async def test_rejection_releases_the_units_to_be_received_or_reported_again(
        self, client, session, org_id, world
    ) -> None:
        tid, line_id, adj_id = await _pending_setup(client, session, org_id, world)
        await consumers.approval_request_decided(_decided(org_id, adj_id, "rejected"))
        line = await _line(session, line_id)
        assert (line.received_milli, line.shortage_milli, line.pending_shortage_milli) == (
            q("5"),
            0,
            0,
        )
        got = (await call(client, mover(org_id, STOCK_READ), "GET", f"{T}/{tid}")).json()
        assert got["status"] == "IN_TRANSIT"
        again = await _receive(client, org_id, tid, line_id, received="5")
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "RECEIVED"

    async def test_cancelling_the_adjustment_releases_the_hold(
        self, client, session, org_id, world
    ) -> None:
        tid, line_id, adj_id = await _pending_setup(client, session, org_id, world)
        resp = await call(
            client,
            mover(org_id, STOCK_ADJUST),
            "POST",
            f"/operations/inventory/adjustments/{adj_id}:cancel",
            {"reason": "recounted"},
        )
        assert resp.status_code == 200, resp.text
        line = await _line(session, line_id)
        assert line.pending_shortage_milli == 0 and line.shortage_milli == 0
        again = await _receive(client, org_id, tid, line_id, received="5")
        assert again.status_code == 200, again.text


class TestDecisionLocksInReceiptOrder:
    async def test_the_consumer_takes_the_transfer_before_the_in_transit_balance(
        self, client, session, admin_session, org_id, world
    ) -> None:
        """`receive_transfer` locks transfer, lines, then balances. The decision consumer must
        take the same order, or a decision and a receipt on one transfer deadlock. With the transfer
        held elsewhere, the consumer must be waiting for it without yet holding the balance."""
        tid, _line_id, adj_id = await _pending_setup(client, session, org_id, world)
        probe_engine = create_async_engine(settings().admin_dsn)
        try:
            async with admin_session.begin():
                await admin_session.execute(
                    select(StockTransfer.id)
                    .where(StockTransfer.id == uuid.UUID(tid))
                    .with_for_update()
                )
                task = asyncio.create_task(
                    consumers.approval_request_decided(_decided(org_id, adj_id, "approved"))
                )
                await asyncio.sleep(1)
                assert not task.done()
                async with AsyncSession(probe_engine) as probe, probe.begin():
                    try:
                        await probe.execute(
                            select(InventoryBalance.id)
                            .where(
                                InventoryBalance.organization_id == org_id,
                                InventoryBalance.status == "IN_TRANSIT",
                            )
                            .with_for_update(nowait=True)
                        )
                    except DBAPIError as exc:  # pragma: no cover - the failure being tested
                        raise AssertionError("the consumer holds the balance lock") from exc
            await task
        finally:
            await probe_engine.dispose()


class TestTransferMovesUseTheIndianDate:
    async def test_dispatch_receipt_and_shortage_moves_are_dated_by_the_ist_clock(
        self, client, session, org_id, world, monkeypatch
    ) -> None:
        from datetime import date, timedelta

        from app.jobwork import domain
        from app.models import InventoryMove

        ist = date.today() + timedelta(days=1)
        monkeypatch.setattr(domain, "ist_today", lambda now=None: ist)
        body = await dispatched(client, org_id, world, dye_line(world, "10"))
        resp = await _receive(
            client, org_id, body["id"], body["lines"][0]["id"], received="6", short="4"
        )
        assert resp.status_code == 200, resp.text
        async with session.begin():
            moves = (
                (
                    await session.execute(
                        select(InventoryMove).where(
                            InventoryMove.reference_id == uuid.UUID(body["id"])
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert {m.reference_type for m in moves} == {
            "stock_transfer",
            "stock_transfer_receipt",
            "stock_transfer_shortage",
        }
        assert {m.occurred_on for m in moves} == {ist}
