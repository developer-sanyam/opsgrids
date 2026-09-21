"""Enquiries: the public submission and the merchant's masked list/detail/status change (gap 6).

Buyer contact fields are PII — sealed at rest, masked by default, unmasked only through the
audited, stepped-up `POST .../{id}:reveal`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import AuditLog
from tests.helpers import all_perms, principal_for, seed_category, seed_product, seed_store

pytestmark = pytest.mark.integration

ENQUIRIES = "/api/v1/storefront/enquiries"
PUBLIC_ENQUIRY = "/api/v1/storefront/public/enquiry"


def _payload(store: str, **overrides: object) -> dict[str, object]:
    return {
        "store": store,
        "name": "Priya Shah",
        "company": "Shah Traders",
        "email": "priya@shahtraders.example",
        "phone": "9876543210",
        "requirement": "Need a quote for 500 bags of rice.",
        "quantity": "500 bags",
        "lines": [],
        **overrides,
    }


class TestPublicSubmission:
    async def test_an_enquiry_against_a_published_store_is_accepted(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")

        response = await client.post(PUBLIC_ENQUIRY, json=_payload(slug))

        assert response.status_code == 201
        assert "id" in response.json()

    async def test_an_unpublished_store_is_404(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="draft")

        response = await client.post(PUBLIC_ENQUIRY, json=_payload(slug))

        assert response.status_code == 404

    async def test_a_missing_required_field_is_422(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        payload = _payload(slug)
        del payload["email"]

        response = await client.post(PUBLIC_ENQUIRY, json=payload)

        assert response.status_code == 422

    async def test_a_fabricated_product_id_is_rejected(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """A `product_id` that is not part of this published store's active catalogue at all
        must not be silently accepted as a real line."""
        await seed_store(admin_session, org_id, slug, status="published")

        response = await client.post(
            PUBLIC_ENQUIRY,
            json=_payload(
                slug,
                lines=[{"product_id": str(uuid.uuid4()), "name": "Anything", "quantity": 5}],
            ),
        )

        assert response.status_code == 422

    async def test_a_product_from_another_store_is_rejected_the_same_way(
        self,
        client,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        slug: str,
    ) -> None:
        """A real product, just not *this* store's — refused identically to an unknown id, so
        the response never discloses that the id belongs to someone else's catalogue."""
        await seed_store(admin_session, org_id, slug, status="published")
        other_category = await seed_category(admin_session, other_org)
        other_product = await seed_product(admin_session, other_org, other_category.id)

        fabricated = await client.post(
            PUBLIC_ENQUIRY,
            json=_payload(
                slug,
                lines=[{"product_id": str(uuid.uuid4()), "name": "Anything", "quantity": 5}],
            ),
        )
        cross_store = await client.post(
            PUBLIC_ENQUIRY,
            json=_payload(
                slug,
                lines=[{"product_id": str(other_product.id), "name": "Anything", "quantity": 5}],
            ),
        )

        assert fabricated.status_code == cross_store.status_code == 422
        assert fabricated.json()["errors"] == cross_store.json()["errors"]

    async def test_a_real_products_name_is_taken_from_the_catalogue_not_the_body(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        category = await seed_category(admin_session, org_id)
        product = await seed_product(admin_session, org_id, category.id, name="Basmati Rice 25kg")

        response = await client.post(
            PUBLIC_ENQUIRY,
            json=_payload(
                slug,
                lines=[
                    {
                        "product_id": str(product.id),
                        "name": "Fabricated Item Name",
                        "quantity": 5,
                    }
                ],
            ),
        )
        assert response.status_code == 201

        with as_principal(all_perms(org_id)):
            listed = await client.get(ENQUIRIES)
        line = listed.json()["items"][0]["lines"][0]
        assert line["name"] == "Basmati Rice 25kg"
        assert line["product_id"] == str(product.id)
        assert line["quantity"] == 5

    async def test_a_quantity_over_the_bound_is_422(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        category = await seed_category(admin_session, org_id)
        product = await seed_product(admin_session, org_id, category.id)

        response = await client.post(
            PUBLIC_ENQUIRY,
            json=_payload(
                slug,
                lines=[
                    {"product_id": str(product.id), "name": "Basmati Rice 25kg", "quantity": 2**31}
                ],
            ),
        )

        assert response.status_code == 422

    async def test_a_malformed_email_is_422(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")

        response = await client.post(PUBLIC_ENQUIRY, json=_payload(slug, email="not-an-email"))

        assert response.status_code == 422

    async def test_it_appears_in_the_merchants_list_with_contact_fields_masked(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        await client.post(PUBLIC_ENQUIRY, json=_payload(slug))

        with as_principal(all_perms(org_id)):
            listed = await client.get(ENQUIRIES)

        assert listed.json()["total"] == 1
        row = listed.json()["items"][0]
        assert "priya" not in row["email_masked"].lower()
        assert "9876543210" not in row["phone_masked"]
        assert row["email_masked"].startswith("p")
        assert row["phone_masked"].endswith("3210")


class TestMerchantStatusChange:
    async def test_new_to_contacted_to_won(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        await client.post(PUBLIC_ENQUIRY, json=_payload(slug))

        with as_principal(all_perms(org_id)):
            listed = await client.get(ENQUIRIES)
            enquiry_id = listed.json()["items"][0]["id"]
            contacted = await client.patch(
                f"{ENQUIRIES}/{enquiry_id}", json={"status": "contacted"}
            )
            won = await client.patch(f"{ENQUIRIES}/{enquiry_id}", json={"status": "won"})

        assert contacted.json()["status"] == "contacted"
        assert won.json()["status"] == "won"

    async def test_skipping_straight_to_won_is_409(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        await client.post(PUBLIC_ENQUIRY, json=_payload(slug))

        with as_principal(all_perms(org_id)):
            listed = await client.get(ENQUIRIES)
            enquiry_id = listed.json()["items"][0]["id"]
            illegal = await client.patch(f"{ENQUIRIES}/{enquiry_id}", json={"status": "won"})

        assert illegal.status_code == 409
        assert illegal.json()["errors"][0]["code"] == "enquiry_transition_invalid"

    async def test_a_terminal_status_cannot_move_again(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        await client.post(PUBLIC_ENQUIRY, json=_payload(slug))

        with as_principal(all_perms(org_id)):
            listed = await client.get(ENQUIRIES)
            enquiry_id = listed.json()["items"][0]["id"]
            await client.patch(f"{ENQUIRIES}/{enquiry_id}", json={"status": "contacted"})
            await client.patch(f"{ENQUIRIES}/{enquiry_id}", json={"status": "lost"})
            reopened = await client.patch(f"{ENQUIRIES}/{enquiry_id}", json={"status": "contacted"})

        assert reopened.status_code == 409


class TestUnmask:
    async def test_reveal_returns_the_real_contact_and_writes_an_audit_row(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        await client.post(PUBLIC_ENQUIRY, json=_payload(slug))

        with as_principal(all_perms(org_id)):
            listed = await client.get(ENQUIRIES)
            enquiry_id = listed.json()["items"][0]["id"]
            revealed = await client.post(f"{ENQUIRIES}/{enquiry_id}:reveal")

        assert revealed.status_code == 200
        assert revealed.json()["email"] == "priya@shahtraders.example"
        assert revealed.json()["phone"] == "9876543210"

        rows = (
            await admin_session.execute(
                select(AuditLog.action).where(AuditLog.organization_id == org_id)
            )
        ).scalars()
        assert "entity.sensitive_read" in list(rows)

    async def test_reveal_without_the_step_up_is_403(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        await client.post(PUBLIC_ENQUIRY, json=_payload(slug))

        no_otp = principal_for(org_id, ["storefront.sales.enquiry.unmask:ORG"])
        with as_principal(all_perms(org_id)):
            listed = await client.get(ENQUIRIES)
        enquiry_id = listed.json()["items"][0]["id"]

        with as_principal(no_otp):
            response = await client.post(f"{ENQUIRIES}/{enquiry_id}:reveal")

        assert response.status_code == 403


class TestCrossTenantIsolation:
    async def test_another_organizations_enquiries_are_invisible(
        self, client, as_principal, admin_session, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        other_slug = f"t{uuid.uuid4().hex[:16]}"
        await seed_store(admin_session, other_org, other_slug, status="published")
        await client.post(PUBLIC_ENQUIRY, json=_payload(other_slug))

        with as_principal(all_perms(org_id)):
            response = await client.get(ENQUIRIES)

        assert response.json()["items"] == []
