"""The live Razorpay wire for tenant collections, against a scripted HTTP transport — the request
shapes Razorpay documents (checked 2026-09-29), the response mapping, and the promises that make
it safe: lookup before create, an idempotency header on every refund, basic auth with the
merchant's own keys, no customer field copied out, no response body in an error. No network.

And the boundary: the platform's own provider never gets a wire, whatever the collections
setting says — platform billing's live debit is B5g, not this.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from app import deps
from app.providers.base import CollectionsProvider, ProviderCallError
from app.providers.razorpay import RazorpayProvider
from app.providers.razorpay_wire import RazorpayApiError, RazorpayHttpWire

KEY_ID = "rzp_test_WireTest0001"
KEY_SECRET = "wire-key-secret-0001"  # pragma: allowlist secret


class Script:
    """A scripted Razorpay: answers by (method, path) and records every request."""

    def __init__(self, answers: dict[tuple[str, str], tuple[int, dict[str, Any]]]) -> None:
        self.answers = answers
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.answers[(request.method, request.url.path)]
        return httpx.Response(status, json=body)


def _provider(script: Script) -> RazorpayProvider:
    wire = RazorpayHttpWire(
        key_id=KEY_ID, key_secret=KEY_SECRET, transport=httpx.MockTransport(script)
    )
    return RazorpayProvider(
        key_id=KEY_ID, key_secret=KEY_SECRET, webhook_secret="hook-secret-0001", wire=wire
    )


def test_an_order_is_looked_up_by_exact_receipt_before_one_is_created() -> None:
    receipt = "11111111-2222-3333-4444-555555555555"
    script = Script(
        {
            # Razorpay's receipt filter "contains" the value: a longer receipt is not ours.
            ("GET", "/v1/orders"): (
                200,
                {"items": [{"id": "order_other", "receipt": receipt + "x"}]},
            ),
            ("POST", "/v1/orders"): (200, {"id": "order_new", "receipt": receipt}),
        }
    )
    order = _provider(script).ensure_order(
        receipt=receipt, amount_minor=50_000, currency="INR", notes={"charge_id": receipt}
    )
    assert order.provider_order_id == "order_new"
    lookup, create = script.requests
    assert lookup.method == "GET" and lookup.url.params["receipt"] == receipt
    assert create.method == "POST"
    assert json.loads(create.content) == {
        "amount": 50_000,
        "currency": "INR",
        "receipt": receipt,
        "notes": {"charge_id": receipt},
    }
    expected = "Basic " + base64.b64encode(f"{KEY_ID}:{KEY_SECRET}".encode()).decode()
    assert create.headers["Authorization"] == expected


def test_an_order_that_already_exists_is_never_created_again() -> None:
    receipt = "r-1"
    script = Script(
        {("GET", "/v1/orders"): (200, {"items": [{"id": "order_1", "receipt": receipt}]})}
    )
    order = _provider(script).ensure_order(
        receipt=receipt, amount_minor=1, currency="INR", notes={}
    )
    assert order.provider_order_id == "order_1"
    assert [r.method for r in script.requests] == ["GET"]


def test_order_payments_keep_ids_money_and_codes_and_drop_the_customer() -> None:
    script = Script(
        {
            ("GET", "/v1/orders/order_1/payments"): (
                200,
                {
                    "items": [
                        {
                            "id": "pay_1",
                            "status": "failed",
                            "amount": 500,
                            "currency": "inr",
                            "error_code": "BAD_REQUEST_ERROR",
                            "email": "customer@example.com",
                            "contact": "+919876543210",
                            "card": {"last4": "1111"},
                            "created_at": 10,
                        },
                        {"id": "pay_2", "status": "captured", "amount": 500, "currency": "INR"},
                    ]
                },
            )
        }
    )
    payments = _provider(script).order_payments(order_id="order_1")
    assert [(p.provider_payment_id, p.status) for p in payments] == [
        ("pay_1", "failed"),
        ("pay_2", "captured"),
    ]
    assert payments[0].currency == "INR" and payments[0].error_code == "BAD_REQUEST_ERROR"
    assert "customer@example.com" not in repr(payments)
    found = _provider(script).find_payment(order_id="order_1", charge_id="c")
    assert found is not None and found.provider_payment_id == "pay_2" and found.state == "captured"


def test_a_refund_carries_the_idempotency_header_and_maps_pending() -> None:
    script = Script(
        {
            ("POST", "/v1/payments/pay_1/refund"): (
                200,
                {"id": "rfnd_1", "status": "pending", "amount": 200, "currency": "INR"},
            )
        }
    )
    result = _provider(script).refund(
        idempotency_key="rfd-abcdef0123456789",
        provider_payment_id="pay_1",
        amount_minor=200,
        currency="INR",
    )
    assert result.provider_refund_id == "rfnd_1" and result.state == "created"
    (request,) = script.requests
    assert request.headers["X-Refund-Idempotency"] == "rfd-abcdef0123456789"
    assert json.loads(request.content) == {"amount": 200, "speed": "normal"}


def test_an_error_keeps_the_code_and_never_the_description() -> None:
    script = Script(
        {
            ("GET", "/v1/orders/order_1/payments"): (
                401,
                {"error": {"code": "BAD_REQUEST_ERROR", "description": "key rzp_test_x is wrong"}},
            )
        }
    )
    with pytest.raises(RazorpayApiError) as raised:
        _provider(script).order_payments(order_id="order_1")
    assert isinstance(raised.value, ProviderCallError)
    assert raised.value.status_code == 401 and raised.value.code == "BAD_REQUEST_ERROR"
    assert "rzp_test_x" not in str(raised.value)


def test_the_collections_wire_never_debits() -> None:
    provider = _provider(Script({}))
    with pytest.raises(NotImplementedError):
        provider.charge(
            idempotency_key="k", amount_minor=1, currency="INR", mandate_id=None, notes={}
        )
    with pytest.raises(NotImplementedError):
        provider.create_mandate(idempotency_key="k", max_amount_minor=1, currency="INR", notes={})


def test_neither_the_wire_nor_the_adapter_reprs_a_secret() -> None:
    provider = _provider(Script({}))
    assert KEY_SECRET not in repr(provider) and "hook-secret" not in repr(provider)
    assert KEY_SECRET not in repr(provider._wire)


def test_the_platform_wire_is_chosen_by_payment_wire_and_never_by_collections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two switches are independent (ADR-0032). Before Phase A this pinned that the platform
    provider never got a wire at all; now it gets one under `PAYMENT_WIRE=live` and **only**
    then: `COLLECTIONS_WIRE` turning a merchant's wire on says nothing about the platform's, and
    the platform's turning on says nothing about a merchant's."""

    class _Secret:
        def __init__(self, value: str) -> None:
            self._value = value

        def get_secret_value(self) -> str:
            return self._value

    class _Cfg:
        collections_wire = "razorpay"
        payment_provider = "razorpay"
        payment_wire = "stub"
        razorpay_key_id = "rzp_test_Platform0001"
        razorpay_key_secret = _Secret("platform-key-secret-0001")  # pragma: allowlist secret
        razorpay_webhook_secret = _Secret("platform-hook-secret")  # pragma: allowlist secret

        @staticmethod
        def collections_wire_for(provider: str) -> str:
            return "live" if provider == "razorpay" else "stub"

    cfg = _Cfg()
    monkeypatch.setattr(deps, "settings", lambda: cfg)
    deps.reset_providers()
    try:
        # Collections live, platform stub: the platform provider still has no wire.
        platform = deps.provider_for("razorpay")
        assert isinstance(platform, RazorpayProvider) and platform.is_live is False
        merchant = deps._collections_provider(KEY_ID, KEY_SECRET, "hook-secret-0001")
        assert isinstance(merchant, CollectionsProvider)
        assert isinstance(merchant, RazorpayProvider) and merchant.is_live is True

        # Platform live (and collections stub): the reverse.
        cfg.payment_wire = "live"
        cfg.collections_wire = "stub"
        cfg.collections_wire_for = staticmethod(lambda provider: "stub")  # type: ignore[method-assign]
        deps.reset_providers()
        live_platform = deps.provider_for("razorpay")
        assert isinstance(live_platform, RazorpayProvider) and live_platform.is_live is True
        quiet_merchant = deps._collections_provider(KEY_ID, KEY_SECRET, "hook-secret-0001")
        assert isinstance(quiet_merchant, RazorpayProvider) and quiet_merchant.is_live is False
    finally:
        deps.reset_providers()


