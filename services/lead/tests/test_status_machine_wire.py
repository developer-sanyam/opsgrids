"""The status moves the server will accept are published, and a refused move reads as a sentence.

The CRM lead dropdown offered every status, so choosing New -> Qualified answered a bare 409
("A lead in 'new' cannot move to 'qualified'") and re-selecting the current status did the same.
`LeadOut.next_statuses` is computed from `domain.can_transition` -- the only copy of the state
machine -- so the UI cannot drift from it; the test below keeps the two in lock-step.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest

from app import domain
from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

WRITE = ["crm.lead.lead.read:ORG", "crm.lead.lead.write:ORG"]


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def test_the_published_moves_are_exactly_the_state_machines() -> None:
    for current in domain.STATUS_ORDER:
        published = set(domain.allowed_next_statuses(current))
        accepted = {new for new in domain.STATUS_ORDER if domain.can_transition(current, new)}
        assert published == accepted, current


def test_a_new_lead_cannot_skip_to_qualified_and_converted_is_never_offered() -> None:
    assert "qualified" not in domain.allowed_next_statuses("new")
    assert domain.allowed_next_statuses("converted") == ()
    for current in domain.STATUS_ORDER:
        assert "converted" not in domain.allowed_next_statuses(current)


class TestTheWireCarriesTheMoves:
    async def test_a_lead_reads_back_its_next_statuses(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, WRITE)):
            response = await client.get(f"/api/v1/leads/{data['lead_b_id']}")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "new"
        assert response.json()["next_statuses"] == ["working", "disqualified"]

    async def test_the_list_carries_them_too(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, WRITE)):
            response = await client.get("/api/v1/leads")
        assert response.status_code == 200
        by_id = {row["id"]: row for row in response.json()["items"]}
        assert by_id[str(data["lead_a_id"])]["next_statuses"] == ["working", "disqualified"]


class TestARefusedMoveIsAPlainSentence:
    async def test_new_to_qualified(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, WRITE)):
            response = await client.patch(
                f"/api/v1/leads/{data['lead_b_id']}", json={"status": "qualified"}
            )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "'new'" not in detail and "cannot move to" not in detail
        assert "Working" in detail and "Qualified" in detail

    async def test_choosing_the_current_status(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, WRITE)):
            response = await client.patch(
                f"/api/v1/leads/{data['lead_b_id']}", json={"status": "new"}
            )
        assert response.status_code == 409
        assert "already" in response.json()["detail"].lower()

    async def test_an_unknown_lead_is_still_a_404(self, client, org_id, data) -> None:
        with as_principal(principal_for(org_id, WRITE)):
            response = await client.patch(
                f"/api/v1/leads/{uuid.uuid4()}", json={"status": "working"}
            )
        assert response.status_code == 404

    async def test_a_writer_without_the_permission_is_forbidden(
        self, client, org_id, data
    ) -> None:
        with as_principal(principal_for(org_id, ["crm.lead.lead.read:ORG"])):
            response = await client.patch(
                f"/api/v1/leads/{data['lead_b_id']}", json={"status": "working"}
            )
        assert response.status_code == 403
