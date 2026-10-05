"""Runs `CheckoutContract` against every checkout adapter the platform has — the live Razorpay
adapter over a stateful fake Razorpay, the Razorpay stub, and the Cashfree adapter over its stateful
fake — and against eight broken ones.

The second half is why the contract can be trusted: each check is run against an adapter broken in
exactly the way it exists to catch, and must fail. A contract that passes everything protects
nothing (the same discipline `test_provider_contract.py` holds `ProviderContract` to).
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from app.providers.base import (
    CheckoutRequest,
    CheckoutSession,
    PaymentProvider,
    PaymentSnapshot,
    WebhookEvent,
)
from app.providers.cashfree import CashfreeProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from app.providers.razorpay import RazorpayProvider
from app.providers.razorpay_wire import RazorpayHttpWire
from tests.checkout_contract import CheckoutContract
from tests.helpers import razorpay_webhook_body
from tests.razorpay_fake import KEY_ID, KEY_SECRET, FakeRazorpay

WEBHOOK_SECRET = "dev-only-contract-webhook-secret"  # pragma: allowlist secret
CASHFREE_SECRET = "dev-only-cashfree-contract-secret"  # pragma: allowlist secret


# --------------------------------------------------------------------------- the adapters


def _razorpay_live(fake: FakeRazorpay | None = None) -> RazorpayProvider:
    fake = fake or FakeRazorpay()
    provider = RazorpayProvider(
        key_id=KEY_ID,
        key_secret=KEY_SECRET,
        webhook_secret=WEBHOOK_SECRET,
        wire=RazorpayHttpWire(key_id=KEY_ID, key_secret=KEY_SECRET, transport=fake.transport()),
        platform_billing=True,
    )
    provider.fake = fake  # type: ignore[attr-defined]
    return provider


def _razorpay_stub() -> RazorpayProvider:
    return RazorpayProvider(
        key_id=None, key_secret=None, webhook_secret=WEBHOOK_SECRET, platform_billing=True
    )


def _cashfree(fake: CashfreeFakeWire | None = None) -> CashfreeProvider:
    fake = fake or CashfreeFakeWire()
    provider = CashfreeProvider(wire=fake, secret_key=CASHFREE_SECRET, mode="stub")
    provider.fake = fake  # type: ignore[attr-defined]
    return provider


def _razorpay_bodies() -> tuple[bytes, bytes]:
    org, charge = uuid.uuid4(), uuid.uuid4()
    common = {"organization_id": org, "charge_id": charge, "body_id": False}
    return (
        razorpay_webhook_body(event="payment.authorized", provider_payment_id="pay_1", **common),
        razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id="pay_2",
            created_at=1_700_000_500,
            **common,
        ),
    )


def _cashfree_bodies() -> tuple[bytes, bytes]:
    def body(event: str, payment_id: int, status: str, at: str) -> bytes:
        return (
            b'{"type":"%s","event_time":"%s","data":{"order":{"order_id":"o-1","order_amount":499.00,'
            b'"order_currency":"INR","order_tags":{}},"payment":{"cf_payment_id":%d,'
            b'"payment_status":"%s","payment_amount":499.00,"payment_currency":"INR"}}}'
            % (event.encode(), at.encode(), payment_id, status.encode())
        )

    return (
        body("PAYMENT_SUCCESS_WEBHOOK", 11, "SUCCESS", "2026-10-06T10:00:00+05:30"),
        body("PAYMENT_FAILED_WEBHOOK", 12, "FAILED", "2026-10-06T10:05:00+05:30"),
    )


# --------------------------------------------------------------------------- the real adapters


class TestTheLiveRazorpayAdapter(CheckoutContract):
    """The platform's live Razorpay adapter, over a stateful fake Razorpay."""

    @pytest.fixture
    def adapter(self) -> PaymentProvider:
        return _razorpay_live()

    def fresh(self, adapter: Any) -> PaymentProvider:
        return _razorpay_live(adapter.fake)

    def create_count(self, adapter: Any) -> int:
        return len(adapter.fake.orders)

    def request_count(self, adapter: Any) -> int:
        return len(adapter.fake.requests)

    def pay(self, adapter: Any, order_id: str) -> None:
        adapter.fake.pay(order_id, status="captured")

    def webhook_bodies(self, adapter: PaymentProvider) -> tuple[bytes, bytes]:
        return _razorpay_bodies()


