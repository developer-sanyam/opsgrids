"""The Razorpay adapter — real mapping, real signature, real state; the wire is the only stub.

These are pure adapter tests: no database, no network, no keys. They prove the seam ADR-0004 §6
describes is real everywhere it matters and stubbed in exactly one place.
"""

from __future__ import annotations

import json
import uuid

from app.config import settings
from app.providers.base import PaymentProvider
from app.providers.razorpay import RazorpayProvider, build_provider
from tests.helpers import razorpay_webhook_body


def _provider() -> RazorpayProvider:
    return RazorpayProvider(key_id=None, key_secret=None, webhook_secret="whsec-test")


class TestNoKeysNoNetwork:
    def test_the_adapter_needs_no_keys(self) -> None:
        """The development posture: Razorpay keys are absent, and the whole path still works
        because the outbound HTTP is stubbed. This is the assertion the phase turns on."""
        cfg = settings()
        assert cfg.razorpay_key_id is None
        assert cfg.razorpay_key_secret is None

    def test_charge_and_mandate_run_with_no_keys_and_no_socket(self) -> None:
        """A charge and a mandate both complete with `key_id`/`key_secret` None — no network is
        opened, no key is read. If either were required, this would raise instead."""
        provider = _provider()
        charge = provider.charge(
            idempotency_key="k1",
            amount_minor=1000,
            currency="USD",
            mandate_id=None,
            notes={"organization_id": "o", "charge_id": "c"},
        )
        mandate = provider.create_mandate(
            idempotency_key="m1", max_amount_minor=50000, currency="USD", notes={}
        )
        assert charge.provider_payment_id.startswith("pay_")
        assert mandate.provider_mandate_id.startswith("mandate_")

    def test_the_adapter_satisfies_the_protocol(self) -> None:
        """Structural: the concrete adapter *is* a `PaymentProvider`, so the service layer can
        hold the Protocol and never the concrete class."""
        assert isinstance(_provider(), PaymentProvider)
        assert isinstance(
            build_provider(key_id=None, key_secret=None, webhook_secret="x"), PaymentProvider
        )


class TestResponseMapping:
    def test_a_charge_maps_to_authorized_not_captured(self) -> None:
        """Razorpay authorizes synchronously and captures asynchronously by webhook, so a freshly
        created charge is `authorized` — capture is a later, separate fact."""
        result = _provider().charge(
            idempotency_key="k",
            amount_minor=2500,
            currency="INR",
            mandate_id=None,
            notes={},
        )
        assert result.state == "authorized"
        assert result.amount_minor == 2500
        assert result.currency == "INR"
        assert result.provider == "razorpay"

    def test_a_new_mandate_is_not_yet_activated(self) -> None:
        """A multi-day eMandate exists before it can be debited — `activated_at` is None at
        creation. This is the Razorpay constraint the seam is designed around (ADR-0004 §6)."""
        mandate = _provider().create_mandate(
            idempotency_key="m", max_amount_minor=100000, currency="INR", notes={}
        )
        assert mandate.state == "created"
        assert mandate.activated_at is None
        assert mandate.max_amount_minor == 100000


class TestIdempotentIds:
    def test_the_same_key_yields_the_same_provider_id(self) -> None:
        """Deterministic ids are the observable property of an idempotent create: the same
        platform key must map to the same provider payment id, so a retry is a no-op."""
        p = _provider()
        first = p.charge(
            idempotency_key="same", amount_minor=1, currency="USD", mandate_id=None, notes={}
        )
        second = p.charge(
            idempotency_key="same", amount_minor=1, currency="USD", mandate_id=None, notes={}
        )
        assert first.provider_payment_id == second.provider_payment_id

    def test_different_keys_yield_different_ids(self) -> None:
        p = _provider()
        a = p.charge(idempotency_key="a", amount_minor=1, currency="USD", mandate_id=None, notes={})
        b = p.charge(idempotency_key="b", amount_minor=1, currency="USD", mandate_id=None, notes={})
        assert a.provider_payment_id != b.provider_payment_id


class TestSignature:
    def test_a_correct_signature_verifies(self) -> None:
        p = _provider()
        body = b'{"event":"payment.captured"}'
        assert p.verify_webhook(signature=p.sign(body), body=body) is True

    def test_a_tampered_body_fails(self) -> None:
        """The signature is over exactly these bytes — change one and it no longer matches."""
        p = _provider()
        body = b'{"event":"payment.captured"}'
        signature = p.sign(body)
        assert p.verify_webhook(signature=signature, body=body + b" ") is False

    def test_a_wrong_secret_fails(self) -> None:
        signed_by_other = RazorpayProvider(
            key_id=None, key_secret=None, webhook_secret="a-different-secret"
        )
        body = b'{"event":"payment.failed"}'
        assert _provider().verify_webhook(signature=signed_by_other.sign(body), body=body) is False

    def test_a_missing_signature_fails(self) -> None:
        assert _provider().verify_webhook(signature=None, body=b"{}") is False


class TestWebhookParsing:
    def test_notes_carry_the_tenant_and_charge(self) -> None:
        """The tenant a callback names comes from the metadata payment stamped and Razorpay
        echoed — read here, trusted only because the whole body verified first."""
        org = uuid.uuid4()
        charge = uuid.uuid4()
        sub = uuid.uuid4()
        body = razorpay_webhook_body(
            event="payment.failed",
            provider_payment_id="pay_x",
            organization_id=org,
            charge_id=charge,
            subscription_id=sub,
            product_key="hrms",
            error_description="insufficient funds",
        )
        event = _provider().parse_webhook(body=body)
        assert event.outcome_state == "failed"
        assert event.organization_id == str(org)
        assert event.charge_id == str(charge)
        assert event.provider_payment_id == "pay_x"
        assert event.failure_reason == "insufficient funds"

    def test_captured_maps_to_the_captured_outcome(self) -> None:
        body = json.dumps(
            {"id": "evt_1", "event": "payment.captured", "payload": {"payment": {"entity": {}}}}
        ).encode()
        assert _provider().parse_webhook(body=body).outcome_state == "captured"
