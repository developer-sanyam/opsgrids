"""ADR-0008 §9.3: the inbox served to a caller with no employee link logs
`approval.inbox.no_subject_ref` at `info` — and still answers 200 with an empty page.

Both halves are pinned here on purpose. The log line is the only in-service surface for a
membership whose `account.memberships.employee_id` was never written ("my approvals page is
empty", answerable from logs rather than a database session), and the response assertion is what
stops a later change from turning §9's deliberately quiet empty page into an error: an *exited*
employee reaches the same "you may decide nothing" state holding a perfectly good `sref`, so a
4xx here would split one user-visible state across two status codes.

`structlog.testing.capture_logs` is the capture mechanism because this service logs through
`platform_core.logging.get_logger` (structlog), not through stdlib `logging` — `caplog` sees
nothing from it.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import open_request, principal_for, seed_person

pytestmark = pytest.mark.integration

EVENT = "approval.inbox.no_subject_ref"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _matching(entries: list[dict], event: str = EVENT) -> list[dict]:
    return [e for e in entries if e.get("event") == event]


class TestUnlinkedInboxIsLogged:
    async def test_an_unlinked_caller_is_logged_and_still_gets_an_empty_page(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None  # a pending, unassigned step an ORG decider would otherwise see

        principal = principal_for(org_id, None, ["core.approval.request.decide:ORG"])
        with capture_logs() as entries, as_principal(principal):
            response = await client.get("/api/v1/approvals/inbox")

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

        lines = _matching(entries)
        assert len(lines) == 1
        line = lines[0]
        assert line["log_level"] == "info"
        assert line["organization_id"] == str(org_id)
        assert line["membership_id"] == str(principal.membership_id)

    async def test_the_line_carries_only_routing_ids_no_customer_content(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Routing and lifecycle facts may be logged; customer content may not. The line is two
        ids and its own name — no requester, no note, no summary, nothing from the request."""
        requester = uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, requester, display_name="Ananya Rao")
        assert await open_request(session, org_id, requester_employee_id=requester) is not None

        principal = principal_for(org_id, None, ["core.approval.request.decide:ORG"])
        with capture_logs() as entries, as_principal(principal):
            await client.get("/api/v1/approvals/inbox")

        line = _matching(entries)[0]
        assert set(line) == {"event", "log_level", "organization_id", "membership_id"}

    async def test_a_linked_caller_is_not_logged(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The counter-case that keeps the line a signal: an ordinary caller with an employee
        link never emits it, however empty their inbox happens to be."""
        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        principal = principal_for(org_id, manager, ["core.approval.request.decide:OWN"])
        with capture_logs() as entries, as_principal(principal):
            response = await client.get("/api/v1/approvals/inbox")

        assert response.status_code == 200
        assert {item["id"] for item in response.json()["items"]} == {str(request.id)}
        assert _matching(entries) == []

    async def test_an_exited_caller_with_a_link_is_not_logged(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """§9's first bullet, as a test: an exited employee gets the same empty page at 200 and
        must *not* produce this line — they hold an `sref`, so nothing about their membership is
        unlinked and an operator chasing unlinked people should not find them here."""
        requester, exited = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, exited, status="exited")
        assert await open_request(session, org_id, requester_employee_id=requester) is not None

        principal = principal_for(org_id, exited, ["core.approval.request.decide:ORG"])
        with capture_logs() as entries, as_principal(principal):
            response = await client.get("/api/v1/approvals/inbox")

        assert response.status_code == 200
        assert response.json()["items"] == []
        assert _matching(entries) == []
