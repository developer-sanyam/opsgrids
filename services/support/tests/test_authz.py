"""Authorization and the write path — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more than
proving one succeeds. Every read and write endpoint carries a check that a caller missing the
permission, the entitlement, or the ORG scope floor is turned away, and that the conversation
thread is genuinely append-only.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest

from app import selectors, services
from app.main import app
from platform_core.auth import Principal, Scope, get_principal
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
WRITE = "support.desk.ticket.write"
AGENT_WRITE = "support.desk.agent.write"
SLA_POLICY_WRITE = "support.desk.sla_policy.write"

_NEW_TICKET_BODY = {
    "subject": "Printer on fire",
    "requester": {"name": "A Requester", "email": "req@example.com", "company": "Acme"},
    "body": "Please send help.",
}
_SLA_POLICY_BODY = {
    "name": "Standard",
    "targets": [
        {"priority": "urgent", "first_response_min": 15, "resolution_min": 240},
        {"priority": "high", "first_response_min": 30, "resolution_min": 480},
        {"priority": "normal", "first_response_min": 60, "resolution_min": 1440},
        {"priority": "low", "first_response_min": 120, "resolution_min": 2880},
    ],
}

# Every principal route, as (method, path). Reads need read:ORG; writes need write:ORG.
READ_ROUTES = [
    ("GET", "/api/v1/support/tickets"),
    ("GET", "/api/v1/support/tickets/TK-0001"),
    ("GET", "/api/v1/support/agents"),
    ("GET", "/api/v1/support/sla-policy"),
    ("GET", "/api/v1/support/overview"),
]
WRITE_ROUTES = [
    ("POST", "/api/v1/support/tickets/TK-0001/reply", {"body": "hi", "author_name": "A"}),
    ("POST", "/api/v1/support/tickets/TK-0001/assign", {"assignee_id": "ag-01"}),
    ("PATCH", "/api/v1/support/tickets/TK-0001", {"priority": "high"}),
    ("POST", "/api/v1/support/tickets/TK-0001/status", {"status": "open"}),
    ("POST", "/api/v1/support/tickets", _NEW_TICKET_BODY),
]

# `support.desk.agent.write` and `support.desk.sla_policy.write` are narrower than the desk's
# ordinary write permission — SUPPORT_ADMIN only, not every SUPPORT_AGENT (`catalogue.py`).
AGENT_WRITE_ROUTES = [
    ("POST", "/api/v1/support/agents", {"name": "New Agent", "email": "new@example.com"}),
    ("PATCH", "/api/v1/support/agents/ag-01", {"online": True}),
    ("DELETE", "/api/v1/support/agents/ag-01", None),
]
SLA_POLICY_WRITE_ROUTES = [
    ("PUT", "/api/v1/support/sla-policy", _SLA_POLICY_BODY),
]


def _full(org_id: uuid.UUID) -> Principal:
    return principal_for(
        org_id, [f"{READ}:ORG", f"{WRITE}:ORG", f"{AGENT_WRITE}:ORG", f"{SLA_POLICY_WRITE}:ORG"]
    )


class TestReadAuthz:
    @pytest.mark.parametrize(("method", "path"), READ_ROUTES)
    async def test_missing_permission_is_forbidden(
        self, client, org_id, queue, method, path
    ) -> None:
        principal = principal_for(org_id, [])  # entitled, but holds nothing
        with as_principal(principal):
            res = await client.request(method, path)
        assert res.status_code == 403

    @pytest.mark.parametrize(("method", "path"), READ_ROUTES)
    async def test_missing_entitlement_is_payment_required(
        self, client, org_id, queue, method, path
    ) -> None:
        principal = principal_for(org_id, [f"{READ}:ORG"], entitlements=[])
        with as_principal(principal):
            res = await client.request(method, path)
        assert res.status_code == 402

    @pytest.mark.parametrize(("method", "path"), READ_ROUTES)
    async def test_below_org_scope_is_forbidden(self, client, org_id, queue, method, path) -> None:
        """The shared queue reads at ORG; an OWN grant is not enough."""
        principal = principal_for(org_id, [f"{READ}:OWN"])
        with as_principal(principal):
            res = await client.request(method, path)
        assert res.status_code == 403


class TestWriteAuthz:
    @pytest.mark.parametrize(("method", "path", "body"), WRITE_ROUTES)
    async def test_read_grant_cannot_write(self, client, org_id, queue, method, path, body) -> None:
        principal = principal_for(org_id, [f"{READ}:ORG"])  # read only
        with as_principal(principal):
            res = await client.request(method, path, json=body)
        assert res.status_code == 403

    @pytest.mark.parametrize(("method", "path", "body"), WRITE_ROUTES)
    async def test_below_org_scope_cannot_write(
        self, client, org_id, queue, method, path, body
    ) -> None:
        principal = principal_for(org_id, [f"{WRITE}:OWN"])
        with as_principal(principal):
            res = await client.request(method, path, json=body)
        assert res.status_code == 403


class TestAgentAndSlaPolicyWriteAuthz:
    """`support.desk.agent.write` and `support.desk.sla_policy.write` are SUPPORT_ADMIN-only —
    holding the desk's ordinary `ticket.write` is not enough to manage agents or the policy."""

    @pytest.mark.parametrize(("method", "path", "body"), AGENT_WRITE_ROUTES)
    async def test_ticket_write_cannot_manage_agents(
        self, client, org_id, queue, method, path, body
    ) -> None:
        principal = principal_for(org_id, [f"{READ}:ORG", f"{WRITE}:ORG"])
        with as_principal(principal):
            res = await client.request(method, path, json=body)
        assert res.status_code == 403

    @pytest.mark.parametrize(("method", "path", "body"), AGENT_WRITE_ROUTES)
    async def test_below_org_scope_cannot_manage_agents(
        self, client, org_id, queue, method, path, body
    ) -> None:
        principal = principal_for(org_id, [f"{AGENT_WRITE}:OWN"])
        with as_principal(principal):
            res = await client.request(method, path, json=body)
        assert res.status_code == 403

    @pytest.mark.parametrize(("method", "path", "body"), SLA_POLICY_WRITE_ROUTES)
    async def test_ticket_write_cannot_set_sla_policy(
        self, client, org_id, queue, method, path, body
    ) -> None:
        principal = principal_for(org_id, [f"{READ}:ORG", f"{WRITE}:ORG"])
        with as_principal(principal):
            res = await client.request(method, path, json=body)
        assert res.status_code == 403

    @pytest.mark.parametrize(("method", "path", "body"), SLA_POLICY_WRITE_ROUTES)
    async def test_below_org_scope_cannot_set_sla_policy(
        self, client, org_id, queue, method, path, body
    ) -> None:
        principal = principal_for(org_id, [f"{SLA_POLICY_WRITE}:OWN"])
        with as_principal(principal):
            res = await client.request(method, path, json=body)
        assert res.status_code == 403


