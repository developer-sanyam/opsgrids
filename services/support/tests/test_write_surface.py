"""The HTTP write surface: opening a ticket, and maintaining the desk's reference data.

The write logic itself is covered by `test_authz.py` and the seed test — what is proven here is
the *route*: that it reaches the real write path (the rows land, the audit row carries the
request, the event is staged), and that the guard in front of it actually refuses.

Every endpoint carries a negative-authz case, and they are deliberately not "a caller with no
permissions is refused". The interesting boundary is the one this slice introduced: the roster
and the SLA policy are administration, so a caller who holds the full queue-working grant —
`support.desk.ticket.write`, enough to reply to and assign every ticket in the organization — is
still refused both. A test that only proved an empty principal is refused would pass even if the
new routes were guarded by `ticket.write`.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import func, select

from app import selectors, services
from app.main import app
from app.models import Agent, AuditLog, OutboxEvent, SlaPolicy
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


READ = "support.desk.ticket.read"
TICKET_WRITE = "support.desk.ticket.write"
AGENT_WRITE = "support.desk.agent.write"
SLA_WRITE = "support.desk.sla_policy.write"


def _holding(org_id: uuid.UUID, *permissions: str, scope: str = "ORG") -> Principal:
    return principal_for(org_id, [f"{p}:{scope}" for p in permissions])


TICKET_PAYLOAD: dict[str, Any] = {
    "number": "TK-7001",
    "subject": "Invoice PDF fails to download",
    "requester": {
        "name": "Marta Ruiz",
        "email": "marta@atlas.example",
        "company": "Atlas Freight",
    },
    "priority": "high",
    "channel": "email",
    "tags": ["billing", "exports"],
    "messages": [
        {"author_type": "customer", "author_name": "Marta Ruiz", "body": "The PDF 404s."},
    ],
}

AGENT_PAYLOAD: dict[str, Any] = {
    "name": "Rosa Klein",
    "email": "rosa@opsgrids.example.com",
    "role": "senior_agent",
    "team": "Billing",
    "open_load": 4,
    "avg_first_response_min": 21,
    "csat_pct": 91,
    "solved_7d": 12,
    "online": True,
}

POLICY_PAYLOAD: dict[str, Any] = {
    "name": "Standard desk SLA",
    "business_hours": "09:00-18:00 IST, Mon-Fri",
    "coverage": "Urgent tickets are worked around the clock; everything else in hours.",
    "targets": [
        {"priority": "urgent", "first_response_min": 30, "resolution_min": 240},
        {"priority": "normal", "first_response_min": 240, "resolution_min": 1440},
    ],
}

#: The three endpoints this slice added, with a payload each.
NEW_WRITE_ROUTES = [
    ("POST", "/api/v1/support/tickets", TICKET_PAYLOAD),
    ("PUT", "/api/v1/support/agents/ag-09", AGENT_PAYLOAD),
    ("PUT", "/api/v1/support/sla-policy", POLICY_PAYLOAD),
]

#: A principal holding every write permission the desk has — the caller these routes exist for.
ALL_WRITES = (READ, TICKET_WRITE, AGENT_WRITE, SLA_WRITE)


async def _count(session, model, org_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(model).where(model.organization_id == org_id)
        )
    ).scalar_one()


class TestOpenTicket:
    async def test_opens_a_ticket_with_its_thread_and_persists_it(
        self, client, session, org_id
    ) -> None:
        with as_principal(_holding(org_id, READ, TICKET_WRITE)):
            res = await client.post("/api/v1/support/tickets", json=TICKET_PAYLOAD)

        assert res.status_code == 201
        body = res.json()
        assert body["id"] == "TK-7001"
        assert body["status"] == "new"  # the lifecycle owns this, not the payload
        assert body["assignee_id"] is None
        assert body["requester"]["company"] == "Atlas Freight"
        assert [m["id"] for m in body["messages"]] == ["m1"]

        async with session.begin():
            ticket = await selectors.get_ticket(session, org_id, "TK-7001")
            assert ticket is not None
            assert ticket.subject == "Invoice PDF fails to download"
            assert ticket.priority == "high"
            assert ticket.tags == ["billing", "exports"]
            stored = await selectors.get_messages(session, org_id, ticket.id)
        assert [(m.ref, m.body) for m in stored] == [("m1", "The PDF 404s.")]

    async def test_opening_a_ticket_audits_the_request_and_stages_the_event(
        self, client, session, org_id
    ) -> None:
        """The route reaches the real write path, and threads the request into the audit row.

        `request_id` is stamped by the middleware onto the live request, so a route that forgot
        to pass `request=` through would leave it null here.
        """
        principal = _holding(org_id, READ, TICKET_WRITE)
        with as_principal(principal):
            res = await client.post("/api/v1/support/tickets", json=TICKET_PAYLOAD)
        assert res.status_code == 201

        async with session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.organization_id == org_id)
                    )
                )
                .scalars()
                .all()
            )
            audit = (
                (await session.execute(select(AuditLog).where(AuditLog.organization_id == org_id)))
                .scalars()
                .all()
            )

        assert [e.event_type for e in events] == [services.EVT_TICKET_CREATED]
        assert events[0].payload["number"] == "TK-7001"
        assert [a.action for a in audit] == ["ticket.created"]
        assert audit[0].actor_id == principal.sub
        assert audit[0].request_id is not None

    async def test_a_repeated_number_is_a_conflict(self, client, org_id, queue) -> None:
        with as_principal(_holding(org_id, READ, TICKET_WRITE)):
            res = await client.post(
                "/api/v1/support/tickets", json={**TICKET_PAYLOAD, "number": "TK-0001"}
            )
        assert res.status_code == 409

    async def test_a_reader_cannot_open_a_ticket(self, client, session, org_id) -> None:
        with as_principal(_holding(org_id, READ)):
            res = await client.post("/api/v1/support/tickets", json=TICKET_PAYLOAD)
        assert res.status_code == 403
        async with session.begin():
            assert await selectors.get_ticket(session, org_id, "TK-7001") is None

    async def test_below_org_scope_cannot_open_a_ticket(self, client, org_id) -> None:
        """The shared queue writes at ORG; an OWN grant is not enough."""
        with as_principal(_holding(org_id, TICKET_WRITE, scope="OWN")):
            res = await client.post("/api/v1/support/tickets", json=TICKET_PAYLOAD)
        assert res.status_code == 403

    async def test_a_blank_subject_is_rejected(self, client, org_id) -> None:
        with as_principal(_holding(org_id, READ, TICKET_WRITE)):
            res = await client.post(
                "/api/v1/support/tickets", json={**TICKET_PAYLOAD, "subject": ""}
            )
        assert res.status_code == 422

    async def test_the_payload_cannot_assign_the_ticket(self, client, org_id, queue) -> None:
        """Assignment goes through the assign route, which refuses an agent outside the
        organization. Accepting it here would be the one way to assign to a ghost, so the
        payload refuses the field outright rather than ignoring it."""
        with as_principal(_holding(org_id, READ, TICKET_WRITE)):
            res = await client.post(
                "/api/v1/support/tickets", json={**TICKET_PAYLOAD, "assignee_id": "ag-01"}
            )
        assert res.status_code == 422


class TestPutAgent:
    async def test_creates_then_updates_the_same_agent(self, client, session, org_id) -> None:
        with as_principal(_holding(org_id, READ, AGENT_WRITE)):
            created = await client.put("/api/v1/support/agents/ag-09", json=AGENT_PAYLOAD)
            updated = await client.put(
                "/api/v1/support/agents/ag-09",
                json={**AGENT_PAYLOAD, "team": "Tier 2", "open_load": 9, "online": False},
            )

        assert created.status_code == 200
        assert created.json()["id"] == "ag-09"
        assert created.json()["team"] == "Billing"
        assert updated.status_code == 200
        assert updated.json()["team"] == "Tier 2"
        assert updated.json()["open_load"] == 9

        # Idempotent on the ref: two PUTs, one agent.
        async with session.begin():
            assert await _count(session, Agent, org_id) == 1
            agent = await selectors.get_agent(session, org_id, "ag-09")
            assert agent is not None
            assert agent.team == "Tier 2"
            assert agent.online is False

    async def test_a_new_agent_shows_up_in_the_roster(self, client, org_id, queue) -> None:
        with as_principal(_holding(org_id, READ, AGENT_WRITE)):
            await client.put("/api/v1/support/agents/ag-09", json=AGENT_PAYLOAD)
            listed = await client.get("/api/v1/support/agents")
        assert {a["id"] for a in listed.json()} == {"ag-01", "ag-02", "ag-09"}

    async def test_working_the_queue_does_not_include_the_roster(
        self, client, session, org_id
    ) -> None:
        """`ticket.write` replies and assigns. It does not rewrite who the agents are."""
        with as_principal(_holding(org_id, READ, TICKET_WRITE)):
            res = await client.put("/api/v1/support/agents/ag-09", json=AGENT_PAYLOAD)
        assert res.status_code == 403
        async with session.begin():
            assert await selectors.get_agent(session, org_id, "ag-09") is None

    async def test_below_org_scope_cannot_write_the_roster(self, client, org_id) -> None:
        with as_principal(_holding(org_id, AGENT_WRITE, scope="OWN")):
            res = await client.put("/api/v1/support/agents/ag-09", json=AGENT_PAYLOAD)
        assert res.status_code == 403

    async def test_an_impossible_csat_is_rejected(self, client, org_id) -> None:
        with as_principal(_holding(org_id, READ, AGENT_WRITE)):
            res = await client.put(
                "/api/v1/support/agents/ag-09", json={**AGENT_PAYLOAD, "csat_pct": 140}
            )
        assert res.status_code == 422


class TestPutSlaPolicy:
    async def test_sets_the_policy_and_the_queue_reads_it_back(
        self, client, session, org_id
    ) -> None:
        with as_principal(_holding(org_id, READ, SLA_WRITE)):
            res = await client.put("/api/v1/support/sla-policy", json=POLICY_PAYLOAD)
            read_back = await client.get("/api/v1/support/sla-policy")

        assert res.status_code == 200
        assert res.json()["name"] == "Standard desk SLA"
        assert read_back.status_code == 200
        assert read_back.json() == res.json()

        async with session.begin():
            policy = await selectors.get_sla_policy(session, org_id)
        assert policy is not None
        assert [t["priority"] for t in policy.targets] == ["urgent", "normal"]

    async def test_a_second_put_replaces_the_policy_whole(self, client, session, org_id) -> None:
        replacement = {
            **POLICY_PAYLOAD,
            "name": "Weekend cover",
            "targets": [{"priority": "urgent", "first_response_min": 15, "resolution_min": 120}],
        }
        with as_principal(_holding(org_id, READ, SLA_WRITE)):
            await client.put("/api/v1/support/sla-policy", json=POLICY_PAYLOAD)
            second = await client.put("/api/v1/support/sla-policy", json=replacement)

        assert second.status_code == 200
        # Replaced, not merged or appended — and still exactly one policy for the organization.
        assert second.json()["name"] == "Weekend cover"
        assert [t["priority"] for t in second.json()["targets"]] == ["urgent"]
        async with session.begin():
            assert await _count(session, SlaPolicy, org_id) == 1

    async def test_working_the_queue_does_not_include_the_policy(
        self, client, session, org_id
    ) -> None:
        """Moving the targets the desk is measured against is the administrator's job."""
        with as_principal(_holding(org_id, READ, TICKET_WRITE)):
            res = await client.put("/api/v1/support/sla-policy", json=POLICY_PAYLOAD)
        assert res.status_code == 403
        async with session.begin():
            assert await selectors.get_sla_policy(session, org_id) is None

    async def test_below_org_scope_cannot_set_the_policy(self, client, org_id) -> None:
        with as_principal(_holding(org_id, SLA_WRITE, scope="OWN")):
            res = await client.put("/api/v1/support/sla-policy", json=POLICY_PAYLOAD)
        assert res.status_code == 403

    async def test_a_target_on_an_unknown_priority_is_rejected(self, client, org_id) -> None:
        with as_principal(_holding(org_id, READ, SLA_WRITE)):
            res = await client.put(
                "/api/v1/support/sla-policy",
                json={
                    **POLICY_PAYLOAD,
                    "targets": [
                        {"priority": "whenever", "first_response_min": 30, "resolution_min": 240}
                    ],
                },
            )
        assert res.status_code == 422


class TestEntitlement:
    @pytest.mark.parametrize(("method", "path", "body"), NEW_WRITE_ROUTES)
    async def test_missing_entitlement_is_payment_required(
        self, client, org_id, method, path, body
    ) -> None:
        """Permissions are fine, the subscription is not — 402, and nothing is written."""
        principal = principal_for(org_id, [f"{p}:ORG" for p in ALL_WRITES], entitlements=[])
        with as_principal(principal):
            res = await client.request(method, path, json=body)
        assert res.status_code == 402
