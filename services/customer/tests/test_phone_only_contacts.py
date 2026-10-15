"""Phone-only contacts (ADR-0038 D-14): `contacts.email` is nullable.

A new Contact used to demand an email while a Lead did not, and a lead carrying only a phone
converted into a company with no contact at all. Company is the party, the contact is the person,
and a person is reachable by phone alone.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.main import app
from platform_core.auth import Principal, Scope, get_principal
from platform_core.internal_auth import mint_service_token
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

CONTACT_WRITE = "crm.book.contact.write"
CONTACT_READ = "crm.book.contact.read"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _payload(data: dict, **overrides: object) -> dict:
    payload: dict = {
        "full_name": "Ravi Kapoor",
        "company_id": str(data["company_a_id"]),
        "owner_id": str(data["subject_a"]),
        "raw_phone": "+919000000012",
    }
    payload.update(overrides)
    return payload


class TestCreatingAPhoneOnlyContact:
    async def test_a_contact_needs_no_email(self, client, session, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG"])):
            response = await client.post("/api/v1/contacts", json=_payload(data))
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["email"] is None
        assert body["phone"] != ""  # the mask, never the raw number
        assert "9000000012" not in body["phone"].replace("*", "").replace("x", "")[:-4]

        stored = await selectors.get_contact(session, org_id, uuid.UUID(body["id"]))
        assert stored is not None and stored.email is None and stored.phone_enc is not None

    async def test_two_phone_only_contacts_coexist(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG"])):
            first = await client.post("/api/v1/contacts", json=_payload(data))
            second = await client.post(
                "/api/v1/contacts",
                json=_payload(data, full_name="Meera Iyer", raw_phone="+919000000013"),
            )
        assert first.status_code == 201 and second.status_code == 201

    async def test_two_contacts_still_cannot_share_a_real_address(
        self, client, org_id, data
    ) -> None:
        body = _payload(data, email="shared@northwind.co")
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG"])):
            first = await client.post("/api/v1/contacts", json=body)
            second = await client.post("/api/v1/contacts", json={**body, "full_name": "Other"})
        assert first.status_code == 201 and second.status_code == 409

    async def test_a_blank_email_is_the_same_as_none(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG"])):
            response = await client.post("/api/v1/contacts", json=_payload(data, email=None))
        assert response.status_code == 201 and response.json()["email"] is None

    async def test_a_malformed_email_is_still_refused(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG"])):
            response = await client.post("/api/v1/contacts", json=_payload(data, email="nope"))
        assert response.status_code == 422

    async def test_an_email_can_be_added_later_and_the_list_reads_back_null_before(
        self, client, org_id, data
    ) -> None:
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG", f"{CONTACT_READ}:ORG"])):
            made = await client.post("/api/v1/contacts", json=_payload(data))
            contact_id = made.json()["id"]
            listed = await client.get("/api/v1/contacts")
            patched = await client.patch(
                f"/api/v1/contacts/{contact_id}", json={"email": "ravi@northwind.co"}
            )
        assert any(c["id"] == contact_id and c["email"] is None for c in listed.json()["items"])
        assert patched.status_code == 200 and patched.json()["email"] == "ravi@northwind.co"

    async def test_a_reader_cannot_add_one(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{CONTACT_READ}:ORG"])):
            response = await client.post("/api/v1/contacts", json=_payload(data))
        assert response.status_code == 403

    async def test_the_search_survives_a_contact_with_no_email(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, [f"{CONTACT_WRITE}:ORG", f"{CONTACT_READ}:ORG"])):
            await client.post("/api/v1/contacts", json=_payload(data))
            found = await client.get("/api/v1/contacts?q=kapoor")
        assert found.status_code == 200 and found.json()["total"] >= 1


def _token(org_id: uuid.UUID) -> str:
    return mint_service_token(
        settings(), audience="customer", caller="lead", claims={"org": str(org_id)}
    )


async def _seed_owner(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    ctx = ctx_for(org_id, "crm.book.owner.write", Scope.ORG)
    async with session.begin():
        owner = await services.create_owner(
            session, ctx, full_name="Priya Rep", email=f"rep-{uuid.uuid4().hex[:6]}@example.com"
        )
    return owner.id


class TestALeadWithOnlyAPhoneConverts:
    async def test_it_creates_a_phone_only_contact(
        self, client, org_id, session: AsyncSession
    ) -> None:
        owner_id = await _seed_owner(session, org_id)
        contact_id = uuid.uuid4()
        response = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json={
                "company_id": str(uuid.uuid4()),
                "contact_id": str(contact_id),
                "company_name": "Vertex Freight",
                "owner_id": str(owner_id),
                "contact_full_name": "Arjun Verma",
                "contact_email": None,
                "contact_raw_phone": "+919000000021",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["contact_id"] == str(contact_id)
        contact = await selectors.get_contact(session, org_id, contact_id)
        assert contact is not None and contact.email is None
        assert contact.phone_enc is not None and contact.phone_index is not None

    async def test_replaying_the_conversion_reuses_the_same_phone_only_contact(
        self, client, org_id, session: AsyncSession
    ) -> None:
        owner_id = await _seed_owner(session, org_id)
        body = {
            "company_id": str(uuid.uuid4()),
            "contact_id": str(uuid.uuid4()),
            "company_name": "Vertex Freight",
            "owner_id": str(owner_id),
            "contact_full_name": "Arjun Verma",
            "contact_email": None,
            "contact_raw_phone": "+919000000021",
        }
        headers = {"X-Internal-Token": _token(org_id)}
        url = f"/internal/v1/organizations/{org_id}/leads:convert"
        first = await client.post(url, headers=headers, json=body)
        second = await client.post(url, headers=headers, json=body)
        assert first.json()["contact_id"] == second.json()["contact_id"] == body["contact_id"]

    async def test_a_second_lead_with_the_same_number_reuses_the_contact(
        self, client, org_id, session: AsyncSession
    ) -> None:
        owner_id = await _seed_owner(session, org_id)
        headers = {"X-Internal-Token": _token(org_id)}
        url = f"/internal/v1/organizations/{org_id}/leads:convert"

        def body() -> dict:
            return {
                "company_id": str(uuid.uuid4()),
                "contact_id": str(uuid.uuid4()),
                "company_name": "Vertex Freight",
                "owner_id": str(owner_id),
                "contact_full_name": "Arjun Verma",
                "contact_email": None,
                "contact_raw_phone": "+919000000021",
            }

        first = await client.post(url, headers=headers, json=body())
        second = await client.post(url, headers=headers, json=body())
        assert first.json()["contact_id"] == second.json()["contact_id"]

    async def test_no_email_and_no_phone_still_makes_a_company_with_no_contact(
        self, client, org_id, session: AsyncSession
    ) -> None:
        owner_id = await _seed_owner(session, org_id)
        response = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json={
                "company_id": str(uuid.uuid4()),
                "contact_id": str(uuid.uuid4()),
                "company_name": "Bare Co",
                "owner_id": str(owner_id),
                "contact_full_name": "Nobody",
                "contact_email": None,
                "contact_raw_phone": None,
            },
        )
        assert response.status_code == 200 and response.json()["contact_id"] is None
