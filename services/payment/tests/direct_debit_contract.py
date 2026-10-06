"""`DirectDebitContract` — what an adapter for a provider with **no Order step** must do, as
executable tests (ADR-0032). The sibling of `ProviderContract` for `debit_shape = "direct"`.

Cashfree debits a saved mandate with `POST /subscriptions/pay`, keyed by a merchant-chosen
`payment_id`: there is no Order to create and no payment to "find against" one. The Order-then-
charge contract (`ProviderContract`, byte-for-byte unchanged and still guarding Razorpay) cannot
describe that, and bending it would either fake an Order per debit at the provider or weaken the
contract for Razorpay. So the shape gets its own, with the **same invariants** reworded:

1. `find_debit` finds the debit made for a charge and **never creates**.
2. **A debit is created at most once per charge** — however many times the service's call is made,
   by however many processes (a fresh adapter over the same provider account included).
3. Two charges never share a debit.
4. The per-request timeouts an adapter declares, summed, fit inside
   `Settings.provider_call_timeout_seconds`.
5. A forged signature — any bytes at all — is `False`, never accepted and never a crash; a body
   that is not JSON is a `MalformedWebhook`; `parse_webhook` never invents a constant event id.

**No live adapter fulfils this in Phase A** (mandates and debits are Phase B). It exists now so the
first one is held to a contract written before it, not to whatever its author's tests happen to say
— and `services.call_provider_charge` already branches on the shape. `test_direct_debit_contract.py`
runs it against a reference adapter and against seven broken ones.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import uuid
from collections import Counter
from typing import Any, cast

import pytest

from app import services
from app.config import settings
from app.models import Charge
from app.providers.base import (
    ChargeResult,
    DirectDebitProvider,
    MalformedWebhook,
    PaymentProvider,
    ProviderCapabilities,
    WebhookEvent,
    capabilities_of,
)


class ReferenceDirectDebit:
    """The simplest adapter that satisfies the contract: an in-memory provider account whose debits
    are keyed by the merchant's own `charge_ref`. `backing` is that account — a second adapter built
    over the same dict is a fresh process over the same account."""

    name = "reference"
    request_timeouts_seconds: tuple[float, ...] = (7.0, 7.0)
    capabilities = ProviderCapabilities(
        debit_shape="direct", requires_mandate_for_debit=True, supports_mandate_debit=True
    )

    def __init__(
        self,
        backing: dict[str, ChargeResult] | None = None,
        secret: str = "dev-only-ref-secret",  # noqa: S107  # pragma: allowlist secret
    ) -> None:
        self.debits: dict[str, ChargeResult] = backing if backing is not None else {}
        self._secret = secret
        self.debit_calls = 0

    def find_debit(self, *, charge_ref: str) -> ChargeResult | None:
        return self.debits.get(charge_ref)

    def debit(
        self,
        *,
        idempotency_key: str,
        charge_ref: str,
        mandate_id: str | None,
        amount_minor: int,
        currency: str,
        notes: dict[str, str],
    ) -> ChargeResult:
        self.debit_calls += 1
        result = ChargeResult(
            provider=self.name,
            provider_payment_id=f"ref_{hashlib.sha256(charge_ref.encode()).hexdigest()[:12]}",
            state="authorized",
            amount_minor=amount_minor,
            currency=currency,
        )
        self.debits[charge_ref] = result
        return result

    def sign(self, body: bytes) -> str:
        return hmac.new(self._secret.encode(), body, hashlib.sha256).hexdigest()

    def verify_webhook(self, *, signature: str | None, body: bytes) -> bool:
        if not signature or not signature.isascii():
            return False
        return hmac.compare_digest(self.sign(body), signature)

    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        try:
            parsed = json.loads(body)
        except ValueError:
            raise MalformedWebhook("not json") from None
        if not isinstance(parsed, dict):
            raise MalformedWebhook("not an object")
        return WebhookEvent(
            provider=self.name,
            provider_event_id="",
            event_name=str(parsed.get("event", "")),
            outcome_state="unknown",
            organization_id=None,
            charge_id=None,
            provider_payment_id=None,
            entity_id=str(parsed.get("id", "")) or None,
            created_at=parsed.get("at") if isinstance(parsed.get("at"), int) else None,
        )


class CountingDebitAdapter:
    """Wraps an adapter and counts every call by method name, delegating everything."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: Counter[str] = Counter()

    def as_provider(self) -> PaymentProvider:
        return cast(PaymentProvider, self)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            return attr(*args, **kwargs)

        return counted


def new_debit_charge(adapter: Any, *, amount_minor: int = 49_900) -> Charge:
    """An unsaved `Charge` — everything `services.call_provider_charge` reads, no session."""
    return Charge(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        provider=adapter.name,
        amount_minor=amount_minor,
        currency="INR",
        idempotency_key=f"invoice:{uuid.uuid4()}",
        state="created",
        mandate_id=uuid.uuid4(),
    )


