"""The acceptance test for tenancy. Everything the RLS migration does exists to make this pass.

The authorization tests check that the application *refuses*. These check that it could not
succeed even if it *forgot*: a session bound to one organization cannot see or touch another's
engagements even when it names them by primary key, with no filter at all.
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
    """Create a second organization's engagement on the BYPASSRLS admin engine."""
    other_org = uuid.uuid4()
    other_project = uuid.uuid4()
    async with admin_session.begin():
        await admin_session.execute(
            Project.__table__.insert().values(
                id=other_project,
                organization_id=other_org,
                code="PB-91",
                name="Beta org engagement",
                client="Beta Client",
                status="on_track",
                progress_pct=50,
                budget_minor=1000,
                spent_minor=0,
                currency="USD",
                billable=True,
                open_tasks=5,
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
                    (await session.execute(text("SELECT organization_id FROM project.projects")))
                    .scalars()
                    .all()
                )
        finally:
            await session.close()
            await engine.dispose()

        assert rows, "the bound session should see its own organization's engagements"
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
                        text("SELECT id FROM project.projects WHERE id = :id"), {"id": stolen}
                    )
                ).scalar_one_or_none()
        finally:
            await session.close()
            await engine.dispose()

        assert found is None, (
            "an engagement from another organization was readable by primary key — the policy "
            "is not applying; check the connecting role is NOBYPASSRLS and does not own the "
            "table."
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
                            "INSERT INTO project.projects "
                            "(id, organization_id, code, name, client, status, progress_pct, "
                            " budget_minor, spent_minor, currency, billable, open_tasks, "
                            " created_at, updated_at) VALUES "
                            "(:id, :org, 'PB-Z9', 'Planted', '', 'on_track', 0, 0, 0, 'USD', "
                            " true, 0, now(), now())"
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
                    await session.execute(text("SELECT count(*) FROM project.projects"))
                ).scalar_one()
        finally:
            await engine.dispose()

        assert count == 0, (
            f"an unbound session saw {count} engagement(s). With no tenant set the policy must "
            "match nothing — otherwise forgetting the chokepoint exposes every tenant."
        )


class TestCrossTenantOverHttp:
    """A principal acting for org B, over the real request path, cannot reach org A's rows."""

    async def test_org_b_cannot_reach_org_a_projects(self, client, org_id, data) -> None:
        other_org = uuid.uuid4()
        principal = principal_for(
            other_org,
            ["pulseboard.engagements.project.read:ORG", "pulseboard.board.task.read:ORG"],
            subject_ref=uuid.uuid4(),
        )
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            by_code = await client.get("/api/v1/projects/PB-01")
            listed = await client.get("/api/v1/projects")
            board = await client.get("/api/v1/projects/tasks")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert by_code.status_code == 404
        assert listed.status_code == 200
        assert listed.json()["items"] == []
        assert "PB-01" not in listed.text
        assert board.json()["items"] == []

    async def test_org_b_cannot_mutate_org_a_engagements_or_cards(
        self, client, org_id, data
    ) -> None:
        """Naming another tenant's row by its human code/key must 404 and change nothing —
        not merely be hidden from a list."""
        other_org = uuid.uuid4()
        principal = principal_for(
            other_org,
            [
                "pulseboard.engagements.project.write:ORG",
                "pulseboard.board.task.write:ORG",
                "pulseboard.team.person.write:ORG",
            ],
            subject_ref=uuid.uuid4(),
        )
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            patch_project = await client.patch("/api/v1/projects/PB-01", json={"status": "done"})
            delete_project = await client.delete("/api/v1/projects/PB-01")
            patch_task = await client.patch(
                "/api/v1/projects/tasks/PB-210", json={"status": "done"}
            )
            delete_task = await client.delete("/api/v1/projects/tasks/PB-210")
            patch_person = await client.patch(
                "/api/v1/projects/people/p01", json={"capacity_hours": 1}
            )
            delete_person = await client.delete("/api/v1/projects/people/p01")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert patch_project.status_code == 404
        assert delete_project.status_code == 404
        assert patch_task.status_code == 404
        assert delete_task.status_code == 404
        assert patch_person.status_code == 404
        assert delete_person.status_code == 404

        # Untouched: org A's own view of its project still shows the original status.
        owner = principal_for(org_id, ["pulseboard.engagements.project.read:ORG"])
        app.dependency_overrides[get_principal] = lambda: owner
        try:
            still_there = await client.get("/api/v1/projects/PB-01")
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert still_there.status_code == 200
        assert still_there.json()["status"] == "on_track"
