"""Task-scoped display fields for the worker app (ADR-0024 §7 addendum).

The `OPERATIONS_WORKER` role holds neither `orders.order.read`, `inventory.location.read` nor
`inventory.item.read`, so a picker used to see UUID fragments. The task endpoints now carry the
order number, each line's bin code/name and the item's name/unit, resolved inside operations — but
only to a caller who holds the matching org-wide read or has a live offer on that very task. These
tests pin the negative side first: another worker, an unassigned task, a caller with no worker row,
another tenant, and the `mine` filter's own scoping.
"""

from __future__ import annotations

import uuid

import pytest

from app import catalogue
from app.fulfilment import services as fulfilment_services
from platform_core.auth import Scope
from tests.fulfilment_fixtures import (
    PACK_EXECUTE_PERM,
    PACK_READ_PERM,
    TASK_ASSIGN_PERM,
    TASK_EXECUTE_PERM,
    TASK_READ_PERM,
    Floor,
    accept_order,
    build_floor,
    confirm_pick,
    create_tasks,
)
from tests.fulfilment_fixtures import accept_offer as accept_offer_http
from tests.fulfilment_fixtures import offer_task as offer_task_http
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration

ORDER_READ = "operations.orders.order.read:ORG"
ITEM_READ = "operations.inventory.item.read:ORG"
LOCATION_READ = "operations.inventory.location.read:ORG"

# What the OPERATIONS_WORKER template grants for the task surface — no order/item/location read.
WORKER_PERMS = [TASK_READ_PERM, TASK_EXECUTE_PERM, PACK_READ_PERM, PACK_EXECUTE_PERM]

DISPLAY_LINE_FIELDS = (
    "item_name",
    "item_unit",
    "source_location_code",
    "source_location_name",
    "destination_location_code",
)


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id, stock=100)


async def _second_worker(session, org_id, floor: Floor) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    employee_id = uuid.uuid4()
    async with session.begin():
        worker = await fulfilment_services.create_worker(
            session,
            ctx_for(org_id, "operations.fulfilment.worker.write", Scope.ORG),
            employee_id=employee_id,
            warehouse_id=floor.shop.warehouse_id,
            display_name="Ben Picker",
            is_active=True,
            skills=[],
            zone_location_ids=[],
        )
    return employee_id, worker.id


async def _new_task(client, session, org_id, floor: Floor) -> dict:  # type: ignore[no-untyped-def]
    order = await accept_order(
        session,
        org_id,
        floor.shop,
        lines=[{"inventory_item_id": floor.shop.widget, "quantity": "5"}],
    )
    return (await create_tasks(client, org_id, order))[0], order


async def _get(client, org_id, perms, task_id, *, subject_ref=None):  # type: ignore[no-untyped-def]
    with as_principal(principal_for(org_id, perms, subject_ref=subject_ref)):
        return await client.get(f"/api/v1/operations/fulfilment/tasks/{task_id}")


def _assert_blank(task: dict) -> None:
    assert task["order_number"] is None
    for line in task["lines"]:
        for key in DISPLAY_LINE_FIELDS:
            assert line[key] is None, key


