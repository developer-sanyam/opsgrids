"""`GET /internal/v1/insights/export/deals` — insights' own backfill read (ADR-0020 §3,
`docs/insights/api.md` §6.1).

Restricted to a token addressed to deal by name **and** issued by `insights` specifically
(`docs/insights/api.md` §8's own "a token from any caller other than insights gets a 401 on
every §6 route"), bound to the named organization as the first statement so a row from another
tenant is invisible under RLS rather than merely unlisted.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import settings
from app.models import Deal
from platform_core.auth import Scope
from platform_core.internal_auth import mint_service_token
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _token(org_id: uuid.UUID, *, caller: str = "insights") -> str:
    return mint_service_token(
        settings(), audience="deal", caller=caller, claims={"org": str(org_id)}
    )


class TestInsightsExport:
    async def test_without_a_service_token_it_is_refused(self, client, org_id) -> None:
        resp = await client.get(
            "/internal/v1/insights/export/deals", params={"organization_id": str(org_id)}
        )
        assert resp.status_code == 401

    async def test_a_caller_other_than_insights_is_refused(self, client, org_id) -> None:
        resp = await client.get(
            "/internal/v1/insights/export/deals",
            params={"organization_id": str(org_id)},
            headers={"X-Internal-Token": _token(org_id, caller="lead")},
        )
        assert resp.status_code == 401

    async def test_returns_every_deal_including_soft_deleted(
        self, client, org_id, session: AsyncSession
    ) -> None:
        ctx = ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
        async with session.begin():
            live = await services.create_deal(
                session,
                ctx,
                name="Live deal",
                company_id=uuid.uuid4(),
                owner_id=uuid.uuid4(),
                value_minor=100_00,
                close_date=date.today() + timedelta(days=10),
            )
            gone = await services.create_deal(
                session,
                ctx,
                name="Will be deleted",
                company_id=uuid.uuid4(),
                owner_id=uuid.uuid4(),
                value_minor=200_00,
                close_date=date.today() + timedelta(days=10),
            )
        async with session.begin():
            fresh = await session.get(type(gone), gone.id)
            await services.delete_deal(session, ctx, fresh)

        resp = await client.get(
            "/internal/v1/insights/export/deals",
            params={"organization_id": str(org_id)},
            headers={"X-Internal-Token": _token(org_id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        ids = {row["id"] for row in body["rows"]}
        assert str(live.id) in ids
        assert str(gone.id) in ids
        deleted_row = next(row for row in body["rows"] if row["id"] == str(gone.id))
        assert deleted_row["deleted_at"] is not None
        assert body["next"] is None

    async def test_a_row_of_another_organization_is_invisible(
        self, client, org_id, admin_session: AsyncSession
    ) -> None:
        """A deal seeded under a *different* organization (on the BYPASSRLS admin role, so RLS
        cannot be the reason it is missing on the write side) never appears in a page bound to
        `org_id` — the route's own tenancy binding, not the query's explicit filter alone."""
        other_org = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                Deal.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=other_org,
                    name="Someone else's deal",
                    company_id=uuid.uuid4(),
                    value_minor=100_00,
                    currency="INR",
                    stage="lead",
                    owner_id=uuid.uuid4(),
                    probability=10,
                    close_date=date.today() + timedelta(days=10),
                    source="",
                )
            )

        resp = await client.get(
            "/internal/v1/insights/export/deals",
            params={"organization_id": str(org_id)},
            headers={"X-Internal-Token": _token(org_id)},
        )
        assert resp.status_code == 200
        assert resp.json()["rows"] == []
