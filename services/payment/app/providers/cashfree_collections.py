"""A merchant's Cashfree account, behind the standalone `CollectionsProvider` seam (ADR-0030).

Built on what platform billing's Phase A already proved and nothing parallel to it: the same
`CashfreeHttpWire` (or, on the stub, the same stateful `CashfreeFakeWire`), the same `Decimal`-exact
`cashfree_codec`, and the same `CashfreeProvider` for the Order itself — its lookup before create,
its `409` handling, its exact amount comparison, its refusal of an Order that can no longer be
paid. What this module adds is what only a merchant's collections need:

* **The Order** is `CashfreeProvider.ensure_checkout`: `GET /pg/orders/{receipt}` first (the
  Order's `order_id` *is* the collection's own id), `POST /pg/orders` only on a `404`, with the
  `x-idempotency-key` derived from it, the payer Cashfree requires (a pseudonymous `customer_id`
  and a phone — passed through, never kept), `order_expiry_time` and, when the platform has a
  public HTTPS base, `order_meta.notify_url`. The `payment_session_id` the browser needs comes back
  in `OrderResult.checkout_token`: held in memory for one response, never stored, and fetched again
  when a checkout is reopened.
* **Payments**: `GET /pg/orders/{id}/payments`, mapped by the codec — `SUCCESS` is captured,
  `FAILED` failed, and an abandoned attempt (`USER_DROPPED`, `NOT_ATTEMPTED`, `VOID`, `CANCELLED`)
  or a `PENDING` one is no observation at all. `USER_DROPPED` is never a failure.
* **Refunds**: `POST /pg/orders/{order_id}/refunds` with our own `refund_id` (`rfd` and 36 hex: 39
  alphanumeric characters, inside Cashfree's 3-40), `refund_speed: STANDARD` and an
  `x-idempotency-key`. A `409` is the refund already made: it is looked up by its `refund_id`
  and used — after checking it is for the same amount.
* **Webhooks**: `base64(HMAC-SHA256(secret, timestamp + raw body))` over the
  `x-webhook-timestamp` header and the exact bytes, in constant time, failing closed. The Secret
  Key is the signing key: a Cashfree account has no webhook secret of its own. `PAYMENT_SUCCESS` is
  captured and `PAYMENT_FAILED` failed (bound to that attempt's payment id); `REFUND_STATUS` is
  keyed by our `refund_id`; everything else (`PAYMENT_USER_DROPPED`, auto-refunds, settlements) is
  recorded and asserts nothing. The delivery id is `x-idempotency-header` (also read as
  `x-idempotency-key`, the spelling some samples show), unsigned, a delivery id and nothing more.
  What the inbox keeps is the codec's allow-list: ids, status, money as decimal strings — never
  the customer, the payment method or free text.
* **Probe**: `GET /pg/orders/<random id>` — a `404` proves the credentials authenticated (an
  Order of that id cannot exist), a `401`/`403` is `ProviderMisconfigured`. Unverified against a
  live sandbox: the owner-run smoke settles it.

Money is rupees on the wire and paise everywhere else, converted only by the codec. INR only, at
least 100 paise, refused before any request.
"""

from __future__ import annotations

import re
import urllib.parse
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from app.providers import cashfree_codec as codec
from app.providers.base import (
    CheckoutCustomer,
    CheckoutRequest,
    CollectionRefundFacts,
    CollectionWebhookFacts,
    MalformedWebhook,
    OrderResult,
    PaymentSnapshot,
    RefundResult,
    WebhookEvent,
)
from app.providers.cashfree import CashfreeProvider, idempotency_key
from app.providers.wire import WIRE_REQUEST_TIMEOUT_SECONDS, ProviderRejected, ProviderWire
from platform_core.logging import get_logger

log = get_logger("payment.cashfree.collections")

PROVIDER = "cashfree"

