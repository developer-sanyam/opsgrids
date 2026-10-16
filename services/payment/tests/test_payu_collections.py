"""The PayU merchant adapter (Hosted Checkout), offline: `CollectionsContract` on the stateful fake
and on the real `PayUHttpWire` over an `httpx.MockTransport` that answers as that fake; the hashes
PayU documents (request, reverse, command); amounts in exact rupees on the wire and paise
everywhere else; and the webhook's reverse hash failing closed. Every credential is a `dev-only-`
placeholder; no request leaves the machine.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import uuid
from typing import Any

import httpx
import pytest

from app.collections import domain
from app.domain import derive_event_id
from app.providers.base import CheckoutCustomer
from app.providers.payu_collections import (
    PayUCollectionsProvider,
    PayUHttpWire,
    paise,
    request_hash,
    response_hash,
    rupees,
    txnid_for,
)
from app.providers.payu_fake import PayUFakeWire
from app.providers.wire import ProviderApiError, ProviderMisconfigured, ProviderRejected
from tests.collections_contract import CollectionsContract, ensure

KEY = "TESTdevKey01"  # pragma: allowlist secret
SALT = "dev-only-payu-salt-0001"  # pragma: allowlist secret
RETURN = "https://app.opsgrids.com/api/v1/pay/return"


def webhook(fields: dict[str, str], *, salt: str = SALT) -> bytes:
    """A PayU payment webhook as PayU sends it: form-encoded, with its reverse hash."""
    body = {
        "key": KEY,
        "productinfo": "Order payment",
        "firstname": "Asha",
        "email": "buyer@example.com",
        "phone": "9876543210",
        "udf1": "",
        "udf2": "",
        "udf3": "",
        "udf4": "",
        "udf5": "",
        "mode": "UPI",
        "field3": "buyer@okaxis",
        "card_no": "XXXXXXXXXXXX1111",
        "addedon": "2026-10-16 10:15:00",
        "error": "E000",
        **fields,
    }
    body["hash"] = response_hash(body, salt)
    return urllib.parse.urlencode(body).encode()


def paid(
    txnid: str = "0" * 25, *, mihpayid: str = "403993715531", status: str = "success"
) -> bytes:
    return webhook(
        {
            "txnid": txnid,
            "mihpayid": mihpayid,
            "status": status,
            "unmappedstatus": "captured" if status == "success" else "failed",
            "amount": "499.00",
            **({"error": "E325"} if status == "failure" else {}),
        }
    )


class _Counting(PayUCollectionsProvider):
    """PayU holds nothing until the customer posts the form, so "an Order" is a transaction id
    this platform handed out: counted here, shared by every adapter over one fake account."""

    def ensure_order(self, **kwargs: Any) -> Any:
        order = super().ensure_order(**kwargs)
        self._wire.issued.add(order.provider_order_id)  # type: ignore[attr-defined]
        return order


def _stub(fake: PayUFakeWire, **kw: Any) -> _Counting:
    if not hasattr(fake, "issued"):
        fake.issued = set()  # type: ignore[attr-defined]
    return _Counting(wire=fake, key=KEY, salt=kw.get("salt", SALT), mode="stub")


class _PayUHooks:
    def unheld_order_id(self, receipt: str) -> str:
        return "z" * 25  # never this receipt's transaction id

    def webhook_bodies(self, adapter: Any) -> tuple[bytes, bytes]:
        return (
            paid(txnid_for(str(uuid.uuid4())), mihpayid="403993715501", status="failure"),
            paid(txnid_for(str(uuid.uuid4())), mihpayid="403993715502"),
        )

    def signed(self, adapter: Any, body: bytes) -> dict[str, str]:
        return {}  # PayU's hash travels in the body

    @staticmethod
    def _fake(adapter: Any) -> PayUFakeWire:
        raise NotImplementedError

    def create_count(self, adapter: Any) -> int:
        return len(self._fake(adapter).issued)  # type: ignore[attr-defined]

    def pay(self, adapter: Any, order_id: str) -> str:
        return self._fake(adapter).pay(order_id, 49_900)

    def refund_count(self, adapter: Any, order_id: str) -> int | None:
        return sum(1 for r in self._fake(adapter).refunds.values() if r["txnid"] == order_id)

    # PayU has no server-side Order and signs its webhooks inside the body, so three contract
    # checks written for an Order-creating provider with a signature header are replaced by what
    # PayU's model can promise (stated in the PR).

    def test_find_order_never_creates(self, adapter: Any) -> None:
        receipt = str(uuid.uuid4())
        assert adapter.find_order(receipt=receipt) is None
        made = ensure(adapter, receipt)
        # Nothing exists at PayU until the customer posts the form, and a lookup never makes it.
        assert adapter.find_order(receipt=receipt) is None
        self.pay(adapter, made.provider_order_id)
        found = adapter.find_order(receipt=receipt)
        assert found is not None and found.provider_order_id == made.provider_order_id
        assert ("POST", "/_payment") not in self._fake(adapter).requests

    def test_a_forged_signature_never_verifies_and_never_crashes(self, adapter: Any) -> None:
        body = self.webhook_bodies(adapter)[1]
        fields = dict(urllib.parse.parse_qsl(body.decode()))
        for forged in ("0" * 128, "zz", "\xff" * 64, "é" * 32, "☃" * 8, " " * 128, ""):
            tampered = urllib.parse.urlencode({**fields, "hash": forged}).encode()
            assert adapter.verify_webhook(headers={}, body=tampered) is False
        assert adapter.verify_webhook(headers={}, body=paid().replace(b"hash=", b"hush=")) is False

    def test_a_genuine_signature_verifies_and_only_over_these_bytes(self, adapter: Any) -> None:
        body = self.webhook_bodies(adapter)[1]
        assert adapter.verify_webhook(headers={}, body=body) is True
        fields = dict(urllib.parse.parse_qsl(body.decode()))
        # Every field the reverse hash covers is bound by it.
        for name, value in (
            ("status", "failure"),
            ("amount", "1.00"),
            ("txnid", "1" * 25),
            ("key", "OTHERkey01"),
            ("email", "x@example.com"),
            ("udf1", "x"),
        ):
            tampered = urllib.parse.urlencode({**fields, name: value}).encode()
            assert adapter.verify_webhook(headers={}, body=tampered) is False, name
        assert adapter.verify_webhook(headers={}, body=webhook({}, salt="dev-only-other")) is False


class TestThePayUFakeWire(_PayUHooks, CollectionsContract):
    @pytest.fixture
    def adapter(self) -> Any:
        return _stub(PayUFakeWire(key=KEY, salt=SALT))

    @staticmethod
    def _fake(adapter: Any) -> PayUFakeWire:
        fake: PayUFakeWire = adapter._wire
        return fake

    def fresh(self, adapter: Any) -> Any:
        return _stub(self._fake(adapter))

    def rejecting(self, adapter: Any) -> Any | None:
        return _stub(self._fake(adapter), salt="dev-only-wrong-salt")


class PayUOverHttp:
    """An `httpx.MockTransport` handler that is PayU's merchant API: the form POST is handed to a
    `PayUFakeWire` that checks every command's hash, and its JSON is answered with HTTP 200 —
    PayU's way, refusals included."""

    def __init__(self) -> None:
        self.fake = PayUFakeWire(key=KEY, salt=SALT)
        self.fake.issued = set()  # type: ignore[attr-defined]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.params.get("form") == "2"
        try:
            result = self.fake.request(request.method, request.url.path, content=request.content)
        except ProviderApiError as exc:
            return httpx.Response(exc.status_code or 400, text="not found")
        return httpx.Response(200, json=result)


