"""The tenant is a predicate of every by-id charge query, not something left to RLS (M-2).

`selectors.lock_charge` used to look a charge up by primary key alone. On the webhook path the
organization comes from an external body (`notes.organization_id`), so the only thing standing
between "a webhook naming org B" and "org A's charge" was row-level security — which CLAUDE.md is
explicit is a net, not the filter. It held (the tests bind the app role), but a BYPASSRLS session —
the sweep's discovery role, an operator's shell, a future admin path — would have reconciled another
tenant's charge.

Every test here runs on the **BYPASSRLS** `admin_session`, where RLS cannot help: what refuses the
cross-tenant read is the query itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.providers.razorpay import RazorpayProvider
from app.schemas import ChargeIn
from platform_core.db import session_scope
from platform_core.errors import BusinessRuleProblem
from tests.helpers import (
    audit_rows,
    charge_row,
    emitted,
    inbox_rows,
    rows,
    seed_charge,
    webhook_body,
)

pytestmark = pytest.mark.integration


class TestAWebhookNamingAnotherTenantCannotReachThisOnesCharge:
    async def test_the_query_itself_refuses_the_charge_without_row_level_security(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """Notes naming organization B and organization A's charge id, run through the real
        `ingest_webhook` on a session RLS does not bind: A's charge must stay untouched and the
        event be recorded as an unknown charge **under B** — which is what `lock_charge` filtering
        on the organization gives, and what it did not."""
        charge = await seed_charge(client, org_id, key="scope-webhook")
        body = webhook_body(charge, org_b, event="payment.failed", error_description="x")

        async with admin_session.begin():
            ack = await services.ingest_webhook(
                admin_session,
                provider,
                signature=provider.sign(body),
                body=body,
                is_production=True,
                event_id="evt_cross_tenant",
            )

        assert ack.ignored_reason == "unknown_charge" and ack.reconciled is False
        assert (await charge_row(admin_session, charge["id"]))["state"] == "authorized"
        assert await emitted(admin_session, org_id) == []
        assert await inbox_rows(admin_session, org_id) == []
        (row,) = await inbox_rows(admin_session, org_b)
        assert row["disposition_reason"] == "unknown_charge" and row["charge_id"] is None
        assert [a["action"] for a in await audit_rows(admin_session, org_b)] == [
            "payment.webhook.ignored"
        ]

    async def test_the_payment_id_fallback_is_scoped_too(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """The other way `_resolve_charge` finds a charge — by the provider's payment id when the
        stamped charge id matches nothing — must not cross tenants either."""
        charge = await seed_charge(client, org_id, key="scope-payment-id")
        stranger = {"id": str(uuid.uuid4()), "provider_payment_id": charge["provider_payment_id"]}
        body = webhook_body(stranger, org_b, event="payment.captured")

        async with admin_session.begin():
            ack = await services.ingest_webhook(
                admin_session,
                provider,
                signature=provider.sign(body),
                body=body,
                is_production=True,
                event_id="evt_cross_payment",
            )

        assert ack.ignored_reason == "unknown_charge"
        assert (await charge_row(admin_session, charge["id"]))["state"] == "authorized"


class TestEveryByIdChargeQueryFiltersOnTheOrganization:
    async def test_get_and_lock_return_nothing_for_another_organization(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="scope-select")
        charge_id = uuid.UUID(charge["id"])

        async with admin_session.begin():
            assert await selectors.get_charge(admin_session, org_b, charge_id) is None
            assert await selectors.lock_charge(admin_session, org_b, charge_id) is None
            assert (
                await selectors.lock_charge(admin_session, org_b, charge_id, skip_locked=True)
                is None
            )
            # The positive control: the same queries find it for its own organization.
            assert await selectors.get_charge(admin_session, org_id, charge_id) is not None
            assert await selectors.lock_charge(admin_session, org_id, charge_id) is not None

    async def test_the_refund_total_of_another_organizations_charge_is_zero(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="scope-refunds")
        charge_id = uuid.UUID(charge["id"])
        async with admin_session.begin():
            await admin_session.execute(
                text(
                    "INSERT INTO payment.refunds "
                    "(id, organization_id, charge_id, provider, amount_minor, currency, state) "
                    "VALUES (gen_random_uuid(), :org, :charge, 'razorpay', 600, 'USD', 'processed')"
                ),
                {"org": org_id, "charge": charge_id},
            )

        async with admin_session.begin():
            assert await selectors.refunded_total_for_charge(admin_session, org_b, charge_id) == 0
            assert (
                await selectors.refunded_total_for_charge(admin_session, org_id, charge_id) == 600
            )

    async def test_the_lease_claim_cannot_take_another_organizations_charge(
        self,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
        engines: None,
    ) -> None:
        async with session_scope(organization_id=org_id) as s:
            charge, _ = await services.stage_charge(
                s,
                provider,
                ChargeIn(
                    organization_id=org_id,
                    amount_minor=1000,
                    currency="USD",
                    idempotency_key="scope-lease",
                ),
            )
        later = datetime.now(UTC) + timedelta(hours=2)

        async with admin_session.begin():
            wrong = await services.claim_charge_lease(
                admin_session, charge.id, organization_id=org_b, now=later
            )
        assert wrong is None
        before = await rows(
            admin_session, "SELECT attempts FROM payment.charges WHERE id = :i", i=charge.id
        )
        assert before[0]["attempts"] == 1, "another organization's claim spent an attempt"

        async with admin_session.begin():
            right = await services.claim_charge_lease(
                admin_session, charge.id, organization_id=org_id, now=later
            )
        assert right is not None and right.attempts == 2

    async def test_the_sweeps_and_the_recorders_steps_refuse_another_organizations_charge(
        self,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
        engines: None,
    ) -> None:
        async with session_scope(organization_id=org_id) as s:
            charge, _ = await services.stage_charge(
                s,
                provider,
                ChargeIn(
                    organization_id=org_id,
                    amount_minor=1000,
                    currency="USD",
                    idempotency_key="scope-steps",
                ),
            )
        result = services.call_provider_charge(provider, charge)
        before = await charge_row(admin_session, charge.id)

        async with admin_session.begin():
            step = await services.claim_stuck_charge(
                admin_session,
                charge.id,
                organization_id=org_b,
                now=datetime.now(UTC) + timedelta(hours=2),
            )
        assert step == ("not_due", None)
        async with admin_session.begin():
            assert (
                await services.fail_exhausted_charge(
                    admin_session, charge.id, organization_id=org_b
                )
                is None
            )
        with pytest.raises(BusinessRuleProblem):
            async with admin_session.begin():
                await services.finalize_charge(
                    admin_session, charge.id, result, organization_id=org_b
                )

        assert await charge_row(admin_session, charge.id) == before
