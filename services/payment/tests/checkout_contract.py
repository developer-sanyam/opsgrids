"""`CheckoutContract` — what every customer-present checkout adapter must do (ADR-0032), as
executable tests. The sibling of `ProviderContract` for the shape that has no server-side debit.

`ProviderContract` holds an adapter that creates a payment against an Order (Razorpay's debit shape)
and cannot describe one that only opens a hosted checkout and asks what happened — the live platform
adapters of Phase A. The invariants are the same ones, reworded for it:

1. **Lookup before create, and the recorded Order above all.** `ensure_checkout` finds the Order by
   its receipt before it creates one — twice with one adapter, or once each with two adapters over
   the same provider account (a fresh process resuming after a crash), it is **one** Order *when
   the provider's receipt search shows it* (the fakes' does; Razorpay's real one can miss a fresh
   Order and accepts a duplicate receipt — ADR-0032, 2026-10-06); `find_order` never creates; and
   an Order the charge already records (`recorded_order_id`) is **fetched by its id** and never
   made again — found even when the receipt search cannot see it, and returned in preference to
   any duplicate carrying the same receipt.
2. **A found Order is the charge's.** Reopening a receipt for another amount is refused, never
   reused.
3. **Nothing leaves that must not.** A customer's details go to the provider and nowhere else: not
   into the session, not into an error; and no provider secret is in the checkout the browser gets.
4. **Refused before any request.** A currency or an amount the provider will not take is a refusal
   with **zero** requests made (an adapter that enforces money rules sets `enforces_money`).
5. **`order_payments`** is scoped to its own Order and carries no person.
6. **Timeouts.** The declared per-request timeouts, summed, fit inside
   `Settings.provider_call_timeout_seconds`.
7. **Webhooks.** A forged signature — any bytes at all — is `False`, never accepted and never a
   crash; a body that is not JSON is `MalformedWebhook`; `parse_webhook` never invents a constant
   event id.

Used like `ProviderContract`: a subclass supplies the `adapter` fixture and the provider-specific
hooks (`fresh`, `create_count`, `pay`, `webhook_bodies`); each check is a plain method taking the
adapter, so `test_checkout_contract.py` also runs every one against an adapter broken in the way it
guards against and requires it to fail.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, cast

import pytest

from app.config import settings
from app.providers.base import (
    CheckoutCustomer,
    CheckoutProvider,
    CheckoutRequest,
    MalformedWebhook,
    PaymentProvider,
)
from app.providers.wire import ProviderRejected, ProviderTransient

PHONE = "9876543210"
EMAIL = "contract.customer@example.com"
NAME = "Contract Customer"


def _checkout(adapter: Any) -> CheckoutProvider:
    """`adapter` as the `CheckoutProvider` it must also be (the contract asserts it is)."""
    return cast(CheckoutProvider, adapter)


def checkout_request(
    receipt: str | None = None, *, amount_minor: int = 49_900, currency: str = "INR"
) -> CheckoutRequest:
    receipt = receipt or str(uuid.uuid4())
    return CheckoutRequest(
        receipt=receipt,
        amount_minor=amount_minor,
        currency=currency,
        notes={"organization_id": str(uuid.uuid4()), "charge_id": receipt, "flow": "checkout"},
        expires_at=datetime.now(UTC) + timedelta(days=1),
        customer=CheckoutCustomer(
            customer_id=uuid.uuid4().hex, name=NAME, email=EMAIL, phone=PHONE
        ),
        return_url="https://app.opsgrids.example/billing/return",
    )


class CheckoutContract:
    """Subclass and supply the `adapter` fixture and the hooks. See the module docstring."""

    #: Whether the adapter under test refuses what its provider would (INR only, 100 paise
    #: minimum). The legacy stub does not, and says so by setting this False.
    enforces_money: ClassVar[bool] = True

    @pytest.fixture
    def adapter(self) -> PaymentProvider:
        raise NotImplementedError("a subclass supplies the adapter under test")

    # -- hooks the subclass fills in ---------------------------------------------------------

    def fresh(self, adapter: PaymentProvider) -> PaymentProvider:
        """A **second** adapter over the same provider account — a fresh process with no memory."""
        raise NotImplementedError

    def create_count(self, adapter: PaymentProvider) -> int:
        """How many Order creates have reached the provider account behind `adapter`."""
        raise NotImplementedError

    def request_count(self, adapter: PaymentProvider) -> int:
        """How many requests have reached the provider account behind `adapter` (0 for a stub)."""
        raise NotImplementedError

    def pay(self, adapter: PaymentProvider, order_id: str) -> None:
        """What a customer paying in the provider's checkout leaves behind at the provider."""
        raise NotImplementedError

    def webhook_bodies(self, adapter: PaymentProvider) -> tuple[bytes, bytes]:
        """Two authentic bodies for two **different** events, in the adapter's own format."""
        raise NotImplementedError

    def hide_from_receipt_search(self, adapter: PaymentProvider, order_id: str) -> None:
        """Make the provider's receipt search miss `order_id` (Razorpay's `count: 0` for a fresh
        Order). A provider whose Order id *is* the receipt has no separate search to miss: no-op."""
        return None

    def make_duplicate(self, adapter: PaymentProvider, receipt: str) -> str | None:
        """A second Order at the provider carrying `receipt` (what an unknown-outcome create leaves
        behind where duplicates are allowed), or None where the provider refuses them."""
        return None

    # -- 0. the shape ------------------------------------------------------------------------

    def test_it_is_a_checkout_provider(self, adapter: PaymentProvider) -> None:
        assert isinstance(adapter, PaymentProvider) and isinstance(adapter, CheckoutProvider)
        assert adapter.name

    # -- 1. lookup before create -------------------------------------------------------------

    def test_ensure_checkout_is_idempotent_by_receipt(self, adapter: PaymentProvider) -> None:
        request = checkout_request()
        first = _checkout(adapter).ensure_checkout(request)
        again = _checkout(adapter).ensure_checkout(request)
        assert first.order.provider_order_id == again.order.provider_order_id
        assert first.order.receipt == again.order.receipt == request.receipt
        assert self.create_count(adapter) == 1, "a second call created a second Order"

    def test_a_fresh_process_resumes_the_order_it_did_not_make(
        self, adapter: PaymentProvider
    ) -> None:
        """The crash between the provider call and recording its answer: the sweep (or the customer
        clicking again) runs `ensure_checkout` from a process that remembers nothing."""
        request = checkout_request()
        made = _checkout(adapter).ensure_checkout(request)
        resumed = _checkout(self.fresh(adapter)).ensure_checkout(request)
        assert made.order.provider_order_id == resumed.order.provider_order_id
        assert self.create_count(adapter) == 1

    def test_an_order_the_charge_already_records_is_never_made_again(
        self, adapter: PaymentProvider
    ) -> None:
        """A reopened checkout names the Order its charge recorded (`recorded_order_id`). An adapter
        that holds it returns it; one that does not — the stub after a rollback from the live wire,
        another environment's keys — refuses as "could not be asked" and creates **nothing**: a
        stand-in Order under the same receipt would make it the authority on a payment it cannot
        see, and the poller would expire a checkout the customer paid."""
        request = checkout_request()
        made = _checkout(adapter).ensure_checkout(request)
        recorded = replace(request, recorded_order_id=made.order.provider_order_id)
        again = _checkout(adapter).ensure_checkout(recorded)
        assert again.order.provider_order_id == made.order.provider_order_id
        assert self.create_count(adapter) == 1

        orphan = checkout_request()
        with pytest.raises(ProviderTransient):
            _checkout(adapter).ensure_checkout(replace(orphan, recorded_order_id="order_elsewhere"))
        assert self.create_count(adapter) == 1, "an Order recorded elsewhere was made again here"
        assert adapter.find_order(receipt=orphan.receipt) is None

    def test_a_recorded_order_is_found_when_the_receipt_search_cannot_see_it(
        self, adapter: PaymentProvider
    ) -> None:
        """Razorpay's receipt search answered `count: 0` for an Order created seconds earlier. A
        reopen that searched for the recorded Order would refuse a customer whose checkout exists
        (or, worse, make another); it must fetch the Order by the id the charge records."""
        request = checkout_request()
        made = _checkout(adapter).ensure_checkout(request)
        self.hide_from_receipt_search(adapter, made.order.provider_order_id)
        recorded = replace(request, recorded_order_id=made.order.provider_order_id)
        try:
            again = _checkout(adapter).ensure_checkout(recorded)
        except ProviderTransient as exc:
            raise AssertionError(
                f"the recorded Order was not found once the receipt search missed it ({exc.code})"
            ) from exc
        assert again.order.provider_order_id == made.order.provider_order_id
        assert self.create_count(adapter) == 1, "a recorded Order was made again"

    def test_a_recorded_order_is_handed_out_never_a_duplicate_of_its_receipt(
        self, adapter: PaymentProvider
    ) -> None:
        """Where a provider accepts a duplicate receipt, two Orders can carry one charge's receipt.
        Only the recorded one may reach a browser: a customer who paid the other would pay an Order
        nothing asks about."""
        request = checkout_request()
        made = _checkout(adapter).ensure_checkout(request)
        duplicate = self.make_duplicate(adapter, request.receipt)
        if duplicate is None:
            return  # the provider keeps a receipt unique: there is no second Order to hand out
        self.hide_from_receipt_search(adapter, made.order.provider_order_id)
        recorded = replace(request, recorded_order_id=made.order.provider_order_id)
        again = _checkout(adapter).ensure_checkout(recorded)
        assert again.order.provider_order_id == made.order.provider_order_id != duplicate
        assert duplicate not in repr(again.client_action), "a duplicate Order was handed out"

    def test_a_different_receipt_is_a_different_order(self, adapter: PaymentProvider) -> None:
        a = _checkout(adapter).ensure_checkout(checkout_request())
        b = _checkout(adapter).ensure_checkout(checkout_request())
        assert a.order.provider_order_id != b.order.provider_order_id

    def test_find_order_never_creates(self, adapter: PaymentProvider) -> None:
        request = checkout_request()
        assert adapter.find_order(receipt=request.receipt) is None
        assert adapter.find_order(receipt=request.receipt) is None
        assert self.create_count(adapter) == 0, "a lookup created an Order"
        made = _checkout(adapter).ensure_checkout(request)
        found = adapter.find_order(receipt=request.receipt)
        assert found is not None and found.provider_order_id == made.order.provider_order_id

    # -- 2. a found Order is the charge's ----------------------------------------------------

    def test_reopening_a_receipt_for_another_amount_is_refused_not_reused(
        self, adapter: PaymentProvider
    ) -> None:
        if not self.enforces_money:
            return  # the legacy stub keeps no Order amount to compare
        receipt = str(uuid.uuid4())
        _checkout(adapter).ensure_checkout(checkout_request(receipt, amount_minor=49_900))
        with pytest.raises(ProviderRejected):
            _checkout(adapter).ensure_checkout(checkout_request(receipt, amount_minor=59_900))

    # -- 3. nothing leaves that must not -----------------------------------------------------

    def test_the_customer_never_appears_in_the_session_or_an_error(
        self, adapter: PaymentProvider
    ) -> None:
        session = _checkout(adapter).ensure_checkout(checkout_request())
        shown = repr(session) + repr(session.order) + repr(session.client_action)
        for personal in (PHONE, EMAIL, NAME):
            assert personal not in shown, f"{personal!r} is in what the browser is handed"
        assert session.client_action.get("type")

    def test_the_browser_is_never_handed_a_secret(self, adapter: PaymentProvider) -> None:
        session = _checkout(adapter).ensure_checkout(checkout_request())
        text = repr(session.client_action).lower()
        for forbidden in ("secret", "password", "private"):
            assert forbidden not in text.replace("payment_session_id", "")

    # -- 4. refused before any request -------------------------------------------------------

    def test_a_currency_the_provider_does_not_take_is_refused_with_no_request(
        self, adapter: PaymentProvider
    ) -> None:
        if not self.enforces_money:
            return  # the legacy stub accepts any currency
        before = self.request_count(adapter)
        with pytest.raises(ProviderRejected) as raised:
            _checkout(adapter).ensure_checkout(checkout_request(currency="USD"))
        assert raised.value.code == "unsupported_currency"
        assert self.request_count(adapter) == before, "a request was made for an unpayable order"

    def test_an_amount_below_the_minimum_is_refused_with_no_request(
        self, adapter: PaymentProvider
    ) -> None:
        if not self.enforces_money:
            return  # the legacy stub accepts any amount
        before = self.request_count(adapter)
        with pytest.raises(ProviderRejected) as raised:
            _checkout(adapter).ensure_checkout(checkout_request(amount_minor=99))
        assert raised.value.code == "amount_below_minimum"
        assert self.request_count(adapter) == before

    # -- 5. order_payments -------------------------------------------------------------------

    def test_order_payments_is_scoped_to_its_own_order_and_carries_no_person(
        self, adapter: PaymentProvider
    ) -> None:
        paid = _checkout(adapter).ensure_checkout(checkout_request())
        other = _checkout(adapter).ensure_checkout(checkout_request())
        assert adapter.order_payments(order_id=paid.order.provider_order_id) == []
        self.pay(adapter, paid.order.provider_order_id)
        found = adapter.order_payments(order_id=paid.order.provider_order_id)
        assert [p.status for p in found] == ["captured"]
        assert (found[0].amount_minor, found[0].currency) == (49_900, "INR")
        assert adapter.order_payments(order_id=other.order.provider_order_id) == []
        for personal in (PHONE, EMAIL, "customer@upi", "+91"):
            assert personal not in repr(found)

    # -- 6. timeouts -------------------------------------------------------------------------

    def test_the_declared_request_timeouts_fit_inside_the_call_budget(
        self, adapter: PaymentProvider
    ) -> None:
        timeouts = adapter.request_timeouts_seconds
        assert timeouts, "an adapter must declare the timeout of every request a checkout makes"
        assert all(math.isfinite(t) and t > 0 for t in timeouts)
        budget = settings().provider_call_timeout_seconds
        assert sum(timeouts) <= budget, f"{sum(timeouts):g}s of timeouts, {budget:g}s budget"

    # -- 7. webhooks -------------------------------------------------------------------------

    def test_a_body_that_is_not_json_is_a_malformed_webhook_not_a_crash(
        self, adapter: PaymentProvider
    ) -> None:
        with pytest.raises(MalformedWebhook):
            adapter.parse_webhook(body=b"this is not json")

    def test_an_unsigned_webhook_does_not_verify(self, adapter: PaymentProvider) -> None:
        assert adapter.verify_webhook(signature=None, body=b"{}") is False
        assert adapter.verify_webhook(signature="", body=b"{}") is False

    def test_a_forged_signature_never_verifies_and_never_crashes(
        self, adapter: PaymentProvider
    ) -> None:
        for forged in ("0" * 64, "zz", "\xff" * 64, "é" * 32, "☃" * 8, " " * 64, "A" * 44):
            try:
                verified = adapter.verify_webhook(signature=forged, body=b"{}")
            except Exception as exc:
                raise AssertionError(
                    f"verify_webhook raised {type(exc).__name__} for the signature {forged!r}"
                ) from exc
            assert verified is False, f"verify_webhook accepted the forged signature {forged!r}"

    def test_parse_webhook_never_invents_a_constant_event_id(
        self, adapter: PaymentProvider
    ) -> None:
        first, second = self.webhook_bodies(adapter)
        a = adapter.parse_webhook(body=first).provider_event_id
        b = adapter.parse_webhook(body=second).provider_event_id
        assert not (a and a == b), f"two different events parsed to the one event id {a!r}"


def check(contract: Any, name: str, adapter: Any) -> None:
    """Run one named check of `contract` against `adapter` (for the broken-adapter negatives)."""
    getattr(contract, name)(adapter)
