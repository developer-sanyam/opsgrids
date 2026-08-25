"""The other half of the dunning ladder: payment raises the retry debit (ADR-0005 §6).

subscription's sweep decides *when* to retry — it owns the lifecycle and the grace window — and
emits `commerce.subscription.payment_retry_due`. payment decides *how* to debit, because it owns
the provider. Neither reads the other's schema; the whole contract is the event payload, so this
suite drives the real consumer with the payload subscription actually stages — the same fields
asserted from the emitting side in subscription's `tests/test_dunning.py`, class `TestTheLadder`.

The property that matters most is the one Razorpay forces (ADR-0004 §6): there is no provider
idempotency key, so the platform supplies one, and a retry must carry a **new** key per attempt.
Reusing the failed charge's key would hit `ON CONFLICT DO NOTHING` against the charge that already
failed — no row, no provider call, no debit. A ladder that silently collects nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.consumers import charge_on_dunning_retry
from app.models import Charge
from platform_core.db import dispose_engine, init_engine

pytestmark = pytest.mark.integration

CYCLE_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
INVOICE_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
PRODUCT = "hrms"


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer opens its own `session_scope`, which needs the process engine initialised —
    the app lifespan does this in production; here the fixture stands in for it."""
    init_engine(settings())
    yield
    await dispose_engine()


def _retry_due(
    org_id: uuid.UUID,
    subscription_id: uuid.UUID,
    *,
    attempt_no: int = 1,
    event_id: uuid.UUID | None = None,
    amount_minor: int | None = 149900,
) -> dict[str, Any]:
    """Exactly the envelope subscription's sweep stages, field for field."""
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": "commerce.subscription.payment_retry_due",
        "organization_id": str(org_id),
        "payload": {
            "organization_id": str(org_id),
            "subscription_id": str(subscription_id),
            "product_key": PRODUCT,
            "cycle_id": str(CYCLE_ID),
            "attempt_no": attempt_no,
            "max_attempts": 3,
            "scheduled_for": "2026-03-04T09:30:00+00:00",
            "grace_ends_at": "2026-03-15T09:30:00+00:00",
            "invoice_id": str(INVOICE_ID),
            "amount_minor": amount_minor,
            "currency": "INR",
            "idempotency_key": f"dunning:{CYCLE_ID}:{attempt_no}",
        },
    }


async def _charges(session: AsyncSession) -> list[Charge]:
    session.expunge_all()
    async with session.begin():
        rows = (await session.execute(select(Charge).order_by(Charge.created_at))).scalars().all()
    return list(rows)


class TestTheDunningRetryDebit:
    async def test_a_retry_request_raises_a_charge_for_the_same_invoice(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = uuid.uuid4()

        await charge_on_dunning_retry(_retry_due(org_id, subscription_id))

        (charge,) = await _charges(session)
        assert charge.invoice_id == INVOICE_ID
        assert charge.subscription_id == subscription_id
        assert charge.product_key == PRODUCT
        assert charge.amount_minor == 149900
        assert charge.currency == "INR"
        assert charge.idempotency_key == f"dunning:{CYCLE_ID}:1"
        assert charge.provider_payment_id is not None, "the provider was never called"

    async def test_each_attempt_is_its_own_charge(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Three rungs, three debits. If the key were per invoice instead of per attempt these
        would collapse into one charge and the second and third retries would debit nobody."""
        subscription_id = uuid.uuid4()

        for attempt_no in (1, 2, 3):
            await charge_on_dunning_retry(
                _retry_due(org_id, subscription_id, attempt_no=attempt_no)
            )

        charges = await _charges(session)
        assert len(charges) == 3
        assert {c.idempotency_key for c in charges} == {
            f"dunning:{CYCLE_ID}:{n}" for n in (1, 2, 3)
        }

    async def test_a_redelivered_retry_request_is_one_debit(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Idempotent twice over: the `processed_events` ledger catches the redelivery, and the
        unique platform key would catch it even if the ledger did not. A duplicate charge is a
        duplicate debit, so both layers are wanted."""
        subscription_id = uuid.uuid4()
        envelope = _retry_due(org_id, subscription_id)

        await charge_on_dunning_retry(envelope)
        await charge_on_dunning_retry(envelope)

        assert len(await _charges(session)) == 1

    async def test_the_same_attempt_under_two_event_ids_is_still_one_debit(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A DLQ replay, or a relay that published the same rung twice under different ids. The
        idempotency ledger cannot help here — the key is what does, at the database."""
        subscription_id = uuid.uuid4()

        await charge_on_dunning_retry(_retry_due(org_id, subscription_id, attempt_no=2))
        await charge_on_dunning_retry(_retry_due(org_id, subscription_id, attempt_no=2))

        assert len(await _charges(session)) == 1

    async def test_a_retry_with_no_amount_debits_nothing(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A zero-value debit is a provider call and a sponsor-bank fee that collects nothing. The
        failure that opened the cycle always carries the amount, so a missing one is a bug
        upstream — logged and skipped, not turned into a charge for ₹0."""
        subscription_id = uuid.uuid4()

        await charge_on_dunning_retry(_retry_due(org_id, subscription_id, amount_minor=None))

        assert await _charges(session) == []