class _HttpCounting(_Counting):
    @property
    def issued_on(self) -> PayUFakeWire:
        return self.bridge.fake  # type: ignore[attr-defined,no-any-return]

    def ensure_order(self, **kwargs: Any) -> Any:
        order = PayUCollectionsProvider.ensure_order(self, **kwargs)
        self.issued_on.issued.add(order.provider_order_id)  # type: ignore[attr-defined]
        return order


def _http(bridge: PayUOverHttp, *, salt: str = SALT) -> Any:
    wire = PayUHttpWire(env="sandbox", transport=httpx.MockTransport(bridge))
    adapter = _HttpCounting(wire=wire, key=KEY, salt=salt, mode="sandbox")
    adapter.bridge = bridge  # type: ignore[attr-defined]
    return adapter


class TestThePayUHttpWire(_PayUHooks, CollectionsContract):
    @pytest.fixture
    def adapter(self) -> Any:
        return _http(PayUOverHttp())

    @staticmethod
    def _fake(adapter: Any) -> PayUFakeWire:
        fake: PayUFakeWire = adapter.bridge.fake
        return fake

    def fresh(self, adapter: Any) -> Any:
        return _http(adapter.bridge)

    def rejecting(self, adapter: Any) -> Any | None:
        return _http(adapter.bridge, salt="dev-only-wrong-salt")


# --------------------------------------------------------------------------- the formulas


def test_the_request_hash_is_payus_documented_sequence() -> None:
    fields = {
        "key": KEY,
        "txnid": "t1",
        "amount": "10.00",
        "productinfo": "iPhone",
        "firstname": "PayU User",
        "email": "test@gmail.com",
    }
    material = f"{KEY}|t1|10.00|iPhone|PayU User|test@gmail.com|||||||||||{SALT}"
    assert request_hash(fields, SALT) == hashlib.sha512(material.encode()).hexdigest()


