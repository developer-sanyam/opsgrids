"""Request and response contracts for console — the operator read API's OpenAPI surface.

A change here is a change to the published contract the operator console frontend generates its
client from. Every field is an operator-facing view of a projection; nothing here is a customer
surface (those are the product gateways').
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


class OrgStateOut(BaseModel):
    """One organization's commercial snapshot."""

    model_config = {"from_attributes": True}

    organization_id: uuid.UUID
    mrr_minor: int
    currency: str | None
    active_products: list[str]
    subscription_states: dict[str, Any]
    trial_ends_at: datetime | None
    locked: bool
    updated_at: datetime


class MetricsOut(BaseModel):
    """Platform-wide commercial metrics, computed from the transition log (ADR-0004 §4)."""

    mrr_minor: int
    currency: str | None
    trials_started: int
    trial_conversions: int
    trial_conversion_rate: float
    active_subscriptions: int
    churned: int
    churn_rate: float


class OrganizationsOut(BaseModel):
    """The operator organizations view: every org's state, plus the platform metrics."""

    organizations: list[OrgStateOut]
    metrics: MetricsOut


class LifecycleCountsOut(BaseModel):
    """Organization counts by lifecycle bucket (`selectors.lifecycle_counts`)."""

    suspended: int
    active: int
    trial: int
    churned: int
    none: int


class MetricsOverviewOut(MetricsOut):
    """`GET /admin/metrics/overview` — the platform metrics plus org counts by lifecycle."""

    organization_count: int
    organizations_by_lifecycle: LifecycleCountsOut


class OrgDetailOut(BaseModel):
    """One organization's detail: console's own commercial projection composed with the
    directory identity from account-service's staff-only route — never account's internal
    router, which authorizes no individual caller (see `app/directory_client.py`).

    The directory fields are `None` only when account-service could not be reached in time
    for a field that is not itself the presence check; a genuinely unknown organization is a
    404, not a row of nulls.
    """

    organization_id: uuid.UUID
    slug: str
    name: str
    status: str
    country: str | None
    created_at: datetime
    commercial: OrgStateOut | None


class SubscriptionOut(BaseModel):
    """One organization's subscription to one product, flattened from its snapshot."""

    organization_id: uuid.UUID
    product_key: str
    state: str
    plan_version_id: str | None
    amount_minor: int
    currency: str | None
    trial_ends_at: datetime | None


class SubscriptionsOut(BaseModel):
    subscriptions: list[SubscriptionOut]


class UsageOut(BaseModel):
    model_config = {"from_attributes": True}

    organization_id: uuid.UUID
    meter_key: str
    period: str
    quantity: int


class UsageListOut(BaseModel):
    usage: list[UsageOut]


class AuditOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    actor_id: uuid.UUID | None
    acting_as: str | None
    action: str
    entity: str
    entity_id: uuid.UUID | None
    reason: str | None
    at: datetime


class AuditListOut(BaseModel):
    audit: list[AuditOut]


class OverrideIn(BaseModel):
    """An operator override. Delegated to the owning service — console writes no foreign schema."""

    #: `suspend` → subscription-service; `comp_grant` → billing-service.
    action: Literal["suspend", "comp_grant"]
    organization_id: uuid.UUID
    #: Required for `suspend`: the subscription to suspend in subscription-service.
    subscription_id: uuid.UUID | None = None
    #: Required for `comp_grant`: the credit to grant in billing-service.
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str | None = None
    reason: str | None = None


class OverrideOut(BaseModel):
    action: str
    organization_id: uuid.UUID
    delegated_to: str
    result: dict[str, Any]
