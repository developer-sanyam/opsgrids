"""UOMs (ADR-0014 Phase 1): the per-organization directory, resolving a wire `uom` string to a
real row (creating one the first time an organization names it), and the read API that seeds the
registry lazily.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.models import Uom
from platform_core.errors import ValidationProblem
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import build_textile

pytestmark = pytest.mark.integration

READ = "operations.inventory.item.read:ORG"
WRITE = "operations.inventory.item.write:ORG"


class TestResolveOrCreateUom:
    async def test_a_registry_code_becomes_a_system_row(self, session, org_id) -> None:
        async with session.begin():
            uom = await inv_services.resolve_or_create_uom(session, org_id, "kg", "tester")
        assert (uom.code, uom.is_system, uom.dimension, uom.is_discrete) == (
            "kg",
            True,
            "mass",
            False,
        )

    async def test_case_and_aliases_fold_onto_one_row(self, session, org_id) -> None:
        async with session.begin():
            upper = await inv_services.resolve_or_create_uom(session, org_id, "KG", "tester")
            alias = await inv_services.resolve_or_create_uom(session, org_id, "kgs", "tester")
        assert upper.id == alias.id

    async def test_an_unknown_code_becomes_a_custom_continuous_row(self, session, org_id) -> None:
        async with session.begin():
            uom = await inv_services.resolve_or_create_uom(session, org_id, "MT", "tester")
        assert (uom.code, uom.is_system, uom.dimension, uom.is_discrete) == (
            "mt",
            False,
            "other",
            False,
        )

    async def test_the_same_unknown_code_in_different_case_is_one_row(
        self, session, org_id
    ) -> None:
        async with session.begin():
            first = await inv_services.resolve_or_create_uom(session, org_id, "MT", "tester")
            second = await inv_services.resolve_or_create_uom(session, org_id, "mt", "tester")
        assert first.id == second.id

    async def test_two_organizations_get_two_rows_for_the_same_code(
        self, session, admin_session, org_id
    ) -> None:
        other_org = uuid.uuid4()
        async with session.begin():
            mine = await inv_services.resolve_or_create_uom(session, org_id, "kg", "tester")
        async with admin_session.begin():
            theirs = await inv_services.resolve_or_create_uom(admin_session, other_org, "kg", "t")
        assert mine.id != theirs.id

    async def test_a_blank_code_is_refused(self, session, org_id) -> None:
        with pytest.raises(ValidationProblem):
            async with session.begin():
                await inv_services.resolve_or_create_uom(session, org_id, "   ", "tester")


class TestEnsureSeedUoms:
    async def test_seeding_is_idempotent_and_covers_the_whole_registry(
        self, session, org_id
    ) -> None:
        async with session.begin():
            await inv_services.ensure_seed_uoms(session, org_id, "tester")
        async with session.begin():
            await inv_services.ensure_seed_uoms(session, org_id, "tester")  # no-op second time
        async with session.begin():
            rows = (await session.execute(select(Uom))).scalars().all()
        codes = {r.code for r in rows}
        assert len(rows) == 19
        assert {"m", "kg", "pcs", "box"} <= codes
        assert all(r.is_system for r in rows)

    async def test_seeding_after_a_custom_unit_exists_does_not_duplicate_or_overwrite_it(
        self, session, org_id
    ) -> None:
        """A custom unit named the same as a registry code cannot exist (both are keyed on the
        same lower-cased code), but seeding must not clobber a custom unit's own dimension either
        — it only inserts codes that are missing."""
        async with session.begin():
            custom = await inv_services.resolve_or_create_uom(session, org_id, "bolt", "tester")
        async with session.begin():
            await inv_services.ensure_seed_uoms(session, org_id, "tester")
        async with session.begin():
            still = await inv_selectors.get_uom_by_code(session, org_id, "bolt")
        assert still is not None
        assert (still.id, still.dimension, still.is_system) == (custom.id, "other", False)


class TestListUomsApi:
    async def test_a_fresh_org_gets_the_full_registry_on_first_read(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, [READ])):
            resp = await client.get("/api/v1/operations/inventory/uoms?size=50")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 19
        codes = {u["code"] for u in body["items"]}
        assert {"m", "kg", "pcs", "box", "roll"} <= codes
        assert all(u["is_system"] for u in body["items"])

    async def test_a_custom_unit_an_item_used_appears_alongside_the_registry(
        self, client, session, org_id
    ) -> None:
        await build_textile(session, org_id)  # items are created with uom="m"/"kg"/"pcs" only
        async with session.begin():
            await inv_services.resolve_or_create_uom(session, org_id, "MT", "tester")
        with as_principal(principal_for(org_id, [READ])):
            resp = await client.get("/api/v1/operations/inventory/uoms?size=50")
        body = resp.json()
        custom = next(u for u in body["items"] if u["code"] == "mt")
        assert (custom["is_system"], custom["dimension"]) == (False, "other")

    async def test_listing_uoms_needs_item_read(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, ["operations.inventory.warehouse.read:ORG"])):
            resp = await client.get("/api/v1/operations/inventory/uoms")
        assert resp.status_code == 403


class TestItemUsesTheDirectory:
    async def test_creating_an_item_resolves_and_links_its_uom(
        self, client, session, org_id, data
    ) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            resp = await client.post(
                "/api/v1/operations/inventory",
                json={
                    "sku": "RM-UOM-001",
                    "name": "Widget",
                    "category": "raw_materials",
                    "warehouse": "Pune Plant",
                    "uom": "Kg",
                },
            )
        assert resp.status_code == 201
        item_id = resp.json()["id"]
        assert resp.json()["uom"] == "Kg"  # the wire field is untouched
        async with session.begin():
            item = await inv_selectors.get_inventory_item(session, org_id, uuid.UUID(item_id))
            assert item.uom_id is not None
            uom = await inv_selectors.get_uom(session, org_id, item.uom_id)
        assert uom is not None and (uom.code, uom.is_system) == ("kg", True)

    async def test_updating_an_items_uom_re_resolves_it(
        self, client, session, org_id, data
    ) -> None:
        """Re-resolution on a `uom` change is only reachable before any stock has moved (M5
        refuses it once history exists — covered in `test_ledger.py`'s
        `TestItemLevelCompatibility`), so this exercises a freshly created, still-empty item."""
        with as_principal(principal_for(org_id, [WRITE])):
            item_id = (
                await client.post(
                    "/api/v1/operations/inventory",
                    json={
                        "sku": "RM-UOM-RESOLVE",
                        "name": "Blank widget",
                        "category": "raw_materials",
                        "warehouse": "Pune Plant",
                        "uom": "kg",
                    },
                )
            ).json()["id"]
            resp = await client.patch(
                f"/api/v1/operations/inventory/{item_id}", json={"uom": "Tonnes"}
            )
        assert resp.status_code == 200
        assert resp.json()["uom"] == "Tonnes"
        async with session.begin():
            item = await inv_selectors.get_inventory_item(session, org_id, uuid.UUID(item_id))
            assert item.uom_id is not None
            uom = await inv_selectors.get_uom(session, org_id, item.uom_id)
        assert uom is not None and uom.code == "tonnes"

    async def test_a_different_spelling_of_the_same_unit_is_not_a_change(
        self, client, session, org_id, data
    ) -> None:
        """LOW #3: `changes["uom"] != item.uom` compared the raw wire strings — "PCS" on an item
        already stocked in "pcs" looked like a real change and, once the item had any stock
        history, was wrongly refused by M5's guard. Canonicalising both sides first (the same
        resolution `resolve_or_create_uom` would do) means only a *real* unit change trips it."""
        with as_principal(principal_for(org_id, [WRITE])):
            item_id = (
                await client.post(
                    "/api/v1/operations/inventory",
                    json={
                        "sku": "RM-UOM-SAME-SPELLING",
                        "name": "Bag of clips",
                        "category": "components",
                        "warehouse": "Pune Plant",
                        "uom": "pcs",
                        "opening_on_hand": 5,
                    },
                )
            ).json()["id"]
        async with session.begin():
            item = await inv_selectors.get_inventory_item(session, org_id, uuid.UUID(item_id))
            assert item.uom_id is not None
            uom_id_before = str(item.uom_id)

        with as_principal(principal_for(org_id, [WRITE])):
            resp = await client.patch(
                f"/api/v1/operations/inventory/{item_id}", json={"uom": "PCS"}
            )
        assert resp.status_code == 200
        assert resp.json()["uom"] == "PCS"  # the wire display string does update
        async with session.begin():
            item = await inv_selectors.get_inventory_item(session, org_id, uuid.UUID(item_id))
            # ...but it is still the same underlying `uoms` row: no ConflictProblem, no new row.
            assert str(item.uom_id) == uom_id_before
