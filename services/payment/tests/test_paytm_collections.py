"""The Paytm merchant adapter, offline: `CollectionsContract` on the stateful fake and on the real
`PaytmHttpWire` over an `httpx.MockTransport` that answers as that fake (checking every signature);
the checksum pinned to vectors produced by Paytm's own `paytmchecksum` 1.7.0; amounts in exact
rupees on the wire and paise everywhere else; and the webhook's `CHECKSUMHASH` failing closed.
Every credential is a `dev-only-` placeholder; no request leaves the machine.
"""

from __future__ import annotations

import json
import urllib.parse
import uuid
from typing import Any

import httpx
import pytest

from app.collections import domain
from app.domain import derive_event_id
from app.providers.paytm_collections import (
    PaytmCollectionsProvider,
    PaytmHttpWire,
    generate_signature,
    order_id_for,
    params_string,
    verify_signature,
)
from app.providers.paytm_fake import PaytmFakeWire
from app.providers.wire import ProviderApiError, ProviderMisconfigured, ProviderRejected
from tests.collections_contract import CollectionsContract, ensure

MID = "TESTMID0001"
KEY = "dev-only-key-016"  # pragma: allowlist secret
RETURN = "https://app.opsgrids.com/api/v1/pay/return"


def status_webhook(
    order_id: str, txn_id: str, *, status: str = "TXN_SUCCESS", key: str = KEY
) -> bytes:
    fields = {
        "ORDERID": order_id,
        "MID": MID,
        "TXNID": txn_id,
        "TXNAMOUNT": "499.00",
        "PAYMENTMODE": "UPI",
        "CURRENCY": "INR",
        "TXNDATE": "2026-10-16 10:15:02.0",
        "STATUS": status,
        "RESPCODE": "01" if status == "TXN_SUCCESS" else "227",
        "RESPMSG": "Txn Success",
        "GATEWAYNAME": "PPBL",
        "BANKTXNID": "777001087423130",
        "BANKNAME": "buyer@paytm",
    }
    fields["CHECKSUMHASH"] = generate_signature(params_string(fields), key)
    return urllib.parse.urlencode(fields).encode()


# --------------------------------------------------------------------------- the checksum


def test_the_checksum_matches_paytms_own_library() -> None:
    """Vectors from `paytmchecksum.PaytmChecksum.calculateChecksum` (1.7.0) with fixed salts."""
    body = '{"mid": "TESTMID01", "orderId": "abc"}'
    assert generate_signature(body, KEY, salt="AbC1") == (
        "+CE/Qsf7+b8aUuUFDgH+UAvfNAmNPx4EbI+clEe4+jKXb1Mzi6hDkdXTaa5FRwlobZVkMZIuTzvLWBAXt+X6Ff"
        "BNtNdqAk043qE3KT5Gon8="
    )
    fields = {
        "MID": "TESTMID01",
        "ORDERID": "abc",
        "STATUS": "TXN_SUCCESS",
        "TXNAMOUNT": "1.00",
        "RESPMSG": "null",
    }
    assert params_string(fields) == "TESTMID01|abc||TXN_SUCCESS|1.00"
    official = (
        "Fn1rCYYft6Ryw3JHmVPpcZ5BEvrR0+bb4kqm1t+DQ9NQ0BFKpDHIdc72FiAjdghvlRoKjCThNt+HFR8tvI5Cbz"
        "y8Rirf5JQaeBut2JZe1w4="
    )
    assert verify_signature(params_string(fields), KEY, official) is True
    assert verify_signature(params_string({**fields, "TXNAMOUNT": "2.00"}), KEY, official) is False


@pytest.mark.parametrize("bad", [None, "", "not base64!", "QUJD", "A" * 44, "☃", " " + "QUJD" * 11])
def test_an_unreadable_checksum_fails_closed(bad: str | None) -> None:
    assert verify_signature("TESTMID01|abc", KEY, bad) is False


def test_a_key_aes_cannot_take_fails_closed() -> None:
    signature = generate_signature("x", KEY)
    assert verify_signature("x", "short", signature) is False


# --------------------------------------------------------------------------- the contract


def _stub(fake: PaytmFakeWire, *, key: str = KEY) -> PaytmCollectionsProvider:
    return PaytmCollectionsProvider(wire=fake, mid=MID, merchant_key=key, mode="stub")


