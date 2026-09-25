"""`/internal/v1/insights/export/tasks` (ADR-0020 slice I6, `docs/insights/api.md` §6.1) —
caller and tenant-binding gates."""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from platform_core.internal_auth import mint_service_token

pytestmark = pytest.mark.integration


def _token(*, caller: str = "insights", audience: str = "projects") -> str:
    return mint_service_token(settings(), audience=audience, caller=caller)


class TestExportAuth:
    async def test_no_token_is_401(self, client, org_id) -> None:
        response = await client.get(
            "/internal/v1/insights/export/tasks", params={"organization_id": str(org_id)}
        )
        assert response.status_code == 401

    async def test_wrong_caller_is_401(self, client, org_id) -> None:
        response = await client.get(
            "/internal/v1/insights/export/tasks",
            params={"organization_id": str(org_id)},
            headers={"X-Internal-Token": _token(caller="console")},
        )
        assert response.status_code == 401


class TestExportPage:
    async def test_export_carries_the_enriched_fields(self, client, org_id, data) -> None:
        response = await client.get(
            "/internal/v1/insights/export/tasks",
            params={"organization_id": str(org_id), "limit": 500},
            headers={"X-Internal-Token": _token()},
        )
        assert response.status_code == 200
        body = response.json()
        titles = {row["title"] for row in body["rows"]}
        assert {"Bulk-order CSV import", "Substation ingestion spike"} <= titles
        for row in body["rows"]:
            assert row["project_id"]
            assert row["stage"]
        assert body["next"] is None

    async def test_a_different_organizations_data_is_invisible(self, client, org_id, data) -> None:
        other = uuid.uuid4()
        response = await client.get(
            "/internal/v1/insights/export/tasks",
            params={"organization_id": str(other)},
            headers={"X-Internal-Token": _token()},
        )
        assert response.status_code == 200
        assert response.json()["rows"] == []
