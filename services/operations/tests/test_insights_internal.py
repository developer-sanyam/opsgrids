"""`/internal/v1/insights/export/purchase_orders` and `/internal/v1/insights/snapshot`
(ADR-0020 slice I5, `docs/insights/api.md` §6.1, §6.3) — caller and tenant-binding gates."""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from platform_core.internal_auth import mint_service_token

pytestmark = pytest.mark.integration


def _token(*, caller: str = "insights", audience: str = "operations") -> str:
    return mint_service_token(settings(), audience=audience, caller=caller)


class TestExportAuth:
    async def test_no_token_is_401(self, client, org_id) -> None:
        response = await client.get(
            "/internal/v1/insights/export/purchase_orders",
            params={"organization_id": str(org_id)},
        )
        assert response.status_code == 401

    async def test_wrong_caller_is_401(self, client, org_id) -> None:
        response = await client.get(
            "/internal/v1/insights/export/purchase_orders",
            params={"organization_id": str(org_id)},
            headers={"X-Internal-Token": _token(caller="console")},
        )
        assert response.status_code == 401

    async def test_wrong_audience_is_401(self, client, org_id) -> None:
        response = await client.get(
            "/internal/v1/insights/export/purchase_orders",
            params={"organization_id": str(org_id)},
            headers={"X-Internal-Token": _token(audience="finance")},
        )
        assert response.status_code == 401


class TestExportPage:
    async def test_export_carries_the_enriched_fields(self, client, org_id, data) -> None:
        response = await client.get(
            "/internal/v1/insights/export/purchase_orders",
            params={"organization_id": str(org_id), "limit": 500},
            headers={"X-Internal-Token": _token()},
        )
        assert response.status_code == 200
        body = response.json()
        numbers = {row["number"] for row in body["rows"]}
        assert {"PO-2026-0148", "PO-2026-0140"} <= numbers
        for row in body["rows"]:
            assert row["supplier_id"]
            assert row["supplier_name"]
            assert row["currency"]
            assert isinstance(row["total_minor"], int)
        assert body["next"] is None

    async def test_a_different_organizations_data_is_invisible(self, client, org_id, data) -> None:
        other = uuid.uuid4()
        response = await client.get(
            "/internal/v1/insights/export/purchase_orders",
            params={"organization_id": str(other)},
            headers={"X-Internal-Token": _token()},
        )
        assert response.status_code == 200
        assert response.json()["rows"] == []


class TestSnapshot:
    async def test_snapshot_reports_stock_and_counts(self, client, org_id, data) -> None:
        response = await client.get(
            "/internal/v1/insights/snapshot",
            params={"organization_id": str(org_id)},
            headers={"X-Internal-Token": _token()},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["low_stock_count"] == 1
        assert body["out_of_stock_count"] == 0
        assert body["item_count"] == 2
        assert body["currency"] == "INR"
        assert body["inventory_value_minor"] > 0
