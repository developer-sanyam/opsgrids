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

  **Against real Razorpay the receipt lookup is best-effort, never proof** (sandbox, observed
  2026-10-06 by the owner's smoke tool): `POST /orders` with a receipt that already exists is
  *accepted* and makes a second Order, and `GET /orders?receipt=` for an Order created seconds
  earlier returned none (one call a `429`). So "found nothing" does not mean "there is none", and an
  Order whose id this platform has **recorded** is fetched by that id (`GET /orders/{id}`), never
  searched for by receipt. Every path that hands an Order to a customer records its id first and
  hands out only the recorded id (`checkout/services.py`); a duplicate an unknown-outcome retry
  made is never shown to anyone, and the poller still searches for one before it closes a checkout
  (`StrayPayment`). The debit shape below is held to `ProviderContract` against the stub, whose
  receipt index is exact; a live order-then-charge debit (Phase B) must record the Order id before
  it creates the payment, because the receipt lookup alone cannot stop a second Order.
* **Every wire call is bounded.** The service runs `ensure_order` + `find_payment` + `charge` on a
  worker thread and stops waiting after `Settings.provider_call_timeout_seconds`; the thread cannot
  be cancelled. An adapter's own per-request connect and read timeouts, **summed over those three
  requests**, must therefore stay under `provider_call_timeout_seconds` — otherwise an abandoned
  call can still be in flight when the charge's lease lapses and the sweep's lookup-before-create
  runs, and the late create lands after the lookup found nothing (a second payment). The lease is
  itself validated to be at least twice the call timeout plus a minute (`app/config.py`).
* `verify_webhook` is signature-first: a provider callback is trusted because it carries the
  provider's HMAC over the raw body, never because it carries a session or a token.

**The live adapter is bound by all of this (B5g), by the contract for its shape (ADR-0032).** The
Razorpay adapter has a stubbed wire by default and a live one (`razorpay_wire.RazorpayHttpWire`)
behind `PAYMENT_WIRE=live`; when it is live it implements the lookup-before-create contract above
as far as Razorpay allows — `ensure_order` searches for the Order by `receipt` before it creates one
(best-effort: see above; and it looks once more after a `400`, a harmless recovery of an Order an
earlier unknown-outcome create left behind — Razorpay does not refuse a duplicate receipt),
`find_order` never creates, `find_payment` finds a payment already made against an Order, `charge`
is reached only after that lookup found none — and declares per-request timeouts
(`request_timeouts_seconds`) whose sum stays inside `provider_call_timeout_seconds`. Nothing else in
the platform re-checks it: a live adapter that "just creates" debits a customer twice after a crash
between the call and its record. Which contract is the definition of done depends on the adapter's
**shape**, because one `ProviderContract` cannot describe both a processor that creates a payment
against an Order (`debit_shape = "order_then_charge"`, Razorpay: `ProviderContract`, unchanged) and
one that debits a saved mandate directly, with no Order step at all (`debit_shape = "direct"`,
Cashfree subscriptions: `DirectDebitContract`, the sibling with the same invariants), and a
customer-present checkout is a third (`CheckoutContract`). An adapter does not merge red on the
contract for its shape.

**Failures are classified at the seam** (`app/providers/wire.py`): a wire raises
`ProviderTransient` (outcome unknown), `ProviderRejected` (provably nothing created) or
`ProviderMisconfigured` (the platform's credentials), and the service layer treats the three
differently — see there.

**Webhook identity.** A provider's per-delivery event id is not always in the body — Razorpay
carries it in the `X-Razorpay-Event-Id` header and its body has no top-level `id`. So
`WebhookEvent.provider_event_id` is only what `parse_webhook` could read from the body (possibly
empty), and `services.ingest_webhook` decides the id the inbox is idempotent on (the transport's
id, else the body's, else a digest of the body's own identifying fields) and refuses a webhook that
yields none. An adapter must never invent a constant for it: an empty string is one key for every
event an organization ever receives.

Everything here is pure data in and pure data out — no ORM, no session. The adapter maps between
these DTOs and the provider's wire format; the service layer maps between these DTOs and the
`charges` / `mandates` / `provider_events` tables. That two-step keeps the provider's vocabulary
out of the database and the database's vocabulary out of the provider.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable


class ProviderCallError(Exception):
    """A provider call that reached the provider and came back unusable — an error status, or a
    success with no id in it. Its message never carries a response body or a credential, so it is
    safe to log; the caller treats it as "the provider did not do it" only where that is provably
    true, and as "unknown" everywhere else."""


class MalformedWebhook(ValueError):
    """A body that verified but cannot be read as a provider event — not JSON, or not an object.
    Raised by `parse_webhook`; the service answers 400 and records nothing. It carries no part of
    the body: the message is safe to log."""


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


@dataclass(frozen=True, slots=True, repr=False)
class CheckoutRedirect:
    """Where a redirect-checkout provider (PhonePe, PayU, Paytm) takes the customer's browser: a
    `GET` of `url`, or a form `POST` of `fields` to it. The URL or fields carry a short-lived token
    or a salted hash, so this is **in memory only**, like `OrderResult.checkout_token`: never
    stored, never logged, and `repr` is suppressed so a traceback cannot print it."""

    url: str
    method: Literal["GET", "POST"] = "GET"
    fields: Mapping[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"CheckoutRedirect(method={self.method!r})"


@dataclass(frozen=True, slots=True)
class OrderResult:
    """A provider Order: what a payment is created against. `receipt` is the platform's charge
    idempotency key — the handle the Order is found again by (Cashfree: the merchant-chosen
    `order_id`)."""

    provider: str
    provider_order_id: str
    receipt: str
    #: What the customer's browser needs to open the provider's hosted checkout, when the provider
    #: hands one back with the Order (Cashfree's `payment_session_id`). **In memory only**: never
    #: stored, never logged — `repr=False` keeps it out of a traceback too — and re-fetched from
    #: the provider when a checkout is reopened.
    checkout_token: str | None = field(default=None, repr=False)
    #: For a redirect-checkout provider, where the browser goes to pay (see `CheckoutRedirect`).
    redirect: CheckoutRedirect | None = field(default=None, repr=False)


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

    `provider_event_id` is what the inbox is idempotent on **once `services.ingest_webhook` has
    resolved it**: as returned by `parse_webhook` it is only the id the body itself carries, and is
    empty for a provider (Razorpay) whose event id travels in a header. `entity_id` and
    `created_at` are the body's own identifying fields — the id of the payment (or other entity)
    the event is about, and the provider's event timestamp — from which a stable id is derived when
    no delivery id is available. `amount_minor` and `currency` are what the provider says the
    payment was for, so reconciliation can refuse a payment that is not the charge it claims to
    settle. `organization_id` and `charge_id` are read from the provider's echoed metadata
    (Razorpay `notes`): the platform stamps them when it creates the charge, and the provider
    returns them on every webhook about it — which is how a callback that arrives before any
    tenant is bound still names its own tenant, authentically, because the whole body was
    signature-verified first.
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
    #: The entity the event is about (Razorpay: the payment's id, else the first entity the
    #: payload carries) and the provider's event timestamp in epoch seconds — the two body fields
    #: the fallback event id is derived from. None when the body does not carry them.
    entity_id: str | None = None
    created_at: int | None = None
    #: What the provider says the payment was for, in integer minor units and an ISO currency.
    #: None when the body does not say — reconciliation then has nothing to compare.
    amount_minor: int | None = None
    currency: str | None = None
    #: What put this observation in front of reconciliation: `webhook` (a signature-verified
    #: delivery) or `sync` (a server-to-server status check — the poller, a checkout's read-through
    #: and its confirm). Audit rows are attributed `<provider>-<source>`.
    source: str = "webhook"


@dataclass(frozen=True, slots=True)
class PaymentSnapshot:
    """One payment attempt against a provider Order, **as the provider reports it now** — the
    answer a server-to-server status check gets (`CollectionsProvider.order_payments`), used by
    tenant collections (`app/collections/`) where a client's word is never evidence.

    `status` is the provider's own vocabulary, mapped to a charge state by
    `app.collections.domain.state_for_provider_status`, never compared as a string anywhere else.
    Carries no person: no email, phone, card or UPI handle — the adapter never copies them out.
    """

    provider_payment_id: str
    status: str
    amount_minor: int
    currency: str
    amount_refunded_minor: int = 0
    #: The provider's stable error code for a failed attempt (Razorpay `error_code`), and its
    #: human description, which is only ever stored truncated, never logged.
    error_code: str | None = None
    error_description: str | None = None
    #: Epoch seconds, the provider's own.
    created_at: int | None = None


@dataclass(frozen=True, slots=True)
class StrayPayment:
    """A payment attempt on an Order that carries a charge's exact `receipt` but is **not** the
    Order the charge records — a duplicate an unknown-outcome create left behind (Razorpay accepts
    a duplicate receipt). Nobody was ever handed its id, so a customer should not be able to pay
    it; the poller asks anyway before it closes a checkout (`checkout/services.record_poll`), and
    one that moved money is recorded against the charge, loudly."""

    provider_order_id: str
    snapshot: PaymentSnapshot


@runtime_checkable
class PaymentProvider(Protocol):
    """The contract `app/services.py` depends on — never a concrete processor.

    Kept a `Protocol` (structural) rather than an ABC so the adapter and a test double satisfy it
    by shape, exactly the `platform_core` directory/reader precedent. The service layer holds a
    `PaymentProvider`; which concrete one it is (`RazorpayProvider`, a future `StripeProvider`, or
    a fake) is a wiring decision, not a code change in the rules.
    """

    name: str
    #: The connect + read timeout of every outbound request one full charge can make, in the worst
    #: case, in call order (Razorpay: the Order lookup, the Order create, the payments lookup, the
    #: payment create). Their **sum** must not exceed `Settings.provider_call_timeout_seconds` —
    #: the caller stops waiting there, and a request still in flight after the lease lapses could
    #: land a second create. `ProviderContract` asserts it for every adapter. **It is a sum, not a
    #: wall-clock bound**: an HTTP client's read timeout restarts whenever bytes arrive, so a live
    #: adapter also needs an overall per-call deadline (ADR-0004, B5g caveats).
    request_timeouts_seconds: tuple[float, ...]

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

        Called only after `verify_webhook` returned True, so it may trust the bytes as the
        provider's — not that they are well-formed: a body that is not a JSON object raises
        `MalformedWebhook`, and any field of an otherwise valid body may be missing or oddly typed.
        Leaves `provider_event_id` empty when the body carries none; it never invents one.
        """
        ...

    def order_payments(self, *, order_id: str) -> list[PaymentSnapshot]:
        """Every payment attempt against `order_id`, as the provider reports it now (Razorpay and
        Cashfree both: `GET /orders/{id}/payments`). An Order this platform created has `receipt`
        = the charge's own id, so every attempt on it belongs to that charge.

        Promoted into the neutral interface for platform billing's checkout: the answer to this
        call — or a signature-verified webhook — is the only thing that may ever move a charge to
        `captured`. The customer's browser saying "paid" is never evidence (ADR-0024 §8,
        ADR-0032).
        """
        ...


@dataclass(frozen=True, slots=True)
class CollectionRefundFacts:
    """A refund entity from a verified merchant refund webhook — ids, money and the refund's state
    in the platform's own vocabulary (`created` / `processed` / `failed`; the adapter maps its
    provider's words into it). Never a person."""

    provider_refund_id: str
    provider_payment_id: str | None
    amount_minor: int | None
    currency: str | None
    state: str


@dataclass(frozen=True, slots=True)
class CollectionWebhookFacts:
    """What collections reads from one verified merchant webhook, beyond the `WebhookEvent`
    itself, in the platform's vocabulary — so `app/collections/` never learns a provider's words.

    `outcome` is the collection outcome a payment event asserts (`captured` / `failed` /
    `authorized`), or None for an event that asserts none (a customer closing the page, a refund,
    a settlement). `refund` is set for a refund event. `failure_code` is the provider's stable
    error code for a failed attempt. `stored` is what the append-only inbox keeps of the body: an
    allow-list the adapter's own `parse_webhook` can still read back (`_identity_of` re-derives a
    stored event's identity from it)."""

    outcome: str | None
    refund: CollectionRefundFacts | None
    failure_code: str | None
    stored: dict[str, Any]


@runtime_checkable
class CollectionsProvider(Protocol):
    """The seam tenant collections (`app/collections/`, ADR-0024 §8) holds — **standalone**, not a
    `PaymentProvider` (ADR-0033): one instance per merchant account, built per call from that
    account's own sealed keys and never cached.

    A collection is paid by the customer, in the provider's own hosted checkout, against an Order
    this platform created; there is no server-side debit, so there is no `charge`,
    `create_mandate` or `find_payment` here for an adapter to stub. What collections needs:

    * `ensure_order` — the Order whose receipt (the collection's own id) is `receipt`, **looked up
      first** and created only if the provider holds none. `payer` is who is paying, for a provider
      that requires a customer on every Order (Cashfree: an id and a phone); passed through to the
      provider and nowhere else. `expires_at` is when the provider should stop taking payment and
      `notify_url` where it should deliver this Order's webhooks (both ignored by a provider that
      has no such field). The result's `checkout_token` is what the browser opens the provider's
      checkout with when that is not the Order id itself (Cashfree's `payment_session_id`): in
      memory only, never stored. `recorded_order_id` is the Order the collection **already
      records**, on a reopen: the adapter fetches that Order by its id and returns it, or refuses
      (`ProviderTransient`, `recorded_order_not_found`) — never a receipt search, never another
      create (the recorded-Order rule of `CheckoutRequest.recorded_order_id`, ADR-0032).
      `return_url` is where a redirect-checkout provider sends the customer's browser afterwards
      (ignored by a provider whose checkout is a modal on the merchant's page).
    * `find_order` — the lookup alone, never a create (the expiry sweep, for a collection that
      recorded no Order).
    * `order_payments` — every attempt on an Order as the provider reports it now, in the neutral
      vocabulary (`best_attempt`). This answer, or a signature-verified webhook, is the only thing
      that may ever move a collection to `captured`.
    * `refund` — by the payment and its Order (Cashfree refunds an Order, Razorpay a payment); the
      same `idempotency_key` is the same refund at the provider.
    * `verify_webhook(headers, body)` — the provider's signature over these exact bytes, with
      whatever headers it signs (Cashfree signs a timestamp header too). Fails closed: False,
      never a raise, for anything that is not an exact match.
    * `parse_webhook`, `webhook_delivery_id`, `webhook_facts` — a verified body as a
      `WebhookEvent`, the transport's (unsigned) delivery id, and the collection facts in the
      platform's vocabulary.
    * `probe` — one authenticated, read-only request that proves the credentials work. Returns on
      success; raises `ProviderMisconfigured` when the provider rejects the credentials and
      another `ProviderCallError` (or an `httpx` error) when it could not tell.

    **Against real Razorpay the receipt lookup is best-effort** (see the module docstring): a
    create whose answer was lost can leave an Order the search does not show, and the retry then
    makes a second one. Collections binds the first Order id it is handed (`record_checkout`) and
    from then on hands out and asks about only that id, so a duplicate is never payable — nobody is
    ever given its id. Unlike the platform poller, collections does not search for payments on one
    (`StrayPayment`).

    Every request is bounded: the declared per-request timeouts, summed, stay inside
    `collections_call_timeout_seconds` (`CollectionsContract`).
    """

    name: str
    request_timeouts_seconds: tuple[float, ...]

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
    ) -> OrderResult: ...

    def find_order(self, *, receipt: str) -> OrderResult | None: ...

    def order_payments(self, *, order_id: str) -> list[PaymentSnapshot]: ...

    def refund(
        self,
        *,
        idempotency_key: str,
        provider_payment_id: str,
        amount_minor: int,
        currency: str,
        provider_order_id: str | None = None,
    ) -> RefundResult: ...

    def verify_webhook(self, *, headers: Mapping[str, str], body: bytes) -> bool: ...

    def parse_webhook(self, *, body: bytes) -> WebhookEvent: ...

    def webhook_delivery_id(self, headers: Mapping[str, str]) -> str | None: ...

    def webhook_facts(self, event: WebhookEvent) -> CollectionWebhookFacts: ...

    def probe(self) -> None: ...


# --------------------------------------------------------------------------- capabilities


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """What one adapter, as built, can and cannot do — read by the service layer **before** any
    provider call, so a charge that could only fail at the provider fails here instead, with no
    call made and no bank fee paid (ADR-0005 Q4, ADR-0032).

    The defaults are the legacy stub's: it accepts any currency and amount, and "debits" with no
    mandate (the development posture every older test depends on). A live adapter narrows them.
    """

    #: Accepted ISO currencies; None means any (the legacy stub).
    currencies: frozenset[str] | None = None
    #: The smallest amount, in minor units, the provider will take (Razorpay: 100 paise).
    min_amount_minor: int = 0
    #: `order_then_charge` (create an Order, then a payment against it — Razorpay) or `direct`
    #: (debit a saved mandate with no Order step — Cashfree subscriptions). Picks the call path
    #: (`services.call_provider_charge`) and the contract the adapter is held to.
    debit_shape: Literal["order_then_charge", "direct"] = "order_then_charge"
    #: A server-side debit needs a customer's saved mandate. When True and none is usable, the
    #: charge fails as `no_mandate` with **no provider call** (ADR-0005 Q4).
    requires_mandate_for_debit: bool = False
    #: Whether this build can actually make the debit under a mandate. False for every live
    #: adapter in Phase A (customer-present checkout only): a mandate then does not make a charge
    #: debitable, and the charge fails as `no_mandate` rather than reaching an unbuilt call.
    supports_mandate_debit: bool = True
    #: Whether a customer can pay in the provider's hosted checkout (`CheckoutProvider`).
    supports_checkout: bool = False
    #: Whether opening a checkout needs the paying customer's details (Cashfree: a customer id and
    #: a phone on every Order). When True a request without a usable phone is refused (422) before
    #: any call — a phone is never invented.
    requires_customer_details: bool = False
    #: Whether the browser's checkout callback carries an HMAC the server can check as a
    #: precondition for a status check (Razorpay: `HMAC(order_id|payment_id)`). Never evidence.
    checkout_confirmation: bool = False
    #: Whether `verify_webhook` needs the request's headers (Cashfree signs a timestamp header plus
    #: the body), and so the route must pass them (`services.ingest_webhook(headers=...)`).
    signs_headers: bool = False
    #: The largest single debit a customer need not re-authenticate for, when the provider has one
    #: (Razorpay cards and Cashfree UPI: Rs 15,000). Larger amounts go to checkout.
    max_debit_without_afa_minor: int | None = None
    #: `stub` (no socket is opened, whatever keys are present) or `live`.
    wire: Literal["stub", "live"] = "stub"


#: The capabilities of an adapter that declares none: a test double, or a stub built before this
#: existed. Never refuses anything.
LEGACY_CAPABILITIES = ProviderCapabilities()


def capabilities_of(provider: object) -> ProviderCapabilities:
    """`provider`'s declared capabilities, or the legacy ones for an adapter that declares none."""
    declared = getattr(provider, "capabilities", None)
    return declared if isinstance(declared, ProviderCapabilities) else LEGACY_CAPABILITIES


#: What an adapter, as built, is running on: `stub` (no provider is called at all), `test` (a
#: live wire on test / sandbox credentials: the provider's test cards pay, no real money moves) or
#: `live` (real money).
CredentialsMode = Literal["stub", "test", "live"]


def credentials_mode_of(provider: object) -> CredentialsMode:
    """`provider`'s declared `credentials_mode`, or `stub` for an adapter that declares none (a test
    double, or an adapter built before this existed)."""
    declared = getattr(provider, "credentials_mode", None)
    if declared == "test":
        return "test"
    if declared == "live":
        return "live"
    return "stub"


def in_test_mode(provider: object) -> bool:
    """Whether `provider` runs on test / sandbox credentials — what a checkout's `test_mode`
    reports. False for a stub (nothing is paid at all) and for live credentials."""
    return credentials_mode_of(provider) == "test"


# --------------------------------------------------------------------------- checkout

#: 10 digits, or `+` and 8-15 digits for an international number — what a phone must look like to
#: be passed to a provider that requires one (Cashfree's own rule, and a reasonable one for any).
_PHONE = re.compile(r"^(?:[0-9]{10}|\+[0-9]{8,15})$")


def valid_phone(phone: str | None) -> bool:
    return bool(phone) and _PHONE.fullmatch(phone or "") is not None


@dataclass(frozen=True, slots=True, repr=False)
class CheckoutCustomer:
    """Who is paying, as the browser's checkout needs it (Cashfree requires a customer id and a
    10-digit phone on every order). **Passed through to the provider and nowhere else**: never
    stored, never logged, never audited, never put in an event — `repr` is suppressed so a
    traceback cannot print it."""

    customer_id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None

    def __repr__(self) -> str:
        return "CheckoutCustomer(...)"


@dataclass(frozen=True, slots=True)
class CheckoutRequest:
    """One customer-present checkout to open: the Order's identity and money (from the platform's
    own charge, never a body), when it stops being payable, and who is paying."""

    receipt: str
    amount_minor: int
    currency: str
    notes: dict[str, str]
    expires_at: datetime
    customer: CheckoutCustomer | None = None
    #: Where the provider sends the browser back to (Cashfree). Never evidence of payment.
    return_url: str | None = None
    #: Where the provider delivers this Order's webhooks (Cashfree `order_meta.notify_url`, which
    #: must be HTTPS). None: whatever the account's dashboard registers.
    notify_url: str | None = None
    #: The Order id the charge **already records**, when it does (a reopened checkout). The adapter
    #: then fetches **that Order by its id** and returns it, or refuses (`ProviderTransient`,
    #: `recorded_order_not_found`) — it never searches by receipt (a receipt can name several
    #: Orders, and the search can miss one that exists) and never creates another. An adapter that
    #: cannot find an Order this charge already recorded is not looking at the account that made it
    #: (the stub after a rollback from a live wire, another environment's keys), and a fresh Order
    #: minted there under the same receipt would make that adapter the authority on a payment it
    #: cannot see.
    recorded_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    """An opened checkout: the Order and what the browser is handed to pay it. `client_action`
    is provider-shaped (`{"type": "razorpay_checkout", ...}` / `{"type": "cashfree_checkout",
    ...}`) and may hold a session token, so it is `repr=False` and never logged."""

    order: OrderResult
    client_action: dict[str, Any] = field(repr=False)


@runtime_checkable
class CheckoutProvider(Protocol):
    """A provider a customer can pay in its own hosted checkout, against an Order this platform
    creates (Phase A). The outcome comes back through `order_payments` or a signed webhook, never
    from the browser."""

    def ensure_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        """The Order the charge records (`request.recorded_order_id`, fetched by its id — never
        made again), or, when it records none, the Order found by `request.receipt` (best-effort)
        or else created; returned with what the browser needs."""
        ...


# --------------------------------------------------------------------------- direct debit


@runtime_checkable
class DirectDebitProvider(Protocol):
    """The shape of a provider whose debit is keyed by the merchant's own payment id on a saved
    mandate, with no Order step (`debit_shape = "direct"`; Cashfree subscriptions). **Declared in
    Phase A, implemented in Phase B**: no live adapter fulfils it yet, `DirectDebitContract`
    (`tests/direct_debit_contract.py`) is the test the first one must pass, and
    `services.call_provider_charge` already branches on it.

    The invariants are `ProviderContract`'s, reworded for a debit with no Order: a lookup that
    never creates, a debit created at most once per charge across any number of calls and
    processes, per-request timeouts that sum inside the call budget, a forged signature that is
    `False` and never raises, and no constant event id.
    """

    name: str
    request_timeouts_seconds: tuple[float, ...]

    def find_debit(self, *, charge_ref: str) -> ChargeResult | None:
        """The debit already made for `charge_ref` (the charge's own id), or None. Never creates."""
        ...

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
        """Debit the mandate for `charge_ref`. Only ever called after `find_debit` found none."""
        ...

    def verify_webhook(self, *, signature: str | None, body: bytes) -> bool: ...

    def parse_webhook(self, *, body: bytes) -> WebhookEvent: ...


# --------------------------------------------------------------------------- reading attempts

#: Which attempt answers "what happened to this Order": money moved beats money held beats an
#: attempt in progress beats an attempt that failed. Most recent first within a rank. The
#: vocabulary is the neutral one every adapter maps its own statuses into.
_PAYMENT_RANK = {"captured": 0, "refunded": 0, "authorized": 1, "created": 2, "failed": 3}


def best_attempt(payments: list[PaymentSnapshot]) -> PaymentSnapshot | None:
    """The attempt that says what happened to an Order: captured (or captured-then-refunded)
    first, then authorized, then one still in progress, then the most recent failure."""
    ranked = [p for p in payments if p.status in _PAYMENT_RANK]
    if not ranked:
        return None
    return min(ranked, key=lambda p: (_PAYMENT_RANK[p.status], -(p.created_at or 0)))