def test_the_reverse_hash_is_payus_documented_sequence_with_additional_charges_first() -> None:
    fields = {
        "key": KEY,
        "txnid": "t1",
        "amount": "10.00",
        "productinfo": "iPhone",
        "firstname": "A",
        "email": "e@x.in",
        "status": "success",
        "udf1": "u1",
    }
    plain = f"{SALT}|success||||||||||u1|e@x.in|A|iPhone|10.00|t1|{KEY}"
    assert response_hash(fields, SALT) == hashlib.sha512(plain.encode()).hexdigest()
    charged = f"5.00|{plain}"
    assert (
        response_hash({**fields, "additional_charges": "5.00"}, SALT)
        == hashlib.sha512(charged.encode()).hexdigest()
    )


def test_the_txnid_is_25_characters_unique_and_stable() -> None:
    receipt = str(uuid.uuid4())
    assert txnid_for(receipt) == txnid_for(receipt) and len(txnid_for(receipt)) == 25
    assert txnid_for(str(uuid.UUID(int=2**128 - 1))) == "f5lxx1zz5pnorynqglhzmsp33"
    assert len({txnid_for(str(uuid.uuid4())) for _ in range(200)}) == 200


@pytest.mark.parametrize(
    ("minor", "wire"),
    [(100, "1.00"), (49_999, "499.99"), (5, "0.05"), (1_000_000_01, "1000000.01")],
)
def test_money_is_exact_rupees_on_the_wire_and_paise_back(minor: int, wire: str) -> None:
    assert rupees(minor) == wire and paise(wire) == minor


@pytest.mark.parametrize("bad", ["1.001", "abc", "", "-1.00", "NaN", True, None, "1e3x"])
def test_an_inexact_amount_is_no_amount(bad: Any) -> None:
    assert paise(bad) is None


# --------------------------------------------------------------------------- the form


def _payer(**kw: Any) -> CheckoutCustomer:
    return CheckoutCustomer(customer_id="c1", name="Asha Verma", phone="9876543210", **kw)


class TestTheForm:
    def test_it_posts_exact_rupees_to_payus_page_with_the_hash_and_return_page(self) -> None:
        fake = PayUFakeWire()
        receipt = str(uuid.uuid4())
        order = _stub(fake).ensure_order(
            receipt=receipt,
            amount_minor=49_999,
            currency="INR",
            notes={},
            payer=_payer(),
            return_url=RETURN,
        )
        assert order.provider_order_id == txnid_for(receipt)
        redirect = order.redirect
        assert redirect is not None and redirect.method == "POST"
        assert redirect.url == "https://test.payu.in/_payment"
        fields = dict(redirect.fields)
        assert fields["amount"] == "499.99" and fields["surl"] == fields["furl"] == RETURN
        assert fields["hash"] == request_hash(fields, SALT)
        assert SALT not in json.dumps(fields) and "hash" not in repr(order)

    def test_production_posts_to_production(self) -> None:
        adapter = PayUCollectionsProvider(
            wire=PayUFakeWire(), key=KEY, salt=SALT, mode="production"
        )
        order = ensure(adapter, str(uuid.uuid4()))
        assert order.redirect.url == "https://secure.payu.in/_payment"

    @pytest.mark.parametrize(
        ("over", "code"),
        [
            ({"currency": "USD"}, "currency_not_supported"),
            ({"amount_minor": 99}, "amount_below_minimum"),
            ({"return_url": None}, "return_url_required"),
            (
                {"return_url": "https://a-much-longer-platform-host.example/api/v1/pay/return"},
                "return_url_too_long",
            ),
            ({"payer": None}, "customer_phone_required"),
            ({"receipt": "not-a-uuid"}, "order_id_invalid"),
        ],
    )
    def test_what_payu_cannot_take_is_refused_before_any_request(
        self, over: dict[str, Any], code: str
    ) -> None:
        fake = PayUFakeWire()
        kwargs: dict[str, Any] = {
            "receipt": str(uuid.uuid4()),
            "amount_minor": 49_900,
            "currency": "INR",
            "notes": {},
            "payer": _payer(),
            "return_url": RETURN,
            **over,
        }
        with pytest.raises(ProviderRejected) as raised:
            _stub(fake).ensure_order(**kwargs)
        assert raised.value.code == code and list(fake.requests) == []

    @pytest.mark.parametrize(
        ("status", "unmapped"), [("success", "captured"), ("pending", "pending")]
    )
    def test_a_transaction_payu_holds_a_payment_or_a_pending_attempt_on_is_not_handed_out(
        self, status: str, unmapped: str
    ) -> None:
        fake = PayUFakeWire()
        receipt = str(uuid.uuid4())
        fake.pay(txnid_for(receipt), 49_900, status=status, unmapped=unmapped)
        with pytest.raises(ProviderRejected) as raised:
            ensure(_stub(fake), receipt)
        assert raised.value.code == "order_not_payable"

    def test_after_a_failed_attempt_the_same_transaction_can_be_paid_again(self) -> None:
        fake = PayUFakeWire()
        receipt = str(uuid.uuid4())
        fake.pay(txnid_for(receipt), 49_900, status="failure", unmapped="failed")
        assert ensure(_stub(fake), receipt).provider_order_id == txnid_for(receipt)

    def test_a_transaction_for_another_amount_is_refused(self) -> None:
        fake = PayUFakeWire()
        receipt = str(uuid.uuid4())
        fake.pay(txnid_for(receipt), 10_000, status="failure", unmapped="failed")
        with pytest.raises(ProviderRejected) as raised:
            ensure(_stub(fake), receipt)
        assert raised.value.code == "order_amount_mismatch"

    def test_a_closed_page_is_no_observation_and_a_failure_carries_payus_code(self) -> None:
        fake = PayUFakeWire()
        txnid = txnid_for(str(uuid.uuid4()))
        adapter = _stub(fake)
        fake.pay(txnid, 49_900, status="failure", unmapped="userCancelled")
        assert adapter.order_payments(order_id=txnid) == []
        fake.pay(txnid, 49_900, status="failure", unmapped="failed", error="E325")
        (failed,) = adapter.order_payments(order_id=txnid)
        assert (failed.status, failed.error_code, failed.amount_minor) == ("failed", "E325", 49_900)

    def test_wrong_credentials_are_misconfigured(self) -> None:
        with pytest.raises(ProviderMisconfigured):
            _stub(PayUFakeWire(key=KEY, salt=SALT), salt="dev-only-wrong").probe()

    def test_a_refund_token_fits_payus_23_characters_and_a_reuse_finds_the_same_refund(
        self,
    ) -> None:
        fake = PayUFakeWire()
        adapter = _stub(fake)
        txnid = txnid_for(str(uuid.uuid4()))
        mihpayid = fake.pay(txnid, 49_900)
        token = domain.refund_idempotency_key(uuid.uuid4(), uuid.uuid4(), "k", "payu")
        assert len(token) == 23
        args: dict[str, Any] = {
            "idempotency_key": token,
            "provider_payment_id": mihpayid,
            "amount_minor": 10_001,
            "currency": "INR",
            "provider_order_id": txnid,
        }
        first = adapter.refund(**args)
        assert (first.state, fake.refunds[token]["Amount"]) == ("created", "100.01")
        assert adapter.refund(**args).provider_refund_id == token and len(fake.refunds) == 1
        with pytest.raises(ProviderRejected) as raised:
            adapter.refund(**{**args, "amount_minor": 20_000})
        assert raised.value.code == "refund_amount_mismatch"