class TestTheRazorpayStub(CheckoutContract):
    """The legacy stub: tolerant of any money, so the refusal checks do not apply to it."""

    enforces_money = False

    @pytest.fixture
    def adapter(self) -> PaymentProvider:
        return _razorpay_stub()

    def fresh(self, adapter: Any) -> PaymentProvider:
        other = _razorpay_stub()
        other._orders_by_receipt = adapter._orders_by_receipt
        other._payments_by_order = adapter._payments_by_order
        other._stub_order_payments = adapter._stub_order_payments
        return other

    def create_count(self, adapter: Any) -> int:
        return len(adapter._orders_by_receipt)

    def request_count(self, adapter: Any) -> int:
        return 0

    def pay(self, adapter: Any, order_id: str) -> None:
        adapter.stub_record_payment(
            order_id,
            PaymentSnapshot(
                provider_payment_id=f"pay_{uuid.uuid4().hex[:12]}",
                status="captured",
                amount_minor=49_900,
                currency="INR",
            ),
        )

    def webhook_bodies(self, adapter: PaymentProvider) -> tuple[bytes, bytes]:
        return _razorpay_bodies()


class TestTheCashfreeAdapter(CheckoutContract):
    """The Cashfree adapter, over its stateful fake (the wire `CASHFREE_ENV=stub` runs on)."""

    @pytest.fixture
    def adapter(self) -> PaymentProvider:
        return _cashfree()

    def fresh(self, adapter: Any) -> PaymentProvider:
        return _cashfree(adapter.fake)

    def create_count(self, adapter: Any) -> int:
        return len(adapter.fake.orders)

    def request_count(self, adapter: Any) -> int:
        return len(adapter.fake.requests)

    def pay(self, adapter: Any, order_id: str) -> None:
        adapter.fake.pay(order_id, status="SUCCESS")

    def webhook_bodies(self, adapter: PaymentProvider) -> tuple[bytes, bytes]:
        return _cashfree_bodies()


# --------------------------------------------------------------------------- broken adapters


class _BrokenRazorpay(RazorpayProvider):
    """The live Razorpay adapter with one behaviour spoiled."""

    def __init__(self) -> None:
        fake = FakeRazorpay()
        super().__init__(
            key_id=KEY_ID,
            key_secret=KEY_SECRET,
            webhook_secret=WEBHOOK_SECRET,
            wire=RazorpayHttpWire(key_id=KEY_ID, key_secret=KEY_SECRET, transport=fake.transport()),
            platform_billing=True,
        )
        self.fake = fake


class _CreatesPerCall(_BrokenRazorpay):
    """No lookup before create: a resumed checkout makes a second Order the customer could pay."""

    def ensure_order(
        self, *, receipt: str, amount_minor: int, currency: str, notes: dict[str, str]
    ) -> Any:
        body = {
            "amount": amount_minor,
            "currency": currency,
            "receipt": f"{receipt}-{uuid.uuid4()}",
        }
        created = self._wire.request("POST", "/orders", json=body)  # type: ignore[union-attr]
        from app.providers.base import OrderResult

        return OrderResult(provider="razorpay", provider_order_id=created["id"], receipt=receipt)


class _RemintsARecordedOrder(_BrokenRazorpay):
    """Ignores the Order the charge already records: after a rollback the stub would mint a
    stand-in under it and become the authority on a payment it cannot see."""

    def ensure_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        return super().ensure_checkout(replace(request, recorded_order_id=None))


class _FindThatCreates(_BrokenRazorpay):
    """`find_order` makes the Order on a miss: every look leaves a phantom Order behind."""

    def find_order(self, *, receipt: str) -> Any:
        found = super().find_order(receipt=receipt)
        if found is not None:
            return found
        return self.ensure_order(receipt=receipt, amount_minor=49_900, currency="INR", notes={})


class _ReusesAnyOrder(_BrokenRazorpay):
    """Reuses a found Order whatever its amount: the customer would pay the wrong sum."""

    def _require_same_order(self, entity: dict[str, Any], amount_minor: int, currency: str) -> None:
        return None


class _LeaksTheCustomer(_BrokenRazorpay):
    """Puts the paying customer's phone in what the browser is handed."""

    def ensure_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        session = super().ensure_checkout(request)
        assert request.customer is not None
        session.client_action["contact"] = request.customer.phone
        return replace(
            session, client_action={**session.client_action, "contact": request.customer.phone}
        )


class _HandsOutTheSecret(_BrokenRazorpay):
    def ensure_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        session = super().ensure_checkout(request)
        return replace(session, client_action={**session.client_action, "key_secret": KEY_SECRET})


class _ValidatesNothing(_BrokenRazorpay):
    """Sends an unpayable order to the provider and waits for it to be refused."""

    def _require_platform_money(self, amount_minor: int, currency: str) -> None:
        return None


class _SlowWire(_BrokenRazorpay):
    """Per-request timeouts that cannot fit the call budget: 5 x 20s against 45s."""

    request_timeouts_seconds = (20.0, 20.0, 20.0, 20.0, 20.0)


