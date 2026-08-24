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
