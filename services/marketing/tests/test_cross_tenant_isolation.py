"""The acceptance test for tenancy. Everything the RLS migration does exists to make this pass.

The authorization tests check that the application *refuses*. These check that the application
could not succeed even if it *forgot*: a session bound to one organization cannot see or touch
another's campaigns even when it names them by primary key, with no filter at all.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.main import app
from app.models import SCHEMA, Audience, Campaign
from platform_core.auth import get_principal
from platform_core.db import TENANT_GUC
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

READ = "marketing.analytics.campaign.read"
WRITE = "marketing.analytics.campaign.write"


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
    """Create a second organization's audience + campaign on the BYPASSRLS admin engine."""
    other_org = uuid.uuid4()
    aud = uuid.uuid4()
    camp = uuid.uuid4()
    async with admin_session.begin():
        await admin_session.execute(
            Audience.__table__.insert().values(
                id=aud,
                organization_id=other_org,
                name="Beta Segment",
                size=100,
                criteria="",
                defined_on=date(2026, 1, 1),
            )
        )
        await admin_session.execute(
            Campaign.__table__.insert().values(
                id=camp,
                organization_id=other_org,
                name="Beta Campaign",
                channel="email",
                status="live",
                currency="INR",
                budget_minor=1,
                spent_minor=1,
                revenue_minor=1,
                pipeline_minor=0,
                leads=0,
                conversions=0,
                goal="",
                audience_id=aud,
                start_date=date(2026, 1, 1),
                owner="",
            )
        )
    return other_org, camp


class TestOneTenantCannotSeeAnother:
    async def test_a_bound_session_sees_only_its_own_campaigns(
        self, org_id, seeded, admin_session
    ) -> None:
        """The headline. No filter in the query — the database applies it."""
        other_org, _ = await _seed_other_org(admin_session)

        session, engine = await _tenant_session(org_id)
        try:
            async with session.begin():
                rows = (
                    (await session.execute(text("SELECT organization_id FROM marketing.campaigns")))
                    .scalars()
                    .all()
                )
        finally:
            await session.close()
            await engine.dispose()

        assert rows, "the bound session should see its own organization's campaigns"
        assert set(rows) == {org_id}
        assert other_org not in set(rows)

    async def test_naming_another_tenants_campaign_directly_returns_nothing(
        self, org_id, seeded, admin_session
    ) -> None:
        """A lookup by primary key alone — no filter to forget, because the id came from somewhere
        else entirely."""
        _, stolen = await _seed_other_org(admin_session)

        session, engine = await _tenant_session(org_id)
        try:
            async with session.begin():
                found = (
                    await session.execute(
                        text("SELECT id FROM marketing.campaigns WHERE id = :id"), {"id": stolen}
                    )
                ).scalar_one_or_none()
        finally:
            await session.close()
            await engine.dispose()

        assert found is None, (
            "a campaign from another organization was readable by primary key. The policy is not "
            "applying — check the connecting role is NOBYPASSRLS and does not own the table."
        )

    async def test_writing_into_another_tenant_is_refused(
        self, org_id, seeded, admin_session
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
                            "INSERT INTO marketing.audiences "
                            "(id, organization_id, name, size, criteria, defined_on, "
                            " created_at, updated_at) "
                            "VALUES (:id, :org, 'Planted', 1, '', '2026-01-01', now(), now())"
                        ),
                        {"id": uuid.uuid4(), "org": other_org},
                    )
        finally:
            await session.close()
            await engine.dispose()

    async def test_an_unbound_session_sees_nothing_at_all(self, org_id, seeded) -> None:
        """Fail closed. A request that somehow skipped the chokepoint reads zero rows."""
        engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session, session.begin():
                count = (
                    await session.execute(text("SELECT count(*) FROM marketing.campaigns"))
                ).scalar_one()
        finally:
            await engine.dispose()

        assert count == 0, (
            f"an unbound session saw {count} campaign(s). With no tenant set the policy must match "
            "nothing — otherwise forgetting the chokepoint exposes every tenant."
        )


class TestCrossTenantOverHttp:
    """A principal acting for org B, over the real request path, cannot reach org A's rows.

    Every attempt must be a 404 or an empty list, never a 200 carrying org-A data, and no org-A
    identifier may appear in a response body.
    """

    async def test_org_b_cannot_reach_org_a_campaigns(self, client, org_id, seeded) -> None:
        target = seeded["campaigns"][0].id
        other_org = uuid.uuid4()
        principal = principal_for(other_org, [f"{READ}:ORG", f"{WRITE}:ORG"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            by_id = await client.get(f"/api/v1/marketing/campaigns/{target}")
            weekly = await client.get(f"/api/v1/marketing/campaigns/{target}/performance")
            listed = await client.get("/api/v1/marketing/campaigns")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert by_id.status_code == 404
        assert weekly.status_code == 404
        # The list is scoped to org B, which has no campaigns, so it is empty — and crucially
        # carries none of org A's ids.
        assert listed.status_code == 200
        body = listed.json()
        assert body["items"] == []
        assert str(target) not in listed.text

    async def test_org_b_cannot_patch_or_delete_org_a_campaigns(
        self, client, org_id, seeded
    ) -> None:
        target = seeded["campaigns"][0].id
        other_org = uuid.uuid4()
        principal = principal_for(other_org, [f"{READ}:ORG", f"{WRITE}:ORG"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            patched = await client.patch(
                f"/api/v1/marketing/campaigns/{target}", json={"name": "Stolen"}
            )
            deleted = await client.delete(f"/api/v1/marketing/campaigns/{target}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert patched.status_code == 404
        assert deleted.status_code == 404

        # The row itself is untouched — still visible to its real organization.
        principal = principal_for(org_id, [f"{READ}:ORG"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            check = await client.get(f"/api/v1/marketing/campaigns/{target}")
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert check.status_code == 200
        assert check.json()["name"] != "Stolen"

    async def test_org_b_cannot_patch_or_delete_org_a_audiences(
        self, client, org_id, seeded
    ) -> None:
        target = seeded["audiences"][1].id  # not found for the other org regardless of use
        other_org = uuid.uuid4()
        principal = principal_for(other_org, [f"{READ}:ORG", f"{WRITE}:ORG"])
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            patched = await client.patch(
                f"/api/v1/marketing/audiences/{target}", json={"name": "Stolen"}
            )
            deleted = await client.delete(f"/api/v1/marketing/audiences/{target}")
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert patched.status_code == 404
        assert deleted.status_code == 404
