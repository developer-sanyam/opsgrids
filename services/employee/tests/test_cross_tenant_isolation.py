"""The acceptance test for tenancy. Everything the RLS migration does exists to make this pass.

The authorization tests check that the application *refuses*. These check that the application
could not succeed even if it *forgot*: a session bound to one organization cannot see or touch
another's employees even when it names them by primary key, with no filter at all.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models import SCHEMA, Employee
from platform_core.auth import get_principal
from platform_core.db import TENANT_GUC
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _tenant_session(org: uuid.UUID):
    """A session bound to one organization, exactly as a request would be."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return session, engine


async def _seed_other_org(admin_session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a second organization's employee on the BYPASSRLS admin engine."""
    other_org = uuid.uuid4()
    other_employee = uuid.uuid4()
    async with admin_session.begin():
        await admin_session.execute(
            Employee.__table__.insert().values(
                id=other_employee,
                organization_id=other_org,
                employee_no="B0001",
                full_name="Beta Person",
                employment_type="full_time",
                work_mode="office",
                status="active",
            )
        )
    return other_org, other_employee


class TestOneTenantCannotSeeAnother:
    async def test_a_bound_session_sees_only_its_own_employees(
        self, org_id, people, admin_session
    ) -> None:
        """The headline. No filter in the query — the database applies it."""
        other_org, _ = await _seed_other_org(admin_session)

        session, engine = await _tenant_session(org_id)
        try:
            async with session.begin():
                rows = (
                    (await session.execute(text("SELECT organization_id FROM employee.employees")))
                    .scalars()
                    .all()
                )
        finally:
            await session.close()
            await engine.dispose()

        assert rows, "the bound session should see its own organization's employees"
        assert set(rows) == {org_id}
        assert other_org not in set(rows)

    async def test_naming_another_tenants_row_directly_returns_nothing(
        self, org_id, people, admin_session
    ) -> None:
        """A lookup by primary key alone — no filter to forget, because the id came from
        somewhere else entirely."""
        _, stolen = await _seed_other_org(admin_session)

        session, engine = await _tenant_session(org_id)
        try:
            async with session.begin():
                found = (
                    await session.execute(
                        text("SELECT id FROM employee.employees WHERE id = :id"), {"id": stolen}
                    )
                ).scalar_one_or_none()
        finally:
            await session.close()
            await engine.dispose()

        assert found is None, (
            "an employee from another organization was readable by primary key. The policy is "
            "not applying — check the connecting role is NOBYPASSRLS and does not own the table."
        )

    async def test_writing_into_another_tenant_is_refused(
        self, org_id, people, admin_session
    ) -> None:
        """`WITH CHECK`: USING gates existing rows, but the new row of an INSERT needs its own
        check, or a tenant could plant rows in somebody else's organization."""
        other_org, _ = await _seed_other_org(admin_session)

        session, engine = await _tenant_session(org_id)
        try:
            with pytest.raises(Exception, match="row-level security"):
                async with session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO employee.employees "
                            "(id, organization_id, employee_no, full_name, employment_type, "
                            " work_mode, status, created_at, updated_at) "
                            "VALUES (:id, :org, 'Z9', 'Planted', 'full_time', 'office', "
                            " 'active', now(), now())"
                        ),
                        {"id": uuid.uuid4(), "org": other_org},
                    )
        finally:
            await session.close()
            await engine.dispose()

    async def test_an_unbound_session_sees_nothing_at_all(self, org_id, people) -> None:
        """Fail closed. A request that somehow skipped the chokepoint reads zero rows."""
        engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session, session.begin():
                count = (
                    await session.execute(text("SELECT count(*) FROM employee.employees"))
                ).scalar_one()
        finally:
            await engine.dispose()

        assert count == 0, (
            f"an unbound session saw {count} employee(s). With no tenant set the policy must "
            "match nothing — otherwise forgetting the chokepoint exposes every tenant."
        )


class TestCrossTenantOverHttp:
    """A principal acting for org B, over the real request path, cannot reach org A's rows.

    This exercises the whole stack — chokepoint, selector, RLS — the way the acceptance suite
    for the platform is meant to. Every attempt must be a 404, never a 200 or 204, and no org-A
    identifier may appear in a response body.
    """

    async def _as_other_org(self, client, people) -> uuid.UUID:
        """Build a principal for a *different* organization and install it on the client."""
        other_org = uuid.uuid4()
        # The principal's organization is B; only its perms and subject reference are borrowed
        # from A's fixture — which is exactly the shape of a genuine cross-tenant attempt.
        principal = principal_for(
            other_org,
            people["manager"],
            [
                "hrms.people.employee.read:ORG",
                "hrms.people.employee.write:ORG",
            ],
        )
        app.dependency_overrides[get_principal] = lambda: principal
        return other_org

    async def test_org_b_cannot_reach_org_a_employees(self, client, org_id, people) -> None:
        target = people["report"].id
        await self._as_other_org(client, people)
        try:
            by_id = await client.get(f"/api/v1/employees/{target}")
            patched = await client.patch(
                f"/api/v1/employees/{target}", json={"job_title": "Hijacked"}
            )
            transitioned = await client.post(
                f"/api/v1/employees/{target}/transition", json={"to": "exited"}
            )
            listed = await client.get("/api/v1/employees")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert by_id.status_code == 404
        assert patched.status_code == 404
        assert transitioned.status_code == 404
        # The list is scoped to org B, which has no employees, so it is empty — and crucially
        # carries none of org A's ids.
        assert listed.status_code == 200
        body = listed.json()
        assert body["items"] == []
        assert str(target) not in listed.text
