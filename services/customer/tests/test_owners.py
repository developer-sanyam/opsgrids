"""Editing and deactivating a rep — `PATCH /customers/owners/{id}`.

Deactivation refuses while the rep still owns a live company or contact — in `data`, both
`rep_a` and `rep_b` own one of each, so the deactivation tests seed a fresh, unencumbered rep
of their own through the real write path rather than reusing either.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.main import app
from platform_core.auth import Principal, Scope, get_principal
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


async def _unencumbered_owner(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    """A rep with no company or contact assigned to them — the case deactivation allows."""
    ctx = ctx_for(org_id, "crm.book.owner.write", Scope.ORG)
    async with session.begin():
        owner = await services.create_owner(
            session, ctx, full_name="Fresh Rep", email=f"fresh-{uuid.uuid4()}@example.com"
        )
    return owner.id


class TestOwnerAuthz:
    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        response = await client.patch(
            f"/api/v1/customers/owners/{data['rep_b'].id}", json={"role": "Growth"}
        )
        assert response.status_code == 401

    async def test_an_own_scoped_write_cannot_edit_the_directory(
        self, client, org_id, data
    ) -> None:
        """`crm.book.owner.write` has a floor of ORG (see `app/api/routes.py`) — an OWN grant
        is refused outright, not silently narrowed."""
        rep_b_id = data["rep_b"].id
        principal = principal_for(
            org_id, ["crm.book.owner.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/customers/owners/{rep_b_id}", json={"role": "Growth"}
            )
        assert response.status_code == 403

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        rep_b_id = data["rep_b"].id
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"], entitlements=[])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/customers/owners/{rep_b_id}", json={"role": "Growth"}
            )
        assert response.status_code == 402


class TestOwnerEdit:
    async def test_an_org_admin_can_edit_a_reps_role(self, client, org_id, data) -> None:
        rep_b_id = data["rep_b"].id
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/customers/owners/{rep_b_id}", json={"role": "Growth Lead"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["role"] == "Growth Lead"
        assert body["is_active"] is True

    async def test_renaming_onto_another_reps_email_conflicts(self, client, org_id, data) -> None:
        rep_a_email = data["rep_a"].email
        rep_b_id = data["rep_b"].id
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/customers/owners/{rep_b_id}", json={"email": rep_a_email}
            )
        assert response.status_code == 409

    async def test_unknown_fields_are_rejected(self, client, org_id, data) -> None:
        rep_b_id = data["rep_b"].id
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/customers/owners/{rep_b_id}", json={"nickname": "nope"}
            )
        assert response.status_code == 422


class TestOwnerDeactivation:
    async def test_deactivating_a_rep_with_no_open_records_succeeds(
        self, client, org_id, data, session: AsyncSession
    ) -> None:
        owner_id = await _unencumbered_owner(session, org_id)
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/customers/owners/{owner_id}", json={"is_active": False}
            )
        assert response.status_code == 200
        assert response.json()["is_active"] is False

    async def test_deactivating_a_rep_who_still_owns_a_company_refuses(
        self, client, org_id, data
    ) -> None:
        """`rep_a` owns `company_a` and `contact_a` in the fixture."""
        rep_a_id = data["rep_a"].id
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/customers/owners/{rep_a_id}", json={"is_active": False}
            )
        assert response.status_code == 409

    async def test_deactivating_twice_is_a_no_op_not_an_error(
        self, client, org_id, data, session: AsyncSession
    ) -> None:
        owner_id = await _unencumbered_owner(session, org_id)
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"])
        with as_principal(principal):
            first = await client.patch(
                f"/api/v1/customers/owners/{owner_id}", json={"is_active": False}
            )
            second = await client.patch(
                f"/api/v1/customers/owners/{owner_id}", json={"is_active": False}
            )
        assert first.status_code == second.status_code == 200

    async def test_a_deactivated_owner_cannot_be_assigned_a_new_company(
        self, client, org_id, data, session: AsyncSession
    ) -> None:
        owner_id = await _unencumbered_owner(session, org_id)
        principal = principal_for(
            org_id, ["crm.book.owner.write:ORG", "crm.book.company.write:ORG"]
        )
        with as_principal(principal):
            await client.patch(f"/api/v1/customers/owners/{owner_id}", json={"is_active": False})
            response = await client.post(
                "/api/v1/customers",
                json={"name": "New Prospect Co", "owner_id": str(owner_id)},
            )
        assert response.status_code == 404

    async def test_reactivating_a_deactivated_owner(
        self, client, org_id, data, session: AsyncSession
    ) -> None:
        owner_id = await _unencumbered_owner(session, org_id)
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"])
        with as_principal(principal):
            await client.patch(f"/api/v1/customers/owners/{owner_id}", json={"is_active": False})
            response = await client.patch(
                f"/api/v1/customers/owners/{owner_id}", json={"is_active": True}
            )
        assert response.status_code == 200
        assert response.json()["is_active"] is True

    async def test_an_unknown_owner_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["crm.book.owner.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/customers/owners/{uuid.uuid4()}", json={"role": "x"}
            )
        assert response.status_code == 404
