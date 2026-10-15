"""`GET /members?employee_id=` -- who, if anyone, is linked to this employee record.

The HRMS employee page shows a "Linked member" field (ADR-0008 Q1) and needs the answer for one
employee without paging the whole directory. Like `?linked=` it is a filter **inside** the scope
`members_stmt` already applies: it narrows, and can never widen.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.models import Membership, Organization
from app.schemas import MemberPatch
from platform_core.auth import Scope
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

MEMBER_WRITE = "core.identity.membership.write"
MEMBER_READ = "core.identity.membership.read"


async def _emails(session: AsyncSession, ctx: object, **kwargs: object) -> set[str]:
    stmt = selectors.members_stmt(ctx, **kwargs)  # type: ignore[arg-type]
    return {row[1].email for row in (await session.execute(stmt)).all()}


async def _link(
    session: AsyncSession, org: Organization, actor: Membership, target: Membership, employee: uuid.UUID
) -> None:
    ctx = ctx_for(org.id, actor, MEMBER_WRITE, Scope.ORG)
    async with session.begin():
        await services.update_member(session, ctx, target, MemberPatch(employee_id=employee))


class TestTheEmployeeFilter:
    async def test_it_finds_the_one_member_linked_to_that_employee(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        employee = uuid.uuid4()
        await _link(session, org, people["manager"], people["report"], employee)
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.ORG)
        assert await _emails(session, ctx, employee_id=employee) == {"report@example.com"}

    async def test_an_employee_nobody_is_linked_to_matches_nobody(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.ORG)
        assert await _emails(session, ctx, employee_id=uuid.uuid4()) == set()

    async def test_a_team_scoped_reader_cannot_reach_an_outsider_through_it(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The negative one: the outsider is linked, but outside the manager's team."""
        employee = uuid.uuid4()
        await _link(session, org, people["manager"], people["outsider"], employee)
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.TEAM)
        assert await _emails(session, ctx, employee_id=employee) == set()

    async def test_it_composes_with_linked(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        employee = uuid.uuid4()
        await _link(session, org, people["manager"], people["report"], employee)
        ctx = ctx_for(org.id, people["manager"], MEMBER_READ, Scope.ORG)
        assert await _emails(session, ctx, employee_id=employee, linked=False) == set()
        assert await _emails(session, ctx, employee_id=employee, linked=True) == {
            "report@example.com"
        }


class TestOverHttp:
    async def test_the_endpoint_answers_for_a_reader_and_refuses_everyone_else(
        self, client, org: Organization, people: dict[str, Membership], session: AsyncSession
    ) -> None:
        from app.main import app
        from platform_core.auth import get_principal
        from tests.helpers import principal_for

        employee = uuid.uuid4()
        await _link(session, org, people["manager"], people["report"], employee)

        reader = principal_for(org.id, people["manager"], [f"{MEMBER_READ}:ORG"])
        app.dependency_overrides[get_principal] = lambda: reader
        try:
            ok = await client.get("/api/v1/members", params={"employee_id": str(employee)})
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert ok.status_code == 200
        assert [m["email"] for m in ok.json()["items"]] == ["report@example.com"]
        assert ok.json()["items"][0]["employee_id"] == str(employee)

        nobody = principal_for(org.id, people["report"], ["core.org.organization.read:ORG"])
        app.dependency_overrides[get_principal] = lambda: nobody
        try:
            denied = await client.get("/api/v1/members", params={"employee_id": str(employee)})
            malformed = await client.get("/api/v1/members", params={"employee_id": "not-a-uuid"})
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert denied.status_code == 403
        assert malformed.status_code in (403, 422)
