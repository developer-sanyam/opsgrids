"""The mandate guard on charge-create, and the refund preconditions.

A mandate is a customer's standing authorization with hard edges — a cap, a currency, an
activation moment — and `models.Mandate` states the invariant: no debit under it may exceed it.
These tests prove `create_charge` enforces every edge before a row is written or a provider is
called, and records which mandate authorized the debit.

Refunds are money moving back out, so the preconditions are proven with their negatives: only a
captured charge refunds, only a positive amount, never more in total than was collected — and
each refund carries its **own** provider idempotency key, so a second partial refund is a second
provider refund, not a silent replay of the first (the stub derives `provider_refund_id`
deterministically from the key, which is exactly what makes the distinctness observable).

No service creates mandate rows yet (activation arrives by webhook in a later slice), so tests
construct `Mandate` rows directly in the org-bound session, the way an activated mandate would
exist in production.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.models import (
    CHARGE_CAPTURED,
    MANDATE_ACTIVE,
    MANDATE_PENDING,
    Charge,
    Mandate,
)
from app.providers.razorpay import RazorpayProvider
from app.schemas import ChargeIn
from platform_core.errors import BusinessRuleProblem, NotFoundProblem

pytestmark = pytest.mark.integration


def _charge_in(
    org_id: uuid.UUID,
    *,
    key: str,
    amount_minor: int = 1000,
    currency: str = "USD",
    mandate_id: uuid.UUID | None = None,
) -> ChargeIn:
    return ChargeIn(
        organization_id=org_id,
        amount_minor=amount_minor,
        currency=currency,
        idempotency_key=key,
        mandate_id=mandate_id,
    )


async def _seed_mandate(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    state: str = MANDATE_ACTIVE,
    max_amount_minor: int = 5000,
    currency: str = "USD",
    activated: bool = True,
) -> uuid.UUID:
    """A mandate row as the bank would have left it — `active` and debitable by default."""
    async with session.begin():
        mandate = Mandate(
            organization_id=org_id,
            provider="razorpay",
            provider_mandate_id=f"mandate_{uuid.uuid4().hex[:14]}",
            state=state,
            max_amount_minor=max_amount_minor,
            currency=currency,
            activated_at=datetime.now(UTC) if activated else None,
        )
        session.add(mandate)
        await session.flush()
        return mandate.id


async def _captured_charge(
    session: AsyncSession,
    provider: RazorpayProvider,
    org_id: uuid.UUID,
    *,
    key: str,
    amount_minor: int = 1000,
) -> uuid.UUID:
    """A charge whose money actually moved: created through the service (so it holds a real
    `provider_payment_id`), then flipped to `captured` the way reconciliation would."""
    async with session.begin():
        charge = await services.create_charge(
            session, provider, _charge_in(org_id, key=key, amount_minor=amount_minor)
        )
        charge.state = CHARGE_CAPTURED
        await session.flush()
        return charge.id


class TestMandateGuard:
    async def test_a_charge_naming_a_nonexistent_mandate_is_refused(
        self, session: AsyncSession, provider: RazorpayProvider, org_id: uuid.UUID
    ) -> None:
        """No mandate, no debit — and the same not-found whether the id is nobody's or another
        tenant's, so probing learns nothing."""
        with pytest.raises(NotFoundProblem):
            async with session.begin():
                await services.create_charge(
                    session,
                    provider,
                    _charge_in(org_id, key="m-missing", mandate_id=uuid.uuid4()),
                )

    async def test_a_pending_mandate_cannot_be_debited(
        self, session: AsyncSession, provider: RazorpayProvider, org_id: uuid.UUID
    ) -> None:
        """A multi-day eMandate exists before the bank confirms it. Existing is not debitable:
        `pending` with no `activated_at` is refused."""
        mandate_id = await _seed_mandate(session, org_id, state=MANDATE_PENDING, activated=False)
        with pytest.raises(BusinessRuleProblem, match="cannot be debited"):
            async with session.begin():
                await services.create_charge(
                    session, provider, _charge_in(org_id, key="m-pending", mandate_id=mandate_id)
                )

    async def test_the_cap_is_a_hard_edge_and_the_authority_is_recorded(
        self, session: AsyncSession, provider: RazorpayProvider, org_id: uuid.UUID
    ) -> None:
        """One over the cap is refused; exactly at the cap is the boundary the mandate promises,
        and the charge row records which mandate authorized the debit."""
        mandate_id = await _seed_mandate(session, org_id, max_amount_minor=5000)
        with pytest.raises(BusinessRuleProblem, match="cap"):
            async with session.begin():
                await services.create_charge(
                    session,
                    provider,
                    _charge_in(org_id, key="m-over", amount_minor=5001, mandate_id=mandate_id),
                )

        async with session.begin():
            charge = await services.create_charge(
                session,
                provider,
                _charge_in(org_id, key="m-at-cap", amount_minor=5000, mandate_id=mandate_id),
            )
            charge_id = charge.id

        async with session.begin():
            stored = await session.get(Charge, charge_id)
            assert stored is not None
            assert stored.mandate_id == mandate_id, "the authority a debit ran under is a column"
            assert stored.amount_minor == 5000

    async def test_a_currency_mismatched_charge_is_refused(
        self, session: AsyncSession, provider: RazorpayProvider, org_id: uuid.UUID
    ) -> None:
        mandate_id = await _seed_mandate(session, org_id, currency="INR")
        with pytest.raises(BusinessRuleProblem, match="denominated"):
            async with session.begin():
                await services.create_charge(
                    session,
                    provider,
                    _charge_in(org_id, key="m-currency", currency="USD", mandate_id=mandate_id),
                )