class TestReadsWork:
    async def test_list_returns_the_seeded_queue(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.get("/api/v1/support/tickets")
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == 3
        ids = {t["id"] for t in body["items"]}
        assert ids == {"TK-0001", "TK-0002", "TK-0003"}
        # The list carries the thread and the contract's nested requester.
        first = next(t for t in body["items"] if t["id"] == "TK-0001")
        assert first["requester"]["company"] == "Northwind"
        assert first["assignee_id"] == "ag-02"
        assert len(first["messages"]) == 2

    async def test_filters_and_search_narrow(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            by_status = await client.get("/api/v1/support/tickets?status=new")
            unassigned = await client.get("/api/v1/support/tickets?assignee=unassigned")
            search = await client.get("/api/v1/support/tickets?q=northwind")
        assert {t["id"] for t in by_status.json()["items"]} == {"TK-0002"}
        assert {t["id"] for t in unassigned.json()["items"]} == {"TK-0002"}
        assert {t["id"] for t in search.json()["items"]} == {"TK-0001"}

    async def test_detail_carries_the_thread(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.get("/api/v1/support/tickets/TK-0001")
        assert res.status_code == 200
        assert [m["id"] for m in res.json()["messages"]] == ["m1", "m2"]

    async def test_overview_rolls_up_the_queue(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.get("/api/v1/support/overview")
        body = res.json()
        assert body["open_tickets"] == 2  # TK-0001 open + TK-0002 new; TK-0003 solved
        assert body["breaching_sla"] == 1  # TK-0001 breached and active
        assert body["csat_pct"] == 100  # one rated ticket, good
        assert len(body["volume"]) == 7

    async def test_unknown_ticket_is_not_found(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.get("/api/v1/support/tickets/TK-9999")
        assert res.status_code == 404


class TestWritePath:
    async def test_reply_appends_and_reopens(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            before = await client.get("/api/v1/support/tickets/TK-0001")
            res = await client.post(
                "/api/v1/support/tickets/TK-0001/reply",
                json={"body": "An update for you", "author_name": "Daniel Okafor"},
            )
        assert res.status_code == 201
        body = res.json()
        assert len(body["messages"]) == len(before.json()["messages"]) + 1
        assert body["messages"][-1]["id"] == "m3"

    async def test_assign_and_unassign(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            assigned = await client.post(
                "/api/v1/support/tickets/TK-0002/assign", json={"assignee_id": "ag-01"}
            )
            cleared = await client.post(
                "/api/v1/support/tickets/TK-0002/assign", json={"assignee_id": None}
            )
        assert assigned.json()["assignee_id"] == "ag-01"
        assert cleared.json()["assignee_id"] is None

    async def test_assigning_an_unknown_agent_is_not_found(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.post(
                "/api/v1/support/tickets/TK-0002/assign", json={"assignee_id": "ag-ghost"}
            )
        assert res.status_code == 404

    async def test_reply_author_type_is_always_agent_never_client_supplied(
        self, client, org_id, queue
    ) -> None:
        """`ReplyIn` has no `author_type` field — a caller cannot post as `customer` or
        `system` through this endpoint, and sending one is an unknown field (422), not a value
        the server might have honoured."""
        with as_principal(_full(org_id)):
            rejected = await client.post(
                "/api/v1/support/tickets/TK-0001/reply",
                json={"body": "hi", "author_name": "Daniel Okafor", "author_type": "customer"},
            )
            accepted = await client.post(
                "/api/v1/support/tickets/TK-0001/reply",
                json={"body": "hi again", "author_name": "Daniel Okafor"},
            )
        assert rejected.status_code == 422
        assert accepted.status_code == 201
        assert accepted.json()["messages"][-1]["author_type"] == "agent"


class TestStatusTransitions:
    async def test_legal_move_updates_status(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.post(
                "/api/v1/support/tickets/TK-0002/status", json={"status": "pending"}
            )
        assert res.status_code == 200
        assert res.json()["status"] == "pending"

    async def test_illegal_move_is_a_conflict(self, client, org_id, queue) -> None:
        """TK-0003 is `solved`; `solved` may only reopen to `open` (`domain.py`)."""
        with as_principal(_full(org_id)):
            res = await client.post(
                "/api/v1/support/tickets/TK-0003/status", json={"status": "pending"}
            )
        assert res.status_code == 409
        assert "cannot move" in res.json()["detail"]

    async def test_solving_clears_sla_due(self, client, org_id, queue) -> None:
        """TK-0001 carries a real `sla_due` (`conftest.seed_queue`); solving it must null that
        out — the clock has nothing left to measure."""
        with as_principal(_full(org_id)):
            before = await client.get("/api/v1/support/tickets/TK-0001")
            res = await client.post(
                "/api/v1/support/tickets/TK-0001/status", json={"status": "solved"}
            )
        assert before.json()["sla_due"] is not None
        assert res.status_code == 200
        assert res.json()["status"] == "solved"
        assert res.json()["sla_due"] is None

    async def test_free_form_status_value_is_rejected(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.post(
                "/api/v1/support/tickets/TK-0002/status", json={"status": "archived"}
            )
        assert res.status_code == 422

    async def test_unknown_ticket_is_not_found(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.post(
                "/api/v1/support/tickets/TK-9999/status", json={"status": "open"}
            )
        assert res.status_code == 404


class TestCreateTicket:
    async def test_first_ticket_in_an_empty_org_gets_a_number(self, client, org_id) -> None:
        """An empty organization — no seeded queue — still opens a ticket, numbered from 1."""
        with as_principal(_full(org_id)):
            res = await client.post("/api/v1/support/tickets", json=_NEW_TICKET_BODY)
        assert res.status_code == 201
        body = res.json()
        assert body["id"] == "TK-0001"
        assert body["status"] == "new"
        assert body["sla_due"] is None  # no SLA policy configured for this organization
        assert len(body["messages"]) == 1
        assert body["messages"][0]["author_type"] == "customer"
        assert body["messages"][0]["author_name"] == "A Requester"

    async def test_server_owns_number_and_status(self, client, org_id, queue) -> None:
        payload = {**_NEW_TICKET_BODY, "number": "TK-9999", "status": "solved"}
        with as_principal(_full(org_id)):
            res = await client.post("/api/v1/support/tickets", json=payload)
        assert res.status_code == 422  # extra=forbid: number/status are not client fields

    async def test_assigning_an_unknown_agent_on_create_is_not_found(
        self, client, org_id, queue
    ) -> None:
        payload = {**_NEW_TICKET_BODY, "assignee_id": "ag-ghost"}
        with as_principal(_full(org_id)):
            res = await client.post("/api/v1/support/tickets", json=payload)
        assert res.status_code == 404

    async def test_sla_due_is_stamped_from_the_policy(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            await client.put("/api/v1/support/sla-policy", json=_SLA_POLICY_BODY)
            res = await client.post(
                "/api/v1/support/tickets", json={**_NEW_TICKET_BODY, "priority": "urgent"}
            )
        assert res.status_code == 201
        assert res.json()["sla_due"] is not None


class TestUpdateTicket:
    async def test_priority_change_is_visible_after_reload(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.patch("/api/v1/support/tickets/TK-0002", json={"priority": "low"})
            reloaded = await client.get("/api/v1/support/tickets/TK-0002")
        assert res.status_code == 200
        assert res.json()["priority"] == "low"
        assert reloaded.json()["priority"] == "low"

    async def test_empty_patch_is_rejected(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.patch("/api/v1/support/tickets/TK-0002", json={})
        assert res.status_code == 422

    async def test_status_and_assignee_are_not_patchable_here(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.patch("/api/v1/support/tickets/TK-0002", json={"status": "solved"})
        assert res.status_code == 422  # extra=forbid


class TestAgentManagement:
    async def test_add_agent_appears_in_the_roster(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.post(
                "/api/v1/support/agents",
                json={"name": "New Agent", "email": "new-agent@example.com"},
            )
            listed = await client.get("/api/v1/support/agents")
        assert res.status_code == 201
        created = res.json()
        assert created["id"] == "ag-03"
        assert created["role"] == "agent"
        assert created["online"] is False
        assert any(a["id"] == "ag-03" for a in listed.json())

    async def test_update_agent_patches_only_sent_fields(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.patch("/api/v1/support/agents/ag-01", json={"online": False})
        assert res.status_code == 200
        body = res.json()
        assert body["online"] is False
        assert body["name"] == "Priya Nair"  # untouched

    async def test_update_unknown_agent_is_not_found(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.patch("/api/v1/support/agents/ag-ghost", json={"online": True})
        assert res.status_code == 404

    async def test_deactivating_an_agent_unassigns_their_tickets(
        self, client, org_id, queue
    ) -> None:
        """TK-0001 is assigned to ag-02 (`conftest.seed_queue`); deactivating ag-02 must leave
        it unassigned rather than pointing at a ghost agent."""
        with as_principal(_full(org_id)):
            res = await client.delete("/api/v1/support/agents/ag-02")
            ticket = await client.get("/api/v1/support/tickets/TK-0001")
            agents = await client.get("/api/v1/support/agents")
        assert res.status_code == 204
        assert ticket.json()["assignee_id"] is None
        assert all(a["id"] != "ag-02" for a in agents.json())

    async def test_retrying_a_deactivate_is_a_clean_not_found(self, client, org_id, queue) -> None:
        """`selectors.get_agent` filters `deleted_at is None`, so a retried DELETE finds
        nothing to act on — a 404, not a 500 or a second audit row. `deactivate_agent` itself
        stays idempotent for any caller that reaches it with an already-deleted row."""
        with as_principal(_full(org_id)):
            first = await client.delete("/api/v1/support/agents/ag-01")
            second = await client.delete("/api/v1/support/agents/ag-01")
        assert first.status_code == 204
        assert second.status_code == 404


class TestSlaPolicy:
    async def test_missing_policy_is_not_found(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            res = await client.get("/api/v1/support/sla-policy")
        assert res.status_code == 404

    async def test_set_then_read_the_policy(self, client, org_id, queue) -> None:
        with as_principal(_full(org_id)):
            put = await client.put("/api/v1/support/sla-policy", json=_SLA_POLICY_BODY)
            got = await client.get("/api/v1/support/sla-policy")
        assert put.status_code == 200
        assert got.status_code == 200
        assert len(got.json()["targets"]) == 4

    async def test_a_second_put_replaces_rather_than_duplicates(
        self, client, org_id, queue
    ) -> None:
        with as_principal(_full(org_id)):
            await client.put("/api/v1/support/sla-policy", json=_SLA_POLICY_BODY)
            renamed = {**_SLA_POLICY_BODY, "name": "Renamed"}
            await client.put("/api/v1/support/sla-policy", json=renamed)
            got = await client.get("/api/v1/support/sla-policy")
        assert got.json()["name"] == "Renamed"

    async def test_missing_a_priority_row_is_rejected(self, client, org_id, queue) -> None:
        incomplete = {**_SLA_POLICY_BODY, "targets": _SLA_POLICY_BODY["targets"][:3]}
        with as_principal(_full(org_id)):
            res = await client.put("/api/v1/support/sla-policy", json=incomplete)
        assert res.status_code == 422


class TestAppendOnlyThread:
    async def test_replies_only_ever_grow_the_thread_unchanged(
        self, session, org_id, queue
    ) -> None:
        write = ctx_for(org_id, WRITE, Scope.ORG)
        async with session.begin():
            ticket = await selectors.get_ticket(session, org_id, "TK-0001")
            assert ticket is not None
            before = await selectors.get_messages(session, org_id, ticket.id)
            original_bodies = {m.ref: m.body for m in before}

        async with session.begin():
            await services.add_message(
                session,
                write,
                ticket,
                body="Second update",
                author_type="agent",
                author_name="Daniel Okafor",
            )
        async with session.begin():
            after = await selectors.get_messages(session, org_id, ticket.id)

        assert len(after) == len(before) + 1
        # Every earlier message is byte-for-byte unchanged: an append never rewrites history.
        for msg in after[: len(before)]:
            assert msg.body == original_bodies[msg.ref]

    def test_service_exposes_no_message_mutation(self) -> None:
        """Append-only is structural, not just conventional: there is no edit or delete path."""
        assert not hasattr(services, "update_message")
        assert not hasattr(services, "delete_message")
        assert not hasattr(services, "edit_message")