class _PaytmHooks:
    def unheld_order_id(self, receipt: str) -> str:
        return uuid.uuid4().hex  # never this receipt's Order id

    def webhook_bodies(self, adapter: Any) -> tuple[bytes, bytes]:
        return (
            status_webhook(uuid.uuid4().hex, "20201016001", status="TXN_FAILURE"),
            status_webhook(uuid.uuid4().hex, "20201016002"),
        )

    def signed(self, adapter: Any, body: bytes) -> dict[str, str]:
        return {}  # the checksum travels in the body

    @staticmethod
    def _fake(adapter: Any) -> PaytmFakeWire:
        raise NotImplementedError

    def create_count(self, adapter: Any) -> int:
        return self._fake(adapter).created

    def pay(self, adapter: Any, order_id: str) -> str:
        return self._fake(adapter).pay(order_id)

    def refund_count(self, adapter: Any, order_id: str) -> int | None:
        return sum(1 for r in self._fake(adapter).refunds.values() if r["orderId"] == order_id)

    # Paytm's checksum travels inside the form, not in a header: the contract's forged-header check
    # is replaced by the same forgeries placed in `CHECKSUMHASH` (the "only over these bytes" check
    # runs unchanged — every field is covered).
    def test_a_forged_signature_never_verifies_and_never_crashes(self, adapter: Any) -> None:
        body = self.webhook_bodies(adapter)[1]
        fields = dict(urllib.parse.parse_qsl(body.decode()))
        for forged in ("0" * 64, "zz", "\xff" * 64, "é" * 32, "☃" * 8, " " * 64, "A" * 44, ""):
            tampered = urllib.parse.urlencode({**fields, "CHECKSUMHASH": forged}).encode()
            assert adapter.verify_webhook(headers={}, body=tampered) is False
        for name, value in (("STATUS", "TXN_FAILURE"), ("TXNAMOUNT", "1.00"), ("MID", "OTHER")):
            tampered = urllib.parse.urlencode({**fields, name: value}).encode()
            assert adapter.verify_webhook(headers={}, body=tampered) is False, name


class TestThePaytmFakeWire(_PaytmHooks, CollectionsContract):
    @pytest.fixture
    def adapter(self) -> Any:
        return _stub(PaytmFakeWire(mid=MID, merchant_key=KEY))

    @staticmethod
    def _fake(adapter: Any) -> PaytmFakeWire:
        fake: PaytmFakeWire = adapter._wire
        return fake

    def fresh(self, adapter: Any) -> Any:
        return _stub(self._fake(adapter))

    def rejecting(self, adapter: Any) -> Any | None:
        return _stub(self._fake(adapter), key="dev-only-wrong16")


class PaytmOverHttp:
    """An `httpx.MockTransport` handler that is Paytm's two hosts: the request is handed to a
    `PaytmFakeWire` that verifies its signature; refusals come back as Paytm's JSON, HTTP 200."""

    def __init__(self) -> None:
        self.fake = PaytmFakeWire(mid=MID, merchant_key=KEY)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if request.url.path.startswith("/theia/"):
            assert host == "securestage.paytmpayments.com"
        else:
            assert host == "securegw-stage.paytmpayments.in"
        try:
            result = self.fake.request(request.method, request.url.path, content=request.content)
        except ProviderApiError as exc:
            return httpx.Response(exc.status_code or 400, text="bad request")
        return httpx.Response(200, json=result)


def _http(bridge: PaytmOverHttp, *, key: str = KEY) -> Any:
    wire = PaytmHttpWire(env="sandbox", transport=httpx.MockTransport(bridge))
    adapter = PaytmCollectionsProvider(wire=wire, mid=MID, merchant_key=key, mode="sandbox")
    adapter.bridge = bridge  # type: ignore[attr-defined]
    return adapter


class TestThePaytmHttpWire(_PaytmHooks, CollectionsContract):
    @pytest.fixture
    def adapter(self) -> Any:
        return _http(PaytmOverHttp())

    @staticmethod
    def _fake(adapter: Any) -> PaytmFakeWire:
        fake: PaytmFakeWire = adapter.bridge.fake
        return fake

    def fresh(self, adapter: Any) -> Any:
        return _http(adapter.bridge)

    def rejecting(self, adapter: Any) -> Any | None:
        return _http(adapter.bridge, key="dev-only-wrong16")


# --------------------------------------------------------------------------- what it sends


