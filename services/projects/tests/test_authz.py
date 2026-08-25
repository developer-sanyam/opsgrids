"""Authorization, scope and validation — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more than
proving one succeeds. The scope tests come in two halves — `ctx.apply` narrows a *list*,
`ctx.assert_can_touch` guards a single *object* — because filtering the list and forgetting the
detail is the named vulnerability. A user who can list only their own projects must also be
refused another's by code.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from app.schemas import MilestoneIn, TaskIn
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import ConflictProblem, NotFoundProblem
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- list scope


class TestListScope:
    async def _codes(self, session: AsyncSession, ctx: ScopeContext) -> set[str]:
        rows = (await session.execute(selectors.projects_stmt(ctx))).scalars().all()
        return {p.code for p in rows}

    async def test_org_scope_sees_every_project(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "projects.portfolio.project.read", Scope.ORG)
        assert await self._codes(session, ctx) == {"PRJ-104", "PRJ-108"}

    async def test_own_scope_sees_only_projects_you_own(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id, "projects.portfolio.project.read", Scope.OWN, subject_ref=data["subject_a"]
        )
        assert await self._codes(session, ctx) == {"PRJ-104"}

    async def test_own_scope_with_no_subject_sees_nothing(self, session, org_id, data) -> None:
        """OWN with nothing to own must match nothing, never everything."""
        ctx = ctx_for(org_id, "projects.portfolio.project.read", Scope.OWN, subject_ref=None)
        assert await self._codes(session, ctx) == set()

    async def test_a_search_cannot_widen_scope(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id, "projects.portfolio.project.read", Scope.OWN, subject_ref=data["subject_a"]
        )
        stmt = selectors.projects_stmt(ctx, q="helios")  # matches only PRJ-108 (subject_b's)
        assert (await session.execute(stmt)).all() == []


# --------------------------------------------------------------------- object-level


class TestObjectLevelChecks:
    async def test_scope_does_not_leak_through_the_detail_route(self, client, org_id, data) -> None:
        """A caller with OWN scope is refused another's project by code, but not their own."""
        principal = principal_for(
            org_id, ["projects.portfolio.project.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            refused = await client.get("/api/v1/projects-workspace/projects/PRJ-108")  # subject_b's
            allowed = await client.get("/api/v1/projects-workspace/projects/PRJ-104")  # subject_a's
        assert refused.status_code == 403
        assert allowed.status_code == 200
        assert allowed.json()["code"] == "PRJ-104"


# --------------------------------------------------------------------- endpoint authz


READ_ENDPOINTS = [
    "/api/v1/projects-workspace/projects",
    "/api/v1/projects-workspace/milestones",
    "/api/v1/projects-workspace/tasks",
    "/api/v1/projects-workspace/board",
    "/api/v1/projects-workspace/timeline",
    "/api/v1/projects-workspace/overview",
]


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        for path in READ_ENDPOINTS:
            response = await client.get(path)
            assert response.status_code == 401, path

    async def test_missing_the_permission_is_forbidden(self, client, org_id, data) -> None:
        """Entitled to projects, but holding no projects permission at all."""
        principal = principal_for(org_id, [], subject_ref=data["subject_a"])
        with as_principal(principal):
            for path in READ_ENDPOINTS:
                response = await client.get(path)
                assert response.status_code == 403, path
                assert response.headers["content-type"].startswith("application/problem+json")

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["projects.portfolio.project.read:ORG", "projects.board.task.read:ORG"],
            subject_ref=data["subject_a"],
            entitlements=[],
        )
        with as_principal(principal):
            projects = await client.get("/api/v1/projects-workspace/projects")
            board = await client.get("/api/v1/projects-workspace/board")
        assert projects.status_code == 402
        assert board.status_code == 402

    async def test_a_reader_cannot_create_a_project(self, client, org_id, data) -> None:
        """Holding project.read but not project.write cannot create — the write guard is by name."""
        principal = principal_for(
            org_id, ["projects.portfolio.project.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects-workspace/projects",
                json={
                    "code": "PRJ-900",
                    "name": "Intruder project",
                    "start_date": "2026-01-01",
                    "target_date": "2026-06-01",
                },
            )
        assert response.status_code == 403

    async def test_a_writer_may_create_a_project(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["projects.portfolio.project.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects-workspace/projects",
                json={
                    "code": "PRJ-777",
                    "name": "Aurora Data Mesh",
                    "client": "Aurora Systems",
                    "owner_ref": "p-01",
                    "phase": "discovery",
                    "health": "on_track",
                    "progress_pct": 5,
                    "start_date": "2026-09-01",
                    "target_date": "2027-03-01",
                    "budget_minor": 500000000,
                    "spent_minor": 0,
                    "currency": "INR",
                    "team_size": 4,
                    "open_tasks": 12,
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["code"] == "PRJ-777"
        assert body["owner"]["id"] == "p-01"
        assert body["budget_minor"] == 500000000
        assert body["milestone_count"] == 0

    async def test_an_own_reader_sees_only_their_own_projects_over_http(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["projects.portfolio.project.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/projects-workspace/projects")
        assert response.status_code == 200
        codes = {item["code"] for item in response.json()["items"]}
        assert codes == {"PRJ-104"}

    async def test_the_board_groups_tasks_by_stage(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.board.task.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/projects-workspace/board")
        assert response.status_code == 200
        columns = {c["stage"]: c for c in response.json()["columns"]}
        assert list(columns) == ["backlog", "in_progress", "review", "done"]
        assert len(columns["backlog"]["tasks"]) == 2

    async def test_overview_rolls_up_the_portfolio(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.portfolio.project.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/projects-workspace/overview")
        assert response.status_code == 200
        body = response.json()
        assert body["active_projects"] == 2  # neither project is in the run phase
        assert {s["key"] for s in body["health_breakdown"]} == {"on_track", "at_risk", "delayed"}
        assert body["total_budget_minor"] == 1_240_000_000 + 1_860_000_000
        assert len(body["burndown_sprint"]) == 7


# --------------------------------------------------------------------- validation


class TestValidation:
    async def test_a_duplicate_project_code_is_refused(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "projects.portfolio.project.write", Scope.ORG)
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_project(
                    session,
                    ctx,
                    code="PRJ-104",
                    name="clash",
                    start_date=None,
                    target_date=None,
                )

    async def test_a_milestone_against_an_unknown_project_is_refused(
        self, session, org_id, data
    ) -> None:
        ctx = ctx_for(org_id, "projects.delivery.milestone.write", Scope.ORG)
        with pytest.raises(NotFoundProblem, match="project"):
            async with session.begin():
                await services.create_milestone_from(
                    session,
                    ctx,
                    MilestoneIn(project_code="PRJ-DOESNOTEXIST", title="x", due_date="2026-09-01"),
                )

    async def test_a_task_against_an_unknown_member_is_refused(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "projects.board.task.write", Scope.ORG)
        with pytest.raises(NotFoundProblem, match="member"):
            async with session.begin():
                await services.create_task_from(
                    session,
                    ctx,
                    TaskIn(project_code="PRJ-104", title="x", assignee_ref="p-99"),
                )

    async def test_creating_stages_an_audit_row_and_an_outbox_event(
        self, session, org_id, data
    ) -> None:
        """The change, its audit row and its event commit together — nothing publishes inline."""
        from sqlalchemy import func, select

        from app.models import AuditLog, OutboxEvent

        ctx = ctx_for(org_id, "projects.portfolio.project.write", Scope.ORG)
        async with session.begin():
            project = await services.create_project(
                session, ctx, code="PRJ-500", name="Audited", start_date=None, target_date=None
            )
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "project.created", AuditLog.entity_id == project.id)
            )
        ).scalar_one()
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.aggregate_id == project.id)
                )
            )
            .scalars()
            .all()
        )
        assert audits == 1
        assert len(events) == 1
        assert events[0].event_type == "projects.portfolio.project.created"
        assert events[0].published_at is None
