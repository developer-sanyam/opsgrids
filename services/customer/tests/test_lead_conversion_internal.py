"""The customer-side half of lead conversion — `POST /internal/v1/organizations/{org}/
leads:convert`, called by lead-service under a token addressed to customer by name.

Two properties worth protecting: it is guarded by a service token (a request without one is
refused, the same as every other internal route), and it is idempotent by the caller's own
ids — calling it twice with the same `company_id`/`contact_id` reuses the same rows rather
than creating a second company (ADR-0017's "same lead converted twice -> one company").
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from platform_core.auth import Scope
from platform_core.internal_auth import mint_service_token
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _token() -> str:
    return mint_service_token(settings(), audience="customer", caller="lead")


async def _seed_owner(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    ctx = ctx_for(org_id, "crm.book.owner.write", Scope.ORG)
    async with session.begin():
        owner = await services.create_owner(
            session, ctx, full_name="Priya Rep", email="priya.rep@example.com"
        )
    return owner.id


class TestLeadConversionInternal:
    async def test_without_a_service_token_it_is_refused(self, client, org_id) -> None:
        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            json={
                "company_id": str(uuid.uuid4()),
                "contact_id": str(uuid.uuid4()),
                "company_name": "Stellar Robotics",
                "owner_id": str(uuid.uuid4()),
                "contact_full_name": "Priya Sharma",
                "contact_email": None,
                "contact_raw_phone": None,
            },
        )
        assert resp.status_code == 401

    async def test_an_unknown_owner_is_not_found(self, client, org_id) -> None:
        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token()},
            json={
                "company_id": str(uuid.uuid4()),
                "contact_id": str(uuid.uuid4()),
                "company_name": "Stellar Robotics",
                "owner_id": str(uuid.uuid4()),
                "contact_full_name": "Priya Sharma",
                "contact_email": None,
                "contact_raw_phone": None,
            },
        )
        assert resp.status_code == 404

    async def test_creates_a_company_and_contact_on_the_first_call(
        self, client, org_id, session: AsyncSession
    ) -> None:
        owner_id = await _seed_owner(session, org_id)
        company_id, contact_id = uuid.uuid4(), uuid.uuid4()

        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token()},
            json={
                "company_id": str(company_id),
                "contact_id": str(contact_id),
                "company_name": "Stellar Robotics",
                "owner_id": str(owner_id),
                "contact_full_name": "Priya Sharma",
                "contact_email": "priya@stellar.example",
                "contact_raw_phone": "+919876543210",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["company_id"] == str(company_id)
        assert body["contact_id"] == str(contact_id)

        company = await selectors.get_company(session, org_id, company_id)
        assert company is not None
        assert company.name == "Stellar Robotics"
        contact = await selectors.get_contact(session, org_id, contact_id)
        assert contact is not None
        assert contact.email == "priya@stellar.example"
        assert contact.phone_enc is not None

    async def test_no_email_creates_a_company_with_no_contact(
        self, client, org_id, session: AsyncSession
    ) -> None:
        owner_id = await _seed_owner(session, org_id)
        company_id, contact_id = uuid.uuid4(), uuid.uuid4()

        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token()},
            json={
                "company_id": str(company_id),
                "contact_id": str(contact_id),
                "company_name": "Vertex Freight",
                "owner_id": str(owner_id),
                "contact_full_name": "Arjun Verma",
                "contact_email": None,
                "contact_raw_phone": None,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["contact_id"] is None

    async def test_calling_twice_with_the_same_ids_reuses_the_same_company(
        self, client, org_id, session: AsyncSession
    ) -> None:
        owner_id = await _seed_owner(session, org_id)
        company_id, contact_id = uuid.uuid4(), uuid.uuid4()
        payload = {
            "company_id": str(company_id),
            "contact_id": str(contact_id),
            "company_name": "Stellar Robotics",
            "owner_id": str(owner_id),
            "contact_full_name": "Priya Sharma",
            "contact_email": "priya@stellar.example",
            "contact_raw_phone": None,
        }

        first = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token()},
            json=payload,
        )
        second = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token()},
            json=payload,
        )
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()

        stmt = selectors.companies_stmt(ctx_for(org_id, "crm.book.company.read", Scope.ORG))
        rows = (await session.execute(stmt)).scalars().all()
        assert len([r for r in rows if r.id == company_id]) == 1
