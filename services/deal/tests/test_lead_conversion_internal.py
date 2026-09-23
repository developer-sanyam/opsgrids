"""The deal-side half of lead conversion — `POST /internal/v1/organizations/{org}/
leads:convert`, called by lead-service under a token addressed to deal by name.

Guarded by a service token like every internal route, and idempotent by the caller's own
`deal_id` — calling it twice reuses the same deal rather than opening a second one
(ADR-0017's "same lead converted twice -> one deal").
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.config import settings
from platform_core.internal_auth import mint_service_token

pytestmark = pytest.mark.integration


def _token() -> str:
    return mint_service_token(settings(), audience="deal", caller="lead")


def _payload(**overrides: object) -> dict:
    base = {
        "deal_id": str(uuid.uuid4()),
        "company_id": str(uuid.uuid4()),
        "contact_id": str(uuid.uuid4()),
        "owner_id": str(uuid.uuid4()),
        "name": "Stellar Robotics — Priya Sharma",
        "close_date": (date.today() + timedelta(days=30)).isoformat(),
        "source": "form",
    }
    base.update(overrides)
    return base


class TestLeadConversionInternal:
    async def test_without_a_service_token_it_is_refused(self, client, org_id) -> None:
        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert", json=_payload()
        )
        assert resp.status_code == 401

    async def test_opens_a_deal_at_the_lead_stage(
        self, client, org_id, session: AsyncSession
    ) -> None:
        deal_id = uuid.uuid4()
        resp = await client.post(
            f"/internal/v1/organizations/{org_id}/leads:convert",
            headers={"X-Internal-Token": _token()},
            json=_payload(deal_id=str(deal_id)),
        )
        assert resp.status_code == 200
        assert resp.json()["deal_id"] == str(deal_id)

        deal = await selectors.get_deal(session, org_id, deal_id)
        assert deal is not None
        assert deal.stage == "lead"
        assert deal.value_minor == 0
        assert deal.source == "form"

    async def test_calling_twice_with_the_same_deal_id_reuses_the_same_deal(
        self, client, org_id, session: AsyncSession
    ) -> None:
        payload = _payload()
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

        deal_id = uuid.UUID(str(payload["deal_id"]))
        deal = await selectors.get_deal(session, org_id, deal_id)
        assert deal is not None
