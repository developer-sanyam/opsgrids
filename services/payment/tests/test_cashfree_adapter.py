"""The Cashfree adapter (ADR-0032, Phase A): the requests it makes against a scripted transport —
the shape, the headers, the exact JSON number for an amount — and how it reads Cashfree's
answers and webhooks. No network and no real keys: every credential here is a `dev-only-`
placeholder, and the stateful behaviour is `CashfreeFakeWire`'s.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import structlog

from app import domain
from app.providers import cashfree_codec as codec
from app.providers.base import (
    CheckoutCustomer,
    CheckoutProvider,
    CheckoutRequest,
    MalformedWebhook,
    PaymentProvider,
)
from app.providers.cashfree import CashfreeProvider
from app.providers.cashfree_fake import CashfreeFakeWire
from app.providers.cashfree_wire import CashfreeHttpWire
from app.providers.wire import ProviderMisconfigured, ProviderRejected, ProviderTransient
from tests.checkout_contract import checkout_request

APP_ID = "TESTdev-only-app-id"  # pragma: allowlist secret
SECRET = "dev-only-cashfree-secret"  # pragma: allowlist secret
PHONE = "9876543210"


def _provider(fake: CashfreeFakeWire | None = None) -> tuple[CashfreeProvider, CashfreeFakeWire]:
    fake = fake or CashfreeFakeWire()
    return CashfreeProvider(wire=fake, secret_key=SECRET, mode="stub"), fake


def _live(handler: Any) -> CashfreeProvider:
    wire = CashfreeHttpWire(
        app_id=APP_ID, secret_key=SECRET, env="sandbox", transport=httpx.MockTransport(handler)
    )
    return CashfreeProvider(wire=wire, secret_key=SECRET, mode="sandbox")


class Script:
    """A scripted Cashfree: answers by `(method, path)` and records every request."""

    def __init__(self, answers: dict[tuple[str, str], list[httpx.Response]]) -> None:
        self.answers = {k: list(v) for k, v in answers.items()}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.answers[(request.method, request.url.path)]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def _order(
    order_id: str, amount: str = "499.00", status: str = "ACTIVE", token: bool = True
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "cf_order_id": 7,
        "order_id": order_id,
        "order_amount": json.loads(amount),
        "order_currency": "INR",
        "order_status": status,
    }
    if token:
        body["payment_session_id"] = "session_dev_only_0001"
    return body


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode())


class TestOpeningACheckout:
    def test_a_missing_order_is_looked_up_then_created_with_the_exact_request(self) -> None:
        request = checkout_request(amount_minor=1999)
        script = Script(
            {
                ("GET", f"/pg/orders/{request.receipt}"): [
                    _json(404, {"code": "order_not_found", "message": "free text"})
                ],
                ("POST", "/pg/orders"): [_json(200, _order(request.receipt, "19.99"))],
            }
        )
        session = _live(script).ensure_checkout(request)

        lookup, create = script.requests
        assert (lookup.method, create.method) == ("GET", "POST")
        assert str(create.url).startswith("https://sandbox.cashfree.com/pg/orders")
        # Credentials in headers only, with the pinned API version.
        assert create.headers["x-client-id"] == APP_ID
        assert create.headers["x-client-secret"] == SECRET
        assert create.headers["x-api-version"] == codec.API_VERSION
        assert create.headers["content-type"] == "application/json"
        assert APP_ID.encode() not in create.content and SECRET.encode() not in create.content
        # The amount is a bare JSON number — 19.99, not "19.99" and not 19.989999999999998.
        assert b'"order_amount":19.99' in create.content
        sent = json.loads(create.content, parse_float=Decimal)
        assert sent["order_id"] == request.receipt and sent["order_currency"] == "INR"
        assert sent["customer_details"]["customer_phone"] == PHONE
        assert sent["customer_details"]["customer_id"] == request.customer.customer_id  # type: ignore[union-attr]
        assert sent["order_meta"]["return_url"] == request.return_url
        assert sent["order_tags"] == request.notes
        assert sent["order_expiry_time"] == request.expires_at.isoformat(timespec="seconds")
        # The idempotency key is derived from the receipt: a retry sends the same one.
        key = create.headers["x-idempotency-key"]
        assert str(uuid.UUID(key)) == key
        assert session.order.provider_order_id == request.receipt
        assert session.client_action == {
            "type": "cashfree_checkout",
            "payment_session_id": "session_dev_only_0001",
            "mode": "sandbox",
            "openable": True,
        }

    def test_the_idempotency_key_is_stable_per_receipt_and_differs_between_receipts(self) -> None:
        keys = []
        for receipt in ("r-1", "r-1", "r-2"):
            script = Script(
                {
                    ("GET", f"/pg/orders/{receipt}"): [_json(404, {"code": "order_not_found"})],
                    ("POST", "/pg/orders"): [_json(200, _order(receipt))],
                }
            )
            _live(script).ensure_checkout(checkout_request(receipt))
            keys.append(script.requests[-1].headers["x-idempotency-key"])
        assert keys[0] == keys[1] != keys[2]

    def test_an_order_that_already_exists_is_never_created_again(self) -> None:
        request = checkout_request()
        script = Script(
            {("GET", f"/pg/orders/{request.receipt}"): [_json(200, _order(request.receipt))]}
        )
        session = _live(script).ensure_checkout(request)
        assert [r.method for r in script.requests] == ["GET"]
        assert session.order.checkout_token == "session_dev_only_0001"

    def test_a_409_on_create_means_it_exists_so_it_is_fetched_and_used(self) -> None:
        request = checkout_request()
        script = Script(
            {
                ("GET", f"/pg/orders/{request.receipt}"): [
                    _json(404, {"code": "order_not_found"}),
                    _json(200, _order(request.receipt)),
                ],
                ("POST", "/pg/orders"): [_json(409, {"code": "order_already_exists"})],
            }
        )
        session = _live(script).ensure_checkout(request)
        assert [r.method for r in script.requests] == ["GET", "POST", "GET"]
        assert session.order.provider_order_id == request.receipt

    def test_a_409_whose_order_cannot_be_found_is_the_409(self) -> None:
        request = checkout_request()
        script = Script(
            {
                ("GET", f"/pg/orders/{request.receipt}"): [_json(404, {"code": "order_not_found"})],
                ("POST", "/pg/orders"): [_json(409, {"code": "order_already_exists"})],
            }
        )
        with pytest.raises(ProviderRejected) as raised:
            _live(script).ensure_checkout(request)
        assert raised.value.status_code == 409

    def test_an_existing_order_for_another_amount_is_refused_not_reused(self) -> None:
        request = checkout_request(amount_minor=49_900)
        script = Script(
            {
                ("GET", f"/pg/orders/{request.receipt}"): [
                    _json(200, _order(request.receipt, "10.00"))
                ]
            }
        )
        with pytest.raises(ProviderRejected) as raised:
            _live(script).ensure_checkout(request)
        assert raised.value.code == "order_amount_mismatch"
        assert [r.method for r in script.requests] == ["GET"]

    @pytest.mark.parametrize("status", ["EXPIRED", "TERMINATED"])
    def test_an_order_that_can_no_longer_be_paid_is_refused(self, status: str) -> None:
        request = checkout_request()
        script = Script(
            {
                ("GET", f"/pg/orders/{request.receipt}"): [
                    _json(200, _order(request.receipt, status=status))
                ]
            }
        )
        with pytest.raises(ProviderRejected) as raised:
            _live(script).ensure_checkout(request)
        assert raised.value.code == "order_not_payable"

    def test_an_order_with_no_session_token_is_refused_rather_than_opened_blind(self) -> None:
        request = checkout_request()
        script = Script(
            {
                ("GET", f"/pg/orders/{request.receipt}"): [
                    _json(200, _order(request.receipt, token=False))
                ]
            }
        )
        with pytest.raises(ProviderRejected) as raised:
            _live(script).ensure_checkout(request)
        assert raised.value.code == "no_checkout_session"

    @pytest.mark.parametrize("phone", [None, "", "12345", "98765432101", "abcdefghij", "+1"])
    def test_a_customer_without_a_usable_phone_is_refused_with_no_request(
        self, phone: str | None
    ) -> None:
        """Cashfree requires a phone on every Order. One is never invented."""
        request = CheckoutRequest(
            receipt=str(uuid.uuid4()),
            amount_minor=49_900,
            currency="INR",
            notes={},
            expires_at=datetime.now(UTC) + timedelta(days=1),
            customer=CheckoutCustomer(customer_id="c1", phone=phone),
        )
        script = Script({})
        with pytest.raises(ProviderRejected) as raised:
            _live(script).ensure_checkout(request)
        assert raised.value.code == "customer_phone_required" and script.requests == []

    def test_no_customer_at_all_is_refused_with_no_request(self) -> None:
        request = CheckoutRequest(
            receipt="r",
            amount_minor=49_900,
            currency="INR",
            notes={},
            expires_at=datetime.now(UTC) + timedelta(days=1),
            customer=None,
        )
        script = Script({})
        with pytest.raises(ProviderRejected):
            _live(script).ensure_checkout(request)
        assert script.requests == []

    def test_an_international_phone_with_a_plus_is_accepted(self) -> None:
        provider, _ = _provider()
        request = replace(
            checkout_request(),
            customer=CheckoutCustomer(customer_id="c1", phone="+14155550123"),
        )
        assert provider.ensure_checkout(request).order.provider_order_id == request.receipt

    def test_the_fake_and_the_real_wire_produce_the_same_session_shape(self) -> None:
        provider, _ = _provider()
        session = provider.ensure_checkout(checkout_request())
        assert set(session.client_action) == {"type", "payment_session_id", "mode", "openable"}
        assert session.client_action["openable"] is False  # the stub wire cannot be paid for real

    def test_an_order_without_a_customer_cannot_be_created_through_ensure_order(self) -> None:
        provider, _ = _provider()
        with pytest.raises(ProviderRejected) as raised:
            provider.ensure_order(receipt="x-1", amount_minor=49_900, currency="INR", notes={})
        assert raised.value.code == "customer_details_required"

    def test_ensure_order_returns_an_existing_one(self) -> None:
        provider, _ = _provider()
        made = provider.ensure_checkout(checkout_request("known-order"))
        found = provider.ensure_order(
            receipt="known-order", amount_minor=49_900, currency="INR", notes={}
        )
        assert found.provider_order_id == made.order.provider_order_id


class TestErrorsAndSecrets:
    def test_a_rejected_credential_is_misconfigured_and_keeps_no_text(self) -> None:
        request = checkout_request()
        script = Script(
            {
                ("GET", f"/pg/orders/{request.receipt}"): [
                    _json(
                        401,
                        {"code": "authentication_error", "message": f"bad key {SECRET} {APP_ID}"},
                    )
                ]
            }
        )
        with pytest.raises(ProviderMisconfigured) as raised:
            _live(script).ensure_checkout(request)
        text = str(raised.value) + repr(raised.value)
        assert SECRET not in text and APP_ID not in text and "bad key" not in text
        assert raised.value.code == "authentication_error"

    @pytest.mark.parametrize(
        ("status", "kind"),
        [(429, ProviderTransient), (500, ProviderTransient), (400, ProviderRejected)],
    )
    def test_other_statuses_classify_by_class(self, status: int, kind: type[Exception]) -> None:
        request = checkout_request()
        script = Script({("GET", f"/pg/orders/{request.receipt}"): [_json(status, {"code": "x"})]})
        with pytest.raises(kind):
            _live(script).ensure_checkout(request)

    def test_nothing_secret_is_in_a_repr(self) -> None:
        provider, fake = _provider()
        live = _live(Script({}))
        for shown in (repr(provider), repr(live), repr(live._wire), repr(fake)):
            assert SECRET not in shown and APP_ID not in shown

    def test_money_is_refused_before_any_request(self) -> None:
        script = Script({})
        provider = _live(script)
        with pytest.raises(ProviderRejected) as raised:
            provider.ensure_checkout(checkout_request(currency="USD"))
        assert raised.value.code == "unsupported_currency"
        with pytest.raises(ProviderRejected) as raised:
            provider.ensure_checkout(checkout_request(amount_minor=99))
        assert raised.value.code == "amount_below_minimum"
        assert script.requests == []

    def test_the_fake_rejects_what_cashfree_would(self) -> None:
        provider, fake = _provider()
        fake.reject_credentials = True
        with pytest.raises(ProviderMisconfigured):
            provider.ensure_checkout(checkout_request())


class TestOrderPayments:
    def test_attempts_map_into_the_neutral_vocabulary_and_abandoned_ones_vanish(self) -> None:
        provider, fake = _provider()
        session = provider.ensure_checkout(checkout_request())
        order = session.order.provider_order_id
        fake.pay(order, status="USER_DROPPED")
        fake.pay(order, status="FAILED", error_code="card_declined")
        fake.pay(order, status="PENDING")
        fake.pay(order, status="SUCCESS")
        statuses = [p.status for p in provider.order_payments(order_id=order)]
        assert statuses == ["failed", "created", "captured"]

    def test_a_surcharged_payment_is_reported_at_the_orders_amount(self) -> None:
        provider, fake = _provider()
        order = provider.ensure_checkout(
            checkout_request(amount_minor=49_900)
        ).order.provider_order_id
        fake.pay(order, amount=Decimal("509.00"))
        (snapshot,) = provider.order_payments(order_id=order)
        assert snapshot.amount_minor == 49_900

    def test_a_short_payment_is_reported_short(self) -> None:
        provider, fake = _provider()
        order = provider.ensure_checkout(
            checkout_request(amount_minor=49_900)
        ).order.provider_order_id
        fake.pay(order, amount=Decimal("400.00"))
        (snapshot,) = provider.order_payments(order_id=order)
        assert snapshot.amount_minor == 40_000

    def test_an_unknown_order_is_a_rejection_not_an_empty_answer(self) -> None:
        provider, _ = _provider()
        with pytest.raises(ProviderRejected) as raised:
            provider.order_payments(order_id="nope")
        assert raised.value.status_code == 404

    def test_find_payment_is_the_best_attempt(self) -> None:
        provider, fake = _provider()
        order = provider.ensure_checkout(checkout_request()).order.provider_order_id
        fake.pay(order, status="FAILED")
        fake.pay(order, status="SUCCESS")
        found = provider.find_payment(order_id=order, charge_id="c")
        assert found is not None and found.state == "captured" and found.provider_order_id == order


class TestWebhooks:
    def _body(
        self,
        *,
        etype: str = "PAYMENT_SUCCESS_WEBHOOK",
        status: str = "SUCCESS",
        order_amount: Any = 499.00,
        payment_amount: Any = 499.00,
        org: str | None = "11111111-1111-1111-1111-111111111111",
        charge: str | None = "22222222-2222-2222-2222-222222222222",
    ) -> bytes:
        tags = {k: v for k, v in (("organization_id", org), ("charge_id", charge)) if v}
        order: dict[str, Any] = {"order_id": "ord-1", "order_currency": "INR", "order_tags": tags}
        if order_amount is not None:
            order["order_amount"] = order_amount
        payment: dict[str, Any] = {
            "cf_payment_id": 3001,
            "payment_status": status,
            "payment_currency": "INR",
            "payment_method": {"upi": {"upi_id": "asha@okbank"}},
        }
        if payment_amount is not None:
            payment["payment_amount"] = payment_amount
        body = {
            "type": etype,
            "event_time": "2026-10-06T10:15:15+05:30",
            "data": {
                "order": order,
                "payment": payment,
                "customer_details": {"customer_phone": PHONE, "customer_email": "a@example.com"},
            },
        }
        return json.dumps(body).encode()

    def test_a_signed_success_is_captured_with_the_payment_and_the_tenant(self) -> None:
        provider, _ = _provider()
        body = self._body()
        ts = "1697191215000"
        assert provider.verify_webhook(signature=provider.sign(body, ts), body=body, timestamp=ts)
        event = provider.parse_webhook(body=body)
        assert event.outcome_state == "captured" and event.event_name == "PAYMENT_SUCCESS"
        assert event.provider_payment_id == "3001" and event.provider_order_id == "ord-1"
        assert event.organization_id == "11111111-1111-1111-1111-111111111111"
        assert event.charge_id == "22222222-2222-2222-2222-222222222222"
        assert (event.amount_minor, event.currency) == (49_900, "INR")
        assert event.created_at == int(datetime(2026, 10, 6, 4, 45, 15, tzinfo=UTC).timestamp())
        assert event.provider_event_id == ""  # the id is a header; nothing is invented
        assert event.source == "webhook"

    def test_the_stored_body_is_redacted_json_safe_and_still_parseable(self) -> None:
        provider, _ = _provider()
        event = provider.parse_webhook(body=self._body())
        text = json.dumps(event.raw)  # no Decimal: JSONB-safe
        for pii in (PHONE, "a@example.com", "asha@okbank", "upi"):
            assert pii not in text
        again = provider.parse_webhook(body=text.encode())
        assert (again.outcome_state, again.provider_payment_id, again.amount_minor) == (
            "captured",
            "3001",
            49_900,
        )
        assert domain.derive_event_id(again) == domain.derive_event_id(event)

    def test_a_failed_attempt_asserts_no_outcome_and_carries_no_payment_id(self) -> None:
        """So an abandoned attempt can never be mistaken for the payment that later succeeds."""
        provider, _ = _provider()
        for etype, status in (
            ("PAYMENT_FAILED_WEBHOOK", "FAILED"),
            ("PAYMENT_USER_DROPPED_WEBHOOK", "USER_DROPPED"),
        ):
            event = provider.parse_webhook(body=self._body(etype=etype, status=status))
            assert event.outcome_state == "unknown" and event.provider_payment_id is None
            assert event.entity_id == "3001"  # still identifiable, for the derived event id

    def test_a_success_event_whose_payment_status_is_not_success_asserts_nothing(self) -> None:
        provider, _ = _provider()
        event = provider.parse_webhook(body=self._body(status="PENDING"))
        assert event.outcome_state == "unknown"

    @pytest.mark.parametrize("payment_amount", [None, "abc", 0.105, True])
    def test_a_capture_whose_amount_cannot_be_read_is_no_outcome_and_logs_an_error(
        self, payment_amount: Any
    ) -> None:
        provider, _ = _provider()
        with structlog.testing.capture_logs() as logs:
            event = provider.parse_webhook(body=self._body(payment_amount=payment_amount))
        assert event.outcome_state == "unknown" and event.provider_payment_id is None
        assert any(
            line["event"] == "cashfree.webhook.capture_amount_unreadable"
            and line["log_level"] == "error"
            for line in logs
        )

    def test_the_surcharge_rule_on_a_webhook(self) -> None:
        provider, _ = _provider()
        assert provider.parse_webhook(body=self._body(payment_amount=509.00)).amount_minor == 49_900
        assert provider.parse_webhook(body=self._body(payment_amount=400.00)).amount_minor == 40_000
        assert provider.parse_webhook(body=self._body(order_amount=None)).amount_minor == 49_900

    def test_a_body_with_no_tags_names_no_tenant(self) -> None:
        provider, _ = _provider()
        event = provider.parse_webhook(body=self._body(org=None, charge=None))
        assert event.organization_id is None and event.charge_id is None

    def test_malformed_bodies(self) -> None:
        provider, _ = _provider()
        for bad in (b"not json", b"[1, 2]", b'"str"', b"null"):
            with pytest.raises(MalformedWebhook):
                provider.parse_webhook(body=bad)

    def test_verification_fails_closed_without_a_secret_or_a_timestamp(self) -> None:
        body = self._body()
        unconfigured = CashfreeProvider(wire=CashfreeFakeWire(), secret_key=None, mode="stub")
        provider, _ = _provider()
        good = provider.sign(body, "1")
        assert unconfigured.verify_webhook(signature=good, body=body, timestamp="1") is False
        assert provider.verify_webhook(signature=good, body=body, timestamp=None) is False
        assert provider.verify_webhook(signature=good, body=body) is False
        assert provider.verify_webhook(signature=good, body=body, timestamp="2") is False
        assert provider.verify_webhook(signature=good, body=body + b" ", timestamp="1") is False
        with pytest.raises(RuntimeError):
            unconfigured.sign(body, "1")


class TestWhatIsNotBuilt:
    def test_debits_mandates_and_refunds_say_so(self) -> None:
        provider, _ = _provider()
        with pytest.raises(NotImplementedError, match="Phase B"):
            provider.charge(
                idempotency_key="k", amount_minor=100, currency="INR", mandate_id=None, notes={}
            )
        with pytest.raises(NotImplementedError):
            provider.create_mandate(
                idempotency_key="k", max_amount_minor=1, currency="INR", notes={}
            )
        with pytest.raises(NotImplementedError):
            provider.refund(
                idempotency_key="k", provider_payment_id="p", amount_minor=100, currency="INR"
            )
        with pytest.raises(NotImplementedError):
            provider.debit(
                idempotency_key="k",
                charge_ref="c",
                mandate_id=None,
                amount_minor=1,
                currency="INR",
                notes={},
            )

    def test_it_is_a_payment_provider_and_a_checkout_provider(self) -> None:
        provider, _ = _provider()
        assert isinstance(provider, PaymentProvider) and isinstance(provider, CheckoutProvider)
        capabilities = provider.capabilities
        assert (
            capabilities.currencies == frozenset({"INR"}) and capabilities.min_amount_minor == 100
        )
        assert capabilities.signs_headers and capabilities.requires_customer_details
        assert capabilities.wire == "stub" and _live(Script({})).capabilities.wire == "live"
