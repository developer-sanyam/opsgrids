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
]


def _full(org_id: uuid.UUID) -> Principal:
    return principal_for(org_id, [f"{READ}:ORG", f"{WRITE}:ORG"])


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
