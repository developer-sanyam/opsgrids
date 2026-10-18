"""Request and response contracts for payment.

This module is the OpenAPI surface: a change here is a change to the published contract. Two
internal surfaces meet payment — the service-to-service charge create (billing calls it after
finalize) and the provider webhooks (signature-guarded, private). The one **customer surface** is
the platform checkout (ADR-0032): a person who may activate products opens, reads and confirms the
checkout for one invoice. A person still never creates a charge directly: the amount is always the
invoice's own charge's, never a request field.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

#: Non-secret connector settings (`MerchantAccount.config`): scalars only, small and bounded.
ConfigValue = str | int | bool
_CONFIG_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_CONFIG_KEYS = 10
MAX_CONFIG_VALUE_CHARS = 200


class ServiceInfo(BaseModel):
    service: str
    schema_name: str
    #: The provider new platform charges and checkouts go to (`PAYMENT_PROVIDER`).
    provider: str
    #: What that provider runs on: `stub` (no provider is called), `test` (test / sandbox
    #: credentials: test cards pay, no real money moves) or `live`.
    mode: Literal["stub", "test", "live"]
    #: Whether this deployment lets test credentials run although it says `production`
    #: (`PAYMENT_ALLOW_TEST_KEYS`, a staging box only).
    test_keys_allowed: bool


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
    #: configured default for new charges). Additive (ADR-0032): a mandate at another provider does
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
    """`PUT /payment/collections/merchant-account` — connect the organization's payment-provider
    account, or rotate its credentials in place (same provider and mode only: switching is
    disconnect, then connect). Needs a session that signed in with a one-time code.

    * **Razorpay**: `key_id` (`rzp_test_…` / `rzp_live_…`, public — Checkout is opened with it; the
      mode is read from it, so `mode` is not sent), `key_secret`, `webhook_secret` (the one set on
      the webhook in Razorpay's dashboard).
    * **Cashfree**: `key_id` is the App ID, `key_secret` the Secret Key (it also signs webhooks, so
      there is no `webhook_secret`), and `mode` is `sandbox` or `production` — an App ID does not
      say which.
    * **PhonePe** (Standard Checkout V2): `key_id` is the Client ID, `key_secret` the Client
      Secret, `webhook_secret` the webhook **password** set in PhonePe's dashboard, `mode`
      `sandbox` or `production`, and `config` exactly `{"client_version": <number>,
      "webhook_username": "<the webhook username>"}`.
    * **PayU** (Hosted Checkout): `key_id` is the merchant key, `key_secret` the salt (it also
      verifies webhooks, so there is no `webhook_secret`), and `mode` `sandbox` or `production`.

    Secrets are sealed on arrival and never returned — only a masked hint of each."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["razorpay", "cashfree", "phonepe", "payu"] = "razorpay"
    key_id: str = Field(min_length=1, max_length=64)
    key_secret: SecretStr = Field(min_length=8, max_length=128)
    webhook_secret: SecretStr | None = Field(default=None, min_length=8, max_length=128)
    mode: Literal["sandbox", "production"] | None = None
    #: Non-secret provider settings (a merchant id, a store id). **Never put a credential here**: it
    #: is returned by `GET` and written to the audit row. Replaces the stored config when sent.
    config: dict[str, ConfigValue] | None = None

    @field_validator("config")
    @classmethod
    def _bounded_config(cls, value: dict[str, ConfigValue] | None) -> dict[str, ConfigValue] | None:
        if value is None:
            return value
        if len(value) > MAX_CONFIG_KEYS:
            raise ValueError(f"config holds at most {MAX_CONFIG_KEYS} settings")
        for key, item in value.items():
            if _CONFIG_KEY.fullmatch(key) is None:
                raise ValueError("config keys are lower-case letters, digits and underscores")
            if isinstance(item, str) and (
                len(item) > MAX_CONFIG_VALUE_CHARS or not item.isprintable()
            ):
                raise ValueError("config values are short printable text")
        return value


class CollectionWiresOut(BaseModel):
    """Per provider, whether a merchant's adapter talks to the provider for real (`live`) or runs
    on the in-process stub (`stub`: a checkout cannot be paid for real)."""

    razorpay: Literal["stub", "live"]
    cashfree: Literal["stub", "live"]
    phonepe: Literal["stub", "live"] = "stub"
    payu: Literal["stub", "live"] = "stub"


class MerchantAccountOut(BaseModel):
    """The organization's payment-provider connection as an admin may see it: masked secrets
    only, the webhook URL to register at the provider (`?account=` names this account), whether
    the credentials have been verified, and when a webhook last arrived."""

    configured: bool
    id: uuid.UUID | None = None
    provider: str | None = None
    key_id: str | None = None
    #: `test` / `live` — for Cashfree, Sandbox / Production.
    mode: Literal["test", "live"] | None = None
    key_secret_masked: str | None = None
    #: Razorpay only; None for a Cashfree account, which has no webhook secret of its own.
    webhook_secret_masked: str | None = None
    #: The neutral webhook path (`/api/v1/webhooks/collections?account=<id>`).
    webhook_path: str | None = None
    #: The absolute webhook URL, from the platform's public base (`APP_BASE_URL`).
    webhook_url: str | None = None
    webhook_events: list[str] = Field(default_factory=lambda: list(COLLECTION_WEBHOOK_EVENTS))
    rotated_at: datetime | None = None
    created_at: datetime | None = None
    #: `unverified` (never checked, or changed since), `verified` (the provider accepted these
    #: credentials at `verified_at`) or `failed` (it refused them: `last_error_code`).
    connection_state: Literal["unverified", "verified", "failed"] | None = None
    verified_at: datetime | None = None
    last_error_code: str | None = None
    #: When the platform last received a webhook from this account's provider (since connecting).
    last_webhook_at: datetime | None = None
    #: The non-secret provider settings stored with the account (empty when none).
    config: dict[str, ConfigValue] = Field(default_factory=dict)
    #: Whether this deployment accepts live keys at all (`COLLECTIONS_ALLOW_LIVE_KEYS`): Razorpay
    #: `rzp_live_…`, Cashfree Production.
    live_keys_allowed: bool
    #: `stub` when no Razorpay call leaves this platform. Kept as it always was for existing
    #: clients; `wires` says it per provider.
    wire: Literal["stub", "razorpay"]
    wires: CollectionWiresOut


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


class RazorpayCheckoutSessionOut(BaseModel):
    """Everything the browser needs to open Razorpay's own hosted Checkout — and nothing a browser
    must not have: the **public** key id and the provider Order id. The amount is the Order's
    (fixed server-side from the order's own total); Checkout charges exactly it."""

    provider: Literal["razorpay"]
    key_id: str
    provider_order_id: str
    amount_minor: int
    currency: str
    mode: Literal["test", "live"]
    #: False on the stub wire: the Order id is a local stand-in no real Checkout would accept.
    openable: bool


class CashfreeCheckoutSessionOut(BaseModel):
    """What the browser's `Cashfree({mode}).checkout({paymentSessionId})` needs. The session id is
    the provider's, fetched for this response and **never stored**: reopening fetches it again.
    No credential, and no customer detail (the phone went to Cashfree server to server)."""

    provider: Literal["cashfree"]
    payment_session_id: str = Field(repr=False)
    #: The SDK's own word for the environment.
    mode: Literal["sandbox", "production"]
    provider_order_id: str
    amount_minor: int
    currency: str
    #: False on the stub: the session is a local stand-in no real checkout would accept.
    openable: bool


class RedirectCheckoutSessionOut(BaseModel):
    """Where the browser goes to pay with a redirect-checkout provider (PhonePe, PayU): a `GET` of
    `url`, or a form `POST` of `fields` to it, in a new tab. The URL (or the fields) carries a
    short-lived token, fetched for this response and **never stored**: reopening asks again. No
    credential. The provider then sends the browser to a static "you can close this tab" page;
    what happened is learned from the provider, never from that return."""

    provider: Literal["phonepe", "payu"]
    method: Literal["GET", "POST"]
    url: str = Field(repr=False)
    fields: dict[str, str] = Field(default_factory=dict, repr=False)
    provider_order_id: str
    amount_minor: int
    currency: str
    #: False on the stub: the URL is a local stand-in no real checkout would accept.
    openable: bool


#: The browser's handle, by provider.
CheckoutSessionOut = Annotated[
    RazorpayCheckoutSessionOut | CashfreeCheckoutSessionOut | RedirectCheckoutSessionOut,
    Field(discriminator="provider"),
]


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
    #: The connected account's provider, and whether its wire is live — None when not connected.
    provider: str | None = None
    provider_wire: Literal["stub", "live"] | None = None
    collections: list[CollectionOut]


class PayerIn(BaseModel):
    """Internal: who is paying, from the order — sent by operations **only** when payment asks
    for it (a provider that requires a customer on every Order, Cashfree). Passed through to the
    provider and nowhere else: never stored, logged, audited or put in an event."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=160, repr=False)
    email: str | None = Field(default=None, max_length=254, repr=False)
    phone: str | None = Field(default=None, max_length=32, repr=False)


class CheckoutIn(BaseModel):
    """Internal (operations → payment): open or reuse the checkout for one order. The amount is
    the order's own stored total, sent by the service that owns the order — never a browser.
    `payer` is the order's customer, for a provider that needs one (a request without it is then
    answered 422 `payer-required`, and operations sends it again with one)."""

    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    payer: PayerIn | None = Field(default=None, repr=False)


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
# (ADR-0032). A customer paying one of OpsGrids' own invoices in the provider's hosted checkout.
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
    reopening fetches it again. `test_mode` is true when the provider that owns the charge runs on
    test / sandbox credentials (a Razorpay `rzp_test_` key on a live wire, or Cashfree's sandbox):
    the checkout takes the provider's test cards and no real money moves. False on live
    credentials and on a stub."""

    charge_id: uuid.UUID
    provider: str
    client_action: dict[str, Any]
    amount_minor: int
    currency: str
    expires_at: datetime
    test_mode: bool


