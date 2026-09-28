"""`GET /internal/v1/organizations/{org}/contacts/{contact_id}` — the identity-bridge read
finance uses to resolve a CRM contact into its own billed-party record.

Properties worth protecting: guarded by a service token like every other internal route;
restricted to `service_caller == "finance"`; a token's `org` claim must name the path's
organization; a contact in another organization, or one that does not exist, reads as a plain
404 rather than disclosing anything about who holds the id; and the response never carries a
phone number — finance's own `Customer` has no field to put one in, and it is sealed PII with
no reason to leave this service for this purpose.
"""

from __future__ import annotations

import uuid

import pytest

from app import services
from app.config import settings
from platform_core.auth import Scope
from platform_core.internal_auth import mint_service_token
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _token(org_id: uuid.UUID, *, caller: str = "finance") -> str:
    return mint_service_token(
        settings(), audience="customer", caller=caller, claims={"org": str(org_id)}
    )


class TestContactSummaryInternal:
    async def test_without_a_service_token_it_is_refused(self, client, org_id, data) -> None:
        resp = await client.get(
            f"/internal/v1/organizations/{org_id}/contacts/{data['contact_a_id']}"
        )
        assert resp.status_code == 401

    async def test_a_caller_other_than_finance_is_refused(self, client, org_id, data) -> None:
        resp = await client.get(
            f"/internal/v1/organizations/{org_id}/contacts/{data['contact_a_id']}",
            headers={"X-Internal-Token": _token(org_id, caller="lead")},
        )
        assert resp.status_code == 403

    async def test_a_token_addressed_to_another_organization_is_refused(
        self, client, org_id, data
    ) -> None:
        other_org = uuid.uuid4()
        resp = await client.get(
            f"/internal/v1/organizations/{org_id}/contacts/{data['contact_a_id']}",
            headers={"X-Internal-Token": _token(other_org)},
        )
        assert resp.status_code == 403

    async def test_an_unknown_contact_is_not_found(self, client, org_id) -> None:
        resp = await client.get(
            f"/internal/v1/organizations/{org_id}/contacts/{uuid.uuid4()}",
            headers={"X-Internal-Token": _token(org_id)},
        )
        assert resp.status_code == 404

    async def test_another_organizations_contact_is_not_found_here(
        self, client, org_id, data
    ) -> None:
        """The id is real, but not in this organization's book — a 404, not a 403, so
        confirming the id exists elsewhere is not something this route discloses."""
        other_org = uuid.uuid4()
        resp = await client.get(
            f"/internal/v1/organizations/{other_org}/contacts/{data['contact_a_id']}",
            headers={"X-Internal-Token": _token(other_org)},
        )
        assert resp.status_code == 404

    async def test_the_happy_path_returns_the_contacts_directory_identity(
        self, client, org_id, data
    ) -> None:
        resp = await client.get(
            f"/internal/v1/organizations/{org_id}/contacts/{data['contact_a_id']}",
            headers={"X-Internal-Token": _token(org_id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "contact_id": str(data["contact_a_id"]),
            "full_name": data["contact_a"].full_name,
            "email": data["contact_a"].email,
            "company_id": str(data["company_a_id"]),
            "company_name": data["company_a"].name,
        }
        # Never a phone number — finance's own Customer has nowhere to put one, and it is
        # sealed PII with no reason to leave this service for this purpose.
        assert "phone" not in body

    async def test_a_soft_deleted_contact_is_not_found(self, client, org_id, data, session) -> None:
        write = ctx_for(org_id, "crm.book.contact.write", Scope.ORG)
        async with session.begin():
            await services.delete_contact(session, write, data["contact_a"])

        resp = await client.get(
            f"/internal/v1/organizations/{org_id}/contacts/{data['contact_a_id']}",
            headers={"X-Internal-Token": _token(org_id)},
        )
        assert resp.status_code == 404
