"""The provider webhook — signature, idempotency, reconciliation, and tenant isolation.

The webhook is the reconciliation seam: a signature-verified provider callback moves a charge to
its terminal state and emits `commerce.payment.captured` / `.failed`. The properties proven here
are the ones the rest of commerce leans on — a captured payment is recorded once, a failed one
emits the event that suspends a subscription, a re-delivery changes nothing, and a callback for one
tenant cannot touch another's charge.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Charge, OutboxEvent, ProviderEvent
from app.providers.razorpay import RazorpayProvider
from tests.helpers import razorpay_webhook_body, service_token

pytestmark = pytest.mark.integration

WEBHOOK = "/api/v1/webhooks/razorpay"
CHARGES = "/internal/v1/charges"


async def _seed_charge(
    client: AsyncClient,
    org_id: uuid.UUID,
    *,
    key: str,
    subscription_id: uuid.UUID | None = None,
    product_key: str | None = None,
) -> dict:
    payload: dict = {
        "organization_id": str(org_id),
        "amount_minor": 1000,
        "currency": "USD",
        "idempotency_key": key,
    }
    if subscription_id is not None:
        payload["subscription_id"] = str(subscription_id)
    if product_key is not None:
        payload["product_key"] = product_key
    response = await client.post(
        CHARGES, headers={"X-Internal-Token": service_token()}, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def _post(provider: RazorpayProvider, body: bytes) -> dict:
    return {"content": body, "headers": {"X-Razorpay-Signature": provider.sign(body)}}


class TestReconciliation:
    async def test_a_captured_webhook_captures_the_charge_and_emits(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        charge = await _seed_charge(client, org_id, key="cap")
        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id=charge["provider_payment_id"],
            organization_id=org_id,
            charge_id=uuid.UUID(charge["id"]),
        )
        response = await client.post(WEBHOOK, **_post(provider, body))
        assert response.status_code == 200, response.text
        ack = response.json()
        assert ack["reconciled"] is True and ack["duplicate"] is False

        async with session.begin():
            stored = await session.get(Charge, uuid.UUID(charge["id"]))
            captured = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == "commerce.payment.captured")
                )
            ).scalar_one()
        assert stored is not None and stored.state == "captured"
        assert captured == 1

    async def test_a_failed_webhook_emits_payment_failed_carrying_the_subscription(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """The event that closes the loop. Its payload must carry `subscription_id` — that is what
        the subscription service reads to know which subscription to suspend (ADR-0004 §5)."""
        subscription_id = uuid.uuid4()
        charge = await _seed_charge(
            client, org_id, key="fail", subscription_id=subscription_id, product_key="hrms"
        )
        body = razorpay_webhook_body(
            event="payment.failed",
            provider_payment_id=charge["provider_payment_id"],
            organization_id=org_id,
            charge_id=uuid.UUID(charge["id"]),
            subscription_id=subscription_id,
            product_key="hrms",
            error_description="card_declined",
        )
        response = await client.post(WEBHOOK, **_post(provider, body))
        assert response.status_code == 200 and response.json()["reconciled"] is True

        async with session.begin():
            stored = await session.get(Charge, uuid.UUID(charge["id"]))
            payload = (
                await session.execute(
                    select(OutboxEvent.payload).where(
                        OutboxEvent.event_type == "commerce.payment.failed"
                    )
                )
            ).scalar_one()
        assert stored is not None and stored.state == "failed"
        assert stored.failure_reason == "card_declined"
        assert payload["organization_id"] == str(org_id)
        assert payload["subscription_id"] == str(subscription_id)
        assert payload["product_key"] == "hrms"
        # Subscription records it on a trial conversion that the failure beat to the billing
        # run's record step (ADR-0005 addendum, review fix 1).
        assert payload["failure_reason"] == "card_declined"


class TestSignature:
    async def test_an_invalid_signature_is_refused_and_records_nothing(
        self,
        client: AsyncClient,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id="pay_x",
            organization_id=org_id,
            charge_id=uuid.uuid4(),
        )
        response = await client.post(
            WEBHOOK, content=body, headers={"X-Razorpay-Signature": "not-a-valid-signature"}
        )
        assert response.status_code == 400
        async with session.begin():
            count = (
                await session.execute(select(func.count()).select_from(ProviderEvent))
            ).scalar_one()
        assert count == 0, "an unverified webhook must never be recorded"

    async def test_a_missing_signature_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id="pay_x",
            organization_id=org_id,
            charge_id=uuid.uuid4(),
        )
        response = await client.post(WEBHOOK, content=body)
        assert response.status_code == 400


class TestIdempotency:
    async def test_a_replayed_webhook_is_one_inbox_row_and_one_event(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """At-least-once delivery: a provider re-delivers freely. The same `event_id` twice is one
        inbox row and one reconciliation."""
        charge = await _seed_charge(client, org_id, key="replay")
        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id=charge["provider_payment_id"],
            organization_id=org_id,
            charge_id=uuid.UUID(charge["id"]),
            event_id="evt_fixed_id",
        )
        first = await client.post(WEBHOOK, **_post(provider, body))
        second = await client.post(WEBHOOK, **_post(provider, body))
        assert first.json()["duplicate"] is False
        assert second.json()["duplicate"] is True and second.json()["reconciled"] is False

        async with session.begin():
            inbox = (
                await session.execute(select(func.count()).select_from(ProviderEvent))
            ).scalar_one()
            events = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == "commerce.payment.captured")
                )
            ).scalar_one()
        assert inbox == 1
        assert events == 1

    async def test_two_distinct_events_for_one_terminal_outcome_emit_once(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """Two different provider event ids both asserting the charge failed: the first moves it
        and emits; the second is a no-op at the charge level — a failed charge is already failed."""
        charge = await _seed_charge(client, org_id, key="twice")
        common: dict = {
            "event": "payment.failed",
            "provider_payment_id": charge["provider_payment_id"],
            "organization_id": org_id,
            "charge_id": uuid.UUID(charge["id"]),
        }
        a = await client.post(
            WEBHOOK, **_post(provider, razorpay_webhook_body(event_id="evt_a", **common))
        )
        b = await client.post(
            WEBHOOK, **_post(provider, razorpay_webhook_body(event_id="evt_b", **common))
        )
        assert a.json()["reconciled"] is True
        assert b.json()["duplicate"] is False and b.json()["reconciled"] is False

        async with session.begin():
            failed = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == "commerce.payment.failed")
                )
            ).scalar_one()
        assert failed == 1, "a second event id must not re-emit for an already-failed charge"


class TestTenantIsolation:
    async def test_a_webhook_cannot_reconcile_another_tenants_charge(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """A callback that names organization B cannot touch organization A's charge. Bound to B,
        A's charge is invisible under RLS, so nothing reconciles and no event is emitted — even
        though the signature is valid."""
        charge = await _seed_charge(client, org_id, key="isolate")
        body = razorpay_webhook_body(
            event="payment.failed",
            provider_payment_id=charge["provider_payment_id"],
            organization_id=org_b,  # a different tenant than the charge belongs to
            charge_id=uuid.UUID(charge["id"]),
        )
        response = await client.post(WEBHOOK, **_post(provider, body))
        # Recorded under B's inbox, but it reconciles nothing.
        assert response.status_code == 200 and response.json()["reconciled"] is False

        async with session.begin():
            stored = await session.get(Charge, uuid.UUID(charge["id"]))
            events = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == "commerce.payment.failed")
                )
            ).scalar_one()
        assert stored is not None and stored.state == "authorized", (
            "A's charge was reconciled by B's webhook"
        )
        assert events == 0
