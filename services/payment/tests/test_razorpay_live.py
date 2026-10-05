"""The platform's live Razorpay adapter (ADR-0029, Phase A), against a stateful fake Razorpay behind
`httpx.MockTransport`: the requests it makes, the `400` it searches once more after, the money it
refuses before any request, the key it classifies as the platform's own, and the checkout callback
signature it verifies. No network and no real keys.

`TestWhatTheSandboxShowed` pins the adapter to what the owner's smoke tool observed with real test
keys on 2026-10-06: a duplicate receipt is accepted (a second Order), the receipt search can miss a
fresh Order (and answer `429`), `payment_capture` is not echoed, and a bad key is a `401`
`BAD_REQUEST_ERROR`. An Order a charge records is therefore fetched by its id, never searched for.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from app.config import settings
from app.providers import razorpay as razorpay_module
from app.providers.base import CheckoutRequest, PaymentSnapshot, StrayPayment
from app.providers.razorpay import RazorpayProvider
from app.providers.razorpay_wire import RazorpayHttpWire
from app.providers.wire import ProviderMisconfigured, ProviderRejected, ProviderTransient
from tests.checkout_contract import checkout_request
from tests.razorpay_fake import KEY_ID, KEY_SECRET, FakeRazorpay

HOOK = "dev-only-live-webhook-secret"  # pragma: allowlist secret


def _live(
    fake: FakeRazorpay, *, platform: bool = True, secret: str = KEY_SECRET
) -> RazorpayProvider:
    return RazorpayProvider(
        key_id=KEY_ID,
        key_secret=secret,
        webhook_secret=HOOK,
        wire=RazorpayHttpWire(key_id=KEY_ID, key_secret=secret, transport=fake.transport()),
        platform_billing=platform,
    )


class TestEnsureOrder:
    def test_it_looks_up_then_creates_and_asks_for_automatic_capture(self) -> None:
        fake = FakeRazorpay()
        order = _live(fake).ensure_order(
            receipt="r-1", amount_minor=49_900, currency="INR", notes={"charge_id": "r-1"}
        )
        assert fake.methods() == ["GET /v1/orders", "POST /v1/orders"]
        (create,) = fake.bodies
        assert create == {
            "amount": 49_900,
            "currency": "INR",
            "receipt": "r-1",
            "notes": {"charge_id": "r-1"},
            "payment_capture": True,
        }
        assert fake.capture_requested[order.provider_order_id] is True
        assert fake.requests[0].url.params["receipt"] == "r-1"
        assert fake.requests[0].headers["Authorization"].startswith("Basic ")

    def test_a_merchants_adapter_keeps_its_own_accounts_capture_setting(self) -> None:
        """`payment_capture` is the platform's; a tenant's account decides its own (flagged, not
        changed, by this release)."""
        fake = FakeRazorpay()
        _live(fake, platform=False).ensure_order(
            receipt="r-1", amount_minor=49_900, currency="INR", notes={}
        )
        assert "payment_capture" not in fake.bodies[0]

    def test_an_order_that_exists_is_found_and_never_created_again(self) -> None:
        fake = FakeRazorpay()
        provider = _live(fake)
        first = provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        again = provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        assert first.provider_order_id == again.provider_order_id
        assert fake.methods() == ["GET /v1/orders", "POST /v1/orders", "GET /v1/orders"]

    def test_only_the_exact_receipt_is_ours(self) -> None:
        """Razorpay's receipt filter is a "contains": `r-1` also matches `r-10`."""
        fake = FakeRazorpay()
        provider = _live(fake)
        longer = provider.ensure_order(
            receipt="r-10", amount_minor=49_900, currency="INR", notes={}
        )
        ours = provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        assert ours.provider_order_id != longer.provider_order_id
        assert provider.find_order(receipt="r-1").provider_order_id == ours.provider_order_id  # type: ignore[union-attr]

    def test_a_create_whose_answer_was_lost_is_found_by_the_next_call(self) -> None:
        """The first create landed; the caller saw a timeout. The retry looks first, so there is one
        Order, not two."""
        fake = FakeRazorpay()
        provider = _live(fake)
        fake.lose_next_create_response = True
        with pytest.raises(ProviderTransient):
            provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        order = provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        assert len(fake.orders) == 1 and order.provider_order_id in fake.orders

    def test_a_400_on_the_create_is_searched_once_more_and_an_exact_order_used(self) -> None:
        """Razorpay does not refuse a duplicate receipt, so a `400` is never "the Order exists".
        The one re-search after it is kept as a harmless recovery: an exact, same-money Order an
        earlier create left behind (the first search missed it) is a better answer than the
        refusal, and a second create is never made."""
        fake = FakeRazorpay()
        provider = _live(fake)
        made = provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        fake.requests.clear()
        fake.inject(httpx.Response(200, json={"entity": "collection", "items": []}))  # a stale list
        fake.inject(
            httpx.Response(400, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "x"}})
        )
        order = provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        assert order.provider_order_id == made.provider_order_id
        assert fake.methods() == ["GET /v1/orders", "POST /v1/orders", "GET /v1/orders"]
        assert len(fake.orders) == 1

    def test_a_400_whose_re_search_fails_concludes_nothing_and_creates_nothing_more(
        self,
    ) -> None:
        """The re-search is a recovery, not a decision: when it cannot be had (a `429`) the call is
        an unknown outcome to retry, and no second create is attempted."""
        fake = FakeRazorpay()
        provider = _live(fake)
        fake.inject(httpx.Response(200, json={"items": []}))
        fake.inject(httpx.Response(400, json={"error": {"code": "BAD_REQUEST_ERROR"}}))
        fake.inject(httpx.Response(429, json={"error": {"code": "BAD_REQUEST_ERROR"}}))
        with pytest.raises(ProviderTransient) as raised:
            provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        assert raised.value.status_code == 429
        assert fake.methods() == ["GET /v1/orders", "POST /v1/orders", "GET /v1/orders"]
        assert fake.orders == {}

    def test_a_400_with_no_order_to_find_is_the_400(self) -> None:
        fake = FakeRazorpay()
        provider = _live(fake)
        fake.inject(httpx.Response(200, json={"items": []}))
        fake.inject(
            httpx.Response(400, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "x"}})
        )
        fake.inject(httpx.Response(200, json={"items": []}))
        with pytest.raises(ProviderRejected) as raised:
            provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        assert raised.value.status_code == 400 and raised.value.code == "BAD_REQUEST_ERROR"

    def test_a_non_400_refusal_is_never_re_looked_up(self) -> None:
        fake = FakeRazorpay()
        provider = _live(fake)
        fake.inject(httpx.Response(200, json={"items": []}))
        fake.inject(httpx.Response(422, json={"error": {"code": "BAD_REQUEST_ERROR"}}))
        with pytest.raises(ProviderRejected):
            provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        assert fake.methods() == ["GET /v1/orders", "POST /v1/orders"]

    def test_an_existing_order_for_another_amount_is_refused_not_reused(self) -> None:
        fake = FakeRazorpay()
        provider = _live(fake)
        provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        fake.requests.clear()
        with pytest.raises(ProviderRejected) as raised:
            provider.ensure_order(receipt="r-1", amount_minor=59_900, currency="INR", notes={})
        assert raised.value.code == "order_amount_mismatch"
        assert fake.methods() == ["GET /v1/orders"]

    @pytest.mark.parametrize(
        ("amount", "currency", "code"),
        [
            (99, "INR", "amount_below_minimum"),
            (0, "INR", "amount_below_minimum"),
            (49_900, "USD", "unsupported_currency"),
        ],
    )
    def test_unpayable_money_is_refused_before_any_request(
        self, amount: int, currency: str, code: str
    ) -> None:
        fake = FakeRazorpay()
        with pytest.raises(ProviderRejected) as raised:
            _live(fake).ensure_order(
                receipt="r-1", amount_minor=amount, currency=currency, notes={}
            )
        assert raised.value.code == code and fake.requests == []

    def test_a_merchants_adapter_takes_the_currencies_it_is_asked_for(self) -> None:
        """INR-only and the 100-paise minimum are the platform's rules, not every merchant's."""
        fake = FakeRazorpay()
        with pytest.raises(ProviderRejected) as raised:  # the fake account itself refuses USD
            _live(fake, platform=False).ensure_order(
                receipt="r-1", amount_minor=49_900, currency="USD", notes={}
            )
        # The refused create is searched once more (the recovery after any 400), finds nothing,
        # and the 400 stands.
        assert fake.methods() == ["GET /v1/orders", "POST /v1/orders", "GET /v1/orders"]
        assert raised.value.status_code == 400


