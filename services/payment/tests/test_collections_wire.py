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


def test_the_platform_provider_never_gets_a_wire_even_with_collections_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Cfg:
        collections_wire = "razorpay"
        razorpay_key_id = "rzp_test_Platform0001"
        razorpay_key_secret = None

        class razorpay_webhook_secret:
            @staticmethod
            def get_secret_value() -> str:
                return "platform-hook-secret"

    monkeypatch.setattr(deps, "settings", lambda: _Cfg())
    deps._build.cache_clear()
    try:
        platform = deps._build()
        assert isinstance(platform, RazorpayProvider) and platform.is_live is False
        merchant = deps._collections_provider(KEY_ID, KEY_SECRET, "hook-secret-0001")
        assert isinstance(merchant, CollectionsProvider)
        assert isinstance(merchant, RazorpayProvider) and merchant.is_live is True
    finally:
        deps._build.cache_clear()


def test_collections_default_to_the_stub_wire() -> None:
    merchant = deps._collections_provider(KEY_ID, KEY_SECRET, "hook-secret-0001")
    assert isinstance(merchant, RazorpayProvider) and merchant.is_live is False
