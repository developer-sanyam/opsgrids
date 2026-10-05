"""The Cashfree merchant adapter (ADR-0030), offline: what it sends, how it reads Cashfree's
answers and webhooks, and what it refuses — against scripted `httpx.MockTransport` answers and the
stateful fake. Every credential is a `dev-only-` placeholder; no request leaves the machine.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.collections import domain
from app.domain import derive_event_id
from app.providers import cashfree_codec as codec
from app.providers.base import CheckoutCustomer
from app.providers.cashfree_collections import (
    CashfreeCollectionsProvider,
    idempotency_key_for_refund,
    refund_key,
)
from app.providers.cashfree_fake import CashfreeFakeWire
from app.providers.cashfree_wire import CashfreeHttpWire
from app.providers.wire import ProviderMisconfigured, ProviderRejected

APP_ID = "TESTdevonlyapp0001"  # pragma: allowlist secret
SECRET = "dev-only-cashfree-merchant-secret"  # pragma: allowlist secret
PHONE = "9876543210"
SERVICE = Path(__file__).resolve().parents[1]


class Script:
    """A scripted Cashfree: answers by `(method, path)` and records every request."""

    def __init__(self, answers: dict[tuple[str, str], list[httpx.Response]]) -> None:
        self.answers = {k: list(v) for k, v in answers.items()}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.answers[(request.method, request.url.path)]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode())


def _live(handler: Any, *, mode: str = "sandbox") -> CashfreeCollectionsProvider:
    wire = CashfreeHttpWire(
        app_id=APP_ID,
        secret_key=SECRET,
        env=mode,  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
    )
    return CashfreeCollectionsProvider(wire=wire, secret_key=SECRET, mode=mode)  # type: ignore[arg-type]


def _stub() -> tuple[CashfreeCollectionsProvider, CashfreeFakeWire]:
    fake = CashfreeFakeWire()
    return CashfreeCollectionsProvider(wire=fake, secret_key=SECRET, mode="stub"), fake


def _payer() -> CheckoutCustomer:
    return CheckoutCustomer(customer_id="c" + uuid.uuid4().hex, name="Asha", phone=PHONE)


def _order_body(order_id: str, amount: str = "499.00") -> dict[str, Any]:
    return {
        "cf_order_id": 9,
        "order_id": order_id,
        "order_amount": json.loads(amount),
        "order_currency": "INR",
        "order_status": "ACTIVE",
        "payment_session_id": "session_dev_only_0001",
    }


class TestTheOrder:
    def test_a_missing_order_is_created_with_the_payer_the_expiry_and_the_notify_url(
        self,
    ) -> None:
        receipt = str(uuid.uuid4())
        script = Script(
            {
                ("GET", f"/pg/orders/{receipt}"): [_json(404, {"code": "order_not_found"})],
                ("POST", "/pg/orders"): [_json(200, _order_body(receipt, "19.99"))],
            }
        )
        expires = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
        payer = CheckoutCustomer(customer_id="c" + "a" * 32, phone=PHONE)
        notify = "https://app.opsgrids.example/api/v1/webhooks/collections?account=abc"
        order = _live(script).ensure_order(
            receipt=receipt,
            amount_minor=1999,
            currency="INR",
            notes={"charge_id": receipt},
            payer=payer,
            expires_at=expires,
            notify_url=notify,
        )
        assert order.provider_order_id == receipt
        assert order.checkout_token == "session_dev_only_0001"
        assert "session_dev_only_0001" not in repr(order)  # never in a traceback
        _, create = script.requests
        assert b'"order_amount":19.99' in create.content  # a bare number, exact
        sent = json.loads(create.content, parse_float=Decimal)
        assert sent["customer_details"] == {
            "customer_id": payer.customer_id,
            "customer_phone": PHONE,
        }
        assert sent["order_meta"] == {"notify_url": notify}
        assert sent["order_expiry_time"] == "2026-10-07T12:00:00+00:00"
        assert create.headers["x-client-id"] == APP_ID
        assert str(create.url).startswith("https://sandbox.cashfree.com/pg/")

    def test_production_talks_to_production(self) -> None:
        receipt = str(uuid.uuid4())
        script = Script({("GET", f"/pg/orders/{receipt}"): [_json(200, _order_body(receipt))]})
        _live(script, mode="production").ensure_order(
            receipt=receipt,
            amount_minor=49_900,
            currency="INR",
            notes={},
            payer=_payer(),
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        assert str(script.requests[0].url).startswith("https://api.cashfree.com/pg/")

    @pytest.mark.parametrize("phone", [None, "", "12345", "abcdefghij"])
    def test_no_usable_phone_is_refused_before_any_request(self, phone: str | None) -> None:
        script = Script({})
        with pytest.raises(ProviderRejected) as raised:
            _live(script).ensure_order(
                receipt="r-1",
                amount_minor=49_900,
                currency="INR",
                notes={},
                payer=CheckoutCustomer(customer_id="c1", phone=phone),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        assert raised.value.code == "customer_phone_required" and script.requests == []

    def test_an_order_found_for_another_amount_is_refused_not_reused(self) -> None:
        receipt = str(uuid.uuid4())
        script = Script(
            {("GET", f"/pg/orders/{receipt}"): [_json(200, _order_body(receipt, "10.00"))]}
        )
        with pytest.raises(ProviderRejected) as raised:
            _live(script).ensure_order(
                receipt=receipt,
                amount_minor=49_900,
                currency="INR",
                notes={},
                payer=_payer(),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        assert raised.value.code == "order_amount_mismatch"

    def test_a_409_on_create_is_looked_up_and_used(self) -> None:
        receipt = str(uuid.uuid4())
        script = Script(
            {
                ("GET", f"/pg/orders/{receipt}"): [
                    _json(404, {"code": "order_not_found"}),
                    _json(200, _order_body(receipt)),
                ],
                ("POST", "/pg/orders"): [_json(409, {"code": "order_already_exists"})],
            }
        )
        order = _live(script).ensure_order(
            receipt=receipt,
            amount_minor=49_900,
            currency="INR",
            notes={},
            payer=_payer(),
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        assert order.provider_order_id == receipt
        assert [r.method for r in script.requests] == ["GET", "POST", "GET"]

    def test_an_expiry_is_required(self) -> None:
        provider, fake = _stub()
        with pytest.raises(ProviderRejected) as raised:
            provider.ensure_order(
                receipt="r-2", amount_minor=49_900, currency="INR", notes={}, payer=_payer()
            )
        assert raised.value.code == "expiry_required" and fake.requests == []


class TestStatuses:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("SUCCESS", ["captured"]),
            ("FAILED", ["failed"]),
            ("PENDING", ["created"]),
            ("USER_DROPPED", []),
            ("NOT_ATTEMPTED", []),
            ("VOID", []),
            ("CANCELLED", []),
        ],
    )
    def test_each_payment_status_maps_and_an_abandoned_attempt_is_no_observation(
        self, status: str, expected: list[str]
    ) -> None:
        provider, fake = _stub()
        order = provider.ensure_order(
            receipt=str(uuid.uuid4()),
            amount_minor=49_900,
            currency="INR",
            notes={},
            payer=_payer(),
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        fake.pay(order.provider_order_id, status=status)
        assert [p.status for p in provider.order_payments(order_id=order.provider_order_id)] == (
            expected
        )

    def test_user_dropped_never_becomes_a_failed_collection(self) -> None:
        """Through the collection state machine too: no observation, so nothing moves."""
        from app.collections.domain import observation_from_snapshot
        from app.providers.base import PaymentSnapshot

        assert codec.PAYMENT_STATUS_STATES.get("USER_DROPPED") is None
        pending = PaymentSnapshot(
            provider_payment_id="1", status="created", amount_minor=1, currency="INR"
        )
        assert observation_from_snapshot(pending, "o") is None

    @pytest.mark.parametrize(
        ("status", "state"),
        [
            ("SUCCESS", "processed"),
            ("PENDING", "created"),
            ("PENDING_APPROVAL", "created"),
            ("ONHOLD", "created"),
            ("INITIATED", "created"),
            ("CANCELLED", "failed"),
            ("REJECTED", "failed"),
            ("FAILED", "failed"),
            ("SOMETHING_NEW", "created"),
            (None, "created"),
        ],
    )
    def test_refund_statuses(self, status: str | None, state: str) -> None:
        assert codec.refund_state(status) == state

    @pytest.mark.parametrize(
        ("minor", "text"), [(1, "0.01"), (99, "0.99"), (100, "1.00"), (10005, "100.05")]
    )
    def test_the_amount_codec_is_exact_in_both_directions(self, minor: int, text: str) -> None:
        assert codec.minor_to_amount(minor, "INR") == Decimal(text)
        assert codec.dumps({"a": codec.minor_to_amount(minor, "INR")}) == f'{{"a":{text}}}'.encode()
        assert codec.amount_to_minor(codec.loads(text.encode())) == minor
        assert codec.amount_to_minor(text) == minor


class TestRefunds:
    def test_the_refund_id_is_rfd_and_36_hex_and_inside_cashfrees_rule(self) -> None:
        key = domain.refund_idempotency_key("org", "charge-1", "operator-key", "cashfree")
        assert re.fullmatch(r"rfd[0-9a-f]{36}", key) and len(key) == 39
        assert key == domain.refund_idempotency_key("org", "charge-1", "operator-key", "cashfree")
        assert key != domain.refund_idempotency_key("org", "charge-2", "operator-key", "cashfree")
        # Razorpay's is unchanged — every refund already recorded is found again by it.
        razorpay = domain.refund_idempotency_key("org", "charge-1", "operator-key")
        assert re.fullmatch(r"rfd-[0-9a-f]{40}", razorpay)
        assert refund_key("ab" * 32) == "rfd" + ("ab" * 32)[:36]

    def test_a_refund_is_made_against_the_order_with_our_refund_id(self) -> None:
        refund_id = "rfd" + "1" * 36
        script = Script(
            {
                ("POST", "/pg/orders/ord-1/refunds"): [
                    _json(
                        200,
                        {
                            "refund_id": refund_id,
                            "cf_refund_id": 77,
                            "refund_amount": 100.00,
                            "refund_currency": "INR",
                            "refund_status": "PENDING",
                            "refund_note": "free text",
                        },
                    )
                ]
            }
        )
        result = _live(script).refund(
            idempotency_key=refund_id,
            provider_payment_id="3001",
            amount_minor=10_000,
            currency="INR",
            provider_order_id="ord-1",
        )
        assert (result.provider_refund_id, result.state, result.amount_minor) == (
            refund_id,
            "created",
            10_000,
        )
        (request,) = script.requests
        assert json.loads(request.content, parse_float=Decimal) == {
            "refund_amount": Decimal("100.00"),
            "refund_id": refund_id,
            "refund_speed": "STANDARD",
        }
        assert request.headers["x-idempotency-key"] == idempotency_key_for_refund(refund_id)

    def test_a_409_is_the_refund_already_made_and_is_looked_up(self) -> None:
        refund_id = "rfd" + "2" * 36
        script = Script(
            {
                ("POST", "/pg/orders/ord-1/refunds"): [
                    _json(409, {"code": "refund_already_exists"})
                ],
                ("GET", f"/pg/orders/ord-1/refunds/{refund_id}"): [
                    _json(
                        200,
                        {"refund_id": refund_id, "refund_amount": 100, "refund_status": "SUCCESS"},
                    )
                ],
            }
        )
        result = _live(script).refund(
            idempotency_key=refund_id,
            provider_payment_id="3001",
            amount_minor=10_000,
            currency="INR",
            provider_order_id="ord-1",
        )
        assert result.state == "processed"
        assert [r.method for r in script.requests] == ["POST", "GET"]

    def test_the_same_id_for_another_amount_is_refused(self) -> None:
        refund_id = "rfd" + "3" * 36
        script = Script(
            {
                ("POST", "/pg/orders/ord-1/refunds"): [
                    _json(409, {"code": "refund_already_exists"})
                ],
                ("GET", f"/pg/orders/ord-1/refunds/{refund_id}"): [
                    _json(200, {"refund_id": refund_id, "refund_amount": 1.00})
                ],
            }
        )
        with pytest.raises(ProviderRejected) as raised:
            _live(script).refund(
                idempotency_key=refund_id,
                provider_payment_id="3001",
                amount_minor=10_000,
                currency="INR",
                provider_order_id="ord-1",
            )
        assert raised.value.code == "refund_amount_mismatch"

    @pytest.mark.parametrize(
        ("kwargs", "code"),
        [
            ({"provider_order_id": None}, "order_required"),
            ({"idempotency_key": "rfd-" + "a" * 40}, "refund_id_invalid"),
            ({"currency": "USD"}, "unsupported_currency"),
        ],
    )
    def test_refused_before_any_request(self, kwargs: dict[str, Any], code: str) -> None:
        script = Script({})
        call: dict[str, Any] = {
            "idempotency_key": "rfd" + "4" * 36,
            "provider_payment_id": "3001",
            "amount_minor": 100,
            "currency": "INR",
            "provider_order_id": "ord-1",
            **kwargs,
        }
        with pytest.raises(ProviderRejected) as raised:
            _live(script).refund(**call)
        assert raised.value.code == code and script.requests == []


def _webhook(etype: str, **data: Any) -> bytes:
    return json.dumps(
        {"type": etype, "event_time": "2026-10-06T10:15:15+05:30", "data": data}
    ).encode()


def _payment_data(status: str, cf: int = 3001) -> dict[str, Any]:
    return {
        "order": {"order_id": "ord-1", "order_amount": 499.00, "order_currency": "INR"},
        "payment": {
            "cf_payment_id": cf,
            "payment_status": status,
            "payment_amount": 499.00,
            "payment_currency": "INR",
            "payment_message": "free text",
            "payment_method": {"card": {"card_number": "XXXX1111"}},
        },
        "customer_details": {"customer_phone": PHONE, "customer_email": "asha@example.com"},
        "error_details": {
            "error_code": "TRANSACTION_DECLINED",
            "error_description": "the bank said no",
        },
    }


class TestWebhooks:
    def _sign(self, body: bytes, ts: str = "1759745115000") -> dict[str, str]:
        digest = hmac.new(SECRET.encode(), ts.encode() + body, hashlib.sha256).digest()
        return {"x-webhook-signature": base64.b64encode(digest).decode(), "x-webhook-timestamp": ts}

    def test_the_signature_is_base64_hmac_of_timestamp_and_body_with_no_separator(self) -> None:
        provider, _ = _stub()
        body = _webhook("PAYMENT_SUCCESS_WEBHOOK", **_payment_data("SUCCESS"))
        headers = self._sign(body)
        assert provider.verify_webhook(headers=headers, body=body) is True
        # A tampered timestamp, a tampered body, a separator, the wrong secret: all False.
        assert (
            provider.verify_webhook(headers={**headers, "x-webhook-timestamp": "1"}, body=body)
            is False
        )
        assert provider.verify_webhook(headers=headers, body=body + b"\n") is False
        sep = base64.b64encode(
            hmac.new(SECRET.encode(), b"1759745115000." + body, hashlib.sha256).digest()
        ).decode()
        assert (
            provider.verify_webhook(headers={**headers, "x-webhook-signature": sep}, body=body)
            is False
        )
        other = CashfreeCollectionsProvider(
            wire=CashfreeFakeWire(), secret_key="dev-only-other", mode="stub"
        )
        assert other.verify_webhook(headers=headers, body=body) is False

    @pytest.mark.parametrize("header", ["x-webhook-signature", "x-webhook-timestamp"])
    def test_a_non_ascii_header_is_false_not_a_crash(self, header: str) -> None:
        provider, _ = _stub()
        body = _webhook("PAYMENT_SUCCESS_WEBHOOK", **_payment_data("SUCCESS"))
        headers = {**self._sign(body), header: "é☃" * 10}
        assert provider.verify_webhook(headers=headers, body=body) is False

    def test_no_secret_verifies_nothing(self) -> None:
        unset = CashfreeCollectionsProvider(wire=CashfreeFakeWire(), secret_key="", mode="stub")
        body = b"{}"
        assert unset.verify_webhook(headers=self._sign(body), body=body) is False

    def test_success_is_captured_and_binds_the_payment(self) -> None:
        provider, _ = _stub()
        event = provider.parse_webhook(
            body=_webhook("PAYMENT_SUCCESS_WEBHOOK", **_payment_data("SUCCESS"))
        )
        facts = provider.webhook_facts(event)
        assert (event.outcome_state, facts.outcome, event.provider_payment_id) == (
            "captured",
            "captured",
            "3001",
        )
        assert (event.amount_minor, event.currency, event.provider_order_id) == (
            49_900,
            "INR",
            "ord-1",
        )
        assert event.created_at == int(datetime(2026, 10, 6, 4, 45, 15, tzinfo=UTC).timestamp())

    def test_failed_is_failed_with_its_own_payment_and_a_stable_code(self) -> None:
        provider, _ = _stub()
        event = provider.parse_webhook(
            body=_webhook("PAYMENT_FAILED_WEBHOOK", **_payment_data("FAILED"))
        )
        facts = provider.webhook_facts(event)
        assert (facts.outcome, event.provider_payment_id) == ("failed", "3001")
        assert facts.failure_code == "TRANSACTION_DECLINED"
        assert event.failure_reason == "TRANSACTION_DECLINED"  # the code, never the bank's words

    @pytest.mark.parametrize(
        "etype",
        ["PAYMENT_USER_DROPPED_WEBHOOK", "AUTO_REFUND_STATUS_WEBHOOK", "PAYMENT_CHARGES_WEBHOOK"],
    )
    def test_other_events_assert_nothing_and_bind_no_payment(self, etype: str) -> None:
        provider, _ = _stub()
        status = "USER_DROPPED" if "DROPPED" in etype else "SUCCESS"
        event = provider.parse_webhook(body=_webhook(etype, **_payment_data(status)))
        facts = provider.webhook_facts(event)
        assert facts.outcome is None and facts.refund is None
        assert event.provider_payment_id is None
        assert derive_event_id(event) is not None  # still identifiable, to be recorded as ignored

    def test_a_success_event_whose_payment_is_not_successful_asserts_nothing(self) -> None:
        provider, _ = _stub()
        event = provider.parse_webhook(
            body=_webhook("PAYMENT_SUCCESS_WEBHOOK", **_payment_data("PENDING"))
        )
        assert provider.webhook_facts(event).outcome is None

    def test_a_refund_status_is_keyed_by_our_refund_id(self) -> None:
        provider, _ = _stub()
        refund_id = "rfd" + "5" * 36
        body = _webhook(
            "REFUND_STATUS_WEBHOOK",
            refund={
                "cf_refund_id": 11325632,
                "cf_payment_id": 3001,
                "refund_id": refund_id,
                "order_id": "ord-1",
                "refund_amount": 100.00,
                "refund_currency": "INR",
                "refund_status": "SUCCESS",
                "refund_arn": "205907014017",
                "status_description": "Refund processed successfully",
                "refund_note": "customer asked",
            },
        )
        event = provider.parse_webhook(body=body)
        facts = provider.webhook_facts(event)
        assert facts.refund is not None
        assert (facts.refund.provider_refund_id, facts.refund.provider_payment_id) == (
            refund_id,
            "3001",
        )
        assert (facts.refund.amount_minor, facts.refund.state) == (10_000, "processed")
        assert facts.outcome is None and event.entity_id == "11325632"
        text = json.dumps(facts.stored)
        for dropped in ("205907014017", "customer asked", "Refund processed successfully"):
            assert dropped not in text

    def test_the_stored_body_keeps_no_person_and_still_parses_to_the_same_event(self) -> None:
        provider, _ = _stub()
        event = provider.parse_webhook(
            body=_webhook("PAYMENT_FAILED_WEBHOOK", **_payment_data("FAILED"))
        )
        stored = provider.webhook_facts(event).stored
        text = json.dumps(stored)
        for pii in (PHONE, "asha@example.com", "XXXX1111", "free text", "the bank said no"):
            assert pii not in text
        again = provider.parse_webhook(body=text.encode())
        assert derive_event_id(again) == derive_event_id(event)
        assert provider.webhook_facts(again).failure_code == "TRANSACTION_DECLINED"

    @pytest.mark.parametrize("name", ["x-idempotency-header", "x-idempotency-key"])
    def test_the_delivery_id_is_read_under_either_documented_name(self, name: str) -> None:
        provider, _ = _stub()
        assert provider.webhook_delivery_id({name: "abc123"}) == "abc123"
        assert provider.webhook_delivery_id({}) is None


class TestProbe:
    def test_a_404_for_a_random_order_is_authenticated(self) -> None:
        script = Script({})

        def handler(request: httpx.Request) -> httpx.Response:
            script.requests.append(request)
            assert request.method == "GET" and request.url.path.startswith("/pg/orders/probe")
            return _json(404, {"code": "order_not_found"})

        _live(handler).probe()
        assert len(script.requests) == 1

    def test_a_401_is_rejected_credentials(self) -> None:
        with pytest.raises(ProviderMisconfigured):
            _live(lambda request: _json(401, {"code": "authentication_error"})).probe()

    def test_another_answer_is_not_mistaken_for_success(self) -> None:
        with pytest.raises(ProviderRejected) as raised:
            _live(lambda request: _json(400, {"code": "api_version_unsupported"})).probe()
        assert raised.value.code == "api_version_unsupported"


class TestCredentials:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"mode": None}, "mode must be sandbox or production"),
            ({"webhook_secret": "x" * 12}, "webhook_secret is not set for Cashfree"),
            ({"key_id": "has spaces"}, "Cashfree App ID"),
            ({"mode": "production"}, "Production is not enabled"),
            ({"key_secret": "cfsk_ma_prod_0123456789abcdef"}, "look like Production keys"),
        ],
    )
    def test_cashfree_credentials_are_checked_for_shape_only(
        self, kwargs: dict[str, Any], message: str
    ) -> None:
        call: dict[str, Any] = {
            "key_id": APP_ID,
            "key_secret": SECRET,
            "webhook_secret": None,
            "mode": "sandbox",
            "allow_live": False,
            **kwargs,
        }
        with pytest.raises(domain.CredentialProblem, match=message):
            domain.validate_credentials("cashfree", **call)

    def test_sandbox_is_test_and_production_is_live(self) -> None:
        base = {"key_id": "APP0001", "key_secret": SECRET, "webhook_secret": None}
        assert (
            domain.validate_credentials("cashfree", mode="sandbox", allow_live=False, **base)
            == "test"
        )
        assert (
            domain.validate_credentials("cashfree", mode="production", allow_live=True, **base)
            == "live"
        )

    def test_test_shaped_keys_are_refused_for_production(self) -> None:
        with pytest.raises(domain.CredentialProblem, match="Sandbox keys"):
            domain.validate_credentials(
                "cashfree",
                key_id="TEST0001",
                key_secret=SECRET,
                webhook_secret=None,
                mode="production",
                allow_live=True,
            )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("9876543210", "9876543210"),
            ("98765 43210", "9876543210"),
            ("+91 98765-43210", "+919876543210"),
            ("(987) 654-3210", "9876543210"),
            ("", None),
            (None, None),
            ("12345", None),
            ("98765432101", None),
            ("call me", None),
        ],
    )
    def test_a_phone_is_tidied_never_invented(self, raw: str | None, expected: str | None) -> None:
        assert domain.normalize_phone(raw) == expected


def test_no_collections_module_reads_the_platforms_own_credentials() -> None:
    """A merchant's adapter is built from that merchant's keys and nothing else (ADR-0030): no
    collections module, no merchant adapter, and not the factory names a platform credential
    setting — so a missing merchant key can never quietly fall back to the platform's."""
    platform = {
        "razorpay_key_id",
        "razorpay_key_secret",
        "razorpay_webhook_secret",
        "cashfree_app_id",
        "cashfree_secret_key",
        "payment_wire",
        "cashfree_env",
    }
    files = [
        *sorted((SERVICE / "app/collections").glob("*.py")),
        SERVICE / "app/api/collections.py",
        SERVICE / "app/api/collections_internal.py",
        SERVICE / "app/providers/razorpay_collections.py",
        SERVICE / "app/providers/cashfree_collections.py",
    ]
    for path in files:
        tree = ast.parse(path.read_text())
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
        }
        assert not names & platform, f"{path.name} reads {sorted(names & platform)}"
    factory = ast.parse((SERVICE / "app/deps.py").read_text())
    fn = next(
        n
        for n in ast.walk(factory)
        if isinstance(n, ast.FunctionDef) and n.name == "_collections_provider"
    )
    used = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert not used & platform, f"the merchant factory reads {sorted(used & platform)}"