class TestAssignedWorkerSeesDisplayFields:
    async def test_accepted_worker_gets_number_location_and_item(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, order = await _new_task(client, session, org_id, floor)
        offer = await offer_task_http(client, org_id, task["id"], floor.worker_id)
        await accept_offer_http(client, org_id, offer["id"], floor.worker_employee_id)

        response = await _get(
            client, org_id, WORKER_PERMS, task["id"], subject_ref=floor.worker_employee_id
        )
        assert response.status_code == 200
        body = response.json()
        assert body["order_number"] == order.number
        line = body["lines"][0]
        assert line["item_name"] == "Widget"
        assert line["item_unit"] == "pcs"
        assert line["source_location_code"]
        assert line["destination_location_code"] == "STG-1"

    async def test_an_offer_not_yet_answered_is_enough(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, order = await _new_task(client, session, org_id, floor)
        await offer_task_http(client, org_id, task["id"], floor.worker_id)
        response = await _get(
            client, org_id, WORKER_PERMS, task["id"], subject_ref=floor.worker_employee_id
        )
        assert response.json()["order_number"] == order.number

    async def test_a_mutation_response_carries_them_too(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, order = await _new_task(client, session, org_id, floor)
        offer = await offer_task_http(client, org_id, task["id"], floor.worker_id)
        await accept_offer_http(client, org_id, offer["id"], floor.worker_employee_id)
        picked = await confirm_pick(
            client, org_id, task["id"], task["lines"][0]["id"], floor.worker_employee_id
        )
        assert picked["order_number"] == order.number
        assert picked["lines"][0]["item_name"] == "Widget"

    async def test_the_list_carries_them_for_my_tasks_only(
        self, client, org_id, session, floor: Floor
    ) -> None:
        mine, mine_order = await _new_task(client, session, org_id, floor)
        theirs, _ = await _new_task(client, session, org_id, floor)
        _, other_worker = await _second_worker(session, org_id, floor)
        await offer_task_http(client, org_id, mine["id"], floor.worker_id)
        await offer_task_http(client, org_id, theirs["id"], other_worker)

        with as_principal(
            principal_for(org_id, WORKER_PERMS, subject_ref=floor.worker_employee_id)
        ):
            response = await client.get("/api/v1/operations/fulfilment/tasks")
        assert response.status_code == 200
        rows = {r["id"]: r for r in response.json()["items"]}
        assert rows[mine["id"]]["order_number"] == mine_order.number
        _assert_blank(rows[theirs["id"]])


class TestNobodyElseSeesThem:
    async def test_another_workers_task_is_blank(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, _ = await _new_task(client, session, org_id, floor)
        offer = await offer_task_http(client, org_id, task["id"], floor.worker_id)
        await accept_offer_http(client, org_id, offer["id"], floor.worker_employee_id)
        other_employee, _ = await _second_worker(session, org_id, floor)

        response = await _get(client, org_id, WORKER_PERMS, task["id"], subject_ref=other_employee)
        assert response.status_code == 200  # task.read is unchanged; only the extras are withheld
        _assert_blank(response.json())

    async def test_an_unassigned_task_is_blank_to_a_worker(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, _ = await _new_task(client, session, org_id, floor)
        response = await _get(
            client, org_id, WORKER_PERMS, task["id"], subject_ref=floor.worker_employee_id
        )
        assert response.status_code == 200
        _assert_blank(response.json())

    async def test_a_released_task_goes_blank_again(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, _ = await _new_task(client, session, org_id, floor)
        offer = await offer_task_http(client, org_id, task["id"], floor.worker_id)
        await accept_offer_http(client, org_id, offer["id"], floor.worker_employee_id)
        with as_principal(
            principal_for(org_id, WORKER_PERMS, subject_ref=floor.worker_employee_id)
        ):
            released = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}/release", json={}
            )
        assert released.status_code == 200, released.text
        _assert_blank(released.json())

    async def test_a_caller_with_no_worker_row_gets_nothing(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, _ = await _new_task(client, session, org_id, floor)
        await offer_task_http(client, org_id, task["id"], floor.worker_id)
        for subject in (None, uuid.uuid4()):
            response = await _get(client, org_id, WORKER_PERMS, task["id"], subject_ref=subject)
            assert response.status_code == 200
            _assert_blank(response.json())

    async def test_a_dispatcher_without_the_reads_gets_nothing(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, _ = await _new_task(client, session, org_id, floor)
        await offer_task_http(client, org_id, task["id"], floor.worker_id)
        response = await _get(client, org_id, [TASK_READ_PERM, TASK_ASSIGN_PERM], task["id"])
        _assert_blank(response.json())

    async def test_other_org_cannot_read_the_task_at_all(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, _ = await _new_task(client, session, org_id, floor)
        offer = await offer_task_http(client, org_id, task["id"], floor.worker_id)
        await accept_offer_http(client, org_id, offer["id"], floor.worker_employee_id)
        response = await _get(
            client,
            uuid.uuid4(),
            [*WORKER_PERMS, ORDER_READ, ITEM_READ, LOCATION_READ],
            task["id"],
            subject_ref=floor.worker_employee_id,
        )
        assert response.status_code == 404

    async def test_without_task_read_there_is_no_task_and_no_extras(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, _ = await _new_task(client, session, org_id, floor)
        response = await _get(
            client, org_id, [TASK_EXECUTE_PERM], task["id"], subject_ref=floor.worker_employee_id
        )
        assert response.status_code == 403


class TestHoldersOfTheReadsAreUnchanged:
    async def test_org_wide_reads_see_every_task(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, order = await _new_task(client, session, org_id, floor)
        response = await _get(
            client, org_id, [TASK_READ_PERM, ORDER_READ, ITEM_READ, LOCATION_READ], task["id"]
        )
        body = response.json()
        assert body["order_number"] == order.number
        assert body["lines"][0]["item_name"] == "Widget"
        assert body["lines"][0]["destination_location_code"] == "STG-1"

    async def test_each_field_group_needs_its_own_read(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, order = await _new_task(client, session, org_id, floor)
        response = await _get(client, org_id, [TASK_READ_PERM, ORDER_READ], task["id"])
        body = response.json()
        assert body["order_number"] == order.number
        assert body["lines"][0]["item_name"] is None
        assert body["lines"][0]["source_location_code"] is None

    async def test_an_own_scoped_read_does_not_count_as_org_wide(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, _ = await _new_task(client, session, org_id, floor)
        response = await _get(
            client, org_id, [TASK_READ_PERM, "operations.orders.order.read:OWN"], task["id"]
        )
        assert response.json()["order_number"] is None


class TestAssignedToMeFilter:
    async def test_returns_only_my_live_tasks(self, client, org_id, session, floor: Floor) -> None:
        mine, _ = await _new_task(client, session, org_id, floor)
        theirs, _ = await _new_task(client, session, org_id, floor)
        await _new_task(client, session, org_id, floor)  # unassigned
        _, other_worker = await _second_worker(session, org_id, floor)
        await offer_task_http(client, org_id, mine["id"], floor.worker_id)
        await offer_task_http(client, org_id, theirs["id"], other_worker)

        with as_principal(
            principal_for(org_id, WORKER_PERMS, subject_ref=floor.worker_employee_id)
        ):
            response = await client.get(
                "/api/v1/operations/fulfilment/tasks", params={"assigned_to_me": "true"}
            )
        assert response.status_code == 200
        body = response.json()
        assert [r["id"] for r in body["items"]] == [mine["id"]]
        assert body["total"] == 1

    async def test_a_declined_offer_is_not_mine_any_more(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, _ = await _new_task(client, session, org_id, floor)
        offer = await offer_task_http(client, org_id, task["id"], floor.worker_id)
        with as_principal(
            principal_for(org_id, WORKER_PERMS, subject_ref=floor.worker_employee_id)
        ):
            declined = await client.post(
                f"/api/v1/operations/fulfilment/offers/{offer['id']}/decline"
            )
            assert declined.status_code == 200, declined.text
            response = await client.get(
                "/api/v1/operations/fulfilment/tasks", params={"assigned_to_me": "true"}
            )
        assert response.json()["items"] == []

    async def test_no_worker_row_means_an_empty_page_not_the_board(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, _ = await _new_task(client, session, org_id, floor)
        await offer_task_http(client, org_id, task["id"], floor.worker_id)
        for subject in (None, uuid.uuid4()):
            with as_principal(principal_for(org_id, WORKER_PERMS, subject_ref=subject)):
                response = await client.get(
                    "/api/v1/operations/fulfilment/tasks", params={"assigned_to_me": "true"}
                )
            assert response.status_code == 200
            assert response.json()["items"] == []

    async def test_other_org_gets_an_empty_page(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task, _ = await _new_task(client, session, org_id, floor)
        await offer_task_http(client, org_id, task["id"], floor.worker_id)
        with as_principal(
            principal_for(uuid.uuid4(), WORKER_PERMS, subject_ref=floor.worker_employee_id)
        ):
            response = await client.get(
                "/api/v1/operations/fulfilment/tasks", params={"assigned_to_me": "true"}
            )
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_still_needs_task_read(self, client, org_id, session, floor: Floor) -> None:
        with as_principal(
            principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=floor.worker_employee_id)
        ):
            response = await client.get(
                "/api/v1/operations/fulfilment/tasks", params={"assigned_to_me": "true"}
            )
        assert response.status_code == 403


class TestPackageLines:
    async def _picked_order(self, client, session, org_id, floor: Floor):  # type: ignore[no-untyped-def]
        task, order = await _new_task(client, session, org_id, floor)
        offer = await offer_task_http(client, org_id, task["id"], floor.worker_id)
        await accept_offer_http(client, org_id, offer["id"], floor.worker_employee_id)
        picked = await confirm_pick(
            client, org_id, task["id"], task["lines"][0]["id"], floor.worker_employee_id
        )
        with as_principal(principal_for(org_id, WORKER_PERMS)):
            response = await client.post(
                "/api/v1/operations/fulfilment/packages",
                json={
                    "order_id": str(order.id),
                    "destination_location_id": str(floor.dock_location_id),
                    "lines": [{"pick_task_line_id": picked["lines"][0]["id"], "quantity": "5"}],
                },
            )
        assert response.status_code == 201, response.text
        return order

    async def test_assigned_worker_sees_item_name_others_do_not(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await self._picked_order(client, session, org_id, floor)
        other_employee, _ = await _second_worker(session, org_id, floor)
        url = "/api/v1/operations/fulfilment/packages"

        with as_principal(
            principal_for(org_id, WORKER_PERMS, subject_ref=floor.worker_employee_id)
        ):
            mine = await client.get(url, params={"order_id": str(order.id)})
        line = mine.json()["items"][0]["lines"][0]
        assert (line["item_name"], line["item_unit"]) == ("Widget", "pcs")

        with as_principal(principal_for(org_id, WORKER_PERMS, subject_ref=other_employee)):
            theirs = await client.get(url, params={"order_id": str(order.id)})
        line = theirs.json()["items"][0]["lines"][0]
        assert line["item_name"] is None and line["item_unit"] is None
        assert line["sku"] == "WGT-001"  # the pre-existing field is untouched

    async def test_other_org_sees_no_packages(self, client, org_id, session, floor: Floor) -> None:
        order = await self._picked_order(client, session, org_id, floor)
        with as_principal(
            principal_for(uuid.uuid4(), WORKER_PERMS, subject_ref=floor.worker_employee_id)
        ):
            response = await client.get(
                "/api/v1/operations/fulfilment/packages", params={"order_id": str(order.id)}
            )
        assert response.json()["items"] == []


class TestTheRoleWasNotWidened:
    """The whole point of resolving these server-side: no new read permission, and no existing one
    handed to the worker template."""

    def test_the_worker_template_still_holds_no_order_item_or_location_read(self) -> None:
        template = next(
            t for t in catalogue.OPERATIONS_ROLE_TEMPLATES if t.code == "OPERATIONS_WORKER"
        )
        granted = {g.code for g in template.grants}
        assert "operations.orders.order.read" not in granted
        assert "operations.inventory.item.read" not in granted
        assert "operations.inventory.location.read" not in granted
