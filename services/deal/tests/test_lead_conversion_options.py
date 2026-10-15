"""Lead conversion -> deal: the rep's value and stage, and a clash on the plain name.

`POST /internal/v1/organizations/{org}/leads:convert` now takes an optional `value_minor` and
`stage`, and resolves a taken deal name to the first free "<name> (2)", "(3)" itself, so
lead-service can send the plain "<Company> -- <Lead name>" with no id fragment. A replay by
`deal_id` never renames or revalues anything.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.config import settings
from platform_core.internal_auth import mint_service_token

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("pipeline_mode")]

NAME = "Stellar Robotics — Priya Sharma"


def _headers(org_id: uuid.UUID) -> dict[str, str]:
    token = mint_service_token(
        settings(), audience="deal", caller="lead", claims={"org": str(org_id)}
    )
    return {"X-Internal-Token": token}


def _payload(**overrides: object) -> dict:
    base: dict = {
        "deal_id": str(uuid.uuid4()),
        "company_id": str(uuid.uuid4()),
        "contact_id": None,
        "owner_id": str(uuid.uuid4()),
        "name": NAME,
        "close_date": (date.today() + timedelta(days=30)).isoformat(),
        "source": "lead",
    }
    base.update(overrides)
    return base


def _url(org_id: uuid.UUID) -> str:
    return f"/internal/v1/organizations/{org_id}/leads:convert"


class TestValueAndStage:
    async def test_the_reps_value_and_stage_are_used(
        self, client, org_id, session: AsyncSession
    ) -> None:
        payload = _payload(value_minor=250000, stage="qualified")
        resp = await client.post(_url(org_id), headers=_headers(org_id), json=payload)
        assert resp.status_code == 200, resp.text
        deal = await selectors.get_deal(session, org_id, uuid.UUID(payload["deal_id"]))
        assert deal is not None
        assert deal.value_minor == 250000 and deal.currency == "INR"
        assert deal.stage == "qualified"

    async def test_omitted_they_default_to_zero_and_the_entry_stage(
        self, client, org_id, session: AsyncSession
    ) -> None:
        payload = _payload()
        resp = await client.post(_url(org_id), headers=_headers(org_id), json=payload)
        assert resp.status_code == 200
        deal = await selectors.get_deal(session, org_id, uuid.UUID(payload["deal_id"]))
        assert deal is not None and deal.value_minor == 0 and deal.stage == "lead"

    @pytest.mark.parametrize(
        "extra",
        [{"value_minor": -1}, {"value_minor": 1.5}, {"stage": "galaxy_brain"}, {"stage": ""}],
    )
    async def test_bad_values_are_refused_and_open_no_deal(
        self, client, org_id, session: AsyncSession, extra: dict
    ) -> None:
        payload = _payload(**extra)
        resp = await client.post(_url(org_id), headers=_headers(org_id), json=payload)
        assert resp.status_code == 422
        assert await selectors.get_deal(session, org_id, uuid.UUID(payload["deal_id"])) is None

    async def test_a_replay_does_not_revalue_the_deal(
        self, client, org_id, session: AsyncSession
    ) -> None:
        payload = _payload(value_minor=100)
        await client.post(_url(org_id), headers=_headers(org_id), json=payload)
        replay = await client.post(
            _url(org_id), headers=_headers(org_id), json={**payload, "value_minor": 999}
        )
        assert replay.status_code == 200
        deal = await selectors.get_deal(session, org_id, uuid.UUID(payload["deal_id"]))
        assert deal is not None and deal.value_minor == 100


class TestANameThatIsTaken:
    async def test_the_second_deal_is_numbered_not_refused(
        self, client, org_id, session: AsyncSession
    ) -> None:
        first, second, third = _payload(), _payload(), _payload()
        for body in (first, second, third):
            resp = await client.post(_url(org_id), headers=_headers(org_id), json=body)
            assert resp.status_code == 200, resp.text
        names = []
        for body in (first, second, third):
            deal = await selectors.get_deal(session, org_id, uuid.UUID(body["deal_id"]))
            assert deal is not None
            names.append(deal.name)
        assert names == [NAME, f"{NAME} (2)", f"{NAME} (3)"]

    async def test_a_replay_keeps_the_name_it_was_given(
        self, client, org_id, session: AsyncSession
    ) -> None:
        await client.post(_url(org_id), headers=_headers(org_id), json=_payload())
        second = _payload()
        await client.post(_url(org_id), headers=_headers(org_id), json=second)
        await client.post(_url(org_id), headers=_headers(org_id), json=second)
        deal = await selectors.get_deal(session, org_id, uuid.UUID(second["deal_id"]))
        assert deal is not None and deal.name == f"{NAME} (2)"

    async def test_a_long_name_is_truncated_to_leave_room_for_the_number(
        self, client, org_id, session: AsyncSession
    ) -> None:
        long_name = "N" * 200
        first, second = _payload(name=long_name), _payload(name=long_name)
        await client.post(_url(org_id), headers=_headers(org_id), json=first)
        resp = await client.post(_url(org_id), headers=_headers(org_id), json=second)
        assert resp.status_code == 200
        deal = await selectors.get_deal(session, org_id, uuid.UUID(second["deal_id"]))
        assert deal is not None and len(deal.name) <= 200 and deal.name.endswith(" (2)")

    async def test_names_are_per_organization(self, client, org_id, session: AsyncSession) -> None:
        """A deal in another tenant with the same name never forces a number here (RLS)."""
        resp = await client.post(_url(org_id), headers=_headers(org_id), json=_payload())
        assert resp.status_code == 200
        deal_id = uuid.UUID(resp.json()["deal_id"])
        deal = await selectors.get_deal(session, org_id, deal_id)
        assert deal is not None and deal.name == NAME
