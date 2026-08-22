"""The acceptance test for tenancy. Everything else in ADR-0002 exists to make this pass.

Until now isolation was a property of every query remembering its filter. These tests assert
it as a property of the *database*: a session bound to one organization cannot see or touch
another's rows even when it asks for them directly by primary key, with no filter at all.

That last part is what makes this different from the authorization tests. Those check that the
application refuses. These check that the application could not succeed even if it forgot.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import services
from app.config import settings
from app.models import SCHEMA, Organization
from app.schemas import OrganizationIn
from platform_core.db import TENANT_GUC

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _tenant_session(org_id: uuid.UUID):
    """A session bound to one organization, exactly as a request would be."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

    return session, engine


@pytest.fixture
def other_org_factory(admin_session: AsyncSession):
    """A second organization, created where organizations legitimately are."""

    async def _make(slug: str) -> Organization:
        async with admin_session.begin():
            org = await services.create_organization(
                admin_session,
                OrganizationIn(name=f"{slug.title()} Corp", slug=slug),
                actor_user_id=None,
            )
            await services.add_member(
                admin_session,
                org_id=org.id,
                email=f"person@{slug}.example.com",
                full_name="Someone Else",
                actor_user_id=None,
                role_codes=["MEMBER"],
            )
        return org

    return _make


class TestOneTenantCannotSeeAnother:
    async def test_a_bound_session_sees_only_its_own_memberships(
        self, org: Organization, people, other_org_factory
    ) -> None:
        """The headline. No filter in the query — the database applies it."""
        other = await other_org_factory("beta")

        session, engine = await _tenant_session(org.id)
        try:
            async with session.begin():
                rows = (
                    (await session.execute(text("SELECT organization_id FROM account.memberships")))
                    .scalars()
                    .all()
                )
        finally:
            await session.close()
            await engine.dispose()

        assert rows, "the bound session should see its own organization's memberships"
        assert set(rows) == {org.id}, (
            f"a session bound to {org.id} saw rows from {set(rows) - {org.id}}. The query "
            "carried no organization filter — this is the database's job now."
        )
        assert other.id not in set(rows)

    async def test_naming_another_tenants_row_directly_returns_nothing(
        self, org: Organization, people, other_org_factory, admin_session: AsyncSession
    ) -> None:
        """The case a forgotten `WHERE` produces: a lookup by primary key alone.

        This is the one an application-level filter cannot save you from, because there is no
        filter to forget — the id came from somewhere else entirely.
        """
        other = await other_org_factory("gamma")
        stolen = (
            await admin_session.execute(
                text("SELECT id FROM account.memberships WHERE organization_id = :o"),
                {"o": other.id},
            )
        ).scalar_one()

        session, engine = await _tenant_session(org.id)
        try:
            async with session.begin():
                found = (
                    await session.execute(
                        text("SELECT id FROM account.memberships WHERE id = :id"), {"id": stolen}
                    )
                ).scalar_one_or_none()
        finally:
            await session.close()
            await engine.dispose()

        assert found is None, (
            "a membership from another organization was readable by primary key. The policy "
            "is not applying — check the connecting role is NOBYPASSRLS and does not own the "
            "table."
        )

    async def test_writing_into_another_tenant_is_refused(
        self, org: Organization, people, other_org_factory
    ) -> None:
        """`WITH CHECK`, and why `USING` alone is not enough.

        `USING` gates which existing rows a statement may touch; it is not applied to the new
        row of an INSERT. Without `WITH CHECK` a tenant could insert rows belonging to somebody
        else — invisible to itself, fully visible to the victim.
        """
        other = await other_org_factory("delta")

        session, engine = await _tenant_session(org.id)
        try:
            with pytest.raises(Exception, match="row-level security"):
                async with session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO account.locations "
                            "(id, organization_id, code, name, created_at, updated_at) "
                            "VALUES (:id, :org, 'HQ', 'Head Office', now(), now())"
                        ),
                        {"id": uuid.uuid4(), "org": other.id},
                    )
        finally:
            await session.close()
            await engine.dispose()

    async def test_an_unbound_session_sees_nothing_at_all(self, org: Organization, people) -> None:
        """Fail closed.

        A request that somehow skipped the chokepoint reads zero rows rather than every row.
        Quiet, which is why the application asserts the binding separately — but quiet in the
        safe direction.
        """
        engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session, session.begin():
                count = (
                    await session.execute(text("SELECT count(*) FROM account.memberships"))
                ).scalar_one()
        finally:
            await engine.dispose()

        assert count == 0, (
            f"an unbound session saw {count} membership(s). With no tenant set the policy must "
            "match nothing — otherwise forgetting the chokepoint exposes every tenant."
        )