class _AcceptsAnySignature(_BrokenRazorpay):
    def verify_webhook(self, *, signature: str | None, body: bytes) -> bool:
        return bool(signature)


class _CrashesOnNonAsciiSignature(_BrokenRazorpay):
    def verify_webhook(self, *, signature: str | None, body: bytes) -> bool:
        if not signature:
            return False
        return hmac.compare_digest(self.sign(body), signature)


class _ConstantEventId(_BrokenRazorpay):
    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        return replace(super().parse_webhook(body=body), provider_event_id="evt_constant")


class _BlindOrderPayments(_BrokenRazorpay):
    """Reports every payment on the account against every Order."""

    def order_payments(self, *, order_id: str) -> list[PaymentSnapshot]:
        everything = [p for payments in self.fake.payments.values() for p in payments]
        from app.providers.razorpay import _snapshot

        return [s for s in (_snapshot(p) for p in everything) if s is not None]


class _BrokenContract(TestTheLiveRazorpayAdapter):
    @pytest.fixture
    def adapter(self) -> PaymentProvider:
        return _BrokenRazorpay()

    def fresh(self, adapter: Any) -> PaymentProvider:
        broken = type(adapter)()
        broken.fake = adapter.fake
        broken._wire = RazorpayHttpWire(
            key_id=KEY_ID, key_secret=KEY_SECRET, transport=adapter.fake.transport()
        )
        return broken


@pytest.mark.parametrize(
    ("adapter_cls", "check"),
    [
        (_CreatesPerCall, "test_ensure_checkout_is_idempotent_by_receipt"),
        (_CreatesPerCall, "test_a_fresh_process_resumes_the_order_it_did_not_make"),
        (_FindThatCreates, "test_find_order_never_creates"),
        (_RemintsARecordedOrder, "test_an_order_the_charge_already_records_is_never_made_again"),
        (_ReusesAnyOrder, "test_reopening_a_receipt_for_another_amount_is_refused_not_reused"),
        (_LeaksTheCustomer, "test_the_customer_never_appears_in_the_session_or_an_error"),
        (_HandsOutTheSecret, "test_the_browser_is_never_handed_a_secret"),
        (
            _ValidatesNothing,
            "test_a_currency_the_provider_does_not_take_is_refused_with_no_request",
        ),
        (_ValidatesNothing, "test_an_amount_below_the_minimum_is_refused_with_no_request"),
        (_SlowWire, "test_the_declared_request_timeouts_fit_inside_the_call_budget"),
        (_AcceptsAnySignature, "test_a_forged_signature_never_verifies_and_never_crashes"),
        (_CrashesOnNonAsciiSignature, "test_a_forged_signature_never_verifies_and_never_crashes"),
        (_ConstantEventId, "test_parse_webhook_never_invents_a_constant_event_id"),
        (
            _BlindOrderPayments,
            "test_order_payments_is_scoped_to_its_own_order_and_carries_no_person",
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else v.__name__,
)
def test_the_contract_fails_for_an_adapter_broken_the_way_it_guards_against(
    adapter_cls: type[_BrokenRazorpay], check: str
) -> None:
    contract = _BrokenContract()
    # `pytest.raises` inside a check ends in `Failed` (not an AssertionError) when the adapter does
    # not raise what the check requires; either is the check failing.
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        getattr(contract, check)(adapter_cls())


def test_a_correct_adapter_passes_the_checks_the_negatives_run() -> None:
    """The negatives only mean something if the same check passes against an unbroken adapter."""
    contract = _BrokenContract()
    for check in (
        "test_ensure_checkout_is_idempotent_by_receipt",
        "test_find_order_never_creates",
        "test_an_order_the_charge_already_records_is_never_made_again",
        "test_reopening_a_receipt_for_another_amount_is_refused_not_reused",
        "test_the_customer_never_appears_in_the_session_or_an_error",
        "test_the_browser_is_never_handed_a_secret",
        "test_a_currency_the_provider_does_not_take_is_refused_with_no_request",
        "test_the_declared_request_timeouts_fit_inside_the_call_budget",
    ):
        getattr(contract, check)(_BrokenRazorpay())


def test_the_cashfree_money_amounts_survive_the_fake_exactly() -> None:
    """A Decimal in, a Decimal out: the fake is as strict about money as the codec."""
    fake = CashfreeFakeWire()
    provider = _cashfree(fake)
    from tests.checkout_contract import checkout_request

    request = checkout_request(amount_minor=1999)
    provider.ensure_checkout(request)
    assert fake.orders[request.receipt]["order_amount"] == Decimal("19.99")
