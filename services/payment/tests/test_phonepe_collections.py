"""The PhonePe merchant adapter (Standard Checkout V2), offline: `CollectionsContract` on the
stateful fake and on the real `PhonePeHttpWire` over an `httpx.MockTransport` that is PhonePe's
token endpoint and PG API; what it sends (form-encoded OAuth, `O-Bearer`, integer paise); what it
refuses before any request; and the webhook's header check failing closed. Every credential is a
`dev-only-` placeholder; no request leaves the machine.
"""

from __future__ import annotations

import json
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.collections import domain
from app.domain import derive_event_id
from app.providers.phonepe_collections import (
    PhonePeCollectionsProvider,
    PhonePeHttpWire,
    redact_webhook_body,
    webhook_authorization,
)
from app.providers.phonepe_fake import PhonePeFakeWire
from app.providers.wire import ProviderApiError, ProviderMisconfigured, ProviderRejected
from tests.collections_contract import CollectionsContract, ensure

CLIENT_ID = "TESTDEVONLYCLIENT01"  # pragma: allowlist secret
CLIENT_SECRET = "dev-only-phonepe-client-secret"  # pragma: allowlist secret
HOOK_USER = "opsgrids-hook"
HOOK_PASSWORD = "dev-only-hook-password"  # pragma: allowlist secret
RETURN = "https://app.opsgrids.example/api/v1/pay/return"


def order_event(
    event: str,
    state: str,
    *,
    order_id: str = "ord-1",
    txn: str = "OM1",
    ts: int = 1_760_000_000_000,
) -> bytes:
    return json.dumps(
        {
            "event": event,
            "payload": {
                "orderId": "OMO1",
                "merchantId": "M1",
                "merchantOrderId": order_id,
                "state": state,
                "amount": 49_900,
                "expireAt": 1_760_000_900_000,
                "metaInfo": {"udf1": "free text the merchant typed"},
                "paymentDetails": [
                    {
                        "paymentMode": "UPI_COLLECT",
                        "transactionId": txn,
                        "timestamp": ts,
                        "amount": 49_900,
                        "state": "COMPLETED" if state == "COMPLETED" else "FAILED",
                        "rail": {"type": "UPI", "utr": "UTR123", "vpa": "buyer@ybl"},
                        "instrument": {"type": "ACCOUNT", "maskedAccountNumber": "XXXX1234"},
                    }
                ],
            },
        }
    ).encode()


def _stub(fake: PhonePeFakeWire, *, password: str = HOOK_PASSWORD) -> PhonePeCollectionsProvider:
    return PhonePeCollectionsProvider(
        wire=fake, mode="stub", webhook_username=HOOK_USER, webhook_password=password
    )


class _PhonePeHooks:
    def unheld_order_id(self, receipt: str) -> str:
        return receipt  # a PhonePe Order's `merchantOrderId` *is* the collection's receipt

    def webhook_bodies(self, adapter: Any) -> tuple[bytes, bytes]:
        return (
            order_event("checkout.order.failed", "FAILED", txn="OM1"),
            order_event("checkout.order.completed", "COMPLETED", txn="OM2"),
        )

    def signed(self, adapter: Any, body: bytes) -> dict[str, str]:
        return {"authorization": webhook_authorization(HOOK_USER, HOOK_PASSWORD)}

    @staticmethod
    def _fake(adapter: Any) -> PhonePeFakeWire:
        raise NotImplementedError

    def create_count(self, adapter: Any) -> int:
        return self._fake(adapter).created

    def pay(self, adapter: Any, order_id: str) -> str:
        return self._fake(adapter).pay(order_id)

    def refund_count(self, adapter: Any, order_id: str) -> int | None:
        fake = self._fake(adapter)
        return sum(1 for r in fake.refunds.values() if r["originalMerchantOrderId"] == order_id)

    # PhonePe's documented webhook credential is a static `Authorization` header —
    # `hex(SHA256(user:password))` — that does not cover the body (module docstring of
    # `phonepe_collections`). The two contract checks written for a per-body signature header are
    # replaced by what that scheme can promise; the body-binding gap is an open question for
    # PhonePe (its HMAC option is undocumented), stated in the PR.

    def test_a_forged_signature_never_verifies_and_never_crashes(self, adapter: Any) -> None:
        body = self.webhook_bodies(adapter)[0]
        for forged in ("0" * 64, "zz", "\xff" * 64, "é" * 32, "☃" * 8, " " * 64, "A" * 44, ""):
            assert adapter.verify_webhook(headers={"authorization": forged}, body=body) is False
        wrong = webhook_authorization(HOOK_USER, "dev-only-another-password")
        assert adapter.verify_webhook(headers={"authorization": wrong}, body=body) is False

    def test_a_genuine_signature_verifies_and_only_over_these_bytes(self, adapter: Any) -> None:
        body = self.webhook_bodies(adapter)[0]
        assert adapter.verify_webhook(headers=self.signed(adapter, body), body=body) is True
        unconfigured = _stub(PhonePeFakeWire(), password="")
        assert unconfigured.verify_webhook(headers=self.signed(adapter, body), body=body) is False