#: Cashfree's webhook headers (lower-cased: the service hands an adapter its headers lower-cased).
SIGNATURE_HEADER = "x-webhook-signature"
TIMESTAMP_HEADER = "x-webhook-timestamp"
EVENT_ID_HEADERS: tuple[str, ...] = ("x-idempotency-header", "x-idempotency-key")
#: What a merchant subscribes to on the webhook endpoint in Cashfree's dashboard (PG webhooks,
#: version 2025-01-01 or later).
WEBHOOK_EVENTS: tuple[str, ...] = (
    "PAYMENT_SUCCESS_WEBHOOK",
    "PAYMENT_FAILED_WEBHOOK",
    "PAYMENT_USER_DROPPED_WEBHOOK",
    "REFUND_STATUS_WEBHOOK",
)

#: The most requests one collections call can make: the Order lookup, its create, and the lookup
#: again after a `409` (a refund is two: the create and the lookup after a `409`).
REQUESTS_PER_CALL = 3

_REFUND_ID = re.compile(r"^[A-Za-z0-9]{3,40}$")
_EVENT_NAME_MAX = 80


def refund_key(digest: str) -> str:
    """Cashfree's `refund_id` for a refund whose identity hashes to `digest`: `rfd` and 36 hex — 39
    alphanumeric characters, inside the 3-40 Cashfree allows (`rfd-` and 40 hex, Razorpay's shape,
    is 44 characters with a hyphen and would be refused)."""
    return f"rfd{digest[:36]}"


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    return headers.get(name) or headers.get(name.title()) or None


