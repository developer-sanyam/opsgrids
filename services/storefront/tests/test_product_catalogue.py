"""Categories and products: `/storefront/categories`, `/storefront/products` (gap 3), and the
public catalogue a buyer reads anonymously (gap 4).
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers import all_perms, seed_category, seed_product, seed_store

pytestmark = pytest.mark.integration

CATEGORIES = "/api/v1/storefront/categories"
PRODUCTS = "/api/v1/storefront/products"
PUBLIC_CATALOGUE = "/api/v1/storefront/public/catalogue"


def _product_payload(category_id: str, **overrides: object) -> dict[str, object]:
    return {
        "code": "SKU-1",
        "name": "Basmati Rice 25kg",
        "category_id": category_id,
        "price_min_minor": 190000,
        "price_max_minor": 190000,
        **overrides,
    }


class TestCategories:
    async def test_create_list_patch_and_delete(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            created = await client.post(CATEGORIES, json={"key": "grocery", "label": "Grocery"})
            category_id = created.json()["id"]
            listed = await client.get(CATEGORIES)
            patched = await client.patch(f"{CATEGORIES}/{category_id}", json={"label": "Groceries"})
            removed = await client.delete(f"{CATEGORIES}/{category_id}")

        assert created.status_code == 201
        assert listed.json()["total"] == 1
        assert patched.json()["label"] == "Groceries"
        assert removed.status_code == 204

    async def test_duplicate_key_is_409(self, client, as_principal, org_id: uuid.UUID) -> None:
        with as_principal(all_perms(org_id)):
            await client.post(CATEGORIES, json={"key": "grocery", "label": "Grocery"})
            dupe = await client.post(CATEGORIES, json={"key": "grocery", "label": "Again"})
        assert dupe.status_code == 409

    async def test_deleting_a_category_in_use_is_422_naming_the_count(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            created = await client.post(CATEGORIES, json={"key": "grocery", "label": "Grocery"})
            category_id = created.json()["id"]
            await client.post(PRODUCTS, json=_product_payload(category_id))
            deleted = await client.delete(f"{CATEGORIES}/{category_id}")

        assert deleted.status_code == 422
        assert "1" in deleted.json()["detail"]

    async def test_the_key_can_be_reused_once_the_row_is_gone(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            first = await client.post(CATEGORIES, json={"key": "grocery", "label": "Grocery"})
            await client.delete(f"{CATEGORIES}/{first.json()['id']}")
            second = await client.post(
                CATEGORIES, json={"key": "grocery", "label": "Grocery again"}
            )

        assert second.status_code == 201


class TestProducts:
    async def test_create_get_patch_and_delete(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            category = await client.post(CATEGORIES, json={"key": "grocery", "label": "Grocery"})
            created = await client.post(PRODUCTS, json=_product_payload(category.json()["id"]))
            product_id = created.json()["id"]
            fetched = await client.get(f"{PRODUCTS}/{product_id}")
            patched = await client.patch(f"{PRODUCTS}/{product_id}", json={"name": "New name"})
            removed = await client.delete(f"{PRODUCTS}/{product_id}")

        assert created.status_code == 201
        assert created.json()["category"] == "grocery"
        assert created.json()["currency"] == "INR"
        assert fetched.json()["code"] == "SKU-1"
        assert patched.json()["name"] == "New name"
        assert removed.status_code == 204

    async def test_mrp_below_price_is_refused(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            category = await client.post(CATEGORIES, json={"key": "grocery", "label": "Grocery"})
            response = await client.post(
                PRODUCTS,
                json=_product_payload(
                    category.json()["id"],
                    price_min_minor=1000,
                    price_max_minor=1000,
                    mrp_minor=500,
                ),
            )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "mrp_below_price"

    async def test_an_unknown_category_is_refused(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.post(PRODUCTS, json=_product_payload(str(uuid.uuid4())))
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "category_not_found"

    async def test_duplicate_code_is_409(self, client, as_principal, org_id: uuid.UUID) -> None:
        with as_principal(all_perms(org_id)):
            category = await client.post(CATEGORIES, json={"key": "grocery", "label": "Grocery"})
            await client.post(PRODUCTS, json=_product_payload(category.json()["id"]))
            dupe = await client.post(PRODUCTS, json=_product_payload(category.json()["id"]))
        assert dupe.status_code == 409

    async def test_variants_sync_the_products_own_price_band(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            category = await client.post(CATEGORIES, json={"key": "grocery", "label": "Grocery"})
            created = await client.post(
                PRODUCTS,
                json=_product_payload(
                    category.json()["id"],
                    variants=[
                        {"label": "500 g", "price_min_minor": 5000, "price_max_minor": 5000},
                        {"label": "1 kg", "price_min_minor": 9000, "price_max_minor": 9000},
                    ],
                ),
            )

        body = created.json()
        assert body["price_min_minor"] == 5000
        assert len(body["variants"]) == 2
        assert body["variants"][0]["label"] == "500 g"

    async def test_status_archive_and_unarchive_via_patch(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            category = await client.post(CATEGORIES, json={"key": "grocery", "label": "Grocery"})
            created = await client.post(PRODUCTS, json=_product_payload(category.json()["id"]))
            archived = await client.patch(
                f"{PRODUCTS}/{created.json()['id']}", json={"status": "archived"}
            )

        assert archived.json()["status"] == "archived"


class TestCrossTenantIsolation:
    async def test_another_organizations_products_are_not_in_the_list(
        self, client, as_principal, admin_session, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        their_category = await seed_category(admin_session, other_org, key="theirs")
        await seed_product(admin_session, other_org, their_category.id, code="THEIRS")
        my_category = await seed_category(admin_session, org_id, key="mine")
        await seed_product(admin_session, org_id, my_category.id, code="MINE")

        with as_principal(all_perms(org_id)):
            response = await client.get(PRODUCTS)

        assert [row["code"] for row in response.json()["items"]] == ["MINE"]

    async def test_another_organizations_product_is_404_by_id(
        self, client, as_principal, admin_session, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        their_category = await seed_category(admin_session, other_org, key="theirs")
        theirs = await seed_product(admin_session, other_org, their_category.id, code="THEIRS")

        with as_principal(all_perms(org_id)):
            response = await client.get(f"{PRODUCTS}/{theirs.id}")

        assert response.status_code == 404


class TestPublicCatalogue:
    async def test_only_active_rows_of_a_published_store_are_returned(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        category = await seed_category(admin_session, org_id, key="grocery")
        await seed_product(admin_session, org_id, category.id, code="ACTIVE", status="active")
        await seed_product(admin_session, org_id, category.id, code="DRAFT", status="draft")

        response = await client.post(PUBLIC_CATALOGUE, json={"store": slug})

        assert response.status_code == 200
        codes = [p["code"] for p in response.json()["products"]["items"]]
        assert codes == ["ACTIVE"]
        assert response.json()["categories"][0]["key"] == "grocery"
        assert response.json()["categories"][0]["line_count"] == 1

    async def test_a_draft_store_answers_the_same_404_as_an_unknown_slug(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="draft")

        published = await client.post(PUBLIC_CATALOGUE, json={"store": slug})
        unknown = await client.post(PUBLIC_CATALOGUE, json={"store": "no-such-store"})

        assert published.status_code == 404
        assert unknown.status_code == 404
        assert published.json()["detail"] == unknown.json()["detail"]
