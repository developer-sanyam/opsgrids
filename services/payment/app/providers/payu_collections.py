"""A merchant's PayU account (PayU Hosted Checkout), behind the standalone `CollectionsProvider`
seam (ADR-0033).

Written against PayU's public reference (docs.payu.in: "Collect Payment API - PayU Hosted
Checkout", "Generate Hash", "Additional Info for Payment APIs", "Verify Payment API", "Refund
Transaction API", "Get All Refunds from Transaction ID", "Webhook Events and Sample Payloads").

* **There is no server-side Order.** The customer's browser form-POSTs the payment request to
  PayU's `_payment` page; nothing exists at PayU until it does. So `ensure_order` makes no create:
  it asks PayU (`verify_payment`) whether the transaction id already has an attempt — a captured,
  authorised or still-pending one is never handed out again — and returns the form
  (`CheckoutRedirect`, `POST`) with the request hash
  `sha512(key|txnid|amount|productinfo|firstname|email|udf1..udf5||||||SALT)`, computed here
  because the salt never leaves the server. Held in memory for one response, never stored.
* **`txnid` is the collection's id in base 36** — 25 characters, PayU's documented ceiling — and
  is what this platform records as the Order id. `surl`/`furl` are the shared return page (PayU
  documents a 50-character ceiling, refused before anything is built when the platform's address
  is longer).
* **Payments**: `verify_payment` reports the transaction id's latest attempt: `success` with
  `unmappedstatus=captured` is captured, `auth` authorised, `failure` failed — except a customer
  closing the page (`userCancelled`, `bounced`), which is no observation, like Cashfree's
  `USER_DROPPED`; `pending`/`initiated`/`in progress` are no observation either.
* **Refunds**: `cancel_refund_transaction` (`var1` the PayU id, `var2` our token — PayU allows 23
  characters, so `r` and 22 hex — `var3` the amount). PayU answers a reused token "token already
  used or request pending", which is followed by `getAllRefundsFromTxnIds`: the refund with our
  token, for the same amount, is used. A queued refund is `created`. PayU's refund webhooks carry
  **no hash at all**, so they are refused (fail closed) and a refund's settlement is not learned
  automatically yet (see the PR's deferred list).
* **Webhooks** (payment `Successful` / `Failed`, form-encoded, the same fields as the browser
  return): the reverse hash `sha512([additional_charges|]SALT|status||||||udf5|udf4|udf3|udf2|
  udf1|email|firstname|productinfo|amount|txnid|key)`, constant time, failing closed. It covers
  those fields only — not `mihpayid`, `unmappedstatus` or anything else — so the outcome is read
  from `status` (covered) and the amount from `amount` (covered); `unmappedstatus` only ever
  *withholds* an outcome. What the inbox keeps is an allow-list: ids, statuses, rupee amount,
  PayU's error code — never a name, email, phone, card, VPA or bank field.
* **Probe**: `verify_payment` of a transaction nobody made — PayU authenticates the request by its
  hash, so "not found" proves the key and salt; "invalid hash"/"invalid key" is
  `ProviderMisconfigured`.

Money: rupees with two decimals on the wire (`"499.00"`), integer paise everywhere else, converted
here exactly (no float). INR only, at least 100 paise.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import urllib.parse
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx

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
from app.providers.wire import (
    WIRE_REQUEST_TIMEOUT_SECONDS,
    HttpWire,
    ProviderMisconfigured,
    ProviderRejected,
    ProviderTransient,
    ProviderWire,
)

PROVIDER = "payu"

#: PayU's hosted payment page and its merchant API host, by environment. Platform configuration.
PAYU_PAYMENT_URL: dict[str, str] = {
    "sandbox": "https://test.payu.in/_payment",
    "production": "https://secure.payu.in/_payment",
}
PAYU_API_BASE: dict[str, str] = {
    "sandbox": "https://test.payu.in",
    "production": "https://info.payu.in",
}
POSTSERVICE_PATH = "/merchant/postservice.php"

#: What a merchant subscribes to on the webhook it creates in PayU's dashboard (Payments type).
WEBHOOK_EVENTS: tuple[str, ...] = ("Successful", "Failed")
#: PayU's documented ceilings: `txnid` 25, `surl`/`furl` 50, the refund `token` 23, `firstname`
#: 20 in the test environment (60 in production), `productinfo` 100, `email` 50.
TXNID_LENGTH = 25
RETURN_URL_MAX = 50
REFUND_TOKEN_MAX = 23
FIRSTNAME_MAX = 20
EMAIL_MAX = 50
PRODUCT_INFO = "Order payment"
MIN_AMOUNT_MINOR = 100
#: A verify (the lookup) is the only request `ensure_order` makes; a refund is the create and the
#: lookup after a reused token.
REQUESTS_PER_CALL = 2

_TXNID = re.compile(r"^[0-9a-z]{25}$")
_REFUND_TOKEN = re.compile(r"^[A-Za-z0-9]{1,23}$")
_IST = timezone(timedelta(hours=5, minutes=30))
_CODE_MAX = 64
_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
#: A customer closing PayU's page: no observation at all, never a failure.
_ABANDONED = frozenset({"usercancelled", "bounced"})
_PENDING = frozenset({"pending", "initiated", "in progress"})
_REFUND_STATES: dict[str, str] = {"success": "processed", "failure": "failed", "failed": "failed"}
#: What the inbox keeps of a PayU payment webhook.
_STORED_FIELDS = (
    "mihpayid",
    "txnid",
    "status",
    "unmappedstatus",
    "amount",
    "addedon",
    "mode",
    "error",
)


def txnid_for(receipt: str) -> str:
    """The collection's id as PayU's `txnid`: its 128 bits in base 36, zero-padded to exactly 25
    characters (36**25 > 2**128) — unique, stable, and inside PayU's 25-character ceiling."""
    value = uuid.UUID(receipt).int
    digits = ""
    while value:
        value, rest = divmod(value, 36)
        digits = _DIGITS[rest] + digits
    return digits.rjust(TXNID_LENGTH, "0")


