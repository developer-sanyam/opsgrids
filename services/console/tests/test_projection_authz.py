"""The projections are RLS-real, and the operator view depends on the deliberate bypass.

Two properties carry the weight (ADR-0002 §2/§3, ADR-0003 §2):

1. **Cross-tenant read on the directory role** returns every organization's projection row — this is
   the operator's whole job, seeing across customers.
2. **The plain app role with no tenant bound returns nothing** — proving the projections carry a
   forced tenant policy, so the bypass is what enables the operator view rather than the app role
   seeing everything anyway. A directory reader that read the same set an unbound app role reads
   would be a bypass proving nothing.

The reader is also SELECT-only: BYPASSRLS is not permission to write, and the one thing between a
bug in an operator read path and a cross-tenant write is the table privilege.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.models import OrgCommercialState, RevenueEvent, UsageSummary

pytestmark = pytest.mark.integration


async def _seed_org(session: AsyncSession, org: uuid.UUID, *, mrr: int, product: str) -> None:
    """Seed one organization's projection rows on the admin (bypass) session."""
    session.add(
        OrgCommercialState(
            organization_id=org,
            mrr_minor=mrr,
            currency="INR",
            active_products=[product],
            subscription_states={
                product: {"state": "active", "amount_minor": mrr, "currency": "INR"}
            },
            locked=False,
        )
    )
    session.add(
        RevenueEvent(
            organization_id=org,
            kind="commerce.subscription.activated",
            amount_minor=mrr,
            currency="INR",
            meta={"from_state": "trial", "to_state": "active", "product_key": product},
        )
    )
    session.add(
        UsageSummary(
            organization_id=org, meter_key=f"{product}.api_call", period="2026-08", quantity=10
        )
    )


class TestCrossTenantRead:
    async def test_the_directory_role_sees_every_organization(
        self,
        admin_session: AsyncSession,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """Seed two organizations; the operator reader returns both — the cross-tenant view."""
        async with admin_session.begin():
            await _seed_org(admin_session, org_id, mrr=50000, product="hrms")
            await _seed_org(admin_session, org_b, mrr=30000, product="crm")

        states = await selectors.all_org_states(directory_session)
        seen = {s.organization_id for s in states}
        assert org_id in seen and org_b in seen, "the directory reader must see across tenants"

        usage = await selectors.all_usage(directory_session)
        assert {u.organization_id for u in usage} == {org_id, org_b}

    async def test_the_app_role_with_no_tenant_sees_nothing(
        self,
        admin_session: AsyncSession,
        unbound_app_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """The companion, so the bug cannot silently return. On the app role with no GUC the forced
        policy hides every row, so the operator view genuinely depends on the bypass — the
        projections are RLS-real, not merely readable by anyone."""
        async with admin_session.begin():
            await _seed_org(admin_session, org_id, mrr=50000, product="hrms")
            await _seed_org(admin_session, org_b, mrr=30000, product="crm")

        states = await selectors.all_org_states(unbound_app_session)
        assert states == [], (
            "the app role saw projection rows with no tenant bound — the policy is not enforced "
            "the directory reader would be pointless"
        )


class TestTheReaderStaysNarrow:
    async def test_the_reader_is_the_bypass_role(self, directory_session: AsyncSession) -> None:
        row = (
            await directory_session.execute(
                text("SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
        assert row.current_user == "atmora_console_directory"
        assert row.rolbypassrls is True, (
            "the operator reader must bypass RLS to read across tenants"
        )

    async def test_the_reader_cannot_write_a_projection(
        self, directory_session: AsyncSession
    ) -> None:
        """SELECT-only. The reader bypasses row-level security, so the one thing between a bug in an
        operator read path and a cross-tenant write is the table privilege — assert it is gone."""
        with pytest.raises(Exception, match="permission denied"):
            await directory_session.execute(
                text("UPDATE console.org_commercial_state SET mrr_minor = 0")
            )

    async def test_the_reader_cannot_reach_another_service_schema(
        self, directory_session: AsyncSession
    ) -> None:
        """BYPASSRLS is not cross-schema: the reader holds USAGE on `console` alone."""
        with pytest.raises(Exception, match="permission denied"):
            await directory_session.execute(text("SELECT 1 FROM auth.credentials"))
