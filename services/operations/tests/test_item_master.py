"""Item master (ADR-0014 Phase 1): categories, typed attributes, variants — all reusing
`operations.inventory.item.read`/`.write`, since they are facets of the one item master
`inventory_items` already is, not a separate object with its own audience.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.models import SCHEMA, AuditLog, ItemAttribute, ItemCategory, OutboxEvent
from app.schemas import ItemCategoryPatch
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem, ValidationProblem
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

    async def test_reading_needs_item_read(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            created = (
                await client.post(
                    "/api/v1/operations/inventory/categories", json={"code": "RO", "name": "RO"}
                )
            ).json()
        with as_principal(principal_for(org_id, [])):
            listed = await client.get("/api/v1/operations/inventory/categories")
            one = await client.get(f"/api/v1/operations/inventory/categories/{created['id']}")
        assert listed.status_code == 403
        assert one.status_code == 403

    async def test_patching_and_deleting_need_item_write(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            created = (
                await client.post(
                    "/api/v1/operations/inventory/categories", json={"code": "PD", "name": "PD"}
                )
            ).json()
        with as_principal(principal_for(org_id, [READ])):
            patched = await client.patch(
                f"/api/v1/operations/inventory/categories/{created['id']}", json={"name": "New"}
            )
            deleted = await client.delete(
                f"/api/v1/operations/inventory/categories/{created['id']}"
            )
        assert patched.status_code == 403
        assert deleted.status_code == 403

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

    async def test_another_organizations_category_cannot_be_patched_or_deleted(
        self, client, admin_session, org_id
    ) -> None:
        other_org = uuid.uuid4()
        ctx = ctx_for(other_org, "operations.inventory.item.write", Scope.ORG)
        async with admin_session.begin():
            other = await inv_services.create_item_category(
                admin_session, ctx, code="THEIRS2", name="Theirs2"
            )
        with as_principal(principal_for(org_id, [WRITE])):
            patched = await client.patch(
                f"/api/v1/operations/inventory/categories/{other.id}", json={"name": "Hijacked"}
            )
            deleted = await client.delete(f"/api/v1/operations/inventory/categories/{other.id}")
        assert patched.status_code == 404
        assert deleted.status_code == 404


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

    async def test_deleting_needs_item_write(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [WRITE, READ])):
            await client.put(
                f"/api/v1/operations/inventory/{item_id}/attributes/gsm",
                json={"value_type": "number", "value_number": 1},
            )
        with as_principal(principal_for(org_id, [READ])):
            resp = await client.delete(f"/api/v1/operations/inventory/{item_id}/attributes/gsm")
        assert resp.status_code == 403

    async def test_reading_needs_item_read(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [])):
            resp = await client.get(f"/api/v1/operations/inventory/{item_id}/attributes")
        assert resp.status_code == 403

    async def test_the_database_itself_refuses_a_mismatched_value_and_type(
        self, session, org_id, data
    ) -> None:
        """The API's own validation (`test_a_mismatched_value_field_is_rejected`) is not the only
        line of defence (M1) — `value_matches_type` on the table itself refuses the same shape
        even for a write that reaches the database directly."""
        item_id = data["low_stock_item"].id
        with pytest.raises(IntegrityError, match="value_matches_type"):
            async with session.begin():
                session.add(
                    ItemAttribute(
                        organization_id=org_id,
                        inventory_item_id=item_id,
                        key="bad",
                        value_type="number",
                        value_text="not a number",
                    )
                )
                await session.flush()

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

    async def test_another_organizations_item_attribute_cannot_be_deleted(
        self, client, admin_session, org_id
    ) -> None:
        other = await build_textile(admin_session, uuid.uuid4())
        with as_principal(principal_for(org_id, [WRITE])):
            resp = await client.delete(
                f"/api/v1/operations/inventory/{other.fabric}/attributes/gsm"
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

    async def test_a_variant_sku_cannot_collide_with_an_items_own_sku(
        self, client, org_id, data
    ) -> None:
        """A variant SKU shares one namespace with item SKUs (LOW) — nothing else distinguishes
        a receipt or move line's SKU as naming the item vs. one of its variants."""
        item_id = data["low_stock_item"].id
        other_item_sku = data["healthy_item"].sku
        with as_principal(principal_for(org_id, [WRITE])):
            resp = await client.post(
                f"/api/v1/operations/inventory/{item_id}/variants",
                json={"sku": other_item_sku, "name": "Collides with another item"},
            )
        assert resp.status_code == 409

    async def test_a_new_items_sku_cannot_collide_with_an_existing_variants_sku(
        self, client, org_id, data
    ) -> None:
        """LOW #4, the other direction: an item's own create path checks the variant namespace
        too, and the two checks are serialised by a per-SKU advisory lock so a variant-create and
        an item-create naming the same SKU cannot both pass their own table's check at once."""
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [WRITE])):
            variant = await client.post(
                f"/api/v1/operations/inventory/{item_id}/variants",
                json={"sku": "RM-ALU-7075-VARIANT", "name": "A variant"},
            )
            assert variant.status_code == 201
            resp = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": "RM-ALU-7075-VARIANT",
                    "name": "Collides with a variant",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "kg",
                },
            )
        assert resp.status_code == 409

    async def test_writing_needs_item_write(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [READ])):
            resp = await client.post(
                f"/api/v1/operations/inventory/{item_id}/variants",
                json={"sku": "X", "name": "X"},
            )
        assert resp.status_code == 403

    async def test_reading_needs_item_read(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [])):
            resp = await client.get(f"/api/v1/operations/inventory/{item_id}/variants")
        assert resp.status_code == 403

    async def test_patching_and_deleting_need_item_write(self, client, org_id, data) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [WRITE])):
            created = (
                await client.post(
                    f"/api/v1/operations/inventory/{item_id}/variants",
                    json={"sku": "RM-ALU-7075-PD", "name": "For permission test"},
                )
            ).json()
        with as_principal(principal_for(org_id, [READ])):
            patched = await client.patch(
                f"/api/v1/operations/inventory/{item_id}/variants/{created['id']}",
                json={"name": "New"},
            )
            deleted = await client.delete(
                f"/api/v1/operations/inventory/{item_id}/variants/{created['id']}"
            )
        assert patched.status_code == 403
        assert deleted.status_code == 403

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

    async def test_another_organizations_variant_cannot_be_patched_or_deleted(
        self, client, admin_session, org_id
    ) -> None:
        other = await build_textile(admin_session, uuid.uuid4())
        other_ctx = ctx_for(other.org_id, "operations.inventory.item.write", Scope.ORG)
        async with admin_session.begin():
            item = await inv_selectors.get_inventory_item(admin_session, other.org_id, other.fabric)
            assert item is not None
            variant = await inv_services.create_item_variant(
                admin_session, other_ctx, item, sku="THEIRS-VAR", name="Theirs"
            )
        with as_principal(principal_for(org_id, [WRITE])):
            patched = await client.patch(
                f"/api/v1/operations/inventory/{other.fabric}/variants/{variant.id}",
                json={"name": "Hijacked"},
            )
            deleted = await client.delete(
                f"/api/v1/operations/inventory/{other.fabric}/variants/{variant.id}"
            )
        assert patched.status_code == 404
        assert deleted.status_code == 404


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

    @pytest.mark.parametrize("hsn_code", ["", "5208", "520810", "52081010"])
    async def test_a_well_formed_hsn_code_is_accepted(
        self, client, org_id, data, hsn_code: str
    ) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            resp = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": f"RM-HSN-OK-{hsn_code or 'BLANK'}",
                    "name": "Cotton fabric",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "m",
                    "hsn_code": hsn_code,
                },
            )
        assert resp.status_code == 201
        assert resp.json()["hsn_code"] == hsn_code

    @pytest.mark.parametrize("hsn_code", ["520", "52081", "5208101", "520810101", "ABCD", "52 08"])
    async def test_a_malformed_hsn_code_is_a_validation_error(
        self, client, org_id, data, hsn_code: str
    ) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            resp = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": "RM-HSN-BAD",
                    "name": "Cotton fabric",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "m",
                    "hsn_code": hsn_code,
                },
            )
        assert resp.status_code == 422

    async def test_patching_to_a_malformed_hsn_code_is_a_validation_error(
        self, client, org_id, data
    ) -> None:
        item_id = data["low_stock_item"].id
        with as_principal(principal_for(org_id, [WRITE])):
            resp = await client.patch(
                f"/api/v1/operations/inventory/{item_id}", json={"hsn_code": "12"}
            )
        assert resp.status_code == 422

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


