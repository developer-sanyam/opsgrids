"""The job-work -> quality hand-off (inventory plan Phases 4 and 6): a receipt on a `qc_required`
order parks the good goods in `QUALITY_HOLD` and emits `operations.jobwork.receipt.qc_requested`;
`app.consumers.job_work_qc_requested` opens an incoming inspection over that held stock.

Driven end to end: the job-work flow over HTTP, the staged outbox payload handed to the consumer as
the broker would deliver it, the inspection read back through the quality API.
"""

from __future__ import annotations

import copy
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app import consumers
from app.config import settings
from app.inventory import ledger
from app.inventory.ledger import MoveLine
from app.jobwork import services as jw_services
from app.models import Inspection, OutboxEvent, ProcessedEvent
from app.quality import handoff
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from tests.helpers import ctx_for
from tests.jobwork_fixtures import (
    Mill,
    balance_milli,
    build_mill,
    issued_order,
    receipt_line,
    receive,
)
from tests.quality_fixtures import call as quality_call

pytestmark = pytest.mark.integration


@pytest.fixture
async def mill(session, org_id) -> Mill:
    return await build_mill(session, org_id)


@pytest.fixture(autouse=True)
async def _engine():  # type: ignore[no-untyped-def]
    """The consumer opens its own `session_scope`, independent of the fixture `session`."""
    init_engine(settings())
    yield
    await dispose_engine()


def test_the_event_name_is_the_job_work_contract() -> None:
    assert handoff.EVT_JOB_WORK_QC_REQUESTED == jw_services.EVT_QC_REQUESTED
    assert handoff.EVT_JOB_WORK_QC_REQUESTED in consumers.consumer._handlers


async def staged_envelope(session, org_id: uuid.UUID) -> dict:
    """The one `qc_requested` outbox row, wrapped the way the broker delivers it."""
    async with session.begin():
        row = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == jw_services.EVT_QC_REQUESTED,
                    OutboxEvent.organization_id == org_id,
                )
            )
        ).scalar_one()
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": row.event_type,
        "organization_id": str(org_id),
        "payload": copy.deepcopy(row.payload),
    }


async def inspections(session, org_id: uuid.UUID) -> list[Inspection]:
    async with session.begin():
        return list(
            (await session.execute(select(Inspection).where(Inspection.organization_id == org_id)))
            .scalars()
            .all()
        )


async def returned(client, mill: Mill, **line) -> dict:  # type: ignore[no-untyped-def]
    order = await issued_order(client, mill, qc_required=True)
    response = await receive(
        client,
        mill,
        order["id"],
        [receipt_line(order["materials"][0]["id"], **line)],
    )
    assert response.status_code == 200, response.text
    return order


class TestTheHandoffOpensAnInspection:
    async def test_good_goods_in_quality_hold_get_an_incoming_inspection(
        self, client, session, mill: Mill, org_id
    ) -> None:
        order = await returned(client, mill, received="70", rejected="2.5")
        envelope = await staged_envelope(session, org_id)

        await consumers.job_work_qc_requested(envelope)

        [inspection] = await inspections(session, org_id)
        assert inspection.kind == "incoming"
        assert inspection.status == "PENDING"
        assert inspection.item_id == mill.fabric
        assert inspection.location_id == mill.recv
        assert inspection.quantity_milli == 70_000
        assert inspection.supplier_id == mill.supplier_id
        assert inspection.reference_type == "job_work_order"
        assert str(inspection.reference_id) == order["id"]
        # The goods were already on hold: no second hold move, the stock has not moved.
        assert inspection.hold_move_id is None
        assert await balance_milli(session, mill.fabric, mill.recv, "QUALITY_HOLD") == 70_000
        # And it is a normal inspection: visible through the quality API.
        listing = await quality_call(client, org_id, "GET", "/inspections")
        assert listing.status_code == 200, listing.text
        assert [i["id"] for i in listing.json()["items"]] == [str(inspection.id)]

    async def test_the_inspection_can_then_release_the_stock_through_quality(
        self, client, session, mill: Mill, org_id
    ) -> None:
        await returned(client, mill, received="70")
        await consumers.job_work_qc_requested(await staged_envelope(session, org_id))
        [inspection] = await inspections(session, org_id)
        response = await quality_call(
            client,
            org_id,
            "POST",
            f"/inspections/{inspection.id}/release",
            json={"note": "ok"},
        )
        # Release needs recorded readings only when a template demands them; with none it passes.
        assert response.status_code == 200, response.text
        assert await balance_milli(session, mill.fabric, mill.recv, "QUALITY_HOLD") == 0
        assert await balance_milli(session, mill.fabric, mill.recv, "AVAILABLE") == 70_000

    async def test_a_pure_rejection_or_shortage_opens_nothing(
        self, client, session, mill: Mill, org_id
    ) -> None:
        order = await issued_order(client, mill, qc_required=True)
        material = order["materials"][0]["id"]
        await receive(client, mill, order["id"], [receipt_line(material, rejected="3")])
        # Only a good quantity brings a hand-off line; a rejection alone emits no event at all.
        async with session.begin():
            staged = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == jw_services.EVT_QC_REQUESTED
                    )
                )
            ).all()
        if staged:
            await consumers.job_work_qc_requested(await staged_envelope(session, org_id))
        assert await inspections(session, org_id) == []


