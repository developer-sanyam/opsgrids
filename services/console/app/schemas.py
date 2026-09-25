"""Request and response contracts for console — the operator read API's OpenAPI surface.

A change here is a change to the published contract the operator console frontend generates its
client from. Every field is an operator-facing view of a projection; nothing here is a customer
surface (those are the product gateways').
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


class OrgStateOut(BaseModel):
    """One organization's commercial snapshot."""

    model_config = {"from_attributes": True}

    organization_id: uuid.UUID
    #: Mirrored from account-service's `core.org.{created,updated}` (`app/consumers.py`) or the
    #: one-off backfill (`app/backfill.py`) — `null` for a row neither has reached yet. Not the
    #: authority: `GET /admin/organizations/{id}`'s directory composite still calls account-service
    #: directly for that. This is what lets the *bulk* lists show a name without an extra
    #: cross-tenant call per row.
    name: str | None = None
    slug: str | None = None
    #: Same mirror, same nullability — the accounts list's Region filter and column.
    country: str | None = None
    #: The organization's creation time (account's `Organization.created_at`), mirrored the same
    #: way — the accounts list's Created filter and column. Distinct from `updated_at` below,
    #: which is this projection row's own last-write time.
    created_at: datetime | None = None
    #: The same bucket `GET /admin/metrics/overview`'s `organizations_by_lifecycle` counts,
    #: computed server-side (`selectors.lifecycle_of`) so the accounts list's Status filter and
    #: pill never reimplement the rule client-side: `suspended` (any subscription locked) beats
    #: `active` (any subscription active) beats `trial` beats `churned` (history but nothing
    #: live) beats `none` (the fold has never touched this organization).
    lifecycle: Literal["suspended", "active", "trial", "churned", "none"]
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


class SeriesPointOut(BaseModel):
    """One point on a monthly series. `value` is `None` for a month before this series has any
    visibility at all — never `0`, which would claim a real, counted "nothing happened" that the
    data cannot actually back (`app/domain.py::commercial_series`)."""

    period: str
    value: int | None


class MetricsSeriesOut(BaseModel):
    """`GET /admin/metrics/series` — monthly account growth and commercial history, replayed
    from console's own append-only `revenue_events` (ADR-0004 §4) plus the identity fold's
    `org_created_at`. Feeds the account-growth chart and every "vs last month" delta; each series
    carries the same ordered list of periods, oldest first.
    """

    currency: str | None
    mrr_minor: list[SeriesPointOut]
    new_organizations: list[SeriesPointOut]
    trials_started: list[SeriesPointOut]
    trial_conversions: list[SeriesPointOut]
    churned: list[SeriesPointOut]


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
    #: Same mirror as `OrgStateOut.name`/`.slug` — `null` until the identity fold or the backfill
    #: has reached this organization.
    name: str | None = None
    slug: str | None = None
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
    #: The `cursor` to request for the next page, or `None` on the last one (or when the
    #: caller never asked for paging at all — every existing consumer of this envelope keeps
    #: working unchanged, it simply now reads a `limit`-sized page instead of the whole table).
    next_cursor: str | None = None


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
    #: The `cursor` to request for the next page, or `None` once there isn't one — the default
    #: page (200, unfiltered) is exactly what this route always returned, so an existing caller
    #: that ignores this field sees no change.
    next_cursor: str | None = None


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


# --------------------------------------------------------------------- client pricing


class ProductSubscriptionOut(BaseModel):
    """The organization's live subscription for one product, read from subscription-service —
    console's own projection (`OrgCommercialState`) does not carry the subscription id or
    `current_period_end`, and a pricing screen needs both."""

    id: uuid.UUID
    state: str
    plan_version_id: uuid.UUID
    trial_ends_at: datetime | None = None
    current_period_end: datetime | None = None


