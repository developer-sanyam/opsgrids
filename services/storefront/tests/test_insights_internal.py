"""`/internal/v1/insights/export/enquiries` (ADR-0020 slice I7, `docs/insights/api.md` §6.1) —
caller and tenant-binding gates."""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from platform_core.internal_auth import mint_service_token
from tests.helpers import seed_category, seed_product, seed_store

pytestmark = pytest.mark.integration

PUBLIC_ENQUIRY = "/api/v1/storefront/public/enquiry"


def _token(*, caller: str = "insights", audience: str = "storefront") -> str:
    return mint_service_token(settings(), audience=audience, caller=caller)


def _payload(store: str, lines: list[dict[str, object]]) -> dict[str, object]:
    return {
        "store": store,
        "name": "Priya Shah",
        "company": "Shah Traders",
        "email": "priya@shahtraders.example",
        "phone": "9876543210",
        "requirement": "Need a quote for 500 bags of rice.",
        "quantity": "500 bags",
        "lines": lines,
    }


class TestExportAuth:
    async def test_no_token_is_401(self, client, org_id) -> None:
        response = await client.get(
            "/internal/v1/insights/export/enquiries", params={"organization_id": str(org_id)}
        )
        assert response.status_code == 401

    async def test_wrong_caller_is_401(self, client, org_id) -> None:
        response = await client.get(
            "/internal/v1/insights/export/enquiries",
            params={"organization_id": str(org_id)},
            headers={"X-Internal-Token": _token(caller="console")},
        )
        assert response.status_code == 401


class TestExportPage:
    async def test_export_carries_the_enriched_fields(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        category = await seed_category(admin_session, org_id)
        product = await seed_product(admin_session, org_id, category.id)
        lines = [{"product_id": str(product.id), "name": "Anything", "quantity": 5}]
        posted = await client.post(PUBLIC_ENQUIRY, json=_payload(slug, lines))
        assert posted.status_code == 201

        response = await client.get(
            "/internal/v1/insights/export/enquiries",
            params={"organization_id": str(org_id), "limit": 500},
            headers={"X-Internal-Token": _token()},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["rows"]) == 1
        row = body["rows"][0]
        assert row["company"] == "Shah Traders"
        assert row["status"] == "new"
        assert row["store_id"]
        assert row["line_count"] == 1
        assert body["next"] is None

    async def test_a_different_organizations_data_is_invisible(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status="published")
        other = uuid.uuid4()
        response = await client.get(
            "/internal/v1/insights/export/enquiries",
            params={"organization_id": str(other)},
            headers={"X-Internal-Token": _token()},
        )
        assert response.status_code == 200
        assert response.json()["rows"] == []
