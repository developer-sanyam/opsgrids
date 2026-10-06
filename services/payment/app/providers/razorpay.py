"""The Razorpay adapter — real everywhere except the wire, which is real only when it is given one.

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
* **Idempotency, as far as Razorpay actually offers it.** Razorpay has no idempotency key on a
  payment create. What it has is the **Order**: created with `receipt` = the platform's charge
  id, fetchable by its id, and searchable by receipt. So the adapter's contract is **lookup before
  create** (`ensure_order`, `find_payment`, then `charge`), and the stubbed wire below keeps the
  state a real account would: Orders by receipt and payments by Order, per adapter instance (one
  per process, `app/deps.py`). A resumed call finds the payment a crashed one made; the ids are
  also derived from the key, so a fresh process agrees on them.

  **What the sandbox showed (the owner's smoke tool, real test keys, 2026-10-06)** — and what the
  adapter is built on since:

  1. `POST /orders` with a receipt that already exists is **accepted** and makes a **second**
     Order: no `400`, no "unique". The receipt is not an idempotency key.
  2. `GET /orders?receipt=<receipt of an Order created seconds earlier>` answered `count: 0` (and
     one call a `429`). The receipt search is **not** a reliable lookup-before-create guard: "found
     none" never proves there is none.
  3. `payment_capture` is not echoed on the Order; it is sent, and nothing reads it back.
  4. A bad key is a `401` with `error.code = BAD_REQUEST_ERROR` (the wire classifies it by status).

  So: an Order whose id the platform has **recorded** is only ever fetched by that id
  (`GET /orders/{id}`, `ensure_checkout` with `recorded_order_id`) — never searched for, never made
  again. The receipt search runs only when no id is recorded (an earlier create whose outcome is
  unknown), as a best-effort recovery that saves a duplicate when it works and costs nothing when
  it does not. The one re-search after a `400` on a create is kept as just that: a `400` is not a
  duplicate receipt, but an exact, same-money Order an earlier create left behind is still the
  better answer than the refusal. A duplicate that slips through is never handed to a browser (the
  service hands out only the recorded id), and `stray_order_payments` is how the poller still looks
  for a payment on one before a checkout is closed. A `429` anywhere is `ProviderTransient`.

What is **stubbed**, unless a wire is given — the one seam:

* **The outbound HTTP to Razorpay.** With no wire, `_call` never opens a socket. Instead it returns
  deterministic fake ids derived from the idempotency key, and the *outcome* of a charge is not
  invented here at all — it is driven by the **webhook fixtures** that reconciliation consumes,
  exactly as production is driven by Razorpay's real webhooks. **Keys never decide this**: a
  provider built with keys and no wire is still the stub (`PAYMENT_WIRE=stub`, the default), which
  is what lets a developer's `.env` hold test keys without any test or local run reaching the
  network. The platform's provider gets a wire only when the owner sets `PAYMENT_WIRE=live`
  (`app/deps.py`); a tenant's collections adapter only when `COLLECTIONS_WIRE=razorpay`.

**What the live platform wire does (Phase A, ADR-0032).** Customer-present checkout only:
`ensure_order` is `GET /orders?receipt=…` then `POST /orders` (with `payment_capture: true`, so an
authorised payment is captured rather than auto-refunded after three days) only if that found none;
a recorded Order is `GET /orders/{id}`; `find_order` is the receipt search alone;
`order_payments` / `find_payment` is `GET /orders/{id}/payments`.
**It never debits**: `charge` and `create_mandate` raise `NotImplementedError` on a wire — the
mandate debit is Phase B — and the service layer refuses a charge that would need one (`no_mandate`,
ADR-0005 Q4) before any call. A platform order is INR only and at least 100 paise, refused here
before any request as well as by the service layer.

**Webhook identity.** A real Razorpay body is `{entity: "event", account_id, event, contains,
payload, created_at}` — no top-level `id`. The per-delivery event id is the `X-Razorpay-Event-Id`
**header**, which `api/webhooks.py` reads and `services.ingest_webhook` prefers. `parse_webhook`
therefore reports a body `id` only if one is present (a fixture may carry one) and otherwise leaves
the id empty for the service to resolve; it never invents a constant.

`notes` is load-bearing and real Razorpay behaviour: metadata attached to a payment is echoed
back on every webhook about it. The platform stamps `organization_id` and `charge_id` into
`notes` at charge-create time, so a later webhook — which arrives with no tenant context — carries
its own tenant, authentically, because the whole body is signature-verified before `notes` is
trusted. (Whether an order's notes reach the payment a customer makes in Checkout is not
documented in the places this was checked; a webhook is therefore never the only way a checkout is
learned, and reconciliation refuses an event whose `order_id` is not the charge's.)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from app.providers.base import (
    ChargeResult,
    CheckoutRequest,
    CheckoutSession,
    MalformedWebhook,
    MandateResult,
    OrderResult,
    PaymentProvider,
    PaymentSnapshot,
    ProviderCallError,
    ProviderCapabilities,
    RefundResult,
    StrayPayment,
    WebhookEvent,
    best_attempt,
)
from app.providers.wire import (
    RAZORPAY_REQUESTS_PER_CHARGE,
    WIRE_CONNECT_TIMEOUT_SECONDS,
    WIRE_READ_TIMEOUT_SECONDS,
    ProviderRejected,
    ProviderTransient,
    ProviderWire,
)

PROVIDER = "razorpay"

#: The longest `event_name` the inbox column holds (`provider_events.event_name`, String(80)).
_EVENT_NAME_MAX = 80

#: Razorpay's payment statuses → the charge state each means. `refunded` is a captured payment
#: whose money has since gone back: the *charge* stays captured and the refund is its own record.
_PAYMENT_STATUS_STATES: dict[str, str] = {
    "created": "created",
    "authorized": "authorized",
    "captured": "captured",
    "refunded": "captured",
    "failed": "failed",
}
#: Razorpay's refund statuses → the platform's refund states.
_REFUND_STATUS_STATES: dict[str, str] = {
    "pending": "created",
    "processed": "processed",
    "failed": "failed",
}

#: How many Orders one receipt search reads. Razorpay does **not** keep a receipt unique (a
#: duplicate create is accepted) and the filter is a "contains" match, so a few may come back; ten
#: is plenty for the exact one and any duplicate an unknown-outcome create left behind.
_RECEIPT_LOOKUP_COUNT = 10
#: The most stray Orders (the charge's exact receipt, not the Order it records) whose payments one
#: `stray_order_payments` asks about: one search plus this many lookups stays well inside a call's
#: deadline beside the recorded Order's own lookup.
_STRAY_ORDER_LIMIT = 2
#: What a Razorpay Order id looks like (`order_` and letters/digits). A recorded id is put in a URL
#: path, so anything else is refused before a request rather than sent.
_ORDER_ID = re.compile(r"^order_[A-Za-z0-9]{1,40}$")

#: Razorpay's minimum charge: 100 paise (Re 1). A platform order below it is refused before a call.
MIN_AMOUNT_MINOR = 100
#: Phase A is INR only (owner decision): Razorpay can take others, the platform bills in rupees.
PLATFORM_CURRENCIES: frozenset[str] = frozenset({"INR"})
#: Cards cannot be debited above Rs 15,000 per debit without the customer's action (Phase B).
MAX_DEBIT_WITHOUT_AFA_MINOR = 1_500_000

#: Razorpay event name → the charge outcome it asserts. The one place this adapter admits it knows
#: Razorpay's vocabulary; `base.py` and the service layer speak only the neutral states.
_EVENT_OUTCOMES: dict[str, str] = {
    "payment.captured": "captured",
    "payment.failed": "failed",
    "payment.authorized": "authorized",
}

#: What the stub signs a checkout callback with when it has no key secret. Stub only: a live
#: adapter with no key secret verifies nothing (`verify_checkout_signature`), and the stub's
#: confirmation is only ever a precondition for a server-side status check against the stub.
_STUB_CHECKOUT_SECRET = "dev-only-razorpay-checkout-signing-secret"  # noqa: S105  # pragma: allowlist secret

# --- what of a webhook body is kept ------------------------------------------------------------
#: Top-level fields of a Razorpay webhook body that are kept. `account_id` is the merchant's
#: own Razorpay account id — the business's, not a person's.
_KEEP_TOP: tuple[str, ...] = ("id", "entity", "account_id", "event", "contains", "created_at")
#: Fields of each entity (`payload.payment.entity`, `payload.order.entity`,
#: `payload.refund.entity`, …) that are kept: identifiers, money, status and stable error codes.
#: Everything else — `email`, `contact`, `card`, `vpa`, `bank`, `wallet`, `notes` (which a
#: browser can set), `acquirer_data`, `error_description` (free text) — is dropped.
_KEEP_ENTITY: tuple[str, ...] = (
    "id",
    "entity",
    "order_id",
    "payment_id",
    "amount",
    "amount_paid",
    "amount_due",
    "amount_refunded",
    "currency",
    "status",
    "method",
    "captured",
    "refund_status",
    "error_code",
    "error_reason",
    "error_source",
    "error_step",
    "receipt",
    "attempts",
    "created_at",
    "speed_requested",
    "speed_processed",
)


def redact_webhook_body(parsed: dict[str, Any]) -> dict[str, Any]:
    """A Razorpay webhook as the inbox keeps it: reduced to what reconciliation reads, with every
    field that could name a customer dropped by construction (an allow-list, so a field Razorpay
    adds tomorrow is dropped too). The inbox is append-only: personal data written there could
    never be erased. Used for tenant collections and, since Phase A, for the platform's own inbox
    — **before** any real event flows into it.

    Keeps exactly what `domain.derive_event_id` is computed from — the event name, each entity's id
    and `created_at` — so a redelivery of a stored event is still recognised as the same event
    (`services._identity_of` re-derives it from the stored body, which therefore must still parse).
    """
    kept: dict[str, Any] = {k: parsed[k] for k in _KEEP_TOP if k in parsed}
    payload = parsed.get("payload")
    reduced: dict[str, Any] = {}
    if isinstance(payload, dict):
        for name, wrapper in payload.items():
            entity = wrapper.get("entity") if isinstance(wrapper, dict) else None
            if isinstance(entity, dict):
                reduced[str(name)] = {"entity": {k: entity[k] for k in _KEEP_ENTITY if k in entity}}
    kept["payload"] = reduced
    return kept


def _as_dict(value: Any) -> dict[str, Any]:
    """`value` if it is a JSON object, else an empty one — Razorpay returns `notes: []` (a list)
    for a payment with no notes, and a body is never trusted to have the nesting it usually has."""
    return value if isinstance(value, dict) else {}


def _as_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _note_text(value: Any) -> str | None:
    """A `notes` value as text. Razorpay notes are strings, but numbers are accepted on the wire; a
    number is kept (as its text) rather than dropped, so a *malformed* organization id is seen as
    present-and-wrong — which is a platform bug worth an error — and not mistaken for absent."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _entity_id(payload: dict[str, Any], event_name: str) -> str | None:
    """The id of the entity an event is about: the one its own name points at (`refund.processed`
    → the refund), else the payment's, else the first other entity in key order — so every event
    a Razorpay account can send identifies itself, and does so the same way on every delivery."""
    own = event_name.split(".", 1)[0]
    for key in (own, "payment", *sorted(k for k in payload if k not in (own, "payment"))):
        found = _as_text(_as_dict(_as_dict(payload.get(key)).get("entity")).get("id"))
        if found is not None:
            return found
    return None