class TestThePhonePeFakeWire(_PhonePeHooks, CollectionsContract):
    @pytest.fixture
    def adapter(self) -> Any:
        return _stub(PhonePeFakeWire())

    @staticmethod
    def _fake(adapter: Any) -> PhonePeFakeWire:
        fake: PhonePeFakeWire = adapter._wire
        return fake

    def fresh(self, adapter: Any) -> Any:
        return _stub(self._fake(adapter))

    def rejecting(self, adapter: Any) -> Any | None:
        refusing = PhonePeFakeWire()
        refusing.reject_credentials = True
        return _stub(refusing)


class PhonePeOverHttp:
    """An `httpx.MockTransport` handler that is PhonePe: the token endpoint checks the
    form-encoded client credentials and issues a token, the PG API checks the `O-Bearer` token and
    hands the call to a `PhonePeFakeWire`, answering its refusals as PhonePe's JSON (a `code`, and
    a free-text `message` the wire must never keep)."""

    def __init__(self, *, secret: str = CLIENT_SECRET) -> None:
        self.fake = PhonePeFakeWire()
        self._secret = secret
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/apis/pg-sandbox")
        if path == "/v1/oauth/token":
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            if form != {
                "client_id": CLIENT_ID,
                "client_version": "1",
                "client_secret": self._secret,
                "grant_type": "client_credentials",
            }:
                return httpx.Response(400, json={"code": "BAD_REQUEST", "message": "free text"})
            return httpx.Response(
                200, json={"access_token": "dev-only-token", "token_type": "O-Bearer"}
            )
        if request.headers.get("authorization") != "O-Bearer dev-only-token":
            return httpx.Response(401, json={"code": "UNAUTHORIZED"})
        try:
            result = self.fake.request(
                request.method,
                path,
                params=dict(request.url.params),
                json=json.loads(request.content) if request.content else None,
            )
        except ProviderApiError as exc:
            return httpx.Response(exc.status_code or 400, json={"code": exc.code, "message": "x"})
        return httpx.Response(200, json=result)


def _http(bridge: PhonePeOverHttp, *, secret: str = CLIENT_SECRET) -> Any:
    wire = PhonePeHttpWire(
        client_id=CLIENT_ID,
        client_secret=secret,
        client_version="1",
        env="sandbox",
        transport=bridge.transport(),
    )
    adapter = PhonePeCollectionsProvider(
        wire=wire, mode="sandbox", webhook_username=HOOK_USER, webhook_password=HOOK_PASSWORD
    )
    adapter.bridge = bridge  # type: ignore[attr-defined]
    return adapter


class TestThePhonePeHttpWire(_PhonePeHooks, CollectionsContract):
    @pytest.fixture
    def adapter(self) -> Any:
        return _http(PhonePeOverHttp())

    @staticmethod
    def _fake(adapter: Any) -> PhonePeFakeWire:
        fake: PhonePeFakeWire = adapter.bridge.fake
        return fake

    def fresh(self, adapter: Any) -> Any:
        return _http(adapter.bridge)

    def rejecting(self, adapter: Any) -> Any | None:
        return _http(adapter.bridge, secret="dev-only-wrong-secret")


# --------------------------------------------------------------------------- what it sends