class TestWhatTheSandboxShowed:
    """The four facts the owner's smoke tool observed with real test keys (2026-10-06), each as
    the fake now models it and as the adapter must cope with it."""

    def test_a_duplicate_receipt_is_accepted_so_the_search_is_no_guard(self) -> None:
        """(a) + (b): a create with an existing receipt makes a second Order, and the search can
        miss the first. `ensure_order` alone therefore cannot promise one Order per receipt — which
        is why nothing hands out an Order it did not record, and a recorded one is fetched by id."""
        fake = FakeRazorpay()
        fake.index_new_orders = False
        provider = _live(fake)
        first = provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        second = provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        assert first.provider_order_id != second.provider_order_id
        assert fake.receipt_orders("r-1") == [first.provider_order_id, second.provider_order_id]

    def test_a_429_on_the_receipt_search_is_transient_and_nothing_is_created(self) -> None:
        fake = FakeRazorpay()
        fake.inject(httpx.Response(429, json={"error": {"code": "BAD_REQUEST_ERROR"}}))
        with pytest.raises(ProviderTransient) as raised:
            _live(fake).ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        assert raised.value.status_code == 429
        assert fake.methods() == ["GET /v1/orders"] and fake.orders == {}

    def test_payment_capture_is_sent_and_never_read_back(self) -> None:
        """(c): the Order Razorpay answers with carries no `payment_capture`; the adapter sends it
        and does not depend on seeing it again."""
        fake = FakeRazorpay()
        order = _live(fake).ensure_order(
            receipt="r-1", amount_minor=49_900, currency="INR", notes={}
        )
        assert "payment_capture" not in fake.orders[order.provider_order_id]
        assert fake.capture_requested[order.provider_order_id] is True

    def test_a_bad_key_is_a_401_bad_request_error_and_the_platforms_misconfiguration(
        self,
    ) -> None:
        """(d): the observed shape — `401`, `error.code = BAD_REQUEST_ERROR`, whatever the
        description says — is the platform's own key, never the customer's refusal."""
        fake = FakeRazorpay()
        fake.inject(
            httpx.Response(
                401,
                json={"error": {"code": "BAD_REQUEST_ERROR", "description": "something else"}},
            )
        )
        with pytest.raises(ProviderMisconfigured) as raised:
            _live(fake).ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        assert (raised.value.status_code, raised.value.code) == (401, "BAD_REQUEST_ERROR")


