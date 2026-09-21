"""Warehouse create / update / delete over HTTP."""

from __future__ import annotations

import uuid

import pytest

from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

WRITE_PERM = "operations.inventory.warehouse.write:ORG"
READ_PERM = "operations.inventory.warehouse.read:ORG"


class TestCreateWarehouse:
    async def test_happy_path(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/warehouses",
                json={"code": "WH-NEW", "name": "New Depot", "location": "Nagpur"},
            )
        assert response.status_code == 201

    async def test_duplicate_code_is_conflict(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/warehouses",
                json={"code": "WH-PUN", "name": "Duplicate Name"},
            )
        assert response.status_code == 409

    async def test_duplicate_name_is_conflict(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/warehouses",
                json={"code": "WH-DUP", "name": "Pune Plant"},
            )
        assert response.status_code == 409

    async def test_a_reader_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/warehouses", json={"code": "WH-NEW2", "name": "New Depot"}
            )
        assert response.status_code == 403


class TestUpdateWarehouse:
    async def test_rename_cascades_to_inventory_and_purchase_orders(
        self, client, org_id, data
    ) -> None:
        write_principal = principal_for(
            org_id, [WRITE_PERM, READ_PERM], subject_ref=data["subject_a"]
        )
        with as_principal(write_principal):
            listed = await client.get("/api/v1/operations/warehouses")
        pune = next(w for w in listed.json()["items"] if w["name"] == "Pune Plant")

        with as_principal(write_principal):
            response = await client.patch(
                f"/api/v1/operations/warehouses/{pune['id']}", json={"name": "Pune Plant Renamed"}
            )
        assert response.status_code == 200

        with as_principal(
            principal_for(
                org_id, ["operations.inventory.item.read:ORG"], subject_ref=data["subject_a"]
            )
        ):
            item = await client.get(f"/api/v1/operations/inventory/{data['low_stock_item'].id}")
        assert item.json()["warehouse"] == "Pune Plant Renamed"

        with as_principal(
            principal_for(
                org_id,
                ["operations.procurement.purchase_order.read:ORG"],
                subject_ref=data["subject_a"],
            )
        ):
            po = await client.get(f"/api/v1/operations/purchase-orders/{data['po_a'].id}")
        assert po.json()["warehouse"] == "Pune Plant Renamed"

    async def test_code_is_422(self, client, org_id, data) -> None:
        write_principal = principal_for(
            org_id, [WRITE_PERM, READ_PERM], subject_ref=data["subject_a"]
        )
        with as_principal(write_principal):
            listed = await client.get("/api/v1/operations/warehouses")
        pune = next(w for w in listed.json()["items"] if w["name"] == "Pune Plant")

        with as_principal(write_principal):
            response = await client.patch(
                f"/api/v1/operations/warehouses/{pune['id']}", json={"code": "WH-CHANGED"}
            )
        assert response.status_code == 422

    async def test_a_reader_is_forbidden(self, client, org_id, data) -> None:
        write_principal = principal_for(
            org_id, [WRITE_PERM, READ_PERM], subject_ref=data["subject_a"]
        )
        with as_principal(write_principal):
            listed = await client.get("/api/v1/operations/warehouses")
        pune = next(w for w in listed.json()["items"] if w["name"] == "Pune Plant")

        with as_principal(principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])):
            response = await client.patch(
                f"/api/v1/operations/warehouses/{pune['id']}", json={"location": "Elsewhere"}
            )
        assert response.status_code == 403


class TestDeleteWarehouse:
    async def test_deleting_a_warehouse_with_stock_is_conflict(self, client, org_id, data) -> None:
        write_principal = principal_for(
            org_id, [WRITE_PERM, READ_PERM], subject_ref=data["subject_a"]
        )
        with as_principal(write_principal):
            listed = await client.get("/api/v1/operations/warehouses")
        pune = next(w for w in listed.json()["items"] if w["name"] == "Pune Plant")

        with as_principal(write_principal):
            response = await client.delete(f"/api/v1/operations/warehouses/{pune['id']}")
        assert response.status_code == 409

    async def test_an_empty_warehouse_can_be_deleted(self, client, org_id, data) -> None:
        write_principal = principal_for(
            org_id, [WRITE_PERM, READ_PERM], subject_ref=data["subject_a"]
        )
        with as_principal(write_principal):
            created = await client.post(
                "/api/v1/operations/warehouses", json={"code": "WH-EMPTY", "name": "Empty Depot"}
            )
        assert created.status_code == 201

        with as_principal(write_principal):
            deleted = await client.delete(f"/api/v1/operations/warehouses/{created.json()['id']}")
        assert deleted.status_code == 204

    async def test_cross_tenant_is_not_found(self, client, org_id, data) -> None:
        write_principal = principal_for(
            org_id, [WRITE_PERM, READ_PERM], subject_ref=data["subject_a"]
        )
        with as_principal(write_principal):
            listed = await client.get("/api/v1/operations/warehouses")
        pune = next(w for w in listed.json()["items"] if w["name"] == "Pune Plant")

        other_principal = principal_for(uuid.uuid4(), [WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other_principal):
            response = await client.delete(f"/api/v1/operations/warehouses/{pune['id']}")
        assert response.status_code == 404
