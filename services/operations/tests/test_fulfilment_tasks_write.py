"""Pick tasks and offers over HTTP (ADR-0025 §2/§3; plan F3). Mirrors `test_orders_write.py`'s
shape: real status codes, a seed step's own success checked before its id is trusted, negative-
authz and cross-tenant-404 on every endpoint.

`TestOfferExpiryRace` is the plan's own F3 exit criterion #1: an offer accepted after its own
scheduled expiry has already fired changes nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app import consumers
from app.config import settings
from app.main import app
from app.models import SCHEMA, AssignmentOffer, PickTask
from app.orders import services as order_services
from platform_core.auth import Scope, get_principal
from platform_core.db import dispose_engine, get_session, init_engine
from tests.fulfilment_fixtures import (
    TASK_ASSIGN_PERM,
    TASK_EXECUTE_PERM,
    TASK_OVERRIDE_PERM,
    TASK_READ_PERM,
    Floor,
    accept_order,
    build_floor,
)
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id)


async def _accepted_order(session, org_id, floor: Floor, *, qty: str = "2") -> Any:
    return await accept_order(
        session,
        org_id,
        floor.shop,
        lines=[{"inventory_item_id": str(floor.shop.widget), "quantity": qty}],
    )


async def _create_tasks(client, org_id, order) -> list[dict]:
    principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            "/api/v1/operations/fulfilment/tasks",
            json={"order_id": str(order.id), "order_version": order.version},
        )
    assert response.status_code == 201, response.text
    return response.json()


async def _offer(client, org_id, task_id: str, worker_id: uuid.UUID) -> dict:
    principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/fulfilment/tasks/{task_id}/offers",
            json={"worker_id": str(worker_id)},
        )
    assert response.status_code == 201, response.text
    return response.json()


async def _accept(client, org_id, offer_id: str, worker_employee_id: uuid.UUID):  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=worker_employee_id)
    with as_principal(principal):
        return await client.post(f"/api/v1/operations/fulfilment/offers/{offer_id}/accept")


# ------------------------------------------------------------------ task creation


class TestCreateTasks:
    async def test_happy_path_creates_one_task_and_moves_the_order(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        assert len(tasks) == 1
        task = tasks[0]
        assert task["status"] == "CREATED"
        assert len(task["lines"]) == 1
        assert task["lines"][0]["quantity"] == "2"
        assert task["lines"][0]["status"] == "pending"

        principal = principal_for(
            org_id, ["operations.orders.order.read:ORG"], subject_ref=uuid.uuid4()
        )
        with as_principal(principal):
            order_response = await client.get(f"/api/v1/operations/orders/{order.id}")
        assert order_response.status_code == 200
        assert order_response.json()["status"] == "IN_FULFILMENT"

    async def test_creating_tasks_twice_is_conflict(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        await _create_tasks(client, org_id, order)
        principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/tasks",
                json={"order_id": str(order.id), "order_version": order.version + 1},
            )
        assert response.status_code == 409

    async def test_a_draft_order_has_no_tasks_to_create(
        self, client, org_id, session, floor: Floor
    ) -> None:
        ctx = ctx_for(org_id, "operations.orders.order.create", Scope.ORG)
        async with session.begin():
            order = await order_services.create_order(
                session,
                ctx,
                channel="manual",
                warehouse_id=floor.shop.warehouse_id,
                customer_id=None,
                customer_name="Asha",
                customer_phone="1",
                currency="INR",
                lines=[{"inventory_item_id": floor.shop.widget, "quantity": "1"}],
            )
        principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/tasks",
                json={"order_id": str(order.id), "order_version": 0},
            )
        assert response.status_code == 409

    async def test_a_reader_cannot_create_tasks(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        principal = principal_for(org_id, [TASK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/tasks",
                json={"order_id": str(order.id), "order_version": order.version},
            )
        assert response.status_code == 403

    async def test_cross_tenant_order_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        other = principal_for(uuid.uuid4(), [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                "/api/v1/operations/fulfilment/tasks",
                json={"order_id": str(order.id), "order_version": order.version},
            )
        assert response.status_code == 404


# ------------------------------------------------------------------ offers


class TestOffer:
    async def test_happy_path_offers_the_task(self, client, org_id, session, floor: Floor) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        assert offer["state"] == "OFFERED"
        assert offer["round"] == 1

        principal = principal_for(org_id, [TASK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}")
        assert response.json()["status"] == "OFFERED"

    async def test_offering_a_second_time_is_conflict(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}/offers",
                json={"worker_id": str(floor.worker_id)},
            )
        assert response.status_code == 409

    async def test_offering_to_an_inactive_worker_is_conflict(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        principal = principal_for(
            org_id, ["operations.fulfilment.worker.write:ORG"], subject_ref=uuid.uuid4()
        )
        with as_principal(principal):
            patched = await client.patch(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}",
                json={"is_active": False},
            )
        assert patched.status_code == 200
        principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}/offers",
                json={"worker_id": str(floor.worker_id)},
            )
        assert response.status_code == 409

    async def test_an_assigner_without_the_permission_is_forbidden(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        principal = principal_for(org_id, [TASK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}/offers",
                json={"worker_id": str(floor.worker_id)},
            )
        assert response.status_code == 403

    async def test_cross_tenant_task_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        other = principal_for(uuid.uuid4(), [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}/offers",
                json={"worker_id": str(floor.worker_id)},
            )
        assert response.status_code == 404


class TestAcceptDeclineRelease:
    async def test_happy_path_accept_moves_the_task(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        response = await _accept(client, org_id, offer["id"], floor.worker_employee_id)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "ACCEPTED"

    async def test_someone_elses_worker_cannot_accept(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        response = await _accept(client, org_id, offer["id"], uuid.uuid4())
        assert response.status_code == 403

    async def test_a_caller_with_no_worker_record_cannot_accept(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=None)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/offers/{offer['id']}/accept"
            )
        assert response.status_code == 403

    async def test_declining_advances_the_round_and_returns_to_created(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=floor.worker_employee_id)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/offers/{offer['id']}/decline"
            )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "DECLINED"

        principal = principal_for(org_id, [TASK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            task_response = await client.get(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}"
            )
        body = task_response.json()
        assert body["status"] == "CREATED"
        assert body["round"] == 1

    async def test_declining_a_second_time_is_conflict(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=floor.worker_employee_id)
        with as_principal(principal):
            first = await client.post(f"/api/v1/operations/fulfilment/offers/{offer['id']}/decline")
            assert first.status_code == 200
            second = await client.post(
                f"/api/v1/operations/fulfilment/offers/{offer['id']}/decline"
            )
        assert second.status_code == 409

    async def test_three_declines_escalate_the_task(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        task_id = tasks[0]["id"]
        for _ in range(3):
            offer = await _offer(client, org_id, task_id, floor.worker_id)
            principal = principal_for(
                org_id, [TASK_EXECUTE_PERM], subject_ref=floor.worker_employee_id
            )
            with as_principal(principal):
                response = await client.post(
                    f"/api/v1/operations/fulfilment/offers/{offer['id']}/decline"
                )
            assert response.status_code == 200, response.text

        principal = principal_for(org_id, [TASK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            task_response = await client.get(f"/api/v1/operations/fulfilment/tasks/{task_id}")
        body = task_response.json()
        assert body["status"] == "ESCALATED"
        assert body["round"] == 3

        # A manager may still assign an escalated task by hand (ADR-0025 §2).
        offer = await _offer(client, org_id, task_id, floor.worker_id)
        assert offer["round"] == 4

    async def test_release_before_starting_is_a_failed_round(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        await _accept(client, org_id, offer["id"], floor.worker_employee_id)

        principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=floor.worker_employee_id)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}/release", json={}
            )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "CREATED"
        assert response.json()["round"] == 1

    async def test_cross_tenant_accept_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        other = principal_for(uuid.uuid4(), [TASK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/fulfilment/offers/{offer['id']}/accept"
            )
        assert response.status_code == 404


# ------------------------------------------------------------------ cancel / close


class TestCancelAndClose:
    async def test_cancel_a_created_task_needs_only_assign(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}/cancel",
                json={"reason": "order changed"},
            )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "CANCELLED"

    async def test_cancel_an_accepted_task_without_override_is_forbidden(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        await _accept(client, org_id, offer["id"], floor.worker_employee_id)

        principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}/cancel",
                json={"reason": "manager forcing it"},
            )
        assert response.status_code == 403

    async def test_an_own_scoped_override_grant_cannot_cancel_an_accepted_task(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """N4 (security review): `task.override` is an org-wide permission (ADR-0025 §2); a
        caller holding it at `OWN` — never issued by this catalogue's own templates, but not
        rejected by a hand-built custom role either — must not be treated as holding it at all."""
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        await _accept(client, org_id, offer["id"], floor.worker_employee_id)

        principal = principal_for(
            org_id,
            [TASK_ASSIGN_PERM, "operations.fulfilment.task.override:OWN"],
            subject_ref=uuid.uuid4(),
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}/cancel",
                json={"reason": "manager forcing it"},
            )
        assert response.status_code == 403, response.text

    async def test_cancel_an_accepted_task_with_override_withdraws_the_offer(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        await _accept(client, org_id, offer["id"], floor.worker_employee_id)

        principal = principal_for(
            org_id, [TASK_ASSIGN_PERM, TASK_OVERRIDE_PERM], subject_ref=uuid.uuid4()
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}/cancel",
                json={"reason": "manager forcing it"},
            )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "CANCELLED"

        async with session.begin():
            row = (
                await session.execute(
                    select(AssignmentOffer).where(AssignmentOffer.id == uuid.UUID(offer["id"]))
                )
            ).scalar_one()
        assert row.state == "WITHDRAWN"

    async def test_cross_tenant_cancel_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        other = principal_for(uuid.uuid4(), [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}/cancel",
                json={"reason": "not yours"},
            )
        assert response.status_code == 404


class TestCancelTasksForOrderSkipsAlreadyPicked:
    """N1: `cancel_tasks_for_order` reads every task once, then re-locks each open one before
    cancelling it — the same read-then-lock gap `TestSameOrderRace`-style fixes close elsewhere.
    A task that settles to `PICKED` in between must be skipped, not raised on: a raise here is
    uncaught (this only ever runs from `app.consumers.order_cancelled`) and would dead-letter the
    whole cancellation event for a task that, by the time the lock lands, needs no cancelling."""

    async def test_a_task_that_becomes_picked_between_read_and_lock_is_skipped(
        self, org_id, session, maker, floor: Floor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sqlalchemy import update as sa_update

        from app.fulfilment import selectors as fulfilment_selectors
        from app.fulfilment import services as fulfilment_services
        from app.orders import selectors as order_selectors
        from platform_core.db import set_tenant

        order = await _accepted_order(session, org_id, floor)
        assign_ctx = ctx_for(org_id, "operations.fulfilment.task.assign", Scope.ORG)
        async with session.begin():
            locked_order = await order_selectors.get_order(
                session, org_id, order.id, for_update=True
            )
            assert locked_order is not None
            created = await fulfilment_services.create_pick_tasks_for_order(
                session, assign_ctx, order_id=locked_order.id, order_version=locked_order.version
            )
        task_id = created[0].id

        async with session.begin():
            t = await fulfilment_selectors.get_pick_task(session, org_id, task_id, for_update=True)
            assert t is not None
            offer = await fulfilment_services.offer_task(
                session, assign_ctx, t, worker_id=floor.worker_id
            )
        offer_id = offer.id

        exec_ctx = ctx_for(
            org_id,
            "operations.fulfilment.task.execute",
            Scope.OWN,
            subject_ref=floor.worker_employee_id,
        )
        async with session.begin():
            await fulfilment_services.accept_offer(session, exec_ctx, offer_id)

        # The exact interleaving N1 describes: patch the module's own `get_pick_task` so that the
        # very first `for_update=True` call inside `cancel_tasks_for_order`'s loop — its re-lock
        # of the task the unlocked initial read already saw as open — first lets a genuinely
        # separate connection settle the task to `PICKED` and commit, then proceeds with the real
        # (now up to date) lock.
        real_get_pick_task = fulfilment_selectors.get_pick_task
        completed = False

        async def _settle_then_lock(sess, org, tid, *, for_update=False):  # type: ignore[no-untyped-def]
            nonlocal completed
            if for_update and tid == task_id and not completed:
                completed = True
                async with maker() as other, other.begin():
                    await set_tenant(other, org)
                    await other.execute(
                        sa_update(PickTask).where(PickTask.id == tid).values(status="PICKED")
                    )
            return await real_get_pick_task(sess, org, tid, for_update=for_update)

        monkeypatch.setattr(fulfilment_services.selectors, "get_pick_task", _settle_then_lock)

        service_ctx = fulfilment_services.service_ctx(org_id)
        async with session.begin():
            cancelled = await fulfilment_services.cancel_tasks_for_order(
                session, service_ctx, order_id=order.id
            )
        # Skipped, not raised — the task was PICKED by the time of the real lock.
        assert cancelled == []

        async with session.begin():
            row = (
                await session.execute(select(PickTask).where(PickTask.id == task_id))
            ).scalar_one()
        assert row.status == "PICKED"  # untouched by the cancellation fan-out


# ------------------------------------------------------------------ offer expiry race (F3 exit
# criterion #1)


def _expiry_envelope(
    *, organization_id: uuid.UUID, offer_id: uuid.UUID, task_id: uuid.UUID
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": consumers.fulfilment_services.EVT_OFFER_EXPIRE_REQUESTED,
        "organization_id": str(organization_id),
        "payload": {"offer_id": str(offer_id), "task_id": str(task_id)},
    }


@pytest.fixture(autouse=True)
async def _engine():
    """The offer-expiry consumer opens its own `session_scope`, independent of the fixture
    `session` this file otherwise uses — same reason and shape as `services/finance/tests/
    test_order_invoice_requested.py`'s own `_engine`: the process-wide engine has to exist for
    that to work, and the app lifespan is what does it in production."""
    init_engine(settings())
    yield
    await dispose_engine()


class TestOfferExpiryRace:
    async def test_an_accept_after_the_expiry_already_fired_is_409_not_500(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)

        # The expiry sweep fires first — the exact conditional update ADR-0025 §3 specifies.
        await consumers.expire_offer_requested(
            _expiry_envelope(
                organization_id=org_id,
                offer_id=uuid.UUID(offer["id"]),
                task_id=uuid.UUID(tasks[0]["id"]),
            )
        )

        # The accept, arriving after, changes nothing.
        response = await _accept(client, org_id, offer["id"], floor.worker_employee_id)
        assert response.status_code == 409, response.text

        async with session.begin():
            row = (
                await session.execute(
                    select(AssignmentOffer).where(AssignmentOffer.id == uuid.UUID(offer["id"]))
                )
            ).scalar_one()
            task_row = (
                await session.execute(
                    select(PickTask).where(PickTask.id == uuid.UUID(tasks[0]["id"]))
                )
            ).scalar_one()
        assert row.state == "EXPIRED"
        assert task_row.status == "CREATED"  # the failed round, not left dangling on OFFERED
        assert task_row.round == 1

    async def test_a_redelivered_expiry_event_is_a_no_op(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        envelope = _expiry_envelope(
            organization_id=org_id,
            offer_id=uuid.UUID(offer["id"]),
            task_id=uuid.UUID(tasks[0]["id"]),
        )
        await consumers.expire_offer_requested(envelope)
        await consumers.expire_offer_requested(envelope)  # the exact same delivery, again

        async with session.begin():
            task_row = (
                await session.execute(
                    select(PickTask).where(PickTask.id == uuid.UUID(tasks[0]["id"]))
                )
            ).scalar_one()
        assert task_row.round == 1  # not doubled by the redelivery

    async def test_an_expiry_that_lost_the_race_to_an_accept_is_a_silent_no_op(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
        accepted = await _accept(client, org_id, offer["id"], floor.worker_employee_id)
        assert accepted.status_code == 200, accepted.text

        await consumers.expire_offer_requested(
            _expiry_envelope(
                organization_id=org_id,
                offer_id=uuid.UUID(offer["id"]),
                task_id=uuid.UUID(tasks[0]["id"]),
            )
        )

        async with session.begin():
            row = (
                await session.execute(
                    select(AssignmentOffer).where(AssignmentOffer.id == uuid.UUID(offer["id"]))
                )
            ).scalar_one()
        assert row.state == "ACCEPTED"  # the accept won; the late expiry changed nothing


# ------------------------------------------------------------------ offer/task lock ordering
# (security review MAJOR 3)


@pytest.fixture
async def maker(org_id):  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=20,
        max_overflow=0,
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def concurrent_client(maker):  # type: ignore[no-untyped-def]
    async def _session_override():  # type: ignore[no-untyped-def]
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_session, None)


def _principal_router(principals: dict[str, Any]) -> Any:
    def _dep(request: Request) -> Any:
        key = request.headers.get("x-test-principal", "default")
        return principals[key]

    return _dep


class TestOfferAcceptCancelLockOrdering:
    """Security review MAJOR 3: accept/decline/expire used to lock the offer row before the task
    row; cancel/release/cascade always locked the task row first. A dispatcher cancelling an
    OFFERED task while the worker taps Accept raced those two opposite orders — Postgres resolves
    that by aborting one side with a raw `deadlock detected` error, a 500, not the clean 409
    ADR-0025 §3 promises. Both now lock the task row first, matching cancel's own order, so no
    interleaving of the two calls can deadlock. Run several times: a deadlock, when the ordering
    is wrong, is real but timing-dependent — most interleavings still just serialise cleanly."""

    async def test_cancel_racing_accept_on_the_same_offered_task_never_500s(
        self, client, org_id, session, maker, concurrent_client, floor: Floor
    ) -> None:
        for attempt in range(20):
            order = await _accepted_order(session, org_id, floor)
            tasks = await _create_tasks(client, org_id, order)
            task_id = tasks[0]["id"]
            offer = await _offer(client, org_id, task_id, floor.worker_id)

            worker_principal = principal_for(
                org_id, [TASK_EXECUTE_PERM], subject_ref=floor.worker_employee_id
            )
            manager_principal = principal_for(
                org_id, [TASK_ASSIGN_PERM, TASK_OVERRIDE_PERM], subject_ref=uuid.uuid4()
            )
            app.dependency_overrides[get_principal] = _principal_router(
                {"worker": worker_principal, "manager": manager_principal}
            )
            try:
                accept_result, cancel_result = await asyncio.gather(
                    concurrent_client.post(
                        f"/api/v1/operations/fulfilment/offers/{offer['id']}/accept",
                        headers={"x-test-principal": "worker"},
                    ),
                    concurrent_client.post(
                        f"/api/v1/operations/fulfilment/tasks/{task_id}/cancel",
                        json={"reason": "racing the accept"},
                        headers={"x-test-principal": "manager"},
                    ),
                    return_exceptions=True,
                )
            finally:
                app.dependency_overrides.pop(get_principal, None)

            # `return_exceptions=True` so a DB-level `deadlock detected` (unhandled, uncaught by
            # any route) fails this assertion with a clear message instead of an opaque
            # SQLAlchemy traceback several layers below the real point of the proof — this is
            # exactly what the old, opposite lock order could raise, worse than a plain 500.
            assert not isinstance(accept_result, BaseException), (attempt, repr(accept_result))
            assert not isinstance(cancel_result, BaseException), (attempt, repr(cancel_result))

            statuses = sorted([accept_result.status_code, cancel_result.status_code])
            assert 500 not in statuses, (
                attempt,
                accept_result.status_code,
                accept_result.text,
                cancel_result.status_code,
                cancel_result.text,
            )
            # Exactly one side can ever accept the (still-live) offer; cancelling an already-
            # accepted task needs (and here holds) override, so both winning is also a clean
            # outcome — the only combinations a correct, race-free serialisation ever produces.
            assert statuses in ([200, 200], [200, 409]), (
                attempt,
                accept_result.status_code,
                accept_result.text,
                cancel_result.status_code,
                cancel_result.text,
            )