class CashfreeCollectionsProvider:
    """One merchant account's Cashfree adapter, built per call from that account's opened keys."""

    name = PROVIDER
    #: One connect + read timeout per request, three requests at worst: 3 x 7s = 21s. An abandoned
    #: call is over long before the collection's right-to-call lease (2 x the call timeout + 60s)
    #: lapses, which is what makes a retry safe (`CollectionsContract`).
    request_timeouts_seconds: tuple[float, ...] = (
        WIRE_REQUEST_TIMEOUT_SECONDS,
    ) * REQUESTS_PER_CALL

    def __init__(
        self,
        *,
        wire: ProviderWire,
        secret_key: str,
        mode: Literal["stub", "sandbox", "production"],
    ) -> None:
        self._wire = wire
        #: The merchant's Secret Key — it also signs every webhook. Never in a repr or a log.
        self._secret_key = secret_key
        self._mode = mode
        self._orders = CashfreeProvider(wire=wire, secret_key=secret_key or None, mode=mode)

    def __repr__(self) -> str:  # never a credential
        return f"CashfreeCollectionsProvider(mode={self._mode!r})"

    @property
    def is_live(self) -> bool:
        return self._mode != "stub"

    @property
    def checkout_mode(self) -> Literal["sandbox", "production"]:
        """What the browser's `Cashfree({mode})` is created with."""
        return "production" if self._mode == "production" else "sandbox"

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
    ) -> OrderResult:
        """The Order for `receipt` — looked up first, created only if Cashfree holds none — with
        its `payment_session_id` in `checkout_token`. A payer with no usable phone, an expiry not
        given, a currency other than INR or an amount under 100 paise is refused before any
        request; an Order found for another amount, or one that can no longer be paid, is refused
        rather than reused."""
        if expires_at is None:
            raise ProviderRejected(PROVIDER, 0, "expiry_required")
        session = self._orders.ensure_checkout(
            CheckoutRequest(
                receipt=receipt,
                amount_minor=amount_minor,
                currency=currency,
                notes=notes,
                expires_at=expires_at,
                customer=payer,
                notify_url=notify_url,
            )
        )
        return session.order

    def find_order(self, *, receipt: str) -> OrderResult | None:
        """`GET /pg/orders/{receipt}` — the lookup alone, never a create."""
        return self._orders.find_order(receipt=receipt)

    def order_payments(self, *, order_id: str) -> list[PaymentSnapshot]:
        """`GET /pg/orders/{id}/payments`, in the neutral vocabulary; an abandoned attempt is not
        in the answer at all, so it can never move a collection."""
        return self._orders.order_payments(order_id=order_id)

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
        """`POST /pg/orders/{order_id}/refunds`. `idempotency_key` is our `refund_id`
        (`refund_key`); the same key is the same refund at Cashfree. A `409` (the refund exists —
        an earlier call landed and its answer was lost) is answered by looking the refund up."""
        if not provider_order_id:
            raise ProviderRejected(PROVIDER, 0, "order_required")
        if not _REFUND_ID.fullmatch(idempotency_key):
            raise ProviderRejected(PROVIDER, 0, "refund_id_invalid")
        amount = codec.minor_to_amount(amount_minor, currency)
        path = f"/orders/{_quote(provider_order_id)}/refunds"
        try:
            body = self._wire.request(
                "POST",
                path,
                json={
                    "refund_amount": amount,
                    "refund_id": idempotency_key,
                    "refund_speed": "STANDARD",
                },
                headers={"x-idempotency-key": idempotency_key_for_refund(idempotency_key)},
            )
        except ProviderRejected as exc:
            if exc.status_code != 409:
                raise
            body = self._wire.request("GET", f"{path}/{_quote(idempotency_key)}")
        return self._refund_result(body, idempotency_key, amount_minor, currency)

    def _refund_result(
        self, body: Any, refund_id: str, amount_minor: int, currency: str
    ) -> RefundResult:
        entity = codec.as_dict(body)
        found_id = codec.as_text(entity.get("refund_id"))
        if found_id is not None and found_id != refund_id:
            raise ProviderRejected(PROVIDER, 200, "refund_id_mismatch")
        found_amount = codec.amount_to_minor(entity.get("refund_amount"), currency)
        if found_amount is not None and found_amount != amount_minor:
            # The same refund id for another amount: an earlier, unrecorded request under this key
            # asked for something else. Never reported as this refund.
            raise ProviderRejected(PROVIDER, 200, "refund_amount_mismatch")
        return RefundResult(
            provider=PROVIDER,
            provider_refund_id=refund_id,
            state=codec.refund_state(entity.get("refund_status")),
            amount_minor=amount_minor,
            currency=currency.upper(),
        )

    # ------------------------------------------------------------------ webhooks

    def verify_webhook(
        self,
        *,
        body: bytes,
        signature: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> bool:
        """`base64(HMAC-SHA256(secret, timestamp + body))`, constant time, failing closed: no
        secret, no signature, no timestamp, a non-ASCII header or any mismatch is False."""
        if signature is None:
            signature = _header(headers, SIGNATURE_HEADER)
        timestamp = _header(headers, TIMESTAMP_HEADER)
        return codec.verify_signature(self._secret_key or None, signature, timestamp, body)

    def sign(self, body: bytes, timestamp: str) -> str:
        """What Cashfree would send for `body` at `timestamp` — tests and fixtures only."""
        return self._orders.sign(body, timestamp)

    def webhook_delivery_id(self, headers: Mapping[str, str]) -> str | None:
        return next(
            (value for value in (_header(headers, n) for n in EVENT_ID_HEADERS) if value), None
        )

    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        """A verified Cashfree PG webhook, in the collections vocabulary.

        A payment event's outcome is `captured` for `PAYMENT_SUCCESS` and `failed` for
        `PAYMENT_FAILED`, and only when the payment's own status agrees; it binds `cf_payment_id`.
        Every other type asserts nothing and binds no payment id — a customer closing the page is
        not a failure. A refund event is identified by the refund (`cf_refund_id`) and carries the
        payment it returns money from. The amount is the codec's (`event_amount`): the Order's
        when the payment covers it, the payment's when it is short, and a capture whose amount
        cannot be read exactly is no outcome, logged at error. `raw` is the redacted body."""
        try:
            parsed = codec.loads(body)
        except ValueError:
            raise MalformedWebhook("the webhook body is not valid JSON") from None
        if not isinstance(parsed, dict):
            raise MalformedWebhook("the webhook body is not a JSON object")
        etype = codec.event_type(parsed)
        data = codec.as_dict(parsed.get("data"))
        created_at = codec.epoch_of(parsed.get("event_time"))
        raw = codec.redact_webhook_body(parsed)

        if etype in codec.REFUND_EVENTS:
            refund = codec.as_dict(data.get("refund"))
            currency = codec.as_text(refund.get("refund_currency"))
            return WebhookEvent(
                provider=PROVIDER,
                provider_event_id="",
                event_name=etype[:_EVENT_NAME_MAX],
                outcome_state="unknown",
                organization_id=None,
                charge_id=None,
                provider_payment_id=codec.as_text(refund.get("cf_payment_id")),
                raw=raw,
                provider_order_id=codec.as_text(refund.get("order_id")),
                entity_id=codec.as_text(refund.get("cf_refund_id"))
                or codec.as_text(refund.get("refund_id")),
                created_at=created_at,
                amount_minor=codec.amount_to_minor(refund.get("refund_amount"), currency or "INR"),
                currency=currency.upper() if currency else None,
            )

        order = codec.as_dict(data.get("order"))
        payment = codec.as_dict(data.get("payment"))
        tags = codec.as_dict(order.get("order_tags"))
        currency = codec.as_text(payment.get("payment_currency")) or codec.as_text(
            order.get("order_currency")
        )
        paid = codec.amount_to_minor(payment.get("payment_amount"), currency or "INR")
        ordered = codec.amount_to_minor(order.get("order_amount"), currency or "INR")
        amount = codec.event_amount(ordered, paid)
        status = str(payment.get("payment_status") or "").upper()

        outcome = codec.COLLECTION_EVENT_OUTCOMES.get(etype, "unknown")
        if (outcome == "captured" and status != "SUCCESS") or (
            outcome == "failed" and status != "FAILED"
        ):
            outcome = "unknown"
        if outcome == "captured" and amount is None:
            log.error("cashfree.collections.capture_amount_unreadable", event_type=etype[:40])
            outcome = "unknown"
        payment_id = codec.as_text(payment.get("cf_payment_id"))
        return WebhookEvent(
            provider=PROVIDER,
            provider_event_id="",
            event_name=etype[:_EVENT_NAME_MAX],
            outcome_state=outcome,
            organization_id=codec.as_text(tags.get("organization_id")),
            charge_id=codec.as_text(tags.get("charge_id")),
            provider_payment_id=payment_id if outcome in ("captured", "failed") else None,
            # Cashfree's description is free text; the stable code stands in for it.
            failure_reason=_error_code(raw) if outcome == "failed" else None,
            raw=raw,
            provider_order_id=codec.as_text(order.get("order_id")),
            entity_id=payment_id or codec.as_text(order.get("order_id")),
            created_at=created_at,
            amount_minor=amount,
            currency=currency.upper() if currency else None,
        )

    def webhook_facts(self, event: WebhookEvent) -> CollectionWebhookFacts:
        refund: CollectionRefundFacts | None = None
        if event.event_name in codec.REFUND_EVENTS:
            entity = codec.as_dict(codec.as_dict(event.raw.get("data")).get("refund"))
            refund_id = codec.as_text(entity.get("refund_id"))
            if refund_id is not None:
                currency = codec.as_text(entity.get("refund_currency"))
                refund = CollectionRefundFacts(
                    provider_refund_id=refund_id,
                    provider_payment_id=codec.as_text(entity.get("cf_payment_id")),
                    amount_minor=codec.amount_to_minor(
                        entity.get("refund_amount"), currency or "INR"
                    ),
                    currency=currency.upper() if currency else None,
                    state=codec.refund_state(entity.get("refund_status")),
                )
        outcome = event.outcome_state if event.outcome_state in ("captured", "failed") else None
        return CollectionWebhookFacts(
            outcome=outcome,
            refund=refund,
            failure_code=_error_code(event.raw),
            stored=event.raw,
        )

    # ------------------------------------------------------------------ verification

    def probe(self) -> None:
        """`GET /pg/orders/<an id nobody made>`: a `404` is Cashfree saying "authenticated, and no
        such Order" — the credentials work. A `401`/`403` propagates as `ProviderMisconfigured`."""
        try:
            self._wire.request("GET", f"/orders/probe{uuid.uuid4().hex[:24]}")
        except ProviderRejected as exc:
            if exc.status_code != 404:
                raise


def idempotency_key_for_refund(refund_id: str) -> str:
    """The `x-idempotency-key` of a refund create: a UUID derived from its `refund_id`."""
    return idempotency_key("refund", refund_id)


def _error_code(raw: dict[str, Any]) -> str | None:
    details = codec.as_dict(codec.as_dict(raw.get("data")).get("error_details"))
    code = codec.as_text(details.get("error_code"))
    return code[:64] if code else None
