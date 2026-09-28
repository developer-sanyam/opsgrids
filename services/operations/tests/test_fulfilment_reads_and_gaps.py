"""The specific negative-authz / cross-tenant-404 test gaps the security review's MAJOR 5 named
by endpoint: GET tasks (list + detail), decline, release, close, packaging-materials GET/POST,
packages GET (list + detail), PATCH worker. `scan` and `exceptions/{id}/resolve` have their own,
larger dedicated sections in `test_fulfilment_pick_and_pack.py` (BLOCKER 1 and BLOCKER 2 each
needed a full test class of their own there).
"""

from __future__ import annotations

import uuid

import pytest

from tests.fulfilment_fixtures import (
    PACK_EXECUTE_PERM,
    PACK_READ_PERM,
    TASK_ASSIGN_PERM,
    TASK_EXECUTE_PERM,
    TASK_OVERRIDE_PERM,
    TASK_READ_PERM,
    WORKER_READ_PERM,
    WORKER_WRITE_PERM,
    Floor,
    accept_order,
    build_floor,
)
from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration


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


async def _accept_offer(client, org_id, offer_id: str, worker_employee_id) -> None:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=worker_employee_id)
    with as_principal(principal):
        response = await client.post(f"/api/v1/operations/fulfilment/offers/{offer_id}/accept")
    assert response.status_code == 200, response.text


async def _offered_task(client, org_id, floor: Floor, session) -> dict:  # type: ignore[no-untyped-def]
    order = await _accepted_order(session, org_id, floor)
    tasks = await _create_tasks(client, org_id, order)
    task_id = tasks[0]["id"]
    await _offer(client, org_id, task_id, floor.worker_id)
    return tasks[0]


async def _accepted_task(client, org_id, floor: Floor, session) -> dict:  # type: ignore[no-untyped-def]
    order = await _accepted_order(session, org_id, floor)
    tasks = await _create_tasks(client, org_id, order)
    task_id = tasks[0]["id"]
    offer = await _offer(client, org_id, task_id, floor.worker_id)
    await _accept_offer(client, org_id, offer["id"], floor.worker_employee_id)
    return tasks[0]


# ------------------------------------------------------------------ GET tasks (list + detail)


