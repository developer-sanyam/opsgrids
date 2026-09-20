"""Authorization, scope and validation — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more than
proving one succeeds. The scope tests come in two halves — `ctx.apply` narrows a *list*,
`ctx.assert_can_touch` guards a single *object* — because filtering the list and forgetting the
detail is the named vulnerability. A user who can list only their own engagements must also be
refused another's by code.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from app.schemas import TaskIn
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
        ctx = ctx_for(org_id, "pulseboard.engagements.project.read", Scope.ORG)
        assert await self._codes(session, ctx) == {"PB-01", "PB-03"}

    async def test_own_scope_sees_only_projects_you_own(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id,
            "pulseboard.engagements.project.read",
            Scope.OWN,
            subject_ref=data["subject_a"],
        )
        assert await self._codes(session, ctx) == {"PB-01"}

    async def test_own_scope_with_no_subject_sees_nothing(self, session, org_id, data) -> None:
        """OWN with nothing to own must match nothing, never everything."""
        ctx = ctx_for(org_id, "pulseboard.engagements.project.read", Scope.OWN, subject_ref=None)
        assert await self._codes(session, ctx) == set()

    async def test_a_search_cannot_widen_scope(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id,
            "pulseboard.engagements.project.read",
            Scope.OWN,
            subject_ref=data["subject_a"],
        )
        stmt = selectors.projects_stmt(ctx, q="meridian")  # matches only PB-03 (subject_b's)
        assert (await session.execute(stmt)).all() == []

    async def test_own_scope_narrows_the_board_to_my_cards(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id, "pulseboard.board.task.read", Scope.OWN, subject_ref=data["subject_a"]
        )
        rows = (await session.execute(selectors.tasks_stmt(ctx))).scalars().all()
        assert {t.key for t in rows} == {"PB-210"}


# --------------------------------------------------------------------- object-level


class TestObjectLevelChecks:
    async def test_scope_does_not_leak_through_the_detail_route(self, client, org_id, data) -> None:
        """A caller with OWN scope is refused another's engagement by code, but not their own."""
        principal = principal_for(
            org_id,
            ["pulseboard.engagements.project.read:OWN"],
            subject_ref=data["subject_a"],
        )
        with as_principal(principal):
            refused = await client.get("/api/v1/projects/PB-03")  # subject_b's
            allowed = await client.get("/api/v1/projects/PB-01")  # subject_a's
        assert refused.status_code == 403
        assert allowed.status_code == 200
        assert allowed.json()["code"] == "PB-01"


# --------------------------------------------------------------------- endpoint authz