# --------------------------------------------------------------------------- webhooks


class TestWebhooks:
    def test_a_success_is_captured_in_paise_bound_to_payus_id_and_keeps_no_person(self) -> None:
        adapter = _stub(PayUFakeWire())
        body = paid(mihpayid="403993715599")
        assert adapter.verify_webhook(headers={}, body=body) is True
        event = adapter.parse_webhook(body=body)
        assert event.outcome_state == "captured" and event.provider_payment_id == "403993715599"
        assert (event.amount_minor, event.currency) == (49_900, "INR")
        assert derive_event_id(event) is not None
        stored = json.dumps(adapter.webhook_facts(event).stored)
        for kept_out in ("buyer@example.com", "9876543210", "Asha", "okaxis", "1111", "hash"):
            assert kept_out not in stored

    def test_an_unmapped_status_can_withhold_an_outcome_but_never_invent_one(self) -> None:
        adapter = _stub(PayUFakeWire())
        body = webhook(
            {"txnid": "0" * 25, "status": "success", "unmappedstatus": "pending", "amount": "1.00"}
        )
        assert adapter.parse_webhook(body=body).outcome_state == "unknown"

    def test_a_refund_webhook_carries_no_hash_and_never_verifies(self) -> None:
        body = json.dumps(
            {
                "action": "refund",
                "status": "success",
                "token": "r" + "a" * 22,
                "amt": "10.00",
                "key": KEY,
            }
        ).encode()
        assert _stub(PayUFakeWire()).verify_webhook(headers={}, body=body) is False

    def test_another_merchants_key_or_a_split_payment_never_verifies(self) -> None:
        adapter = _stub(PayUFakeWire())
        fields = dict(urllib.parse.parse_qsl(paid().decode()))
        other = {**fields, "key": "OTHERkey01"}
        other["hash"] = response_hash(other, SALT)
        assert (
            adapter.verify_webhook(headers={}, body=urllib.parse.urlencode(other).encode()) is False
        )
        split = urllib.parse.urlencode({**fields, "splitInfo": "{}"}).encode()
        assert adapter.verify_webhook(headers={}, body=split) is False
