"""Workers over HTTP (plan F3). Mirrors `test_locations_write.py`'s shape: real status codes,
negative-authz and cross-tenant-404 on every endpoint.
"""

from __future__ import annotations

import uuid

import pytest

from tests.fulfilment_fixtures import (
    WORKER_LOCATION_READ_PERM,
    WORKER_READ_PERM,
    WORKER_WRITE_OWN_PERM,
    WORKER_WRITE_PERM,
    Floor,
    build_floor,
)
from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id)


class TestListAndGetWorkers:
    async def test_happy_path_lists_the_seeded_worker(self, client, org_id, floor: Floor) -> None:
        principal = principal_for(org_id, [WORKER_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get("/api/v1/operations/fulfilment/workers")
        assert response.status_code == 200, response.text
        ids = {row["id"] for row in response.json()["items"]}
        assert str(floor.worker_id) in ids

    async def test_zone_location_ids_is_null_without_location_read(
        self, client, org_id, floor: Floor
    ) -> None:
        principal = principal_for(org_id, [WORKER_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/fulfilment/workers/{floor.worker_id}")
        assert response.status_code == 200, response.text
        assert response.json()["zone_location_ids"] is None

    async def test_zone_location_ids_is_a_list_with_location_read(
        self, client, org_id, floor: Floor
    ) -> None:
        principal = principal_for(
            org_id, [WORKER_READ_PERM, WORKER_LOCATION_READ_PERM], subject_ref=uuid.uuid4()
        )
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/fulfilment/workers/{floor.worker_id}")
        assert response.status_code == 200, response.text
        assert response.json()["zone_location_ids"] == []

    async def test_a_caller_without_read_is_forbidden(self, client, org_id, floor: Floor) -> None:
        principal = principal_for(org_id, [], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get("/api/v1/operations/fulfilment/workers")
        assert response.status_code == 403

    async def test_cross_tenant_get_is_not_found(self, client, org_id, floor: Floor) -> None:
        other = principal_for(uuid.uuid4(), [WORKER_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.get(f"/api/v1/operations/fulfilment/workers/{floor.worker_id}")
        assert response.status_code == 404

    async def test_cross_tenant_list_excludes_other_org_workers(
        self, client, org_id, floor: Floor
    ) -> None:
        other = principal_for(uuid.uuid4(), [WORKER_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.get("/api/v1/operations/fulfilment/workers")
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["items"]}
        assert str(floor.worker_id) not in ids


class TestCreateAndUpdateWorker:
    async def test_happy_path_creates_a_worker(self, client, org_id, floor: Floor) -> None:
        principal = principal_for(org_id, [WORKER_WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/workers",
                json={
                    "employee_id": str(uuid.uuid4()),
                    "warehouse_id": str(floor.shop.warehouse_id),
                    "display_name": "Rohit Packer",
                    "is_active": True,
                    "skills": ["forklift"],
                    "zone_location_ids": [],
                },
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["display_name"] == "Rohit Packer"
        assert body["skills"] == ["forklift"]
        assert body["presence_status"] == "offline"

    async def test_a_duplicate_employee_is_conflict(self, client, org_id, floor: Floor) -> None:
        principal = principal_for(org_id, [WORKER_WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/workers",
                json={
                    "employee_id": str(floor.worker_employee_id),
                    "warehouse_id": str(floor.shop.warehouse_id),
                },
            )
        assert response.status_code == 409

    async def test_an_own_scope_writer_cannot_create(self, client, org_id, floor: Floor) -> None:
        """`worker.write` defaults to OWN — a grant no wider than that cannot create a worker for
        someone else, only set their own presence."""
        principal = principal_for(org_id, [WORKER_WRITE_OWN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/workers",
                json={
                    "employee_id": str(uuid.uuid4()),
                    "warehouse_id": str(floor.shop.warehouse_id),
                },
            )
        assert response.status_code == 403

    async def test_update_replaces_skills_and_zone_access(
        self, client, org_id, floor: Floor
    ) -> None:
        principal = principal_for(org_id, [WORKER_WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}",
                json={"skills": ["cold_storage", "forklift"], "is_active": False},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert sorted(body["skills"]) == ["cold_storage", "forklift"]
        assert body["is_active"] is False

    async def test_a_reader_cannot_create(self, client, org_id, floor: Floor) -> None:
        principal = principal_for(org_id, [WORKER_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/workers",
                json={
                    "employee_id": str(uuid.uuid4()),
                    "warehouse_id": str(floor.shop.warehouse_id),
                },
            )
        assert response.status_code == 403

    async def test_cross_tenant_warehouse_is_not_found(self, client, org_id, floor: Floor) -> None:
        other = principal_for(uuid.uuid4(), [WORKER_WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                "/api/v1/operations/fulfilment/workers",
                json={
                    "employee_id": str(uuid.uuid4()),
                    "warehouse_id": str(floor.shop.warehouse_id),
                },
            )
        assert response.status_code == 404

    async def test_cross_tenant_update_is_not_found(self, client, org_id, floor: Floor) -> None:
        other = principal_for(uuid.uuid4(), [WORKER_WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.patch(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}",
                json={"is_active": False},
            )
        assert response.status_code == 404


class TestPresence:
    async def test_the_worker_sets_their_own_presence(self, client, org_id, floor: Floor) -> None:
        principal = principal_for(
            org_id, [WORKER_WRITE_OWN_PERM], subject_ref=floor.worker_employee_id
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}/presence",
                json={"status": "online"},
            )
        assert response.status_code == 200, response.text
        assert response.json()["presence_status"] == "online"

    async def test_a_different_worker_cannot_set_someone_elses_presence(
        self, client, org_id, floor: Floor
    ) -> None:
        principal = principal_for(org_id, [WORKER_WRITE_OWN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}/presence",
                json={"status": "online"},
            )
        assert response.status_code == 403

    async def test_an_org_scoped_writer_may_set_any_workers_presence(
        self, client, org_id, floor: Floor
    ) -> None:
        principal = principal_for(org_id, [WORKER_WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}/presence",
                json={"status": "online"},
            )
        assert response.status_code == 200, response.text

    async def test_an_unrecognised_status_is_422(self, client, org_id, floor: Floor) -> None:
        principal = principal_for(
            org_id, [WORKER_WRITE_OWN_PERM], subject_ref=floor.worker_employee_id
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}/presence",
                json={"status": "busy"},
            )
        assert response.status_code == 422

    async def test_cross_tenant_presence_is_not_found(self, client, org_id, floor: Floor) -> None:
        other = principal_for(uuid.uuid4(), [WORKER_WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}/presence",
                json={"status": "online"},
            )
        assert response.status_code == 404
