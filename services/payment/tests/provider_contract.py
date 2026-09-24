"""`ProviderContract` — what every `PaymentProvider` adapter must do, as executable tests.

ADR-0004 §6 and its D3 addendum say what the seam guarantees: Razorpay has no idempotency key on a
payment create, so an adapter is **lookup before create** — the Order is found by its `receipt`
before one is made, a payment already made against it is found before another is made, and a call
resumed after a crash lands on the payment the first one made instead of debiting twice. Prose is
how that gets forgotten when the wire is finally written (B5g); this is the same statement as a
test the live adapter has to pass, unchanged.

**Using it.** It is not collected on its own (no `Test` prefix). An adapter's test module subclasses
it and supplies the one fixture, `adapter`:

    class TestMyAdapter(ProviderContract):
        @pytest.fixture
        def adapter(self) -> PaymentProvider:
            return MyAdapter(...)

Today that is the stub Razorpay adapter (`test_provider_contract.py`). The live adapter's PR adds
its own subclass — run against a recorded-wire double, never the network — and does not merge red.

What it pins:

1. `ensure_order` is idempotent by `receipt`, finds before it creates, and `find_order` never
   creates.
2. `find_payment` finds the payment made against an Order, and only that Order's.
3. A charge's provider call creates **at most once per receipt** — however many times the call is
   made, by however many processes (a fresh adapter over the same provider account included).
4. The per-request timeouts an adapter declares, summed, fit inside
   `Settings.provider_call_timeout_seconds` — or an abandoned call can still land its create after
   the sweep's lookup found nothing.

Each check is a plain method taking the adapter, so `test_provider_contract.py` can also run every
one against a deliberately broken adapter and require it to fail: a contract that cannot fail is
decoration.
"""

from __future__ import annotations

import math
import uuid
from collections import Counter
from typing import Any, cast

import pytest

from app import services
from app.config import settings
from app.models import Charge
from app.providers.base import MalformedWebhook, PaymentProvider


class CountingAdapter:
    """Wraps an adapter and counts every call by method name, delegating everything. Counts what
    reaches the provider; `create` calls are the ones a real account would be billed for."""

    def __init__(self, inner: PaymentProvider) -> None:
        self.inner = inner
        self.calls: Counter[str] = Counter()

    def as_provider(self) -> PaymentProvider:
        """This wrapper as the `PaymentProvider` the service functions are typed to take."""
        return cast(PaymentProvider, self)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            return attr(*args, **kwargs)

        return counted


def new_charge(adapter: PaymentProvider, *, amount_minor: int = 1000) -> Charge:
    """An unsaved `Charge` — everything `services.call_provider_charge` reads, no session. Its `id`
    is the Order receipt (36 characters, unique, stable), exactly as in production."""
    return Charge(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        provider=adapter.name,
        amount_minor=amount_minor,
        currency="INR",
        idempotency_key=f"invoice:{uuid.uuid4()}",
        state="created",
    )


def _notes(charge_id: str) -> dict[str, str]:
    return {"organization_id": str(uuid.uuid4()), "charge_id": charge_id}