class TestARecordedOrder:
    """`ensure_checkout` for a charge that already records its Order: `GET /orders/{id}`, never a
    receipt search, never a create."""

    def _recorded(self, fake: FakeRazorpay) -> tuple[RazorpayProvider, CheckoutRequest, str]:
        provider = _live(fake)
        request = checkout_request()
        made = provider.ensure_checkout(request)
        fake.requests.clear()
        recorded = replace(request, recorded_order_id=made.order.provider_order_id)
        return provider, recorded, made.order.provider_order_id

    def test_it_is_fetched_by_its_id_even_when_the_search_cannot_see_it(self) -> None:
        fake = FakeRazorpay()
        provider, recorded, order_id = self._recorded(fake)
        fake.unindexed.add(order_id)  # the sandbox's `count: 0` seconds after the create
        session = provider.ensure_checkout(recorded)
        assert session.order.provider_order_id == order_id
        assert session.client_action["order_id"] == order_id
        assert fake.methods() == [f"GET /v1/orders/{order_id}"]
        assert len(fake.orders) == 1

    def test_it_is_returned_never_a_duplicate_of_its_receipt(self) -> None:
        """A duplicate Order carries the same receipt (an unknown-outcome create made it). A
        receipt search could hand that one back instead — and a customer would pay an Order the
        poller never asks about. The recorded id is the answer, whatever the search shows."""
        fake = FakeRazorpay()
        provider, recorded, order_id = self._recorded(fake)
        duplicate = fake.duplicate(recorded.receipt)
        fake.unindexed.add(order_id)  # the search shows only the duplicate
        session = provider.ensure_checkout(recorded)
        assert session.order.provider_order_id == order_id != duplicate
        assert session.client_action["order_id"] == order_id

    def test_one_the_account_does_not_hold_is_not_found_and_nothing_is_made(self) -> None:
        fake = FakeRazorpay()
        provider = _live(fake)
        request = replace(checkout_request(), recorded_order_id="order_Missing0000001")
        with pytest.raises(ProviderTransient) as raised:
            provider.ensure_checkout(request)
        assert raised.value.code == "recorded_order_not_found"
        assert fake.methods() == ["GET /v1/orders/order_Missing0000001"]
        assert fake.orders == {}

    @pytest.mark.parametrize("bad", ["../payments", "order_x/payments", "pay_123", ""])
    def test_an_id_that_is_not_an_order_id_is_never_put_in_a_url(self, bad: str) -> None:
        fake = FakeRazorpay()
        request = replace(checkout_request(), recorded_order_id=bad)
        with pytest.raises(ProviderTransient) as raised:
            _live(fake).ensure_checkout(request)
        assert raised.value.code == "recorded_order_not_found" and fake.requests == []

    def test_a_429_fetching_it_is_transient(self) -> None:
        fake = FakeRazorpay()
        provider, recorded, _ = self._recorded(fake)
        fake.inject(httpx.Response(429, json={"error": {"code": "BAD_REQUEST_ERROR"}}))
        with pytest.raises(ProviderTransient) as raised:
            provider.ensure_checkout(recorded)
        assert raised.value.status_code == 429
        assert len(fake.orders) == 1

    def test_one_for_another_receipt_or_amount_is_refused_never_handed_out(self) -> None:
        fake = FakeRazorpay()
        provider, recorded, order_id = self._recorded(fake)
        fake.orders[order_id]["receipt"] = "someone-else"
        with pytest.raises(ProviderRejected) as raised:
            provider.ensure_checkout(recorded)
        assert raised.value.code == "order_receipt_mismatch"
        fake.orders[order_id]["receipt"] = recorded.receipt
        fake.orders[order_id]["amount"] = 1_000
        with pytest.raises(ProviderRejected) as raised:
            provider.ensure_checkout(recorded)
        assert raised.value.code == "order_amount_mismatch"


