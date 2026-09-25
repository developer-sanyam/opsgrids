"""The staff per-organization invoice read: `GET /admin/invoices?organization_id=...`.

Same three properties as the credit-ledger route it is routed and gated beside (ADR-0003 §2, and
the addendum's role matrix), plus what makes this one different:

1. **Audience boundary** — a product token, or none, is refused with the same 404/401 an unknown
   route gets.
2. **Role matrix** — `owner` and `billing_ops` may read; `support` gets a plain 403.
3. **One organization, and only that one** — `organization_id` is required, the read finds that
   organization's invoices and no other's, and every call is audited (there is no unaudited
   platform-wide overview here to be the exception).
4. **Read-only and honest** — the shape carries no paid date or paid status, because billing
   records what was billed and never whether it was collected.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Invoice
from tests.helpers import as_principal, operator_principal

pytestmark = pytest.mark.integration

PERIOD_START = datetime(2026, 8, 1, tzinfo=UTC)
PERIOD_END = datetime(2026, 9, 1, tzinfo=UTC)


async def _invoice(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    number: str,
    total_minor: int = 149900,
    currency: str = "INR",
    state: str = "finalized",
    period_start: datetime = PERIOD_START,
    period_end: datetime = PERIOD_END,
) -> None:
    async with session.begin():
        session.add(
            Invoice(
                organization_id=org_id,
                number=number,
                state=state,
                currency=currency,
                total_minor=total_minor,
                period_start=period_start,
                period_end=period_end,
                finalized_at=period_start,
                voided_at=period_end if state == "void" else None,
            )
        )


class TestAudienceAndRoleBoundary:
    async def test_an_owner_reads_the_invoices(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _invoice(admin_session, org_id, number="INV-1")
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1

    async def test_billing_ops_may_read(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _invoice(admin_session, org_id, number="INV-1")
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get(
                "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 200

    async def test_support_gets_403_not_404(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        with as_principal(operator_principal(roles=["support"])):
            response = await client.get(
                "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 403

    async def test_an_operator_token_that_never_completed_mfa_gets_403(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal(roles=["owner"], amr=["pwd"])):
            response = await client.get(
                "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 403

    async def test_a_product_audience_token_gets_404(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal(staff=False)):
            response = await client.get(
                "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 404

    async def test_an_unauthenticated_caller_is_rejected(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        response = await client.get(
            "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
        )
        assert response.status_code == 401


class TestOneOrganizationOnly:
    async def test_organization_id_is_required(self, client: AsyncClient) -> None:
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get("/api/v1/admin/invoices")
        assert response.status_code == 422

    async def test_another_organizations_invoices_are_never_returned(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        await _invoice(admin_session, org_id, number="A-1", total_minor=5000)
        await _invoice(admin_session, org_b, number="B-1", total_minor=9000)

        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert [row["number"] for row in body["items"]] == ["A-1"]
        assert body["items"][0]["organization_id"] == str(org_id)

    async def test_an_organization_with_no_invoices_is_an_empty_page_not_an_error(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total"] == 0


class TestShapeAndPagination:
    async def test_the_shape_is_number_period_amount_status_and_dates_and_nothing_paid(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _invoice(admin_session, org_id, number="INV-7", total_minor=149900, currency="INR")
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
            )
        row = response.json()["items"][0]
        assert row["number"] == "INV-7"
        assert row["status"] == "finalized"
        assert row["total_minor"] == 149900
        assert row["currency"] == "INR"
        assert row["period_start"].startswith("2026-08-01")
        assert row["period_end"].startswith("2026-09-01")
        assert row["issued_at"].startswith("2026-08-01")
        assert row["voided_at"] is None
        assert "paid_at" not in row and "paid" not in row, (
            "billing never records collection; a paid field here would be invented"
        )
        assert "lines" not in row, "the cross-tenant surface carries no line items"

    async def test_a_void_invoice_reports_void_and_its_void_date(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _invoice(admin_session, org_id, number="INV-V", state="void")
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
            )
        row = response.json()["items"][0]
        assert row["status"] == "void"
        assert row["voided_at"] is not None

    async def test_newest_period_first_and_offset_paginated(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        for month in (1, 2, 3):
            await _invoice(
                admin_session,
                org_id,
                number=f"INV-{month}",
                period_start=datetime(2026, month, 1, tzinfo=UTC),
                period_end=datetime(2026, month + 1, 1, tzinfo=UTC),
            )
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                "/api/v1/admin/invoices",
                params={"organization_id": str(org_id), "page": 1, "size": 2},
            )
        body = response.json()
        assert body["total"] == 3
        assert body["size"] == 2
        assert [row["number"] for row in body["items"]] == ["INV-3", "INV-2"]


class TestAuditTrail:
    async def test_every_read_is_audited_against_the_organization(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 200

        rows = (
            (
                await admin_session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "invoices.viewed",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].acting_as is not None and rows[0].acting_as.startswith("operator:")

    async def test_a_refused_read_writes_no_audit_row(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal(roles=["support"])):
            response = await client.get(
                "/api/v1/admin/invoices", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 403
        rows = (
            (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.action == "invoices.viewed")
                )
            )
            .scalars()
            .all()
        )
        assert rows == []
