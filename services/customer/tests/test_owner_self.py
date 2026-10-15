"""`PUT /customers/owners/me` -- a login with no employee link becomes a sales rep.

The org owner who signs up is a membership with no employee record, so `subject_ref` is null and
nothing could attribute an activity, a company or a deal to them: "No owner could be determined",
an import that demanded an Owner column, a Company form with no default. The roster row this
route ensures carries the caller's membership id as its own id (ADR-0038 R2.7 direction), so the
deal service can default an owner from the token alone, with no cross-schema read.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text

from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

URL = "/api/v1/customers/owners/me"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _body(email: str | None = None) -> dict[str, str]:
    return {"full_name": "Asha Verma", "email": email or f"asha-{uuid.uuid4().hex[:6]}@example.com"}


class TestEnsureMyRep:
    async def test_it_creates_a_rep_whose_id_is_the_membership_id(
        self, client, org_id, data
    ) -> None:
        membership_id = uuid.uuid4()
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"], membership_id=membership_id)
        with as_principal(principal):
            response = await client.put(URL, json=_body())
        assert response.status_code == 201, response.text
        assert response.json()["id"] == str(membership_id)
        assert response.json()["is_active"] is True

    async def test_it_is_idempotent(self, client, org_id, data) -> None:
        membership_id = uuid.uuid4()
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"], membership_id=membership_id)
        body = _body()
        with as_principal(principal):
            first = await client.put(URL, json=body)
            second = await client.put(URL, json=body)
        assert first.status_code == 201 and second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        with as_principal(principal_for(org_id, ["crm.book.company.read:ORG"])):
            roster = (await client.get("/api/v1/customers/owners")).json()
        assert [o["id"] for o in roster].count(str(membership_id)) == 1

    async def test_a_roster_row_already_made_for_the_same_email_is_returned_not_duplicated(
        self, client, org_id, data
    ) -> None:
        """The owner who used the old one-click claim already has a random-id row; asking again
        must hand that row back instead of a 409."""
        existing = data["rep_a"]
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"])
        with as_principal(principal):
            response = await client.put(URL, json=_body(existing.email))
        assert response.status_code == 200, response.text
        assert response.json()["id"] == str(existing.id)

    async def test_an_own_scoped_caller_is_refused(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["crm.book.owner.write:OWN"])
        with as_principal(principal):
            response = await client.put(URL, json=_body())
        assert response.status_code == 403

    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        assert (await client.put(URL, json=_body())).status_code == 401

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"], entitlements=[])
        with as_principal(principal):
            assert (await client.put(URL, json=_body())).status_code == 402

    async def test_unknown_fields_are_rejected(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"])
        with as_principal(principal):
            response = await client.put(URL, json={**_body(), "id": str(uuid.uuid4())})
        assert response.status_code == 422

    async def test_the_row_belongs_to_the_callers_organization(
        self, client, org_id, data, admin_session
    ) -> None:
        membership_id = uuid.uuid4()
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"], membership_id=membership_id)
        with as_principal(principal):
            assert (await client.put(URL, json=_body())).status_code == 201
        async with admin_session.begin():
            row = (
                await admin_session.execute(
                    text("select organization_id from customer.owners where id = :i"),
                    {"i": membership_id},
                )
            ).scalar_one()
        assert row == org_id
