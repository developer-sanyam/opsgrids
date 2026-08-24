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
* `charge` takes a caller-supplied **`idempotency_key`**, because the platform must supply one:
  the provider has none of its own, so a retried charge is de-duplicated by *us*, keyed on a
  string *we* chose. A Stripe adapter simply forwards it to Stripe's native idempotency header.
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

    def charge(
        self,
        *,
        idempotency_key: str,
        amount_minor: int,
        currency: str,
        mandate_id: str | None,
        notes: dict[str, str],
    ) -> ChargeResult:
        """Create a charge. `idempotency_key` is platform-supplied — the provider has no native
        one (ADR-0004 §6), so the same key must yield the same charge on the provider side too."""
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
