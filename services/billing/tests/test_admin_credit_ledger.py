"""The staff credit-ledger route: `GET /admin/credit-ledger`.

Three properties (ADR-0003 §2, and the addendum's role matrix):

1. **Audience boundary** — a product (`app`-audience) token, or none, is refused with the same
   404 an unknown route gives.
2. **Role matrix** — `owner` and `billing_ops` may read; `support` gets a plain 403, because the
   audience check has already conceded the surface exists.
3. **Cross-tenant, filtered and paginated** — the read finds every organization's rows, can be
   narrowed by `organization_id`/`currency`/`entry_type`, and comes back as the platform's
   ordinary offset `Page`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, CreditLedgerEntry
from tests.helpers import as_principal, operator_principal

pytestmark = pytest.mark.integration


async def _grant(
    session: AsyncSession, org_id: uuid.UUID, *, amount_minor: int = 10000, currency: str = "INR"
) -> None:
    async with session.begin():
        session.add(
            CreditLedgerEntry(
                organization_id=org_id,
                entry_type="grant",
                amount_minor=amount_minor,
                currency=currency,
                reason="test grant",
                valid_from=datetime.now(UTC),
            )
        )


class TestAudienceAndRoleBoundary:
    async def test_an_owner_reads_the_ledger(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _grant(admin_session, org_id)
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get("/api/v1/admin/credit-ledger")
        assert response.status_code == 200, response.text
        assert response.json()["total"] >= 1

    async def test_billing_ops_may_read(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _grant(admin_session, org_id)
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get("/api/v1/admin/credit-ledger")
        assert response.status_code == 200

    async def test_support_gets_403_not_404(self, client: AsyncClient) -> None:
        """Right audience — the surface's existence is already conceded — so a role mismatch is
        an ordinary Forbidden, not the audience boundary's 404."""
        with as_principal(operator_principal(roles=["support"])):
            response = await client.get("/api/v1/admin/credit-ledger")
        assert response.status_code == 403

    async def test_an_operator_token_that_never_completed_mfa_gets_403(
        self, client: AsyncClient
    ) -> None:
        """m8 of the third-round security review: the right audience and the right role are not
        enough without `otp` in `amr` — `require_operator` refuses a session that only ever
        proved its password, at the route, not just in the dependency's own unit tests."""
        with as_principal(operator_principal(roles=["owner"], amr=["pwd"])):
            response = await client.get("/api/v1/admin/credit-ledger")
        assert response.status_code == 403

    async def test_a_product_audience_token_gets_404(self, client: AsyncClient) -> None:
        with as_principal(operator_principal(staff=False)):
            response = await client.get("/api/v1/admin/credit-ledger")
        assert response.status_code == 404

    async def test_an_unauthenticated_caller_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/admin/credit-ledger")
        assert response.status_code == 401


class TestFilteringAndPagination:
    async def test_filters_by_organization(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        await _grant(admin_session, org_id, amount_minor=5000)
        await _grant(admin_session, org_b, amount_minor=9000)

        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                "/api/v1/admin/credit-ledger", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["organization_id"] == str(org_id)
        assert body["items"][0]["amount_minor"] == 5000

    async def test_reads_across_every_organization_by_default(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        await _grant(admin_session, org_id)
        await _grant(admin_session, org_b)

        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get("/api/v1/admin/credit-ledger")
        assert response.status_code == 200
        seen = {row["organization_id"] for row in response.json()["items"]}
        assert {str(org_id), str(org_b)} <= seen

    async def test_pagination_is_offset_based_like_every_other_list(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        for i in range(3):
            await _grant(admin_session, org_id, amount_minor=1000 + i)

        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                "/api/v1/admin/credit-ledger",
                params={"organization_id": str(org_id), "page": 1, "size": 2},
            )
        body = response.json()
        assert body["total"] == 3
        assert body["size"] == 2
        assert len(body["items"]) == 2

    async def test_an_unbound_application_role_read_is_not_what_serves_this_route(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The directory session, not the tenant-bound one, is what the route depends on —
        proven by seeding on the bypass session and reading with no tenant ever set."""
        await _grant(admin_session, org_id)
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                "/api/v1/admin/credit-ledger", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 200
        assert response.json()["total"] == 1


class TestAuditTrail:
    """MINOR 11 of the security review: a read narrowed to one organization is audited."""

    async def test_a_read_filtered_by_organization_is_audited(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await _grant(admin_session, org_id)
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                "/api/v1/admin/credit-ledger", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 200

        rows = (
            (
                await admin_session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "credit_ledger.viewed",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].acting_as is not None and rows[0].acting_as.startswith("operator:")

    async def test_the_unfiltered_platform_wide_read_is_not_audited(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """An overview across every organization is a different act from looking at one
        customer's ledger — auditing every page of it would be noise, not a trail."""
        await _grant(admin_session, org_id)
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get("/api/v1/admin/credit-ledger")
        assert response.status_code == 200

        rows = (
            (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.action == "credit_ledger.viewed")
                )
            )
            .scalars()
            .all()
        )
        assert rows == []