def refund_key(digest: str) -> str:
    """PayU's refund `token` for a refund whose identity hashes to `digest`: `r` and 22 hex — the
    23 characters PayU allows."""
    return f"r{digest[:22]}"


def rupees(amount_minor: int) -> str:
    """Integer paise as PayU's amount: rupees with exactly two decimals, no float on the way."""
    return f"{amount_minor // 100}.{amount_minor % 100:02d}"


def paise(value: Any) -> int | None:
    """A PayU rupee amount (`"499.00"`, `"1000"`, `2.00`) as integer paise, or None when it is not
    an exact amount with at most two decimals."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
        return None
    return int(amount * 100)


def _sha512(*parts: str) -> str:
    return hashlib.sha512("|".join(parts).encode()).hexdigest()


def request_hash(fields: Mapping[str, str], salt: str) -> str:
    """`sha512(key|txnid|amount|productinfo|firstname|email|udf1..udf5||||||SALT)`."""
    return _sha512(
        *(fields.get(k, "") for k in ("key", "txnid", "amount", "productinfo", "firstname")),
        fields.get("email", ""),
        *(fields.get(f"udf{i}", "") for i in range(1, 6)),
        "",
        "",
        "",
        "",
        "",
        salt,
    )


def response_hash(fields: Mapping[str, str], salt: str) -> str:
    """The reverse hash PayU puts on a payment response or webhook:
    `sha512([additional_charges|]SALT|status||||||udf5|udf4|udf3|udf2|udf1|email|firstname|
    productinfo|amount|txnid|key)`."""
    parts = [
        salt,
        fields.get("status", ""),
        "",
        "",
        "",
        "",
        "",
        *(fields.get(f"udf{i}", "") for i in range(5, 0, -1)),
        *(fields.get(k, "") for k in ("email", "firstname", "productinfo", "amount", "txnid")),
        fields.get("key", ""),
    ]
    charges = fields.get("additional_charges")
    if charges:
        parts.insert(0, charges)
    return _sha512(*parts)


def command_hash(key: str, command: str, var1: str, salt: str) -> str:
    """`sha512(key|command|var1|salt)` — every merchant API call's hash."""
    return _sha512(key, command, var1, salt)


def _text(value: Any, limit: int = 128) -> str | None:
    if isinstance(value, str) and value.strip() and value.isprintable():
        return value.strip()[:limit]
    return None


def _epoch(addedon: Any) -> int | None:
    """PayU's `addedon` (`2026-03-04 14:46:14`, India time) as epoch seconds."""
    text = _text(addedon)
    if text is None:
        return None
    try:
        return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_IST).timestamp())
    except ValueError:
        return None


