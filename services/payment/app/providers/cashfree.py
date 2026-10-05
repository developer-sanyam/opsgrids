"""The Cashfree adapter — platform billing's second provider (ADR-0029), Phase A: customer-present
checkout. Real everywhere it exists; on `CASHFREE_ENV=stub` its wire is an in-process stateful fake
(`cashfree_fake.py`) and no socket is ever opened, whatever keys are present.

**What it is.** A `PaymentProvider` and a `CheckoutProvider` for the part of Cashfree a customer
pays in: `ensure_checkout` creates (after looking up) the Order the customer pays in Cashfree's own
hosted checkout, `order_payments` asks Cashfree what happened to it, and `verify_webhook` /
`parse_webhook` read the signed deliveries that will accelerate that once the webhook is public.
The browser's redirect is never evidence of anything: success is a captured payment found by
`order_payments` or a signature-verified `PAYMENT_SUCCESS` webhook.

**What it declares and does not build.** `debit_shape = "direct"`: Cashfree debits a saved mandate
by a merchant-chosen `payment_id`, with no Order step, so the Order-then-charge contract
(`ProviderContract`) cannot describe it and `DirectDebitContract` is its sibling. `find_debit`,
`debit`, `create_mandate`, `charge` and `refund` raise `NotImplementedError`: mandates and debits
are Phase B, refunds are not built, and `capabilities` says so (`supports_mandate_debit=False`), so
the service layer fails a debit charge as `no_mandate` — with no provider call — before any of them
could be reached.

**Identity.** The Order's `order_id` is the charge's own id (36 characters, within Cashfree's 45),
the same handle Razorpay calls `receipt`; creates carry an `x-idempotency-key` derived from it; the
Order's `order_tags` carry the platform's metadata (organization and charge ids — never a person).
A `409` on create means the Order exists: it is fetched and used. A found Order's amount is compared
exactly — one that disagrees is refused, never silently reused.

**Customer data** (name, email, a 10-digit phone — Cashfree requires the phone on every Order) is
passed through to Cashfree and nowhere else: not stored, not tagged, not logged, and not in any
exception (`CheckoutCustomer` suppresses its own `repr`). The `payment_session_id` the browser needs
is held in memory for the one response and re-fetched when a checkout is reopened.

**Money** is rupees on the wire and paise everywhere else, converted only by `cashfree_codec`
(`Decimal`, never a float). INR only, at least 100 paise: anything else is refused before any
request, here and (first) in the service layer.
"""

from __future__ import annotations

import urllib.parse
import uuid
from typing import Any, Literal

from app.providers import cashfree_codec as codec
from app.providers.base import (
    ChargeResult,
    CheckoutRequest,
    CheckoutSession,
    MalformedWebhook,
    MandateResult,
    OrderResult,
    PaymentSnapshot,
    ProviderCapabilities,
    RefundResult,
    WebhookEvent,
    best_attempt,
    valid_phone,
)
from app.providers.wire import (
    CASHFREE_REQUESTS_PER_CHARGE,
    WIRE_REQUEST_TIMEOUT_SECONDS,
    ProviderRejected,
    ProviderWire,
)
from platform_core.logging import get_logger

log = get_logger("payment.cashfree")

PROVIDER = "cashfree"

#: Cashfree's minimum: Re 1 (100 paise). INR only — the owner's decision for this release.
MIN_AMOUNT_MINOR = 100
CURRENCIES: frozenset[str] = frozenset({"INR"})
#: UPI debits above Rs 15,000 need the customer's additional authentication (Phase B).
MAX_DEBIT_WITHOUT_AFA_MINOR = 1_500_000

_EVENT_NAME_MAX = 80
#: Namespace of the `x-idempotency-key` UUIDs: a platform key like `invoice:<uuid>` is not a UUID,
#: so the header value is derived from the Order's receipt deterministically (a retry sends the
#: same key; two receipts never share one).
_IDEMPOTENCY_NAMESPACE = uuid.UUID("5d0e3d3a-9f0e-4c8e-9a43-0b7a0f6f3c11")

_NOT_BUILT = "is not built: mandates and debits are Phase B and refunds are not built (ADR-0029)"


def idempotency_key(op: str, receipt: str) -> str:
    """The `x-idempotency-key` of a create: a UUID derived from what it creates (`op`) and its
    receipt — the same create sends the same key; two receipts never share one."""
    return str(uuid.uuid5(_IDEMPOTENCY_NAMESPACE, f"{op}:{receipt}"))


