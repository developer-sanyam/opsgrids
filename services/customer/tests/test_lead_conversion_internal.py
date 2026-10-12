"""The customer-side half of lead conversion — `POST /internal/v1/organizations/{org}/
leads:convert`, called by lead-service under a token addressed to customer by name.

Properties worth protecting: it is guarded by a service token (a request without one is
refused, the same as every other internal route); it is idempotent by the caller's own ids —
calling it twice with the same `company_id`/`contact_id` reuses the same rows rather than
creating a second company (ADR-0017's "same lead converted twice -> one company"); it is
restricted to `service_caller == "lead"`; and a token's `org` claim must name the path's
organization even though the signature and audience are otherwise valid.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.models import AuditLog, Owner
from platform_core.auth import Scope
from platform_core.internal_auth import mint_service_token
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _token(org_id: uuid.UUID, *, caller: str = "lead", actor: uuid.UUID | None = None) -> str:
    claims = {"org": str(org_id)}
    if actor is not None:
        claims["sub"] = str(actor)
    return mint_service_token(settings(), audience="customer", caller=caller, claims=claims)


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
            headers={"X-Internal-Token": _token(org_id)},
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
            headers={"X-Internal-Token": _token(org_id)},
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
            headers={"X-Internal-Token": _token(org_id)},
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
            headers={"X-Internal-Token": _token(org_id)},
            json=payload,
        )
        second = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json=payload,
        )
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()

        stmt = selectors.companies_stmt(ctx_for(org_id, "crm.book.company.read", Scope.ORG))
        rows = (await session.execute(stmt)).scalars().all()
        assert len([r for r in rows if r.id == company_id]) == 1

    async def test_a_soft_deleted_company_at_the_same_id_refuses_rather_than_replays(
        self, client, org_id, session: AsyncSession
    ) -> None:
        """A retried `:convert` must not attempt a second `INSERT` at an id Postgres still
        holds — but a soft-deleted row at that id is not a safe replay to reuse silently
        either (M2, fix round 2): the company is *gone* from the book, and handing its id
        back as though the conversion succeeded would attach the lead to a phantom account.
        A distinct 409 `company-deleted` problem type, not a bare 404 and not a quiet 200."""
        owner_id = await _seed_owner(session, org_id)
        company_id, contact_id = uuid.uuid4(), uuid.uuid4()
        payload = {
            "company_id": str(company_id),
            "contact_id": str(contact_id),
            "company_name": "Stellar Robotics",
            "owner_id": str(owner_id),
            "contact_full_name": "Priya Sharma",
            "contact_email": None,
            "contact_raw_phone": None,
        }
        first = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json=payload,
        )
        assert first.status_code == 200

        write = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        async with session.begin():
            company = await selectors.get_company(session, org_id, company_id)
            assert company is not None
            await services.delete_company(session, write, company)

        second = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json=payload,
        )
        assert second.status_code == 409
        assert second.json()["type"].endswith("/company-deleted")

    async def test_a_soft_deleted_contact_at_the_same_id_refuses_rather_than_replays(
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
            headers={"X-Internal-Token": _token(org_id)},
            json=payload,
        )
        assert first.status_code == 200

        write = ctx_for(org_id, "crm.book.contact.write", Scope.ORG)
        async with session.begin():
            contact = await selectors.get_contact(session, org_id, contact_id)
            assert contact is not None
            await services.delete_contact(session, write, contact)

        second = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json=payload,
        )
        assert second.status_code == 409
        assert second.json()["type"].endswith("/contact-deleted")

    async def test_reusing_a_contact_by_email_never_moves_them_to_the_new_company(
        self, client, org_id, session: AsyncSession
    ) -> None:
        """m3: the same person is the same person regardless of which company's lead brought
        them up. A second lead naming a different company but the same contact email must
        reuse that contact -- never reassign it to the new company."""
        owner_id = await _seed_owner(session, org_id)
        first_company_id, contact_id = uuid.uuid4(), uuid.uuid4()
        await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json={
                "company_id": str(first_company_id),
                "contact_id": str(contact_id),
                "company_name": "Stellar Robotics",
                "owner_id": str(owner_id),
                "contact_full_name": "Priya Sharma",
                "contact_email": "priya@stellar.example",
                "contact_raw_phone": None,
            },
        )

        second_company_id, second_contact_id = uuid.uuid4(), uuid.uuid4()
        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json={
                "company_id": str(second_company_id),
                "contact_id": str(second_contact_id),
                "company_name": "Second Chance Ltd",
                "owner_id": str(owner_id),
                "contact_full_name": "Priya Sharma",
                "contact_email": "priya@stellar.example",
                "contact_raw_phone": None,
            },
        )
        assert resp.status_code == 200
        # The reused contact's own id came back, not the new lead's deterministic one.
        assert resp.json()["contact_id"] == str(contact_id)

        contact = await selectors.get_contact(session, org_id, contact_id)
        assert contact is not None
        assert contact.company_id == first_company_id

    async def test_reusing_a_phoneless_contact_fills_the_gap_from_the_lead(
        self, client, org_id, session: AsyncSession
    ) -> None:
        owner_id = await _seed_owner(session, org_id)
        company_id, contact_id = uuid.uuid4(), uuid.uuid4()
        await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json={
                "company_id": str(company_id),
                "contact_id": str(contact_id),
                "company_name": "Stellar Robotics",
                "owner_id": str(owner_id),
                "contact_full_name": "Priya Sharma",
                "contact_email": "priya@stellar.example",
                "contact_raw_phone": None,
            },
        )

        second_company_id, second_contact_id = uuid.uuid4(), uuid.uuid4()
        await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json={
                "company_id": str(second_company_id),
                "contact_id": str(second_contact_id),
                "company_name": "Second Chance Ltd",
                "owner_id": str(owner_id),
                "contact_full_name": "Priya Sharma",
                "contact_email": "priya@stellar.example",
                "contact_raw_phone": "+919876543210",
            },
        )

        contact = await selectors.get_contact(session, org_id, contact_id)
        assert contact is not None
        assert contact.phone_enc is not None

        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "contact.updated", AuditLog.entity_id == contact_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any(r.acting_as == "lead" for r in rows)

    async def test_reusing_a_contact_with_a_different_phone_keeps_the_existing_one(
        self, client, org_id, session: AsyncSession
    ) -> None:
        owner_id = await _seed_owner(session, org_id)
        company_id, contact_id = uuid.uuid4(), uuid.uuid4()
        await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
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
        async with session.begin():
            contact_before = await selectors.get_contact(session, org_id, contact_id)
            assert contact_before is not None
            original_masked_phone = contact_before.phone

        second_company_id, second_contact_id = uuid.uuid4(), uuid.uuid4()
        await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json={
                "company_id": str(second_company_id),
                "contact_id": str(second_contact_id),
                "company_name": "Second Chance Ltd",
                "owner_id": str(owner_id),
                "contact_full_name": "Priya Sharma",
                "contact_email": "priya@stellar.example",
                "contact_raw_phone": "+919988776655",
            },
        )

        contact_after = await selectors.get_contact(session, org_id, contact_id)
        assert contact_after is not None
        # Kept, never overwritten by the second lead's different number.
        assert contact_after.phone == original_masked_phone

        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "contact.reused_for_lead",
                        AuditLog.entity_id == contact_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].acting_as == "lead"
        # The audit records the *masked* form the lead brought, never the raw digits.
        assert "9988776655" not in str(rows[0].after)
        assert "•" in rows[0].after["phone_from_lead"]

    async def _convert(
        self, client, org_id, owner_id, *, name: str, email: str | None, phone: str | None
    ):  # type: ignore[no-untyped-def]
        return await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id)},
            json={
                "company_id": str(uuid.uuid4()),
                "contact_id": str(uuid.uuid4()),
                "company_name": name,
                "owner_id": str(owner_id),
                "contact_full_name": "Priya Sharma",
                "contact_email": email,
                "contact_raw_phone": phone,
            },
        )

    async def _markers(self, session: AsyncSession, entity_id: str) -> list[AuditLog]:
        session.expire_all()
        async with session.begin():
            stmt = select(AuditLog).where(
                AuditLog.entity_id == uuid.UUID(entity_id),
                AuditLog.action.like("%.reused_for_lead"),
            )
            return list((await session.execute(stmt)).scalars())

    async def test_a_company_reused_by_name_writes_one_marker_row(
        self, client, org_id, session: AsyncSession
    ) -> None:
        owner_id = await _seed_owner(session, org_id)
        first = await self._convert(
            client, org_id, owner_id, name="Stellar Robotics", email=None, phone=None
        )
        second = await self._convert(
            client, org_id, owner_id, name="Stellar Robotics", email=None, phone=None
        )
        assert second.json()["company_id"] == first.json()["company_id"]
        rows = await self._markers(session, first.json()["company_id"])
        assert [(r.action, r.acting_as, r.after) for r in rows] == [
            ("company.reused_for_lead", "lead", {"matched_by": "name"})
        ]

    @pytest.mark.parametrize("phone", [None, "+919876543210"])
    async def test_a_contact_reused_by_email_writes_one_marker_row(
        self, client, org_id, session: AsyncSession, phone: str | None
    ) -> None:
        """No phone, or the same number already on file: nothing else is written, so the
        marker is the reuse's only trace."""
        owner_id = await _seed_owner(session, org_id)
        email = "priya@stellar.example"
        first = await self._convert(client, org_id, owner_id, name="A Co", email=email, phone=phone)
        second = await self._convert(
            client, org_id, owner_id, name="B Co", email=email, phone=phone
        )
        assert second.json()["contact_id"] == first.json()["contact_id"]
        rows = await self._markers(session, first.json()["contact_id"])
        assert [(r.action, r.after) for r in rows] == [
            ("contact.reused_for_lead", {"matched_by": "email"})
        ]

    async def test_a_caller_other_than_lead_is_refused(self, client, org_id) -> None:
        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id, caller="deal")},
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
        assert resp.status_code == 403

    async def test_a_token_addressed_to_another_organization_is_refused(
        self, client, org_id
    ) -> None:
        other_org = uuid.uuid4()
        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(other_org)},
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
        assert resp.status_code == 403

    async def test_the_tokens_actor_claim_attributes_the_audit_row(
        self, client, org_id, session: AsyncSession
    ) -> None:
        owner_id = await _seed_owner(session, org_id)
        actor = uuid.uuid4()
        company_id, contact_id = uuid.uuid4(), uuid.uuid4()

        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token(org_id, actor=actor)},
            json={
                "company_id": str(company_id),
                "contact_id": str(contact_id),
                "company_name": "Stellar Robotics",
                "owner_id": str(owner_id),
                "contact_full_name": "Priya Sharma",
                "contact_email": None,
                "contact_raw_phone": None,
            },
        )
        assert resp.status_code == 200

        row = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity == "company",
                    AuditLog.entity_id == company_id,
                    AuditLog.action == "company.created",
                )
            )
        ).scalar_one()
        assert row.actor_id == actor
        assert row.acting_as == "lead"


