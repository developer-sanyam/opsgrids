"""A merchant's Razorpay account, behind the standalone `CollectionsProvider` seam (ADR-0030).

Tenant collections (ADR-0024 §8) used to hold the platform's whole `PaymentProvider` and keep
Razorpay's vocabulary — its event names, its refund statuses, the shape of a refund entity, where a
failed payment's error code sits — in `app/collections/domain.py`. A second provider made that the
wrong place: those words are Razorpay's, so they live here now, and collections reads only the
neutral `CollectionWebhookFacts` an adapter hands it.

**No behaviour change.** This is the same `RazorpayProvider` a merchant's account always ran on —
the same lookup-before-create Order (a best-effort receipt search on the live wire: Razorpay keeps
no receipt unique and its search can lag a create, ADR-0029 "Sandbox facts"), the same live wire
behind `COLLECTIONS_WIRE=razorpay`, the same HMAC — with the collections half of the seam added
around it:

* `ensure_order` takes the `payer`, `expires_at` and `notify_url` the seam allows and ignores all
  three: a Razorpay Order needs no customer (Checkout collects the contact itself), takes no expiry
  this platform sets, and Razorpay delivers webhooks to the URL the merchant registered in its
  dashboard. A `recorded_order_id` is fetched by its id, never searched for or made again.
* `refund` takes the Order id the seam allows and ignores it: Razorpay refunds a payment.
* `verify_webhook` reads the signature from `X-Razorpay-Signature` when it is given the headers.
* `webhook_delivery_id` is `X-Razorpay-Event-Id`, unsigned, a delivery id and nothing more.
* `probe` is one authenticated read (`GET /orders?count=1`); on the stub it makes no request.

It is a `RazorpayProvider` subclass, so the platform's adapter is untouched and a merchant adapter
is still everything a `RazorpayProvider` is (the stub's `stub_record_payment`, `sign`).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.providers.base import (
    CheckoutCustomer,
    CollectionRefundFacts,
    CollectionWebhookFacts,
    OrderResult,
    RefundResult,
    WebhookEvent,
)
from app.providers.razorpay import RazorpayProvider, redact_webhook_body
from app.providers.wire import ProviderWire

PROVIDER = "razorpay"

#: Razorpay's signature and per-delivery id headers, lower-cased (the service hands an adapter its
#: request headers lower-cased).
SIGNATURE_HEADER = "x-razorpay-signature"
EVENT_ID_HEADER = "x-razorpay-event-id"

#: Razorpay event name → the collection outcome it asserts. `order.paid` carries the payment entity
#: too, and means exactly what `payment.captured` does for an Order-based payment.
COLLECTION_EVENT_OUTCOMES: dict[str, str] = {
    "payment.authorized": "authorized",
    "payment.captured": "captured",
    "payment.failed": "failed",
    "order.paid": "captured",
}
#: Razorpay refund events. Their `payload.refund.entity` is the refund; `payload.payment.entity` the
#: payment it returns money from.
REFUND_EVENTS: frozenset[str] = frozenset({"refund.created", "refund.processed", "refund.failed"})
#: What a merchant enables on the webhook it registers in Razorpay's dashboard.
WEBHOOK_EVENTS: tuple[str, ...] = (
    "payment.authorized",
    "payment.captured",
    "payment.failed",
    "order.paid",
    "refund.created",
    "refund.processed",
    "refund.failed",
)

#: Razorpay's refund statuses → the platform's refund states. A status this code does not know is
#: `created` — pending, the one state that claims nothing about the outcome.
_REFUND_STATUS_STATES: dict[str, str] = {
    "pending": "created",
    "created": "created",
    "processed": "processed",
    "failed": "failed",
}


def refund_state(status: str | None) -> str:
    return _REFUND_STATUS_STATES.get(status or "", "created")


def refund_key(digest: str) -> str:
    """The `X-Refund-Idempotency` value for a refund whose identity hashes to `digest`: `rfd-` and
    40 hex characters, inside Razorpay's own shape (at least 10 of `[A-Za-z0-9_-]`). Unchanged
    since tenant collections shipped — refunds already recorded are found again by it."""
    return f"rfd-{digest[:40]}"


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _entity(parsed: dict[str, Any], name: str) -> dict[str, Any] | None:
    payload = parsed.get("payload")
    wrapper = payload.get(name) if isinstance(payload, dict) else None
    entity = wrapper.get("entity") if isinstance(wrapper, dict) else None
    return entity if isinstance(entity, dict) else None


def payment_error_code(parsed: dict[str, Any]) -> str | None:
    """The stable `error_code` of a webhook's payment entity (a free-text description is never
    what the history records)."""
    code = (_entity(parsed, "payment") or {}).get("error_code")
    return code[:64] if isinstance(code, str) and code else None


def refund_facts(parsed: dict[str, Any]) -> CollectionRefundFacts | None:
    """A refund entity from a verified refund webhook — ids, money and status only."""
    entity = _entity(parsed, "refund")
    if entity is None:
        return None
    refund_id = entity.get("id")
    if not isinstance(refund_id, str) or not refund_id:
        return None
    amount = entity.get("amount")
    return CollectionRefundFacts(
        provider_refund_id=refund_id,
        provider_payment_id=_text(entity.get("payment_id")),
        amount_minor=amount if isinstance(amount, int) and not isinstance(amount, bool) else None,
        currency=(_text(entity.get("currency")) or "").upper() or None,
        state=refund_state(_text(entity.get("status"))),
    )


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    return headers.get(name) or headers.get(name.title()) or None


class RazorpayCollectionsProvider(RazorpayProvider):
    """One merchant account's Razorpay adapter. Network stubbed unless a wire is given."""

    def __init__(
        self,
        *,
        key_id: str | None,
        key_secret: str | None,
        webhook_secret: str,
        wire: ProviderWire | None = None,
    ) -> None:
        super().__init__(
            key_id=key_id, key_secret=key_secret, webhook_secret=webhook_secret, wire=wire
        )

    def __repr__(self) -> str:  # never the key secret or the webhook secret
        return (
            f"RazorpayCollectionsProvider(key_id={self._key_id!r}, "
            f"live_wire={self._wire is not None})"
        )

    # ------------------------------------------------------------------ orders and refunds

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
    ) -> OrderResult:
        """`RazorpayProvider.ensure_order`, unchanged. The payer, the expiry and the notify URL are
        not Razorpay Order fields and are dropped here — the payer never leaves this process.

        An Order the collection already records (`recorded_order_id`) is fetched by its id
        (`GET /orders/{id}`) and never searched for or made again — the platform's recorded-Order
        rule (ADR-0029). Collections never reopens a Razorpay checkout at the provider today (the
        browser is handed the recorded id), so this is the seam's promise kept, not a new call."""
        if recorded_order_id is not None:
            return self._recorded_order(
                order_id=recorded_order_id,
                receipt=receipt,
                amount_minor=amount_minor,
                currency=currency,
            )
        return super().ensure_order(
            receipt=receipt, amount_minor=amount_minor, currency=currency, notes=notes
        )

    def refund(
        self,
        *,
        idempotency_key: str,
        provider_payment_id: str,
        amount_minor: int,
        currency: str,
        provider_order_id: str | None = None,
    ) -> RefundResult:
        """`RazorpayProvider.refund`, unchanged: Razorpay refunds the payment, not the Order."""
        return super().refund(
            idempotency_key=idempotency_key,
            provider_payment_id=provider_payment_id,
            amount_minor=amount_minor,
            currency=currency,
        )

    # ------------------------------------------------------------------ webhooks

    def verify_webhook(
        self,
        *,
        body: bytes,
        signature: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> bool:
        """Razorpay's HMAC over the raw body, from `X-Razorpay-Signature` when given headers."""
        if signature is None:
            signature = _header(headers, SIGNATURE_HEADER)
        return super().verify_webhook(signature=signature, body=body)

    def webhook_delivery_id(self, headers: Mapping[str, str]) -> str | None:
        return _header(headers, EVENT_ID_HEADER)

    def webhook_facts(self, event: WebhookEvent) -> CollectionWebhookFacts:
        raw = event.raw
        return CollectionWebhookFacts(
            outcome=COLLECTION_EVENT_OUTCOMES.get(event.event_name),
            refund=refund_facts(raw) if event.event_name in REFUND_EVENTS else None,
            failure_code=payment_error_code(raw),
            stored=redact_webhook_body(raw),
        )

    # ------------------------------------------------------------------ verification

    def probe(self) -> None:
        """One authenticated read that creates nothing: `GET /orders?count=1`. A rejected key is
        `ProviderMisconfigured` (the wire classifies Razorpay's invalid-key answer whatever status
        it comes with). The stub has nothing to ask and answers at once."""
        if self._wire is None:
            return
        self._wire.request("GET", "/orders", params={"count": 1})


def build_collections_provider(
    *, key_id: str, key_secret: str, webhook_secret: str, wire: ProviderWire | None
) -> RazorpayCollectionsProvider:
    """One merchant account's adapter (ADR-0024 §8: "one adapter instance per merchant account,
    built from that account's sealed keys"). `wire` is the live HTTP wire, or None for the stub —
    `app.deps.get_collections_provider_factory` decides which from configuration."""
    return RazorpayCollectionsProvider(
        key_id=key_id, key_secret=key_secret, webhook_secret=webhook_secret, wire=wire
    )