class ProviderContract:
    """Subclass and supply the `adapter` fixture. See the module docstring."""

    @pytest.fixture
    def adapter(self) -> PaymentProvider:
        raise NotImplementedError("a subclass supplies the adapter under test")

    # ------------------------------------------------------------------ the shape

    def test_it_is_a_payment_provider_with_a_name(self, adapter: PaymentProvider) -> None:
        assert isinstance(adapter, PaymentProvider)
        assert isinstance(adapter.name, str) and adapter.name

    # ------------------------------------------------------------------ 1. orders

    def test_ensure_order_is_idempotent_by_receipt(self, adapter: PaymentProvider) -> None:
        receipt = str(uuid.uuid4())
        first = adapter.ensure_order(
            receipt=receipt, amount_minor=1000, currency="INR", notes=_notes(receipt)
        )
        second = adapter.ensure_order(
            receipt=receipt, amount_minor=1000, currency="INR", notes=_notes(receipt)
        )
        assert first.provider_order_id == second.provider_order_id
        assert first.receipt == second.receipt == receipt

    def test_a_different_receipt_is_a_different_order(self, adapter: PaymentProvider) -> None:
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        order_a = adapter.ensure_order(receipt=a, amount_minor=1, currency="INR", notes={})
        order_b = adapter.ensure_order(receipt=b, amount_minor=1, currency="INR", notes={})
        assert order_a.provider_order_id != order_b.provider_order_id

    def test_an_order_is_found_by_the_receipt_it_was_made_with(
        self, adapter: PaymentProvider
    ) -> None:
        receipt = str(uuid.uuid4())
        made = adapter.ensure_order(receipt=receipt, amount_minor=1, currency="INR", notes={})
        found = adapter.find_order(receipt=receipt)
        assert found is not None
        assert found.provider_order_id == made.provider_order_id and found.receipt == receipt

    def test_find_order_never_creates(self, adapter: PaymentProvider) -> None:
        """The stuck-charge sweep's final look at the attempt budget must find out whether the last
        attempt's create landed *without risking another*. A lookup that creates on a miss would
        answer 'none found' once and 'found' the next time — a phantom Order per look."""
        receipt = str(uuid.uuid4())
        assert adapter.find_order(receipt=receipt) is None
        assert adapter.find_order(receipt=receipt) is None
        made = adapter.ensure_order(receipt=receipt, amount_minor=1, currency="INR", notes={})
        found = adapter.find_order(receipt=receipt)
        assert found is not None and found.provider_order_id == made.provider_order_id

    # ------------------------------------------------------------------ 2. payments

    def test_no_payment_is_found_before_one_is_made(self, adapter: PaymentProvider) -> None:
        receipt = str(uuid.uuid4())
        order = adapter.ensure_order(receipt=receipt, amount_minor=1000, currency="INR", notes={})
        assert adapter.find_payment(order_id=order.provider_order_id, charge_id=receipt) is None

    def test_find_payment_finds_the_payment_made_against_the_order(
        self, adapter: PaymentProvider
    ) -> None:
        receipt = str(uuid.uuid4())
        order = adapter.ensure_order(
            receipt=receipt, amount_minor=1000, currency="INR", notes=_notes(receipt)
        )
        made = adapter.charge(
            idempotency_key=f"invoice:{receipt}",
            amount_minor=1000,
            currency="INR",
            mandate_id=None,
            notes=_notes(receipt),
            order_id=order.provider_order_id,
        )
        found = adapter.find_payment(order_id=order.provider_order_id, charge_id=receipt)
        assert found is not None
        assert found.provider_payment_id == made.provider_payment_id
        assert (found.amount_minor, found.currency) == (1000, "INR")

    def test_find_payment_is_scoped_to_its_own_order(self, adapter: PaymentProvider) -> None:
        paid_receipt, other_receipt = str(uuid.uuid4()), str(uuid.uuid4())
        paid = adapter.ensure_order(receipt=paid_receipt, amount_minor=1, currency="INR", notes={})
        other = adapter.ensure_order(
            receipt=other_receipt, amount_minor=1, currency="INR", notes={}
        )
        adapter.charge(
            idempotency_key=f"invoice:{paid_receipt}",
            amount_minor=1,
            currency="INR",
            mandate_id=None,
            notes=_notes(paid_receipt),
            order_id=paid.provider_order_id,
        )
        assert (
            adapter.find_payment(order_id=other.provider_order_id, charge_id=other_receipt) is None
        )

    # ------------------------------------------------------------------ 3. create at most once

    def test_create_is_called_at_most_once_per_receipt(self, adapter: PaymentProvider) -> None:
        """The property the whole D3 design exists for. The service's provider call is made three
        times for one charge: twice by one process (a request and its own HTTP retry), and once by
        a *fresh process* over the same provider account (the sweep resuming after a crash between
        the call and its record). One payment is created, and every call returns it."""
        charge = new_charge(adapter)
        process_one = CountingAdapter(adapter)

        first = services.call_provider_charge(process_one.as_provider(), charge)
        again = services.call_provider_charge(process_one.as_provider(), charge)
        process_two = CountingAdapter(adapter)  # same account, no memory of the first
        resumed = services.call_provider_charge(process_two.as_provider(), charge)

        assert process_one.calls["charge"] == 1, "a second call created a second payment"
        assert process_two.calls["charge"] == 0, "a resumed call created a payment it should find"
        assert first.provider_payment_id == again.provider_payment_id == resumed.provider_payment_id

    def test_two_charges_never_share_an_order_or_a_payment(self, adapter: PaymentProvider) -> None:
        a, b = new_charge(adapter), new_charge(adapter)
        first = services.call_provider_charge(adapter, a)
        second = services.call_provider_charge(adapter, b)
        assert first.provider_payment_id != second.provider_payment_id
        assert first.provider_order_id != second.provider_order_id

    def test_the_lookup_only_call_finds_what_a_create_made_and_never_makes_anything(
        self, adapter: PaymentProvider
    ) -> None:
        """`services.lookup_provider_charge` is the sweep's final check at the attempt budget. On a
        charge nobody has called for it finds nothing and creates nothing — no Order, no payment —
        and after a create it finds exactly that payment."""
        charge = new_charge(adapter)
        watched = CountingAdapter(adapter)

        assert services.lookup_provider_charge(watched.as_provider(), charge) is None
        assert watched.calls["charge"] == 0 and watched.calls["ensure_order"] == 0
        assert adapter.find_order(receipt=str(charge.id)) is None, "the lookup created an Order"

        made = services.call_provider_charge(adapter, charge)
        found = services.lookup_provider_charge(adapter, charge)
        assert found is not None and found.provider_payment_id == made.provider_payment_id

    # ------------------------------------------------------------------ 4. timeouts

    def test_the_declared_request_timeouts_fit_inside_the_call_budget(
        self, adapter: PaymentProvider
    ) -> None:
        """The caller stops waiting after `provider_call_timeout_seconds`, but the thread it
        abandoned is not cancelled: if the adapter's own worst case (every request timing out in
        turn) is longer, an abandoned call is still in flight when the charge's lease lapses and
        the sweep's lookup finds nothing — then the abandoned create lands. A second payment."""
        timeouts = adapter.request_timeouts_seconds
        assert timeouts, "an adapter must declare the timeout of every request a charge makes"
        assert all(math.isfinite(t) and t > 0 for t in timeouts), "every request needs a timeout"
        budget = settings().provider_call_timeout_seconds
        assert sum(timeouts) <= budget, (
            f"per-request timeouts sum to {sum(timeouts):g}s, over the {budget:g}s call budget"
        )

    # ------------------------------------------------------------------ webhooks

    def test_a_body_that_is_not_json_is_a_malformed_webhook_not_a_crash(
        self, adapter: PaymentProvider
    ) -> None:
        with pytest.raises(MalformedWebhook):
            adapter.parse_webhook(body=b"this is not json")

    def test_an_unsigned_webhook_does_not_verify(self, adapter: PaymentProvider) -> None:
        assert adapter.verify_webhook(signature=None, body=b"{}") is False
        assert adapter.verify_webhook(signature="", body=b"{}") is False
