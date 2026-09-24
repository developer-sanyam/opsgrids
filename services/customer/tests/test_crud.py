"""Create, update and delete for owners, companies and contacts — the write half of the
account book, over the real HTTP surface.

Every new endpoint carries: a happy path, a validation failure (422), a duplicate/conflict
(409) where one applies, a not-found, a negative-authz check (missing permission -> 403),
and cross-tenant isolation (another organization's row -> 404, never leaked).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest

from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


#: An org-scoped caller holding every new `crm.book.*` write permission this slice adds —
#: the "admin" shape `CRM_BOOK_ADMIN` grants.
BOOK_ADMIN_PERMS = [
    "crm.book.company.read:ORG",
    "crm.book.company.write:ORG",
    "crm.book.company.delete:ORG",
    "crm.book.contact.read:ORG",
    "crm.book.contact.write:ORG",
    "crm.book.contact.delete:ORG",
    "crm.book.owner.write:ORG",
]


def _admin(org_id: uuid.UUID, subject_ref: uuid.UUID) -> Principal:
    return principal_for(org_id, BOOK_ADMIN_PERMS, subject_ref=subject_ref)


# --------------------------------------------------------------------- owners


class TestCreateOwner:
    async def test_happy_path_and_it_resolves_in_the_directory(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/customers/owners",
                json={
                    "full_name": "Priya Nair",
                    "email": "priya.nair@opsgrids.example.com",
                    "role": "SDR",
                },
            )
            listed = await client.get("/api/v1/customers/owners")
        assert response.status_code == 201
        body = response.json()
        assert body["full_name"] == "Priya Nair"
        assert body["role"] == "SDR"
        assert uuid.UUID(body["id"])
        assert "Priya Nair" in {o["full_name"] for o in listed.json()}

    async def test_a_duplicate_email_is_refused(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/customers/owners",
                json={
                    "full_name": "Somebody Else",
                    "email": "sofia.marchetti@opsgrids.example.com",
                },
            )
        assert response.status_code == 409

    async def test_an_unknown_field_is_refused(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/customers/owners",
                json={"full_name": "X", "email": "x@example.com", "member_id": str(uuid.uuid4())},
            )
        assert response.status_code == 422

    async def test_a_missing_field_is_a_422_with_field_detail(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post("/api/v1/customers/owners", json={"full_name": "X"})
        assert response.status_code == 422
        assert any(err["field"] == "email" for err in response.json()["errors"])

    async def test_without_the_permission_is_forbidden(self, client, org_id, data) -> None:
        # A sales rep does not get `owner.write` — the directory write is ORG-admin only.
        principal = principal_for(
            org_id,
            ["crm.book.company.read:OWN", "crm.book.company.write:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/customers/owners",
                json={"full_name": "X", "email": "sales-rep@example.com"},
            )
        assert response.status_code == 403


# --------------------------------------------------------------------- companies


class TestCreateCompany:
    async def test_happy_path(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/customers",
                json={
                    "name": "Harbor & Co",
                    "owner_id": str(data["subject_a"]),
                    "city": "Chennai",
                    "status": "prospect",
                    "annual_revenue_minor": 500_000_00,
                    "currency": "inr",
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Harbor & Co"
        assert body["currency"] == "INR"

    async def test_an_unknown_owner_is_not_found(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/customers",
                json={"name": "Ghost Co", "owner_id": str(uuid.uuid4())},
            )
        assert response.status_code == 404

    async def test_a_duplicate_name_is_refused(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/customers",
                json={"name": "Northwind Logistics", "owner_id": str(data["subject_a"])},
            )
        assert response.status_code == 409

    async def test_a_case_insensitive_duplicate_name_is_also_refused(
        self, client, org_id, data
    ) -> None:
        """`uq_companies_org_name` stayed case-sensitive (M2: a unique index on `lower(name)`
        would fail `alembic upgrade` on an organization already holding two case-variant
        names) — this is `create_company`'s own `pg_advisory_xact_lock`-guarded check doing
        the case-insensitive refusal the database constraint no longer can."""
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/customers",
                json={"name": "NORTHWIND LOGISTICS", "owner_id": str(data["subject_a"])},
            )
        assert response.status_code == 409

    async def test_negative_revenue_is_a_422(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/customers",
                json={
                    "name": "Bad Money Co",
                    "owner_id": str(data["subject_a"]),
                    "annual_revenue_minor": -1,
                },
            )
        assert response.status_code == 422

    async def test_an_own_scoped_rep_can_only_assign_to_themselves(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["crm.book.company.write:OWN", "crm.book.company.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            refused = await client.post(
                "/api/v1/customers",
                json={"name": "Someone Elses Co", "owner_id": str(data["subject_b"])},
            )
            allowed = await client.post(
                "/api/v1/customers",
                json={"name": "My Own Co", "owner_id": str(data["subject_a"])},
            )
        assert refused.status_code == 403
        assert allowed.status_code == 201

    async def test_without_the_permission_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.book.company.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/customers",
                json={"name": "No Perms Co", "owner_id": str(data["subject_a"])},
            )
        assert response.status_code == 403

    async def test_created_in_one_org_is_invisible_to_another(self, client, org_id, data) -> None:
        other_org = uuid.uuid4()
        with as_principal(_admin(other_org, uuid.uuid4())):
            response = await client.get(f"/api/v1/customers/{data['company_a_id']}")
        assert response.status_code == 404


class TestUpdateCompany:
    async def test_happy_path(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/customers/{data['company_a_id']}",
                json={"city": "Pune", "status": "customer"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["city"] == "Pune"
        assert body["status"] == "customer"
        # untouched fields survive
        assert body["name"] == "Northwind Logistics"

    async def test_renaming_onto_an_existing_name_is_refused(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/customers/{data['company_a_id']}",
                json={"name": "Meridian Steelworks"},
            )
        assert response.status_code == 409

    async def test_reassigning_to_an_unknown_owner_is_not_found(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/customers/{data['company_a_id']}",
                json={"owner_id": str(uuid.uuid4())},
            )
        assert response.status_code == 404

    async def test_an_unknown_company_is_not_found(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/customers/{uuid.uuid4()}", json={"city": "Nowhere"}
            )
        assert response.status_code == 404

    async def test_an_own_scoped_rep_cannot_edit_another_reps_company(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["crm.book.company.write:OWN", "crm.book.company.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/customers/{data['company_b_id']}", json={"city": "Nope"}
            )
        assert response.status_code == 403

    async def test_without_the_permission_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.book.company.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/customers/{data['company_a_id']}", json={"city": "Nope"}
            )
        assert response.status_code == 403

    async def test_an_unknown_field_is_refused(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/customers/{data['company_a_id']}", json={"revenue": 5}
            )
        assert response.status_code == 422


class TestDeleteCompany:
    async def test_refuses_while_it_still_has_contacts(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.delete(f"/api/v1/customers/{data['company_a_id']}")
        assert response.status_code == 409

    async def test_happy_path_then_the_name_is_reusable(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            # delete the contact first so the company has none left
            await client.delete(f"/api/v1/contacts/{data['contact_a_id']}")
            deleted = await client.delete(f"/api/v1/customers/{data['company_a_id']}")
            gone = await client.get(f"/api/v1/customers/{data['company_a_id']}")
            recreated = await client.post(
                "/api/v1/customers",
                json={"name": "Northwind Logistics", "owner_id": str(data["subject_a"])},
            )
        assert deleted.status_code == 204
        assert gone.status_code == 404
        assert recreated.status_code == 201

    async def test_an_unknown_company_is_not_found(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.delete(f"/api/v1/customers/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_without_the_permission_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.book.company.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.delete(f"/api/v1/customers/{data['company_a_id']}")
        assert response.status_code == 403

    async def test_cross_tenant_delete_is_refused_as_not_found(self, client, org_id, data) -> None:
        other_org = uuid.uuid4()
        with as_principal(_admin(other_org, uuid.uuid4())):
            response = await client.delete(f"/api/v1/customers/{data['company_a_id']}")
        assert response.status_code == 404
        # and the row still exists for the real tenant
        with as_principal(_admin(org_id, data["subject_a"])):
            still_there = await client.get(f"/api/v1/customers/{data['company_a_id']}")
        assert still_there.status_code == 200


# --------------------------------------------------------------------- contacts


class TestCreateContact:
    async def test_happy_path_with_a_phone(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/contacts",
                json={
                    "full_name": "New Contact",
                    "email": "new.contact@northwind.co",
                    "company_id": str(data["company_a_id"]),
                    "owner_id": str(data["subject_a"]),
                    "raw_phone": "+91 98765 43210",
                },
            )
        assert response.status_code == 201
        body = response.json()
        # only the mask travels; the raw number never appears on the wire
        assert body["phone"] != "+91 98765 43210"
        assert "•" in body["phone"]

    async def test_happy_path_with_no_phone(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/contacts",
                json={
                    "full_name": "No Phone Yet",
                    "email": "no.phone@northwind.co",
                    "company_id": str(data["company_a_id"]),
                    "owner_id": str(data["subject_a"]),
                },
            )
        assert response.status_code == 201
        assert response.json()["phone"] == ""

    async def test_a_phone_field_is_rejected_a_mask_can_never_be_written(
        self, client, org_id, data
    ) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/contacts",
                json={
                    "full_name": "Sneaky",
                    "email": "sneaky@northwind.co",
                    "company_id": str(data["company_a_id"]),
                    "owner_id": str(data["subject_a"]),
                    "phone": "+91 98••• ••210",
                },
            )
        assert response.status_code == 422

    async def test_an_unqualified_phone_is_a_422(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/contacts",
                json={
                    "full_name": "Bad Phone",
                    "email": "bad.phone@northwind.co",
                    "company_id": str(data["company_a_id"]),
                    "owner_id": str(data["subject_a"]),
                    "raw_phone": "9876543210",
                },
            )
        assert response.status_code == 422

    async def test_a_duplicate_email_is_refused(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/contacts",
                json={
                    "full_name": "Dup",
                    "email": "rajesh.khanna@northwind.co",
                    "company_id": str(data["company_a_id"]),
                    "owner_id": str(data["subject_a"]),
                },
            )
        assert response.status_code == 409

    async def test_an_unknown_company_is_not_found(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/contacts",
                json={
                    "full_name": "Ghost",
                    "email": "ghost@example.com",
                    "company_id": str(uuid.uuid4()),
                    "owner_id": str(data["subject_a"]),
                },
            )
        assert response.status_code == 404

    async def test_without_the_permission_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.book.contact.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/contacts",
                json={
                    "full_name": "X",
                    "email": "x@example.com",
                    "company_id": str(data["company_a_id"]),
                    "owner_id": str(data["subject_a"]),
                },
            )
        assert response.status_code == 403


class TestUpdateContact:
    async def test_happy_path_never_touches_the_phone(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            before = await client.get(f"/api/v1/contacts/{data['contact_a_id']}")
            response = await client.patch(
                f"/api/v1/contacts/{data['contact_a_id']}", json={"title": "SVP Operations"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "SVP Operations"
        assert body["phone"] == before.json()["phone"]

    async def test_extra_or_phone_fields_are_rejected(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/contacts/{data['contact_a_id']}", json={"phone": "+91 00000 00000"}
            )
        assert response.status_code == 422

    async def test_a_duplicate_email_is_refused(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/contacts/{data['contact_a_id']}",
                json={"email": "suresh.iyer@meridiansteel.com"},
            )
        assert response.status_code == 409

    async def test_moving_to_an_unknown_company_is_not_found(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/contacts/{data['contact_a_id']}", json={"company_id": str(uuid.uuid4())}
            )
        assert response.status_code == 404

    async def test_an_own_scoped_rep_cannot_edit_another_reps_contact(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["crm.book.contact.write:OWN", "crm.book.contact.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/contacts/{data['contact_b_id']}", json={"title": "Nope"}
            )
        assert response.status_code == 403

    async def test_reassigning_to_an_unknown_owner_is_not_found(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/contacts/{data['contact_a_id']}",
                json={"owner_id": str(uuid.uuid4())},
            )
        assert response.status_code == 404

    async def test_an_own_scoped_rep_cannot_reassign_a_contact_to_another_rep(
        self, client, org_id, data
    ) -> None:
        """M2: `owner_id` is the OWN-scope column — an own-scoped rep patching their own
        contact must not be able to hand it to another rep's book, mirroring
        `TestUpdateCompany`'s equivalent check."""
        principal = principal_for(
            org_id,
            ["crm.book.contact.write:OWN", "crm.book.contact.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/contacts/{data['contact_a_id']}",
                json={"owner_id": str(data["subject_b"])},
            )
        assert response.status_code == 403


class TestDeleteContact:
    async def test_happy_path(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.delete(f"/api/v1/contacts/{data['contact_a_id']}")
            gone = await client.get(f"/api/v1/contacts/{data['contact_a_id']}")
        assert response.status_code == 204
        assert gone.status_code == 404

    async def test_an_unknown_contact_is_not_found(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.delete(f"/api/v1/contacts/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_without_the_permission_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.book.contact.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.delete(f"/api/v1/contacts/{data['contact_a_id']}")
        assert response.status_code == 403

    async def test_cross_tenant_delete_is_refused_as_not_found(self, client, org_id, data) -> None:
        other_org = uuid.uuid4()
        with as_principal(_admin(other_org, uuid.uuid4())):
            response = await client.delete(f"/api/v1/contacts/{data['contact_a_id']}")
        assert response.status_code == 404


class TestContactsFilterByCompany:
    async def test_company_id_narrows_the_list(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.get(f"/api/v1/contacts?company_id={data['company_b_id']}")
        names = {c["full_name"] for c in response.json()["items"]}
        assert names == {"Suresh Iyer"}