def _snapshot(item: Any) -> PaymentSnapshot | None:
    """One Razorpay payment entity as a `PaymentSnapshot` — ids, status, money and the error
    code only. The entity's `email`, `contact`, `card`, `vpa`, `notes` and the rest are never
    copied out of the response."""
    entity = _as_dict(item)
    payment_id = _as_text(entity.get("id"))
    status = _as_text(entity.get("status"))
    amount = _as_int(entity.get("amount"))
    currency = _as_text(entity.get("currency"))
    if payment_id is None or status is None or amount is None or currency is None:
        return None
    return PaymentSnapshot(
        provider_payment_id=payment_id,
        status=status,
        amount_minor=amount,
        currency=currency.upper(),
        amount_refunded_minor=_as_int(entity.get("amount_refunded")) or 0,
        error_code=_as_text(entity.get("error_code")),
        error_description=_as_text(entity.get("error_description")),
        created_at=_as_int(entity.get("created_at")),
    )


def _fake_id(prefix: str, idempotency_key: str) -> str:
    """A deterministic stand-in for a Razorpay id, derived from the platform idempotency key.

    Deterministic on purpose: the same key produces the same id, which is the observable property
    of a genuinely idempotent create and what a retry relies on. `pay_`, `mandate_`, `rfnd_` echo
    Razorpay's own id prefixes so a fake reads like the real thing in a log.
    """
    digest = hashlib.sha256(f"{prefix}:{idempotency_key}".encode()).hexdigest()[:14]
    return f"{prefix}{digest}"


