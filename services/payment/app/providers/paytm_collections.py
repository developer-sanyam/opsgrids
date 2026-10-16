"""A merchant's Paytm Payments account (Standard Checkout: Initiate Transaction, then the hosted
payment page), behind the standalone `CollectionsProvider` seam (ADR-0033).

Written against Paytm's public reference (paytmpayments.com/docs: "Initiate Transaction API",
"Show Payment Page", "Transaction Status API", "Refund API", "Refund Status API", "Payment Status"
webhook, "Checksum Logic") and its official `paytmchecksum` library (1.7.0), whose algorithm
`generate_signature` / `verify_signature` reproduce exactly: `AES-128/256-CBC(key, iv="@@@@&&&&####
$$$$", PKCS7(sha256_hex("<params>|<salt>") + salt))`, base64, with a random 4-character salt.

* **The Order** is Paytm's: `POST {theia}/theia/api/v1/initiateTransaction?mid=&orderId=`, its JSON
  `body` signed (the signature over `json.dumps(body)`, sent as `{"body": …, "head":
  {"signature": …}}` — byte for byte what Paytm's own samples send). `orderId` is the collection's
  id without hyphens (32 alphanumerics; Paytm allows 50); `custId` is pseudonymous; no customer
  detail is sent. Paytm answers a `txnToken` (valid 15 minutes): the browser's handle is a form
  `POST` of `mid`, `orderId` and `txnToken` to `showPaymentPage` — in memory only, fetched again on
  every (re)open (Paytm documents a repeat initiate as `0002 Success Idempotent`). Looked up first
  (Transaction Status): a captured Order is never initiated again; one Paytm does not hold is
  created; a recorded Order is never re-minted under another id.
* **Payments**: Transaction Status (`/v3/order/status`) reports the Order's latest attempt:
  `TXN_SUCCESS` is captured, `TXN_FAILURE` failed (with its `resultCode`), `PENDING` and "no
  record" are no observation.
* **Refunds**: `/refund/apply` with our `refId`. `PENDING` (601) is `created`. A reused `refId`
  (617 "already raised", 629 "already successful", 671 "retry not supported with same refund id")
  is looked up with Refund Status (`/v2/refund/status`): the same refund for the same amount is
  used. Paytm's refund webhooks are JSON whose signature covers a body Paytm re-serialises (and its
  failure sample carries none), so they are refused, failing closed; settlement is not learned
  automatically yet.
* **Webhooks**: the Payment Status webhook (and the browser callback, which the shared return page
  never reads) is form-encoded with `CHECKSUMHASH` over **every** other field (sorted by name,
  `|`-joined). Verified by decrypting it with the merchant key and recomputing, in constant time,
  failing closed on anything unreadable; another merchant's `MID` is refused. What the inbox
  keeps is an allow-list: ids, status, code, rupee amount, date — never a bank, card or VPA field.
* **Probe**: Transaction Status of an Order nobody made: Paytm checks the signature and the `MID`
  first (330, 335 — `ProviderMisconfigured`), so "invalid order id" proves the credentials.

Two Paytm hosts: the checkout one (`secure[stage].paytmpayments.com`, `/theia/…`) and the API one
(`securegw[-stage].paytmpayments.in`). Money: rupee strings with two decimals on the wire, integer
paise everywhere else, converted exactly. INR only, at least 100 paise.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import string
import urllib.parse
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.providers.base import (
    CheckoutCustomer,
    CheckoutRedirect,
    CollectionWebhookFacts,
    CredentialsMode,
    MalformedWebhook,
    OrderResult,
    PaymentSnapshot,
    RefundResult,
    WebhookEvent,
)
from app.providers.payu_collections import paise, rupees
from app.providers.wire import (
    WIRE_REQUEST_TIMEOUT_SECONDS,
    HttpWire,
    ProviderMisconfigured,
    ProviderRejected,
    ProviderTransient,
    ProviderWire,
)

PROVIDER = "paytm"

#: Paytm's checkout host (`/theia/…`) and API host, by environment. Platform configuration.
PAYTM_CHECKOUT_BASE: dict[str, str] = {
    "sandbox": "https://securestage.paytmpayments.com",
    "production": "https://secure.paytmpayments.com",
}
PAYTM_API_BASE: dict[str, str] = {
    "sandbox": "https://securegw-stage.paytmpayments.in",
    "production": "https://securegw.paytmpayments.in",
}
INITIATE_PATH = "/theia/api/v1/initiateTransaction"
SHOW_PAYMENT_PATH = "/theia/api/v1/showPaymentPage"
STATUS_PATH = "/v3/order/status"
REFUND_PATH = "/refund/apply"
REFUND_STATUS_PATH = "/v2/refund/status"

WEBHOOK_EVENTS: tuple[str, ...] = ("Payment Status",)
CHECKSUM_FIELD = "CHECKSUMHASH"
MIN_AMOUNT_MINOR = 100
#: The status lookup and the initiate: the most requests one collections call makes.
REQUESTS_PER_CALL = 2

#: The result codes for an Order Paytm does not hold, and for credentials it refuses.
_NO_ORDER_CODES = frozenset({"331", "334"})
_CREDENTIAL_CODES = frozenset({"330", "335"})
#: A refund `refId` already used: look it up instead.
_REFUND_REPEAT_CODES = frozenset({"617", "629", "671"})

_IV = b"@@@@&&&&####$$$$"
_SALT_CHARS = string.ascii_letters + string.digits
_ORDER_ID = re.compile(r"^[0-9a-f]{32}$")
_REFUND_ID = re.compile(r"^[A-Za-z0-9]{3,50}$")
_IST = timezone(timedelta(hours=5, minutes=30))
_CODE_MAX = 64
_REFUND_STATES: dict[str, str] = {
    "TXN_SUCCESS": "processed",
    "PENDING": "created",
    "TXN_FAILURE": "failed",
}
_STORED_FIELDS = (
    "ORDERID",
    "TXNID",
    "STATUS",
    "RESPCODE",
    "TXNAMOUNT",
    "CURRENCY",
    "TXNDATE",
    "PAYMENTMODE",
)


# --------------------------------------------------------------------------- the checksum


def _cipher(key: str) -> Cipher[modes.CBC]:
    return Cipher(algorithms.AES(key.encode()), modes.CBC(_IV))


def _hash(params: str, salt: str) -> str:
    return hashlib.sha256(f"{params}|{salt}".encode()).hexdigest() + salt


def generate_signature(params: str, key: str, *, salt: str | None = None) -> str:
    """`paytmchecksum.generateSignatureByString`: a random 4-character salt, the salted SHA-256,
    PKCS7-padded and AES-CBC-encrypted under the merchant key, base64."""
    salt = salt if salt is not None else "".join(secrets.choice(_SALT_CHARS) for _ in range(4))
    padder = padding.PKCS7(128).padder()
    plain = padder.update(_hash(params, salt).encode()) + padder.finalize()
    encryptor = _cipher(key).encryptor()
    return base64.b64encode(encryptor.update(plain) + encryptor.finalize()).decode()


def verify_signature(params: str, key: str, checksum: str | None) -> bool:
    """`paytmchecksum.verifySignatureByString`, failing closed: an absent, non-base64, wrongly
    padded or undecodable checksum, a key AES cannot take, or any mismatch is False."""
    if not checksum or not key:
        return False
    try:
        raw = base64.b64decode(checksum.encode("ascii"), validate=True)
        decryptor = _cipher(key).decryptor()
        padded = decryptor.update(raw) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        decrypted = (unpadder.update(padded) + unpadder.finalize()).decode("ascii")
    except (ValueError, UnicodeError, binascii.Error):
        return False
    if len(decrypted) < 4:
        return False
    return hmac.compare_digest(decrypted.encode(), _hash(params, decrypted[-4:]).encode())


def params_string(fields: Mapping[str, str]) -> str:
    """`paytmchecksum.getStringByParams`: every field but the checksum, sorted by name, its value
    (`"null"` read as empty) joined by `|`."""
    return "|".join(
        "" if fields[k].lower() == "null" else fields[k]
        for k in sorted(fields)
        if k != CHECKSUM_FIELD
    )


def order_id_for(receipt: str) -> str:
    """The collection's id as Paytm's `orderId`: its 32 hex digits, no hyphens."""
    return uuid.UUID(receipt).hex


def refund_key(digest: str) -> str:
    """Paytm's `refId` for a refund whose identity hashes to `digest`: `rfd` and 36 hex."""
    return f"rfd{digest[:36]}"


