"""One invoice, two captures — always detected, alerted and audited (N-1).

Applying `failed -> captured` for a late same-payment capture (ADR-0004, PAY-A round 2) makes a
stale failed charge come back to life on its own — and that opens a way for one invoice to be
collected twice that the dunning ladder's own key discipline never anticipated: a retry for the
SAME invoice can capture under its own charge while the first charge's late capture also lands, in
either order. Both charges are genuinely captured at the provider; nothing about either capture,
taken on its own, is wrong.

`services._detect_double_collection` runs on every transition to `captured` and compares against
every other captured charge of the same `(organization_id, invoice_id)`. It never refunds anything:
an operator decides which of the two charges to give back, by hand, against the evidence this
leaves (`payment.invoice.collected_twice`, logged at error and audited). The other half of N-1 —
preventing the debit before it happens, where prevention is still possible — is
`consumers.charge_on_dunning_retry`; see `test_dunning_retry.py`.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.providers.razorpay import RazorpayProvider
from tests.helpers import audit_rows, charge_row, emitted, post_webhook, seed_charge, webhook_body

pytestmark = pytest.mark.integration


class TestBothOrderingsAreCaught:
    @pytest.mark.parametrize(
        "late_first", [False, True], ids=["retry-captures-first", "late-capture-first"]
    )
    async def test_two_captures_of_one_invoice_alert_once_naming_each_other(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        late_first: bool,
    ) -> None:
        invoice_id = uuid.uuid4()
        x = await seed_charge(client, org_id, key="dc-x", invoice_id=invoice_id)
        await post_webhook(
            client,
            provider,
            webhook_body(x, org_id, event="payment.failed", error_description="bank_timeout"),
            "evt_dc_xf",
        )
        # The dunning retry raises its own charge for the same invoice, under its own key —
        # exactly what `charge_on_dunning_retry` does in production.
        y = await seed_charge(client, org_id, key="dc-y-retry", invoice_id=invoice_id)

        late = webhook_body(x, org_id, event="payment.captured", created_at=1_700_000_900)
        retry_capture = webhook_body(y, org_id, event="payment.captured")
        order = (
            [(late, "evt_dc_xc"), (retry_capture, "evt_dc_yc")]
            if late_first
            else [(retry_capture, "evt_dc_yc"), (late, "evt_dc_xc")]
        )

        with capture_logs() as logs:
            for body, event_id in order:
                response = await post_webhook(client, provider, body, event_id)
                assert response.json()["reconciled"] is True, response.text

        assert (await charge_row(admin_session, x["id"]))["state"] == "captured"
        assert (await charge_row(admin_session, y["id"]))["state"] == "captured"
        assert (await emitted(admin_session, org_id)).count("commerce.payment.captured") == 2

        pages = [e for e in logs if e["event"] == "payment.invoice.collected_twice"]
        assert len(pages) == 1, (
            "the alert must fire exactly once, from whichever capture landed second"
        )

        # Whichever charge captured second is the one that ran the check and found the other
        # already standing; the alert names the *other* one as the pre-existing capture.
        # `late_first` orders [late(X), retry(Y)]: X captures first, so Y is second — and the
        # reverse when it is False.
        second_charge, first_charge = (y, x) if late_first else (x, y)
        assert pages[0]["log_level"] == "error"
        assert pages[0]["invoice_id"] == str(invoice_id)
        assert pages[0]["charge_id"] == second_charge["id"]
        assert pages[0]["other_captured_charge_ids"] == [first_charge["id"]]
        assert pages[0]["amount_minor"] == 1000
        assert pages[0]["currency"] == "USD"

        (audit,) = [
            a
            for a in await audit_rows(admin_session, org_id)
            if a["action"] == "payment.invoice.collected_twice"
        ]
        assert audit["entity"] == "invoice" and str(audit["entity_id"]) == str(invoice_id)
        assert audit["after"]["invoice_id"] == str(invoice_id)
        assert audit["after"]["other_captured_charge_ids"] == [first_charge["id"]]
        assert audit["after"]["amount_minor"] == 1000
        assert audit["after"]["currency"] == "USD"

    async def test_a_third_charge_capturing_the_same_invoice_names_both_others(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        invoice_id = uuid.uuid4()
        a = await seed_charge(client, org_id, key="dc-triple-a", invoice_id=invoice_id)
        b = await seed_charge(client, org_id, key="dc-triple-b", invoice_id=invoice_id)
        c = await seed_charge(client, org_id, key="dc-triple-c", invoice_id=invoice_id)
        await post_webhook(
            client, provider, webhook_body(a, org_id, event="payment.captured"), "evt_dc3_a"
        )
        await post_webhook(
            client, provider, webhook_body(b, org_id, event="payment.captured"), "evt_dc3_b"
        )

        with capture_logs() as logs:
            await post_webhook(
                client, provider, webhook_body(c, org_id, event="payment.captured"), "evt_dc3_c"
            )

        (page,) = [e for e in logs if e["event"] == "payment.invoice.collected_twice"]
        assert page["charge_id"] == c["id"]
        assert sorted(page["other_captured_charge_ids"]) == sorted([a["id"], b["id"]])


class TestASingleCaptureIsNotAlerted:
    async def test_a_charge_with_no_invoice_is_never_checked(
        self, client: AsyncClient, provider: RazorpayProvider, org_id: uuid.UUID
    ) -> None:
        x = await seed_charge(client, org_id, key="dc-no-invoice")
        with capture_logs() as logs:
            response = await post_webhook(
                client, provider, webhook_body(x, org_id, event="payment.captured"), "evt_dc_none"
            )
        assert response.json()["reconciled"] is True
        assert not [e for e in logs if e["event"] == "payment.invoice.collected_twice"]

    async def test_one_charge_on_its_own_invoice_is_not_alerted(
        self, client: AsyncClient, provider: RazorpayProvider, org_id: uuid.UUID
    ) -> None:
        x = await seed_charge(client, org_id, key="dc-single-invoice", invoice_id=uuid.uuid4())
        with capture_logs() as logs:
            response = await post_webhook(
                client, provider, webhook_body(x, org_id, event="payment.captured"), "evt_dc_single"
            )
        assert response.json()["reconciled"] is True
        assert not [e for e in logs if e["event"] == "payment.invoice.collected_twice"]

    async def test_a_second_charge_for_the_invoice_that_never_captures_is_not_alerted(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """Two charges share an invoice, but only one ever captures — a retry raised and left
        `authorized` must not itself look like a double collection."""
        invoice_id = uuid.uuid4()
        x = await seed_charge(client, org_id, key="dc-only-one-captures", invoice_id=invoice_id)
        await seed_charge(client, org_id, key="dc-never-captures", invoice_id=invoice_id)

        with capture_logs() as logs:
            response = await post_webhook(
                client, provider, webhook_body(x, org_id, event="payment.captured"), "evt_dc_lone"
            )

        assert response.json()["reconciled"] is True
        assert not [e for e in logs if e["event"] == "payment.invoice.collected_twice"]

    async def test_a_redelivered_capture_does_not_re_alert(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """The detection runs on a *transition* to captured, not on every webhook that mentions a
        captured charge — a redelivery of the second capture must not page a second time."""
        invoice_id = uuid.uuid4()
        x = await seed_charge(client, org_id, key="dc-redelivery-x", invoice_id=invoice_id)
        y = await seed_charge(client, org_id, key="dc-redelivery-y", invoice_id=invoice_id)
        await post_webhook(
            client, provider, webhook_body(x, org_id, event="payment.captured"), "evt_dc_rx"
        )
        second = webhook_body(y, org_id, event="payment.captured")
        first_delivery = await post_webhook(client, provider, second, "evt_dc_ry")
        assert first_delivery.json()["reconciled"] is True

        with capture_logs() as logs:
            redelivery = await post_webhook(client, provider, second, "evt_dc_ry")

        assert redelivery.json()["duplicate"] is True
        assert not [e for e in logs if e["event"] == "payment.invoice.collected_twice"]