class TestStrayOrders:
    """`stray_order_payments`: payments on other Orders carrying the charge's exact receipt."""

    def test_it_finds_payments_on_a_duplicate_and_never_on_the_recorded_order(self) -> None:
        fake = FakeRazorpay()
        provider = _live(fake)
        made = provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        recorded = made.provider_order_id
        fake.pay(recorded, status="captured")
        duplicate = fake.duplicate("r-1")
        paid = fake.pay(duplicate, status="captured")
        fake.requests.clear()

        found = provider.stray_order_payments(receipt="r-1", recorded_order_id=recorded)

        assert found == [
            StrayPayment(
                provider_order_id=duplicate,
                snapshot=PaymentSnapshot(
                    provider_payment_id=paid,
                    status="captured",
                    amount_minor=49_900,
                    currency="INR",
                    created_at=1_700_000_000,
                ),
            )
        ]
        assert fake.methods() == ["GET /v1/orders", f"GET /v1/orders/{duplicate}/payments"]

    def test_a_longer_receipt_and_another_charges_order_are_not_strays(self) -> None:
        fake = FakeRazorpay()
        provider = _live(fake)
        recorded = provider.ensure_order(
            receipt="r-1", amount_minor=49_900, currency="INR", notes={"charge_id": "r-1"}
        ).provider_order_id
        longer = provider.ensure_order(
            receipt="r-10", amount_minor=49_900, currency="INR", notes={}
        ).provider_order_id
        foreign = fake.duplicate("r-1", notes={"charge_id": "another-charge"})
        for order_id in (longer, foreign):
            fake.pay(order_id, status="captured")
        assert provider.stray_order_payments(receipt="r-1", recorded_order_id=recorded) == []

    def test_at_most_a_few_strays_are_asked_about(self) -> None:
        fake = FakeRazorpay()
        provider = _live(fake)
        recorded = provider.ensure_order(
            receipt="r-1", amount_minor=49_900, currency="INR", notes={}
        ).provider_order_id
        for _ in range(razorpay_module._STRAY_ORDER_LIMIT + 2):
            fake.duplicate("r-1")
        fake.requests.clear()
        provider.stray_order_payments(receipt="r-1", recorded_order_id=recorded)
        assert len(fake.requests) == 1 + razorpay_module._STRAY_ORDER_LIMIT

    def test_it_finds_only_what_the_search_shows_and_the_stub_has_none(self) -> None:
        """Best-effort, like every receipt search: a duplicate the search does not show yet is not
        found (the poller asks again through the late window), and the stub never duplicates."""
        fake = FakeRazorpay()
        provider = _live(fake)
        recorded = provider.ensure_order(
            receipt="r-1", amount_minor=49_900, currency="INR", notes={}
        ).provider_order_id
        hidden = fake.duplicate("r-1")
        fake.pay(hidden, status="captured")
        fake.unindexed.add(hidden)
        assert provider.stray_order_payments(receipt="r-1", recorded_order_id=recorded) == []
        fake.unindexed.discard(hidden)
        assert len(provider.stray_order_payments(receipt="r-1", recorded_order_id=recorded)) == 1
        stub = RazorpayProvider(key_id=None, key_secret=None, webhook_secret=HOOK)
        assert stub.stray_order_payments(receipt="r-1", recorded_order_id="order_x") == []


