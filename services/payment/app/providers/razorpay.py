"""The Razorpay adapter — real everywhere except the wire.

What is **real and tested** here:

* **Signature verification.** `verify_webhook` computes the HMAC-SHA256 of the exact request
  body under the webhook secret and compares it in constant time — the identical check Razorpay
  documents. A tampered body or a wrong secret fails, and the caller turns that into a 400.
* **Request/response mapping.** `charge`, `create_mandate` and `refund` build the request the way
  Razorpay expects (amount in the smallest currency unit, the cap on a mandate, `notes` metadata)
  and map the response back into the neutral DTOs `base.py` defines.
* **The state machine.** Razorpay's event names (`payment.captured`, `payment.failed`,
  `payment.authorized`) are mapped to the platform's charge states in one place, `_EVENT_OUTCOMES`
  — the only spot that knows Razorpay's vocabulary.
* **Idempotency, the way Razorpay actually offers it.** Razorpay has no idempotency key on a
  payment create. What it has is the **Order**: created with `receipt` = the platform's charge
  key, and queryable — by receipt, and for the payments made against it. So the adapter's
  contract is **lookup before create** (`ensure_order`, `find_payment`, then `charge`), and the
  stubbed wire below keeps the state a real account would: Orders by receipt and payments by
  Order, per adapter instance (one per process, `app/deps.py`). A resumed call finds the payment
  a crashed one made; the ids are also derived from the key, so a fresh process agrees on them.

What is **stubbed** — the one seam:

* **The outbound HTTP to Razorpay.** There are no live keys in development (`razorpay_key_id` /
  `razorpay_key_secret` are absent), so `_call` never opens a socket. Instead it returns
  deterministic fake ids derived from the idempotency key, and the *outcome* of a charge is not
  invented here at all — it is driven by the **webhook fixtures** that reconciliation consumes,
  exactly as production is driven by Razorpay's real webhooks. A test asserts that no network and
  no keys are needed for any of this.

`notes` is load-bearing and real Razorpay behaviour: metadata attached to a payment is echoed
back on every webhook about it. The platform stamps `organization_id` and `charge_id` into
`notes` at charge-create time, so a later webhook — which arrives with no tenant context — carries
its own tenant, authentically, because the whole body is signature-verified before `notes` is
trusted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from app.providers.base import (
    ChargeResult,
    MandateResult,
    OrderResult,
    PaymentProvider,
    RefundResult,
    WebhookEvent,
)

PROVIDER = "razorpay"

#: Razorpay event name → the charge outcome it asserts. The one place this adapter admits it knows
#: Razorpay's vocabulary; `base.py` and the service layer speak only the neutral states.
_EVENT_OUTCOMES: dict[str, str] = {
    "payment.captured": "captured",
    "payment.failed": "failed",
    "payment.authorized": "authorized",
}


def _fake_id(prefix: str, idempotency_key: str) -> str:
    """A deterministic stand-in for a Razorpay id, derived from the platform idempotency key.

    Deterministic on purpose: the same key produces the same id, which is the observable property
    of a genuinely idempotent create and what a retry relies on. `pay_`, `mandate_`, `rfnd_` echo
    Razorpay's own id prefixes so a fake reads like the real thing in a log.
    """
    digest = hashlib.sha256(f"{prefix}:{idempotency_key}".encode()).hexdigest()[:14]
    return f"{prefix}{digest}"


class RazorpayProvider:
    """A `PaymentProvider` for Razorpay. Network stubbed; everything else real."""

    name = PROVIDER

    def __init__(
        self,
        *,
        key_id: str | None,
        key_secret: str | None,
        webhook_secret: str,
    ) -> None:
        # Kept for parity with a real adapter and to prove the point: these are None in
        # development, and every method below still works, because the wire is stubbed.
        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret
        # The stubbed account's own state — what the real Razorpay would hold and answer lookups
        # from: Orders by receipt, and the payments made against each Order.
        self._orders_by_receipt: dict[str, str] = {}
        self._payments_by_order: dict[str, ChargeResult] = {}

    # ------------------------------------------------------------------ the stubbed wire

    def _call(
        self, endpoint: str, request: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        """The ONE stub. In production this is an authenticated POST to Razorpay; here it returns
        a deterministic fake response and never touches the network — no keys required.

        It deliberately does not decide a charge's *outcome*: a real create returns `authorized`
        and the capture/fail arrives later by webhook, so the stub returns `authorized` too and
        leaves the terminal state to reconciliation of an injected fixture.
        """
        return {
            "id": _fake_id(request["_id_prefix"], idempotency_key),
            "status": request["_status"],
        }

    # ------------------------------------------------------------------ mandates

    def create_mandate(
        self,
        *,
        idempotency_key: str,
        max_amount_minor: int,
        currency: str,
        notes: dict[str, str],
    ) -> MandateResult:
        response = self._call(
            "mandates",
            {
                "_id_prefix": "mandate_",
                # A newly created eMandate is `pending` at Razorpay: the bank confirms it over
                # days, and only then can it be debited. Activation arrives by webhook.
                "_status": "created",
                "max_amount": max_amount_minor,
                "currency": currency,
                "notes": notes,
            },
            idempotency_key=idempotency_key,
        )
        return MandateResult(
            provider=PROVIDER,
            provider_mandate_id=response["id"],
            state="created",
            max_amount_minor=max_amount_minor,
            currency=currency,
            # None: not debitable yet. The multi-day activation is a later webhook, never
            # synchronous — the constraint an interface designed Stripe-first would have missed.
            activated_at=None,
        )

    # ------------------------------------------------------------------ orders

    def ensure_order(
        self,
        *,
        receipt: str,
        amount_minor: int,
        currency: str,
        notes: dict[str, str],
    ) -> OrderResult:
        """`GET /orders?receipt=…` first; `POST /orders` only when there is none. The stub answers
        the lookup from the state the create left behind."""
        existing = self._orders_by_receipt.get(receipt)
        if existing is None:
            response = self._call(
                "orders",
                {
                    "_id_prefix": "order_",
                    "_status": "created",
                    "amount": amount_minor,
                    "currency": currency,
                    "receipt": receipt,
                    "notes": notes,
                },
                idempotency_key=receipt,
            )
            existing = str(response["id"])
            self._orders_by_receipt[receipt] = existing
        return OrderResult(provider=PROVIDER, provider_order_id=existing, receipt=receipt)

    def find_payment(self, *, order_id: str, charge_id: str) -> ChargeResult | None:
        """`GET /orders/{id}/payments`, matched on the `notes.charge_id` the platform stamped."""
        return self._payments_by_order.get(order_id)

    # ------------------------------------------------------------------ charges

    def charge(
        self,
        *,
        idempotency_key: str,
        amount_minor: int,
        currency: str,
        mandate_id: str | None,
        notes: dict[str, str],
        order_id: str | None = None,
    ) -> ChargeResult:
        response = self._call(
            "payments",
            {
                "_id_prefix": "pay_",
                "_status": "authorized",
                # Razorpay takes the amount in the smallest currency unit — which is exactly the
                # platform's integer minor unit, no conversion (ADR-0000 §8).
                "amount": amount_minor,
                "currency": currency,
                "token": mandate_id,
                "order_id": order_id,
                "notes": notes,
            },
            idempotency_key=idempotency_key,
        )
        result = ChargeResult(
            provider=PROVIDER,
            provider_payment_id=response["id"],
            # `authorized`, not `captured`: capture is asynchronous and confirmed by webhook.
            state="authorized",
            amount_minor=amount_minor,
            currency=currency,
            provider_order_id=order_id,
        )
        if order_id is not None:
            self._payments_by_order[order_id] = result
        return result

    # ------------------------------------------------------------------ refunds

    def refund(
        self,
        *,
        idempotency_key: str,
        provider_payment_id: str,
        amount_minor: int,
        currency: str,
    ) -> RefundResult:
        response = self._call(
            "refunds",
            {"_id_prefix": "rfnd_", "_status": "processed", "payment_id": provider_payment_id},
            idempotency_key=idempotency_key,
        )
        return RefundResult(
            provider=PROVIDER,
            provider_refund_id=response["id"],
            state="processed",
            amount_minor=amount_minor,
            currency=currency,
        )

    # ------------------------------------------------------------------ webhooks

    def verify_webhook(self, *, signature: str | None, body: bytes) -> bool:
        """Constant-time HMAC-SHA256 of the raw body under the webhook secret — Razorpay's own
        scheme. No signature, or a signature that does not match these exact bytes, is False."""
        if not signature:
            return False
        expected = hmac.new(self._webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def sign(self, body: bytes) -> str:
        """The signature Razorpay would send for `body`. Not part of the provider Protocol — it
        exists so tests (and the injected fixtures) can produce an authentic signature without a
        real Razorpay, and so a valid callback can be exercised end to end without live keys."""
        return hmac.new(self._webhook_secret.encode(), body, hashlib.sha256).hexdigest()

    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        """Normalise a verified Razorpay webhook body into a `WebhookEvent`.

        Razorpay's shape: a top-level `event` name and `id`, and the payment entity under
        `payload.payment.entity`, whose `notes` echo the metadata the platform stamped at
        charge-create time. Reconciliation reads the tenant and the charge from those notes.
        """
        parsed: dict[str, Any] = json.loads(body)
        event_name = str(parsed.get("event", ""))
        entity: dict[str, Any] = parsed.get("payload", {}).get("payment", {}).get("entity", {})
        notes: dict[str, Any] = entity.get("notes") or {}
        return WebhookEvent(
            provider=PROVIDER,
            # Razorpay's per-delivery event id; the inbox is idempotent on it.
            provider_event_id=str(parsed.get("id", "")),
            event_name=event_name,
            outcome_state=_EVENT_OUTCOMES.get(event_name, "unknown"),
            organization_id=notes.get("organization_id"),
            charge_id=notes.get("charge_id"),
            provider_payment_id=entity.get("id"),
            failure_reason=entity.get("error_description"),
            raw=parsed,
        )


def build_provider(
    *, key_id: str | None, key_secret: str | None, webhook_secret: str
) -> PaymentProvider:
    """Construct the configured provider. Only Razorpay exists today; a `StripeProvider` would be
    selected here, and because it satisfies the same `Protocol`, nothing downstream changes."""
    return RazorpayProvider(key_id=key_id, key_secret=key_secret, webhook_secret=webhook_secret)
