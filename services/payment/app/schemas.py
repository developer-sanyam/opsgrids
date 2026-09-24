"""Request and response contracts for payment.

This module is the OpenAPI surface: a change here is a change to the published contract. Two
internal surfaces meet payment — the service-to-service charge create (billing calls it after
finalize) and the provider webhook (Razorpay calls it, signature-guarded). There is deliberately
**no customer surface**: a person never creates a charge directly, so no `/api/v1/payment/*`
write exists.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


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
