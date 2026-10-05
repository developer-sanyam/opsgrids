"""Request and response contracts for payment.

This module is the OpenAPI surface: a change here is a change to the published contract. Two
internal surfaces meet payment — the service-to-service charge create (billing calls it after
finalize) and the provider webhooks (signature-guarded, private). The one **customer surface** is
the platform checkout (ADR-0029): a person who may activate products opens, reads and confirms the
checkout for one invoice. A person still never creates a charge directly: the amount is always the
invoice's own charge's, never a request field.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- charge create (internal)


class ChargeIn(BaseModel):
    """A charge to raise, posted server-to-server (billing, after it finalizes an invoice).

    `organization_id` travels in the body, not a token: this is a service-token call with no
    tenant context, and the service layer binds the tenant GUC before writing. `idempotency_key`
    is **platform-supplied** (Razorpay has none, ADR-0004 §6) and unique per organization, so a
    retried billing run raises exactly one charge. `subscription_id` and `product_key` are what a
    later failure suspends — they ride out on `commerce.payment.failed`.
    """

    organization_id: uuid.UUID
    amount_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    idempotency_key: str = Field(max_length=200)
    invoice_id: uuid.UUID | None = None
    subscription_id: uuid.UUID | None = None
    product_key: str | None = Field(default=None, max_length=40)
    mandate_id: uuid.UUID | None = None


class ChargeOut(BaseModel):
    """A charge as stored. `provider_payment_id` is the id the webhook reconciles against."""

    id: uuid.UUID
    organization_id: uuid.UUID
    invoice_id: uuid.UUID | None
    subscription_id: uuid.UUID | None
    product_key: str | None
    provider: str
    provider_payment_id: str | None
    amount_minor: int
    currency: str
    state: str
    idempotency_key: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UsableMandateOut(BaseModel):
    """Whether an organization could be debited `amount_minor` in `currency` now, and under
    which mandate. Carries identifiers only — never card or bank details, which this platform
    does not hold (the provider does)."""

    organization_id: uuid.UUID
    currency: str
    amount_minor: int
    usable: bool
    mandate_id: uuid.UUID | None = None
    #: The provider the mandate is held at, and the provider the question was asked of (the
    #: configured default for new charges). Additive (ADR-0029): a mandate at another provider does
    #: not count, because a Razorpay token cannot be debited through Cashfree.
    provider: str | None = None


# --------------------------------------------------------------------- webhook (provider)


class WebhookAck(BaseModel):
    """The result of ingesting a provider webhook.

    `duplicate` is true when the `provider_event_id` was already in the inbox — the callback was a
    re-delivery and did nothing the first time did not already do. `reconciled` is true when the
    event moved a charge to a terminal state and emitted a `commerce.payment.*`.

    `provider_event_id` is the id the inbox deduplicated on: the delivery's `X-Razorpay-Event-Id`
    header, else an id the body carried, else `derived:<sha256>` from the body's event name, entity
    id and timestamp. It is never empty.

    `ignored_reason` is set when the webhook was authentic but cannot be reconciled and never will
    be — the provider is answered 200 (a retry could not change the outcome) and the reason is a
    stable code: `unknown_charge` and `unattributable` (nothing here to reconcile), or
    `illegal_transition`, `amount_mismatch`, `currency_mismatch` and `provider_id_conflict` (it
    contradicts what this platform holds; no state was changed, an operator should look). Every
    reason except `unattributable` — which has no tenant to record under — is kept on the inbox
    row and audited as `payment.webhook.ignored`. Null on a webhook that was reconciled or was
    simply a redelivery.
    """

    received: bool
    provider_event_id: str
    duplicate: bool
    reconciled: bool
    ignored_reason: str | None = None


# ===================================================================== tenant collections
# (ADR-0024 §8). A tenant's own customers paying the tenant. Every amount is integer minor units
# with an explicit ISO currency. **No card, bank or UPI detail appears in any model here** — the
# customer pays in the provider's own hosted Checkout, and the platform only ever holds the
# provider's identifiers.


#: The Razorpay events a merchant should enable on the webhook it registers.
COLLECTION_WEBHOOK_EVENTS = (
    "payment.authorized",
    "payment.captured",
    "payment.failed",
    "order.paid",
    "refund.created",
    "refund.processed",
    "refund.failed",
)


class MerchantAccountIn(BaseModel):
    """`PUT /payment/collections/merchant-account` — connect the organization's Razorpay account,
    or rotate its credentials in place. The key id is public (the browser gets it); the key secret
    and webhook secret are sealed on arrival and never returned — only a masked hint of each."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["razorpay"] = "razorpay"
    key_id: str = Field(min_length=1, max_length=64)
    key_secret: SecretStr = Field(min_length=8, max_length=128)
    webhook_secret: SecretStr = Field(min_length=8, max_length=128)