class ProductPriceOut(BaseModel):
    """One priced plan version, as either the org's current pin or the product's public plan."""

    plan_version_id: uuid.UUID
    amount_minor: int
    currency: str
    billing_interval: str
    #: True for a version restricted to this one organization (ADR-0004 §2); always false for
    #: `public_price`, which by definition is nobody's bespoke version.
    is_bespoke: bool = False


class PriceHistoryEntryOut(BaseModel):
    """One past bespoke price this organization was set on this product, oldest details first —
    read from console's own audit trail (`catalog.bespoke_price.created`), never from catalog:
    an immutable plan version carries no reason and no actor, and console's record of the act is
    the only place both live."""

    plan_version_id: uuid.UUID
    amount_minor: int
    currency: str
    billing_interval: str
    created_at: datetime
    #: Null until console can resolve a staff id to an email (no such read exists yet — see
    #: `app/api/routes.py`'s pricing module docstring). Never the plaintext email either way:
    #: PII is masked by default (CLAUDE.md).
    created_by_email_masked: str | None = None
    reason: str | None = None


class ProductPricingOut(BaseModel):
    """One product's whole pricing picture for one organization — what the console pricing
    screen renders per row."""

    product_key: str
    product_name: str
    subscription: ProductSubscriptionOut | None = None
    current_price: ProductPriceOut | None = None
    public_price: ProductPriceOut | None = None
    history: list[PriceHistoryEntryOut] = Field(default_factory=list)


class OrganizationPricingOut(BaseModel):
    organization_id: uuid.UUID
    products: list[ProductPricingOut] = Field(default_factory=list)


_ISO_CURRENCY = re.compile(r"[A-Z]{3}")


class SetPricingIn(BaseModel):
    """Set one organization's price for one product (ADR-0004 §2): clone-and-publish a bespoke
    flat plan version, and by default re-pin the organization's subscription to it.

    `extra="forbid"` — a dangerous write accepts exactly this shape and nothing smuggled in
    beside it (CLAUDE.md).
    """

    model_config = ConfigDict(extra="forbid")

    #: Lowercase, starts with a letter — the same shape every product key on this platform
    #: already has (hrms, crm, storefront, ...). Fix for MAJOR-1 (security review, fix round 1):
    #: this value is interpolated into a URL path segment two hops downstream
    #: (`catalog_client.ensure_bespoke_price`, then catalog's own path param); an unconstrained
    #: string let `product_key="../../../../../admin/catalog/plans?"` retarget that request
    #: entirely, carrying console's own service token and the operator's forwarded bearer with
    #: it. The pattern is defence at the boundary; `catalog_client` also percent-encodes it
    #: before interpolating (defence in depth — neither alone is trusted).
    product_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    amount_minor: int = Field(gt=0, le=10_000_000_000)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    billing_interval: Literal["month", "year"] = "month"
    #: Why this price was set — required so a bespoke price is never silent about the deal it
    #: reflects. 1-500 characters, per the contract.
    reason: str = Field(min_length=1, max_length=500)
    apply_to_subscription: bool = True

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, value: str) -> str:
        upper = value.upper()
        if not _ISO_CURRENCY.fullmatch(upper):
            raise ValueError("currency must be a 3-letter ISO-4217 code")
        return upper


class SetPricingOut(BaseModel):
    plan_version_id: uuid.UUID
    applied_to_subscription_id: uuid.UUID | None = None
    #: `now` — re-pinned immediately (a trial, or a paid upgrade, which prorates now);
    #: `next_period` — a paid downgrade, scheduled for the subscription's existing next-period
    #: mechanism; `not_subscribed` — no subscription for this product, `apply_to_subscription`
    #: was false, or the subscription is in a state (`past_due`, `suspended`, `cancelled`) that
    #: subscription-service's own change-plan rule refuses to re-pin. See the pricing module
    #: docstring in `app/api/routes.py` for exactly which.
    effective: Literal["now", "next_period", "not_subscribed"]
