"""Item master (ADR-0014 Phase 1): categories, typed attributes, variants — all reusing
`operations.inventory.item.read`/`.write`, since they are facets of the one item master
`inventory_items` already is, not a separate object with its own audience.
"""

from __future__ import annotations

import uuid

import pytest

from app.inventory import services as inv_services
from platform_core.auth import Scope
from tests.helpers import as_principal, ctx_for, principal_for
from tests.inventory_fixtures import build_textile

pytestmark = pytest.mark.integration

READ = "operations.inventory.item.read:ORG"
WRITE = "operations.inventory.item.write:ORG"


class TestItemCategories:
    async def test_create_read_list(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, [WRITE, READ])):
            created = await client.post(
                "/api/v1/operations/inventory/categories",
                json={"code": "FABRICS", "name": "Fabrics"},
            )
            assert created.status_code == 201
            body = created.json()
            assert (body["code"], body["name"], body["parent_id"]) == ("FABRICS", "Fabrics", None)

            child = await client.post(
                "/api/v1/operations/inventory/categories",
                json={"code": "COTTON", "name": "Cotton", "parent_id": body["id"]},
            )
            assert child.status_code == 201
            assert child.json()["parent_id"] == body["id"]

            one = await client.get(f"/api/v1/operations/inventory/categories/{body['id']}")
            assert one.status_code == 200

            listed = await client.get("/api/v1/operations/inventory/categories")
        assert {c["code"] for c in listed.json()["items"]} == {"FABRICS", "COTTON"}

    async def test_duplicate_code_is_conflict(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            await client.post(
                "/api/v1/operations/inventory/categories", json={"code": "DUP", "name": "One"}
            )
            clash = await client.post(
                "/api/v1/operations/inventory/categories", json={"code": "DUP", "name": "Two"}
            )
        assert clash.status_code == 409

    async def test_a_category_cannot_be_its_own_parent(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            created = await client.post(
                "/api/v1/operations/inventory/categories", json={"code": "ROOT", "name": "Root"}
            )
            cid = created.json()["id"]
            circular = await client.patch(
                f"/api/v1/operations/inventory/categories/{cid}", json={"parent_id": cid}
            )
        assert circular.status_code == 422

    async def test_a_deeper_cycle_is_refused(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            a = (
                await client.post(
                    "/api/v1/operations/inventory/categories", json={"code": "A", "name": "A"}
                )
            ).json()
            b = (
                await client.post(
                    "/api/v1/operations/inventory/categories",
                    json={"code": "B", "name": "B", "parent_id": a["id"]},
                )
            ).json()
            # A → B already; making A's parent B would close the loop.
            cyclic = await client.patch(
                f"/api/v1/operations/inventory/categories/{a['id']}",
                json={"parent_id": b["id"]},
            )
        assert cyclic.status_code == 422

    async def test_delete_is_refused_while_children_or_items_exist(
        self, client, org_id, data
    ) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            parent = (
                await client.post(
                    "/api/v1/operations/inventory/categories",
                    json={"code": "PARENT", "name": "Parent"},
                )
            ).json()
            await client.post(
                "/api/v1/operations/inventory/categories",
                json={"code": "CHILD", "name": "Child", "parent_id": parent["id"]},
            )
            blocked = await client.delete(f"/api/v1/operations/inventory/categories/{parent['id']}")
        assert blocked.status_code == 409

    async def test_delete_succeeds_once_clear(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            created = (
                await client.post(
                    "/api/v1/operations/inventory/categories",
                    json={"code": "LEAF", "name": "Leaf"},
                )
            ).json()
            deleted = await client.delete(
                f"/api/v1/operations/inventory/categories/{created['id']}"
            )
        assert deleted.status_code == 204

    async def test_writing_needs_item_write(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, [READ])):
            resp = await client.post(
                "/api/v1/operations/inventory/categories", json={"code": "X", "name": "X"}
            )
        assert resp.status_code == 403

    async def test_another_organizations_category_is_not_found(
        self, client, admin_session, org_id
    ) -> None:
        other_org = uuid.uuid4()
        ctx = ctx_for(other_org, "operations.inventory.item.write", Scope.ORG)
        async with admin_session.begin():
            other = await inv_services.create_item_category(
                admin_session, ctx, code="THEIRS", name="Theirs"
            )
        with as_principal(principal_for(org_id, [READ])):
            resp = await client.get(f"/api/v1/operations/inventory/categories/{other.id}")
        assert resp.status_code == 404


class TestItemAttributes:
    async def test_set_read_and_overwrite(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [WRITE, READ])):
            set_resp = await client.put(
                f"/api/v1/operations/inventory/{item_id}/attributes/gsm",
                json={"value_type": "number", "value_number": 180},
            )
            assert set_resp.status_code == 200
            assert set_resp.json() == {
                "id": set_resp.json()["id"],
                "key": "gsm",
                "value_type": "number",
                "value_text": None,
                "value_number": 180,
                "value_boolean": None,
                "value_date": None,
            }

            listed = await client.get(f"/api/v1/operations/inventory/{item_id}/attributes")
            assert [a["key"] for a in listed.json()] == ["gsm"]

            overwritten = await client.put(
                f"/api/v1/operations/inventory/{item_id}/attributes/gsm",
                json={"value_type": "text", "value_text": "heavy"},
            )
        assert overwritten.status_code == 200
        assert overwritten.json()["value_type"] == "text"
        assert overwritten.json()["value_text"] == "heavy"
        assert overwritten.json()["value_number"] is None

    async def test_a_mismatched_value_field_is_rejected(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [WRITE])):
            # value_type=number but a text value is given: rejected before it ever reaches the
            # database — no untyped mass-assignment.
            resp = await client.put(
                f"/api/v1/operations/inventory/{item_id}/attributes/width",
                json={"value_type": "number", "value_text": "wide"},
            )
        assert resp.status_code == 422

    async def test_more_than_one_value_field_is_rejected(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [WRITE])):
            resp = await client.put(
                f"/api/v1/operations/inventory/{item_id}/attributes/width",
                json={"value_type": "number", "value_number": 5, "value_text": "also this"},
            )
        assert resp.status_code == 422

    async def test_an_unknown_field_is_rejected(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [WRITE])):
            resp = await client.put(
                f"/api/v1/operations/inventory/{item_id}/attributes/width",
                json={"value_type": "number", "value_number": 5, "value": 5},
            )
        assert resp.status_code == 422

    async def test_delete_then_missing_is_404(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [WRITE])):
            await client.put(
                f"/api/v1/operations/inventory/{item_id}/attributes/shade",
                json={"value_type": "text", "value_text": "navy"},
            )
            deleted = await client.delete(
                f"/api/v1/operations/inventory/{item_id}/attributes/shade"
            )
            assert deleted.status_code == 204
            again = await client.delete(f"/api/v1/operations/inventory/{item_id}/attributes/shade")
        assert again.status_code == 404

    async def test_writing_needs_item_write(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [READ])):
            resp = await client.put(
                f"/api/v1/operations/inventory/{item_id}/attributes/gsm",
                json={"value_type": "number", "value_number": 1},
            )
        assert resp.status_code == 403

    async def test_another_organizations_item_is_not_found(
        self, client, admin_session, org_id
    ) -> None:
        other = await build_textile(admin_session, uuid.uuid4())
        with as_principal(principal_for(org_id, [WRITE])):
            resp = await client.put(
                f"/api/v1/operations/inventory/{other.fabric}/attributes/gsm",
                json={"value_type": "number", "value_number": 1},
            )
        assert resp.status_code == 404


