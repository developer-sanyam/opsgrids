"""Authorization, scope and validation — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more than
proving one succeeds. The scope tests come in two halves — `ctx.apply` narrows a *list*,
`ctx.assert_can_touch` guards a single *object* — because filtering the list and forgetting the
detail is the named vulnerability. A user who can list only their own engagements must also be
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

    async def test_a_roster_reader_cannot_add_a_person(self, client, session, org_id, data) -> None:
        """Holding person.read but not person.write cannot add to the roster."""
        principal = principal_for(
            org_id, ["pulseboard.team.person.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects/people",
                json={"ref": "p90", "name": "Intruder"},
            )
        assert response.status_code == 403
        assert await selectors.get_person_by_ref(session, org_id, "p90") is None

    async def test_a_writer_may_add_a_person_to_the_roster(
        self, client, session, org_id, data
    ) -> None:
        """The written row is the row the roster then lists — the SPA's `Person` shape."""
        principal = principal_for(
            org_id, ["pulseboard.team.person.write:ORG", "pulseboard.team.person.read:ORG"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects/people",
                json={
                    "ref": "p03",
                    "name": "Iris Fernandes",
                    "role": "QA Lead",
                    "location": "Pune",
                    "capacity_hours": 35,
                },
            )
            listed = await client.get("/api/v1/projects/people")

        assert response.status_code == 201
        body = response.json()
        assert uuid.UUID(body.pop("uuid"))
        assert body == {
            "id": "p03",
            "name": "Iris Fernandes",
            "role": "QA Lead",
            "location": "Pune",
            "capacity_hours": 35,
        }
        assert [p["id"] for p in listed.json()["items"]] == ["p01", "p02", "p03"]

        stored = await selectors.get_person_by_ref(session, org_id, "p03")
        assert stored is not None
        assert stored.organization_id == org_id
        assert stored.capacity_hours == 35

    async def test_a_new_person_takes_the_writer_defaults(self, client, session, org_id) -> None:
        """Only `ref` and `name` are required; the rest default as `create_person` does."""
        principal = principal_for(org_id, ["pulseboard.team.person.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects/people",
                json={"ref": "p07", "name": "Devika Nair"},
            )
        assert response.status_code == 201
        body = response.json()
        assert uuid.UUID(body.pop("uuid"))
        assert body == {
            "id": "p07",
            "name": "Devika Nair",
            "role": "",
            "location": "",
            "capacity_hours": 40,
        }

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
            "uuid": str(data["person_a"].id),
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

    async def test_a_soft_deleted_project_code_still_conflicts(self, session, org_id, data) -> None:
        """`uq_projects_org_code` is not a partial index, so a soft-deleted row still occupies
        the code — the duplicate check must see it and answer 409, not fall through to an
        unhandled `IntegrityError`."""
        ctx = ctx_for(org_id, "pulseboard.engagements.project.write", Scope.ORG)
        async with session.begin():
            await services.delete_project(session, ctx, data["project_a"])
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_project(session, ctx, code="PB-01", name="reused code")

    async def test_a_soft_deleted_task_key_still_conflicts(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "pulseboard.board.task.write", Scope.ORG)
        async with session.begin():
            await services.delete_task(session, ctx, data["task_a"])
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_task(
                    session, ctx, project_id=data["project_b"].id, key="PB-210", title="reused key"
                )

    async def test_a_soft_deleted_person_ref_still_conflicts(self, session, org_id, data) -> None:
        write = ctx_for(org_id, "pulseboard.engagements.project.write", Scope.ORG)
        async with session.begin():
            await services.delete_person(session, write, data["person_a"])
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_person(session, write, ref="p01", name="reused ref")

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

    async def test_an_invalid_roster_payload_is_unprocessable(
        self, client, session, org_id, data
    ) -> None:
        """Each of these is refused by the contract, before any writer runs."""
        principal = principal_for(org_id, ["pulseboard.team.person.write:ORG"])
        rejected = [
            {"ref": "p 03", "name": "Space In Ref"},
            {"ref": "p03", "name": ""},
            {"ref": "p03", "name": "Negative Capacity", "capacity_hours": -1},
            {"name": "No Ref At All"},
        ]
        with as_principal(principal):
            for payload in rejected:
                response = await client.post("/api/v1/projects/people", json=payload)
                assert response.status_code == 422, payload
        assert await selectors.get_person_by_ref(session, org_id, "p03") is None

    async def test_the_roster_payload_cannot_choose_its_own_tenant(
        self, client, session, org_id, data
    ) -> None:
        """`organization_id` comes from the tenant context, never the body: an extra field is
        ignored and the row lands in the caller's own organization."""
        principal = principal_for(org_id, ["pulseboard.team.person.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects/people",
                json={
                    "ref": "p08",
                    "name": "Tenant Hopper",
                    "organization_id": "00000000-0000-0000-0000-0000000000ff",
                },
            )
        assert response.status_code == 201
        stored = await selectors.get_person_by_ref(session, org_id, "p08")
        assert stored is not None
        assert stored.organization_id == org_id

    async def test_a_duplicate_person_ref_is_refused_over_http(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.team.person.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects/people",
                json={"ref": "p01", "name": "Clashes with Nadia"},
            )
        assert response.status_code == 409
        assert response.headers["content-type"].startswith("application/problem+json")

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


# --------------------------------------------------------------------- task mutations


class TestTaskMutations:
    async def test_a_writer_can_move_a_card(self, client, org_id, data) -> None:
        """Persisting a board move is the headline gap this slice closes."""
        principal = principal_for(
            org_id, ["pulseboard.board.task.write:ORG", "pulseboard.board.task.read:ORG"]
        )
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects/tasks/PB-210", json={"status": "review"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "review"
        assert body["key"] == "PB-210"

        with as_principal(principal):
            reread = await client.get("/api/v1/projects/tasks")
        moved = next(t for t in reread.json()["items"] if t["key"] == "PB-210")
        assert moved["status"] == "review"

    async def test_explicit_null_clears_assignee_and_due_date(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.board.task.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects/tasks/PB-210",
                json={"assignee_ref": None, "due_date": None},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["assignee_id"] is None
        assert body["due_date"] is None

    async def test_moving_to_an_unknown_assignee_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.board.task.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects/tasks/PB-210", json={"assignee_ref": "p99"}
            )
        assert response.status_code == 404

    async def test_updating_an_unknown_task_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.board.task.write:ORG"])
        with as_principal(principal):
            response = await client.patch("/api/v1/projects/tasks/PB-NOPE", json={"status": "done"})
        assert response.status_code == 404

    async def test_a_reader_cannot_move_a_card(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.board.task.read:ORG"])
        with as_principal(principal):
            response = await client.patch("/api/v1/projects/tasks/PB-210", json={"status": "done"})
        assert response.status_code == 403

    async def test_own_scope_cannot_move_someone_elses_card(self, client, org_id, data) -> None:
        """Filtering the board and forgetting the object is the named vulnerability."""
        principal = principal_for(
            org_id, ["pulseboard.board.task.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            refused = await client.patch(
                "/api/v1/projects/tasks/PB-215",
                json={"status": "done"},  # subject_b's card
            )
            allowed = await client.patch(
                "/api/v1/projects/tasks/PB-210",
                json={"status": "done"},  # subject_a's card
            )
        assert refused.status_code == 403
        assert allowed.status_code == 200

    async def test_moving_a_card_stages_an_audit_row_and_an_event(
        self, session, org_id, data
    ) -> None:
        from sqlalchemy import func, select

        from app.models import AuditLog, OutboxEvent
        from app.schemas import TaskUpdate

        ctx = ctx_for(org_id, "pulseboard.board.task.write", Scope.ORG)
        async with session.begin():
            await services.update_task(session, ctx, data["task_a"], TaskUpdate(status="review"))
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "task.updated", AuditLog.entity_id == data["task_a"].id)
            )
        ).scalar_one()
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == data["task_a"].id,
                        OutboxEvent.event_type == "pulseboard.board.task.updated",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert audits == 1
        assert len(events) == 1

    async def test_a_writer_can_delete_a_card(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["pulseboard.board.task.write:ORG", "pulseboard.board.task.read:ORG"]
        )
        with as_principal(principal):
            response = await client.delete("/api/v1/projects/tasks/PB-215")
        assert response.status_code == 204
        with as_principal(principal):
            listed = await client.get("/api/v1/projects/tasks")
        assert "PB-215" not in {t["key"] for t in listed.json()["items"]}

    async def test_deleting_an_already_deleted_card_is_not_found(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, ["pulseboard.board.task.write:ORG"])
        with as_principal(principal):
            first = await client.delete("/api/v1/projects/tasks/PB-215")
            second = await client.delete("/api/v1/projects/tasks/PB-215")
        assert first.status_code == 204
        assert second.status_code == 404

    async def test_a_reader_cannot_delete_a_card(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.board.task.read:ORG"])
        with as_principal(principal):
            response = await client.delete("/api/v1/projects/tasks/PB-210")
        assert response.status_code == 403


# --------------------------------------------------------------------- project mutations


class TestProjectMutations:
    async def test_a_writer_can_edit_an_engagement(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.engagements.project.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects/PB-03", json={"status": "at_risk", "progress_pct": 40}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "at_risk"
        assert body["progress_pct"] == 40
        assert body["code"] == "PB-03"

    async def test_code_cannot_be_changed_through_patch(self, client, org_id, data) -> None:
        """`code` is not even a field on `ProjectUpdate` — an attempt is silently ignored."""
        principal = principal_for(org_id, ["pulseboard.engagements.project.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects/PB-03", json={"code": "PB-99", "name": "Renamed"}
            )
        assert response.status_code == 200
        assert response.json()["code"] == "PB-03"

    async def test_editing_an_unknown_project_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.engagements.project.write:ORG"])
        with as_principal(principal):
            response = await client.patch("/api/v1/projects/PB-NOPE", json={"status": "done"})
        assert response.status_code == 404

    async def test_a_reader_cannot_edit_an_engagement(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.engagements.project.read:ORG"])
        with as_principal(principal):
            response = await client.patch("/api/v1/projects/PB-01", json={"status": "done"})
        assert response.status_code == 403

    async def test_own_scope_cannot_edit_someone_elses_engagement(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["pulseboard.engagements.project.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            refused = await client.patch(
                "/api/v1/projects/PB-03",
                json={"status": "done"},  # subject_b's engagement
            )
            allowed = await client.patch(
                "/api/v1/projects/PB-01",
                json={"status": "at_risk"},  # subject_a's engagement
            )
        assert refused.status_code == 403
        assert allowed.status_code == 200

    async def test_a_writer_can_delete_an_engagement_and_its_tasks_cascade(
        self, client, org_id, data
    ) -> None:
        """A soft-deleted engagement takes its own board cards with it in the same transaction,
        so an orphan card never renders its project as "—"."""
        principal = principal_for(
            org_id,
            [
                "pulseboard.engagements.project.write:ORG",
                "pulseboard.engagements.project.read:ORG",
                "pulseboard.board.task.read:ORG",
            ],
        )
        with as_principal(principal):
            response = await client.delete("/api/v1/projects/PB-03")
        assert response.status_code == 204
        with as_principal(principal):
            projects = await client.get("/api/v1/projects")
            tasks = await client.get("/api/v1/projects/tasks")
        assert "PB-03" not in {p["code"] for p in projects.json()["items"]}
        assert "PB-215" not in {t["key"] for t in tasks.json()["items"]}  # PB-03's card

    async def test_deleting_an_already_deleted_engagement_is_not_found(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, ["pulseboard.engagements.project.write:ORG"])
        with as_principal(principal):
            first = await client.delete("/api/v1/projects/PB-03")
            second = await client.delete("/api/v1/projects/PB-03")
        assert first.status_code == 204
        assert second.status_code == 404

    async def test_a_reader_cannot_delete_an_engagement(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.engagements.project.read:ORG"])
        with as_principal(principal):
            response = await client.delete("/api/v1/projects/PB-01")
        assert response.status_code == 403


# --------------------------------------------------------------------- roster mutations


class TestRosterMutations:
    async def test_a_writer_can_add_a_person(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.team.person.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects/people",
                json={"ref": "p05", "name": "Priya Nair", "role": "Analyst", "capacity_hours": 35},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["id"] == "p05"
        assert body["name"] == "Priya Nair"
        assert body["capacity_hours"] == 35
        assert uuid.UUID(body["uuid"])  # the timesheet/standup write id

    async def test_a_duplicate_ref_is_refused(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.team.person.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects/people", json={"ref": "p01", "name": "Someone Else"}
            )
        assert response.status_code == 409

    async def test_a_reader_cannot_add_a_person(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.team.person.read:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/projects/people", json={"ref": "p06", "name": "Refused"}
            )
        assert response.status_code == 403

    async def test_a_member_role_has_no_person_write_by_default(self) -> None:
        """The catalogue guard, restated as a wire-level fact: `PULSEBOARD_MEMBER` never picks
        `team.person.write`, so the People screen's create action is admin-only."""
        from app.catalogue import PULSEBOARD_ROLE_TEMPLATES

        member = next(t for t in PULSEBOARD_ROLE_TEMPLATES if t.code == "PULSEBOARD_MEMBER")
        assert "pulseboard.team.person.write" not in {g.code for g in member.grants}

    async def test_a_writer_can_edit_a_person(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.team.person.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects/people/p01", json={"capacity_hours": 25}
            )
        assert response.status_code == 200
        assert response.json()["capacity_hours"] == 25
        assert response.json()["id"] == "p01"

    async def test_a_reader_cannot_edit_a_person(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.team.person.read:ORG"])
        with as_principal(principal):
            response = await client.patch("/api/v1/projects/people/p01", json={"capacity_hours": 1})
        assert response.status_code == 403

    async def test_editing_an_unknown_person_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.team.person.write:ORG"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/projects/people/p99", json={"capacity_hours": 25}
            )
        assert response.status_code == 404

    async def test_deleting_a_person_nulls_their_assignments(self, client, org_id, data) -> None:
        """Deleting the roster entry does not orphan the engagement/card it owned — they keep
        rendering "Unassigned" instead of joining to a person the roster no longer lists."""
        principal = principal_for(org_id, ["pulseboard.team.person.write:ORG"])
        with as_principal(principal):
            response = await client.delete("/api/v1/projects/people/p01")
        assert response.status_code == 204

        reader = principal_for(
            org_id,
            [
                "pulseboard.engagements.project.read:ORG",
                "pulseboard.board.task.read:ORG",
                "pulseboard.team.person.read:ORG",
            ],
        )
        with as_principal(reader):
            projects = await client.get("/api/v1/projects")
            tasks = await client.get("/api/v1/projects/tasks")
            people = await client.get("/api/v1/projects/people")
        project_a = next(p for p in projects.json()["items"] if p["code"] == "PB-01")
        task_a = next(t for t in tasks.json()["items"] if t["key"] == "PB-210")
        assert project_a["owner_id"] is None
        assert task_a["assignee_id"] is None
        assert "p01" not in {p["id"] for p in people.json()["items"]}

    async def test_a_reader_cannot_delete_a_person(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.team.person.read:ORG"])
        with as_principal(principal):
            response = await client.delete("/api/v1/projects/people/p02")
        assert response.status_code == 403
