"""An org owner with no employee record can still log an activity and open a deal.

The self-serve signup owner is a membership with no employee link, so `subject_ref` is None.
Logging an activity or opening a deal used to answer 422 "No owner could be determined" for them
-- the production blocker. An ORG-scoped caller now defaults to their membership id, which is also
their roster id (`PUT /customers/owners/me` in customer-service).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest

from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("pipeline_mode")]

PIPELINE_ADMIN_PERMS = [
    "crm.pipeline.deal.read:ORG",
    "crm.pipeline.deal.write:ORG",
    "crm.pipeline.deal.delete:ORG",
    "crm.pipeline.activity.read:ORG",
    "crm.pipeline.activity.write:ORG",
]


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


class TestAnOrgOwnerWithNoEmployeeRecord:
    async def test_logging_an_activity_defaults_the_owner_to_the_membership(
        self, client, org_id, data
    ) -> None:
        membership_id = uuid.uuid4()
        principal = principal_for(
            org_id, PIPELINE_ADMIN_PERMS, subject_ref=None, membership_id=membership_id
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "type": "call",
                    "subject": "First call",
                    "company_id": str(data["company_a"]),
                },
            )
        assert response.status_code == 201, response.text
        assert response.json()["owner_id"] == str(membership_id)

    async def test_opening_a_deal_defaults_the_owner_to_the_membership(
        self, client, org_id, data
    ) -> None:
        membership_id = uuid.uuid4()
        principal = principal_for(
            org_id, PIPELINE_ADMIN_PERMS, subject_ref=None, membership_id=membership_id
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Owner-less opener",
                    "company_id": str(data["company_a"]),
                    "value_minor": 100,
                    "close_date": "2026-12-01",
                },
            )
        assert response.status_code == 201, response.text
        assert response.json()["owner_id"] == str(membership_id)

    async def test_an_employee_link_still_wins_over_the_membership(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, PIPELINE_ADMIN_PERMS, subject_ref=data["subject_a"], membership_id=uuid.uuid4()
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "type": "note",
                    "subject": "Linked rep",
                    "company_id": str(data["company_a"]),
                },
            )
        assert response.status_code == 201
        assert response.json()["owner_id"] == str(data["subject_a"])

    async def test_an_own_scoped_caller_with_no_link_is_refused_in_plain_words(
        self, client, org_id, data
    ) -> None:
        """OWN compares the employee link, so there is nothing true to attribute the entry to;
        the refusal says what to do instead of a bare state sentence."""
        principal = principal_for(
            org_id,
            ["crm.pipeline.activity.write:OWN", "crm.pipeline.deal.read:OWN"],
            subject_ref=None,
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "type": "note",
                    "subject": "Unlinked rep",
                    "company_id": str(data["company_a"]),
                },
            )
        assert response.status_code == 422
        assert "administrator" in response.text.lower()
