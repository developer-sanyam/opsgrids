"""The provider seam — one `Protocol` every processor adapter satisfies.

This interface is deliberately designed against the **more constrained** provider, Razorpay
(ADR-0004 §6). Stripe is the more familiar API, and designing Stripe-first produces abstractions
Razorpay cannot satisfy — mandate caps, a legally required pre-debit notice, multi-day eMandate
activation, and no true idempotency key — discovered only after the interface has ten call sites.
Designing against the tighter constraints first yields an abstraction the looser provider fits
inside easily; the reverse does not hold.

So the shape here encodes Razorpay's constraints as first-class:

* `create_mandate` takes a **cap** (`max_amount_minor`) and returns a mandate whose activation is
  a separate, later moment — an eMandate is not debitable the instant it is created.
* **Lookup before create.** The provider has no idempotency key of its own, so a charge is made
  against a provider **Order** whose `receipt` is the charge's own id (`str(charge.id)`: 36
  characters, globally unique and stable — Razorpay caps `receipt` at 40 and does not enforce its
  uniqueness, so a platform key like `invoice:<uuid>` would neither fit nor be safe):
  `ensure_order` finds the Order by that receipt before it would create one, and `find_payment`
  finds a payment already made against it before `charge` makes another. A resumed call (after a
  crash between the call and recording it, D3) therefore lands on the payment the first call made
  instead of debiting twice. `find_order` is the lookup alone — what the stuck-charge sweep's
  final check at the attempt budget uses, because that check must never create anything.
  `charge` still carries the idempotency key; a Stripe adapter forwards it to Stripe's native
  idempotency header and may answer the lookups trivially.
* **Every wire call is bounded.** The service runs `ensure_order` + `find_payment` + `charge` on a
  worker thread and stops waiting after `Settings.provider_call_timeout_seconds`; the thread cannot
  be cancelled. An adapter's own per-request connect and read timeouts, **summed over those three
  requests**, must therefore stay under `provider_call_timeout_seconds` — otherwise an abandoned
  call can still be in flight when the charge's lease lapses and the sweep's lookup-before-create
  runs, and the late create lands after the lookup found nothing (a second payment). The lease is
  itself validated to be at least twice the call timeout plus a minute (`app/config.py`).
* `verify_webhook` is signature-first: a provider callback is trusted because it carries the
  provider's HMAC over the raw body, never because it carries a session or a token.

Everything here is pure data in and pure data out — no ORM, no session. The adapter maps between
these DTOs and the provider's wire format; the service layer maps between these DTOs and the
`charges` / `mandates` / `provider_events` tables. That two-step keeps the provider's vocabulary
out of the database and the database's vocabulary out of the provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class MandateResult:
    """What a provider returns when a mandate is registered.

    `activated_at` is intentionally optional and usually **absent** at creation: a Razorpay
    eMandate is confirmed by the bank over multiple days, so the mandate exists (`state`
    `created`/`pending`) before it can be debited. The activation arrives later, by webhook.
    """

    provider: str
    provider_mandate_id: str
    state: str
    max_amount_minor: int
    currency: str
    #: ISO-8601 string if the provider activated the mandate synchronously; almost always None.
    activated_at: str | None = None


@dataclass(frozen=True, slots=True)
class OrderResult:
    """A provider Order: what a payment is created against. `receipt` is the platform's charge
    idempotency key — the handle the Order is found again by."""

    provider: str
    provider_order_id: str
    receipt: str


@dataclass(frozen=True, slots=True)
class ChargeResult:
    """What a provider returns when a charge is created.

    A charge is typically `authorized` synchronously and `captured` asynchronously by webhook, so
    this carries the id to reconcile against later, not a final outcome.
    """

    provider: str
    provider_payment_id: str
    state: str
    amount_minor: int
    currency: str
    failure_reason: str | None = None
    #: The Order the payment was made against, when the provider has one.
    provider_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class RefundResult:
    """What a provider returns when a refund is requested."""

    provider: str
    provider_refund_id: str
    state: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """A verified provider callback, normalised to what reconciliation needs.

    `provider_event_id` is what the inbox is idempotent on. `organization_id` and `charge_id` are
    read from the provider's echoed metadata (Razorpay `notes`): the platform stamps them when it
    creates the charge, and the provider returns them on every webhook about it — which is how a
    callback that arrives before any tenant is bound still names its own tenant, authentically,
    because the whole body was signature-verified first.
    """

    provider: str
    provider_event_id: str
    #: The normalised outcome this event asserts about the charge: one of the charge states
    #: (`captured` / `failed` / `authorized`). The adapter maps the provider's event name to it.
    event_name: str
    outcome_state: str
    organization_id: str | None
    charge_id: str | None
    provider_payment_id: str | None
    failure_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    #: The Order the payment was made against, when the provider says (Razorpay: `order_id` on
    #: the payment entity). Recorded on the charge if it has none yet.
    provider_order_id: str | None = None


@runtime_checkable
class PaymentProvider(Protocol):
    """The contract `app/services.py` depends on — never a concrete processor.

    Kept a `Protocol` (structural) rather than an ABC so the adapter and a test double satisfy it
    by shape, exactly the `platform_core` directory/reader precedent. The service layer holds a
    `PaymentProvider`; which concrete one it is (`RazorpayProvider`, a future `StripeProvider`, or
    a fake) is a wiring decision, not a code change in the rules.
    """

    name: str

    def create_mandate(
        self,
        *,
        idempotency_key: str,
        max_amount_minor: int,
        currency: str,
        notes: dict[str, str],
    ) -> MandateResult:
        """Register a standing mandate with the provider, capped at `max_amount_minor`."""
        ...

    def ensure_order(
        self,
        *,
        receipt: str,
        amount_minor: int,
        currency: str,
        notes: dict[str, str],
    ) -> OrderResult:
        """The Order whose `receipt` is `receipt` — **looked up first**, created only if the
        provider holds none. Idempotent by receipt: calling it twice is one Order."""
        ...

    def find_order(self, *, receipt: str) -> OrderResult | None:
        """The Order whose `receipt` is `receipt`, or None — a lookup that **never creates**.
        The stuck-charge sweep's final check uses it at the attempt budget."""
        ...

    def find_payment(self, *, order_id: str, charge_id: str) -> ChargeResult | None:
        """The payment already made against `order_id` (Razorpay: `GET /orders/{id}/payments`,
        matched on the `notes.charge_id` the platform stamped), or None. What turns a resumed
        call into a lookup instead of a second debit."""
        ...

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
        """Create a payment, against `order_id` when given. Only ever called after
        `find_payment` found none for that Order (`services.call_provider_charge`)."""
        ...

    def refund(
        self,
        *,
        idempotency_key: str,
        provider_payment_id: str,
        amount_minor: int,
        currency: str,
    ) -> RefundResult:
        """Refund a captured charge, in full or in part."""
        ...

    def verify_webhook(self, *, signature: str | None, body: bytes) -> bool:
        """True iff `signature` is the provider's HMAC over exactly these bytes.

        Signature-first: a webhook is authentic because of this, and for no other reason. A
        false here is a 400, and the callback is never recorded or acted on.
        """
        ...

    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        """Turn a verified provider body into the normalised `WebhookEvent` reconciliation uses.

        Called only after `verify_webhook` returned True, so it may trust the bytes.
        """
        ...
