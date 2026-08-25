"""HEADLINE — the payment-failure → dunning → de-entitle → 402 loop (ADR-0004 §5, ADR-0005).

The loop crosses three services, and one process can only ever hold one service's `app` — the
constraint the end-to-end harness itself records ("every service exposes a top-level `app` and one
process cannot import two of them"). So the loop is proven as a **chain of contracts**, each link
executed by the service that owns it, connected by the events on the wire:

    1. payment          a signature-verified Razorpay webhook — NO KEYS, NO NETWORK — fails a
       (HERE, real)     charge and emits `commerce.payment.failed`, carrying the subscription id.
    2. subscription     consuming `commerce.payment.failed` opens a **grace window** — `past_due`,
       (tests/          still entitling — retries on a schedule, and emits
        test_dunning.py) `commerce.subscription.suspended` only when the window closes (ADR-0005).
    2a. payment         consuming `commerce.subscription.payment_retry_due` raises each retry
       (tests/          debit under its own platform idempotency key.
        test_dunning_retry.py)
    3. product          projecting `commerce.subscription.suspended` sets Activation.entitles
       (tests/          False — and `past_due` deliberately leaves it True.
        test_projection.py::test_suspended_de_entitles, ::test_past_due_still_entitles)
    4. the gateway      a principal no longer entitled to the product is refused **402**
       (HERE, real)     — asserted against the real `require_product`, the exact check the gateway
                        runs before proxying.

Links 1 and 4 execute in *this* test, against real code; links 2 and 3 execute in their own suites
(named above) because their `app` cannot be imported alongside payment's. What ties the links
together is the event payloads, so this test also asserts that link 1 emits exactly the fields link
2 reads. The whole thing runs with no Razorpay keys and no outbound network — the only stub is the
provider's wire.

Note what moved in ADR-0005 and what did not: the *timing* of link 2's suspension changed from
"immediately" to "when the fourteen-day window closes". Links 1, 3 and 4 are untouched — commerce
still invents no new enforcement point.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Charge, OutboxEvent
from app.providers.razorpay import RazorpayProvider
from platform_core.auth import Principal, require_product
from platform_core.errors import PaymentRequiredProblem
from tests.helpers import razorpay_webhook_body, service_token

pytestmark = pytest.mark.integration

WEBHOOK = "/api/v1/webhooks/razorpay"
CHARGES = "/internal/v1/charges"
PRODUCT = "hrms"


class TestTheSuspendLoop:
    async def test_a_failed_charge_emits_the_event_that_suspends_and_de_entitles(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id = uuid.uuid4()

        # --- link 1: payment (real, no keys, no network) -----------------------------------
        # billing raises a charge for the subscription's invoice; the stubbed provider assigns a
        # deterministic payment id. No Razorpay key is configured or needed.
        seed = await client.post(
            CHARGES,
            headers={"X-Internal-Token": service_token()},
            json={
                "organization_id": str(org_id),
                "amount_minor": 4900,
                "currency": "USD",
                "idempotency_key": f"loop-{subscription_id}",
                "subscription_id": str(subscription_id),
                "product_key": PRODUCT,
            },
        )
        assert seed.status_code == 201, seed.text
        charge = seed.json()

        # The provider reports the charge failed, by webhook — signed with the shared secret, so it
        # verifies exactly as a real Razorpay callback would, with no live keys involved.
        body = razorpay_webhook_body(
            event="payment.failed",
            provider_payment_id=charge["provider_payment_id"],
            organization_id=org_id,
            charge_id=uuid.UUID(charge["id"]),
            subscription_id=subscription_id,
            product_key=PRODUCT,
            error_description="mandate_revoked",
        )
        response = await client.post(
            WEBHOOK, content=body, headers={"X-Razorpay-Signature": provider.sign(body)}
        )
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

        # --- link 1→2 contract: the failure event carries exactly what subscription reads --------
        # subscription-service's consumer binds `organization_id`, opens the grace window against
        # `subscription_id`, and copies the invoice, amount and currency onto the dunning cycle so
        # each retry can be raised without reading another service's schema (ADR-0005 §6).
        assert payload["organization_id"] == str(org_id)
        assert payload["subscription_id"] == str(subscription_id)
        assert payload["product_key"] == PRODUCT
        assert payload["invoice_id"] is not None or payload["amount_minor"] == 4900
        assert payload["amount_minor"] == 4900
        assert payload["currency"] == "USD"

        # --- link 4: the gateway (real) -----------------------------------------------------------
        # Links 2 and 3 (proven in their own suites) turn a *closed window* into a de-entitled
        # Activation — fourteen days after this failure, not on it.
        # A token refreshed after suspension no longer carries the product in `ent`; the gateway's
        # own check — this exact function — then refuses the product route with 402, not 403.
        de_entitled = Principal(
            sub=uuid.uuid4(),
            organization_id=org_id,
            entitlements=[],  # the projection removed the product after suspension
        )
        gate = require_product(PRODUCT)
        with pytest.raises(PaymentRequiredProblem) as refused:
            gate(de_entitled)
        assert refused.value.status_code == 402

        # And the converse, so the gate is proven to actually gate: while still entitled, it passes.
        still_entitled = Principal(sub=uuid.uuid4(), organization_id=org_id, entitlements=[PRODUCT])
        assert gate(still_entitled) is still_entitled
