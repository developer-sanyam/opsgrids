"""The pipeline's write surface, over HTTP.

The write logic itself is proven elsewhere (`test_stage_transitions.py`,
`test_activity_append_only.py`, the validation half of `test_authz.py`) — what these
tests hold is the route in front of it: that the permission guarding each endpoint is the
*write* one and not merely "some CRM permission", that object-level scope is re-applied
before a named deal is moved or logged against, that the body cannot carry a field the
server owns, and that a refused call leaves nothing behind.

Every negative case asserts the state as well as the status: a 403 that still wrote the
row would pass a status-only assertion.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.main import app
from app.models import AuditLog, Deal, OutboxEvent
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


async def _deal_count(session: AsyncSession) -> int:
    async with session.begin():
        return (await session.execute(select(func.count()).select_from(Deal))).scalar_one()


async def _reload(session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID) -> Deal:
    """Read a deal back in a fresh transaction — the request committed and expired the
    fixture's instances, so an assertion has to go back to the database for the truth."""
    async with session.begin():
        deal = await selectors.get_deal(session, org_id, deal_id)
    assert deal is not None
    return deal


# --------------------------------------------------------------------- POST /deals


class TestCreateDeal:
    async def test_a_writer_creates_a_deal(self, client, session, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.deal.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Warehouse WMS rollout",
                    "company_id": str(data["company_a"]),
                    "value_minor": 4_500_000 * 100,
                    "close_date": "2026-11-30",
                    "stage": "qualified",
                    "source": "Inbound",
                },
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["name"] == "Warehouse WMS rollout"
        assert body["stage"] == "qualified"
        # Neither sent: the owner is the calling rep, the probability the stage's default.
        assert body["owner_id"] == str(data["subject_a"])
        assert body["probability"] == 40
        assert body["value_minor"] == 4_500_000 * 100
        assert isinstance(body["value_minor"], int)

        deal = await _reload(session, org_id, uuid.UUID(body["id"]))
        assert deal.name == "Warehouse WMS rollout"
        assert deal.value_minor == 4_500_000 * 100
        assert deal.currency == "INR"
        assert deal.organization_id == org_id

    async def test_the_audit_row_and_the_event_are_staged_by_the_route(
        self, client, session, org_id, data
    ) -> None:
        """The HTTP path produces the same audit row and outbox event the seeder does, and
        `request=request` is what puts the call's origin on the audit row."""
        principal = principal_for(
            org_id, ["crm.pipeline.deal.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Audited over http",
                    "company_id": str(data["company_a"]),
                    "value_minor": 100_00,
                    "close_date": "2026-12-01",
                },
            )
        assert response.status_code == 201, response.text
        deal_id = uuid.UUID(response.json()["id"])

        async with session.begin():
            audit = (
                await session.execute(select(AuditLog).where(AuditLog.entity_id == deal_id))
            ).scalar_one()
            event = (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.aggregate_id == deal_id)
                )
            ).scalar_one()
        assert audit.action == "deal.created"
        assert audit.actor_id == principal.sub
        # Origin, not just the fact of the change — this is the `request` pass-through.
        assert audit.request_id is not None
        assert event.event_type == "crm.pipeline.deal.created"
        assert event.published_at is None  # staged, never published inline

    async def test_a_reader_cannot_create_a_deal(self, client, session, org_id, data) -> None:
        """The negative-authz case CLAUDE.md requires: deal.read is not deal.write."""
        before = await _deal_count(session)
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Snuck in",
                    "company_id": str(data["company_a"]),
                    "value_minor": 1,
                    "close_date": "2026-12-01",
                },
            )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")
        assert await _deal_count(session) == before

    async def test_an_own_writer_cannot_create_a_deal_for_another_rep(
        self, client, session, org_id, data
    ) -> None:
        """Scope is re-applied to the owner in the body: an OWN-scoped rep assigning a deal
        to a colleague would otherwise write a row they cannot read."""
        before = await _deal_count(session)
        principal = principal_for(
            org_id, ["crm.pipeline.deal.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Assigned elsewhere",
                    "company_id": str(data["company_a"]),
                    "owner_id": str(data["subject_b"]),
                    "value_minor": 1,
                    "close_date": "2026-12-01",
                },
            )
        assert response.status_code == 403
        assert await _deal_count(session) == before

    async def test_a_caller_with_no_rep_identity_must_name_an_owner(
        self, client, org_id, data
    ) -> None:
        """Nobody to attribute the deal to is a 422, not a row owned by nothing."""
        principal = principal_for(org_id, ["crm.pipeline.deal.write:ORG"], subject_ref=None)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/deals",
                json={
                    "name": "Ownerless",
                    "company_id": str(data["company_a"]),
                    "value_minor": 1,
                    "close_date": "2026-12-01",
                },
            )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("value_minor", -1),  # money is a non-negative integer of minor units
            ("value_minor", 12.5),  # and never a float
            ("name", ""),
            ("stage", "galaxy_brain"),
            ("currency", "RUPEES"),
            ("probability", 140),
        ],
    )
    async def test_an_invalid_body_is_refused(
        self, client, session, org_id, data, field, value
    ) -> None:
        before = await _deal_count(session)
        principal = principal_for(
            org_id, ["crm.pipeline.deal.write:ORG"], subject_ref=data["subject_a"]
        )
        payload: dict = {
            "name": "Malformed",
            "company_id": str(data["company_a"]),
            "value_minor": 1,
            "close_date": "2026-12-01",
        }
        payload[field] = value
        with as_principal(principal):
            response = await client.post("/api/v1/deals", json=payload)
        assert response.status_code == 422, f"{field}={value!r}"
        assert await _deal_count(session) == before


