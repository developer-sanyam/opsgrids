"""`CollectionsContract` (ADR-0030) against every merchant adapter — the Razorpay one on its stub
and on a stateful fake Razorpay over HTTP, the Cashfree one on the in-process fake wire and on the
real `CashfreeHttpWire` over an `httpx.MockTransport` that answers as that same fake — and against
adapters broken in exactly the way each check exists to catch. No network, no real keys.
"""

from __future__ import annotations

import hmac
import json
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.providers import cashfree_codec as codec
from app.providers.base import (
    CollectionsProvider,
    OrderResult,
    PaymentSnapshot,
    RefundResult,
    WebhookEvent,
)
from app.providers.cashfree_collections import CashfreeCollectionsProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from app.providers.cashfree_wire import CashfreeHttpWire
from app.providers.razorpay_collections import RazorpayCollectionsProvider
from app.providers.razorpay_wire import RazorpayHttpWire
from app.providers.wire import ProviderApiError
from tests.collections_contract import CollectionsContract, check
from tests.collections_support import payment_webhook
from tests.razorpay_fake import KEY_ID, KEY_SECRET, FakeRazorpay

HOOK_SECRET = "dev-only-merchant-hook-secret"  # pragma: allowlist secret
CF_APP_ID = "TESTdevonlyappid01"  # pragma: allowlist secret
CF_SECRET = "dev-only-cashfree-merchant-secret"  # pragma: allowlist secret
TS = "1759740000000"


# --------------------------------------------------------------------------- Razorpay


def _razorpay_stub() -> RazorpayCollectionsProvider:
    return RazorpayCollectionsProvider(key_id=KEY_ID, key_secret=None, webhook_secret=HOOK_SECRET)


class _RazorpayBodies:
    def webhook_bodies(self, adapter: Any) -> tuple[bytes, bytes]:
        return (
            payment_webhook(event="payment.authorized", payment_id="pay_1", order_id="order_1"),
            payment_webhook(
                event="payment.captured",
                payment_id="pay_2",
                order_id="order_1",
                created_at=1_700_000_900,
            ),
        )

    def signed(self, adapter: Any, body: bytes) -> dict[str, str]:
        return {"x-razorpay-signature": adapter.sign(body)}


class TestTheRazorpayStub(_RazorpayBodies, CollectionsContract):
    @pytest.fixture
    def adapter(self) -> RazorpayCollectionsProvider:
        return _razorpay_stub()

    def fresh(self, adapter: Any) -> Any:
        again = _razorpay_stub()
        again._orders_by_receipt = adapter._orders_by_receipt
        again._payments_by_order = adapter._payments_by_order
        again._stub_order_payments = adapter._stub_order_payments
        return again

    def create_count(self, adapter: Any) -> int:
        return len(adapter._orders_by_receipt)

    def pay(self, adapter: Any, order_id: str) -> str:
        payment_id = f"pay_{uuid.uuid4().hex[:12]}"
        adapter.stub_record_payment(
            order_id,
            PaymentSnapshot(
                provider_payment_id=payment_id,
                status="captured",
                amount_minor=49_900,
                currency="INR",
            ),
        )
        return payment_id

    def refund_count(self, adapter: Any, order_id: str) -> int | None:
        return None

    def rejecting(self, adapter: Any) -> Any | None:
        return None  # the stub makes no request: there is nobody to reject anything


def _razorpay_live(fake: FakeRazorpay, *, secret: str = KEY_SECRET) -> RazorpayCollectionsProvider:
    wire = RazorpayHttpWire(key_id=KEY_ID, key_secret=secret, transport=fake.transport())
    adapter = RazorpayCollectionsProvider(
        key_id=KEY_ID, key_secret=secret, webhook_secret=HOOK_SECRET, wire=wire
    )
    adapter._contract_fake = fake  # type: ignore[attr-defined]
    return adapter