class TestTaskListAndDetailAuthz:
    async def test_a_caller_without_task_read_cannot_list(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _accepted_task(client, org_id, floor, session)
        principal = principal_for(org_id, [], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get("/api/v1/operations/fulfilment/tasks")
        assert response.status_code == 403

    async def test_a_caller_without_task_read_cannot_read_detail(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task = await _accepted_task(client, org_id, floor, session)
        principal = principal_for(org_id, [], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/fulfilment/tasks/{task['id']}")
        assert response.status_code == 403

    async def test_cross_tenant_task_detail_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task = await _accepted_task(client, org_id, floor, session)
        other = principal_for(uuid.uuid4(), [TASK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.get(f"/api/v1/operations/fulfilment/tasks/{task['id']}")
        assert response.status_code == 404

    async def test_cross_tenant_list_excludes_other_orgs_tasks(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task = await _accepted_task(client, org_id, floor, session)
        other = principal_for(uuid.uuid4(), [TASK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.get("/api/v1/operations/fulfilment/tasks")
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["items"]}
        assert task["id"] not in ids


# ------------------------------------------------------------------ decline


class TestDeclineAuthz:
    async def test_someone_elses_worker_cannot_decline(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task = await _offered_task(client, org_id, floor, session)
        # `_offered_task` returns the pre-offer snapshot; re-read the task to learn the live
        # offer's id.
        principal = principal_for(org_id, [TASK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            detail = await client.get(f"/api/v1/operations/fulfilment/tasks/{task['id']}")
        offer_id = detail.json()["offers"][0]["id"]

        stranger = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(stranger):
            response = await client.post(f"/api/v1/operations/fulfilment/offers/{offer_id}/decline")
        assert response.status_code == 403

    async def test_cross_tenant_decline_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task = await _offered_task(client, org_id, floor, session)
        principal = principal_for(org_id, [TASK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            detail = await client.get(f"/api/v1/operations/fulfilment/tasks/{task['id']}")
        offer_id = detail.json()["offers"][0]["id"]

        other = principal_for(uuid.uuid4(), [TASK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(f"/api/v1/operations/fulfilment/offers/{offer_id}/decline")
        assert response.status_code == 404


# ------------------------------------------------------------------ release


class TestReleaseAuthz:
    async def test_someone_elses_worker_cannot_release(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task = await _accepted_task(client, org_id, floor, session)
        stranger = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(stranger):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}/release", json={}
            )
        assert response.status_code == 403

    async def test_cross_tenant_release_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task = await _accepted_task(client, org_id, floor, session)
        other = principal_for(uuid.uuid4(), [TASK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}/release", json={}
            )
        assert response.status_code == 404


# ------------------------------------------------------------------ close


class TestCloseTask:
    async def _picked_task(self, client, org_id, floor: Floor, session) -> dict:  # type: ignore[no-untyped-def]
        task = await _accepted_task(client, org_id, floor, session)
        line_id = task["lines"][0]["id"]
        principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=floor.worker_employee_id)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}/lines/{line_id}/pick", json={}
            )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "PICKED"
        return response.json()

    async def test_happy_path_closes_a_picked_task(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task = await self._picked_task(client, org_id, floor, session)
        principal = principal_for(org_id, [TASK_OVERRIDE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(f"/api/v1/operations/fulfilment/tasks/{task['id']}/close")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "CLOSED"

    async def test_a_dispatcher_without_override_cannot_close(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task = await self._picked_task(client, org_id, floor, session)
        principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(f"/api/v1/operations/fulfilment/tasks/{task['id']}/close")
        assert response.status_code == 403

    async def test_cross_tenant_close_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        task = await self._picked_task(client, org_id, floor, session)
        other = principal_for(uuid.uuid4(), [TASK_OVERRIDE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(f"/api/v1/operations/fulfilment/tasks/{task['id']}/close")
        assert response.status_code == 404


# ------------------------------------------------------------------ packaging materials


class TestPackagingMaterialsAuthz:
    async def test_a_caller_without_pack_read_cannot_list(
        self, client, org_id, session, floor: Floor
    ) -> None:
        principal = principal_for(org_id, [], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get("/api/v1/operations/fulfilment/packaging-materials")
        assert response.status_code == 403

    async def test_a_reader_cannot_create_a_packaging_material(
        self, client, org_id, session, floor: Floor
    ) -> None:
        principal = principal_for(org_id, [PACK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/packaging-materials",
                json={"code": "BOX-S", "name": "Small box"},
            )
        assert response.status_code == 403

    async def test_happy_path_creates_and_lists_a_packaging_material(
        self, client, org_id, session, floor: Floor
    ) -> None:
        principal = principal_for(org_id, [PACK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            created = await client.post(
                "/api/v1/operations/fulfilment/packaging-materials",
                json={"code": "BOX-S", "name": "Small box"},
            )
        assert created.status_code == 201, created.text

        reader = principal_for(org_id, [PACK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(reader):
            listed = await client.get("/api/v1/operations/fulfilment/packaging-materials")
        assert listed.status_code == 200
        codes = {row["code"] for row in listed.json()["items"]}
        assert "BOX-S" in codes

    async def test_cross_tenant_list_excludes_other_orgs_materials(
        self, client, org_id, session, floor: Floor
    ) -> None:
        principal = principal_for(org_id, [PACK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            created = await client.post(
                "/api/v1/operations/fulfilment/packaging-materials",
                json={"code": "BOX-M", "name": "Medium box"},
            )
        assert created.status_code == 201, created.text

        other = principal_for(uuid.uuid4(), [PACK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.get("/api/v1/operations/fulfilment/packaging-materials")
        assert response.status_code == 200
        codes = {row["code"] for row in response.json()["items"]}
        assert "BOX-M" not in codes


# ------------------------------------------------------------------ packages (list + detail)


class TestPackagesReadAuthz:
    async def _packed(self, client, org_id, floor: Floor, session) -> dict:  # type: ignore[no-untyped-def]
        task = await _accepted_task(client, org_id, floor, session)
        line_id = task["lines"][0]["id"]
        worker = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=floor.worker_employee_id)
        with as_principal(worker):
            picked = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}/lines/{line_id}/pick", json={}
            )
        assert picked.status_code == 200, picked.text

        principal = principal_for(org_id, [PACK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/packages",
                json={
                    "order_id": task["order_id"],
                    "destination_location_id": str(floor.dock_location_id),
                    "lines": [{"pick_task_line_id": line_id, "quantity": "10"}],
                },
            )
        assert response.status_code == 201, response.text
        return response.json()

    async def test_a_caller_without_pack_read_cannot_list(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await self._packed(client, org_id, floor, session)
        principal = principal_for(org_id, [], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get("/api/v1/operations/fulfilment/packages")
        assert response.status_code == 403

    async def test_a_caller_without_pack_read_cannot_read_detail(
        self, client, org_id, session, floor: Floor
    ) -> None:
        package = await self._packed(client, org_id, floor, session)
        principal = principal_for(org_id, [], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/fulfilment/packages/{package['id']}")
        assert response.status_code == 403

    async def test_cross_tenant_package_detail_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        package = await self._packed(client, org_id, floor, session)
        other = principal_for(uuid.uuid4(), [PACK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.get(f"/api/v1/operations/fulfilment/packages/{package['id']}")
        assert response.status_code == 404

    async def test_cross_tenant_list_excludes_other_orgs_packages(
        self, client, org_id, session, floor: Floor
    ) -> None:
        package = await self._packed(client, org_id, floor, session)
        other = principal_for(uuid.uuid4(), [PACK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.get("/api/v1/operations/fulfilment/packages")
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["items"]}
        assert package["id"] not in ids

    async def test_happy_path_list_and_detail(self, client, org_id, session, floor: Floor) -> None:
        package = await self._packed(client, org_id, floor, session)
        principal = principal_for(org_id, [PACK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            listed = await client.get("/api/v1/operations/fulfilment/packages")
            detail = await client.get(f"/api/v1/operations/fulfilment/packages/{package['id']}")
        assert listed.status_code == 200
        assert {row["id"] for row in listed.json()["items"]} >= {package["id"]}
        assert detail.status_code == 200
        assert detail.json()["id"] == package["id"]


# ------------------------------------------------------------------ PATCH worker


class TestWorkerPatchAuthz:
    async def test_a_reader_cannot_patch(self, client, org_id, session, floor: Floor) -> None:
        principal = principal_for(org_id, [WORKER_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}",
                json={"is_active": False},
            )
        assert response.status_code == 403

    async def test_a_caller_with_no_permission_at_all_cannot_patch(
        self, client, org_id, session, floor: Floor
    ) -> None:
        principal = principal_for(org_id, [], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}",
                json={"is_active": False},
            )
        assert response.status_code == 403

    async def test_happy_path_patch(self, client, org_id, session, floor: Floor) -> None:
        principal = principal_for(org_id, [WORKER_WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}",
                json={"is_active": False},
            )
        assert response.status_code == 200, response.text
        assert response.json()["is_active"] is False
