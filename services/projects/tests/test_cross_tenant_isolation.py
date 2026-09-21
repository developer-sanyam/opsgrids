"""The acceptance test for tenancy. Everything the RLS migration does exists to make this pass.

The authorization tests check that the application *refuses*. These check that it could not
succeed even if it *forgot*: a session bound to one organization cannot see or touch another's
projects even when it names them by primary key, with no filter at all.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models import SCHEMA, Project
from platform_core.auth import get_principal
from platform_core.db import TENANT_GUC
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _tenant_session(org: uuid.UUID):
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return session, engine


async def _seed_other_org(admin_session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a second organization's project on the BYPASSRLS admin engine."""
    other_org = uuid.uuid4()
    other_project = uuid.uuid4()
    async with admin_session.begin():
        await admin_session.execute(
            Project.__table__.insert().values(
                id=other_project,
                organization_id=other_org,
                code="PRJ-901",
                name="Beta org project",
                client="Beta Client",
                phase="build",
                health="on_track",
                progress_pct=50,
                budget_minor=1000,
                spent_minor=0,
                currency="INR",
                team_size=3,
                open_tasks=5,
                summary="",
            )
        )
    return other_org, other_project


class TestOneTenantCannotSeeAnother:
    async def test_a_bound_session_sees_only_its_own_projects(
        self, org_id, data, admin_session
    ) -> None:
        other_org, _ = await _seed_other_org(admin_session)
        session, engine = await _tenant_session(org_id)
        try:
            async with session.begin():
                rows = (
                    (await session.execute(text("SELECT organization_id FROM projects.projects")))
                    .scalars()
                    .all()
                )
        finally:
            await session.close()
            await engine.dispose()

        assert rows, "the bound session should see its own organization's projects"
        assert set(rows) == {org_id}
        assert other_org not in set(rows)

    async def test_naming_another_tenants_project_returns_nothing(
        self, org_id, data, admin_session
    ) -> None:
        _, stolen = await _seed_other_org(admin_session)
        session, engine = await _tenant_session(org_id)
        try:
            async with session.begin():
                found = (
                    await session.execute(
                        text("SELECT id FROM projects.projects WHERE id = :id"), {"id": stolen}
                    )
                ).scalar_one_or_none()
        finally:
            await session.close()
            await engine.dispose()

        assert found is None, (
            "a project from another organization was readable by primary key — the policy is "
            "not applying; check the connecting role is NOBYPASSRLS and does not own the table."
        )

    async def test_writing_into_another_tenant_is_refused(
        self, org_id, data, admin_session
    ) -> None:
        """`WITH CHECK`: USING gates existing rows; the new row of an INSERT needs its own check."""
        other_org, _ = await _seed_other_org(admin_session)
        session, engine = await _tenant_session(org_id)
        try:
            with pytest.raises(Exception, match="row-level security"):
                async with session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO projects.projects "
                            "(id, organization_id, code, name, client, phase, health, "
                            " progress_pct, budget_minor, spent_minor, currency, team_size, "
                            " open_tasks, summary, created_at, updated_at) VALUES "
                            "(:id, :org, 'PRJ-Z9', 'Planted', '', 'build', 'on_track', 0, 0, 0, "
                            " 'INR', 0, 0, '', now(), now())"
                        ),
                        {"id": uuid.uuid4(), "org": other_org},
                    )
        finally:
            await session.close()
            await engine.dispose()

    async def test_an_unbound_session_sees_nothing_at_all(self, org_id, data) -> None:
        """Fail closed. A request that somehow skipped the chokepoint reads zero rows."""
        engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session, session.begin():
                count = (
                    await session.execute(text("SELECT count(*) FROM projects.projects"))
                ).scalar_one()
        finally:
            await engine.dispose()

        assert count == 0, (
            f"an unbound session saw {count} project(s). With no tenant set the policy must "
            "match nothing — otherwise forgetting the chokepoint exposes every tenant."
        )


class TestCrossTenantOverHttp:
    """A principal acting for org B, over the real request path, cannot reach org A's rows."""

    async def test_org_b_cannot_reach_org_a_projects(self, client, org_id, data) -> None:
        other_org = uuid.uuid4()
        principal = principal_for(
            other_org,
            ["projects.portfolio.project.read:ORG"],
            subject_ref=uuid.uuid4(),
        )
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            by_code = await client.get("/api/v1/projects-workspace/projects/PRJ-104")
            listed = await client.get("/api/v1/projects-workspace/projects")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert by_code.status_code == 404
        assert listed.status_code == 200
        assert listed.json()["items"] == []
        assert "PRJ-104" not in listed.text

    async def test_org_b_cannot_edit_or_delete_org_as_project(self, client, org_id, data) -> None:
        """The RLS-backed 404 on `GET` must hold for the new `PATCH`/`DELETE` routes too — a
        caller who cannot see a row by its tenant filter must not be able to touch it by
        naming it directly, even with a fully-privileged permission grant."""
        other_org = uuid.uuid4()
        principal = principal_for(
            other_org,
            ["projects.portfolio.project.write:ORG"],
            subject_ref=uuid.uuid4(),
        )
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            patched = await client.patch(
                "/api/v1/projects-workspace/projects/PRJ-104", json={"health": "delayed"}
            )
            deleted = await client.delete("/api/v1/projects-workspace/projects/PRJ-104")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert patched.status_code == 404
        assert deleted.status_code == 404

        reader = principal_for(org_id, ["projects.portfolio.project.read:ORG"])
        app.dependency_overrides[get_principal] = lambda: reader
        try:
            still_there = await client.get("/api/v1/projects-workspace/projects/PRJ-104")
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert still_there.status_code == 200
        assert still_there.json()["health"] == "on_track"  # the cross-org PATCH did not apply

    async def test_org_b_cannot_edit_or_delete_org_as_task(self, client, org_id, data) -> None:
        other_org = uuid.uuid4()
        principal = principal_for(
            other_org, ["projects.board.task.write:ORG"], subject_ref=uuid.uuid4()
        )
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            patched = await client.patch(
                f"/api/v1/projects-workspace/tasks/{data['task_a_id']}", json={"stage": "done"}
            )
            deleted = await client.delete(f"/api/v1/projects-workspace/tasks/{data['task_a_id']}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert patched.status_code == 404
        assert deleted.status_code == 404

    async def test_org_b_cannot_edit_or_delete_org_as_milestone(self, client, org_id, data) -> None:
        other_org = uuid.uuid4()
        principal = principal_for(
            other_org, ["projects.delivery.milestone.write:ORG"], subject_ref=uuid.uuid4()
        )
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            patched = await client.patch(
                f"/api/v1/projects-workspace/milestones/{data['milestone_a_id']}",
                json={"status": "completed"},
            )
            deleted = await client.delete(
                f"/api/v1/projects-workspace/milestones/{data['milestone_a_id']}"
            )
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert patched.status_code == 404
        assert deleted.status_code == 404

    async def test_org_b_cannot_edit_or_delete_org_as_member(self, client, org_id, data) -> None:
        other_org = uuid.uuid4()
        principal = principal_for(
            other_org, ["projects.workspace.member.write:ORG"], subject_ref=uuid.uuid4()
        )
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            patched = await client.patch(
                "/api/v1/projects-workspace/members/p-01", json={"role": "hijacked"}
            )
            deleted = await client.delete("/api/v1/projects-workspace/members/p-01")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert patched.status_code == 404
        assert deleted.status_code == 404