class TestTheWire:
    def test_the_token_is_form_encoded_and_every_call_carries_it(self) -> None:
        bridge = PhonePeOverHttp()
        order = ensure(_http(bridge), str(uuid.uuid4()), amount_minor=1999)
        token, lookup, create = bridge.requests
        assert token.headers["content-type"] == "application/x-www-form-urlencoded"
        assert str(token.url).startswith("https://api-preprod.phonepe.com/apis/pg-sandbox/")
        assert lookup.method == "GET" and create.method == "POST"
        sent = json.loads(create.content)
        # Integer paise, never rupees and never a float.
        assert sent["amount"] == 1999 and isinstance(sent["amount"], int)
        assert sent["paymentFlow"] == {
            "type": "PG_CHECKOUT",
            "merchantUrls": {"redirectUrl": RETURN},
        }
        assert 300 <= sent["expireAfter"] <= 3600
        assert order.redirect is not None and order.redirect.method == "GET"
        assert order.redirect.url.startswith("https://")
        assert "token" not in repr(order)  # the handle never reaches a traceback

    def test_production_talks_to_production_including_the_token_endpoint(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.path.endswith("/oauth/token"):
                return httpx.Response(200, json={"access_token": "t"})
            return httpx.Response(400, json={"code": "INVALID_MERCHANT_ORDER_ID"})

        wire = PhonePeHttpWire(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            client_version="1",
            env="production",
            transport=httpx.MockTransport(handler),
        )
        PhonePeCollectionsProvider(wire=wire, mode="production").probe()
        assert seen[0].startswith("https://api.phonepe.com/apis/identity-manager/v1/oauth/token")
        assert seen[1].startswith("https://api.phonepe.com/apis/pg/checkout/v2/order/")

    @pytest.mark.parametrize(
        ("amount", "currency", "code"),
        [(99, "INR", "amount_below_minimum"), (49_900, "USD", "currency_not_supported")],
    )
    def test_money_it_cannot_take_is_refused_before_any_request(
        self, amount: int, currency: str, code: str
    ) -> None:
        fake = PhonePeFakeWire()
        with pytest.raises(ProviderRejected) as raised:
            _stub(fake).ensure_order(
                receipt=str(uuid.uuid4()),
                amount_minor=amount,
                currency=currency,
                notes={},
                return_url=RETURN,
            )
        assert raised.value.code == code and list(fake.requests) == []

    def test_no_return_url_is_refused_before_any_request(self) -> None:
        fake = PhonePeFakeWire()
        with pytest.raises(ProviderRejected) as raised:
            _stub(fake).ensure_order(
                receipt=str(uuid.uuid4()), amount_minor=49_900, currency="INR", notes={}
            )
        assert raised.value.code == "return_url_required" and list(fake.requests) == []

    def test_an_order_found_for_another_amount_is_refused_not_reused(self) -> None:
        fake = PhonePeFakeWire()
        adapter = _stub(fake)
        receipt = str(uuid.uuid4())
        ensure(adapter, receipt, amount_minor=10_000)
        with pytest.raises(ProviderRejected) as raised:
            ensure(adapter, receipt, amount_minor=49_900)
        assert raised.value.code == "order_amount_mismatch" and fake.created == 1

    def test_a_paid_order_is_not_reopened(self) -> None:
        fake = PhonePeFakeWire()
        adapter = _stub(fake)
        receipt = str(uuid.uuid4())
        ensure(adapter, receipt)
        fake.pay(receipt)
        with pytest.raises(ProviderRejected) as raised:
            ensure(adapter, receipt, recorded_order_id=receipt)
        assert raised.value.code == "order_not_payable"

    def test_expiry_is_clamped_to_phonepes_window(self) -> None:
        bridge = PhonePeOverHttp()
        _http(bridge).ensure_order(
            receipt=str(uuid.uuid4()),
            amount_minor=49_900,
            currency="INR",
            notes={},
            expires_at=datetime.now(UTC) + timedelta(days=1),
            return_url=RETURN,
        )
        assert json.loads(bridge.requests[-1].content)["expireAfter"] == 3600

    def test_wrong_client_credentials_are_misconfigured_not_transient(self) -> None:
        with pytest.raises(ProviderMisconfigured):
            _http(PhonePeOverHttp(), secret="dev-only-wrong").find_order(receipt="r-1")

    def test_a_pending_attempt_is_no_observation_and_a_failure_carries_its_code(self) -> None:
        fake = PhonePeFakeWire()
        adapter = _stub(fake)
        receipt = str(uuid.uuid4())
        ensure(adapter, receipt)
        fake.pay(receipt, state="PENDING")
        assert adapter.order_payments(order_id=receipt) == []
        fake.pay(receipt, state="FAILED", error_code="AUTHORIZATION_ERROR")
        (failed,) = adapter.order_payments(order_id=receipt)
        assert (failed.status, failed.error_code) == ("failed", "AUTHORIZATION_ERROR")

    def test_a_refund_whose_answer_was_lost_is_found_by_our_id(self) -> None:
        fake = PhonePeFakeWire()
        adapter = _stub(fake)
        receipt = str(uuid.uuid4())
        ensure(adapter, receipt)
        payment = fake.pay(receipt)
        key = domain.refund_idempotency_key(uuid.uuid4(), receipt, "k", "phonepe")
        args: dict[str, Any] = {
            "idempotency_key": key,
            "provider_payment_id": payment,
            "amount_minor": 10_000,
            "currency": "INR",
            "provider_order_id": receipt,
        }
        first = adapter.refund(**args)
        assert adapter.refund(**args).provider_refund_id == first.provider_refund_id == key
        assert len(key) == 39 and key.startswith("rfd")
        with pytest.raises(ProviderRejected) as raised:
            adapter.refund(**{**args, "amount_minor": 20_000})
        assert raised.value.code == "refund_amount_mismatch"


# --------------------------------------------------------------------------- webhooks


class TestWebhooks:
    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"authorization": ""},
            {"authorization": webhook_authorization(HOOK_USER, "dev-only-wrong")},
            {"authorization": webhook_authorization("someone-else", HOOK_PASSWORD)},
            {"authorization": "Basic " + webhook_authorization(HOOK_USER, HOOK_PASSWORD)},
        ],
    )
    def test_anything_but_the_exact_credential_fails_closed(self, headers: dict[str, str]) -> None:
        body = order_event("checkout.order.completed", "COMPLETED")
        assert _stub(PhonePeFakeWire()).verify_webhook(headers=headers, body=body) is False

    def test_no_configured_username_never_verifies(self) -> None:
        adapter = PhonePeCollectionsProvider(
            wire=PhonePeFakeWire(), mode="stub", webhook_password=HOOK_PASSWORD
        )
        headers = {"authorization": webhook_authorization("", HOOK_PASSWORD)}
        assert adapter.verify_webhook(headers=headers, body=b"{}") is False

    def test_a_completed_order_is_captured_in_paise_and_bound_to_its_attempt(self) -> None:
        adapter = _stub(PhonePeFakeWire())
        event = adapter.parse_webhook(
            body=order_event("checkout.order.completed", "COMPLETED", txn="OM9")
        )
        assert event.outcome_state == "captured" and event.provider_payment_id == "OM9"
        assert (event.amount_minor, event.currency) == (49_900, "INR")
        assert event.provider_order_id == "ord-1" and derive_event_id(event) is not None

    def test_the_root_state_decides_not_the_event_name(self) -> None:
        adapter = _stub(PhonePeFakeWire())
        event = adapter.parse_webhook(body=order_event("checkout.order.completed", "PENDING"))
        assert event.outcome_state == "unknown" and event.provider_payment_id is None

    def test_what_is_stored_holds_no_vpa_utr_account_or_free_text(self) -> None:
        stored = json.dumps(
            redact_webhook_body(json.loads(order_event("checkout.order.completed", "COMPLETED")))
        )
        for kept_out in ("buyer@ybl", "UTR123", "XXXX1234", "free text", "metaInfo"):
            assert kept_out not in stored

    def test_a_refund_event_is_keyed_by_our_refund_id(self) -> None:
        adapter = _stub(PhonePeFakeWire())
        body = json.dumps(
            {
                "event": "pg.refund.completed",
                "payload": {
                    "merchantRefundId": "rfd" + "a" * 36,
                    "originalMerchantOrderId": "ord-1",
                    "refundId": "OMR1",
                    "amount": 10_000,
                    "state": "COMPLETED",
                    "timestamp": 1_760_000_000_000,
                },
            }
        ).encode()
        facts = adapter.webhook_facts(adapter.parse_webhook(body=body))
        assert facts.outcome is None and facts.refund is not None
        assert (facts.refund.provider_refund_id, facts.refund.state) == (
            "rfd" + "a" * 36,
            "processed",
        )
        assert facts.refund.amount_minor == 10_000