class PlatformCheckoutStatusOut(BaseModel):
    """The state of one checkout, as the server knows it. `paid` is true only for a **captured**
    payment — one the provider reported, by a status check or a signed webhook; never because a
    browser said so. `provider_checked` is false when the provider could not be asked this time
    (the state shown is then the last one recorded). A failed checkout with `failure_reason`
    `checkout_expired` is one nobody paid before it expired. `test_mode` is as on
    `PlatformCheckoutOut`: the owning provider runs on test / sandbox credentials, so `paid` means
    paid with a test instrument."""

    charge_id: uuid.UUID
    provider: str
    state: Literal["created", "authorized", "captured", "failed"]
    paid: bool
    amount_minor: int
    currency: str
    expires_at: datetime | None
    failure_reason: str | None = None
    provider_checked: bool = True
    test_mode: bool


class CheckoutConfirmIn(BaseModel):
    """`POST /payment/checkouts/{charge_id}/confirm` (Razorpay) — the three fields Checkout's
    callback hands the browser. **Proof of nothing**: the server checks the signature as a
    precondition, then asks Razorpay what happened. Missing or blank fields are a 400."""

    model_config = ConfigDict(extra="forbid")

    razorpay_payment_id: str | None = Field(default=None, max_length=64)
    razorpay_order_id: str | None = Field(default=None, max_length=64)
    razorpay_signature: str | None = Field(default=None, max_length=128)