READ_ENDPOINTS = [
    "/api/v1/projects",
    "/api/v1/projects/tasks",
    "/api/v1/projects/people",
    "/api/v1/projects/summary",
    "/api/v1/projects/PB-01",
]


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        for path in READ_ENDPOINTS:
            response = await client.get(path)
            assert response.status_code == 401, path

    async def test_missing_the_permission_is_forbidden(self, client, org_id, data) -> None:
        """Entitled to pulseboard, but holding no pulseboard permission at all."""
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
            [
                "pulseboard.engagements.project.read:ORG",
                "pulseboard.board.task.read:ORG",
                "pulseboard.team.person.read:ORG",
            ],
            subject_ref=data["subject_a"],
            entitlements=[],
        )
        with as_principal(principal):
            projects = await client.get("/api/v1/projects")
            tasks = await client.get("/api/v1/projects/tasks")
            people = await client.get("/api/v1/projects/people")
        assert projects.status_code == 402
        assert tasks.status_code == 402
        assert people.status_code == 402

    async def test_a_reader_cannot_create_a_project(self, client, org_id, data) -> None:
        """Holding project.read but not project.write cannot create — the guard is by name."""
        principal = principal_for(
            org_id, ["pulseboard.engagements.project.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects",
                json={"code": "PB-90", "name": "Intruder project"},
            )
        assert response.status_code == 403

    async def test_a_task_reader_cannot_create_a_card(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["pulseboard.board.task.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects/tasks",
                json={"key": "PB-900", "project_code": "PB-01", "title": "Intruder card"},
            )
        assert response.status_code == 403

    async def test_a_writer_may_create_a_project(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["pulseboard.engagements.project.write:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects",
                json={
                    "code": "PB-77",
                    "name": "Aurora Data Mesh",
                    "client": "Aurora Systems",
                    "owner_ref": "p01",
                    "status": "on_track",
                    "progress_pct": 5,
                    "budget_minor": 5_000_000,
                    "spent_minor": 0,
                    "currency": "USD",
                    "start_date": "2026-09-01",
                    "due_date": "2027-03-01",
                    "billable": True,
                    "open_tasks": 12,
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["code"] == "PB-77"
        assert body["owner_id"] == "p01"
        assert body["budget_minor"] == 5_000_000

    async def test_an_own_reader_sees_only_their_own_projects_over_http(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["pulseboard.engagements.project.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/projects")
        assert response.status_code == 200
        codes = {item["code"] for item in response.json()["items"]}
        assert codes == {"PB-01"}

    async def test_the_list_matches_the_spa_shape(self, client, org_id, data) -> None:
        """The wire item is exactly the SPA's `Project`: flat fields, person-ref owner_id."""
        principal = principal_for(org_id, ["pulseboard.engagements.project.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/projects?status=on_track")
        assert response.status_code == 200
        items = response.json()["items"]
        assert [i["code"] for i in items] == ["PB-01"]
        item = items[0]
        assert item["owner_id"] == "p01"
        assert item["budget_minor"] == 24_000_000
        assert item["currency"] == "USD"
        assert item["billable"] is True
        assert item["open_tasks"] == 14

    async def test_the_board_list_matches_the_spa_shape(self, client, org_id, data) -> None:
        """A task's `project_id` is the engagement's opaque id, so the SPA's join holds."""
        principal = principal_for(org_id, ["pulseboard.board.task.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/projects/tasks")
        assert response.status_code == 200
        by_key = {t["key"]: t for t in response.json()["items"]}
        assert set(by_key) == {"PB-210", "PB-215"}
        card = by_key["PB-210"]
        assert card["status"] == "in_progress"
        assert card["assignee_id"] == "p01"
        assert card["project_id"] == str(data["project_a"].id)
        assert card["tags"] == ["frontend"]

    async def test_the_roster_matches_the_spa_shape(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.team.person.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/projects/people")
        assert response.status_code == 200
        items = response.json()["items"]
        assert [p["id"] for p in items] == ["p01", "p02"]
        assert items[0] == {
            "id": "p01",
            "name": "Nadia Rahman",
            "role": "Head of Delivery",
            "location": "Bengaluru",
            "capacity_hours": 20,
        }

    async def test_summary_rolls_up_the_portfolio(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.engagements.project.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/projects/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["active_projects"] == 2
        assert body["tasks_due_this_week"] == 1  # PB-210 is due today; PB-215 has no date
        health = {h["status"]: h for h in body["health"]}
        assert set(health) == {"on_track", "at_risk", "blocked"}
        assert health["blocked"]["count"] == 1
        assert health["on_track"]["pct"] == 50
        attention = body["needs_attention"]
        assert [a["code"] for a in attention] == ["PB-03"]  # blocked outranks everything
        assert attention[0]["severity"] == "blocked"


# --------------------------------------------------------------------- validation


class TestValidation:
    async def test_a_duplicate_project_code_is_refused(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "pulseboard.engagements.project.write", Scope.ORG)
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_project(session, ctx, code="PB-01", name="clash")

    async def test_a_task_against_an_unknown_project_is_refused(
        self, session, org_id, data
    ) -> None:
        ctx = ctx_for(org_id, "pulseboard.board.task.write", Scope.ORG)
        with pytest.raises(NotFoundProblem, match="project"):
            async with session.begin():
                await services.create_task_from(
                    session, ctx, TaskIn(key="PB-901", project_code="PB-NOPE", title="x")
                )

    async def test_a_task_against_an_unknown_person_is_refused(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "pulseboard.board.task.write", Scope.ORG)
        with pytest.raises(NotFoundProblem, match="person"):
            async with session.begin():
                await services.create_task_from(
                    session,
                    ctx,
                    TaskIn(key="PB-902", project_code="PB-01", title="x", assignee_ref="p99"),
                )

    async def test_creating_stages_an_audit_row_and_an_outbox_event(
        self, session, org_id, data
    ) -> None:
        """The change, its audit row and its event commit together — nothing publishes inline."""
        from sqlalchemy import func, select

        from app.models import AuditLog, OutboxEvent

        ctx = ctx_for(org_id, "pulseboard.engagements.project.write", Scope.ORG)
        async with session.begin():
            project = await services.create_project(session, ctx, code="PB-50", name="Audited")
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
        assert events[0].event_type == "pulseboard.engagements.project.created"
        assert events[0].published_at is None
