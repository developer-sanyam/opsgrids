"""The pure half of tenant collections: the collection state machine (`decide`), credential
shape, webhook redaction and the reconciliation comparison. No database."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.collections import domain
from app.domain import WebhookNotApplied, derive_event_id
from app.providers.base import PaymentSnapshot
from app.providers.razorpay import RazorpayProvider, best_attempt
from tests.collections_support import payment_webhook


def _obs(outcome: str, payment_id: str | None = "pay_1", **kw: object) -> domain.Observation:
    return domain.Observation(
        outcome=outcome,
        provider_payment_id=payment_id,
        provider_order_id="order_1",
        amount_minor=500,
        currency="INR",
        **kw,  # type: ignore[arg-type]
    )


class TestDecide:
    @pytest.mark.parametrize("state", ["created", "authorized", "failed"])
    def test_a_capture_from_any_open_state_captures_and_emits(self, state: str) -> None:
        decision = domain.decide(state, "pay_0" if state == "failed" else None, _obs("captured"))
        assert decision.to_state == "captured"
        assert decision.emit == domain.EVT_COLLECTION_CAPTURED
        assert decision.bind_payment and decision.clear_failure and decision.note is None

    @pytest.mark.parametrize("state", ["cancelled", "expired"])
    def test_a_capture_after_this_platform_stopped_waiting_is_still_recorded(
        self, state: str
    ) -> None:
        decision = domain.decide(state, None, _obs("captured"))
        assert decision.to_state == "captured"
        assert decision.emit == domain.EVT_COLLECTION_CAPTURED
        assert decision.note == f"captured_after_{state}"

    def test_a_redelivered_capture_is_a_no_op(self) -> None:
        assert domain.decide("captured", "pay_1", _obs("captured")) is domain.NOOP

    def test_a_second_different_capture_on_a_paid_order_is_a_conflict(self) -> None:
        with pytest.raises(WebhookNotApplied) as raised:
            domain.decide("captured", "pay_1", _obs("captured", "pay_2"))
        assert raised.value.reason == "provider_id_conflict"

    def test_the_captured_payment_failing_afterwards_is_a_conflict(self) -> None:
        with pytest.raises(WebhookNotApplied) as raised:
            domain.decide("captured", "pay_1", _obs("failed", "pay_1"))
        assert raised.value.reason == "illegal_transition"

    def test_an_older_attempts_late_failure_never_undoes_a_capture(self) -> None:
        assert domain.decide("captured", "pay_2", _obs("failed", "pay_1")) is domain.NOOP

    def test_another_attempt_failing_while_one_is_authorized_changes_nothing(self) -> None:
        assert domain.decide("authorized", "pay_1", _obs("failed", "pay_2")) is domain.NOOP

    def test_a_failure_then_a_retry_on_the_same_order_captures(self) -> None:
        failed = domain.decide("created", None, _obs("failed", "pay_1", failure_reason="declined"))
        assert failed.to_state == "failed" and failed.emit == domain.EVT_COLLECTION_FAILED
        again = domain.decide("failed", "pay_1", _obs("failed", "pay_2", failure_reason="otp"))
        assert again.to_state is None and again.bind_payment and again.failure_reason == "otp"
        assert domain.decide("failed", "pay_2", _obs("captured", "pay_3")).to_state == "captured"

    def test_authorization_after_cancel_is_only_noted(self) -> None:
        decision = domain.decide("cancelled", None, _obs("authorized"))
        assert decision.to_state is None and decision.note == "late_authorization"

    def test_out_of_order_authorized_after_captured_is_a_no_op(self) -> None:
        assert domain.decide("captured", "pay_1", _obs("authorized", "pay_1")) is domain.NOOP


class TestCredentials:
    @pytest.mark.parametrize(
        ("key_id", "mode"),
        [
            ("rzp_test_1DP5mmOlF5G5ag", "test"),
            ("rzp_live_1DP5mmOlF5G5ag", "live"),
            ("rzp_test_", None),
            ("pk_test_123456789", None),
            ("rzp_test_abc def12", None),
        ],
    )
    def test_the_mode_comes_from_the_key_id(self, key_id: str, mode: str | None) -> None:
        assert domain.mode_for_key_id(key_id) == mode

    def test_a_mask_never_reveals_the_length(self) -> None:
        assert domain.mask_secret("a" * 12 + "wxyz") == domain.mask_secret("b" * 90 + "wxyz")

    def test_the_refund_idempotency_key_is_razorpay_shaped_and_charge_scoped(self) -> None:
        a = domain.refund_idempotency_key("org", "charge-1", "key-1")
        assert a == domain.refund_idempotency_key("org", "charge-1", "key-1")
        assert a != domain.refund_idempotency_key("org", "charge-2", "key-1")
        assert len(a) >= 10 and all(c.isalnum() or c in "-_" for c in a)


class TestRedaction:
    def test_the_customer_is_dropped_and_the_identity_is_kept(self) -> None:
        body = json.loads(
            payment_webhook(event="payment.captured", payment_id="pay_1", order_id="order_1")
        )
        stored = domain.redact_webhook_body(body)
        text = json.dumps(stored)
        for leaked in ("customer@example.com", "+919876543210", "okbank", "1111", "A Customer"):
            assert leaked not in text
        assert "notes" not in text
        entity = stored["payload"]["payment"]["entity"]
        assert entity["id"] == "pay_1" and entity["order_id"] == "order_1"
        assert entity["amount"] == 50_000 and entity["currency"] == "INR"
        # The stored body still identifies the same event, so a redelivery is still recognised.
        adapter = RazorpayProvider(key_id=None, key_secret=None, webhook_secret="x" * 16)
        original = adapter.parse_webhook(body=json.dumps(body).encode())
        reduced = adapter.parse_webhook(body=json.dumps(stored).encode())
        assert derive_event_id(original) == derive_event_id(reduced) is not None


def _snap(pid: str, status: str, **kw: object) -> PaymentSnapshot:
    return PaymentSnapshot(
        provider_payment_id=pid,
        status=status,
        amount_minor=int(kw.pop("amount", 500)),  # type: ignore[call-overload]
        currency=str(kw.pop("currency", "INR")),
        **kw,  # type: ignore[arg-type]
    )


class TestReconcileOne:
    def test_agreement_is_no_mismatch(self) -> None:
        assert (
            domain.reconcile_one(
                state="captured",
                amount_minor=500,
                currency="INR",
                provider_payment_id="pay_1",
                refunded_minor=0,
                provider_payments=[_snap("pay_1", "captured")],
            )
            == []
        )

    def test_money_the_provider_took_that_this_platform_missed(self) -> None:
        assert domain.reconcile_one(
            state="expired",
            amount_minor=500,
            currency="INR",
            provider_payment_id=None,
            refunded_minor=0,
            provider_payments=[_snap("pay_1", "captured")],
        ) == [domain.MISMATCH_CAPTURED_AT_PROVIDER_ONLY]

    def test_a_capture_the_provider_does_not_have(self) -> None:
        assert domain.reconcile_one(
            state="captured",
            amount_minor=500,
            currency="INR",
            provider_payment_id="pay_1",
            refunded_minor=0,
            provider_payments=[_snap("pay_1", "failed")],
        ) == [domain.MISMATCH_NOT_CAPTURED_AT_PROVIDER]

    def test_refund_and_amount_disagreements(self) -> None:
        assert domain.reconcile_one(
            state="captured",
            amount_minor=500,
            currency="INR",
            provider_payment_id="pay_1",
            refunded_minor=100,
            provider_payments=[_snap("pay_1", "refunded", amount=400, amount_refunded_minor=0)],
        ) == [domain.MISMATCH_AMOUNT, domain.MISMATCH_REFUNDED]


def test_the_deciding_attempt_is_money_first_then_most_recent() -> None:
    attempts = [
        _snap("pay_1", "failed", created_at=1),
        _snap("pay_2", "captured", created_at=2),
        _snap("pay_3", "failed", created_at=3),
    ]
    best = best_attempt(attempts)
    assert best is not None and best.provider_payment_id == "pay_2"
    only_failures = best_attempt(
        [_snap("a", "failed", created_at=1), _snap("b", "failed", created_at=5)]
    )
    assert only_failures is not None and only_failures.provider_payment_id == "b"
    assert best_attempt([]) is None


class TestPayableUntil:
    """How long a closed collection's provider Order may still take a payment — what holds up a
    disconnect (ADR-0030 §8). Known answers, written down first."""

    CREATED = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
    TTL = timedelta(hours=24)

    def test_a_cashfree_order_takes_payment_until_its_own_expiry(self) -> None:
        closed = datetime(2026, 10, 6, 10, 5, tzinfo=UTC)
        until = domain.payable_until(
            domain.CASHFREE, created_at=self.CREATED, closed_at=closed, checkout_ttl=self.TTL
        )
        assert until == datetime(2026, 10, 7, 10, 0, tzinfo=UTC)

    def test_closed_near_the_end_the_late_payment_grace_still_runs(self) -> None:
        closed = datetime(2026, 10, 7, 9, 55, tzinfo=UTC)
        until = domain.payable_until(
            domain.CASHFREE, created_at=self.CREATED, closed_at=closed, checkout_ttl=self.TTL
        )
        assert until == datetime(2026, 10, 7, 10, 15, tzinfo=UTC)

    def test_a_razorpay_order_only_finishes_an_attempt_already_started(self) -> None:
        closed = datetime(2026, 10, 6, 10, 5, tzinfo=UTC)
        until = domain.payable_until(
            domain.RAZORPAY, created_at=self.CREATED, closed_at=closed, checkout_ttl=self.TTL
        )
        assert until == datetime(2026, 10, 6, 10, 25, tzinfo=UTC)