class RazorpayProvider:
    """A `PaymentProvider` for Razorpay. Network stubbed unless a wire is given."""

    name = PROVIDER
    #: The declared worst-case budget of the live wire: one connect + read timeout per request,
    #: five requests to charge (`RAZORPAY_REQUESTS_PER_CHARGE`: the Order lookup, the Order
    #: create, the re-search after a `400`, the payments lookup, the payment create). 5 x 7s =
    #: 35s, inside the 45s `provider_call_timeout_seconds` default. The stub opens no socket and
    #: honours none of it; the live wire must, and the contract holds it to the sum — and the
    #: wire's overall deadline holds it to the clock (`app/providers/wire.py`).
    request_timeouts_seconds: tuple[float, ...] = (
        WIRE_CONNECT_TIMEOUT_SECONDS + WIRE_READ_TIMEOUT_SECONDS,
    ) * RAZORPAY_REQUESTS_PER_CHARGE

    def __init__(
        self,
        *,
        key_id: str | None,
        key_secret: str | None,
        webhook_secret: str,
        wire: ProviderWire | None = None,
        platform_billing: bool = False,
    ) -> None:
        # The key id is public (the browser is given it); the key secret is held only to verify a
        # checkout callback's HMAC and is never put in a repr, a log or an exception.
        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret
        #: The live HTTP wire (`razorpay_wire.RazorpayHttpWire`). None keeps every call on the
        #: stub — the default for the platform provider and for a collections adapter.
        self._wire = wire
        #: True for OpsGrids' own billing (and only that): orders are captured automatically, and
        #: INR / the minimum amount are enforced before any request. A merchant's collections
        #: adapter keeps its own account's capture setting and any currency it was asked for.
        self._platform_billing = platform_billing
        # The stubbed account's own state — what the real Razorpay would hold and answer lookups
        # from: Orders by receipt, and the payments made against each Order.
        self._orders_by_receipt: dict[str, str] = {}
        self._payments_by_order: dict[str, ChargeResult] = {}
        self._stub_order_payments: dict[str, list[PaymentSnapshot]] = {}

    def __repr__(self) -> str:  # never the key secret or the webhook secret
        return f"RazorpayProvider(key_id={self._key_id!r}, live_wire={self._wire is not None})"

    @property
    def is_live(self) -> bool:
        """Whether calls reach Razorpay over the network (a live wire) or the in-process stub."""
        return self._wire is not None

    @property
    def capabilities(self) -> ProviderCapabilities:
        """What this adapter can do **as built**. The stub keeps the legacy, tolerant posture the
        development flow and every older test rely on. A live platform adapter is checkout-only in
        Phase A: INR, at least 100 paise, no mandate debit (so a debit charge fails `no_mandate`
        before any call), a checkout whose callback carries an HMAC."""
        if self._wire is not None and self._platform_billing:
            return ProviderCapabilities(
                currencies=PLATFORM_CURRENCIES,
                min_amount_minor=MIN_AMOUNT_MINOR,
                debit_shape="order_then_charge",
                requires_mandate_for_debit=True,
                supports_mandate_debit=False,
                supports_checkout=True,
                checkout_confirmation=True,
                max_debit_without_afa_minor=MAX_DEBIT_WITHOUT_AFA_MINOR,
                wire="live",
            )
        return ProviderCapabilities(
            supports_checkout=True,
            checkout_confirmation=True,
            wire="live" if self._wire is not None else "stub",
        )

    def _refuse_debit_on_wire(self, what: str) -> None:
        """A live wire never debits: a collection, and a platform checkout, is paid by the customer
        in Razorpay's own Checkout. A server-side debit over the live wire is Phase B (ADR-0032),
        and is not built; the service layer refuses a charge that would need one."""
        if self._wire is not None:
            raise NotImplementedError(f"{what} is not available on the live wire")

    def _require_platform_money(self, amount_minor: int, currency: str) -> None:
        """INR and at least 100 paise, refused **before any request** — a platform order outside
        them would be a certain `400` that burns an attempt (the service layer checks the same
        thing first; this is the adapter's own line)."""
        if self._wire is None or not self._platform_billing:
            return
        if currency.upper() not in PLATFORM_CURRENCIES:
            raise ProviderRejected(PROVIDER, 0, "unsupported_currency")
        if amount_minor < MIN_AMOUNT_MINOR:
            raise ProviderRejected(PROVIDER, 0, "amount_below_minimum")

    # ------------------------------------------------------------------ the stubbed wire

    def _call(
        self, endpoint: str, request: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        """The ONE stub. On a live wire this would be an authenticated POST to Razorpay; with none
        it returns a deterministic fake response and never touches the network — no keys required.

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
        self._refuse_debit_on_wire("create_mandate")
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
        """`GET /orders?receipt=…` first (`find_order`); `POST /orders` only when that found none.
        The stub answers the search from the state the create left behind.

        **On the live wire the search is best-effort** (Razorpay's can miss an Order created
        seconds earlier, and a duplicate receipt is accepted, not refused): it saves a duplicate
        when an earlier create whose outcome was lost is visible, and proves nothing when it finds
        none. A caller that has an Order id recorded never comes here — it fetches that Order by id
        (`ensure_checkout`). A `429` on the search is `ProviderTransient` and nothing is created. A
        create refused with a `400` is searched once more — an exact, same-money Order an earlier
        create left behind is the answer — and otherwise the `400` stands."""
        # First, before a single request: an order Razorpay would refuse is not worth a lookup.
        self._require_platform_money(amount_minor, currency)
        entity = self._find_entity(receipt)
        if entity is not None:
            self._require_same_order(entity, amount_minor, currency)
            existing: str | None = str(entity["id"])
        else:
            found = self.find_order(receipt=receipt) if self._wire is None else None
            existing = found.provider_order_id if found is not None else None
        if existing is None and self._wire is not None:
            body: dict[str, Any] = {
                "amount": amount_minor,
                "currency": currency,
                "receipt": receipt,
                "notes": notes,
            }
            if self._platform_billing:
                # Authorised-but-never-captured payments are refunded by Razorpay after three days;
                # a platform charge must be captured when the customer pays it.
                body["payment_capture"] = True
            try:
                created = self._wire.request("POST", "/orders", json=body)
            except ProviderRejected as exc:
                if exc.status_code != 400:
                    raise
                again = self._find_entity(receipt)
                if again is None:
                    raise
                self._require_same_order(again, amount_minor, currency)
                return OrderResult(
                    provider=PROVIDER, provider_order_id=str(again["id"]), receipt=receipt
                )
            order_id = _as_text(created.get("id"))
            if order_id is None:
                raise ProviderCallError("razorpay created an order and returned no id")
            return OrderResult(provider=PROVIDER, provider_order_id=order_id, receipt=receipt)
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

    def _find_entity(self, receipt: str) -> dict[str, Any] | None:
        """`GET /orders?receipt=…` on the live wire: an Order entity whose receipt is **exactly**
        `receipt`, or None. Never a create. Always None on the stub, which keeps no entities.
        Best-effort: None is "the search showed none", never "there is none"."""
        found = self._receipt_entities(receipt)
        return found[0] if found else None

    def _receipt_entities(self, receipt: str) -> list[dict[str, Any]]:
        """Every Order entity the receipt search returns whose receipt is **exactly** `receipt`
        (Razorpay's filter "contains" the value, so a longer receipt is not ours), in the order
        Razorpay listed them. Razorpay keeps no receipt unique, so there can be more than one."""
        if self._wire is None:
            return []
        found = self._wire.request(
            "GET", "/orders", params={"receipt": receipt, "count": _RECEIPT_LOOKUP_COUNT}
        )
        items = found.get("items")
        return [
            entity
            for entity in (_as_dict(item) for item in (items if isinstance(items, list) else []))
            if entity.get("receipt") == receipt and _as_text(entity.get("id"))
        ]

    def _recorded_order(
        self, *, order_id: str, receipt: str, amount_minor: int, currency: str
    ) -> OrderResult:
        """The Order a charge already **records**, fetched by its id (`GET /orders/{id}`) — never
        searched for by receipt (the search can miss it, and can return a duplicate instead) and
        never made again. Refused `recorded_order_not_found` (`ProviderTransient`: could not be
        asked, nothing made) when this account does not hold it — a `400`/`404` from Razorpay, an
        id that is not an Order id, or the stub, which knows only the Orders it minted. An Order
        that is someone else's receipt or money is refused, never handed out."""
        self._require_platform_money(amount_minor, currency)
        if self._wire is None:
            if self._orders_by_receipt.get(receipt) != order_id:
                raise ProviderTransient(PROVIDER, 0, "recorded_order_not_found")
            return OrderResult(provider=PROVIDER, provider_order_id=order_id, receipt=receipt)
        if _ORDER_ID.fullmatch(order_id) is None:
            raise ProviderTransient(PROVIDER, 0, "recorded_order_not_found")
        try:
            entity = _as_dict(self._wire.request("GET", f"/orders/{order_id}"))
        except ProviderRejected as exc:
            if exc.status_code in (400, 404):
                # Razorpay answers an id it does not hold with `400 BAD_REQUEST_ERROR`.
                raise ProviderTransient(
                    PROVIDER, exc.status_code, "recorded_order_not_found"
                ) from None
            raise
        if entity.get("id") != order_id:
            raise ProviderTransient(PROVIDER, 200, "recorded_order_not_found")
        found_receipt = _as_text(entity.get("receipt"))
        if found_receipt is not None and found_receipt != receipt:
            raise ProviderRejected(PROVIDER, 0, "order_receipt_mismatch")
        self._require_same_order(entity, amount_minor, currency)
        return OrderResult(provider=PROVIDER, provider_order_id=order_id, receipt=receipt)

    def stray_order_payments(self, *, receipt: str, recorded_order_id: str) -> list[StrayPayment]:
        """Payments on Orders that carry this charge's **exact** `receipt` but are not the Order it
        records — a duplicate an unknown-outcome create left behind (Razorpay accepts a duplicate
        receipt). Nobody was handed such an Order's id, so a payment on one should be impossible;
        the poller asks anyway before a checkout is closed, and through its late window
        (`checkout/services.record_poll`).

        **Best-effort, like every receipt search here**: it finds what Razorpay's search shows, at
        most `_STRAY_ORDER_LIMIT` Orders. An Order whose `notes.charge_id` names another charge is
        not this charge's and is skipped. Empty on the stub, which never makes a duplicate. A
        failed request raises, classified by the wire; the caller treats that as "nothing found"."""
        if self._wire is None:
            return []
        strays: list[str] = []
        for entity in self._receipt_entities(receipt):
            order_id = str(entity["id"])
            charge_note = _note_text(_as_dict(entity.get("notes")).get("charge_id"))
            if order_id == recorded_order_id or order_id in strays:
                continue
            if charge_note is not None and charge_note != receipt:
                continue
            strays.append(order_id)
        return [
            StrayPayment(provider_order_id=order_id, snapshot=snapshot)
            for order_id in strays[:_STRAY_ORDER_LIMIT]
            for snapshot in self.order_payments(order_id=order_id)
        ]

    @staticmethod
    def _require_same_order(entity: dict[str, Any], amount_minor: int, currency: str) -> None:
        """An Order found by a charge's receipt must be for the charge's own money. One that says
        otherwise is somebody's mistake, and reusing it would collect the wrong amount: refused
        (`order_amount_mismatch`), never silently used. An entity that carries no amount or currency
        is not contradicting anything."""
        amount = _as_int(entity.get("amount"))
        found_currency = _as_text(entity.get("currency"))
        if (amount is not None and amount != amount_minor) or (
            found_currency is not None and found_currency.upper() != currency.upper()
        ):
            raise ProviderRejected(PROVIDER, 0, "order_amount_mismatch")

    def find_order(self, *, receipt: str) -> OrderResult | None:
        """`GET /orders?receipt=…` — the search alone, never a create. On the live wire None means
        "the search showed none", which is not proof that there is none (Razorpay's search can lag
        a create by seconds); a recorded Order is fetched by its id instead."""
        if self._wire is not None:
            entity = self._find_entity(receipt)
            if entity is None:
                return None
            return OrderResult(
                provider=PROVIDER, provider_order_id=str(entity["id"]), receipt=receipt
            )
        existing = self._orders_by_receipt.get(receipt)
        if existing is None:
            return None
        return OrderResult(provider=PROVIDER, provider_order_id=existing, receipt=receipt)

    def order_payments(self, *, order_id: str) -> list[PaymentSnapshot]:
        """`GET /orders/{id}/payments` — every attempt against the Order, as Razorpay reports it
        now. The stub answers from what a test recorded (`stub_record_payment`) plus any payment
        its own `charge` made."""
        if self._wire is not None:
            found = self._wire.request("GET", f"/orders/{order_id}/payments")
            items = found.get("items")
            snapshots = [_snapshot(item) for item in (items if isinstance(items, list) else [])]
            return [s for s in snapshots if s is not None]
        if self._platform_billing and not self._stub_knows(order_id):
            # The platform's stub keeps its Orders in this process only. An Order it never minted
            # — one made by a live wire before the switch was turned back to `stub`, or by this
            # process before a restart — is not "an Order with no payments": the stub cannot tell.
            # Saying `[]` would let the poller expire a checkout a customer may have paid at the
            # real provider. Not being able to ask is the honest answer, and it changes nothing.
            #
            # **The platform adapter only.** A merchant's collections adapter is built afresh for
            # every call (`deps._collections_provider`, never cached), so its stub never holds the
            # Order it is asked about; it keeps the "no payments" answer it has always given, or
            # every stub-wire sync, cancel and expiry of a collection would fail.
            raise ProviderTransient(PROVIDER, 0, "stub_holds_no_such_order")
        recorded = list(self._stub_order_payments.get(order_id, []))
        made = self._payments_by_order.get(order_id)
        if made is not None and all(
            s.provider_payment_id != made.provider_payment_id for s in recorded
        ):
            recorded.append(
                PaymentSnapshot(
                    provider_payment_id=made.provider_payment_id,
                    status=made.state,
                    amount_minor=made.amount_minor,
                    currency=made.currency,
                )
            )
        return recorded

    def _stub_knows(self, order_id: str) -> bool:
        """Whether this stub minted or was told about `order_id` (the stub's own bookkeeping)."""
        return (
            order_id in self._orders_by_receipt.values()
            or order_id in self._stub_order_payments
            or order_id in self._payments_by_order
        )

    def stub_record_payment(self, order_id: str, snapshot: PaymentSnapshot) -> None:
        """What a customer paying in Checkout would leave behind at the provider — for tests and
        local development against the stub only; a live wire has no such door. Replaces an
        earlier snapshot of the same payment, so a test can move one attempt through states."""
        if self._wire is not None:
            raise RuntimeError("a live provider's payments are made by customers, not recorded")
        kept = [
            s
            for s in self._stub_order_payments.get(order_id, [])
            if s.provider_payment_id != snapshot.provider_payment_id
        ]
        self._stub_order_payments[order_id] = [*kept, snapshot]

    def find_payment(self, *, order_id: str, charge_id: str) -> ChargeResult | None:
        """`GET /orders/{id}/payments`, matched on the `notes.charge_id` the platform stamped.

        On the live wire every attempt on the Order is the charge's (its receipt is the charge's
        id), so the answer is the attempt that says what happened (`best_attempt`)."""
        if self._wire is not None:
            best = best_attempt(self.order_payments(order_id=order_id))
            if best is None:
                return None
            return ChargeResult(
                provider=PROVIDER,
                provider_payment_id=best.provider_payment_id,
                state=_PAYMENT_STATUS_STATES.get(best.status, "created"),
                amount_minor=best.amount_minor,
                currency=best.currency,
                failure_reason=best.error_code,
                provider_order_id=order_id,
            )
        return self._payments_by_order.get(order_id)

    # ------------------------------------------------------------------ checkout

    def ensure_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        """The Order a customer pays in Razorpay's Checkout and what `checkout.js` is opened with:
        the **public** key id and the Order id. Nothing a browser must not have, and nothing about
        the customer (the customer is not sent to Razorpay here at all). `openable` is False on the
        stub: its Order id is a local stand-in no real Checkout would accept.

        A charge that already records an Order (`request.recorded_order_id`) gets **that Order**,
        fetched by its id (`_recorded_order`): never a receipt search, which could miss it or hand
        back a duplicate, and never a create — an adapter that cannot find it is not the account
        that made it, and never mints one. A charge that records none goes through `ensure_order`
        (a best-effort search, then a create); the service records the id before any browser sees
        it."""
        if request.recorded_order_id is None:
            order = self.ensure_order(
                receipt=request.receipt,
                amount_minor=request.amount_minor,
                currency=request.currency,
                notes=request.notes,
            )
        else:
            order = self._recorded_order(
                order_id=request.recorded_order_id,
                receipt=request.receipt,
                amount_minor=request.amount_minor,
                currency=request.currency,
            )
        return CheckoutSession(
            order=order,
            client_action={
                "type": "razorpay_checkout",
                "key_id": self._key_id or "",
                "order_id": order.provider_order_id,
                "openable": self._wire is not None,
            },
        )

    def sign_checkout(self, order_id: str, payment_id: str) -> str:
        """The signature Checkout's callback would carry for this payment — `HMAC-SHA256` of
        `order_id|payment_id` under the key secret (the stub's own dev secret when it has none).
        For tests and local development; the live path only ever **verifies**."""
        secret = self._key_secret or _STUB_CHECKOUT_SECRET
        return hmac.new(
            secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
        ).hexdigest()

    def verify_checkout_signature(self, *, order_id: str, payment_id: str, signature: str) -> bool:
        """Constant-time check of a Checkout callback's `razorpay_signature`. **A precondition for
        asking Razorpay what happened, never proof of payment**: the browser's callback is a client
        saying "paid", and success is reported only after `order_payments` shows the payment. False
        — never an exception — for anything that is not the exact ASCII hex digest, and always
        False for a live adapter that holds no key secret."""
        if not order_id or not payment_id or not signature or not signature.isascii():
            return False
        if self._wire is not None and not self._key_secret:
            return False
        return hmac.compare_digest(self.sign_checkout(order_id, payment_id), signature)

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
        self._refuse_debit_on_wire("charge")
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
        if self._wire is not None:
            # `X-Refund-Idempotency`: the same key is the same refund at Razorpay, so a call
            # abandoned on timeout and retried never refunds twice. `speed: normal` — the only
            # speed every payment method supports; `optimum` is a merchant pricing decision.
            created = self._wire.request(
                "POST",
                f"/payments/{provider_payment_id}/refund",
                json={"amount": amount_minor, "speed": "normal"},
                headers={"X-Refund-Idempotency": idempotency_key},
            )
            refund_id = _as_text(created.get("id"))
            if refund_id is None:
                raise ProviderCallError("razorpay created a refund and returned no id")
            return RefundResult(
                provider=PROVIDER,
                provider_refund_id=refund_id,
                state=_REFUND_STATUS_STATES.get(str(created.get("status")), "created"),
                amount_minor=_as_int(created.get("amount")) or amount_minor,
                currency=(_as_text(created.get("currency")) or currency).upper(),
            )
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
        # `compare_digest` on a `str` raises `TypeError` for a non-ASCII one — a 500 (retried for
        # days) for a header anyone can send. A real HMAC is hex; anything else is simply wrong.
        if not signature or not signature.isascii():
            return False
        expected = hmac.new(self._webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def sign(self, body: bytes) -> str:
        """The signature Razorpay would send for `body`. Not part of the provider Protocol — it
        exists so tests (and the injected fixtures) can produce an authentic signature without a
        real Razorpay, and so a valid callback can be exercised end to end without live keys."""
        return hmac.new(self._webhook_secret.encode(), body, hashlib.sha256).hexdigest()

    def redact_webhook(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """What the platform inbox stores of a verified body (`redact_webhook_body`)."""
        return redact_webhook_body(parsed)

    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        """Normalise a verified Razorpay webhook body into a `WebhookEvent`.

        Razorpay's shape: a top-level `event` name and `created_at`, and the payment entity under
        `payload.payment.entity`, whose `notes` echo the metadata the platform stamped at
        charge-create time. Reconciliation reads the tenant and the charge from those notes.

        **There is no top-level `id`.** The per-delivery event id is the `X-Razorpay-Event-Id`
        header, which is not part of the body and so not something this method can see; the route
        reads it and `services.ingest_webhook` resolves the id the inbox keys on. A body `id` is
        reported when one exists (a test fixture may carry one) and is otherwise the empty string,
        which the service treats as "none" — it is never itself used as a key.

        Every read is defensive: the body is signed, so it is Razorpay's, but a missing or
        oddly-typed field must not become a 500 that Razorpay retries for days.
        """
        try:
            parsed = json.loads(body)
        except ValueError:
            raise MalformedWebhook("the webhook body is not valid JSON") from None
        if not isinstance(parsed, dict):
            raise MalformedWebhook("the webhook body is not a JSON object")

        event_name = str(parsed.get("event") or "")[:_EVENT_NAME_MAX]
        payload = _as_dict(parsed.get("payload"))
        entity = _as_dict(_as_dict(payload.get("payment")).get("entity"))
        notes = _as_dict(entity.get("notes"))
        return WebhookEvent(
            provider=PROVIDER,
            # Only a body that carries one; the header wins and is applied by the service.
            provider_event_id=_as_text(parsed.get("id")) or "",
            event_name=event_name,
            outcome_state=_EVENT_OUTCOMES.get(event_name, "unknown"),
            organization_id=_note_text(notes.get("organization_id")),
            charge_id=_note_text(notes.get("charge_id")),
            provider_payment_id=_as_text(entity.get("id")),
            failure_reason=_as_text(entity.get("error_description")),
            raw=parsed,
            provider_order_id=_as_text(entity.get("order_id")),
            entity_id=_entity_id(payload, event_name),
            created_at=_as_int(parsed.get("created_at")),
            # Razorpay's `amount` is the smallest currency unit — the platform's minor unit.
            amount_minor=_as_int(entity.get("amount")),
            currency=_as_text(entity.get("currency")),
        )


def build_provider(
    *,
    key_id: str | None,
    key_secret: str | None,
    webhook_secret: str,
    wire: ProviderWire | None = None,
) -> PaymentProvider:
    """Construct the platform's Razorpay provider. `wire` is the live HTTP wire, or None for the
    stub — `app.deps` decides which from `PAYMENT_WIRE`, and never from the presence of keys."""
    return RazorpayProvider(
        key_id=key_id,
        key_secret=key_secret,
        webhook_secret=webhook_secret,
        wire=wire,
        platform_billing=True,
    )


def build_collections_provider(
    *, key_id: str, key_secret: str, webhook_secret: str, wire: ProviderWire | None
) -> RazorpayProvider:
    """One merchant account's adapter (ADR-0024 §8: "one adapter instance per merchant account,
    built from that account's sealed keys"). `wire` is the live HTTP wire, or None for the stub —
    `app.deps.get_collections_provider_factory` decides which from configuration."""
    return RazorpayProvider(
        key_id=key_id, key_secret=key_secret, webhook_secret=webhook_secret, wire=wire
    )
