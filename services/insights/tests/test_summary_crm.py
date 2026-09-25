"""`GET /summary` for the I3/I4 metrics — metric definitions, authz gates, OWN isolation and
cross-tenant isolation (`docs/insights/api.md` §8, `docs/insights/metrics.md` §4.2).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import CompanyFact, DealFact, LeadFact
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _deal(
    organization_id: uuid.UUID,
    *,
    owner_ref: uuid.UUID,
    stage: str,
    value_minor: int,
    probability: int = 50,
    created_at: datetime,
    closed_at: datetime | None = None,
    currency: str = "INR",
) -> DealFact:
    return DealFact(
        organization_id=organization_id,
        id=uuid.uuid4(),
        name=f"Deal {uuid.uuid4().hex[:6]}",
        stage=stage,
        value_minor=value_minor,
        currency=currency,
        probability=probability,
        owner_ref=owner_ref,
        company_ref=uuid.uuid4(),
        close_date=date(2026, 12, 1),
        closed_at=closed_at,
        source_created_at=created_at,
        source_updated_at=created_at,
    )


def _lead(
    organization_id: uuid.UUID,
    *,
    owner_ref: uuid.UUID | None,
    created_at: datetime,
    converted_at: datetime | None = None,
) -> LeadFact:
    return LeadFact(
        organization_id=organization_id,
        id=uuid.uuid4(),
        status="converted" if converted_at else "new",
        source="form",
        owner_ref=owner_ref,
        converted_at=converted_at,
        source_created_at=created_at,
        source_updated_at=created_at,
    )


def _company(organization_id: uuid.UUID, *, owner_ref: uuid.UUID, status: str) -> CompanyFact:
    now = datetime.now(UTC)
    return CompanyFact(
        organization_id=organization_id,
        id=uuid.uuid4(),
        name=f"Company {uuid.uuid4().hex[:6]}",
        status=status,
        owner_ref=owner_ref,
        source_created_at=now,
        source_updated_at=now,
    )


class TestMetricDefinitions:
    async def test_deals_won_and_won_value_count_only_closed_won_in_window(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        owner = uuid.uuid4()
        in_window = datetime(2026, 9, 10, tzinfo=UTC)
        outside_window = datetime(2026, 8, 1, tzinfo=UTC)
        async with admin_session.begin():
            admin_session.add_all(
                [
                    _deal(
                        org_id,
                        owner_ref=owner,
                        stage="closed_won",
                        value_minor=100_000,
                        created_at=in_window,
                        closed_at=in_window,
                    ),
                    _deal(
                        org_id,
                        owner_ref=owner,
                        stage="closed_won",
                        value_minor=50_000,
                        created_at=outside_window,
                        closed_at=outside_window,
                    ),
                    _deal(
                        org_id,
                        owner_ref=owner,
                        stage="closed_lost",
                        value_minor=999_999,
                        created_at=in_window,
                        closed_at=in_window,
                    ),
                    _deal(
                        org_id,
                        owner_ref=owner,
                        stage="qualified",
                        value_minor=1,
                        created_at=in_window,
                    ),
                ]
            )

        principal = principal_for(
            org_id, entitlements=["crm"], perms=["crm.pipeline.deal.read:ORG"]
        )
        with as_principal(principal):
            response = await client.get(
                "/api/v1/insights/summary",
                params={
                    "metrics": "deals_won,deals_won_value,deals_lost",
                    "period": "custom",
                    "from": "2026-09-01",
                    "to": "2026-09-25",
                    "compare": "none",
                },
            )
        assert response.status_code == 200
        metrics = response.json()["metrics"]
        assert metrics["deals_won"]["value"] == 1
        assert metrics["deals_won_value"]["value"] == 100_000
        assert metrics["deals_won_value"]["currency"] == "INR"
        assert metrics["deals_lost"]["value"] == 1

    async def test_win_rate_is_won_over_decided_not_all_time(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        owner = uuid.uuid4()
        day = datetime(2026, 9, 5, tzinfo=UTC)
        async with admin_session.begin():
            admin_session.add_all(
                [
                    _deal(
                        org_id,
                        owner_ref=owner,
                        stage="closed_won",
                        value_minor=1,
                        created_at=day,
                        closed_at=day,
                    ),
                    _deal(
                        org_id,
                        owner_ref=owner,
                        stage="closed_won",
                        value_minor=1,
                        created_at=day,
                        closed_at=day,
                    ),
                    _deal(
                        org_id,
                        owner_ref=owner,
                        stage="closed_won",
                        value_minor=1,
                        created_at=day,
                        closed_at=day,
                    ),
                    _deal(
                        org_id,
                        owner_ref=owner,
                        stage="closed_lost",
                        value_minor=1,
                        created_at=day,
                        closed_at=day,
                    ),
                ]
            )
        principal = principal_for(
            org_id, entitlements=["crm"], perms=["crm.pipeline.deal.read:ORG"]
        )
        with as_principal(principal):
            response = await client.get(
                "/api/v1/insights/summary",
                params={
                    "metrics": "win_rate",
                    "period": "custom",
                    "from": "2026-09-01",
                    "to": "2026-09-25",
                    "compare": "none",
                },
            )
        assert response.json()["metrics"]["win_rate"]["value"] == 75.0

    async def test_weighted_pipeline_uses_round_half_even_per_deal(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        owner = uuid.uuid4()
        async with admin_session.begin():
            # 100 * 50 / 100 = 50.0 exactly. 101 * 50 / 100 = 50.5 -> banker's rounding to 50.
            admin_session.add_all(
                [
                    _deal(
                        org_id,
                        owner_ref=owner,
                        stage="qualified",
                        value_minor=100,
                        probability=50,
                        created_at=datetime.now(UTC),
                    ),
                    _deal(
                        org_id,
                        owner_ref=owner,
                        stage="qualified",
                        value_minor=101,
                        probability=50,
                        created_at=datetime.now(UTC),
                    ),
                ]
            )
        principal = principal_for(
            org_id, entitlements=["crm"], perms=["crm.pipeline.deal.read:ORG"]
        )
        with as_principal(principal):
            response = await client.get(
                "/api/v1/insights/summary", params={"metrics": "weighted_pipeline"}
            )
        assert response.json()["metrics"]["weighted_pipeline"]["value"] == 100  # 50 + 50

    async def test_customers_counts_only_customer_status(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        owner = uuid.uuid4()
        async with admin_session.begin():
            admin_session.add_all(
                [
                    _company(org_id, owner_ref=owner, status="customer"),
                    _company(org_id, owner_ref=owner, status="customer"),
                    _company(org_id, owner_ref=owner, status="prospect"),
                ]
            )
        principal = principal_for(org_id, entitlements=["crm"], perms=["crm.book.company.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/insights/summary", params={"metrics": "customers"})
        assert response.json()["metrics"]["customers"]["value"] == 2


class TestAuthzGates:
    async def test_own_scoped_caller_sees_only_their_own_deals(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        mine, someone_elses = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with admin_session.begin():
            admin_session.add_all(
                [
                    _deal(org_id, owner_ref=mine, stage="lead", value_minor=1, created_at=now),
                    _deal(
                        org_id, owner_ref=someone_elses, stage="lead", value_minor=1, created_at=now
                    ),
                ]
            )
        principal = principal_for(
            org_id,
            subject_ref=mine,
            entitlements=["crm"],
            perms=["crm.pipeline.deal.read:OWN"],
        )
        with as_principal(principal):
            response = await client.get(
                "/api/v1/insights/summary", params={"metrics": "deals_created"}
            )
        body = response.json()["metrics"]["deals_created"]
        assert body["value"] == 1
        assert body["scope"] == "OWN"

    async def test_org_only_state_metric_refuses_own_scope(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        principal = principal_for(
            org_id,
            subject_ref=uuid.uuid4(),
            entitlements=["crm"],
            perms=["crm.pipeline.deal.read:OWN"],
        )
        with as_principal(principal):
            response = await client.get(
                "/api/v1/insights/summary", params={"metrics": "pipeline_value"}
            )
        assert response.json()["metrics"]["pipeline_value"] == {"state": "no_access"}

    async def test_missing_entitlement_is_not_active_even_with_facts_present(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with admin_session.begin():
            admin_session.add(
                _deal(
                    org_id,
                    owner_ref=uuid.uuid4(),
                    stage="lead",
                    value_minor=1,
                    created_at=datetime.now(UTC),
                )
            )
        principal = principal_for(org_id, perms=["crm.pipeline.deal.read:ORG"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/insights/summary", params={"metrics": "deals_created"}
            )
        assert response.json()["metrics"]["deals_created"] == {
            "state": "not_active",
            "product_key": "crm",
        }


class TestCrossTenant:
    async def test_another_organizations_facts_never_appear(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        now = datetime.now(UTC)
        async with admin_session.begin():
            admin_session.add_all(
                [
                    _deal(
                        org_id, owner_ref=uuid.uuid4(), stage="lead", value_minor=1, created_at=now
                    ),
                    _deal(
                        other_org,
                        owner_ref=uuid.uuid4(),
                        stage="lead",
                        value_minor=999_999,
                        created_at=now,
                    ),
                    _deal(
                        other_org,
                        owner_ref=uuid.uuid4(),
                        stage="lead",
                        value_minor=999_999,
                        created_at=now,
                    ),
                ]
            )
        principal = principal_for(
            org_id, entitlements=["crm"], perms=["crm.pipeline.deal.read:ORG"]
        )
        with as_principal(principal):
            response = await client.get(
                "/api/v1/insights/summary", params={"metrics": "deals_created"}
            )
        assert response.json()["metrics"]["deals_created"]["value"] == 1

    async def test_a_lead_fact_from_another_organization_is_invisible_to_leads_created(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        now = datetime.now(UTC)
        async with admin_session.begin():
            admin_session.add(_lead(other_org, owner_ref=uuid.uuid4(), created_at=now))
        principal = principal_for(org_id, entitlements=["crm"], perms=["crm.lead.lead.read:ORG"])
        with as_principal(principal):
            response = await client.get(
                "/api/v1/insights/summary", params={"metrics": "leads_created"}
            )
        assert response.json()["metrics"]["leads_created"]["value"] == 0