class TestItemVariants:
    async def test_create_read_update_delete(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [WRITE, READ])):
            created = await client.post(
                f"/api/v1/operations/inventory/{item_id}/variants",
                json={"sku": "RM-ALU-7075-BLK", "name": "Black anodised"},
            )
            assert created.status_code == 201
            variant_id = created.json()["id"]

            listed = await client.get(f"/api/v1/operations/inventory/{item_id}/variants")
            assert [v["sku"] for v in listed.json()] == ["RM-ALU-7075-BLK"]

            updated = await client.patch(
                f"/api/v1/operations/inventory/{item_id}/variants/{variant_id}",
                json={"is_active": False},
            )
            assert updated.status_code == 200
            assert updated.json()["is_active"] is False

            deleted = await client.delete(
                f"/api/v1/operations/inventory/{item_id}/variants/{variant_id}"
            )
        assert deleted.status_code == 204

    async def test_duplicate_sku_is_conflict(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [WRITE])):
            await client.post(
                f"/api/v1/operations/inventory/{item_id}/variants",
                json={"sku": "RM-ALU-7075-RED", "name": "Red"},
            )
            clash = await client.post(
                f"/api/v1/operations/inventory/{item_id}/variants",
                json={"sku": "RM-ALU-7075-RED", "name": "Also red"},
            )
        assert clash.status_code == 409

    async def test_writing_needs_item_write(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [READ])):
            resp = await client.post(
                f"/api/v1/operations/inventory/{item_id}/variants",
                json={"sku": "X", "name": "X"},
            )
        assert resp.status_code == 403

    async def test_a_variant_from_another_item_is_not_found(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        other_item_id = data["healthy_item"].id
        with as_principal(principal_for(org_id, [WRITE])):
            created = await client.post(
                f"/api/v1/operations/inventory/{item_id}/variants",
                json={"sku": "RM-ALU-7075-XL", "name": "XL"},
            )
            variant_id = created.json()["id"]
            resp = await client.patch(
                f"/api/v1/operations/inventory/{other_item_id}/variants/{variant_id}",
                json={"name": "Hijacked"},
            )
        assert resp.status_code == 404


class TestItemMasterFieldsOnTheItemCard:
    async def test_creating_an_item_with_master_fields(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            category = (
                await client.post(
                    "/api/v1/operations/inventory/categories",
                    json={"code": "TEXT-01", "name": "Textiles"},
                )
            ).json()
            resp = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": "RM-TEX-001",
                    "name": "Cotton fabric",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "m",
                    "item_category_id": category["id"],
                    "hsn_code": "5208",
                    "track_lot": True,
                    "track_roll": True,
                    "is_active": True,
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["item_category_id"] == category["id"]
        assert (body["hsn_code"], body["track_lot"], body["track_roll"]) == ("5208", True, True)
        assert (body["track_expiry"], body["track_quality"]) == (False, False)

    async def test_an_unknown_category_id_is_a_validation_error(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            resp = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": "RM-TEX-002",
                    "name": "Cotton fabric",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "m",
                    "item_category_id": str(uuid.uuid4()),
                },
            )
        assert resp.status_code == 422

    async def test_listing_by_item_category_id(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [WRITE, READ])):
            category = (
                await client.post(
                    "/api/v1/operations/inventory/categories",
                    json={"code": "TEXT-02", "name": "Textiles 2"},
                )
            ).json()
            await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": "RM-TEX-003",
                    "name": "Wool fabric",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "m",
                    "item_category_id": category["id"],
                },
            )
            listed = await client.get(
                f"/api/v1/operations/inventory?item_category_id={category['id']}"
            )
        assert [i["sku"] for i in listed.json()["items"]] == ["RM-TEX-003"]