class TestTheKey:
    @pytest.mark.parametrize("status", [401, 400])
    def test_a_bad_key_is_the_platforms_misconfiguration_whatever_status_it_comes_with(
        self, status: int
    ) -> None:
        fake = FakeRazorpay()
        fake.invalid_key_status = status
        with pytest.raises(ProviderMisconfigured):
            _live(fake, secret="dev-only-wrong-secret").ensure_order(  # pragma: allowlist secret
                receipt="r-1", amount_minor=49_900, currency="INR", notes={}
            )

    def test_the_provider_repr_shows_no_secret(self) -> None:
        provider = _live(FakeRazorpay())
        assert KEY_SECRET not in repr(provider) and HOOK not in repr(provider)
        assert KEY_SECRET not in repr(provider._wire)


class TestPaymentsAndCapabilities:
    def test_order_payments_keep_ids_money_and_codes_and_drop_the_customer(self) -> None:
        fake = FakeRazorpay()
        provider = _live(fake)
        order = provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        fake.pay(order.provider_order_id, status="failed", error_code="BAD_REQUEST_ERROR")
        fake.pay(order.provider_order_id, status="captured")
        snapshots = provider.order_payments(order_id=order.provider_order_id)
        assert [s.status for s in snapshots] == ["failed", "captured"]
        assert snapshots[0].error_code == "BAD_REQUEST_ERROR"
        for personal in ("customer@example.com", "+919876543210", "customer@upi"):
            assert personal not in repr(snapshots)

    def test_find_payment_is_the_best_attempt_in_the_neutral_vocabulary(self) -> None:
        fake = FakeRazorpay()
        provider = _live(fake)
        order = provider.ensure_order(receipt="r-1", amount_minor=49_900, currency="INR", notes={})
        assert provider.find_payment(order_id=order.provider_order_id, charge_id="c") is None
        fake.pay(order.provider_order_id, status="failed")
        fake.pay(order.provider_order_id, status="authorized")
        found = provider.find_payment(order_id=order.provider_order_id, charge_id="c")
        assert found is not None and found.state == "authorized"

    def test_a_live_platform_adapter_cannot_debit_and_says_so(self) -> None:
        provider = _live(FakeRazorpay())
        with pytest.raises(NotImplementedError):
            provider.charge(
                idempotency_key="k", amount_minor=49_900, currency="INR", mandate_id=None, notes={}
            )
        with pytest.raises(NotImplementedError):
            provider.create_mandate(
                idempotency_key="k", max_amount_minor=1, currency="INR", notes={}
            )

    def test_capabilities_by_build(self) -> None:
        live = _live(FakeRazorpay()).capabilities
        assert live.wire == "live" and live.currencies == frozenset({"INR"})
        assert live.min_amount_minor == 100 and live.requires_mandate_for_debit is True
        assert live.supports_mandate_debit is False and live.checkout_confirmation is True
        assert live.max_debit_without_afa_minor == 1_500_000
        merchant = _live(FakeRazorpay(), platform=False).capabilities
        assert merchant.currencies is None and merchant.requires_mandate_for_debit is False
        stub = RazorpayProvider(key_id=None, key_secret=None, webhook_secret=HOOK).capabilities
        assert stub.wire == "stub" and stub.currencies is None and stub.min_amount_minor == 0
        assert stub.requires_mandate_for_debit is False and stub.supports_mandate_debit is True

    def test_the_declared_budget_covers_five_requests_inside_the_call_timeout(self) -> None:
        provider = _live(FakeRazorpay())
        assert len(provider.request_timeouts_seconds) == 5
        assert sum(provider.request_timeouts_seconds) == 35.0
        assert sum(provider.request_timeouts_seconds) <= settings().provider_call_timeout_seconds

    def test_the_stub_still_records_what_a_customer_paid(self) -> None:
        stub = RazorpayProvider(key_id=None, key_secret=None, webhook_secret=HOOK)
        stub.stub_record_payment(
            "order_x",
            PaymentSnapshot(
                provider_payment_id="pay_x", status="captured", amount_minor=100, currency="INR"
            ),
        )
        assert [s.status for s in stub.order_payments(order_id="order_x")] == ["captured"]
        with pytest.raises(RuntimeError):
            _live(FakeRazorpay()).stub_record_payment(
                "o", PaymentSnapshot("p", "captured", 1, "INR")
            )


