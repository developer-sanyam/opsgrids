"""Endpoint-level authz negatives and the empty/unavailable states every widget answers with in
I1 (ADR-0020 §4, `docs/insights/api.md` §1, §8).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import access
from app.main import app
from app.models import OrgProfile
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


class TestUnauthenticated:
    async def test_summary_requires_a_principal(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/insights/summary", params={"metrics": "revenue"})
        assert response.status_code == 401

    async def test_series_requires_a_principal(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/v1/insights/series",
            params={"metric": "revenue", "grain": "month", "from": "2026-01-01", "to": "2026-09-01"},
        )
        assert response.status_code == 401

    async def test_activity_requires_a_principal(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/insights/activity")
        assert response.status_code == 401

    async def test_rankings_requires_a_principal(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/insights/rankings", params={"board": "sales_reps"})
        assert response.status_code == 401


class TestSummaryValidation:
    async def test_unknown_metric_key_is_422(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id)):
            response = await client.get(
                "/api/v1/insights/summary", params={"metrics": "not_a_real_metric"}
            )
        assert response.status_code == 422

    async def test_no_metrics_at_all_is_422(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        with as_principal(principal_for(org_id)):
            response = await client.get("/api/v1/insights/summary", params={"metrics": ""})
        assert response.status_code == 422

    async def test_more_than_sixteen_metrics_is_422(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        keys = ",".join(list(access.METRICS)[:17])
        with as_principal(principal_for(org_id)):
            response = await client.get("/api/v1/insights/summary", params={"metrics": keys})
        assert response.status_code == 422

    async def test_bad_period_is_422(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        with as_principal(principal_for(org_id)):
            response = await client.get(
                "/api/v1/insights/summary", params={"metrics": "revenue", "period": "fortnight"}
            )
        assert response.status_code == 422


class TestSummaryStates:
    async def test_missing_entitlement_is_not_active(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, perms=["finance.reports.summary.read:ORG"])):
            response = await client.get("/api/v1/insights/summary", params={"metrics": "revenue"})
        assert response.status_code == 200
        body = response.json()
        assert body["metrics"]["revenue"] == {"state": "not_active", "product_key": "finance"}

    async def test_missing_permission_is_no_access(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, entitlements=["finance"])):
            response = await client.get("/api/v1/insights/summary", params={"metrics": "revenue"})
        assert response.status_code == 200
        assert response.json()["metrics"]["revenue"] == {"state": "no_access"}

    async def test_entitled_and_permitted_is_unavailable_not_a_fake_zero(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(
            principal_for(
                org_id, entitlements=["finance"], perms=["finance.reports.summary.read:ORG"]
            )
        ):
            response = await client.get("/api/v1/insights/summary", params={"metrics": "revenue"})
        assert response.status_code == 200
        metric = response.json()["metrics"]["revenue"]
        assert metric["state"] == "unavailable"
        assert metric["kind"] == "flow"
        assert metric["unit"] == "money"
        assert metric["scope"] == "ORG"
        assert metric.get("value") is None

    async def test_response_echoes_the_resolved_period_and_comparison(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id)):
            response = await client.get(
                "/api/v1/insights/summary",
                params={"metrics": "revenue", "period": "custom", "from": "2026-09-01", "to": "2026-09-25"},
            )
        body = response.json()
        assert body["period"]["from"] == "2026-09-01"
        assert body["period"]["to"] == "2026-09-25"
        assert body["comparison"]["key"] == "previous_period"

    async def test_scoping_reflects_the_callers_own_organization(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        """Cross-tenant: two organizations' profiles exist; a caller bound to `org_id` only ever
        sees its own timezone/currency, never `other_org`'s."""
        async with admin_session.begin():
            admin_session.add_all(
                [
                    OrgProfile(
                        organization_id=org_id, timezone="Asia/Kolkata", currency="INR",
                        fiscal_year_start_month=4, source_updated_at=datetime.now(UTC),
                    ),
                    OrgProfile(
                        organization_id=other_org, timezone="America/New_York", currency="USD",
                        fiscal_year_start_month=1, source_updated_at=datetime.now(UTC),
                    ),
                ]
            )
        with as_principal(principal_for(org_id)):
            response = await client.get("/api/v1/insights/summary", params={"metrics": "revenue"})
        body = response.json()
        assert body["timezone"] == "Asia/Kolkata"
        assert body["currency"] == "INR"


class TestSeries:
    async def test_unknown_metric_is_422(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        with as_principal(principal_for(org_id)):
            response = await client.get(
                "/api/v1/insights/series",
                params={"metric": "nope", "grain": "month", "from": "2026-01-01", "to": "2026-09-01"},
            )
        assert response.status_code == 422

    async def test_current_kind_metric_has_no_series(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id)):
            response = await client.get(
                "/api/v1/insights/series",
                params={
                    "metric": "overdue_tasks", "grain": "day", "from": "2026-09-01",
                    "to": "2026-09-25",
                },
            )
        assert response.status_code == 422

    async def test_beyond_the_grains_own_ceiling_is_422(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id)):
            response = await client.get(
                "/api/v1/insights/series",
                params={
                    "metric": "revenue", "grain": "day", "from": "2026-01-01", "to": "2026-09-25",
                },
            )
        assert response.status_code == 422

    async def test_gated_ok_series_is_unavailable_with_no_points(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(
            principal_for(
                org_id, entitlements=["finance"], perms=["finance.reports.summary.read:ORG"]
            )
        ):
            response = await client.get(
                "/api/v1/insights/series",
                params={
                    "metric": "revenue", "grain": "month", "from": "2026-01-01",
                    "to": "2026-09-01",
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "unavailable"
        assert body["points"] == []


class TestActivity:
    async def test_empty_page(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        with as_principal(principal_for(org_id)):
            response = await client.get("/api/v1/insights/activity")
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body.get("next_cursor") is None

    async def test_malformed_cursor_is_422(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        with as_principal(principal_for(org_id)):
            response = await client.get(
                "/api/v1/insights/activity", params={"before": "not-a-real-cursor!!"}
            )
        assert response.status_code == 422


class TestRankings:
    async def test_unknown_board_is_422(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        with as_principal(principal_for(org_id)):
            response = await client.get(
                "/api/v1/insights/rankings", params={"board": "not_a_real_board"}
            )
        assert response.status_code == 422

    async def test_missing_entitlement_is_not_active(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, perms=["crm.pipeline.deal.read:ORG"])):
            response = await client.get(
                "/api/v1/insights/rankings", params={"board": "sales_reps"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["board"] == "sales_reps"
        assert body["state"] == "not_active"
        assert body["product_key"] == "crm"
        assert body["rows"] == []
        assert "scope" not in body
        assert "period" not in body

    async def test_ok_gate_is_unavailable(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        with as_principal(
            principal_for(org_id, entitlements=["crm"], perms=["crm.pipeline.deal.read:ORG"])
        ):
            response = await client.get(
                "/api/v1/insights/rankings", params={"board": "sales_reps"}
            )
        body = response.json()
        assert body["state"] == "unavailable"
        assert body["scope"] == "ORG"
        assert body["period"]["key"] == "this_month"