class TestTheLiveRazorpayWire(_RazorpayBodies, CollectionsContract):
    @pytest.fixture
    def adapter(self) -> RazorpayCollectionsProvider:
        return _razorpay_live(FakeRazorpay())

    @staticmethod
    def _fake(adapter: Any) -> FakeRazorpay:
        fake: FakeRazorpay = adapter._contract_fake
        return fake

    def fresh(self, adapter: Any) -> Any:
        return _razorpay_live(self._fake(adapter))

    def create_count(self, adapter: Any) -> int:
        return self._fake(adapter).methods().count("POST /v1/orders")

    def pay(self, adapter: Any, order_id: str) -> str:
        return self._fake(adapter).pay(order_id)

    def refund_count(self, adapter: Any, order_id: str) -> int | None:
        return len(self._fake(adapter).refunds)

    def rejecting(self, adapter: Any) -> Any | None:
        return _razorpay_live(self._fake(adapter), secret="dev-only-wrong-secret")


# --------------------------------------------------------------------------- Cashfree


def _cashfree_body(
    etype: str, status: str, *, cf_payment_id: int, order_id: str = "ord-1", event_time: str
) -> bytes:
    body = {
        "type": etype,
        "event_time": event_time,
        "data": {
            "order": {"order_id": order_id, "order_amount": 499.00, "order_currency": "INR"},
            "payment": {
                "cf_payment_id": cf_payment_id,
                "payment_status": status,
                "payment_amount": 499.00,
                "payment_currency": "INR",
                "payment_method": {"upi": {"upi_id": "asha@okbank"}},
            },
            "customer_details": {"customer_phone": "9876543210", "customer_email": "a@x.example"},
        },
    }
    return json.dumps(body).encode()


class _CashfreeHooks:
    def webhook_bodies(self, adapter: Any) -> tuple[bytes, bytes]:
        return (
            _cashfree_body(
                "PAYMENT_FAILED_WEBHOOK",
                "FAILED",
                cf_payment_id=1001,
                event_time="2026-10-06T10:00:00+05:30",
            ),
            _cashfree_body(
                "PAYMENT_SUCCESS_WEBHOOK",
                "SUCCESS",
                cf_payment_id=1002,
                event_time="2026-10-06T10:05:00+05:30",
            ),
        )

    def signed(self, adapter: Any, body: bytes) -> dict[str, str]:
        return {
            "x-webhook-signature": codec.sign(CF_SECRET, TS, body),
            "x-webhook-timestamp": TS,
        }

    @staticmethod
    def _fake(adapter: Any) -> CashfreeFakeWire:
        raise NotImplementedError

    def create_count(self, adapter: Any) -> int:
        return self._fake(adapter).requests.count(("POST", "/orders"))

    def pay(self, adapter: Any, order_id: str) -> str:
        return str(self._fake(adapter).pay(order_id))

    def refund_count(self, adapter: Any, order_id: str) -> int | None:
        return len(self._fake(adapter).refunds.get(order_id, []))


def _cashfree_stub(fake: CashfreeFakeWire) -> CashfreeCollectionsProvider:
    return CashfreeCollectionsProvider(wire=fake, secret_key=CF_SECRET, mode="stub")


class TestTheCashfreeFakeWire(_CashfreeHooks, CollectionsContract):
    @pytest.fixture
    def adapter(self) -> CashfreeCollectionsProvider:
        return _cashfree_stub(CashfreeFakeWire())

    @staticmethod
    def _fake(adapter: Any) -> CashfreeFakeWire:
        fake: CashfreeFakeWire = adapter._wire
        return fake

    def fresh(self, adapter: Any) -> Any:
        return _cashfree_stub(self._fake(adapter))

    def rejecting(self, adapter: Any) -> Any | None:
        refusing = CashfreeFakeWire()
        refusing.reject_credentials = True
        return _cashfree_stub(refusing)