def test_the_legacy_collections_wire_value_means_razorpay_live() -> None:
    """`COLLECTIONS_WIRE=razorpay` — the only non-stub value there has ever been — keeps meaning
    Razorpay live under the per-provider switch, so an environment that already says it cannot be
    silently flipped to the stub by this release (checkouts would then be not-openable with no
    error). A provider it does not name stays on the stub."""
    from app.config import Settings

    legacy = Settings(collections_wire="razorpay")
    assert legacy.collections_wire_for("razorpay") == "live"
    assert legacy.collections_wire_for("cashfree") == "stub"
    assert Settings(collections_wire="stub").collections_wire_for("razorpay") == "stub"


def test_collections_default_to_the_stub_wire() -> None:
    merchant = deps._collections_provider(KEY_ID, KEY_SECRET, "hook-secret-0001")
    assert isinstance(merchant, RazorpayProvider) and merchant.is_live is False


class TestTheCashfreeStubAccounts:
    """The in-process Cashfree a merchant's stub adapter runs on is a process-wide object: it is
    keyed so that two organizations never share one, and it is bounded."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(deps, "_CASHFREE_STUB_WIRES", type(deps._CASHFREE_STUB_WIRES)())

    def test_the_same_app_id_with_another_secret_is_another_account(self) -> None:
        mine = deps._cashfree_stub_wire("TESTsharedapp01", "dev-only-secret-a")
        again = deps._cashfree_stub_wire("TESTsharedapp01", "dev-only-secret-a")
        theirs = deps._cashfree_stub_wire("TESTsharedapp01", "dev-only-secret-b")
        assert mine is again and mine is not theirs
        assert all("dev-only-secret" not in str(key) for key in deps._CASHFREE_STUB_WIRES)

    def test_the_registry_and_each_request_log_are_bounded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(deps, "_STUB_WIRES_MAX", 3)
        first = deps._cashfree_stub_wire("TESTapp0", "dev-only-secret")
        for n in range(1, 5):
            deps._cashfree_stub_wire(f"TESTapp{n}", "dev-only-secret")
        assert len(deps._CASHFREE_STUB_WIRES) == 3
        assert deps._cashfree_stub_wire("TESTapp0", "dev-only-secret") is not first
        wire = deps._cashfree_stub_wire("TESTlog", "dev-only-secret")
        for _ in range(deps._STUB_LOG_MAX + 50):
            with pytest.raises(ProviderCallError):
                wire.request("GET", "/orders/none")
        assert len(wire.requests) == deps._STUB_LOG_MAX
