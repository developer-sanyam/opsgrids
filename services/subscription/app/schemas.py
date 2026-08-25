"""Request and response contracts for subscription.

This module is the OpenAPI surface: a change here is a change to the published contract, and the
frontend generates its client from it. `CatalogPlanVersion`/`CatalogPrice` are the shape of
catalog's internal read, parsed when a subscribe or change-plan resolves its pin.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- catalog internal read


class CatalogPrice(BaseModel):
    """One priced component of a catalog plan version, as its internal read returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    amount_minor: int | None = None
    currency: str
    unit: str | None = None
    unit_price_micros: int | None = None
    meter_key: str | None = None


class CatalogPlanVersion(BaseModel):
    """A catalog plan version as the pin lookup sees it — unfiltered, including the audience
    pointer, so this service can apply the published-and-visible rule itself."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    product_key: str | None = None
    version_no: int
    state: str
    billing_interval: str
    currency: str
    restricted_to_org_id: uuid.UUID | None = None
    prices: list[CatalogPrice] = Field(default_factory=list)


# --------------------------------------------------------------------- customer input


class SubscribeIn(BaseModel):
    """Start a subscription for a product, pinned to a catalog plan version.

    `trial_days` set starts the subscription in `trial` with an end date; left null it starts
    `active`. A trial is a *state of the subscription*, never a separate object (ADR-0004 §4).
    """

    product_key: str = Field(min_length=1, max_length=40)
    plan_version_id: uuid.UUID
    trial_days: int | None = Field(default=None, ge=1, le=365)


class ChangePlanIn(BaseModel):
    """Re-pin a subscription to a different published version. The old pin is preserved on the
    transition row — never overwritten in history (ADR-0004 §2)."""

    plan_version_id: uuid.UUID


class CancelIn(BaseModel):
    """Cancel a subscription. Immediate for the MVP; `reason` is recorded on the transition."""

    reason: str | None = Field(default=None, max_length=200)


class PaymentOutcome(BaseModel):
    """What a `commerce.payment.*` event tells this service about one collection attempt.

    Parsed from the event payload at the edge of the consumer rather than passed around as a raw
    dict, so a payload that changes shape fails at one named place. Only the fields dunning needs
    are here: enough to open a cycle and to ask for the same debit again later, and nothing about
    the provider — which processor took the money is payment's business alone (ADR-0004 §6).
    """

    invoice_id: uuid.UUID | None = None
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class SuspendIn(BaseModel):
    """Operator-driven suspension, delegated from the console over /internal (a later phase).

    `organization_id` names the owning tenant so the internal handler can bind the transaction to
    it; RLS then confirms the subscription really belongs there. `operator_id` is the accountable
    human the console passes through for the audit row.
    """

    organization_id: uuid.UUID
    operator_id: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=200)


# --------------------------------------------------------------------- output


class SubscriptionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    price_id: uuid.UUID
    quantity: int


class TransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_state: str | None = None
    to_state: str
    from_plan_version_id: uuid.UUID | None = None
    to_plan_version_id: uuid.UUID | None = None
    reason: str | None = None
    occurred_at: datetime


class DunningOut(BaseModel):
    """The live grace window, as the customer's own app should render it (ADR-0005).

    Present only while a cycle is open, which is exactly while the subscription is `past_due` or
    `suspended` for non-payment. It carries the two dates a banner has to be able to say out loud
    — when the next debit will be attempted, and when access stops — read from the stored
    schedule rather than recomputed, so the screen and the sweep can never disagree.
    """

    model_config = ConfigDict(from_attributes=True)

    cycle_id: uuid.UUID
    opened_at: datetime
    grace_ends_at: datetime
    next_attempt_at: datetime | None = None
    attempts_made: int
    max_attempts: int
    amount_minor: int | None = None
    currency: str | None = None


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_key: str
    plan_version_id: uuid.UUID
    state: str
    trial_ends_at: datetime | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at: datetime | None = None
    currency: str
    items: list[SubscriptionItemOut] = Field(default_factory=list)
    #: Null unless a dunning cycle is open against this subscription.
    dunning: DunningOut | None = None