class TestIdempotency:
    async def test_a_redelivered_event_does_not_open_a_second_inspection(
        self, client, session, mill: Mill, org_id
    ) -> None:
        await returned(client, mill, received="70")
        envelope = await staged_envelope(session, org_id)
        await consumers.job_work_qc_requested(envelope)
        await consumers.job_work_qc_requested(envelope)
        assert len(await inspections(session, org_id)) == 1
        async with session.begin():
            processed = (
                await session.execute(
                    select(ProcessedEvent).where(
                        ProcessedEvent.event_id == uuid.UUID(envelope["event_id"])
                    )
                )
            ).all()
        assert len(processed) == 1

    async def test_the_same_receipt_under_a_new_event_id_replays_the_inspection(
        self, client, session, mill: Mill, org_id
    ) -> None:
        """Even if the processed-event check were bypassed (a republished event), the inspection's
        own deterministic key makes the second delivery find the first inspection."""
        await returned(client, mill, received="70")
        envelope = await staged_envelope(session, org_id)
        await consumers.job_work_qc_requested(envelope)
        again = {**envelope, "event_id": str(uuid.uuid4())}
        await consumers.job_work_qc_requested(again)
        assert len(await inspections(session, org_id)) == 1


class TestRefusalsDoNotPoisonTheQueue:
    async def test_stock_that_left_quality_hold_is_skipped_not_raised(
        self, client, session, mill: Mill, org_id
    ) -> None:
        await returned(client, mill, received="70")
        envelope = await staged_envelope(session, org_id)
        # Someone moves the goods out of quality hold before the consumer runs.
        ctx = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
        async with session.begin():
            await ledger.post_move(
                session,
                ctx,
                kind="relocation",
                lines=[
                    MoveLine(mill.fabric, mill.recv, -70_000, status="QUALITY_HOLD"),
                    MoveLine(mill.fabric, mill.recv, 70_000, status="AVAILABLE"),
                ],
                occurred_on=date.today(),
            )
        await consumers.job_work_qc_requested(envelope)  # must not raise
        assert await inspections(session, org_id) == []
        async with session.begin():
            processed = (
                await session.execute(
                    select(ProcessedEvent).where(
                        ProcessedEvent.event_id == uuid.UUID(envelope["event_id"])
                    )
                )
            ).all()
        assert len(processed) == 1  # handled once, never retried into a loop

    async def test_a_payload_naming_another_organization_is_ignored(
        self, client, session, mill: Mill, org_id
    ) -> None:
        await returned(client, mill, received="70")
        envelope = await staged_envelope(session, org_id)
        envelope["payload"]["organization_id"] = str(uuid.uuid4())
        await consumers.job_work_qc_requested(envelope)
        assert await inspections(session, org_id) == []
