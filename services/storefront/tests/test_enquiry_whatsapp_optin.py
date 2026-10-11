"""The enquiry form's WhatsApp marketing opt-in (ENGAGE-02).

A buyer may tick an unticked-by-default box to get WhatsApp marketing. The public body is the
trust boundary: the tick must be a real boolean and needs a phone number with its country code.
A ticked enquiry stamps `whatsapp_opt_in_at` and stages `storefront.enquiry.whatsapp_opted_in`
(ids only — the number never travels on an event). Notification's consumer then fetches the number
through `GET /internal/v1/enquiries/{id}/whatsapp-opt-in`, which only notification may call, only
for its token's organization, only for a ticked enquiry, and which audits every read.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import AuditLog, Enquiry, OutboxEvent
from platform_core.internal_auth import mint_service_token
from tests.helpers import seed_enquiry, seed_store

pytestmark = pytest.mark.integration

PUBLIC_ENQUIRY = "/api/v1/storefront/public/enquiry"
EVENT = "storefront.enquiry.whatsapp_opted_in"


def _payload(store: str, **overrides: object) -> dict[str, object]:
    return {
        "store": store,
        "name": "Priya Shah",
        "company": "Shah Traders",
        "email": "priya@shahtraders.example",
        "phone": "+91 98765 43210",
        "requirement": "Need a quote for 500 bags of rice.",
        "lines": [],
        **overrides,
    }


async def _enquiries(admin_session, org_id: uuid.UUID) -> list[Enquiry]:
    await admin_session.rollback()
    return list(
        (
            await admin_session.execute(select(Enquiry).where(Enquiry.organization_id == org_id))
        ).scalars()
    )


async def _events(admin_session, org_id: uuid.UUID) -> list[OutboxEvent]:
    await admin_session.rollback()
    return list(
        (
            await admin_session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.organization_id == org_id, OutboxEvent.event_type == EVENT
                )
            )
        ).scalars()
    )


class TestPublicForm:
    async def test_a_ticked_opt_in_is_stamped_and_announced_without_the_number(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")

        response = await client.post(
            PUBLIC_ENQUIRY, json=_payload(slug, whatsapp_marketing_opt_in=True)
        )

        assert response.status_code == 201, response.text
        [enquiry] = await _enquiries(admin_session, org_id)
        assert enquiry.whatsapp_opt_in_at is not None
        enquiry_id = enquiry.id
        [event] = await _events(admin_session, org_id)
        assert event.payload == {"enquiry_id": str(enquiry_id)}
        assert event.aggregate_id == enquiry_id

    async def test_the_box_is_unticked_by_default(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")

        assert (await client.post(PUBLIC_ENQUIRY, json=_payload(slug))).status_code == 201

        [enquiry] = await _enquiries(admin_session, org_id)
        assert enquiry.whatsapp_opt_in_at is None
        assert await _events(admin_session, org_id) == []

    @pytest.mark.parametrize(
        "overrides",
        [
            {"whatsapp_marketing_opt_in": True, "phone": None},
            {"whatsapp_marketing_opt_in": True, "phone": "98765 43210"},
            {"whatsapp_marketing_opt_in": True, "phone": "+91 call me"},
            {"whatsapp_marketing_opt_in": "yes"},
            {"whatsapp_marketing_opt_in": 1},
        ],
    )
    async def test_a_tick_needs_a_real_boolean_and_a_number_with_its_country_code(
        self, client, admin_session, org_id: uuid.UUID, slug: str, overrides: dict
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")

        response = await client.post(PUBLIC_ENQUIRY, json=_payload(slug, **overrides))

        assert response.status_code == 422
        assert await _enquiries(admin_session, org_id) == []


def _token(org: uuid.UUID | None, *, caller: str = "notification", audience: str = "storefront"):
    claims = {"org": str(org)} if org is not None else {}
    return mint_service_token(settings(), audience=audience, caller=caller, claims=claims)


def _url(enquiry_id: uuid.UUID) -> str:
    return f"/internal/v1/enquiries/{enquiry_id}/whatsapp-opt-in"


class TestInternalRead:
    async def _ticked(self, client, admin_session, org_id: uuid.UUID, slug: str) -> uuid.UUID:
        await seed_store(admin_session, org_id, slug, status="published")
        response = await client.post(
            PUBLIC_ENQUIRY, json=_payload(slug, whatsapp_marketing_opt_in=True)
        )
        assert response.status_code == 201
        return uuid.UUID(response.json()["id"])

    async def test_notification_reads_the_number_and_every_read_is_audited(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        enquiry_id = await self._ticked(client, admin_session, org_id, slug)

        response = await client.get(_url(enquiry_id), headers={"X-Internal-Token": _token(org_id)})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["phone"] == "+91 98765 43210"
        assert body["email"] == "priya@shahtraders.example"
        assert body["name"] == "Priya Shah"
        assert body["opted_in_at"]
        await admin_session.rollback()
        audits = list(
            (
                await admin_session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "entity.sensitive_read",
                        AuditLog.entity_id == enquiry_id,
                    )
                )
            ).scalars()
        )
        assert len(audits) == 1

    async def test_an_unticked_enquiry_is_not_found(
        self, client, admin_session, org_id: uuid.UUID
    ) -> None:
        enquiry = await seed_enquiry(admin_session, org_id)

        response = await client.get(_url(enquiry.id), headers={"X-Internal-Token": _token(org_id)})

        assert response.status_code == 404

    async def test_another_organizations_enquiry_is_not_found(
        self, client, admin_session, org_id: uuid.UUID, other_org: uuid.UUID, slug: str
    ) -> None:
        enquiry_id = await self._ticked(client, admin_session, other_org, slug)

        response = await client.get(_url(enquiry_id), headers={"X-Internal-Token": _token(org_id)})

        assert response.status_code == 404
        assert "98765" not in response.text

    async def test_no_token_is_401(self, client) -> None:
        assert (await client.get(_url(uuid.uuid4()))).status_code == 401

    async def test_a_token_for_another_service_is_401(self, client, org_id: uuid.UUID) -> None:
        response = await client.get(
            _url(uuid.uuid4()),
            headers={"X-Internal-Token": _token(org_id, audience="notification")},
        )
        assert response.status_code == 401

    async def test_any_caller_but_notification_is_403(self, client, org_id: uuid.UUID) -> None:
        response = await client.get(
            _url(uuid.uuid4()), headers={"X-Internal-Token": _token(org_id, caller="customer")}
        )
        assert response.status_code == 403

    async def test_a_token_naming_no_organization_is_403(self, client) -> None:
        response = await client.get(_url(uuid.uuid4()), headers={"X-Internal-Token": _token(None)})
        assert response.status_code == 403
