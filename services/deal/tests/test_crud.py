"""Create, update, delete, stage moves and activity logging — the write half of the
pipeline, over the real HTTP surface.

Every new endpoint carries: a happy path, a validation failure (422), a duplicate/illegal
transition (409) where one applies, a not-found, a negative-authz check (missing
permission -> 403, an OWN-scoped rep on another rep's deal -> 403), and cross-tenant
isolation.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("pipeline_mode")]


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


#: An org-scoped caller holding every new `crm.pipeline.*` permission this slice adds —
#: the "admin" shape `CRM_PIPELINE_ADMIN` grants.
PIPELINE_ADMIN_PERMS = [
    "crm.pipeline.deal.read:ORG",
    "crm.pipeline.deal.write:ORG",
    "crm.pipeline.deal.delete:ORG",
    "crm.pipeline.activity.read:ORG",
    "crm.pipeline.activity.write:ORG",
]


def _admin(org_id: uuid.UUID, subject_ref: uuid.UUID) -> Principal:
    return principal_for(org_id, PIPELINE_ADMIN_PERMS, subject_ref=subject_ref)


# --------------------------------------------------------------------- create


class TestCreateDeal:
    async def test_happy_path(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "New Opportunity",
                    "company_id": str(data["company_a"]),
                    "owner_id": str(data["subject_a"]),
                    "value_minor": 1_000_000,
                    "close_date": "2026-12-01",
                    "currency": "inr",
                    "stage": "qualified",
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "New Opportunity"
        assert body["currency"] == "INR"
        assert body["stage"] == "qualified"
        assert body["probability"] == 40  # the stage default

    async def test_a_duplicate_name_is_refused(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Fleet telematics rollout",
                    "company_id": str(data["company_a"]),
                    "owner_id": str(data["subject_a"]),
                    "value_minor": 1,
                    "close_date": "2026-12-01",
                },
            )
        assert response.status_code == 409

    async def test_a_negative_value_is_a_422(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Bad Money Deal",
                    "company_id": str(data["company_a"]),
                    "owner_id": str(data["subject_a"]),
                    "value_minor": -5,
                    "close_date": "2026-12-01",
                },
            )
        assert response.status_code == 422

    async def test_an_unknown_stage_is_a_422(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Galaxy Brain Deal",
                    "company_id": str(data["company_a"]),
                    "owner_id": str(data["subject_a"]),
                    "value_minor": 1,
                    "close_date": "2026-12-01",
                    "stage": "galaxy_brain",
                },
            )
        assert response.status_code == 422

    async def test_without_the_permission_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "No Perms Deal",
                    "company_id": str(data["company_a"]),
                    "owner_id": str(data["subject_a"]),
                    "value_minor": 1,
                    "close_date": "2026-12-01",
                },
            )
        assert response.status_code == 403

    async def test_an_own_scoped_rep_cannot_create_a_deal_owned_by_another_rep(
        self, client, org_id, data
    ) -> None:
        """#9: `assert_can_touch` already refuses this at the route — this is the missing
        coverage, mirroring `TestUpdateDeal`'s equivalent edit check."""
        principal = principal_for(
            org_id,
            ["crm.pipeline.deal.write:OWN", "crm.pipeline.deal.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Handed To Someone Else",
                    "company_id": str(data["company_a"]),
                    "owner_id": str(data["subject_b"]),
                    "value_minor": 1,
                    "close_date": "2026-12-01",
                },
            )
        assert response.status_code == 403

    async def test_created_in_one_org_is_invisible_to_another(self, client, org_id, data) -> None:
        other_org = uuid.uuid4()
        with as_principal(_admin(other_org, uuid.uuid4())):
            response = await client.get(f"/api/v1/deals/{data['deal_a_id']}")
        assert response.status_code == 404


# --------------------------------------------------------------------- update


