"""Runs `ProviderContract` against the stub adapter — and against eight broken ones.

The stub Razorpay adapter is the only adapter today; the live one (B5g) subclasses the same
contract and must pass it unchanged (`provider_contract.py`). The second half of this module is why
the contract can be trusted: each check is run against an adapter broken in exactly the way it
exists to catch, and must fail. A contract that passes everything protects nothing.
"""

from __future__ import annotations

import hmac
import itertools
import uuid
from dataclasses import replace

import pytest

from app.config import settings
from app.providers.base import ChargeResult, OrderResult, PaymentProvider, WebhookEvent
from app.providers.razorpay import RazorpayProvider
from tests.helpers import razorpay_webhook_body
from tests.provider_contract import ProviderContract


def _stub() -> RazorpayProvider:
    return RazorpayProvider(
        key_id=None,
        key_secret=None,
        webhook_secret=settings().razorpay_webhook_secret.get_secret_value(),
    )


class _StubContract(ProviderContract):
    """The contract with the one thing it cannot know: what Razorpay's bodies look like."""

    def webhook_bodies(self, adapter: PaymentProvider) -> tuple[bytes, bytes]:
        org, charge = uuid.uuid4(), uuid.uuid4()
        common = {"organization_id": org, "charge_id": charge, "body_id": False}
        return (
            razorpay_webhook_body(
                event="payment.authorized", provider_payment_id="pay_1", **common
            ),
            razorpay_webhook_body(
                event="payment.captured",
                provider_payment_id="pay_2",
                created_at=1_700_000_500,
                **common,
            ),
        )


class TestTheStubRazorpayAdapter(_StubContract):
    """The whole contract, against the adapter the platform runs today."""

    @pytest.fixture
    def adapter(self) -> PaymentProvider:
        return _stub()


# --------------------------------------------------------------------------- broken adapters


class _Broken(RazorpayProvider):
    """A stub adapter with one behaviour spoiled. Built with no arguments, like the contract's own
    `adapter` fixture would build it."""

    def __init__(self) -> None:
        super().__init__(key_id=None, key_secret=None, webhook_secret="x")


class _OrderPerCall(_Broken):
    """Creates a fresh Order every time — no lookup by receipt. A resumed charge would be made
    against a new Order, where `find_payment` finds nothing: a second debit."""

    def __init__(self) -> None:
        super().__init__()
        self._counter = itertools.count()

    def ensure_order(
        self, *, receipt: str, amount_minor: int, currency: str, notes: dict[str, str]
    ) -> OrderResult:
        order_id = f"order_{next(self._counter)}"
        self._orders_by_receipt[receipt] = order_id
        return OrderResult(provider="razorpay", provider_order_id=order_id, receipt=receipt)


class _FindThatCreates(_Broken):
    """`find_order` makes the Order on a miss. The sweep's 'is there a payment?' look would leave a
    phantom Order behind every charge it asked about."""

    def find_order(self, *, receipt: str) -> OrderResult | None:
        order_id = self._orders_by_receipt.setdefault(receipt, f"order_phantom_{receipt[:8]}")
        return OrderResult(provider="razorpay", provider_order_id=order_id, receipt=receipt)


class _BlindToItsOwnPayments(_Broken):
    """`find_payment` never finds anything, so the service creates on every call — the double
    debit lookup-before-create exists to prevent."""

    def find_payment(self, *, order_id: str, charge_id: str) -> ChargeResult | None:
        return None


class _SlowWire(_Broken):
    """Per-request timeouts that cannot fit the call budget: 4 x 20s against 30s."""

    request_timeouts_seconds = (20.0, 20.0, 20.0, 20.0)


class _NoTimeoutsDeclared(_Broken):
    request_timeouts_seconds = ()


class _AcceptsAnySignature(_Broken):
    """Verification that verifies nothing: the signature is a webhook's only authentication."""

    def verify_webhook(self, *, signature: str | None, body: bytes) -> bool:
        return bool(signature)


class _CrashesOnNonAsciiSignature(_Broken):
    """The defect this slice fixed: `compare_digest` on a non-ASCII `str` is a `TypeError`, so any
    unauthenticated request could turn a webhook into a 500."""

    def verify_webhook(self, *, signature: str | None, body: bytes) -> bool:
        if not signature:
            return False
        return hmac.compare_digest(self.sign(body), signature)


class _ConstantEventId(_Broken):
    """Reports the same event id for every body — the empty-id collision, with a real-looking id."""

    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        return replace(super().parse_webhook(body=body), provider_event_id="evt_constant")


@pytest.mark.parametrize(
    ("adapter_cls", "check"),
    [
        (_OrderPerCall, "test_ensure_order_is_idempotent_by_receipt"),
        (_FindThatCreates, "test_find_order_never_creates"),
        (
            _FindThatCreates,
            "test_the_lookup_only_call_finds_what_a_create_made_and_never_makes_anything",
        ),
        (_BlindToItsOwnPayments, "test_find_payment_finds_the_payment_made_against_the_order"),
        (_BlindToItsOwnPayments, "test_create_is_called_at_most_once_per_receipt"),
        (_SlowWire, "test_the_declared_request_timeouts_fit_inside_the_call_budget"),
        (_NoTimeoutsDeclared, "test_the_declared_request_timeouts_fit_inside_the_call_budget"),
        (_AcceptsAnySignature, "test_a_forged_signature_never_verifies_and_never_crashes"),
        (_CrashesOnNonAsciiSignature, "test_a_forged_signature_never_verifies_and_never_crashes"),
        (_ConstantEventId, "test_parse_webhook_never_invents_a_constant_event_id"),
    ],
    ids=lambda v: v if isinstance(v, str) else v.__name__,
)
def test_the_contract_fails_for_an_adapter_broken_the_way_it_guards_against(
    adapter_cls: type[_Broken], check: str
) -> None:
    contract = _StubContract()
    with pytest.raises(AssertionError):
        getattr(contract, check)(adapter_cls())


def test_the_default_timeout_budget_of_the_stub_leaves_room_under_the_lease() -> None:
    """The lease is validated to be at least twice the call timeout plus a minute (`config.py`);
    the adapter's declared worst case must sit inside that call timeout, which is what makes the
    lease outlive an abandoned call."""
    budget = settings().provider_call_timeout_seconds
    assert sum(_stub().request_timeouts_seconds) <= budget
    assert settings().charge_reclaim_after_seconds >= 2 * budget + 60
