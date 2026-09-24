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

That per-attempt key is exactly what removes the platform-key dedupe every other charge-creating
path leans on — `charge_on_invoice_finalized` and the internal route both derive their key from the
invoice itself, so a second attempt at an already-charged invoice returns the existing charge
rather than raising a new one. A retry mints a fresh key on purpose, so nothing stops it debiting an
invoice a **different** charge already collected — which is exactly what a bank's late,
same-payment capture can now do to a charge this ladder already failed and moved on from (ADR-0004,
PAY-A round 2). `TestARetryForAnAlreadyPaidInvoiceIsSkipped` is the guard that closes it: checked
immediately before `stage_charge`, not left for the sweep or the reconciliation-side alert
(`services._detect_double_collection`) to notice after the money has moved twice.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.config import settings
from app.consumers import charge_on_dunning_retry
from app.models import CHARGE_CAPTURED, Charge
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


async def _seed_captured_charge(
    session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID, *, key: str
) -> Charge:
    """A charge already `captured` for `invoice_id` — standing in for the original charge whose
    late same-payment capture (or an ordinary first collection) already settled it, whichever
    actually happened does not matter to the guard: it only asks whether the invoice is paid."""
    charge = Charge(
        id=uuid.uuid4(),
        organization_id=org_id,
        invoice_id=invoice_id,
        provider="razorpay",
        provider_payment_id=f"pay_{key}",
        amount_minor=149900,
        currency="INR",
        idempotency_key=key,
        state=CHARGE_CAPTURED,
    )
    async with session.begin():
        session.add(charge)
    return charge


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


class TestARetryForAnAlreadyPaidInvoiceIsSkipped:
    """N-1's prevention half. The invoice this retry would debit is already `captured` — most
    likely by the charge whose failure opened this cycle, come back to life through its own late,
    same-payment capture (ADR-0004, PAY-A round 2) — so the retry is dropped before it stages
    anything, rather than debiting an invoice a second time and leaving only the reconciliation
    side's after-the-fact alert to notice."""

    async def test_it_is_not_staged_and_the_event_is_marked_processed(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice_id = uuid.uuid4()
        await _seed_captured_charge(session, org_id, invoice_id, key="already-paid")
        subscription_id = uuid.uuid4()
        envelope = _retry_due(org_id, subscription_id)
        envelope["payload"]["invoice_id"] = str(invoice_id)

        with capture_logs() as logs:
            await charge_on_dunning_retry(envelope)

        charges = await _charges(session)
        assert len(charges) == 1, "the retry must not have raised a second charge"
        assert charges[0].idempotency_key == "already-paid"
        (skip,) = [e for e in logs if e["event"] == "dunning.retry.invoice_already_paid"]
        assert skip["log_level"] == "warning"
        assert skip["invoice_id"] == str(invoice_id)
        assert skip["subscription_id"] == str(subscription_id)

    async def test_a_replay_of_the_skipped_event_is_still_processed_exactly_once(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        invoice_id = uuid.uuid4()
        await _seed_captured_charge(session, org_id, invoice_id, key="already-paid-2")
        subscription_id = uuid.uuid4()
        envelope = _retry_due(org_id, subscription_id)
        envelope["payload"]["invoice_id"] = str(invoice_id)

        with capture_logs() as first_logs:
            await charge_on_dunning_retry(envelope)
        with capture_logs() as second_logs:
            await charge_on_dunning_retry(envelope)  # a redelivery of the exact same event

        assert len(await _charges(session)) == 1
        assert [e for e in first_logs if e["event"] == "dunning.retry.invoice_already_paid"]
        # The redelivery is caught by the processed-events ledger before the invoice is even
        # checked a second time — it does not re-skip, it is simply already handled.
        assert [e for e in second_logs if e["event"] == "dunning.retry.duplicate"]
        assert not [e for e in second_logs if e["event"] == "dunning.retry.invoice_already_paid"]

    async def test_a_retry_for_a_different_invoice_is_unaffected(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The guard is scoped to the retry's own invoice: a captured charge on another invoice —
        even for the same organization — must not block a legitimate one."""
        await _seed_captured_charge(session, org_id, uuid.uuid4(), key="a-different-invoice-paid")
        subscription_id = uuid.uuid4()

        await charge_on_dunning_retry(_retry_due(org_id, subscription_id))

        charges = await _charges(session)
        assert len(charges) == 2
        assert INVOICE_ID in {c.invoice_id for c in charges}
        raised = next(c for c in charges if c.invoice_id == INVOICE_ID)
        assert raised.provider_payment_id is not None, "the legitimate retry must still debit"

    async def test_a_retry_with_no_invoice_id_is_unaffected(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Nothing to check against is not a reason to refuse a retry: the guard is additive, not
        a new requirement that every retry name an invoice."""
        subscription_id = uuid.uuid4()
        envelope = _retry_due(org_id, subscription_id)
        del envelope["payload"]["invoice_id"]

        await charge_on_dunning_retry(envelope)

        (charge,) = await _charges(session)
        assert charge.provider_payment_id is not None