class MerchantAccountOut(BaseModel):
    """The organization's payment-provider connection as an admin may see it: masked secrets
    only, and the webhook URL path to register at the provider (`?account=` names this account)."""

    configured: bool
    id: uuid.UUID | None = None
    provider: str | None = None
    key_id: str | None = None
    mode: Literal["test", "live"] | None = None
    key_secret_masked: str | None = None
    webhook_secret_masked: str | None = None
    webhook_path: str | None = None
    webhook_events: list[str] = Field(default_factory=lambda: list(COLLECTION_WEBHOOK_EVENTS))
    rotated_at: datetime | None = None
    created_at: datetime | None = None
    #: Whether this deployment accepts `rzp_live_…` keys at all (`COLLECTIONS_ALLOW_LIVE_KEYS`).
    live_keys_allowed: bool
    #: `stub` when no provider call leaves this platform (checkouts cannot be paid for real).
    wire: Literal["stub", "razorpay"]


class CollectionRefundOut(BaseModel):
    id: uuid.UUID
    provider_refund_id: str | None
    amount_minor: int
    currency: str
    #: The refund's *current* state (`created` = pending at the provider, `processed`, `failed`).
    state: str
    reason: str | None
    created_at: datetime


class StatusHistoryOut(BaseModel):
    from_state: str | None
    to_state: str
    #: `api` | `webhook` | `sync` | `sweep` — what moved it.
    source: str
    reason: str | None
    at: datetime


