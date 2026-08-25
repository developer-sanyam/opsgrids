"""Authorization, validation and events — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more than
proving one succeeds, because the success path is exercised by hand anyway. Every read and write
endpoint has a guard here, so a route that loses its permission or its entitlement gate fails a
test rather than shipping.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.main import app
from app.models import AuditLog, Campaign, OutboxEvent
from app.schemas import AudienceIn, CampaignIn
from platform_core.auth import Principal, Scope, get_principal
from platform_core.errors import ConflictProblem, NotFoundProblem
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

READ = "marketing.analytics.campaign.read"
WRITE = "marketing.analytics.campaign.write"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- endpoint authz


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, seeded) -> None:
        """No principal, no override: `get_principal` raises 401 before anything runs."""
        response = await client.get("/api/v1/marketing/campaigns")
        assert response.status_code == 401

    async def test_missing_the_permission_is_forbidden(self, client, org_id, seeded) -> None:
        """Entitled to marketing, but holding no marketing permission at all."""
        with as_principal(principal_for(org_id, [])):
            response = await client.get("/api/v1/marketing/campaigns")
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, seeded
    ) -> None:
        """The gateway gates the product; the service does too. 402, not 403 — the caller's
        permissions are fine, the subscription is not."""
        with as_principal(principal_for(org_id, [f"{READ}:ORG"], entitlements=[])):
            response = await client.get("/api/v1/marketing/campaigns")
        assert response.status_code == 402

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/marketing/campaigns",
            "/api/v1/marketing/audiences",
            "/api/v1/marketing/analytics/revenue-vs-spend",
            "/api/v1/marketing/channels",
            "/api/v1/marketing/overview",
        ],
    )
    async def test_every_read_endpoint_needs_the_read_permission(
        self, client, org_id, seeded, path
    ) -> None:
        """A caller entitled to the product but holding no read grant is refused on every
        read surface — no endpoint is accidentally left ungated."""
        with as_principal(principal_for(org_id, [])):
            response = await client.get(path)
        assert response.status_code == 403

    async def test_a_reader_cannot_create_a_campaign(self, client, org_id, seeded) -> None:
        """The read grant does not reach the write endpoints — the ORG write floor is what stops
        it. This is the regression guard for that floor."""
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            response = await client.post(
                "/api/v1/marketing/campaigns",
                json={
                    "name": "Sneaky",
                    "channel": "email",
                    "audience_id": str(uuid.uuid4()),
                    "start_date": "2026-06-01",
                },
            )
        assert response.status_code == 403

    async def test_a_reader_cannot_create_an_audience(self, client, org_id, seeded) -> None:
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            response = await client.post(
                "/api/v1/marketing/audiences",
                json={"name": "Sneaky", "size": 1, "created_at": "2026-01-01"},
            )
        assert response.status_code == 403

    async def test_an_org_writer_may_create_an_audience(self, client, org_id, seeded) -> None:
        with as_principal(principal_for(org_id, [f"{WRITE}:ORG"])):
            response = await client.post(
                "/api/v1/marketing/audiences",
                json={
                    "name": "In-market buyers",
                    "size": 15620,
                    "criteria": "Pricing-page visit",
                    "created_at": "2025-08-04",
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "In-market buyers"
        assert body["created_at"] == "2025-08-04"


# --------------------------------------------------------------------- read shapes


class TestReadShapes:
    async def test_campaigns_list_matches_the_wire_contract(self, client, org_id, seeded) -> None:
        """The list is the payload the SPA's `getCampaigns` expects: `items` + `total`, money in
        integer minor units, and every field `types.ts#Campaign` names."""
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            response = await client.get("/api/v1/marketing/campaigns?size=200")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 4
        item = body["items"][0]
        assert set(item) >= {
            "id",
            "name",
            "channel",
            "status",
            "currency",
            "budget_minor",
            "spent_minor",
            "revenue_minor",
            "pipeline_minor",
            "leads",
            "conversions",
            "goal",
            "audience_id",
            "start_date",
            "end_date",
            "owner",
        }
        assert isinstance(item["budget_minor"], int)

    async def test_overview_derives_the_kpis_and_carries_the_series(
        self, client, org_id, seeded
    ) -> None:
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            response = await client.get("/api/v1/marketing/overview")
        assert response.status_code == 200
        body = response.json()
        # Seeded spend: (28 + 8 + 3 + 0) lakh; revenue: (142 + 47 + 12 + 0) lakh.
        lakh = 10_000_000
        assert body["spend_minor"] == 39 * lakh
        assert body["revenue_minor"] == 201 * lakh
        assert body["roas"] == (201 * lakh) / (39 * lakh)
        assert len(body["revenue_vs_spend"]) == 2
        assert {c["channel"] for c in body["channels"]} == {"search", "social", "email"}

    async def test_channels_rolls_up_and_orders_by_revenue(self, client, org_id, seeded) -> None:
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            response = await client.get("/api/v1/marketing/channels")
        assert response.status_code == 200
        channels = response.json()
        # search 142 lakh > social 47 lakh > email 12 lakh.
        assert [c["channel"] for c in channels] == ["search", "social", "email"]

    async def test_campaign_detail_and_weekly_series(self, client, org_id, seeded) -> None:
        target = seeded["campaigns"][0]
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            detail = await client.get(f"/api/v1/marketing/campaigns/{target.id}")
            weekly = await client.get(f"/api/v1/marketing/campaigns/{target.id}/performance")
        assert detail.status_code == 200
        assert detail.json()["name"] == target.name
        assert weekly.status_code == 200
        assert len(weekly.json()) == 8

    async def test_a_missing_campaign_is_a_404(self, client, org_id, seeded) -> None:
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            response = await client.get(f"/api/v1/marketing/campaigns/{uuid.uuid4()}")
        assert response.status_code == 404


# --------------------------------------------------------------------- validation


class TestValidation:
    async def test_a_duplicate_audience_name_is_refused(self, session, org_id, seeded) -> None:
        ctx = ctx_for(org_id, WRITE, Scope.ORG)
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_audience(
                    session,
                    ctx,
                    AudienceIn(name="Enterprise India", size=1, created_at="2026-01-01"),
                )

    async def test_a_campaign_needs_an_audience_of_this_org(self, session, org_id, seeded) -> None:
        ctx = ctx_for(org_id, WRITE, Scope.ORG)
        with pytest.raises(NotFoundProblem, match="audience"):
            async with session.begin():
                await services.create_campaign(
                    session,
                    ctx,
                    CampaignIn(
                        name="Orphan",
                        channel="email",
                        audience_id=uuid.uuid4(),
                        start_date="2026-06-01",
                    ),
                )

    async def test_an_audience_from_another_org_is_not_valid(
        self, session, org_id, seeded, admin_session
    ) -> None:
        """The audience check is tenant-scoped: an id from another organization must read as
        'not found here', never resolve into a cross-tenant link."""
        from app.models import Audience

        other_org = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                Audience.__table__.insert().values(
                    id=(stranger := uuid.uuid4()),
                    organization_id=other_org,
                    name="Foreign Segment",
                    size=1,
                    criteria="",
                    defined_on=date(2026, 1, 1),
                )
            )
        ctx = ctx_for(org_id, WRITE, Scope.ORG)
        with pytest.raises(NotFoundProblem):
            async with session.begin():
                await services.create_campaign(
                    session,
                    ctx,
                    CampaignIn(
                        name="Cross",
                        channel="email",
                        audience_id=stranger,
                        start_date="2026-06-01",
                    ),
                )


# --------------------------------------------------------------------- events


class TestEvents:
    async def test_creating_a_campaign_stages_an_unpublished_event_and_an_audit_row(
        self, session, org_id, seeded
    ) -> None:
        """Nothing publishes inline — that is the whole point of the outbox — and the change, its
        audit row and its event commit together."""
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == services.EVT_CAMPAIGN_CREATED
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 4  # the four seeded campaigns
        assert all(e.published_at is None for e in events)

        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "campaign.created")
            )
        ).scalar_one()
        assert audits == 4

    async def test_creating_an_audience_stages_its_event(
        self, session: AsyncSession, org_id, seeded
    ) -> None:
        count = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type == services.EVT_AUDIENCE_CREATED)
            )
        ).scalar_one()
        assert count == 2  # the two seeded audiences

    async def test_seed_produces_the_expected_rows(self, session, org_id, seeded) -> None:
        """A direct count of what the fixture created — the shape the demo seed also produces."""
        campaigns = (await session.execute(select(func.count()).select_from(Campaign))).scalar_one()
        assert campaigns == 4
        assert {c.channel for c in seeded["campaigns"]} == {"search", "social", "email"}