class TestTheCheckoutCallbackSignature:
    #: `openssl dgst -sha256 -hmac "dev-only-razorpay-fake-secret" <"order_Abc123|pay_Xyz789">`.
    KNOWN = (
        "2f639a5a9d0759e72926c628b8deb48e"  # pragma: allowlist secret
        "1f54bea83f8fc49a28adb8d8eb93c107"  # pragma: allowlist secret
    )

    def test_the_known_answer(self) -> None:
        provider = _live(FakeRazorpay())
        assert provider.sign_checkout("order_Abc123", "pay_Xyz789") == self.KNOWN
        assert provider.verify_checkout_signature(
            order_id="order_Abc123", payment_id="pay_Xyz789", signature=self.KNOWN
        )

    @pytest.mark.parametrize(
        ("order_id", "payment_id", "signature"),
        [
            ("order_Abc124", "pay_Xyz789", KNOWN),  # another order
            ("order_Abc123", "pay_Xyz780", KNOWN),  # another payment
            ("pay_Xyz789", "order_Abc123", KNOWN),  # swapped
            ("order_Abc123", "pay_Xyz789", KNOWN.upper()),
            ("order_Abc123", "pay_Xyz789", KNOWN[:-1]),
            ("order_Abc123", "pay_Xyz789", ""),
            ("", "pay_Xyz789", KNOWN),
            ("order_Abc123", "", KNOWN),
            ("order_Abc123", "pay_Xyz789", "☃" * 64),  # non-ASCII: False, not a TypeError
        ],
    )
    def test_anything_else_is_false_and_never_raises(
        self, order_id: str, payment_id: str, signature: str
    ) -> None:
        provider = _live(FakeRazorpay())
        assert not provider.verify_checkout_signature(
            order_id=order_id, payment_id=payment_id, signature=signature
        )

    def test_a_live_adapter_with_no_key_secret_verifies_nothing(self) -> None:
        fake = FakeRazorpay()
        provider = RazorpayProvider(
            key_id=KEY_ID,
            key_secret=None,
            webhook_secret=HOOK,
            wire=RazorpayHttpWire(key_id=KEY_ID, key_secret=KEY_SECRET, transport=fake.transport()),
            platform_billing=True,
        )
        signature = _live(fake).sign_checkout("order_Abc123", "pay_Xyz789")
        assert not provider.verify_checkout_signature(
            order_id="order_Abc123", payment_id="pay_Xyz789", signature=signature
        )

    def test_the_stub_uses_its_own_dev_secret_which_proves_nothing_about_payment(self) -> None:
        stub = RazorpayProvider(key_id=None, key_secret=None, webhook_secret=HOOK)
        signature = stub.sign_checkout("order_a", "pay_b")
        assert stub.verify_checkout_signature(
            order_id="order_a", payment_id="pay_b", signature=signature
        )
        assert signature != self.KNOWN


def test_the_checkout_session_for_a_live_razorpay_order_is_openable() -> None:
    fake = FakeRazorpay()
    session = _live(fake).ensure_checkout(checkout_request())
    assert session.client_action == {
        "type": "razorpay_checkout",
        "key_id": KEY_ID,
        "order_id": session.order.provider_order_id,
        "openable": True,
    }
    stub = RazorpayProvider(key_id=None, key_secret=None, webhook_secret=HOOK)
    assert stub.ensure_checkout(checkout_request()).client_action["openable"] is False
