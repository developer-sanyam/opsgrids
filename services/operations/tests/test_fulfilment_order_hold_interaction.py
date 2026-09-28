"""Fulfilment writes against a held order (ADR-0025 §1's "a hold stops new offers on the order's
tasks"; security review BLOCKER 3 and MAJOR 1 #2).

Three refusals BLOCKER 3 requires, none of which existed before this fix:

* `create_pick_tasks_for_order` must require `order.status == "ACCEPTED"` — a held order cannot
  spawn new tasks (`TestCreateTasksRefusesOnHold`).
* `_maybe_ready_order`'s target must require `order.status == "IN_FULFILMENT"` — a task that
  finishes while its order is held must not carry the order straight to `READY`
  (`TestReadyPropagationStaysOnHold`).
* `offer_task` must refuse (409) a new offer while the order is `ON_HOLD`
  (`TestOfferRefusesOnHold`).

And MAJOR 1 #2: `release_hold_order`'s target is recomputed from the order's own pick tasks —
no tasks yet -> `ACCEPTED`; open tasks remain -> `IN_FULFILMENT`; every task settled -> `READY`
(`TestReleaseRecomputesFromRealTasks`), replacing the F2-era hardcoded "always `ACCEPTED`" the
`TODO(F3)` in `app/orders/services.py` left behind.
"""

from __future__ import annotations

import uuid

import pytest

from tests.fulfilment_fixtures import (
    TASK_ASSIGN_PERM,
    TASK_EXECUTE_PERM,
    Floor,
    accept_order,
    build_floor,
)
from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

ORDER_READ_PERM = "operations.orders.order.read:ORG"
ORDER_WRITE_PERM = "operations.orders.order.write:ORG"


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id, stock=100)


async def _accepted_order(session, org_id, floor: Floor, *, qty: str = "10"):  # type: ignore[no-untyped-def]
    return await accept_order(
        session,
        org_id,
        floor.shop,
        lines=[{"inventory_item_id": floor.shop.widget, "quantity": qty}],
    )


async def _create_tasks(client, org_id, order_id, order_version) -> list[dict]:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            "/api/v1/operations/fulfilment/tasks",
            json={"order_id": str(order_id), "order_version": order_version},
        )
    assert response.status_code == 201, response.text
    return response.json()


async def _offer(client, org_id, task_id: str, worker_id) -> dict:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/fulfilment/tasks/{task_id}/offers",
            json={"worker_id": str(worker_id)},
        )


async def _accept_offer(client, org_id, offer_id: str, worker_employee_id) -> None:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=worker_employee_id)
    with as_principal(principal):
        response = await client.post(f"/api/v1/operations/fulfilment/offers/{offer_id}/accept")
    assert response.status_code == 200, response.text


async def _confirm_pick(client, org_id, task_id, line_id, worker_employee_id):  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=worker_employee_id)
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/fulfilment/tasks/{task_id}/lines/{line_id}/pick", json={}
        )


async def _hold(client, org_id, order_id, version, *, reason: str = "customer asked to wait"):  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [ORDER_WRITE_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/orders/{order_id}/hold",
            json={"version": version, "reason": reason},
        )
    assert response.status_code == 200, response.text
    return response.json()


async def _release(client, org_id, order_id, version):  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [ORDER_WRITE_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/orders/{order_id}/release", json={"version": version}
        )


async def _read_order(client, org_id, order_id) -> dict:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [ORDER_READ_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.get(f"/api/v1/operations/orders/{order_id}")
    assert response.status_code == 200, response.text
    return response.json()


class TestCreateTasksRefusesOnHold:
    async def test_a_held_order_cannot_spawn_new_pick_tasks(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        held = await _hold(client, org_id, order.id, order.version)
        assert held["status"] == "ON_HOLD"

        principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/tasks",
                json={"order_id": str(order.id), "order_version": held["version"]},
            )
        assert response.status_code == 409, response.text


class TestOfferRefusesOnHold:
    async def test_offering_a_task_on_a_held_order_is_refused(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order.id, order.version)
        task_id = tasks[0]["id"]

        order_body = await _read_order(client, org_id, order.id)
        held = await _hold(client, org_id, order.id, order_body["version"])
        assert held["status"] == "ON_HOLD"

        response = await _offer(client, org_id, task_id, floor.worker_id)
        assert response.status_code == 409, response.text


class TestReadyPropagationStaysOnHold:
    async def test_a_task_finishing_while_held_does_not_carry_the_order_to_ready(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """ADR-0025 §1: "If those tasks finish while the order is held, the order stays
        `ON_HOLD`" — the hold does not recall a worker already mid-aisle, but the finish must not
        silently carry the order on to `READY` either."""
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order.id, order.version)
        task = tasks[0]
        offer = await _offer(client, org_id, task["id"], floor.worker_id)
        assert offer.status_code == 201, offer.text
        await _accept_offer(client, org_id, offer.json()["id"], floor.worker_employee_id)

        order_body = await _read_order(client, org_id, order.id)
        held = await _hold(client, org_id, order.id, order_body["version"])
        assert held["status"] == "ON_HOLD"

        line_id = task["lines"][0]["id"]
        picked = await _confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)
        assert picked.status_code == 200, picked.text
        assert picked.json()["status"] == "PICKED"  # the task itself completes...

        order_after = await _read_order(client, org_id, order.id)
        assert order_after["status"] == "ON_HOLD"  # ...but the order does not silently follow


class TestReleaseRecomputesFromRealTasks:
    async def test_release_with_no_tasks_goes_to_accepted(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        held = await _hold(client, org_id, order.id, order.version)
        released = await _release(client, org_id, order.id, held["version"])
        assert released.status_code == 200, released.text
        assert released.json()["status"] == "ACCEPTED"

    async def test_release_with_an_open_task_goes_to_in_fulfilment(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        await _create_tasks(client, org_id, order.id, order.version)  # task stays CREATED, unopened

        order_body = await _read_order(client, org_id, order.id)
        held = await _hold(client, org_id, order.id, order_body["version"])
        assert held["status"] == "ON_HOLD"

        released = await _release(client, org_id, order.id, held["version"])
        assert released.status_code == 200, released.text
        assert released.json()["status"] == "IN_FULFILMENT"

    async def test_release_with_every_task_picked_goes_to_ready(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order.id, order.version)
        task = tasks[0]
        offer = await _offer(client, org_id, task["id"], floor.worker_id)
        assert offer.status_code == 201, offer.text
        await _accept_offer(client, org_id, offer.json()["id"], floor.worker_employee_id)

        order_body = await _read_order(client, org_id, order.id)
        held = await _hold(client, org_id, order.id, order_body["version"])
        assert held["status"] == "ON_HOLD"

        line_id = task["lines"][0]["id"]
        picked = await _confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)
        assert picked.status_code == 200, picked.text
        assert picked.json()["status"] == "PICKED"

        order_after_pick = await _read_order(client, org_id, order.id)
        assert order_after_pick["status"] == "ON_HOLD"  # still held, per the class above

        released = await _release(client, org_id, order.id, order_after_pick["version"])
        assert released.status_code == 200, released.text
        assert released.json()["status"] == "READY"
