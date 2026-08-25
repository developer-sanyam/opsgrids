"""The acceptance test for tenancy. Everything the RLS migration does exists to make this
pass.

The authorization tests check that the application *refuses*. These check that it could
not succeed even if it *forgot*: a session bound to one organization cannot see or touch
another's companies even when it names them by primary key, with no filter at all.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models import SCHEMA, Company
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
    """Create a second organization's company on the BYPASSRLS admin engine."""
    other_org = uuid.uuid4()
    other_company = uuid.uuid4()
    async with admin_session.begin():
        await admin_session.execute(
            Company.__table__.insert().values(
                id=other_company,
                organization_id=other_org,
                name="Beta Org Industries",
                domain="",
                industry="",
                size_band="",
                city="",
                country="",
                status="prospect",
                owner_id=uuid.uuid4(),
                annual_revenue_minor=0,
                currency="INR",
            )
        )
    return other_org, other_company


class TestOneTenantCannotSeeAnother:
    async def test_a_bound_session_sees_only_its_own_companies(
        self, org_id, data, admin_session
    ) -> None:
        other_org, _ = await _seed_other_org(admin_session)
        session, engine = await _tenant_session(org_id)
        try:
            async with session.begin():
                rows = (
                    (await session.execute(text("SELECT organization_id FROM customer.companies")))
                    .scalars()
                    .all()
                )
        finally:
            await session.close()
            await engine.dispose()

        assert rows, "the bound session should see its own organization's companies"
        assert set(rows) == {org_id}
        assert other_org not in set(rows)

    async def test_naming_another_tenants_company_returns_nothing(
        self, org_id, data, admin_session
    ) -> None:
        _, stolen = await _seed_other_org(admin_session)
        session, engine = await _tenant_session(org_id)
        try:
            async with session.begin():
                found = (
                    await session.execute(
                        text("SELECT id FROM customer.companies WHERE id = :id"), {"id": stolen}
                    )
                ).scalar_one_or_none()
        finally:
            await session.close()
            await engine.dispose()

        assert found is None, (
            "a company from another organization was readable by primary key — the policy is "
            "not applying; check the connecting role is NOBYPASSRLS and does not own the table."
        )

    async def test_writing_into_another_tenant_is_refused(
        self, org_id, data, admin_session
    ) -> None:
        """`WITH CHECK`: USING gates existing rows; the new row of an INSERT needs its own
        check."""
        other_org, _ = await _seed_other_org(admin_session)
        session, engine = await _tenant_session(org_id)
        try:
            with pytest.raises(Exception, match="row-level security"):
                async with session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO customer.companies "
                            "(id, organization_id, name, domain, industry, size_band, city, "
                            " country, status, owner_id, annual_revenue_minor, currency, "
                            " created_at, updated_at) VALUES (:id, :org, 'Planted', '', '', '', "
                            " '', '', 'prospect', :owner, 0, 'INR', now(), now())"
                        ),
                        {"id": uuid.uuid4(), "org": other_org, "owner": uuid.uuid4()},
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
                    await session.execute(text("SELECT count(*) FROM customer.companies"))
                ).scalar_one()
        finally:
            await engine.dispose()

        assert count == 0, (
            f"an unbound session saw {count} company(ies). With no tenant set the policy must "
            "match nothing — otherwise forgetting the chokepoint exposes every tenant."
        )


class TestCrossTenantOverHttp:
    """A principal acting for org B, over the real request path, cannot reach org A's rows."""

    async def test_org_b_cannot_reach_org_a_companies(self, client, org_id, data) -> None:
        other_org = uuid.uuid4()
        principal = principal_for(
            other_org,
            ["crm.book.company.read:ORG", "crm.book.contact.read:ORG"],
            subject_ref=uuid.uuid4(),
        )
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            by_id = await client.get(f"/api/v1/customers/{data['company_a_id']}")
            listed = await client.get("/api/v1/customers")
            contacts = await client.get("/api/v1/contacts")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert by_id.status_code == 404
        assert listed.status_code == 200
        assert listed.json()["items"] == []
        assert "Northwind" not in listed.text
        assert contacts.json()["items"] == []
