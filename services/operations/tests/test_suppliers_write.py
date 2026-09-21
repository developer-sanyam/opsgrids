"""Supplier create / update / delete over HTTP."""

from __future__ import annotations

import uuid

import pytest

from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

WRITE_PERM = "operations.procurement.supplier.write:ORG"
READ_PERM = "operations.procurement.supplier.read:ORG"


class TestCreateSupplier:
    async def test_happy_path_defaults_since_to_today(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/suppliers",
                json={"code": "SUP-NEW", "name": "New Supplier", "category": "packaging"},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["since"] is not None
        assert body["spend_minor"] == 0
        assert body["open_pos"] == 0

    async def test_duplicate_code_is_conflict(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/suppliers",
                json={
                    "code": data["meridian"].code,
                    "name": "Duplicate",
                    "category": "raw_materials",
                },
            )
        assert response.status_code == 409

    async def test_spend_minor_in_the_body_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/suppliers",
                json={
                    "code": "SUP-NEW2",
                    "name": "New Supplier",
                    "category": "packaging",
                    "spend_minor": 1000,
                },
            )
        assert response.status_code == 422

    async def test_a_reader_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/suppliers",
                json={"code": "SUP-NEW3", "name": "New Supplier", "category": "packaging"},
            )
        assert response.status_code == 403


class TestUpdateSupplier:
    async def test_happy_path_status_change(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/suppliers/{data['volt'].id}",
                json={"status": "on_hold"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "on_hold"

    async def test_rename_cascades_to_purchase_orders(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/suppliers/{data['meridian'].id}",
                json={"name": "Meridian Metals Renamed"},
            )
        assert response.status_code == 200

        with as_principal(
            principal_for(
                org_id,
                ["operations.procurement.purchase_order.read:ORG"],
                subject_ref=data["subject_a"],
            )
        ):
            po = await client.get(f"/api/v1/operations/purchase-orders/{data['po_a'].id}")
        assert po.json()["supplier_name"] == "Meridian Metals Renamed"

    async def test_code_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/suppliers/{data['volt'].id}",
                json={"code": "SUP-CHANGED"},
            )
        assert response.status_code == 422

    async def test_currency_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/suppliers/{data['volt'].id}",
                json={"currency": "USD"},
            )
        assert response.status_code == 422

    async def test_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/suppliers/{uuid.uuid4()}", json={"status": "on_hold"}
            )
        assert response.status_code == 404

    async def test_a_reader_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/suppliers/{data['volt'].id}", json={"status": "on_hold"}
            )
        assert response.status_code == 403


class TestDeleteSupplier:
    async def test_deleting_a_supplier_with_an_open_po_is_conflict(
        self, client, org_id, data
    ) -> None:
        # `meridian` has po_a, still `approved` (open).
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.delete(f"/api/v1/operations/suppliers/{data['meridian'].id}")
        assert response.status_code == 409

    async def test_a_supplier_with_no_open_pos_can_be_deleted_and_recoded(
        self, client, org_id, data
    ) -> None:
        # `volt`'s only PO (po_b) is `received`, not open.
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            deleted = await client.delete(f"/api/v1/operations/suppliers/{data['volt'].id}")
        assert deleted.status_code == 204

        with as_principal(principal):
            recreated = await client.post(
                "/api/v1/operations/suppliers",
                json={"code": data["volt"].code, "name": "Volt Reborn", "category": "components"},
            )
        assert recreated.status_code == 201

    async def test_a_reader_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.delete(f"/api/v1/operations/suppliers/{data['volt'].id}")
        assert response.status_code == 403

    async def test_cross_tenant_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(uuid.uuid4(), [WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.delete(f"/api/v1/operations/suppliers/{data['volt'].id}")
        assert response.status_code == 404