class CashfreeOverHttp:
    """An `httpx.MockTransport` handler that is Cashfree's PG API: it checks the credentials and
    the pinned API version, hands the call to a `CashfreeFakeWire`, and answers its result — or
    its refusal — as Cashfree would, as JSON (a `code`, and a free-text `message` the wire must
    never keep)."""

    def __init__(self, *, app_id: str = CF_APP_ID, secret: str = CF_SECRET) -> None:
        self.fake = CashfreeFakeWire()
        self._credentials = (app_id, secret)
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        given = (request.headers.get("x-client-id"), request.headers.get("x-client-secret"))
        if given != self._credentials:
            return httpx.Response(
                401, json={"code": "authentication_error", "message": "auth failed"}
            )
        assert request.headers.get("x-api-version") == codec.API_VERSION
        path = request.url.path.removeprefix("/pg")
        try:
            result = self.fake.request(
                request.method,
                path,
                content=request.content or None,
                headers={k.lower(): v for k, v in request.headers.items()},
            )
        except ProviderApiError as exc:
            return httpx.Response(
                exc.status_code or 400, json={"code": exc.code, "message": "free text"}
            )
        return httpx.Response(200, content=codec.dumps(result))


def _cashfree_http(bridge: CashfreeOverHttp, *, secret: str = CF_SECRET) -> Any:
    wire = CashfreeHttpWire(
        app_id=CF_APP_ID, secret_key=secret, env="sandbox", transport=bridge.transport()
    )
    adapter = CashfreeCollectionsProvider(wire=wire, secret_key=secret, mode="sandbox")
    adapter._contract_bridge = bridge  # type: ignore[attr-defined]
    return adapter


class TestTheCashfreeHttpWire(_CashfreeHooks, CollectionsContract):
    @pytest.fixture
    def adapter(self) -> CashfreeCollectionsProvider:
        result: CashfreeCollectionsProvider = _cashfree_http(CashfreeOverHttp())
        return result

    @staticmethod
    def _fake(adapter: Any) -> CashfreeFakeWire:
        fake: CashfreeFakeWire = adapter._contract_bridge.fake
        return fake

    def fresh(self, adapter: Any) -> Any:
        return _cashfree_http(adapter._contract_bridge)

    def rejecting(self, adapter: Any) -> Any | None:
        return _cashfree_http(adapter._contract_bridge, secret="dev-only-wrong-secret")


# --------------------------------------------------------------------------- broken adapters


class _Broken(CashfreeCollectionsProvider):
    def __init__(self) -> None:
        super().__init__(wire=CashfreeFakeWire(), secret_key=CF_SECRET, mode="stub")


class _OrderPerCall(_Broken):
    """Never looks the Order up: a second call is a second Order."""

    def ensure_order(self, **kwargs: Any) -> OrderResult:
        kwargs["receipt"] = f"{kwargs['receipt'][:30]}{uuid.uuid4().hex[:8]}"
        return super().ensure_order(**kwargs)


class _FindThatCreates(_Broken):
    def find_order(self, *, receipt: str) -> OrderResult | None:
        from tests.collections_contract import ensure

        return ensure(self, receipt)


class _AnotherOrderInAFreshProcess(_Broken):
    """Remembers its Orders in process memory instead of asking the provider."""

    def __init__(self) -> None:
        super().__init__()
        self._mine: dict[str, OrderResult] = {}

    def ensure_order(self, **kwargs: Any) -> OrderResult:
        receipt = kwargs["receipt"]
        if receipt not in self._mine:
            kwargs["receipt"] = f"{receipt[:30]}{uuid.uuid4().hex[:8]}"
            self._mine[receipt] = super().ensure_order(**kwargs)
        return self._mine[receipt]


class _RefundPerCall(_Broken):
    def refund(self, **kwargs: Any) -> RefundResult:
        kwargs["idempotency_key"] = "rfd" + uuid.uuid4().hex[:36]
        return super().refund(**kwargs)


class _SlowWire(_Broken):
    request_timeouts_seconds = (60.0, 60.0)


class _NoTimeoutsDeclared(_Broken):
    request_timeouts_seconds = ()


class _AcceptsAnySignature(_Broken):
    def verify_webhook(
        self, *, body: bytes, signature: str | None = None, headers: Any = None
    ) -> bool:
        return bool(headers)


class _CrashesOnNonAsciiSignature(_Broken):
    def verify_webhook(
        self, *, body: bytes, signature: str | None = None, headers: Any = None
    ) -> bool:
        given = (headers or {}).get("x-webhook-signature")
        ts = (headers or {}).get("x-webhook-timestamp")
        if not given or not ts:
            return False
        return hmac.compare_digest(codec.sign(CF_SECRET, ts, body), given)


