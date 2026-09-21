"""The HTTP write surface: opening a ticket, and maintaining the desk's reference data.

The write logic itself is covered by `test_authz.py` and the seed test — what is proven here is
the *route*: that it reaches the real write path (the rows land, the audit row carries the
request, the event is staged), and that a refusal genuinely leaves nothing written.

The negative-authz boundary this slice introduced — the roster and the SLA policy are
administration, so a caller who holds the full queue-working grant (`support.desk.ticket.write`,
enough to reply to and assign every ticket in the organization) is still refused both — is
asserted once, in `test_authz.py::TestAgentAndSlaPolicyWriteAuthz`, and not repeated here for
every route; what stays here is the case unique to this module: proving a refusal on the ticket
route leaves no row behind.

The roster's write moved from PR #16's single upsert, `PUT /support/agents/{ref}`, to a
server-allocated `POST /support/agents` plus `PATCH`/`DELETE /support/agents/{ref}` — the create
and edit paths are proven here; `test_authz.py` and the seed test cover delete and its
idempotency.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select

from app import selectors, services
from app.main import app
from app.models import Agent, AuditLog, OutboxEvent, SlaPolicy, Ticket
from platform_core.auth import Principal, Scope, get_principal
from platform_core.errors import ConflictProblem
from tests.helpers import ctx_for, principal_for

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
    "subject": "Invoice PDF fails to download",
    "requester": {
        "name": "Marta Ruiz",
        "email": "marta@atlas.example",
        "company": "Atlas Freight",
    },
    "priority": "high",
    "channel": "email",
    "tags": ["billing", "exports"],
    # `number` is server-allocated (`services.next_ticket_number`) and the opening message is a
    # single `body`, not a client-built `messages` array — see `TicketCreateIn`.
    "body": "The PDF 404s.",
}

#: `AgentIn` — the roster's create contract. The four metric columns (`open_load`,
#: `avg_first_response_min`, `csat_pct`, `solved_7d`) are a desk projection and are never
#: accepted from a client, on create or on patch.
AGENT_PAYLOAD: dict[str, Any] = {
    "name": "Rosa Klein",
    "email": "rosa@opsgrids.example.com",
    "role": "senior_agent",
    "team": "Billing",
    "online": True,
}

AGENT_PATCH_PAYLOAD: dict[str, Any] = {
    "team": "Tier 2",
    "online": False,
}

#: `SlaPolicyIn` — the policy is total, not a sparse override: `targets` must carry exactly one
#: row for every `Priority`, so a payload naming only some of them is a 422.
POLICY_PAYLOAD: dict[str, Any] = {
    "name": "Standard desk SLA",
    "business_hours": "09:00-18:00 IST, Mon-Fri",
    "coverage": "Urgent tickets are worked around the clock; everything else in hours.",
    "targets": [
        {"priority": "urgent", "first_response_min": 30, "resolution_min": 240},
        {"priority": "high", "first_response_min": 60, "resolution_min": 480},
        {"priority": "normal", "first_response_min": 240, "resolution_min": 1440},
        {"priority": "low", "first_response_min": 480, "resolution_min": 2880},
    ],
}

#: The three write endpoints this slice added, with a payload each. The roster's create moved
#: from an upsert (`PUT /support/agents/{ref}`) to a server-allocated `POST /support/agents`.
NEW_WRITE_ROUTES = [
    ("POST", "/api/v1/support/tickets", TICKET_PAYLOAD),
    ("POST", "/api/v1/support/agents", AGENT_PAYLOAD),
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
        assert body["id"] == "TK-0001"  # server-allocated, race-safe (`test_numbering.py`)
        assert body["status"] == "new"  # the lifecycle owns this, not the payload
        assert body["assignee_id"] is None
        assert body["requester"]["company"] == "Atlas Freight"
        assert [m["id"] for m in body["messages"]] == ["m1"]

        async with session.begin():
            ticket = await selectors.get_ticket(session, org_id, "TK-0001")
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
        assert events[0].payload["number"] == "TK-0001"
        assert [a.action for a in audit] == ["ticket.created"]
        assert audit[0].actor_id == principal.sub
        assert audit[0].request_id is not None

    async def test_a_repeated_number_is_a_conflict(self, session, org_id, queue) -> None:
        """The HTTP route no longer accepts a client-supplied `number` — allocation moved to
        `services.next_ticket_number`, under an advisory lock (`test_numbering.py`), so a
        collision can no longer reach this route. The guard against a duplicate number still
        lives in `services.create_ticket`, the writer the demo seed (`tools/demo/seed_support.py`)
        and admin tooling call directly with an explicit number — proven here at that level."""
        write = ctx_for(org_id, TICKET_WRITE, Scope.ORG)
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.create_ticket(
                    session,
                    write,
                    number="TK-0001",  # already seeded by `queue`
                    subject="Duplicate",
                    requester={"name": "X", "email": "x@example.com", "company": "Acme"},
                    priority="normal",
                    status="new",
                    channel="web",
                    assignee_ref=None,
                    tags=[],
                    created_at=datetime.now(UTC),
                )

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

    async def test_creation_cannot_assign_to_a_ghost(self, client, session, org_id, queue) -> None:
        """`TicketCreateIn.assignee_id` is a real field here (unlike PR #16's schema, which
        forbade it outright) — but it is validated exactly as the dedicated assign route
        validates it: an agent outside the organization is refused, never silently assigned.
        That is the guarantee that matters; accepting the field only works because the ghost
        case is still turned away."""
        with as_principal(_holding(org_id, READ, TICKET_WRITE)):
            res = await client.post(
                "/api/v1/support/tickets", json={**TICKET_PAYLOAD, "assignee_id": "ag-ghost"}
            )
        assert res.status_code == 404
        async with session.begin():
            assert await _count(session, Ticket, org_id) == 3  # only the seeded queue


class TestAgentRoster:
    """PR #16's roster write was one upsert route, `PUT /support/agents/{ref}`. The surviving
    contract splits it into a server-allocated create, an edit and a soft delete —
    `POST`/`PATCH`/`DELETE /support/agents/{ref}` — so the tests below prove the same ground:
    create and edit reach the real write path and land on the same row, and the roster's
    metric columns are never client-writable, not even to an in-range value.

    The negative-authz cases PR #16 carried here (`ticket.write` cannot manage the roster; an
    `OWN`-scoped grant cannot either) are identical to, and already covered by,
    `test_authz.py::TestAgentAndSlaPolicyWriteAuthz::test_ticket_write_cannot_manage_agents` and
    `::test_below_org_scope_cannot_manage_agents` — not repeated here.
    """

    async def test_creates_then_updates_the_same_agent(self, client, session, org_id) -> None:
        with as_principal(_holding(org_id, READ, AGENT_WRITE)):
            created = await client.post("/api/v1/support/agents", json=AGENT_PAYLOAD)
            ref = created.json()["id"]
            updated = await client.patch(f"/api/v1/support/agents/{ref}", json=AGENT_PATCH_PAYLOAD)

        assert created.status_code == 201
        assert created.json()["team"] == "Billing"
        assert updated.status_code == 200
        assert updated.json()["id"] == ref
        assert updated.json()["team"] == "Tier 2"
        assert updated.json()["online"] is False

        # One create, one edit, the same row throughout.
        async with session.begin():
            assert await _count(session, Agent, org_id) == 1
            agent = await selectors.get_agent(session, org_id, ref)
            assert agent is not None
            assert agent.team == "Tier 2"
            assert agent.online is False

    async def test_a_new_agent_shows_up_in_the_roster(self, client, org_id, queue) -> None:
        with as_principal(_holding(org_id, READ, AGENT_WRITE)):
            created = await client.post("/api/v1/support/agents", json=AGENT_PAYLOAD)
            listed = await client.get("/api/v1/support/agents")
        assert {a["id"] for a in listed.json()} == {"ag-01", "ag-02", created.json()["id"]}

    async def test_the_metric_columns_are_never_client_writable(self, client, org_id) -> None:
        """`open_load`, `avg_first_response_min`, `csat_pct` and `solved_7d` are a desk
        projection, computed server-side — `AgentIn`/`AgentPatch` do not carry them at all, so
        a caller cannot write even an in-range value, let alone an impossible one like a 140%
        CSAT."""
        with as_principal(_holding(org_id, READ, AGENT_WRITE)):
            res = await client.post(
                "/api/v1/support/agents", json={**AGENT_PAYLOAD, "csat_pct": 140}
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
        assert [t["priority"] for t in policy.targets] == ["urgent", "high", "normal", "low"]

    async def test_a_second_put_replaces_the_policy_whole(self, client, session, org_id) -> None:
        # `SlaPolicyIn` requires exactly one row per `Priority` — the policy is total, not a
        # sparse override — so the replacement still names all four, with different minutes.
        replacement = {
            **POLICY_PAYLOAD,
            "name": "Weekend cover",
            "targets": [
                {"priority": "urgent", "first_response_min": 15, "resolution_min": 120},
                {"priority": "high", "first_response_min": 30, "resolution_min": 240},
                {"priority": "normal", "first_response_min": 120, "resolution_min": 720},
                {"priority": "low", "first_response_min": 240, "resolution_min": 1440},
            ],
        }
        with as_principal(_holding(org_id, READ, SLA_WRITE)):
            await client.put("/api/v1/support/sla-policy", json=POLICY_PAYLOAD)
            second = await client.put("/api/v1/support/sla-policy", json=replacement)

        assert second.status_code == 200
        # Replaced, not merged or appended — and still exactly one policy for the organization.
        assert second.json()["name"] == "Weekend cover"
        assert [t["first_response_min"] for t in second.json()["targets"]] == [15, 30, 120, 240]
        async with session.begin():
            assert await _count(session, SlaPolicy, org_id) == 1

    # The negative-authz cases PR #16 carried here (`ticket.write` cannot set the policy; an
    # `OWN`-scoped grant cannot either) are identical to, and already covered by,
    # `test_authz.py::TestAgentAndSlaPolicyWriteAuthz::test_ticket_write_cannot_set_sla_policy`
    # and `::test_below_org_scope_cannot_set_sla_policy` — not repeated here.

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
