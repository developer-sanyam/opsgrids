"""`POST /leads/{id}:convert` -- the deal is named sensibly and the rep can set its value and stage.

The conversion used to name the deal "<Company> -- <Lead> . 68d1da6a" (an id fragment, to dodge a
duplicate-name refusal) with value 0 and no way to say otherwise. The name is now the plain
"<Company> -- <Lead name>" (deal-service resolves a clash itself) and the optional body carries
`deal_value_minor` and `deal_stage`.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest

from app import clients
from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

CONVERT = ["crm.lead.lead.read:ORG", "crm.lead.lead.convert:ORG"]


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    recorded: list[tuple[str, dict]] = []
    contact_id = uuid.uuid4()

    async def _company(settings, organization_id, **kwargs):  # type: ignore[no-untyped-def]
        recorded.append(("company", kwargs))
        return uuid.uuid4(), contact_id

    async def _deal(settings, organization_id, **kwargs):  # type: ignore[no-untyped-def]
        recorded.append(("deal", kwargs))
        return uuid.uuid4()

    monkeypatch.setattr(clients, "ensure_company_and_contact", _company)
    monkeypatch.setattr(clients, "ensure_deal", _deal)
    return recorded


def _deal_call(calls: list[tuple[str, dict]]) -> dict:
    return next(kwargs for name, kwargs in calls if name == "deal")


class TestTheDealName:
    async def test_it_is_company_and_lead_with_no_id_fragment(
        self, client, org_id, data, calls
    ) -> None:
        with as_principal(principal_for(org_id, CONVERT)):
            response = await client.post(f"/api/v1/leads/{data['lead_a_id']}:convert")
        assert response.status_code == 200, response.text
        assert _deal_call(calls)["name"] == "Stellar Robotics — Priya Sharma"

    async def test_with_no_body_the_deal_opens_at_zero_in_the_entry_stage(
        self, client, org_id, data, calls
    ) -> None:
        with as_principal(principal_for(org_id, CONVERT)):
            await client.post(f"/api/v1/leads/{data['lead_a_id']}:convert")
        deal = _deal_call(calls)
        assert deal["value_minor"] == 0 and deal["stage"] is None


class TestTheRepSetsTheDeal:
    async def test_value_and_stage_travel_to_deal_service(
        self, client, org_id, data, calls
    ) -> None:
        with as_principal(principal_for(org_id, CONVERT)):
            response = await client.post(
                f"/api/v1/leads/{data['lead_a_id']}:convert",
                json={"deal_value_minor": 250000, "deal_stage": "qualified"},
            )
        assert response.status_code == 200, response.text
        deal = _deal_call(calls)
        assert deal["value_minor"] == 250000 and deal["stage"] == "qualified"

    @pytest.mark.parametrize(
        "body",
        [
            {"deal_value_minor": -1},
            {"deal_value_minor": 12.5},
            {"deal_value_minor": "lots"},
            {"deal_stage": ""},
            {"deal_stage": "x" * 80},
            {"owner_id": str(uuid.uuid4())},
        ],
    )
    async def test_a_bad_body_is_refused_before_anything_is_claimed(
        self, client, org_id, data, calls, body
    ) -> None:
        with as_principal(principal_for(org_id, CONVERT)):
            response = await client.post(f"/api/v1/leads/{data['lead_a_id']}:convert", json=body)
        assert response.status_code == 422
        assert calls == []

    async def test_a_replay_returns_the_same_ids_and_ignores_the_new_options(
        self, client, org_id, data, calls
    ) -> None:
        with as_principal(principal_for(org_id, CONVERT)):
            first = await client.post(f"/api/v1/leads/{data['lead_a_id']}:convert")
            second = await client.post(
                f"/api/v1/leads/{data['lead_a_id']}:convert", json={"deal_value_minor": 99}
            )
        assert first.json() == second.json()
        assert len([c for c in calls if c[0] == "deal"]) == 1


class TestConvertStaysGuarded:
    async def test_without_the_permission_it_is_forbidden(self, client, org_id, data, calls) -> None:
        with as_principal(principal_for(org_id, ["crm.lead.lead.read:ORG"])):
            response = await client.post(f"/api/v1/leads/{data['lead_a_id']}:convert")
        assert response.status_code == 403 and calls == []

    async def test_an_own_scoped_rep_cannot_convert_someone_elses_lead(
        self, client, org_id, data, calls
    ) -> None:
        principal = principal_for(
            org_id,
            ["crm.lead.lead.read:OWN", "crm.lead.lead.convert:OWN"],
            subject_ref=data["owner_b"],
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/leads/{data['lead_a_id']}:convert",
                json={"deal_value_minor": 1},
            )
        assert response.status_code == 403 and calls == []

    async def test_a_phone_only_lead_hands_its_phone_to_the_contact_step(
        self, client, org_id, data, calls, session
    ) -> None:
        """Lead B has neither; lead A has both. The claim opens the sealed values and passes them
        on -- the contact is customer-service's to create (phone-only is allowed there)."""
        with as_principal(principal_for(org_id, CONVERT)):
            await client.post(f"/api/v1/leads/{data['lead_a_id']}:convert")
        company_call = next(kwargs for name, kwargs in calls if name == "company")
        assert company_call["contact_raw_phone"] == "+919876543210"