class TestTheOrder:
    def test_it_initiates_exact_rupees_signed_and_hands_the_browser_a_form(self) -> None:
        bridge = PaytmOverHttp()
        receipt = str(uuid.uuid4())
        order = ensure(_http(bridge), receipt, amount_minor=49_999)
        lookup, initiate = bridge.requests
        assert lookup.url.path == "/v3/order/status"
        assert initiate.url.params["orderId"] == order_id_for(receipt) == order.provider_order_id
        sent = json.loads(initiate.content)
        assert sent["body"]["txnAmount"] == {"value": "499.99", "currency": "INR"}
        assert sent["body"]["callbackUrl"].endswith("/api/v1/pay/return")
        assert sent["body"]["userInfo"] == {"custId": f"c{order_id_for(receipt)}"}
        assert verify_signature(json.dumps(sent["body"]), KEY, sent["head"]["signature"])
        redirect = order.redirect
        assert redirect is not None and redirect.method == "POST"
        assert redirect.url.startswith(
            "https://securestage.paytmpayments.com/theia/api/v1/showPaymentPage?"
        )
        assert set(redirect.fields) == {"mid", "orderId", "txnToken"}
        assert "stub-token" not in repr(order)

    def test_production_initiates_at_production(self) -> None:
        adapter = PaytmCollectionsProvider(
            wire=PaytmFakeWire(), mid=MID, merchant_key=KEY, mode="production"
        )
        redirect = ensure(adapter, str(uuid.uuid4())).redirect
        assert redirect.url.startswith("https://secure.paytmpayments.com/theia/")

    @pytest.mark.parametrize(
        ("over", "code"),
        [
            ({"currency": "USD"}, "currency_not_supported"),
            ({"amount_minor": 99}, "amount_below_minimum"),
            ({"return_url": None}, "return_url_required"),
            ({"receipt": "not-a-uuid"}, "order_id_invalid"),
        ],
    )
    def test_what_paytm_cannot_take_is_refused_before_any_request(
        self, over: dict[str, Any], code: str
    ) -> None:
        fake = PaytmFakeWire()
        kwargs: dict[str, Any] = {
            "receipt": str(uuid.uuid4()),
            "amount_minor": 49_900,
            "currency": "INR",
            "notes": {},
            "return_url": RETURN,
            **over,
        }
        with pytest.raises(ProviderRejected) as raised:
            _stub(fake).ensure_order(**kwargs)
        assert raised.value.code == code and list(fake.requests) == []

    def test_a_captured_order_is_never_initiated_again(self) -> None:
        fake = PaytmFakeWire()
        receipt = str(uuid.uuid4())
        order = ensure(_stub(fake), receipt)
        fake.pay(order.provider_order_id)
        with pytest.raises(ProviderRejected) as raised:
            ensure(_stub(fake), receipt, recorded_order_id=order.provider_order_id)
        assert raised.value.code == "order_not_payable" and fake.created == 1

    def test_an_order_for_another_amount_is_refused(self) -> None:
        fake = PaytmFakeWire()
        receipt = str(uuid.uuid4())
        ensure(_stub(fake), receipt, amount_minor=10_000)
        with pytest.raises(ProviderRejected) as raised:
            ensure(_stub(fake), receipt, amount_minor=49_900)
        assert raised.value.code == "order_amount_mismatch"

    def test_wrong_credentials_are_misconfigured(self) -> None:
        with pytest.raises(ProviderMisconfigured):
            _stub(PaytmFakeWire(mid=MID, merchant_key=KEY), key="dev-only-wrong16").probe()

    def test_a_pending_order_is_no_observation_and_a_failure_carries_its_code(self) -> None:
        fake = PaytmFakeWire()
        adapter = _stub(fake)
        order = ensure(adapter, str(uuid.uuid4()))
        assert adapter.order_payments(order_id=order.provider_order_id) == []
        fake.pay(order.provider_order_id, status="TXN_FAILURE", code="227")
        (failed,) = adapter.order_payments(order_id=order.provider_order_id)
        assert (failed.status, failed.error_code, failed.amount_minor) == ("failed", "227", 49_900)

    def test_a_reused_refund_id_is_looked_up_and_amounts_are_exact(self) -> None:
        fake = PaytmFakeWire()
        adapter = _stub(fake)
        order = ensure(adapter, str(uuid.uuid4()))
        txn = fake.pay(order.provider_order_id)
        key = domain.refund_idempotency_key(uuid.uuid4(), uuid.uuid4(), "k", "paytm")
        args: dict[str, Any] = {
            "idempotency_key": key,
            "provider_payment_id": txn,
            "amount_minor": 10_001,
            "currency": "INR",
            "provider_order_id": order.provider_order_id,
        }
        assert adapter.refund(**args).state == "created"
        assert fake.refunds[key]["refundAmount"] == "100.01"
        assert adapter.refund(**args).provider_refund_id == key and len(fake.refunds) == 1
        with pytest.raises(ProviderRejected) as raised:
            adapter.refund(**{**args, "amount_minor": 20_000})
        assert raised.value.code == "refund_amount_mismatch"


class TestWebhooks:
    def test_a_success_is_captured_in_paise_and_keeps_no_bank_field(self) -> None:
        adapter = _stub(PaytmFakeWire())
        body = status_webhook("a" * 32, "2020101600099")
        assert adapter.verify_webhook(headers={}, body=body) is True
        event = adapter.parse_webhook(body=body)
        assert event.outcome_state == "captured" and event.provider_payment_id == "2020101600099"
        assert (event.amount_minor, event.currency, event.provider_order_id) == (
            49_900,
            "INR",
            "a" * 32,
        )
        assert derive_event_id(event) is not None
        stored = json.dumps(adapter.webhook_facts(event).stored)
        assert "buyer@paytm" not in stored and "777001087423130" not in stored

    def test_another_merchants_webhook_never_verifies(self) -> None:
        other = PaytmCollectionsProvider(
            wire=PaytmFakeWire(), mid="OTHERMID01", merchant_key=KEY, mode="stub"
        )
        assert other.verify_webhook(headers={}, body=status_webhook("a" * 32, "1")) is False
        wrong_key = _stub(PaytmFakeWire(), key="dev-only-wrong16")
        assert wrong_key.verify_webhook(headers={}, body=status_webhook("a" * 32, "1")) is False

    def test_a_json_refund_webhook_never_verifies(self) -> None:
        body = json.dumps(
            {"head": {"signature": "x"}, "body": {"mid": MID, "refId": "r1"}}
        ).encode()
        assert _stub(PaytmFakeWire()).verify_webhook(headers={}, body=body) is False