class CollectionOut(BaseModel):
    """One online payment taken for one order: its state, its provider identifiers, its refunds
    and the history of how it got here. `pending` in the plain-language sense is `created` (or
    `authorized`, money held); `captured` is paid."""

    id: uuid.UUID
    reference_type: str
    reference_id: uuid.UUID
    state: Literal["created", "authorized", "captured", "failed", "cancelled", "expired"]
    amount_minor: int
    currency: str
    provider: str
    provider_order_id: str | None
    provider_payment_id: str | None
    failure_reason: str | None
    refunded_minor: int
    refunds: list[CollectionRefundOut] = Field(default_factory=list)
    history: list[StatusHistoryOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CheckoutSessionOut(BaseModel):
    """Everything the browser needs to open the provider's own hosted Checkout — and nothing a
    browser must not have: the **public** key id and the provider Order id. The amount is the
    Order's (fixed server-side from the order's own total); Checkout charges exactly it."""

    provider: str
    key_id: str
    provider_order_id: str
    amount_minor: int
    currency: str
    mode: Literal["test", "live"]
    #: False on the stub wire: the Order id is a local stand-in no real Checkout would accept.
    openable: bool


class CheckoutOut(BaseModel):
    collection: CollectionOut
    #: None while another request is still creating the provider Order — try again shortly.
    checkout: CheckoutSessionOut | None


class ReferenceCollectionsOut(BaseModel):
    """Every online payment taken for one order, newest first, and whether the organization can
    take one at all."""

    merchant_account_configured: bool
    mode: Literal["test", "live"] | None = None
    wire: Literal["stub", "razorpay"]
    collections: list[CollectionOut]


class CheckoutIn(BaseModel):
    """Internal (operations → payment): open or reuse the checkout for one order. The amount is
    the order's own stored total, sent by the service that owns the order — never a browser."""

    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class CollectionActorIn(BaseModel):
    """Internal: who, at operations, asked for this (for payment's own audit row)."""

    model_config = ConfigDict(extra="forbid")

    actor_id: uuid.UUID | None = None


class CollectionRefundIn(BaseModel):
    """Internal: refund part or all of a captured collection. `idempotency_key` is the caller's
    own (the operator's request key): the same key is the same refund here and at the provider."""

    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[\x21-\x7e]+$")
    actor_id: uuid.UUID | None = None


class ReconciliationRowOut(BaseModel):
    charge_id: uuid.UUID
    reference_type: str | None
    reference_id: uuid.UUID | None
    state: str
    amount_minor: int
    currency: str
    provider_order_id: str | None
    provider_payment_id: str | None
    refunded_minor: int
    #: What the provider says about the Order's deciding attempt, or None if it holds none.
    provider_status: str | None
    #: Stable codes (`captured_at_provider_only`, `not_captured_at_provider`,
    #: `payment_id_mismatch`, `amount_mismatch`, `currency_mismatch`, `refund_mismatch`).
    mismatches: list[str]
    #: The provider could not be asked: unknown — neither a match nor a mismatch.
    lookup_failed: bool


class ReconciliationOut(BaseModel):
    """A read-only comparison of this platform's collection records with the provider's. It
    corrects nothing: a mismatch is for a person to look at (a status check, or the provider's
    own dashboard, is what settles it)."""

    generated_at: datetime
    checked: int
    mismatched: int
    unchecked: int
    rows: list[ReconciliationRowOut]


# ===================================================================== platform checkout
# (ADR-0029). A customer paying one of OpsGrids' own invoices in the provider's hosted checkout.
# The amount, the currency and the subscription are never in a request: they are read from the
# invoice's own charge.


class CheckoutCustomerIn(BaseModel):
    """Who is paying, as the provider's checkout needs it (Cashfree requires a phone on every
    Order). **Passed through to the provider and nowhere else** — never stored, logged, audited or
    put in an event. A phone is 10 digits, or `+` and 8-15 digits."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=16)


class PlatformCheckoutIn(BaseModel):
    """`POST /payment/checkouts` — open (or reopen) the checkout for one invoice."""

    model_config = ConfigDict(extra="forbid")

    invoice_id: uuid.UUID
    customer: CheckoutCustomerIn | None = None


class PlatformCheckoutOut(BaseModel):
    """What the browser needs to open the provider's checkout, and what it will charge.
    `amount_minor` and `currency` are the invoice's own charge's. `client_action` is provider-shaped
    and tagged by `type`: `{"type": "razorpay_checkout", "key_id", "order_id", "openable"}` (the
    **public** key id and the Order id — never the secret) or `{"type": "cashfree_checkout",
    "payment_session_id", "mode", "openable"}`. `openable` is False on a stub provider: the Order
    is a local stand-in no real checkout would accept. The session token is never stored;
    reopening fetches it again."""

    charge_id: uuid.UUID
    provider: str
    client_action: dict[str, Any]
    amount_minor: int
    currency: str
    expires_at: datetime


class PlatformCheckoutStatusOut(BaseModel):
    """The state of one checkout, as the server knows it. `paid` is true only for a **captured**
    payment — one the provider reported, by a status check or a signed webhook; never because a
    browser said so. `provider_checked` is false when the provider could not be asked this time
    (the state shown is then the last one recorded). A failed checkout with `failure_reason`
    `checkout_expired` is one nobody paid before it expired."""

    charge_id: uuid.UUID
    provider: str
    state: Literal["created", "authorized", "captured", "failed"]
    paid: bool
    amount_minor: int
    currency: str
    expires_at: datetime | None
    failure_reason: str | None = None
    provider_checked: bool = True


class CheckoutConfirmIn(BaseModel):
    """`POST /payment/checkouts/{charge_id}/confirm` (Razorpay) — the three fields Checkout's
    callback hands the browser. **Proof of nothing**: the server checks the signature as a
    precondition, then asks Razorpay what happened. Missing or blank fields are a 400."""

    model_config = ConfigDict(extra="forbid")

    razorpay_payment_id: str | None = Field(default=None, max_length=64)
    razorpay_order_id: str | None = Field(default=None, max_length=64)
    razorpay_signature: str | None = Field(default=None, max_length=128)
