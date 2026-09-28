"""`app.consumers.order_cancelled` (MAJOR 5, security review: zero test coverage before this).
The fulfilment half of ADR-0025 §1's cancellation fan-out — `app.orders.services.cancel_order`
never calls into `app.fulfilment` directly (ADR-0024 §1), so this consumer, bound to
`operations.orders.order.cancelled`, is the only path from "order cancelled" to "its open tasks
are cancelled too".

Covers, at minimum, what MAJOR 5 names: a wrong-tenant envelope does nothing, redelivery is
idempotent, and an open task actually moves to `CANCELLED` with its live offer `WITHDRAWN`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app import consumers
from app.config import settings
from app.models import AssignmentOffer, PickTask, ProcessedEvent
from platform_core.db import dispose_engine, init_engine
from tests.fulfilment_fixtures import TASK_ASSIGN_PERM, Floor, accept_order, build_floor
from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id, stock=100)


@pytest.fixture(autouse=True)
async def _engine():  # type: ignore[no-untyped-def]
    """`order_cancelled` opens its own `session_scope`, independent of the fixture `session` this
    file otherwise uses — same reason and shape as `test_fulfilment_tasks_write.py::_engine`: the
    process-wide engine has to exist for that to work, and the app lifespan is what does it in
    production."""
    init_engine(settings())
    yield
    await dispose_engine()


async def _create_tasks(client, org_id, order) -> list[dict]:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            "/api/v1/operations/fulfilment/tasks",
            json={"order_id": str(order.id), "order_version": order.version},
        )
    assert response.status_code == 201, response.text
    return response.json()


async def _offer(client, org_id, task_id: str, worker_id) -> dict:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/fulfilment/tasks/{task_id}/offers",
            json={"worker_id": str(worker_id)},
        )
    assert response.status_code == 201, response.text
    return response.json()


def _cancelled_envelope(*, organization_id: uuid.UUID, order_id: uuid.UUID) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "operations.orders.order.cancelled",
        "organization_id": str(organization_id),
        "payload": {"organization_id": str(organization_id), "order_id": str(order_id)},
    }


class TestOrderCancelledHappyPath:
    async def test_an_open_task_moves_to_cancelled_and_its_live_offer_to_withdrawn(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "10"}],
        )
        tasks = await _create_tasks(client, org_id, order)
        task_id = uuid.UUID(tasks[0]["id"])
        offer = await _offer(client, org_id, task_id, floor.worker_id)
        offer_id = uuid.UUID(offer["id"])

        await consumers.order_cancelled(
            _cancelled_envelope(organization_id=org_id, order_id=order.id)
        )

        async with session.begin():
            task_row = (
                await session.execute(select(PickTask).where(PickTask.id == task_id))
            ).scalar_one()
            offer_row = (
                await session.execute(select(AssignmentOffer).where(AssignmentOffer.id == offer_id))
            ).scalar_one()
        assert task_row.status == "CANCELLED"
        assert offer_row.state == "WITHDRAWN"

    async def test_a_task_with_no_live_offer_still_cancels(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "10"}],
        )
        tasks = await _create_tasks(client, org_id, order)
        task_id = uuid.UUID(tasks[0]["id"])  # CREATED, never offered

        await consumers.order_cancelled(
            _cancelled_envelope(organization_id=org_id, order_id=order.id)
        )

        async with session.begin():
            task_row = (
                await session.execute(select(PickTask).where(PickTask.id == task_id))
            ).scalar_one()
        assert task_row.status == "CANCELLED"


class TestOrderCancelledIdempotency:
    async def test_a_redelivered_event_does_not_double_process(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "10"}],
        )
        tasks = await _create_tasks(client, org_id, order)
        task_id = uuid.UUID(tasks[0]["id"])
        offer = await _offer(client, org_id, task_id, floor.worker_id)
        offer_id = uuid.UUID(offer["id"])

        envelope = _cancelled_envelope(organization_id=org_id, order_id=order.id)
        await consumers.order_cancelled(envelope)
        # The exact same delivery again — must not raise (a naive re-cancel of an already-
        # CANCELLED task would 409 inside `cancel_task`) and must not touch the row twice.
        await consumers.order_cancelled(envelope)

        async with session.begin():
            task_row = (
                await session.execute(select(PickTask).where(PickTask.id == task_id))
            ).scalar_one()
            offer_row = (
                await session.execute(select(AssignmentOffer).where(AssignmentOffer.id == offer_id))
            ).scalar_one()
            processed = (
                (
                    await session.execute(
                        select(ProcessedEvent).where(
                            ProcessedEvent.event_id == uuid.UUID(envelope["event_id"])
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert task_row.status == "CANCELLED"
        assert offer_row.state == "WITHDRAWN"
        assert len(processed) == 1  # recorded once, not once per delivery


class TestOrderCancelledWrongTenant:
    async def test_an_envelope_for_a_different_organization_touches_nothing(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """The envelope's own `organization_id` binds the tenant GUC (`session_scope`) — an
        envelope claiming a different organization than the one that actually owns the named
        order must not be able to reach that order's tasks at all: RLS plus the explicit org
        filter in `pick_tasks_stmt` both refuse it, and the call is a clean no-op, never a
        cross-tenant write."""
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "10"}],
        )
        tasks = await _create_tasks(client, org_id, order)
        task_id = uuid.UUID(tasks[0]["id"])
        offer = await _offer(client, org_id, task_id, floor.worker_id)
        offer_id = uuid.UUID(offer["id"])

        wrong_org = uuid.uuid4()
        await consumers.order_cancelled(
            _cancelled_envelope(organization_id=wrong_org, order_id=order.id)
        )

        async with session.begin():
            task_row = (
                await session.execute(select(PickTask).where(PickTask.id == task_id))
            ).scalar_one()
            offer_row = (
                await session.execute(select(AssignmentOffer).where(AssignmentOffer.id == offer_id))
            ).scalar_one()
        assert task_row.status == "OFFERED"  # untouched
        assert offer_row.state == "OFFERED"  # untouched
