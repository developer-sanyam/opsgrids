"""Request and response contracts for catalog.

This module is the OpenAPI surface: a change here is a change to the published contract,
and the frontend generates its client from it.

Money is carried as integer minor units and a usage rate as integer micros — the wire
format matches the storage, so no float ever appears between the database and the client
(ADR-0000 §8).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- customer output


class PriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    amount_minor: int | None = None
    currency: str
    unit: str | None = None
    unit_price_micros: int | None = None
    meter_key: str | None = None


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feature_key: str
    value: Any = None


class PlanVersionOut(BaseModel):
    """A priced snapshot. `restricted_to_org_id` is echoed so an operator UI can see an
    audience-of-one at a glance; the customer read only ever returns versions the caller is
    allowed to see, so for them it is either NULL or their own organization."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_no: int
    state: str
    billing_interval: str
    currency: str
    restricted_to_org_id: uuid.UUID | None = None
    published_at: datetime | None = None
    prices: list[PriceOut] = Field(default_factory=list)
    features: list[FeatureOut] = Field(default_factory=list)


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_key: str
    code: str
    name: str
    state: str
    sort_order: int
    versions: list[PlanVersionOut] = Field(default_factory=list)


# --------------------------------------------------------------------- internal read (pin lookup)


class InternalPriceOut(BaseModel):
    """A price as the internal pin-lookup returns it — includes the id, which the customer read
    omits, because a subscription references the price by that id."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    amount_minor: int | None = None
    currency: str
    unit: str | None = None
    unit_price_micros: int | None = None
    meter_key: str | None = None


class InternalPlanVersionOut(BaseModel):
    """One plan version for a sibling service pinning it (subscription).

    Returned **unfiltered** — including `restricted_to_org_id` and whatever state it is in — so the
    caller applies the published-and-visible rule itself. The customer bespoke filter deliberately
    does not run here; this is a service-token read behind the internal API, not a customer browse.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    product_key: str | None = None
    version_no: int
    state: str
    billing_interval: str
    currency: str
    restricted_to_org_id: uuid.UUID | None = None
    prices: list[InternalPriceOut] = Field(default_factory=list)


# --------------------------------------------------------------------- operator input


class PriceIn(BaseModel):
    kind: str = Field(pattern="^(flat|per_seat|usage)$")
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str = Field(min_length=3, max_length=3)
    unit: str | None = Field(default=None, max_length=40)
    unit_price_micros: int | None = Field(default=None, ge=0)
    meter_key: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _coherent(self) -> PriceIn:
        """A price must actually carry a number of the right shape for its kind.

        A `flat`/`per_seat` price with no `amount_minor`, or a `usage` price with no
        `unit_price_micros`, is a priced component that prices nothing — refused here rather
        than discovered when an invoice comes out empty.
        """
        if self.kind in ("flat", "per_seat") and self.amount_minor is None:
            raise ValueError(f"a {self.kind} price needs amount_minor")
        if self.kind == "usage" and self.unit_price_micros is None:
            raise ValueError("a usage price needs unit_price_micros")
        return self


class FeatureIn(BaseModel):
    feature_key: str = Field(min_length=1, max_length=60)
    value: Any = None


class PlanCreateIn(BaseModel):
    product_key: str = Field(min_length=1, max_length=40)
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    sort_order: int = 100


class PlanPatchIn(BaseModel):
    """Editing a plan touches only its presentation. Re-pricing is a new version, never a
    field update — that is what keeps published versions reconstructable (ADR-0000 §9)."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    sort_order: int | None = None


class VersionCreateIn(BaseModel):
    """Clone the plan's active version into a new draft, with these prices and features.

    `restricted_to_org_id` set makes it a bespoke, audience-of-one version (ADR-0004 §2);
    left NULL it is a public version every customer of the product can see once published.
    """

    billing_interval: str = Field(default="month", pattern="^(month|year|one_time)$")
    currency: str = Field(min_length=3, max_length=3)
    restricted_to_org_id: uuid.UUID | None = None
    prices: list[PriceIn] = Field(default_factory=list)
    features: list[FeatureIn] = Field(default_factory=list)