class TestOwnersValidate:
    async def test_without_a_service_token_it_is_refused(self, client, org_id) -> None:
        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/owners:validate",
            json={"owner_ids": [str(uuid.uuid4())]},
        )
        assert resp.status_code == 401

    async def test_a_caller_other_than_lead_is_refused(self, client, org_id) -> None:
        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/owners:validate",
            headers={"X-Internal-Token": _token(org_id, caller="notification")},
            json={"owner_ids": [str(uuid.uuid4())]},
        )
        assert resp.status_code == 403

    async def test_a_token_addressed_to_another_organization_is_refused(
        self, client, org_id
    ) -> None:
        other_org = uuid.uuid4()
        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/owners:validate",
            headers={"X-Internal-Token": _token(other_org)},
            json={"owner_ids": [str(uuid.uuid4())]},
        )
        assert resp.status_code == 403

    async def test_an_unknown_id_is_invalid(self, client, org_id) -> None:
        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/owners:validate",
            headers={"X-Internal-Token": _token(org_id)},
            json={"owner_ids": [str(uuid.uuid4())]},
        )
        assert resp.status_code == 200
        assert len(resp.json()["invalid_owner_ids"]) == 1

    async def test_an_active_owner_is_valid_an_inactive_one_is_not(
        self, client, org_id, session: AsyncSession
    ) -> None:
        active_id = await _seed_owner(session, org_id)
        write = ctx_for(org_id, "crm.book.owner.write", Scope.ORG)
        async with session.begin():
            inactive = await services.create_owner(
                session, write, full_name="Inactive Rep", email="inactive@example.com"
            )
            await services.deactivate_owner(session, write, inactive)

        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/owners:validate",
            headers={"X-Internal-Token": _token(org_id)},
            json={"owner_ids": [str(active_id), str(inactive.id)]},
        )
        assert resp.status_code == 200
        assert resp.json()["invalid_owner_ids"] == [str(inactive.id)]

    async def test_another_organizations_active_owner_is_still_invalid_here(
        self, client, org_id, admin_session: AsyncSession
    ) -> None:
        """An id that is a perfectly valid, active owner **in another organization** must
        still come back invalid here — the tenant filter, not just the active flag. Seeded on
        the BYPASSRLS admin session, the same way `test_cross_tenant_isolation.py` plants
        another tenant's row: the ordinary tenant-bound `session` fixture could not insert an
        owner for an organization it is not bound to."""
        other_org = uuid.uuid4()
        other_owner_id = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                Owner.__table__.insert().values(
                    id=other_owner_id,
                    organization_id=other_org,
                    full_name="Someone Else's Rep",
                    email="someone-else@example.com",
                    role="",
                    is_active=True,
                )
            )

        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/owners:validate",
            headers={"X-Internal-Token": _token(org_id)},
            json={"owner_ids": [str(other_owner_id)]},
        )
        assert resp.status_code == 200
        assert resp.json()["invalid_owner_ids"] == [str(other_owner_id)]