class TestCategoryTreeConcurrency:
    """H2: two concurrent re-parents, each checking a snapshot that looks cycle-free in
    isolation, can still land on a cycle once both commit — `A.parent = B` and `B.parent = A`,
    raced, each read the *other's* pre-race `parent_id IS NULL` and neither one's own check ever
    sees what the other is about to do. `_lock_category_tree`'s `pg_advisory_xact_lock` must
    serialise them: whichever gets the lock first commits, and the second one's check then runs
    against the real, updated tree and is refused.
    """

    @pytest.fixture
    async def maker(self):  # type: ignore[no-untyped-def]
        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
            pool_size=10,
            max_overflow=0,
        )
        yield async_sessionmaker(engine, expire_on_commit=False)
        await engine.dispose()

    async def _connection(self, maker, org_id: uuid.UUID) -> AsyncSession:  # type: ignore[no-untyped-def]
        s = maker()

        @event.listens_for(s.sync_session, "after_begin")
        def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        return s

    async def test_two_concurrent_reparents_never_create_a_cycle(
        self, session, maker, org_id
    ) -> None:
        ctx = ctx_for(org_id, "operations.inventory.item.write", Scope.ORG)
        async with session.begin():
            a = await inv_services.create_item_category(session, ctx, code="A", name="A")
            b = await inv_services.create_item_category(session, ctx, code="B", name="B")

        async def reparent(category_id: uuid.UUID, new_parent_id: uuid.UUID) -> str:
            s = await self._connection(maker, org_id)
            try:
                async with s.begin():
                    inner_ctx = ctx_for(org_id, "operations.inventory.item.write", Scope.ORG)
                    category = await inv_selectors.get_item_category(s, org_id, category_id)
                    assert category is not None
                    await inv_services.update_item_category(
                        s,
                        inner_ctx,
                        category,
                        ItemCategoryPatch(parent_id=str(new_parent_id)),
                    )
            except ValidationProblem:
                return "refused"
            finally:
                await s.close()
            return "ok"

        results = await asyncio.gather(
            reparent(a.id, b.id), reparent(b.id, a.id), return_exceptions=False
        )
        # Never both "ok" — that would be a 2-cycle. Exactly one direction wins the lock.
        assert sorted(results) == ["ok", "refused"]

        async with session.begin():
            # `a`/`b` are already in this session's identity map from the creates above, at
            # their pre-race values — the race ran on separate connections, so a plain selector
            # read would return that stale copy rather than what was actually committed.
            rows = (
                await session.execute(
                    select(ItemCategory)
                    .where(ItemCategory.id.in_([a.id, b.id]))
                    .execution_options(populate_existing=True)
                )
            ).scalars()
            parents = {row.id: row.parent_id for row in rows}
        # Exactly one of the two now has a parent, and it is not a cycle.
        non_null = [p for p in parents.values() if p is not None]
        assert len(non_null) == 1

    async def test_two_concurrent_deletes_of_the_same_category_write_one_audit_row_and_event(
        self, session, maker, org_id
    ) -> None:
        """LOW #6: both routes read the category (not yet deleted) before either reaches
        `_lock_category_tree`. Without re-reading it under the lock, the second delete to get the
        lock still sees its own stale, pre-race copy and unconditionally soft-deletes it again —
        a second audit row and a second event for one logical delete."""
        ctx = ctx_for(org_id, "operations.inventory.item.write", Scope.ORG)
        async with session.begin():
            category = await inv_services.create_item_category(session, ctx, code="DBL", name="DBL")

        async def delete_once() -> str:
            s = await self._connection(maker, org_id)
            try:
                async with s.begin():
                    inner_ctx = ctx_for(org_id, "operations.inventory.item.write", Scope.ORG)
                    current = await inv_selectors.get_item_category(s, org_id, category.id)
                    assert current is not None  # both requests read it before either deletes
                    await inv_services.delete_item_category(s, inner_ctx, current)
            finally:
                await s.close()
            return "ok"

        results = await asyncio.gather(delete_once(), delete_once())
        assert results == ["ok", "ok"]  # a delete is idempotent; neither call raises

        async with session.begin():
            audits = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.entity_id == category.id,
                            AuditLog.action == "item_category.deleted",
                        )
                    )
                )
                .scalars()
                .all()
            )
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.organization_id == org_id,
                            OutboxEvent.aggregate_id == category.id,
                            OutboxEvent.event_type == "operations.inventory.item_category.deleted",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(audits) == 1
        assert len(events) == 1