class _ConstantEventId(_Broken):
    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        return replace(super().parse_webhook(body=body), provider_event_id="evt_constant")


class _StoresWhatItCannotReadBack(_Broken):
    def webhook_facts(self, event: WebhookEvent) -> Any:
        return replace(super().webhook_facts(event), stored={"data": {}})


class _ReprShowsThePayer(_Broken):
    def ensure_order(self, **kwargs: Any) -> OrderResult:
        self._last = kwargs["payer"]
        return super().ensure_order(**kwargs)

    def __repr__(self) -> str:
        return f"_ReprShowsThePayer({self._last.phone})"


class _ProbeThatSwallows(CashfreeCollectionsProvider):
    def probe(self) -> None:
        try:
            super().probe()
        except Exception:
            return


class _BrokenContract(_CashfreeHooks, CollectionsContract):
    @staticmethod
    def _fake(adapter: Any) -> CashfreeFakeWire:
        fake: CashfreeFakeWire = adapter._wire
        return fake

    def fresh(self, adapter: Any) -> Any:
        again = type(adapter)()
        again._wire = adapter._wire
        again._orders._wire = adapter._wire
        return again

    def rejecting(self, adapter: Any) -> Any | None:
        refusing = CashfreeFakeWire()
        refusing.reject_credentials = True
        return _ProbeThatSwallows(wire=refusing, secret_key=CF_SECRET, mode="stub")


@pytest.mark.parametrize(
    ("adapter_cls", "name"),
    [
        (_OrderPerCall, "test_ensure_order_is_idempotent_by_receipt"),
        (_AnotherOrderInAFreshProcess, "test_a_fresh_process_resumes_the_order_it_did_not_make"),
        (_FindThatCreates, "test_find_order_never_creates"),
        (_RefundPerCall, "test_a_refund_is_idempotent_by_its_key"),
        (_SlowWire, "test_the_declared_request_timeouts_end_before_the_lease_does"),
        (_NoTimeoutsDeclared, "test_the_declared_request_timeouts_end_before_the_lease_does"),
        (_AcceptsAnySignature, "test_a_forged_signature_never_verifies_and_never_crashes"),
        (_CrashesOnNonAsciiSignature, "test_a_forged_signature_never_verifies_and_never_crashes"),
        (_ConstantEventId, "test_parse_webhook_never_invents_a_constant_event_id"),
        (_StoresWhatItCannotReadBack, "test_what_is_stored_still_identifies_the_event"),
        (_ReprShowsThePayer, "test_the_payer_never_appears_in_the_order_or_its_repr"),
        (_Broken, "test_the_probe_fails_with_rejected_credentials"),
    ],
    ids=lambda v: v if isinstance(v, str) else v.__name__,
)
def test_the_contract_fails_for_an_adapter_broken_the_way_it_guards_against(
    adapter_cls: type[_Broken], name: str
) -> None:
    with pytest.raises(AssertionError):
        check(_BrokenContract(), name, adapter_cls())


def test_every_adapter_here_is_a_collections_provider_and_none_is_a_payment_provider_stub() -> None:
    """The standalone seam: the Cashfree merchant adapter has no `charge`, `create_mandate` or
    `find_payment` to stub (ADR-0030)."""
    cashfree = _cashfree_stub(CashfreeFakeWire())
    assert isinstance(cashfree, CollectionsProvider)
    for absent in ("charge", "create_mandate", "find_payment", "debit"):
        assert not hasattr(cashfree, absent), absent
    assert isinstance(_razorpay_stub(), CollectionsProvider)


def test_the_http_bridge_is_strict_about_money() -> None:
    """The bridge answers what the fake answers: a float is never an amount on the wire."""
    bridge = CashfreeOverHttp()
    adapter = _cashfree_http(bridge)
    from tests.collections_contract import ensure

    order = ensure(adapter, str(uuid.uuid4()), amount_minor=1999)
    stored = bridge.fake.orders[order.provider_order_id]["order_amount"]
    assert stored == Decimal("19.99") and isinstance(stored, Decimal)
