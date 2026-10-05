"""Cashfree's wire format, as pure functions: amounts, JSON, the webhook signature, the status maps
and the webhook redaction. No I/O, no clock, no state, no keys held.

**Money.** Cashfree speaks rupees as JSON *numbers*, the platform speaks integer paise, and the
conversion is where money bugs live, so it is done in exactly one place and never through a float:

* outbound, `Decimal(minor) / 10**exponent`, written as a bare JSON number by `dumps` (19.99, not
  "19.99" and not 19.989999999999998);
* inbound, the body is parsed with `parse_float=Decimal`, an amount is a number or a numeric
  string, and it must be an **exact** number of minor units — `0.105` is not an amount, it is a
  defect, and it is `None` here so that nothing downstream can mistake it for one;
* only `INR` (exponent 2) is known; any other currency is refused with no call.

`json.loads`'s default would turn `499.00` into a float and `1e2` into 100.0; an amount that went
through a float and was then `int(x * 100)`-ed is the classic way 19.99 becomes 1998 paise. Nothing
here does that, and `tests/test_cashfree_codec.py` pins independent known answers.

**The signature** is `base64(HMAC-SHA256(secret, timestamp + raw_body))` — the timestamp header's
bytes immediately followed by the body's, no separator. It is verified in constant time over ASCII
bytes and **fails closed**: a missing secret, signature or timestamp, a non-ASCII header, or any
mismatch is `False`, never an exception (a header anyone can send must not become a 500 that is
retried), and an unset secret verifies nothing at all. No freshness window is enforced (the docs
are silent and Cashfree retries re-use the timestamp); the idempotent inbox is the replay defence.

**Redaction.** A Cashfree webhook carries the paying customer (name, email, phone) and the payment
method (UPI id, card, bank). The inbox is append-only, so what is stored is an **allow-list** that
keeps the Cashfree envelope shape — `parse_webhook` must still be able to read it back
(`services._identity_of`) — and that writes amounts as canonical decimal **strings**, so a
`Decimal` never reaches JSONB and the stored body parses to the same amounts it came from.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.providers.base import PaymentSnapshot
from app.providers.wire import ProviderRejected

PROVIDER = "cashfree"

#: Sent as `x-api-version` on every request. A code constant, not configuration: the shapes below
#: are written against one version, and an environment variable would let a deploy change the
#: contract without a review. 2025-01-01 is the version the webhook documentation this adapter was
#: designed from names; the owner-run smoke tool prints whether the account accepts it.
API_VERSION = "2025-01-01"

#: Minor-unit exponent per ISO currency this adapter will convert. INR only (owner decision).
CURRENCY_EXPONENTS: dict[str, int] = {"INR": 2}
#: The largest amount, in major units, an inbound number may be: a defence against a body whose
#: `1E+999999999` would otherwise be asked to become an integer.
_MAX_AMOUNT = Decimal("1e12")


# --------------------------------------------------------------------------- money


def exponent_for(currency: str) -> int:
    """The minor-unit exponent of `currency`, or a `ProviderRejected` (`unsupported_currency`)
    **before any request is made**."""
    try:
        return CURRENCY_EXPONENTS[currency.upper()]
    except KeyError:
        raise ProviderRejected(PROVIDER, 0, "unsupported_currency") from None


def minor_to_amount(minor: int, currency: str) -> Decimal:
    """Integer minor units as the exact decimal Cashfree is sent: 1999 INR minor is 19.99."""
    exponent = exponent_for(currency)
    return (Decimal(minor) / Decimal(10) ** exponent).quantize(Decimal(1).scaleb(-exponent))


def amount_to_minor(value: Any, currency: str = "INR") -> int | None:
    """What Cashfree says an amount is, as integer minor units — or None when it is not an exact,
    non-negative number of them (missing, a bool, NaN, a string that is not a number, three
    decimal places of rupees). Accepts a `Decimal` (the codec's own parse), an `int`, and a numeric
    string; a `float` is accepted only if it is the exact value of its shortest representation,
    which no code path of ours produces (the body is parsed with `parse_float=Decimal`)."""
    try:
        exponent = exponent_for(currency)
    except ProviderRejected:
        return None
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, Decimal):
            amount = value
        elif isinstance(value, int):
            amount = Decimal(value)
        elif isinstance(value, float):
            amount = Decimal(repr(value))
        elif isinstance(value, str):
            amount = Decimal(value.strip())
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0 or amount > _MAX_AMOUNT:
        return None
    minor = amount.scaleb(exponent)
    if minor != minor.to_integral_value():
        return None
    return int(minor)


def decimal_text(minor: int, currency: str = "INR") -> str:
    """`minor` as the canonical decimal string the redacted body stores (`"499.00"`)."""
    return format(minor_to_amount(minor, currency), "f")


# --------------------------------------------------------------------------- JSON


def loads(raw: bytes | str) -> Any:
    """Parse a Cashfree body with every non-integer number as a `Decimal`, never a float."""
    return json.loads(raw, parse_float=Decimal)


def _encode(value: Any) -> str:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("a non-finite number cannot be sent")
        return format(value, "f")
    if isinstance(value, bool) or value is None or isinstance(value, int | str):
        return json.dumps(value)
    if isinstance(value, float):
        raise TypeError("a float is never a Cashfree amount: use Decimal")
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(str(k))}:{_encode(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list | tuple):
        return "[" + ",".join(_encode(v) for v in value) + "]"
    raise TypeError(f"cannot encode {type(value).__name__}")


def dumps(value: Any) -> bytes:
    """JSON for a Cashfree request body, with a `Decimal` written as a bare number (`19.99`),
    exactly. A `float` is refused outright: it is how an amount silently loses a paisa."""
    return _encode(value).encode()


# --------------------------------------------------------------------------- the signature


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """`base64(HMAC-SHA256(secret, timestamp + body))` — what Cashfree sends in
    `x-webhook-signature`. For the adapter's `sign` (tests, fixtures); the live path only
    verifies."""
    digest = hmac.new(secret.encode(), timestamp.encode() + body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_signature(
    secret: str | None, signature: str | None, timestamp: str | None, body: bytes
) -> bool:
    """Constant-time check of a webhook signature. Fails closed: `False` — never an exception —
    for an unset secret, a missing or non-ASCII signature or timestamp, or any mismatch."""
    if not secret or not signature or not timestamp:
        return False
    if not signature.isascii() or not timestamp.isascii():
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


# --------------------------------------------------------------------------- statuses

#: Cashfree payment status → the neutral vocabulary `PaymentSnapshot.status` is in (the same one
#: Razorpay's maps into, so `best_attempt` and the reconciler never learn Cashfree's words).
#: Absent statuses (`NOT_ATTEMPTED`, `USER_DROPPED`, `VOID`, `CANCELLED`) are an attempt that
#: never happened or was abandoned: **no observation**, and above all never `failed` — a customer
#: closing the page is not a payment failing, and treating it as one would fail a charge that is
#: still payable.
PAYMENT_STATUS_STATES: dict[str, str] = {
    "SUCCESS": "captured",
    "FAILED": "failed",
    "PENDING": "created",
}

#: The PG webhook types that assert an outcome, and what. Anything else — `PAYMENT_FAILED`,
#: `PAYMENT_USER_DROPPED`, settlement and refund events — asserts none here: the poll settles it.
EVENT_OUTCOMES: dict[str, str] = {"PAYMENT_SUCCESS": "captured"}

_EVENT_SUFFIX = "_WEBHOOK"


def event_type(parsed: dict[str, Any]) -> str:
    """The webhook's `type`, upper-cased, without the `_WEBHOOK` suffix some versions add."""
    raw = parsed.get("type")
    name = raw.strip().upper() if isinstance(raw, str) else ""
    return name.removesuffix(_EVENT_SUFFIX)


# --------------------------------------------------------------------------- reading bodies


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_text(value: Any) -> str | None:
    """A non-empty string, or an int rendered as one (`cf_payment_id` is a number)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return value if isinstance(value, str) and value else None


def epoch_of(value: Any) -> int | None:
    """An ISO-8601 timestamp (`2021-10-13T10:15:15+05:30`, or `…Z`) as integer epoch seconds, or
    None. A naive timestamp is read as UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def snapshot_from_payment(item: Any) -> PaymentSnapshot | None:
    """One Cashfree payment (`GET /orders/{id}/payments`) as a `PaymentSnapshot` — ids, status,
    money and the error code only. Nothing about the customer or the payment method is copied.

    `None` for a payment this code reads no outcome from: an unknown status, an abandoned or
    never-attempted attempt, or a **missing or inexact amount** — an amount that cannot be read
    must not be treated as one that matches. The amount is the Order's (`order_amount`, the
    merchant-set value) when the payment covers it: a payment above it (a surcharge) is accepted
    at the Order's amount, and a payment **below** it is reported as what it is, so the reconciler
    sees the mismatch."""
    entity = as_dict(item)
    status = PAYMENT_STATUS_STATES.get(str(entity.get("payment_status") or "").upper())
    payment_id = as_text(entity.get("cf_payment_id"))
    currency = as_text(entity.get("payment_currency"))
    paid = amount_to_minor(entity.get("payment_amount"), currency or "INR")
    if status is None or payment_id is None or currency is None or paid is None:
        return None
    amount = event_amount(amount_to_minor(entity.get("order_amount"), currency), paid)
    error = as_dict(entity.get("error_details"))
    return PaymentSnapshot(
        provider_payment_id=payment_id,
        status=status,
        amount_minor=paid if amount is None else amount,
        currency=currency.upper(),
        error_code=as_text(error.get("error_code")),
        created_at=epoch_of(entity.get("payment_completion_time") or entity.get("payment_time")),
    )


def event_amount(order_minor: int | None, paid_minor: int | None) -> int | None:
    """The amount a payment is reconciled at: **the Order's** (the merchant-set value) when the
    payment covers it — a payment above it is a surcharge, accepted — and the payment's own when
    it is below it, which is a mismatch the reconciler must see. When the body does not say what
    the Order was, the payment's amount stands. **None when the payment's own amount cannot be
    read**: money that cannot be seen to have moved is not a capture."""
    if paid_minor is None:
        return None
    if order_minor is None:
        return paid_minor
    return order_minor if paid_minor >= order_minor else paid_minor


# --------------------------------------------------------------------------- redaction

_KEEP_TOP: tuple[str, ...] = ("type", "event_time")
_KEEP_ORDER: tuple[str, ...] = ("order_id", "order_currency", "order_tags")
_KEEP_ORDER_AMOUNT = "order_amount"
_KEEP_PAYMENT: tuple[str, ...] = (
    "cf_payment_id",
    "payment_status",
    "payment_currency",
    "payment_time",
)
_KEEP_PAYMENT_AMOUNT = "payment_amount"


def _amount_text(value: Any, currency: Any) -> str | None:
    """An amount as a canonical decimal string, or None when it is not an exact one."""
    minor = amount_to_minor(value, currency if isinstance(currency, str) else "INR")
    return (
        None
        if minor is None
        else decimal_text(minor, currency if isinstance(currency, str) else "INR")
    )


def _clean_tags(tags: Any) -> dict[str, str]:
    """`order_tags` — the platform's own metadata (ids), kept — as strings only."""
    return {
        str(k)[:40]: str(v)[:120]
        for k, v in as_dict(tags).items()
        if isinstance(v, str | int) and not isinstance(v, bool)
    }


def redact_webhook_body(parsed: dict[str, Any]) -> dict[str, Any]:
    """The webhook as the inbox keeps it: the Cashfree envelope (`type`, `event_time`,
    `data.order`, `data.payment`) reduced to identifiers, status, currency and amounts (as
    decimal strings), with `customer_details`, `payment_method`, bank references, UPI ids and
    every free-text field dropped by construction (an allow-list: a field added tomorrow is
    dropped too). Contains no `Decimal`, so it is JSONB-safe, and `CashfreeProvider.parse_webhook`
    reads it back to the same event."""
    kept: dict[str, Any] = {k: parsed[k] for k in _KEEP_TOP if isinstance(parsed.get(k), str)}
    data = as_dict(parsed.get("data"))
    order = as_dict(data.get("order"))
    payment = as_dict(data.get("payment"))

    order_out: dict[str, Any] = {}
    for key in _KEEP_ORDER:
        if key == "order_tags":
            order_out[key] = _clean_tags(order.get(key))
        elif key in order:
            text = as_text(order[key])
            if text is not None:
                order_out[key] = text
    amount = _amount_text(order.get(_KEEP_ORDER_AMOUNT), order.get("order_currency"))
    if amount is not None:
        order_out[_KEEP_ORDER_AMOUNT] = amount

    payment_out: dict[str, Any] = {}
    for key in _KEEP_PAYMENT:
        text = as_text(payment.get(key))
        if text is not None:
            payment_out[key] = text
    paid = _amount_text(payment.get(_KEEP_PAYMENT_AMOUNT), payment.get("payment_currency"))
    if paid is not None:
        payment_out[_KEEP_PAYMENT_AMOUNT] = paid

    kept["data"] = {"order": order_out, "payment": payment_out}
    return kept