class TestSkuCreationConcurrency:
    """LOW #4: an item's SKU and a variant's SKU share one namespace across two different
    tables, each checked with its own `SELECT` — without `_lock_sku` serialising them, a
    concurrent item-create and variant-create naming the same SKU can each see the other's table
    as clear and both commit."""

    @pytest.fixture
    async def maker(self):  # type: ignore[no-untyped-def]
        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
            pool_size=10,
            max_overflow=0,
        )
        yield async_sessionmaker(engine, expire_on_commit=False)
        await engine.dispose()

    async def _connection(self, maker, org_id: uuid.UUID) -> AsyncSession:  # type: ignore[no-untyped-def]
        s = maker()

        @event.listens_for(s.sync_session, "after_begin")
        def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        return s

    async def test_a_concurrent_item_create_and_variant_create_never_both_win_the_same_sku(
        self, session, maker, org_id
    ) -> None:
        ctx = ctx_for(org_id, "operations.inventory.item.write", Scope.ORG)
        async with session.begin():
            await inv_services.create_warehouse(session, ctx, code="WH-RACE", name="Race Store")
            host = await inv_services.create_inventory_item(
                session,
                ctx,
                sku="HOST-ITEM",
                name="Host item",
                category="raw_materials",
                warehouse="Race Store",
                uom="kg",
            )

        async def create_item() -> str:
            s = await self._connection(maker, org_id)
            try:
                async with s.begin():
                    inner_ctx = ctx_for(org_id, "operations.inventory.item.write", Scope.ORG)
                    await inv_services.create_inventory_item(
                        s,
                        inner_ctx,
                        sku="RACE-SKU",
                        name="Racing item",
                        category="raw_materials",
                        warehouse="Race Store",
                        uom="kg",
                    )
            except ConflictProblem:
                return "refused"
            finally:
                await s.close()
            return "ok"

        async def create_variant() -> str:
            s = await self._connection(maker, org_id)
            try:
                async with s.begin():
                    inner_ctx = ctx_for(org_id, "operations.inventory.item.write", Scope.ORG)
                    item = await inv_selectors.get_inventory_item(s, org_id, host.id)
                    assert item is not None
                    await inv_services.create_item_variant(
                        s, inner_ctx, item, sku="RACE-SKU", name="Racing variant"
                    )
            except ConflictProblem:
                return "refused"
            finally:
                await s.close()
            return "ok"

        results = await asyncio.gather(create_item(), create_variant(), return_exceptions=False)
        # Never both "ok" — that would be the same SKU claimed by an item and a variant at once.
        assert sorted(results) == ["ok", "refused"]
