"""Authorization, scope and validation — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more than
proving one succeeds. The scope tests come in two halves — `ctx.apply` narrows a *list*,
`ctx.assert_can_touch` guards a single *object* — because filtering the list and forgetting the
detail is the named vulnerability. A user who can list only their own projects must also be
refused another's by code.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from app.schemas import MilestoneIn, TaskIn, TaskUpdate
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


# --------------------------------------------------------------------- the roster write surface

MEMBERS = "/api/v1/projects-workspace/members"


class TestMemberWrites:
    """POST /members — the roster row every `Person` on the wire is addressed by.

    Staffing the team is its own permission: a caller who may create projects and move board
    cards still cannot invent people, because the guard is by permission name rather than by
    "is a writer of something".
    """

    async def test_a_writer_may_add_a_member(self, client, session, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.team.member.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                MEMBERS, json={"ref": "p-03", "name": "Ravi Menon", "role": "QA Lead"}
            )
        assert response.status_code == 201
        assert response.json() == {
            "id": "p-03",
            "name": "Ravi Menon",
            "role": "QA Lead",
            "directory_id": None,
        }

        stored = await selectors.get_member_by_ref(session, org_id, "p-03")
        assert stored is not None
        assert stored.organization_id == org_id
        assert stored.name == "Ravi Menon"

    async def test_a_new_member_can_own_a_project_straight_away(
        self, client, session, org_id, data
    ) -> None:
        """The point of the endpoint: the `ref` it returns is usable as an `owner_ref`."""
        principal = principal_for(
            org_id, ["projects.team.member.write:ORG", "projects.portfolio.project.write:ORG"]
        )
        with as_principal(principal):
            created = await client.post(
                MEMBERS, json={"ref": "p-04", "name": "Ines Duarte", "role": "Delivery Lead"}
            )
            project = await client.post(
                "/api/v1/projects-workspace/projects",
                json={
                    "code": "PRJ-801",
                    "name": "Meridian Rollout",
                    "owner_ref": created.json()["id"],
                    "start_date": "2026-10-01",
                    "target_date": "2027-02-01",
                },
            )
        assert created.status_code == 201
        assert project.status_code == 201
        assert project.json()["owner"] == {
            "id": "p-04",
            "name": "Ines Duarte",
            "role": "Delivery Lead",
        }

    async def test_unauthenticated_cannot_add_a_member(self, client, session, org_id, data) -> None:
        response = await client.post(MEMBERS, json={"ref": "p-90", "name": "Nobody"})
        assert response.status_code == 401
        assert await selectors.get_member_by_ref(session, org_id, "p-90") is None

    async def test_a_project_writer_cannot_add_a_member(
        self, client, session, org_id, data
    ) -> None:
        """Holding every other projects write is not staffing: the roster guard is by name."""
        principal = principal_for(
            org_id,
            [
                "projects.portfolio.project.write:ORG",
                "projects.delivery.milestone.write:ORG",
                "projects.board.task.write:ORG",
                "projects.portfolio.project.read:ORG",
            ],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            response = await client.post(
                MEMBERS, json={"ref": "p-91", "name": "Smuggled In", "role": "Ghost"}
            )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")
        assert await selectors.get_member_by_ref(session, org_id, "p-91") is None

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, ["projects.team.member.write:ORG"], entitlements=[])
        with as_principal(principal):
            response = await client.post(MEMBERS, json={"ref": "p-92", "name": "Unentitled"})
        assert response.status_code == 402

    async def test_a_blank_ref_is_refused_by_validation(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.team.member.write:ORG"])
        with as_principal(principal):
            response = await client.post(MEMBERS, json={"ref": "", "name": "No handle"})
        assert response.status_code == 422

    async def test_a_member_with_no_name_is_refused_by_validation(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, ["projects.team.member.write:ORG"])
        with as_principal(principal):
            response = await client.post(MEMBERS, json={"ref": "p-93"})
        assert response.status_code == 422

    async def test_the_payload_cannot_choose_its_tenant(
        self, client, session, org_id, data
    ) -> None:
        """`organization_id` is not part of the contract — a smuggled one is ignored, and the
        row lands in the caller's organization."""
        other_org = uuid.uuid4()
        principal = principal_for(org_id, ["projects.team.member.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                MEMBERS,
                json={"ref": "p-05", "name": "Tenant Test", "organization_id": str(other_org)},
            )
        assert response.status_code == 201
        stored = await selectors.get_member_by_ref(session, org_id, "p-05")
        assert stored is not None
        assert stored.organization_id == org_id

    async def test_a_ref_already_taken_is_a_conflict(self, client, org_id, data) -> None:
        """`p-01` is seeded by the `data` fixture — the handle is unique per organization."""
        principal = principal_for(org_id, ["projects.team.member.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                MEMBERS, json={"ref": "p-01", "name": "Second Aarav", "role": "Delivery Lead"}
            )
        assert response.status_code == 409
        assert response.headers["content-type"].startswith("application/problem+json")


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

    async def test_a_soft_deleted_project_code_still_conflicts(self, session, org_id, data) -> None:
        """`uq_projects_org_code` is not a partial index — a soft-deleted project still
        occupies the code, and the duplicate check must see it too rather than falling through
        to an unhandled `IntegrityError`."""
        write = ctx_for(org_id, "projects.portfolio.project.write", Scope.ORG)
        async with session.begin():
            await services.delete_project(session, write, data["project_a"])
        ctx = ctx_for(org_id, "projects.portfolio.project.write", Scope.ORG)
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_project(
                    session, ctx, code="PRJ-104", name="reused", start_date=None, target_date=None
                )

    async def test_a_soft_deleted_member_ref_still_conflicts(self, session, org_id, data) -> None:
        write = ctx_for(org_id, "projects.team.member.write", Scope.ORG)
        async with session.begin():
            await services.delete_member(session, write, data["member_a"])
        ctx = ctx_for(org_id, "projects.team.member.write", Scope.ORG)
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_member(session, ctx, ref="p-01", name="reused")

    async def test_own_scope_create_forces_self_assignment_on_a_task(
        self, session, org_id, data
    ) -> None:
        """Risk: a `task.write:OWN` create with no object-level check could plant a card
        assigned to anyone. Leaving `assignee_ref` unset is refused..."""
        ctx = ctx_for(
            org_id, "projects.board.task.write", Scope.OWN, subject_ref=data["directory_a"]
        )
        with pytest.raises(Exception, match=r"[Oo]ut of scope"):
            async with session.begin():
                await services.create_task_from(
                    session, ctx, TaskIn(project_code="PRJ-104", title="unowned")
                )

    async def test_own_scope_create_assigning_myself_a_task_succeeds(
        self, session, org_id, data
    ) -> None:
        """...naming yourself is allowed."""
        ctx = ctx_for(
            org_id, "projects.board.task.write", Scope.OWN, subject_ref=data["directory_a"]
        )
        async with session.begin():
            task = await services.create_task_from(
                session, ctx, TaskIn(project_code="PRJ-104", title="mine", assignee_ref="p-01")
            )
        assert task.assignee_id == data["member_a"].id

    async def test_own_scope_create_assigning_someone_else_a_task_is_refused(
        self, session, org_id, data
    ) -> None:
        ctx = ctx_for(
            org_id, "projects.board.task.write", Scope.OWN, subject_ref=data["directory_a"]
        )
        with pytest.raises(Exception, match=r"[Oo]ut of scope"):
            async with session.begin():
                await services.create_task_from(
                    session,
                    ctx,
                    TaskIn(project_code="PRJ-104", title="not mine", assignee_ref="p-02"),
                )


# --------------------------------------------------------------------- task mutations


class TestTaskMutations:
    async def test_a_writer_can_move_a_card(self, client, org_id, data) -> None:
        """Persisting a board move is the headline gap this slice closes."""
        principal = principal_for(org_id, ["projects.board.task.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/projects-workspace/tasks/{data['task_a_id']}",
                json={"stage": "review"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["stage"] == "review"
        assert body["id"] == data["task_a_id"]

    async def test_explicit_null_clears_assignee_and_due_date(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.board.task.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/projects-workspace/tasks/{data['task_a_id']}",
                json={"assignee_ref": None, "due_date": None},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["assignee"]["id"] == ""  # falls back to the "Unassigned" projection
        assert body["due_date"] is None

    async def test_moving_to_an_unknown_assignee_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.board.task.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/projects-workspace/tasks/{data['task_a_id']}",
                json={"assignee_ref": "p-99"},
            )
        assert response.status_code == 404

    async def test_updating_an_unknown_task_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.board.task.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/projects-workspace/tasks/{uuid.uuid4()}", json={"stage": "done"}
            )
        assert response.status_code == 404

    async def test_a_reader_cannot_move_a_card(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.board.task.read:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/projects-workspace/tasks/{data['task_a_id']}", json={"stage": "done"}
            )
        assert response.status_code == 403

    async def test_own_scope_cannot_move_someone_elses_card(self, client, org_id, data) -> None:
        """Filtering the board and forgetting the object is the named vulnerability. The
        caller's `subject_ref` is the assignee's `directory_id`, not their `members.id` — see
        `services._owner_directory_id`."""
        principal = principal_for(
            org_id, ["projects.board.task.write:OWN"], subject_ref=data["directory_a"]
        )
        with as_principal(principal):
            refused = await client.patch(
                f"/api/v1/projects-workspace/tasks/{data['task_b_id']}",
                json={"stage": "done"},  # member_b's card
            )
            allowed = await client.patch(
                f"/api/v1/projects-workspace/tasks/{data['task_a_id']}",
                json={"stage": "done"},  # member_a's card
            )
        assert refused.status_code == 403
        assert allowed.status_code == 200

    async def test_own_scope_with_no_directory_link_cannot_move_any_card(
        self, client, org_id, data
    ) -> None:
        """A caller whose token carries no `sref`-matching member link (the pre-fix state for
        every member today) is refused even their own assignments — narrower than a leak."""
        principal = principal_for(
            org_id, ["projects.board.task.write:OWN"], subject_ref=data["member_a"].id
        )
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/projects-workspace/tasks/{data['task_a_id']}", json={"stage": "done"}
            )
        assert response.status_code == 403

    async def test_moving_a_card_stages_an_audit_row_and_an_event(
        self, session, org_id, data
    ) -> None:
        from sqlalchemy import func, select

        from app.models import AuditLog, OutboxEvent

        ctx = ctx_for(org_id, "projects.board.task.write", Scope.ORG)
        async with session.begin():
            await services.update_task(session, ctx, data["task_a"], TaskUpdate(stage="review"))
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "task.updated", AuditLog.entity_id == data["task_a_id"])
            )
        ).scalar_one()
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == data["task_a_id"],
                        OutboxEvent.event_type == "projects.board.task.updated",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert audits == 1
        assert len(events) == 1

    async def test_a_writer_can_delete_a_card(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.board.task.write:ORG"])
        with as_principal(principal):
            response = await client.delete(f"/api/v1/projects-workspace/tasks/{data['task_b_id']}")
        assert response.status_code == 204

        reader = principal_for(org_id, ["projects.board.task.read:ORG"])
        with as_principal(reader):
            listed = await client.get("/api/v1/projects-workspace/tasks")
        assert data["task_b_id"] not in {t["id"] for t in listed.json()["items"]}

    async def test_deleting_an_already_deleted_card_is_not_found(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, ["projects.board.task.write:ORG"])
        with as_principal(principal):
            first = await client.delete(f"/api/v1/projects-workspace/tasks/{data['task_b_id']}")
            second = await client.delete(f"/api/v1/projects-workspace/tasks/{data['task_b_id']}")
        assert first.status_code == 204
        assert second.status_code == 404

    async def test_a_reader_cannot_delete_a_card(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.board.task.read:ORG"])
        with as_principal(principal):
            response = await client.delete(f"/api/v1/projects-workspace/tasks/{data['task_a_id']}")
        assert response.status_code == 403

    async def test_project_code_filters_the_task_list(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.board.task.read:ORG"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/projects-workspace/tasks", params={"project_code": "PRJ-108"}
            )
        assert response.status_code == 200
        codes = {t["project_code"] for t in response.json()["items"]}
        assert codes == {"PRJ-108"}


# --------------------------------------------------------------------- project mutations


class TestProjectMutations:
    async def test_a_writer_can_edit_a_project(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.portfolio.project.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects-workspace/projects/PRJ-104",
                json={"health": "at_risk", "progress_pct": 70},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["health"] == "at_risk"
        assert body["progress_pct"] == 70
        assert body["code"] == "PRJ-104"

    async def test_code_cannot_be_changed_through_patch(self, client, org_id, data) -> None:
        """`code` is not even a field on `ProjectUpdate` — an attempt is silently ignored."""
        principal = principal_for(org_id, ["projects.portfolio.project.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects-workspace/projects/PRJ-104",
                json={"code": "PRJ-999", "name": "Renamed"},
            )
        assert response.status_code == 200
        assert response.json()["code"] == "PRJ-104"

    async def test_open_tasks_is_not_editable_through_patch(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.portfolio.project.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects-workspace/projects/PRJ-104", json={"open_tasks": 999}
            )
        assert response.status_code == 200
        assert response.json()["open_tasks"] == 34  # unchanged — the field is not accepted

    async def test_target_before_start_is_rejected(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.portfolio.project.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects-workspace/projects/PRJ-104",
                json={"start_date": "2026-10-15", "target_date": "2026-04-01"},
            )
        assert response.status_code == 422

    async def test_editing_an_unknown_project_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.portfolio.project.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects-workspace/projects/PRJ-NOPE", json={"health": "delayed"}
            )
        assert response.status_code == 404

    async def test_a_reader_cannot_edit_a_project(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.portfolio.project.read:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects-workspace/projects/PRJ-104", json={"health": "delayed"}
            )
        assert response.status_code == 403

    async def test_own_scope_cannot_edit_someone_elses_project(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["projects.portfolio.project.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            refused = await client.patch(
                "/api/v1/projects-workspace/projects/PRJ-108",
                json={"health": "delayed"},  # subject_b's project
            )
            allowed = await client.patch(
                "/api/v1/projects-workspace/projects/PRJ-104",
                json={"health": "delayed"},  # subject_a's project
            )
        assert refused.status_code == 403
        assert allowed.status_code == 200

    async def test_a_writer_can_delete_a_project_and_its_children_cascade(
        self, client, org_id, data
    ) -> None:
        """A soft-deleted project takes its own milestones and board cards with it in the same
        transaction, so an orphan child never renders its project as an empty string."""
        principal = principal_for(
            org_id,
            [
                "projects.portfolio.project.write:ORG",
                "projects.portfolio.project.read:ORG",
                "projects.delivery.milestone.read:ORG",
                "projects.board.task.read:ORG",
            ],
        )
        with as_principal(principal):
            response = await client.delete("/api/v1/projects-workspace/projects/PRJ-104")
        assert response.status_code == 204
        with as_principal(principal):
            projects = await client.get("/api/v1/projects-workspace/projects")
            milestones = await client.get("/api/v1/projects-workspace/milestones")
            tasks = await client.get("/api/v1/projects-workspace/tasks")
        assert "PRJ-104" not in {p["code"] for p in projects.json()["items"]}
        assert data["milestone_a_id"] not in {m["id"] for m in milestones.json()["items"]}
        assert data["task_a_id"] not in {t["id"] for t in tasks.json()["items"]}

    async def test_deleting_an_already_deleted_project_is_not_found(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, ["projects.portfolio.project.write:ORG"])
        with as_principal(principal):
            first = await client.delete("/api/v1/projects-workspace/projects/PRJ-104")
            second = await client.delete("/api/v1/projects-workspace/projects/PRJ-104")
        assert first.status_code == 204
        assert second.status_code == 404

    async def test_a_reader_cannot_delete_a_project(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.portfolio.project.read:ORG"])
        with as_principal(principal):
            response = await client.delete("/api/v1/projects-workspace/projects/PRJ-108")
        assert response.status_code == 403

    async def test_sort_by_name_and_health(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.portfolio.project.read:ORG"])
        with as_principal(principal):
            by_name = await client.get(
                "/api/v1/projects-workspace/projects", params={"sort": "name"}
            )
            by_health = await client.get(
                "/api/v1/projects-workspace/projects", params={"sort": "health"}
            )
        assert [p["code"] for p in by_name.json()["items"]] == ["PRJ-108", "PRJ-104"]  # H < N
        # PRJ-108 is at_risk, PRJ-104 is on_track — at_risk sorts first
        assert [p["code"] for p in by_health.json()["items"]] == ["PRJ-108", "PRJ-104"]

    async def test_search_matches_the_owners_name(self, client, org_id, data) -> None:
        """`q` covers the owner's name too, not only name/code/client/summary."""
        principal = principal_for(org_id, ["projects.portfolio.project.read:ORG"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/projects-workspace/projects", params={"q": "aarav"}
            )
        assert {p["code"] for p in response.json()["items"]} == {"PRJ-104"}


# --------------------------------------------------------------------- milestone mutations


class TestMilestoneMutations:
    async def test_read_one_milestone(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.delivery.milestone.read:ORG"])
        with as_principal(principal):
            response = await client.get(
                f"/api/v1/projects-workspace/milestones/{data['milestone_a_id']}"
            )
        assert response.status_code == 200
        assert response.json()["title"] == "Payment gateway integration"

    async def test_reading_an_unknown_milestone_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.delivery.milestone.read:ORG"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/projects-workspace/milestones/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_a_writer_can_edit_a_milestone(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.delivery.milestone.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/projects-workspace/milestones/{data['milestone_a_id']}",
                json={"status": "completed", "progress_pct": 100},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["progress_pct"] == 100

    async def test_a_reader_cannot_edit_a_milestone(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.delivery.milestone.read:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/projects-workspace/milestones/{data['milestone_a_id']}",
                json={"status": "completed"},
            )
        assert response.status_code == 403

    async def test_editing_an_unknown_milestone_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.delivery.milestone.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/projects-workspace/milestones/{uuid.uuid4()}",
                json={"status": "completed"},
            )
        assert response.status_code == 404

    async def test_a_writer_can_delete_a_milestone(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id,
            ["projects.delivery.milestone.write:ORG", "projects.delivery.milestone.read:ORG"],
        )
        with as_principal(principal):
            response = await client.delete(
                f"/api/v1/projects-workspace/milestones/{data['milestone_a_id']}"
            )
        assert response.status_code == 204
        with as_principal(principal):
            listed = await client.get("/api/v1/projects-workspace/milestones")
        assert data["milestone_a_id"] not in {m["id"] for m in listed.json()["items"]}

    async def test_deleting_an_already_deleted_milestone_is_not_found(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, ["projects.delivery.milestone.write:ORG"])
        with as_principal(principal):
            first = await client.delete(
                f"/api/v1/projects-workspace/milestones/{data['milestone_a_id']}"
            )
            second = await client.delete(
                f"/api/v1/projects-workspace/milestones/{data['milestone_a_id']}"
            )
        assert first.status_code == 204
        assert second.status_code == 404

    async def test_a_reader_cannot_delete_a_milestone(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.delivery.milestone.read:ORG"])
        with as_principal(principal):
            response = await client.delete(
                f"/api/v1/projects-workspace/milestones/{data['milestone_a_id']}"
            )
        assert response.status_code == 403

    async def test_project_code_filters_the_milestone_list(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.delivery.milestone.read:ORG"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/projects-workspace/milestones", params={"project_code": "PRJ-108"}
            )
        assert response.status_code == 200
        assert response.json()["items"] == []  # PRJ-108 has no milestones in the fixture


# --------------------------------------------------------------------- member mutations


class TestMemberMutations:
    async def test_a_writer_can_add_a_member(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.team.member.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects-workspace/members",
                json={"ref": "p-05", "name": "Priya Nair", "role": "Design Lead"},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["id"] == "p-05"
        assert body["name"] == "Priya Nair"
        assert body["directory_id"] is None

    async def test_a_duplicate_ref_is_refused(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.team.member.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects-workspace/members",
                json={"ref": "p-01", "name": "Someone Else"},
            )
        assert response.status_code == 409

    async def test_a_reader_cannot_add_a_member(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.team.member.read:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects-workspace/members", json={"ref": "p-06", "name": "Refused"}
            )
        assert response.status_code == 403

    async def test_a_member_role_has_no_member_write_by_default(self) -> None:
        """The catalogue guard, restated as a wire-level fact: `PROJECTS_MEMBER` never picks
        `team.member.write`, so the Team screen's write controls are admin/manager-only."""
        from app.catalogue import PROJECTS_ROLE_TEMPLATES

        member = next(t for t in PROJECTS_ROLE_TEMPLATES if t.code == "PROJECTS_MEMBER")
        assert "projects.team.member.write" not in {g.code for g in member.grants}
        assert "projects.team.member.read" in {g.code for g in member.grants}

    async def test_a_writer_can_edit_a_member(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.team.member.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects-workspace/members/p-01", json={"role": "Principal Engineer"}
            )
        assert response.status_code == 200
        assert response.json()["role"] == "Principal Engineer"
        assert response.json()["id"] == "p-01"

    async def test_ref_cannot_be_changed_through_patch(self, client, org_id, data) -> None:
        """`ref` is not even a field on `MemberUpdate`."""
        principal = principal_for(org_id, ["projects.team.member.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects-workspace/members/p-01", json={"ref": "p-99"}
            )
        assert response.status_code == 200
        assert response.json()["id"] == "p-01"

    async def test_editing_an_unknown_member_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.team.member.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects-workspace/members/p-99", json={"role": "x"}
            )
        assert response.status_code == 404

    async def test_a_reader_cannot_edit_a_member(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.team.member.read:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects-workspace/members/p-01", json={"role": "x"}
            )
        assert response.status_code == 403

    async def test_deleting_a_member_falls_back_to_unassigned(self, client, org_id, data) -> None:
        """Deleting the roster entry does not orphan the project/task it owned — they keep
        rendering "Unassigned" because `members_by_id` filters `deleted_at`."""
        principal = principal_for(
            org_id,
            [
                "projects.team.member.write:ORG",
                "projects.team.member.read:ORG",
                "projects.portfolio.project.read:ORG",
                "projects.board.task.read:ORG",
            ],
        )
        with as_principal(principal):
            response = await client.delete("/api/v1/projects-workspace/members/p-01")
        assert response.status_code == 204

        with as_principal(principal):
            projects = await client.get("/api/v1/projects-workspace/projects")
            tasks = await client.get("/api/v1/projects-workspace/tasks")
            members = await client.get("/api/v1/projects-workspace/members")
        project_a = next(p for p in projects.json()["items"] if p["code"] == "PRJ-104")
        task_a = next(t for t in tasks.json()["items"] if t["id"] == data["task_a_id"])
        assert project_a["owner"]["id"] == ""
        assert task_a["assignee"]["id"] == ""
        assert "p-01" not in {m["id"] for m in members.json()["items"]}

    async def test_deleting_an_already_deleted_member_is_not_found(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, ["projects.team.member.write:ORG"])
        with as_principal(principal):
            first = await client.delete("/api/v1/projects-workspace/members/p-02")
            second = await client.delete("/api/v1/projects-workspace/members/p-02")
        assert first.status_code == 204
        assert second.status_code == 404

    async def test_a_reader_cannot_delete_a_member(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.team.member.read:ORG"])
        with as_principal(principal):
            response = await client.delete("/api/v1/projects-workspace/members/p-02")
        assert response.status_code == 403

    async def test_search_matches_name_or_ref(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["projects.team.member.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/projects-workspace/members", params={"q": "lena"})
        assert {m["id"] for m in response.json()["items"]} == {"p-02"}
