"""Inventory item create / read-one / update / delete over HTTP."""

from __future__ import annotations

import uuid

import pytest

from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

WRITE_PERM = "operations.inventory.item.write:ORG"
READ_PERM = "operations.inventory.item.read:ORG"


class TestCreateInventoryItem:
    async def test_happy_path_creates_an_opening_movement(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": "RM-NEW-001",
                    "name": "New raw material",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "MT",
                    "opening_on_hand": 40,
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["on_hand"] == 40
        assert body["updated_at"] is not None

        with as_principal(principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])):
            movements = await client.get(f"/api/v1/operations/inventory/{body['id']}/movements")
        assert movements.status_code == 200
        items = movements.json()["items"]
        assert len(items) == 1
        assert items[0]["kind"] == "opening"
        assert items[0]["quantity_delta"] == 40

    async def test_duplicate_sku_is_conflict(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": data["low_stock_item"].sku,
                    "name": "Duplicate",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "MT",
                },
            )
        assert response.status_code == 409

    async def test_unknown_warehouse_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": "RM-NEW-002",
                    "name": "New raw material",
                    "category": "raw_materials",
                    "warehouse": "Nowhere Depot",
                    "uom": "MT",
                },
            )
        assert response.status_code == 422

    async def test_an_opening_on_hand_beyond_the_ledger_ceiling_is_422(
        self, client, org_id, data
    ) -> None:
        """`opening_on_hand` is whole units that get multiplied by `QTY_SCALE` before they ever
        reach the ledger; bounding it here turns an overflow into a 422 rather than a 500 out of
        the kernel or a BIGINT overflow at the database."""
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": "RM-NEW-004",
                    "name": "New raw material",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "MT",
                    "opening_on_hand": 10**13,
                },
            )
        assert response.status_code == 422

    async def test_on_hand_in_the_body_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": "RM-NEW-003",
                    "name": "New raw material",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "MT",
                    "on_hand": 5,
                },
            )
        assert response.status_code == 422

    async def test_a_reader_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": "RM-NEW-004",
                    "name": "New raw material",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "MT",
                },
            )
        assert response.status_code == 403


class TestReadOneInventoryItem:
    async def test_happy_path(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/inventory/{data['low_stock_item'].id}")
        assert response.status_code == 200
        assert response.json()["sku"] == data["low_stock_item"].sku

    async def test_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/inventory/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_cross_tenant_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(uuid.uuid4(), [READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/inventory/{data['low_stock_item'].id}")
        assert response.status_code == 404


class TestUpdateInventoryItem:
    async def test_happy_path(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}",
                json={"reorder_point": 20},
            )
        assert response.status_code == 200
        assert response.json()["reorder_point"] == 20

    async def test_on_hand_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}",
                json={"on_hand": 5},
            )
        assert response.status_code == 422

    async def test_sku_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}",
                json={"sku": "NEW-SKU"},
            )
        assert response.status_code == 422

    async def test_a_reader_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}",
                json={"reorder_point": 20},
            )
        assert response.status_code == 403


class TestDeleteInventoryItem:
    async def test_deleting_an_item_with_stock_is_conflict(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.delete(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}"
            )
        assert response.status_code == 409

    async def test_a_zeroed_item_can_be_deleted_and_recreated(self, client, org_id, data) -> None:
        write = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        stock_perm = principal_for(
            org_id, ["operations.inventory.stock.adjust:ORG"], subject_ref=data["subject_a"]
        )
        item_id = data["low_stock_item"].id
        with as_principal(stock_perm):
            adjust = await client.post(
                f"/api/v1/operations/inventory/{item_id}/adjustments",
                json={"counted_on_hand": 0},
            )
        assert adjust.status_code == 201

        with as_principal(write):
            deleted = await client.delete(f"/api/v1/operations/inventory/{item_id}")
        assert deleted.status_code == 204

        with as_principal(write):
            recreated = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": data["low_stock_item"].sku,
                    "name": "Re-created",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "MT",
                },
            )
        assert recreated.status_code == 201

    async def test_a_reader_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.delete(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}"
            )
        assert response.status_code == 403