# --------------------------------------------------------------------- POST /deals/{id}/stage


class TestMoveStage:
    async def test_a_writer_moves_a_deal(self, client, session, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.deal.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{data['deal_a_id']}/stage", json={"stage": "closed_won"}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stage"] == "closed_won"
        assert body["probability"] == 100  # won pins to 100 without being asked

        deal = await _reload(session, org_id, data["deal_a_id"])
        assert deal.stage == "closed_won"

    async def test_a_reader_cannot_move_a_deal(self, client, session, org_id, data) -> None:
        """The required negative-authz case: reading the board does not move it."""
        principal = principal_for(
            org_id, ["crm.pipeline.deal.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{data['deal_a_id']}/stage", json={"stage": "closed_won"}
            )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")
        assert (await _reload(session, org_id, data["deal_a_id"])).stage == "negotiation"

    async def test_an_own_writer_cannot_move_another_reps_deal(
        self, client, session, org_id, data
    ) -> None:
        """Object-level scope on the mutate path, not only on the detail read."""
        principal = principal_for(
            org_id, ["crm.pipeline.deal.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{data['deal_b_id']}/stage", json={"stage": "lead"}
            )
        assert response.status_code == 403
        assert (await _reload(session, org_id, data["deal_b_id"])).stage == "closed_won"

    async def test_an_unknown_stage_is_refused(self, client, session, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.deal.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{data['deal_a_id']}/stage", json={"stage": "galaxy_brain"}
            )
        assert response.status_code == 422
        assert (await _reload(session, org_id, data["deal_a_id"])).stage == "negotiation"

    async def test_a_closed_deal_moves_nowhere(self, client, session, org_id, data) -> None:
        """The transition rule reaches the wire: reopening a decided deal is a 409, not a
        silent edit."""
        principal = principal_for(
            org_id, ["crm.pipeline.deal.write:ORG"], subject_ref=data["subject_b"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{data['deal_b_id']}/stage", json={"stage": "negotiation"}
            )
        assert response.status_code == 409
        assert (await _reload(session, org_id, data["deal_b_id"])).stage == "closed_won"

    async def test_another_tenants_deal_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.deal.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{uuid.uuid4()}/stage", json={"stage": "lead"}
            )
        assert response.status_code == 404


# --------------------------------------------------------------------- POST /deals/{id}/activities


class TestLogActivity:
    async def test_a_writer_logs_a_touch(self, client, session, org_id, data) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.activity.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{data['deal_a_id']}/activities",
                json={
                    "type": "call",
                    "subject": "Pricing follow-up",
                    "body": "Agreed to a 30-vehicle pilot.",
                    "direction": "outbound",
                    "occurred_at": "2026-09-01T10:30:00Z",
                },
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["type"] == "call"
        assert body["subject"] == "Pricing follow-up"
        assert body["deal_id"] == str(data["deal_a_id"])
        # The company is the deal's, not the body's — an entry cannot name two accounts.
        assert body["company_id"] == str(data["company_a"])
        assert body["owner_id"] == str(data["subject_a"])

        async with session.begin():
            rows = await selectors.activities_for_deal(session, org_id, data["deal_a_id"])
        assert [a.subject for a in rows] == [
            "Pricing follow-up",
            "Discovery call on telematics scope",
        ]

        # Logging advances the staleness clock the Overview reads.
        deal = await _reload(session, org_id, data["deal_a_id"])
        assert deal.last_activity_at is not None
        assert deal.last_activity_at.isoformat().startswith("2026-09-01T10:30")

    async def test_a_reader_cannot_log_a_touch(self, client, session, org_id, data) -> None:
        """The required negative-authz case: activity.read reads the timeline, it does not
        append to it — and the append-only table makes a wrong entry permanent."""
        principal = principal_for(
            org_id,
            ["crm.pipeline.activity.read:ORG", "crm.pipeline.deal.read:ORG"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{data['deal_a_id']}/activities",
                json={"type": "note", "subject": "Snuck in"},
            )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

        async with session.begin():
            rows = await selectors.activities_for_deal(session, org_id, data["deal_a_id"])
        assert [a.subject for a in rows] == ["Discovery call on telematics scope"]

    async def test_an_own_writer_cannot_log_against_another_reps_deal(
        self, client, session, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.activity.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{data['deal_b_id']}/activities",
                json={"type": "note", "subject": "Not my deal"},
            )
        assert response.status_code == 403

        async with session.begin():
            rows = await selectors.activities_for_deal(session, org_id, data["deal_b_id"])
        assert [a.subject for a in rows] == ["Kickoff - Harbor & Co go-live"]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("subject", ""),
            ("type", "telepathy"),
            ("direction", "sideways"),
            ("occurred_at", "whenever"),
        ],
    )
    async def test_an_invalid_body_is_refused(
        self, client, session, org_id, data, field, value
    ) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.activity.write:ORG"], subject_ref=data["subject_a"]
        )
        payload: dict = {"type": "note", "subject": "Malformed"}
        payload[field] = value
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{data['deal_a_id']}/activities", json=payload
            )
        assert response.status_code == 422, f"{field}={value!r}"

        async with session.begin():
            rows = await selectors.activities_for_deal(session, org_id, data["deal_a_id"])
        assert len(rows) == 1

    async def test_logging_against_another_tenants_deal_is_not_found(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["crm.pipeline.activity.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/deals/{uuid.uuid4()}/activities",
                json={"type": "note", "subject": "Ghost"},
            )
        assert response.status_code == 404
