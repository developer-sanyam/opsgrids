"""`GET /internal/v1/insights/export/leads` — insights' own backfill read (ADR-0020 §3,
`docs/insights/api.md` §6.1). See `services/deal/tests/test_insights_export.py` for the same
shape on a sibling service.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Lead
from platform_core.internal_auth import mint_service_token

pytestmark = pytest.mark.integration


def _token(org_id: uuid.UUID, *, caller: str = "insights") -> str:
    return mint_service_token(
        settings(), audience="lead", caller=caller, claims={"org": str(org_id)}
    )


class TestInsightsExport:
    async def test_without_a_service_token_it_is_refused(self, client, org_id) -> None:
        resp = await client.get(
            "/internal/v1/insights/export/leads", params={"organization_id": str(org_id)}
        )
        assert resp.status_code == 401

    async def test_a_caller_other_than_insights_is_refused(self, client, org_id) -> None:
        resp = await client.get(
            "/internal/v1/insights/export/leads",
            params={"organization_id": str(org_id)},
            headers={"X-Internal-Token": _token(org_id, caller="lead")},
        )
        assert resp.status_code == 401

    async def test_returns_leads_with_no_pii(self, client, org_id, data) -> None:
        resp = await client.get(
            "/internal/v1/insights/export/leads",
            params={"organization_id": str(org_id)},
            headers={"X-Internal-Token": _token(org_id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        ids = {row["id"] for row in body["rows"]}
        assert str(data["lead_a_id"]) in ids
        assert str(data["lead_b_id"]) in ids
        for row in body["rows"]:
            assert set(row.keys()) == {
                "id",
                "status",
                "source",
                "owner_id",
                "converted_at",
                "created_at",
                "updated_at",
                "deleted_at",
            }

    async def test_a_row_of_another_organization_is_invisible(
        self, client, org_id, admin_session: AsyncSession
    ) -> None:
        other_org = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                Lead.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=other_org,
                    full_name="Someone Else",
                    source="manual",
                    status="new",
                    notes="",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )

        resp = await client.get(
            "/internal/v1/insights/export/leads",
            params={"organization_id": str(org_id)},
            headers={"X-Internal-Token": _token(org_id)},
        )
        assert resp.status_code == 200
        ids = {row["id"] for row in resp.json()["rows"]}
        assert str(other_org) not in ids
