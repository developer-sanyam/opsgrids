"""`CollectionsContract` — what every merchant (tenant collections) adapter must do, as executable
tests (ADR-0033). The sibling of `ProviderContract` and `CheckoutContract` for the standalone
`CollectionsProvider` seam: there is no server-side debit here, so nothing about `charge` or
`find_payment`, and the invariants are reworded for an Order a customer pays in the provider's own
checkout.

1. **Lookup before create.** `ensure_order` finds the Order by its receipt before it creates one —
   twice with one adapter, or once each with two adapters over the same provider account (a fresh
   process after a crash), it is **one** Order; `find_order` never creates. (Against real
   Razorpay the receipt search is best-effort — ADR-0032, "Sandbox facts" — so this is held
   against a double whose search is exact; collections relies on it only until an Order id is
   recorded.) **A recorded Order is never made again**: given `recorded_order_id`, the adapter
   fetches that Order by its id, and one its account does not hold is refused
   (`recorded_order_not_found`) with nothing created.
2. **`order_payments`** is scoped to its own Order, in the neutral vocabulary, and carries no
   person.
3. **A refund is idempotent by its key**: the same key twice is one refund at the provider, and the
   result's state is in the platform's refund vocabulary.
4. **Nothing leaves that must not.** The payer goes to the provider and nowhere else: not into the
   Order result, not into an error, not into a repr.
5. **Timeouts.** The declared per-request timeouts are finite and positive, and their sum fits
   inside the collection's right-to-call lease (`2 x collections_call_timeout_seconds + 60`): a
   call the caller stopped waiting for is over before a retry may race it.
6. **Webhooks.** An unsigned or forged signature — any bytes at all — is `False`, never accepted
   and never a crash; a properly signed body verifies; a body that is not JSON is
   `MalformedWebhook`; `parse_webhook` never invents a constant event id; and what
   `webhook_facts` stores re-parses to the same identity (`_identity_of`).
7. **Probe.** Good credentials pass; credentials the provider rejects are `ProviderMisconfigured`.

A subclass supplies the `adapter` fixture and the hooks; each check is a plain method taking the
adapter, so `test_collections_contract.py` also runs them against broken adapters and requires
each to fail.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.config import settings
from app.domain import derive_event_id
from app.providers.base import CheckoutCustomer, CollectionsProvider, MalformedWebhook
from app.providers.wire import ProviderMisconfigured, ProviderTransient

PHONE = "9876543210"
EMAIL = "contract.payer@example.com"
NAME = "Contract Payer"


def payer() -> CheckoutCustomer:
    return CheckoutCustomer(customer_id=f"c{uuid.uuid4().hex}", name=NAME, email=EMAIL, phone=PHONE)


def ensure(
    adapter: Any,
    receipt: str,
    *,
    amount_minor: int = 49_900,
    recorded_order_id: str | None = None,
) -> Any:
    return adapter.ensure_order(
        receipt=receipt,
        amount_minor=amount_minor,
        currency="INR",
        notes={"organization_id": str(uuid.uuid4()), "charge_id": receipt},
        payer=payer(),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        notify_url="https://app.opsgrids.example/api/v1/webhooks/collections?account=x",
        recorded_order_id=recorded_order_id,
    )


class CollectionsContract:
    """Subclass and supply the `adapter` fixture and the hooks. See the module docstring."""

    @pytest.fixture
    def adapter(self) -> CollectionsProvider:
        raise NotImplementedError("a subclass supplies the adapter under test")

    # -- hooks -------------------------------------------------------------------------------

    def fresh(self, adapter: Any) -> Any:
        """A second adapter over the same provider account — a fresh process, no memory."""
        raise NotImplementedError

    def create_count(self, adapter: Any) -> int:
        """How many Order creates reached the provider account behind `adapter`."""
        raise NotImplementedError

    def pay(self, adapter: Any, order_id: str) -> str:
        """A customer paying the Order in the provider's checkout; returns the payment id."""
        raise NotImplementedError

    def refund_count(self, adapter: Any, order_id: str) -> int | None:
        """Refunds the provider account holds for the Order, or None when it keeps no count."""
        raise NotImplementedError

    def signed(self, adapter: Any, body: bytes) -> dict[str, str]:
        """The headers a genuine delivery of `body` carries (lower-cased)."""
        raise NotImplementedError

    def webhook_bodies(self, adapter: Any) -> tuple[bytes, bytes]:
        """Two authentic bodies for two different events, in the provider's own format."""
        raise NotImplementedError

    def rejecting(self, adapter: Any) -> Any | None:
        """An adapter over the same account with credentials the provider rejects, or None when
        the double cannot model a rejection (the legacy stub makes no request at all)."""
        raise NotImplementedError

    def unheld_order_id(self, receipt: str) -> str:
        """An Order id a collection with this `receipt` could have recorded, which the account
        behind the adapter does not hold (made on the live wire before the stub, or elsewhere)."""
        raise NotImplementedError

    # -- 0. the shape ------------------------------------------------------------------------

    def test_it_is_a_collections_provider(self, adapter: Any) -> None:
        assert isinstance(adapter, CollectionsProvider)
        assert isinstance(adapter.name, str) and adapter.name

    # -- 1. lookup before create -------------------------------------------------------------

    def test_ensure_order_is_idempotent_by_receipt(self, adapter: Any) -> None:
        receipt = str(uuid.uuid4())
        first = ensure(adapter, receipt)
        again = ensure(adapter, receipt)
        assert first.provider_order_id == again.provider_order_id
        assert first.receipt == again.receipt == receipt
        assert self.create_count(adapter) == 1, "a second call created a second Order"

    def test_a_fresh_process_resumes_the_order_it_did_not_make(self, adapter: Any) -> None:
        receipt = str(uuid.uuid4())
        made = ensure(adapter, receipt)
        resumed = ensure(self.fresh(adapter), receipt)
        assert made.provider_order_id == resumed.provider_order_id
        assert self.create_count(adapter) == 1

    def test_a_recorded_order_is_fetched_by_its_id_and_never_made_again(self, adapter: Any) -> None:
        receipt = str(uuid.uuid4())
        made = ensure(adapter, receipt)
        again = ensure(self.fresh(adapter), receipt, recorded_order_id=made.provider_order_id)
        assert again.provider_order_id == made.provider_order_id
        assert self.create_count(adapter) == 1, "a recorded Order was made again"

    def test_a_recorded_order_the_account_does_not_hold_is_refused_and_nothing_is_made(
        self, adapter: Any
    ) -> None:
        receipt = str(uuid.uuid4())
        try:
            ensure(adapter, receipt, recorded_order_id=self.unheld_order_id(receipt))
        except ProviderTransient as exc:
            assert exc.code == "recorded_order_not_found", exc.code
        else:
            raise AssertionError("an Order the collection records was minted where it is not held")
        assert self.create_count(adapter) == 0, "a recorded Order was made again"

    def test_a_different_receipt_is_a_different_order(self, adapter: Any) -> None:
        a = ensure(adapter, str(uuid.uuid4()))
        b = ensure(adapter, str(uuid.uuid4()))
        assert a.provider_order_id != b.provider_order_id

    def test_find_order_never_creates(self, adapter: Any) -> None:
        receipt = str(uuid.uuid4())
        assert adapter.find_order(receipt=receipt) is None
        assert adapter.find_order(receipt=receipt) is None
        assert self.create_count(adapter) == 0, "a lookup created an Order"
        made = ensure(adapter, receipt)
        found = adapter.find_order(receipt=receipt)
        assert found is not None and found.provider_order_id == made.provider_order_id

    # -- 2. order_payments -------------------------------------------------------------------

    def test_order_payments_is_scoped_to_its_own_order_and_carries_no_person(
        self, adapter: Any
    ) -> None:
        paid = ensure(adapter, str(uuid.uuid4()))
        other = ensure(adapter, str(uuid.uuid4()))
        assert adapter.order_payments(order_id=paid.provider_order_id) == []
        self.pay(adapter, paid.provider_order_id)
        found = adapter.order_payments(order_id=paid.provider_order_id)
        assert [p.status for p in found] == ["captured"]
        assert (found[0].amount_minor, found[0].currency) == (49_900, "INR")
        assert adapter.order_payments(order_id=other.provider_order_id) == []
        for personal in (PHONE, EMAIL, NAME, "customer@", "+91"):
            assert personal not in repr(found)

    # -- 3. refunds --------------------------------------------------------------------------

    def test_a_refund_is_idempotent_by_its_key(self, adapter: Any) -> None:
        order = ensure(adapter, str(uuid.uuid4()))
        payment_id = self.pay(adapter, order.provider_order_id)
        key = "rfd" + uuid.uuid4().hex[:36]
        kwargs: dict[str, Any] = {
            "idempotency_key": key,
            "provider_payment_id": payment_id,
            "amount_minor": 10_000,
            "currency": "INR",
            "provider_order_id": order.provider_order_id,
        }
        first = adapter.refund(**kwargs)
        again = adapter.refund(**kwargs)
        assert first.provider_refund_id == again.provider_refund_id
        assert first.state in ("created", "processed", "failed")
        assert first.amount_minor == 10_000 and first.currency == "INR"
        count = self.refund_count(adapter, order.provider_order_id)
        assert count in (None, 1), "the same key made a second refund"

    # -- 4. nothing leaves that must not -----------------------------------------------------

    def test_the_payer_never_appears_in_the_order_or_its_repr(self, adapter: Any) -> None:
        order = ensure(adapter, str(uuid.uuid4()))
        shown = repr(order) + repr(adapter)
        for personal in (PHONE, EMAIL, NAME):
            assert personal not in shown

    # -- 5. timeouts -------------------------------------------------------------------------

    def test_the_declared_request_timeouts_end_before_the_lease_does(self, adapter: Any) -> None:
        timeouts = adapter.request_timeouts_seconds
        assert timeouts, "an adapter must declare the timeout of every request a call makes"
        assert all(math.isfinite(t) and t > 0 for t in timeouts)
        lease = settings().collection_lease_seconds
        assert sum(timeouts) < lease, f"{sum(timeouts):g}s of timeouts, a {lease}s lease"

    # -- 6. webhooks -------------------------------------------------------------------------

    def test_an_unsigned_webhook_does_not_verify(self, adapter: Any) -> None:
        assert adapter.verify_webhook(headers={}, body=b"{}") is False

    def test_a_forged_signature_never_verifies_and_never_crashes(self, adapter: Any) -> None:
        body = self.webhook_bodies(adapter)[0]
        genuine = self.signed(adapter, body)
        for forged in ("0" * 64, "zz", "\xff" * 64, "é" * 32, "☃" * 8, " " * 64, "A" * 44):
            headers = {**genuine, **{k: forged for k in genuine if "signature" in k}}
            try:
                verified = adapter.verify_webhook(headers=headers, body=body)
            except Exception as exc:
                raise AssertionError(
                    f"verify_webhook raised {type(exc).__name__} for {forged!r}"
                ) from exc
            assert verified is False, f"verify_webhook accepted the forged signature {forged!r}"

    def test_a_genuine_signature_verifies_and_only_over_these_bytes(self, adapter: Any) -> None:
        body = self.webhook_bodies(adapter)[0]
        headers = self.signed(adapter, body)
        assert adapter.verify_webhook(headers=headers, body=body) is True
        assert adapter.verify_webhook(headers=headers, body=body + b" ") is False

    def test_a_body_that_is_not_json_is_a_malformed_webhook_not_a_crash(self, adapter: Any) -> None:
        with pytest.raises(MalformedWebhook):
            adapter.parse_webhook(body=b"this is not json")

    def test_parse_webhook_never_invents_a_constant_event_id(self, adapter: Any) -> None:
        first, second = self.webhook_bodies(adapter)
        a = adapter.parse_webhook(body=first).provider_event_id
        b = adapter.parse_webhook(body=second).provider_event_id
        assert not (a and a == b), f"two different events parsed to the one event id {a!r}"

    def test_what_is_stored_still_identifies_the_event(self, adapter: Any) -> None:
        for body in self.webhook_bodies(adapter):
            event = adapter.parse_webhook(body=body)
            stored = adapter.webhook_facts(event).stored
            again = adapter.parse_webhook(body=json.dumps(stored).encode())
            assert derive_event_id(again) == derive_event_id(event) is not None

    # -- 7. probe ----------------------------------------------------------------------------

    def test_the_probe_passes_with_good_credentials_and_creates_nothing(self, adapter: Any) -> None:
        adapter.probe()
        assert self.create_count(adapter) == 0

    def test_the_probe_fails_with_rejected_credentials(self, adapter: Any) -> None:
        rejecting = self.rejecting(adapter)
        if rejecting is None:
            return
        try:
            rejecting.probe()
        except ProviderMisconfigured:
            return
        raise AssertionError("the probe passed with credentials the provider rejects")


def check(contract: Any, name: str, adapter: Any) -> None:
    """Run one named check of `contract` against `adapter` (for the broken-adapter negatives)."""
    getattr(contract, name)(adapter)