def _outcome(status: str, unmapped: str) -> str:
    """The collection outcome a PayU attempt asserts. `status` is what PayU's hash covers;
    `unmappedstatus` can only withhold an outcome (a closed page, a still-pending attempt)."""
    if unmapped in _ABANDONED or unmapped in _PENDING:
        return "unknown"
    if status == "success":
        return "authorized" if unmapped == "auth" else "captured"
    if status == "failure":
        return "failed"
    return "unknown"


def _form(body: bytes) -> dict[str, str] | None:
    """A form-encoded body as a flat dict (a repeated field makes it unreadable), or None."""
    try:
        text = body.decode("utf-8")
        pairs = urllib.parse.parse_qsl(text, keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        return None
    fields = dict(pairs)
    return fields if len(fields) == len(pairs) else None


# --------------------------------------------------------------------------- the HTTP wire


class PayUHttpWire(HttpWire):
    """The one class in this service that opens a socket to PayU's merchant API. Every call is a
    form POST to `postservice.php?form=2`; the hash, not a header, authenticates it."""

    provider = PROVIDER

    def __init__(
        self,
        *,
        env: Literal["sandbox", "production"],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url=PAYU_API_BASE[env], transport=transport)

    def _headers(self) -> dict[str, str]:
        return {"accept": "application/json"}


# --------------------------------------------------------------------------- the adapter


class PayUCollectionsProvider:
    """One merchant account's PayU adapter, built per call from that account's opened keys."""

    name = PROVIDER
    #: One connect + read timeout per request, two at worst: 2 x 7s = 14s.
    request_timeouts_seconds: tuple[float, ...] = (
        WIRE_REQUEST_TIMEOUT_SECONDS,
    ) * REQUESTS_PER_CALL

    def __init__(
        self,
        *,
        wire: ProviderWire,
        key: str,
        salt: str,
        mode: Literal["stub", "sandbox", "production"],
    ) -> None:
        self._wire = wire
        self._key = key
        #: The merchant's salt: it hashes every request and verifies every response. Never in a
        #: repr, a log or a stored row.
        self._salt = salt
        self._mode = mode

    def __repr__(self) -> str:  # never a credential
        return f"PayUCollectionsProvider(mode={self._mode!r})"

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

    # ------------------------------------------------------------------ the merchant API

    def _command(self, command: str, var1: str, **more: str) -> dict[str, Any]:
        """One `postservice` call. PayU answers HTTP 200 even when it refuses, so its body is read:
        a refused hash or key is the merchant's credentials (`ProviderMisconfigured`)."""
        form = {
            "key": self._key,
            "command": command,
            "var1": var1,
            **more,
            "hash": command_hash(self._key, command, var1, self._salt),
        }
        raw = self._wire.request(
            "POST",
            POSTSERVICE_PATH,
            params={"form": "2"},
            content=urllib.parse.urlencode(form).encode(),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        body = raw if isinstance(raw, dict) else {}
        message = str(body.get("msg") or "").lower()
        if "invalid hash" in message or "invalid key" in message:
            raise ProviderMisconfigured(PROVIDER, 200, "credentials_rejected")
        return body

    def _latest(self, txnid: str) -> dict[str, Any] | None:
        """The transaction id's latest attempt as PayU reports it, or None when it has none."""
        body = self._command("verify_payment", txnid)
        details = body.get("transaction_details")
        entry = details.get(txnid) if isinstance(details, dict) else None
        if not isinstance(entry, dict) or str(entry.get("status") or "").lower() in (
            "",
            "not found",
        ):
            return None
        return entry

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
        """The form that pays `receipt`'s transaction at PayU. Refused before any request: not
        INR, under 100 paise, no return URL or one over PayU's 50 characters, no payer phone, or a
        receipt that is not a collection id. A recorded Order that is not this receipt's
        transaction is refused; a transaction PayU already holds a captured, authorised or pending
        attempt on is never handed out again, nor one for another amount."""
        if currency.upper() != "INR":
            raise ProviderRejected(PROVIDER, 0, "currency_not_supported")
        if amount_minor < MIN_AMOUNT_MINOR:
            raise ProviderRejected(PROVIDER, 0, "amount_below_minimum")
        if not return_url:
            raise ProviderRejected(PROVIDER, 0, "return_url_required")
        if len(return_url) > RETURN_URL_MAX:
            raise ProviderRejected(PROVIDER, 0, "return_url_too_long")
        if payer is None or not payer.phone:
            raise ProviderRejected(PROVIDER, 0, "customer_phone_required")
        try:
            txnid = txnid_for(receipt)
        except ValueError:
            raise ProviderRejected(PROVIDER, 0, "order_id_invalid") from None
        if recorded_order_id is not None and recorded_order_id != txnid:
            raise ProviderTransient(PROVIDER, 0, "recorded_order_not_found")

        latest = self._latest(txnid)
        if latest is not None:
            held = paise(latest.get("transaction_amount") or latest.get("amt"))
            if held is not None and held != amount_minor:
                raise ProviderRejected(PROVIDER, 200, "order_amount_mismatch")
            outcome = _outcome(
                str(latest.get("status") or "").lower(),
                str(latest.get("unmappedstatus") or "").lower(),
            )
            if outcome in ("captured", "authorized") or (
                str(latest.get("unmappedstatus") or "").lower() in _PENDING
            ):
                raise ProviderRejected(PROVIDER, 200, "order_not_payable")

        fields = {
            "key": self._key,
            "txnid": txnid,
            "amount": rupees(amount_minor),
            "productinfo": PRODUCT_INFO,
            "firstname": (payer.name or "Customer").strip()[:FIRSTNAME_MAX] or "Customer",
            # Mandatory per PayU; sent empty when the order has none (sandbox to confirm).
            "email": (payer.email or "").strip()[:EMAIL_MAX],
            "phone": payer.phone,
            "surl": return_url,
            "furl": return_url,
        }
        fields["hash"] = request_hash(fields, self._salt)
        return OrderResult(
            provider=PROVIDER,
            provider_order_id=txnid,
            receipt=receipt,
            redirect=CheckoutRedirect(
                url=PAYU_PAYMENT_URL[self._env], method="POST", fields=fields
            ),
        )

    def find_order(self, *, receipt: str) -> OrderResult | None:
        """The transaction as PayU holds it — only once the customer has posted the form; never a
        create (PayU has none)."""
        try:
            txnid = txnid_for(receipt)
        except ValueError:
            return None
        if self._latest(txnid) is None:
            return None
        return OrderResult(provider=PROVIDER, provider_order_id=txnid, receipt=receipt)

    def order_payments(self, *, order_id: str) -> list[PaymentSnapshot]:
        """The transaction's latest attempt (all `verify_payment` reports), in the neutral
        vocabulary; an abandoned or pending one is not in the answer."""
        if not _TXNID.fullmatch(order_id):
            return []
        latest = self._latest(order_id)
        if latest is None:
            return []
        outcome = _outcome(
            str(latest.get("status") or "").lower(),
            str(latest.get("unmappedstatus") or "").lower(),
        )
        payment_id = _text(latest.get("mihpayid"))
        amount = paise(latest.get("transaction_amount") or latest.get("amt"))
        if outcome == "unknown" or payment_id is None or amount is None:
            return []
        return [
            PaymentSnapshot(
                provider_payment_id=payment_id,
                status=outcome,
                amount_minor=amount,
                currency="INR",
                error_code=_text(latest.get("error_code"), _CODE_MAX)
                if outcome == "failed"
                else None,
                created_at=_epoch(latest.get("addedon")),
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
        """`cancel_refund_transaction` with our token. A reused token ("token already used or
        request pending") is looked up by `getAllRefundsFromTxnIds`; the refund with our token for
        the same amount is used, anything else is refused."""
        if not provider_order_id:
            raise ProviderRejected(PROVIDER, 0, "order_required")
        if not _REFUND_TOKEN.fullmatch(idempotency_key):
            raise ProviderRejected(PROVIDER, 0, "refund_id_invalid")
        if currency.upper() != "INR":
            raise ProviderRejected(PROVIDER, 0, "currency_not_supported")
        if amount_minor < MIN_AMOUNT_MINOR:
            raise ProviderRejected(PROVIDER, 0, "amount_below_minimum")
        body = self._command(
            "cancel_refund_transaction",
            provider_payment_id,
            var2=idempotency_key,
            var3=rupees(amount_minor),
        )
        if str(body.get("status")) == "1":
            state = "created"
        elif "token already used" in str(body.get("msg") or "").lower():
            state = self._existing_refund(provider_order_id, idempotency_key, amount_minor)
        else:
            raise ProviderRejected(PROVIDER, 200, "refund_refused")
        return RefundResult(
            provider=PROVIDER,
            provider_refund_id=idempotency_key,
            state=state,
            amount_minor=amount_minor,
            currency="INR",
        )

    def _existing_refund(self, txnid: str, token: str, amount_minor: int) -> str:
        body = self._command("getAllRefundsFromTxnIds", txnid)
        details = body.get("Refund Details")
        refunds = [
            entry
            for group in (details.values() if isinstance(details, dict) else [])
            if isinstance(group, list)
            for entry in group
            if isinstance(entry, dict) and entry.get("RefundToken") == token
        ]
        if not refunds:
            # Pending at PayU under our token but not listed yet: try again later.
            raise ProviderTransient(PROVIDER, 200, "refund_not_listed")
        found = refunds[-1]
        if paise(found.get("Amount")) not in (None, amount_minor):
            raise ProviderRejected(PROVIDER, 200, "refund_amount_mismatch")
        return _REFUND_STATES.get(str(found.get("Status") or "").lower(), "created")

    # ------------------------------------------------------------------ webhooks

    def verify_webhook(self, *, headers: Mapping[str, str] | None = None, body: bytes) -> bool:
        """The reverse hash over the form's own fields, constant time, failing closed: not a form,
        another merchant's key, no hash, a split payment (a different hash), a non-ASCII hash or any
        mismatch is False. A refund webhook (JSON, no hash) is False."""
        fields = _form(body)
        if not fields or not self._salt or "splitInfo" in fields:
            return False
        given = fields.get("hash", "")
        if not given or fields.get("key") != self._key:
            return False
        try:
            presented = given.strip().lower().encode("ascii")
        except UnicodeEncodeError:
            return False
        expected = response_hash(fields, self._salt).encode("ascii")
        return hmac.compare_digest(expected, presented)

    def webhook_delivery_id(self, headers: Mapping[str, str]) -> str | None:
        return None  # PayU documents none: the body's own identity is derived

    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        """A verified PayU payment webhook (form-encoded; the stored allow-list, JSON, re-parses
        too) in the collections vocabulary."""
        fields: dict[str, Any] | None
        try:
            loaded = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            fields = _form(body)
        else:
            fields = loaded if isinstance(loaded, dict) else None
        if not fields or not _text(fields.get("txnid")):
            raise MalformedWebhook("the webhook body is not a PayU payment response")
        status = str(fields.get("status") or "").lower()
        outcome = _outcome(status, str(fields.get("unmappedstatus") or "").lower())
        payment_id = _text(fields.get("mihpayid"))
        stored = {k: fields[k] for k in _STORED_FIELDS if _text(fields.get(k)) is not None}
        stored = {k: str(v)[:128] for k, v in stored.items()}
        return WebhookEvent(
            provider=PROVIDER,
            provider_event_id="",
            event_name=f"payment.{status or 'unknown'}"[:80],
            outcome_state=outcome,
            organization_id=None,
            charge_id=None,
            provider_payment_id=payment_id if outcome != "unknown" else None,
            failure_reason=_text(fields.get("error"), _CODE_MAX) if outcome == "failed" else None,
            raw=stored,
            provider_order_id=_text(fields.get("txnid")),
            entity_id=payment_id or _text(fields.get("txnid")),
            created_at=_epoch(fields.get("addedon")),
            amount_minor=paise(fields.get("amount")),
            currency="INR",
        )

    def webhook_facts(self, event: WebhookEvent) -> CollectionWebhookFacts:
        outcome = event.outcome_state if event.outcome_state != "unknown" else None
        code = _text(event.raw.get("error"), _CODE_MAX)
        return CollectionWebhookFacts(
            outcome=outcome,
            refund=None,
            failure_code=code if code and code != "E000" else None,
            stored=event.raw,
        )

    # ------------------------------------------------------------------ verification

    def probe(self) -> None:
        """`verify_payment` of a transaction nobody made: PayU checks the hash first, so an answer
        at all proves the key and salt."""
        self._latest(txnid_for(str(uuid.uuid4())))