class CashfreeProvider:
    """A `PaymentProvider` and `CheckoutProvider` for Cashfree PG."""

    name = PROVIDER
    #: Declared, not implemented: see the module docstring.
    debit_shape = "direct"
    #: One connect + read timeout per request, three requests at worst (the lookup, the create,
    #: the lookup again after a `409`): 3 x 7s = 21s, inside `provider_call_timeout_seconds`.
    request_timeouts_seconds: tuple[float, ...] = (
        WIRE_REQUEST_TIMEOUT_SECONDS,
    ) * CASHFREE_REQUESTS_PER_CHARGE

    def __init__(
        self,
        *,
        wire: ProviderWire,
        secret_key: str | None,
        mode: Literal["stub", "sandbox", "production"] = "stub",
    ) -> None:
        self._wire = wire
        #: Signs nothing but is the webhook's HMAC key (Cashfree has no separate webhook secret).
        #: Unset means no webhook verifies: `verify_webhook` fails closed.
        self._secret_key = secret_key
        self._mode = mode

    def __repr__(self) -> str:  # never a credential
        return f"CashfreeProvider(mode={self._mode!r})"

    @property
    def is_live(self) -> bool:
        return self._mode != "stub"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            currencies=CURRENCIES,
            min_amount_minor=MIN_AMOUNT_MINOR,
            debit_shape="direct",
            requires_mandate_for_debit=True,
            supports_mandate_debit=False,
            supports_checkout=True,
            requires_customer_details=True,
            checkout_confirmation=False,
            signs_headers=True,
            max_debit_without_afa_minor=MAX_DEBIT_WITHOUT_AFA_MINOR,
            wire="live" if self.is_live else "stub",
        )

    # ------------------------------------------------------------------ orders

    def _require_money(self, amount_minor: int, currency: str) -> None:
        """INR and at least 100 paise, refused **before any request**."""
        codec.exponent_for(currency)
        if amount_minor < MIN_AMOUNT_MINOR:
            raise ProviderRejected(PROVIDER, 0, "amount_below_minimum")

    def _get_order(self, order_id: str) -> dict[str, Any] | None:
        """`GET /orders/{id}`: the Order as it stands, or None for one Cashfree does not hold."""
        try:
            body = self._wire.request("GET", f"/orders/{urllib.parse.quote(order_id, safe='')}")
        except ProviderRejected as exc:
            if exc.status_code == 404:
                return None
            raise
        return body if isinstance(body, dict) else None

    def _order_result(self, body: dict[str, Any], receipt: str) -> OrderResult | None:
        order_id = codec.as_text(body.get("order_id"))
        if order_id is None:
            return None
        token = codec.as_text(body.get("payment_session_id"))
        return OrderResult(
            provider=PROVIDER, provider_order_id=order_id, receipt=receipt, checkout_token=token
        )

    def find_order(self, *, receipt: str) -> OrderResult | None:
        """`GET /orders/{receipt}` — the lookup alone, never a create. The Order's `order_id` *is*
        the receipt."""
        body = self._get_order(receipt)
        return self._order_result(body, receipt) if body is not None else None

    def ensure_order(
        self,
        *,
        receipt: str,
        amount_minor: int,
        currency: str,
        notes: dict[str, str],
    ) -> OrderResult:
        """The Order for `receipt` if Cashfree holds one. Cashfree will not create a PG Order
        without the paying customer's details, which this signature has no room for — so a missing
        Order is refused (`customer_details_required`); `ensure_checkout` is how one is made."""
        found = self.find_order(receipt=receipt)
        if found is None:
            raise ProviderRejected(PROVIDER, 0, "customer_details_required")
        return found

    def ensure_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        """Find the Order by its receipt (a re-opened checkout, or a create whose answer was lost),
        create it only if Cashfree holds none, and hand back what the browser opens checkout with.

        A found Order whose amount or currency is not the charge's is refused — it is somebody's
        mistake, and reusing it would collect the wrong amount. A found Order that can no longer be
        paid is refused. The create carries an `x-idempotency-key`, and a `409` is treated as
        "it exists": fetched, compared, used."""
        self._require_money(request.amount_minor, request.currency)
        customer = request.customer
        if customer is None or not valid_phone(customer.phone):
            raise ProviderRejected(PROVIDER, 0, "customer_phone_required")

        order = self._existing(request)
        if order is None:
            body: dict[str, Any] = {
                "order_id": request.receipt,
                "order_amount": codec.minor_to_amount(request.amount_minor, request.currency),
                "order_currency": request.currency.upper(),
                "customer_details": {
                    "customer_id": customer.customer_id,
                    "customer_phone": customer.phone,
                    **({"customer_name": customer.name} if customer.name else {}),
                    **({"customer_email": customer.email} if customer.email else {}),
                },
                "order_expiry_time": request.expires_at.isoformat(timespec="seconds"),
                "order_tags": dict(request.notes),
            }
            meta = {
                key: value
                for key, value in (
                    ("return_url", request.return_url),
                    ("notify_url", request.notify_url),
                )
                if value
            }
            if meta:
                body["order_meta"] = meta
            try:
                created = self._wire.request(
                    "POST",
                    "/orders",
                    json=body,
                    headers={"x-idempotency-key": idempotency_key("order", request.receipt)},
                )
            except ProviderRejected as exc:
                if exc.status_code != 409:
                    raise
                order = self._existing(request)
                if order is None:
                    raise
            else:
                order = (
                    self._order_result(created, request.receipt)
                    if isinstance(created, dict)
                    else None
                )
                if order is None:
                    raise ProviderRejected(PROVIDER, 200, "no_order_id")
        if order.checkout_token is None:
            raise ProviderRejected(PROVIDER, 200, "no_checkout_session")
        return CheckoutSession(
            order=order,
            client_action={
                "type": "cashfree_checkout",
                "payment_session_id": order.checkout_token,
                "mode": "production" if self._mode == "production" else "sandbox",
                "openable": self.is_live,
            },
        )

    def _existing(self, request: CheckoutRequest) -> OrderResult | None:
        body = self._get_order(request.receipt)
        if body is None:
            return None
        found_amount = codec.amount_to_minor(body.get("order_amount"), request.currency)
        found_currency = codec.as_text(body.get("order_currency"))
        if (
            found_amount != request.amount_minor
            or (found_currency or "").upper() != request.currency.upper()
        ):
            raise ProviderRejected(PROVIDER, 0, "order_amount_mismatch")
        status = str(body.get("order_status") or "").upper()
        if status in ("EXPIRED", "TERMINATED", "TERMINATION_REQUESTED"):
            raise ProviderRejected(PROVIDER, 0, "order_not_payable")
        return self._order_result(body, request.receipt)

    def order_payments(self, *, order_id: str) -> list[PaymentSnapshot]:
        """`GET /orders/{id}/payments` — every attempt, as Cashfree reports it now. A payment this
        code reads no outcome from (abandoned, never attempted, an unreadable amount) is left out,
        so it can never move a charge."""
        body = self._wire.request(
            "GET", f"/orders/{urllib.parse.quote(order_id, safe='')}/payments"
        )
        items = body if isinstance(body, list) else []
        snapshots = [codec.snapshot_from_payment(item) for item in items]
        return [s for s in snapshots if s is not None]

    def find_payment(self, *, order_id: str, charge_id: str) -> ChargeResult | None:
        """The attempt that says what happened to the Order (`best_attempt`), as a `ChargeResult`.
        Not on the debit path (Cashfree's is direct); present because the seam asks for it."""
        best = best_attempt(self.order_payments(order_id=order_id))
        if best is None:
            return None
        return ChargeResult(
            provider=PROVIDER,
            provider_payment_id=best.provider_payment_id,
            state=best.status,
            amount_minor=best.amount_minor,
            currency=best.currency,
            failure_reason=best.error_code,
            provider_order_id=order_id,
        )

    # ------------------------------------------------------------------ declared, not built

    def find_debit(self, *, charge_ref: str) -> ChargeResult | None:
        raise NotImplementedError(f"find_debit {_NOT_BUILT}")

    def debit(
        self,
        *,
        idempotency_key: str,
        charge_ref: str,
        mandate_id: str | None,
        amount_minor: int,
        currency: str,
        notes: dict[str, str],
    ) -> ChargeResult:
        raise NotImplementedError(f"debit {_NOT_BUILT}")

    def create_mandate(
        self,
        *,
        idempotency_key: str,
        max_amount_minor: int,
        currency: str,
        notes: dict[str, str],
    ) -> MandateResult:
        raise NotImplementedError(f"create_mandate {_NOT_BUILT}")

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
        raise NotImplementedError(f"charge {_NOT_BUILT}")

    def refund(
        self,
        *,
        idempotency_key: str,
        provider_payment_id: str,
        amount_minor: int,
        currency: str,
    ) -> RefundResult:
        raise NotImplementedError(f"refund {_NOT_BUILT}")

    # ------------------------------------------------------------------ webhooks

    def verify_webhook(
        self, *, signature: str | None, body: bytes, timestamp: str | None = None
    ) -> bool:
        """`base64(HMAC-SHA256(secret, timestamp + body))` in constant time. **Fails closed**:
        no secret configured, no signature, no timestamp, or any mismatch is False, never a
        raise."""
        return codec.verify_signature(self._secret_key, signature, timestamp, body)

    def sign(self, body: bytes, timestamp: str) -> str:
        """The signature Cashfree would send for `body` at `timestamp` — for tests and fixtures
        only (the live path only verifies). Needs the secret, like Cashfree does."""
        if not self._secret_key:
            raise RuntimeError("no secret key is configured to sign with")
        return codec.sign(self._secret_key, timestamp, body)

    def redact_webhook(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """What the platform inbox stores of a verified body (`cashfree_codec.redact_webhook_body`):
        an allow-list in the Cashfree envelope shape, amounts as strings, no person."""
        return codec.redact_webhook_body(parsed)

    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        """Normalise a verified Cashfree PG webhook into a `WebhookEvent`.

        Only `PAYMENT_SUCCESS` asserts an outcome (`captured`, binding `cf_payment_id`); every
        other type — a failed attempt, a customer closing the page, settlements — asserts none
        and carries **no** payment id, so an abandoned attempt can never be mistaken for the
        payment that later succeeds on the same Order. The amount is the Order's when the payment
        covers it (a surcharge is accepted) and the payment's when it is below it (a mismatch the
        reconciler must see); a capture whose amount cannot be read exactly is **no outcome**,
        logged at error, and the poll settles it — the check is never silently skipped.

        `raw` is the **redacted** body (no `Decimal`, no customer), so what reaches JSONB can never
        be a person or a number JSON cannot store. The per-delivery id is a header
        (`x-idempotency-header`), applied by the service; nothing here invents one."""
        try:
            parsed = codec.loads(body)
        except ValueError:
            raise MalformedWebhook("the webhook body is not valid JSON") from None
        if not isinstance(parsed, dict):
            raise MalformedWebhook("the webhook body is not a JSON object")

        etype = codec.event_type(parsed)
        data = codec.as_dict(parsed.get("data"))
        order = codec.as_dict(data.get("order"))
        payment = codec.as_dict(data.get("payment"))
        tags = codec.as_dict(order.get("order_tags"))
        currency = codec.as_text(payment.get("payment_currency")) or codec.as_text(
            order.get("order_currency")
        )
        paid = codec.amount_to_minor(payment.get("payment_amount"), currency or "INR")
        ordered = codec.amount_to_minor(order.get("order_amount"), currency or "INR")
        amount = codec.event_amount(ordered, paid)

        outcome = codec.EVENT_OUTCOMES.get(etype, "unknown")
        if outcome == "captured" and str(payment.get("payment_status") or "").upper() != "SUCCESS":
            outcome = "unknown"
        if outcome == "captured" and amount is None:
            log.error("cashfree.webhook.capture_amount_unreadable", event_type=etype[:40])
            outcome = "unknown"
        if outcome == "captured" and paid is not None and ordered is not None and paid > ordered:
            log.warning("cashfree.webhook.paid_above_order_amount", event_type=etype[:40])

        payment_id = codec.as_text(payment.get("cf_payment_id"))
        return WebhookEvent(
            provider=PROVIDER,
            provider_event_id="",
            event_name=etype[:_EVENT_NAME_MAX],
            outcome_state=outcome,
            organization_id=codec.as_text(tags.get("organization_id")),
            charge_id=codec.as_text(tags.get("charge_id")),
            provider_payment_id=payment_id if outcome == "captured" else None,
            failure_reason=None,
            raw=codec.redact_webhook_body(parsed),
            provider_order_id=codec.as_text(order.get("order_id")),
            entity_id=payment_id or codec.as_text(order.get("order_id")),
            created_at=codec.epoch_of(parsed.get("event_time")),
            amount_minor=amount,
            currency=currency.upper() if currency else None,
        )