def test_the_spec_never_stores_the_authorization_header() -> None:
    assert domain.spec_for("phonepe").signature_header is None
    assert domain.spec_for("phonepe").has_webhook_secret is True


@pytest.mark.parametrize(
    ("config", "hook", "ok"),
    [
        ({"client_version": 1, "webhook_username": HOOK_USER}, HOOK_PASSWORD, True),
        ({"client_version": "1", "webhook_username": HOOK_USER}, HOOK_PASSWORD, True),
        ({"webhook_username": HOOK_USER}, HOOK_PASSWORD, False),
        ({"client_version": 1}, HOOK_PASSWORD, False),
        ({"client_version": True, "webhook_username": HOOK_USER}, HOOK_PASSWORD, False),
        ({"client_version": 1, "webhook_username": HOOK_USER, "x": "y"}, HOOK_PASSWORD, False),
        ({"client_version": 1, "webhook_username": HOOK_USER}, None, False),
    ],
)
def test_phonepe_credentials_need_their_config_and_webhook_password(
    config: dict[str, Any], hook: str | None, ok: bool
) -> None:
    def check() -> str:
        return domain.validate_credentials(
            "phonepe",
            key_id=CLIENT_ID,
            key_secret=CLIENT_SECRET,
            webhook_secret=hook,
            mode="sandbox",
            allow_live=False,
            config=config,
        )

    if ok:
        assert check() == "test"
    else:
        with pytest.raises(domain.CredentialProblem):
            check()


def test_production_needs_live_keys_allowed() -> None:
    args: dict[str, Any] = {
        "key_id": CLIENT_ID,
        "key_secret": CLIENT_SECRET,
        "webhook_secret": HOOK_PASSWORD,
        "mode": "production",
        "config": {"client_version": 1, "webhook_username": HOOK_USER},
    }
    with pytest.raises(domain.CredentialProblem):
        domain.validate_credentials("phonepe", allow_live=False, **args)
    assert domain.validate_credentials("phonepe", allow_live=True, **args) == "live"