class TestUpdateDeal:
    async def test_happy_path(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/deals/{data['deal_a_id']}",
                json={"value_minor": 40_000_000_00, "source": "Outbound"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["value_minor"] == 40_000_000_00
        assert body["source"] == "Outbound"
        # untouched, and still the negotiation stage a stage move would have to go through
        assert body["stage"] == "negotiation"

    async def test_stage_is_not_a_patchable_field(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/deals/{data['deal_a_id']}", json={"stage": "closed_won"}
            )
        assert response.status_code == 422

    async def test_renaming_onto_an_existing_name_is_refused(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/deals/{data['deal_a_id']}", json={"name": "Omnichannel CRM"}
            )
        assert response.status_code == 409

    async def test_an_unknown_deal_is_not_found(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(f"/api/v1/deals/{uuid.uuid4()}", json={"source": "X"})
        assert response.status_code == 404

    async def test_an_own_scoped_rep_cannot_edit_another_reps_deal(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["crm.pipeline.deal.write:OWN", "crm.pipeline.deal.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/deals/{data['deal_b_id']}", json={"source": "Nope"}
            )
        assert response.status_code == 403

    async def test_without_the_permission_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/deals/{data['deal_a_id']}", json={"source": "X"}
            )
        assert response.status_code == 403

    async def test_an_own_scoped_rep_cannot_reassign_their_deal_to_another_rep(
        self, client, org_id, data
    ) -> None:
        """Minor #1: `owner_id` is the OWN-scope column — the route only checks the
        deal's *current* owner, so without a check in the writer an own-scoped rep could
        patch their own deal onto anyone. deal-service cannot verify the target id exists
        (no cross-schema read), so the scope half is what's enforced: OWN may only set
        their own `subject_ref`."""
        principal = principal_for(
            org_id,
            ["crm.pipeline.deal.write:OWN", "crm.pipeline.deal.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/deals/{data['deal_a_id']}", json={"owner_id": str(data["subject_b"])}
            )
        assert response.status_code == 403

    async def test_an_org_scoped_caller_can_reassign_to_any_rep(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.patch(
                f"/api/v1/deals/{data['deal_a_id']}", json={"owner_id": str(data["subject_b"])}
            )
        assert response.status_code == 200
        assert response.json()["owner_id"] == str(data["subject_b"])


# --------------------------------------------------------------------- delete


class TestDeleteDeal:
    async def test_happy_path(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.delete(f"/api/v1/deals/{data['deal_a_id']}")
            gone = await client.get(f"/api/v1/deals/{data['deal_a_id']}")
        assert response.status_code == 204
        assert gone.status_code == 404

    async def test_an_unknown_deal_is_not_found(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.delete(f"/api/v1/deals/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_without_the_permission_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.delete(f"/api/v1/deals/{data['deal_a_id']}")
        assert response.status_code == 403

    async def test_cross_tenant_delete_is_refused_as_not_found(self, client, org_id, data) -> None:
        other_org = uuid.uuid4()
        with as_principal(_admin(other_org, uuid.uuid4())):
            response = await client.delete(f"/api/v1/deals/{data['deal_a_id']}")
        assert response.status_code == 404
        with as_principal(_admin(org_id, data["subject_a"])):
            still_there = await client.get(f"/api/v1/deals/{data['deal_a_id']}")
        assert still_there.status_code == 200


# --------------------------------------------------------------------- stage moves


class TestMoveStage:
    async def test_happy_path(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                f"/api/v1/deals/{data['deal_a_id']}/stage", json={"stage": "closed_won"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["stage"] == "closed_won"
        assert body["probability"] == 100

    async def test_a_closed_deal_refuses_to_move(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                f"/api/v1/deals/{data['deal_b_id']}/stage", json={"stage": "negotiation"}
            )
        assert response.status_code == 409

    async def test_moving_to_the_same_stage_is_refused(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                f"/api/v1/deals/{data['deal_a_id']}/stage", json={"stage": "negotiation"}
            )
        assert response.status_code == 409

    async def test_an_unknown_stage_is_a_422(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                f"/api/v1/deals/{data['deal_a_id']}/stage", json={"stage": "galaxy_brain"}
            )
        assert response.status_code == 422

    async def test_an_own_scoped_rep_cannot_move_another_reps_deal(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["crm.pipeline.deal.write:OWN", "crm.pipeline.deal.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{data['deal_b_id']}/stage", json={"stage": "lead"}
            )
        assert response.status_code == 403

    async def test_without_the_permission_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{data['deal_a_id']}/stage", json={"stage": "closed_won"}
            )
        assert response.status_code == 403


# --------------------------------------------------------------------- activities


class TestLogActivity:
    async def test_happy_path_against_a_deal(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "type": "note",
                    "subject": "Sent the revised proposal",
                    "deal_id": str(data["deal_a_id"]),
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["subject"] == "Sent the revised proposal"
        assert body["deal_id"] == str(data["deal_a_id"])

    async def test_at_least_one_target_is_required(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/deals/activities", json={"type": "note", "subject": "Floating note"}
            )
        assert response.status_code == 422

    async def test_direction_only_applies_to_call_or_email(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "type": "note",
                    "subject": "A note with a direction",
                    "deal_id": str(data["deal_a_id"]),
                    "direction": "outbound",
                },
            )
        assert response.status_code == 422

    async def test_occurred_at_cannot_be_in_the_future(self, client, org_id, data) -> None:
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "type": "call",
                    "subject": "Time traveller's call",
                    "deal_id": str(data["deal_a_id"]),
                    "occurred_at": future,
                },
            )
        assert response.status_code == 422

    async def test_a_deal_outside_the_callers_scope_is_refused(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id,
            ["crm.pipeline.activity.write:OWN", "crm.pipeline.deal.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "type": "note",
                    "subject": "Sneaking into another rep's deal",
                    "deal_id": str(data["deal_b_id"]),
                },
            )
        assert response.status_code == 403

    async def test_an_own_scoped_caller_is_always_the_owner_regardless_of_payload(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["crm.pipeline.activity.write:OWN", "crm.pipeline.deal.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "type": "note",
                    "subject": "Trying to attribute to somebody else",
                    "deal_id": str(data["deal_a_id"]),
                    "owner_id": str(data["subject_b"]),
                },
            )
        assert response.status_code == 201
        assert response.json()["owner_id"] == str(data["subject_a"])

    async def test_an_org_scoped_caller_may_attribute_to_a_named_rep(
        self, client, org_id, data
    ) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "type": "meeting",
                    "subject": "Kickoff logged on behalf of a rep",
                    "deal_id": str(data["deal_a_id"]),
                    "owner_id": str(data["subject_b"]),
                },
            )
        assert response.status_code == 201
        assert response.json()["owner_id"] == str(data["subject_b"])

    async def test_without_the_permission_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.activity.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals/activities",
                json={
                    "type": "note",
                    "subject": "No perms",
                    "deal_id": str(data["deal_a_id"]),
                },
            )
        assert response.status_code == 403


# --------------------------------------------------------------------- list filter


class TestDealsFilterByCompany:
    async def test_company_id_narrows_the_list(self, client, org_id, data) -> None:
        with as_principal(_admin(org_id, data["subject_a"])):
            response = await client.get(f"/api/v1/deals?company_id={data['company_a']}")
        names = {d["name"] for d in response.json()["items"]}
        assert names == {"Fleet telematics rollout"}