def _text(value: Any, limit: int = 128) -> str | None:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value).strip()
        if text and text.isprintable():
            return text[:limit]
    return None


def _epoch(value: Any) -> int | None:
    """Paytm's `TXNDATE` / `txnDate` (`2020-07-24 15:13:02.0`, India time) as epoch seconds."""
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.strptime(text.split(".")[0], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=_IST).timestamp())


def _form(body: bytes) -> dict[str, str] | None:
    try:
        pairs = urllib.parse.parse_qsl(
            body.decode("utf-8"), keep_blank_values=True, strict_parsing=True
        )
    except (UnicodeDecodeError, ValueError):
        return None
    fields = dict(pairs)
    return fields if len(fields) == len(pairs) else None


def _outcome(status: str, code: str) -> str:
    if status == "TXN_SUCCESS":
        return "captured"
    if status == "TXN_FAILURE" and code not in _NO_ORDER_CODES:
        return "failed"
    return "unknown"


# --------------------------------------------------------------------------- the HTTP wire


class _PaytmApiWire(HttpWire):
    provider = PROVIDER

    def _headers(self) -> dict[str, str]:
        return {"accept": "application/json", "content-type": "application/json"}


class PaytmHttpWire:
    """The one class in this service that opens a socket to Paytm: `/theia/…` goes to the checkout
    host, everything else to the API host."""

    provider = PROVIDER

    def __init__(
        self,
        *,
        env: Literal["sandbox", "production"],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._checkout = _PaytmApiWire(base_url=PAYTM_CHECKOUT_BASE[env], transport=transport)
        self._api = _PaytmApiWire(base_url=PAYTM_API_BASE[env], transport=transport)

    def __repr__(self) -> str:
        return "PaytmHttpWire()"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        wire = self._checkout if path.startswith("/theia/") else self._api
        return wire.request(
            method, path, params=params, json=json, content=content, headers=headers
        )


# --------------------------------------------------------------------------- the adapter


class PaytmCollectionsProvider:
    """One merchant account's Paytm adapter, built per call from that account's opened keys."""

    name = PROVIDER
    #: One connect + read timeout per request, two at worst: 2 x 7s = 14s.
    request_timeouts_seconds: tuple[float, ...] = (
        WIRE_REQUEST_TIMEOUT_SECONDS,
    ) * REQUESTS_PER_CALL

    def __init__(
        self,
        *,
        wire: ProviderWire,
        mid: str,
        merchant_key: str,
        mode: Literal["stub", "sandbox", "production"],
    ) -> None:
        self._wire = wire
        self._mid = mid
        #: The merchant key: it signs every request and verifies every webhook. Never in a repr,
        #: a log or a stored row.
        self._key = merchant_key
        self._mode = mode

    def __repr__(self) -> str:  # never a credential
        return f"PaytmCollectionsProvider(mode={self._mode!r})"

    @property
    def is_live(self) -> bool:
        return self._mode != "stub"

    @property
    def credentials_mode(self) -> CredentialsMode:
        if self._mode == "stub":
            return "stub"
        return "live" if self._mode == "production" else "test"

    @property
    def _env(self) -> Literal["sandbox", "production"]:
        return "production" if self._mode == "production" else "sandbox"

    # ------------------------------------------------------------------ the API

    def _call(
        self, path: str, body: dict[str, Any], *, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """One signed Paytm call; its `body` back. The signature is over `json.dumps(body)` and the
        request is sent exactly as Paytm's samples send it. A refused signature or `MID` is the
        merchant's credentials."""
        body_text = json.dumps(body)
        signature = generate_signature(body_text, self._key)
        content = f'{{"body": {body_text}, "head": {{"signature": "{signature}"}}}}'.encode()
        answer = self._wire.request("POST", path, params=params, content=content)
        result = answer.get("body") if isinstance(answer, dict) else None
        if not isinstance(result, dict):
            raise ProviderTransient(PROVIDER, 200, "unreadable_response")
        if self._info(result)[1] in _CREDENTIAL_CODES:
            raise ProviderMisconfigured(PROVIDER, 200, "credentials_rejected")
        return result

    @staticmethod
    def _info(result: Mapping[str, Any]) -> tuple[str, str]:
        info = result.get("resultInfo")
        if not isinstance(info, dict):
            return "", ""
        return str(info.get("resultStatus") or "").upper(), str(info.get("resultCode") or "")

    def _status(self, order_id: str) -> dict[str, Any] | None:
        """The Order's latest attempt as Paytm reports it, or None when it holds none."""
        result = self._call(STATUS_PATH, {"mid": self._mid, "orderId": order_id})
        status, code = self._info(result)
        if status == "NO_RECORD_FOUND" or code in _NO_ORDER_CODES:
            return None
        return result

    # ------------------------------------------------------------------ orders

    def ensure_order(
        self,
        *,
        receipt: str,
        amount_minor: int,
        currency: str,
        notes: dict[str, str],
        payer: CheckoutCustomer | None = None,
        expires_at: datetime | None = None,
        notify_url: str | None = None,
        recorded_order_id: str | None = None,
        return_url: str | None = None,
    ) -> OrderResult:
        """The Order for `receipt` — looked up first, initiated only if Paytm holds no captured
        one — with the form that opens Paytm's payment page. Refused before any request: not INR,
        under 100 paise, no return URL, or a receipt that is not a collection id; a recorded Order
        that is not this receipt's is refused; one found for another amount is refused."""
        if currency.upper() != "INR":
            raise ProviderRejected(PROVIDER, 0, "currency_not_supported")
        if amount_minor < MIN_AMOUNT_MINOR:
            raise ProviderRejected(PROVIDER, 0, "amount_below_minimum")
        if not return_url:
            raise ProviderRejected(PROVIDER, 0, "return_url_required")
        try:
            order_id = order_id_for(receipt)
        except ValueError:
            raise ProviderRejected(PROVIDER, 0, "order_id_invalid") from None
        if recorded_order_id is not None and recorded_order_id != order_id:
            raise ProviderTransient(PROVIDER, 0, "recorded_order_not_found")

        found = self._status(order_id)
        if found is not None:
            held = paise(found.get("txnAmount"))
            if held is not None and held != amount_minor:
                raise ProviderRejected(PROVIDER, 200, "order_amount_mismatch")
            if self._info(found)[0] == "TXN_SUCCESS":
                raise ProviderRejected(PROVIDER, 200, "order_not_payable")

        result = self._call(
            INITIATE_PATH,
            {
                "requestType": "Payment",
                "mid": self._mid,
                "orderId": order_id,
                "callbackUrl": return_url,
                "txnAmount": {"value": rupees(amount_minor), "currency": "INR"},
                "userInfo": {"custId": f"c{order_id}"},
            },
            params={"mid": self._mid, "orderId": order_id},
        )
        status, code = self._info(result)
        token = result.get("txnToken")
        if status == "U" or (status == "S" and not isinstance(token, str)):
            raise ProviderTransient(PROVIDER, 200, code or "initiate_unknown")
        if status != "S" or not isinstance(token, str):
            raise ProviderRejected(PROVIDER, 200, (code or "initiate_refused")[:_CODE_MAX])
        query = urllib.parse.urlencode({"mid": self._mid, "orderId": order_id})
        return OrderResult(
            provider=PROVIDER,
            provider_order_id=order_id,
            receipt=receipt,
            redirect=CheckoutRedirect(
                url=f"{PAYTM_CHECKOUT_BASE[self._env]}{SHOW_PAYMENT_PATH}?{query}",
                method="POST",
                fields={"mid": self._mid, "orderId": order_id, "txnToken": token},
            ),
        )

    def find_order(self, *, receipt: str) -> OrderResult | None:
        """The lookup alone (Transaction Status), never an initiate."""
        try:
            order_id = order_id_for(receipt)
        except ValueError:
            return None
        if self._status(order_id) is None:
            return None
        return OrderResult(provider=PROVIDER, provider_order_id=order_id, receipt=receipt)

    def order_payments(self, *, order_id: str) -> list[PaymentSnapshot]:
        """The Order's latest attempt, in the neutral vocabulary; a pending one is not reported."""
        if not _ORDER_ID.fullmatch(order_id):
            return []
        found = self._status(order_id)
        if found is None:
            return []
        status, code = self._info(found)
        outcome = _outcome(status, code)
        payment_id = _text(found.get("txnId"))
        amount = paise(found.get("txnAmount"))
        if outcome == "unknown" or payment_id is None or amount is None:
            return []
        return [
            PaymentSnapshot(
                provider_payment_id=payment_id,
                status=outcome,
                amount_minor=amount,
                currency="INR",
                error_code=code[:_CODE_MAX] if outcome == "failed" else None,
                created_at=_epoch(found.get("txnDate")),
            )
        ]

    # ------------------------------------------------------------------ refunds

    def refund(
        self,
        *,
        idempotency_key: str,
        provider_payment_id: str,
        amount_minor: int,
        currency: str,
        provider_order_id: str | None = None,
    ) -> RefundResult:
        """`/refund/apply` with our `refId`; a reused `refId` is looked up (Refund Status) and the
        same refund for the same amount used."""
        if not provider_order_id:
            raise ProviderRejected(PROVIDER, 0, "order_required")
        if not _REFUND_ID.fullmatch(idempotency_key):
            raise ProviderRejected(PROVIDER, 0, "refund_id_invalid")
        if currency.upper() != "INR":
            raise ProviderRejected(PROVIDER, 0, "currency_not_supported")
        if amount_minor < MIN_AMOUNT_MINOR:
            raise ProviderRejected(PROVIDER, 0, "amount_below_minimum")
        result = self._call(
            REFUND_PATH,
            {
                "mid": self._mid,
                "txnType": "REFUND",
                "orderId": provider_order_id,
                "txnId": provider_payment_id,
                "refId": idempotency_key,
                "refundAmount": rupees(amount_minor),
            },
        )
        status, code = self._info(result)
        if code in _REFUND_REPEAT_CODES:
            result = self._call(
                REFUND_STATUS_PATH,
                {"mid": self._mid, "orderId": provider_order_id, "refId": idempotency_key},
            )
            status, code = self._info(result)
            found = paise(result.get("refundAmount"))
            if found is not None and found != amount_minor:
                raise ProviderRejected(PROVIDER, 200, "refund_amount_mismatch")
        elif status == "TXN_FAILURE":
            raise ProviderRejected(PROVIDER, 200, (code or "refund_refused")[:_CODE_MAX])
        return RefundResult(
            provider=PROVIDER,
            provider_refund_id=idempotency_key,
            state=_REFUND_STATES.get(status, "created"),
            amount_minor=amount_minor,
            currency="INR",
        )

    # ------------------------------------------------------------------ webhooks

    def verify_webhook(self, *, headers: Mapping[str, str] | None = None, body: bytes) -> bool:
        """`CHECKSUMHASH` over every other field, failing closed: not a form, another `MID`, no
        checksum, an unreadable one or any mismatch is False — and so is a JSON refund webhook."""
        fields = _form(body)
        if not fields or fields.get("MID") != self._mid:
            return False
        return verify_signature(params_string(fields), self._key, fields.get(CHECKSUM_FIELD))

    def webhook_delivery_id(self, headers: Mapping[str, str]) -> str | None:
        return None  # Paytm documents none: the body's own identity is derived

    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        """A verified Paytm Payment Status webhook (form-encoded; the stored allow-list, JSON,
        re-parses too) in the collections vocabulary."""
        fields: dict[str, Any] | None
        try:
            loaded = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            fields = _form(body)
        else:
            fields = loaded if isinstance(loaded, dict) else None
        if not fields or not _text(fields.get("ORDERID")):
            raise MalformedWebhook("the webhook body is not a Paytm payment status")
        status = str(fields.get("STATUS") or "").upper()
        code = str(fields.get("RESPCODE") or "")
        outcome = _outcome(status, code)
        payment_id = _text(fields.get("TXNID"))
        stored = {k: str(fields[k])[:128] for k in _STORED_FIELDS if _text(fields.get(k))}
        currency = _text(fields.get("CURRENCY"))
        return WebhookEvent(
            provider=PROVIDER,
            provider_event_id="",
            event_name=f"payment.{status.lower() or 'unknown'}"[:80],
            outcome_state=outcome,
            organization_id=None,
            charge_id=None,
            provider_payment_id=payment_id if outcome != "unknown" else None,
            failure_reason=code[:_CODE_MAX] if outcome == "failed" and code else None,
            raw=stored,
            provider_order_id=_text(fields.get("ORDERID")),
            entity_id=payment_id or _text(fields.get("ORDERID")),
            created_at=_epoch(fields.get("TXNDATE")),
            amount_minor=paise(fields.get("TXNAMOUNT")),
            currency=currency.upper() if currency else None,
        )

    def webhook_facts(self, event: WebhookEvent) -> CollectionWebhookFacts:
        outcome = event.outcome_state if event.outcome_state != "unknown" else None
        code = _text(event.raw.get("RESPCODE"), _CODE_MAX)
        return CollectionWebhookFacts(
            outcome=outcome,
            refund=None,
            failure_code=code if outcome == "failed" else None,
            stored=event.raw,
        )

    # ------------------------------------------------------------------ verification

    def probe(self) -> None:
        """Transaction Status of an Order nobody made: 330/335 is `ProviderMisconfigured`, "no
        such order" proves the credentials."""
        self._status(uuid.uuid4().hex)