class DirectDebitContract:
    """Subclass and supply the `adapter` fixture and the `fresh` / `webhook_bodies` hooks."""

    @pytest.fixture
    def adapter(self) -> Any:
        raise NotImplementedError("a subclass supplies the adapter under test")

    def fresh(self, adapter: Any) -> Any:
        """A second adapter over the same provider account — a process with no memory."""
        raise NotImplementedError

    def webhook_bodies(self, adapter: Any) -> tuple[bytes, bytes]:
        raise NotImplementedError

    # -- the shape ---------------------------------------------------------------------------

    def test_it_declares_the_direct_shape(self, adapter: Any) -> None:
        assert isinstance(adapter, DirectDebitProvider)
        assert capabilities_of(adapter).debit_shape == "direct"
        assert isinstance(adapter.name, str) and adapter.name

    # -- 1. find never creates ---------------------------------------------------------------

    def test_find_debit_never_creates(self, adapter: Any) -> None:
        watched = CountingDebitAdapter(adapter)
        ref = str(uuid.uuid4())
        assert watched.find_debit(charge_ref=ref) is None
        assert watched.find_debit(charge_ref=ref) is None
        assert watched.calls["debit"] == 0
        assert adapter.find_debit(charge_ref=ref) is None, "the lookup created a debit"

    def test_the_lookup_only_call_finds_what_a_create_made_and_never_makes_anything(
        self, adapter: Any
    ) -> None:
        charge = new_debit_charge(adapter)
        watched = CountingDebitAdapter(adapter)
        assert services.lookup_provider_charge(watched.as_provider(), charge) is None
        assert watched.calls["debit"] == 0
        made = services.call_provider_charge(adapter, charge)
        found = services.lookup_provider_charge(adapter, charge)
        assert found is not None and found.provider_payment_id == made.provider_payment_id

    # -- 2. created at most once -------------------------------------------------------------

    def test_a_debit_is_created_at_most_once_per_charge(self, adapter: Any) -> None:
        """The property the whole D3 design exists for, for the shape with no Order. The service's
        call is made twice by one process and once by a **fresh** one over the same account (the
        sweep resuming after a crash between the call and its record). One debit is created and
        every call returns it."""
        charge = new_debit_charge(adapter)
        process_one = CountingDebitAdapter(adapter)
        first = services.call_provider_charge(process_one.as_provider(), charge)
        again = services.call_provider_charge(process_one.as_provider(), charge)
        process_two = CountingDebitAdapter(self.fresh(adapter))
        resumed = services.call_provider_charge(process_two.as_provider(), charge)
        assert process_one.calls["debit"] == 1, "a second call created a second debit"
        assert process_two.calls["debit"] == 0, "a resumed call created a debit it should find"
        assert first.provider_payment_id == again.provider_payment_id == resumed.provider_payment_id

    # -- 3. two charges --------------------------------------------------------------------

    def test_two_charges_never_share_a_debit(self, adapter: Any) -> None:
        a, b = new_debit_charge(adapter), new_debit_charge(adapter)
        first = services.call_provider_charge(adapter, a)
        second = services.call_provider_charge(adapter, b)
        assert first.provider_payment_id != second.provider_payment_id

    # -- 4. timeouts -----------------------------------------------------------------------

    def test_the_declared_request_timeouts_fit_inside_the_call_budget(self, adapter: Any) -> None:
        timeouts = adapter.request_timeouts_seconds
        assert timeouts, "an adapter must declare the timeout of every request a debit makes"
        assert all(math.isfinite(t) and t > 0 for t in timeouts)
        budget = settings().provider_call_timeout_seconds
        assert sum(timeouts) <= budget, f"{sum(timeouts):g}s of timeouts, {budget:g}s budget"

    # -- 5. webhooks -----------------------------------------------------------------------

    def test_a_body_that_is_not_json_is_a_malformed_webhook_not_a_crash(self, adapter: Any) -> None:
        with pytest.raises(MalformedWebhook):
            adapter.parse_webhook(body=b"this is not json")

    def test_an_unsigned_webhook_does_not_verify(self, adapter: Any) -> None:
        assert adapter.verify_webhook(signature=None, body=b"{}") is False
        assert adapter.verify_webhook(signature="", body=b"{}") is False

    def test_a_forged_signature_never_verifies_and_never_crashes(self, adapter: Any) -> None:
        for forged in ("0" * 64, "zz", "\xff" * 64, "é" * 32, "☃" * 8, " " * 64):
            try:
                verified = adapter.verify_webhook(signature=forged, body=b"{}")
            except Exception as exc:
                raise AssertionError(
                    f"verify_webhook raised {type(exc).__name__} for the signature {forged!r}"
                ) from exc
            assert verified is False, f"verify_webhook accepted the forged signature {forged!r}"

    def test_parse_webhook_never_invents_a_constant_event_id(self, adapter: Any) -> None:
        first, second = self.webhook_bodies(adapter)
        a = adapter.parse_webhook(body=first).provider_event_id
        b = adapter.parse_webhook(body=second).provider_event_id
        assert not (a and a == b), f"two different events parsed to the one event id {a!r}"