class TestRefundPreconditions:
    async def test_a_non_captured_charge_cannot_be_refunded(
        self, session: AsyncSession, provider: RazorpayProvider, org_id: uuid.UUID
    ) -> None:
        """An authorized charge holds a `provider_payment_id`, but no money moved — that id alone
        proves nothing, and the state is what the guard checks."""
        async with session.begin():
            charge = await services.create_charge(
                session, provider, _charge_in(org_id, key="r-auth-only")
            )
        assert charge.state == "authorized"
        with pytest.raises(BusinessRuleProblem, match="captured"):
            async with session.begin():
                await services.create_refund(session, provider, charge, amount_minor=100)

    async def test_a_zero_or_negative_refund_is_refused(
        self, session: AsyncSession, provider: RazorpayProvider, org_id: uuid.UUID
    ) -> None:
        """Zero refunds nothing and negative would mint money. (The `amount_minor > 0` CHECK is
        the net under this; the service refuses first, with a message.)"""
        charge_id = await _captured_charge(session, provider, org_id, key="r-zero")
        with pytest.raises(BusinessRuleProblem, match="positive"):
            async with session.begin():
                charge = await services.require_charge(session, charge_id)
                await services.create_refund(session, provider, charge, amount_minor=0)
        with pytest.raises(BusinessRuleProblem, match="positive"):
            async with session.begin():
                charge = await services.require_charge(session, charge_id)
                await services.create_refund(session, provider, charge, amount_minor=-100)

    async def test_partial_refunds_may_never_exceed_what_was_collected(
        self, session: AsyncSession, provider: RazorpayProvider, org_id: uuid.UUID
    ) -> None:
        """The cap is cumulative: a first partial refund counts against the charge, so a second
        that would push the total past `amount_minor` is refused."""
        charge_id = await _captured_charge(
            session, provider, org_id, key="r-over", amount_minor=1000
        )
        async with session.begin():
            charge = await services.require_charge(session, charge_id)
            await services.create_refund(session, provider, charge, amount_minor=600)
        with pytest.raises(BusinessRuleProblem, match="exceed"):
            async with session.begin():
                charge = await services.require_charge(session, charge_id)
                await services.create_refund(session, provider, charge, amount_minor=500)

        async with session.begin():
            refunded = await selectors.refunded_total_for_charge(session, charge_id)
        assert refunded == 600, "the refused refund left no row behind"

    async def test_two_partial_refunds_carry_distinct_provider_keys(
        self, session: AsyncSession, provider: RazorpayProvider, org_id: uuid.UUID
    ) -> None:
        """Two partials within the total both succeed — and as two distinct refunds at the
        provider. The key is per-refund (`refund:{refund_id}`); keyed per-charge, the stub (like
        a real idempotent provider) would return the FIRST refund's id again and the second
        partial would silently replay the first at the provider."""
        charge_id = await _captured_charge(
            session, provider, org_id, key="r-two", amount_minor=1000
        )
        async with session.begin():
            charge = await services.require_charge(session, charge_id)
            first = await services.create_refund(session, provider, charge, amount_minor=400)
        async with session.begin():
            charge = await services.require_charge(session, charge_id)
            second = await services.create_refund(session, provider, charge, amount_minor=500)

        assert first.provider_refund_id is not None
        assert first.provider_refund_id.startswith("rfnd_")
        assert first.provider_refund_id != second.provider_refund_id, (
            "each partial refund must reach the provider under its own idempotency key"
        )

        async with session.begin():
            refunded = await selectors.refunded_total_for_charge(session, charge_id)
        assert refunded == 900
