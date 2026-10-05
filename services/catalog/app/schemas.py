"""Request and response contracts for catalog.

This module is the OpenAPI surface: a change here is a change to the published contract,
and the frontend generates its client from it.

Money is carried as integer minor units and a usage rate as integer micros — the wire
format matches the storage, so no float ever appears between the database and the client
(ADR-0000 §8).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AliasChoices,
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain import price_sort_key


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- operator/internal output
#
# PriceOut / PlanVersionOut / PlanOut carry the actual amounts. They are for the admin API
# (`api/admin.py`) and the console-internal API (`api/console_internal.py`) — staff and
# sibling services, never a tenant caller. The tenant read (`api/routes.py`) uses the
# Tenant* schemas below instead, because pricing is negotiated per organization by staff and
# a client must never receive a list price (docs/commerce-packaging.md).


class PriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    #: The row id, so a seat class's `price_id` can be joined to the price it is charged at.
    id: uuid.UUID
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


class SeatClassOut(BaseModel):
    """A class of seat this version sells. `price_id` NULL is a free class.

    `can_write` is on the wire because it is the *entitlement* half of the seat class, not a
    presentational one: a client rendering a seat picker has to be able to say "view-only" out
    loud, and the grant computed server-side for such a seat contains no write permission
    (`domain.seat_class_grants`).
    """

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    price_id: uuid.UUID | None = None
    included_count: int | None = None
    can_write: bool
    sort_order: int
    description: str | None = None


class PlanVersionOut(BaseModel):
    """A priced snapshot. `restricted_to_org_id` is echoed so an operator UI can see an
    audience-of-one at a glance; the customer read only ever returns versions the caller is
    allowed to see, so for them it is either NULL or their own organization."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: The plan this version belongs to, and its name, product and state — so a screen holding
    #: only a version (a picker, a pinned subscription) can say what it is without a second read.
    #: Read off the eagerly loaded `PlanVersion.plan` (`selectors._with_children`).
    plan_id: uuid.UUID
    plan_name: str | None = Field(
        default=None, validation_alias=AliasChoices("plan_name", AliasPath("plan", "name"))
    )
    product_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("product_key", AliasPath("plan", "product_key")),
    )
    plan_state: str | None = Field(
        default=None, validation_alias=AliasChoices("plan_state", AliasPath("plan", "state"))
    )
    version_no: int
    state: str
    billing_interval: str
    currency: str
    restricted_to_org_id: uuid.UUID | None = None
    published_at: datetime | None = None
    #: Deterministic: flat, then per-seat, then usage, ties broken by id (`domain.price_sort_key`).
    prices: list[PriceOut] = Field(default_factory=list)
    #: Sorted by `feature_key`.
    features: list[FeatureOut] = Field(default_factory=list)
    seat_classes: list[SeatClassOut] = Field(default_factory=list)

    @field_validator("prices", mode="after")
    @classmethod
    def _sort_prices(cls, prices: list[PriceOut]) -> list[PriceOut]:
        return sorted(
            prices,
            key=lambda p: price_sort_key(
                p.kind, p.unit, p.meter_key, p.amount_minor, p.unit_price_micros, str(p.id)
            ),
        )

    @field_validator("features", mode="after")
    @classmethod
    def _sort_features(cls, features: list[FeatureOut]) -> list[FeatureOut]:
        return sorted(features, key=lambda f: f.feature_key)


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_key: str
    code: str
    name: str
    state: str
    sort_order: int
    versions: list[PlanVersionOut] = Field(default_factory=list)


# --------------------------------------------------------------------- tenant (customer) output


class TenantPriceOut(BaseModel):
    """A price component as a tenant caller may see it — its shape, never its amount.

    No `amount_minor`, `currency` or `unit_price_micros`: the owner's rule is that pricing is
    set per client by staff, so a client must not receive a list price. `kind` still tells the
    client a flat offer exists to start a trial against, without saying what it costs.
    """

    model_config = ConfigDict(from_attributes=True)

    kind: str
    unit: str | None = None
    meter_key: str | None = None


class TenantPlanVersionOut(BaseModel):
    """A priced-snapshot's packaging, as a tenant may see it — no amount, no currency.

    `restricted_to_org_id` is still echoed: the customer read only ever returns versions the
    caller is allowed to see, so for them it is either NULL or their own organization, and that
    is not pricing information.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_no: int
    state: str
    billing_interval: str
    restricted_to_org_id: uuid.UUID | None = None
    published_at: datetime | None = None
    prices: list[TenantPriceOut] = Field(default_factory=list)
    features: list[FeatureOut] = Field(default_factory=list)
    seat_classes: list[SeatClassOut] = Field(default_factory=list)


class TenantPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_key: str
    code: str
    name: str
    state: str
    sort_order: int
    versions: list[TenantPlanVersionOut] = Field(default_factory=list)


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
    #: The plan's display name (`Plan.name`, "Growth") — what a staff screen shows beside a
    #: subscription instead of a bare version uuid. Additive: a caller written before it existed
    #: simply never reads it.
    plan_name: str | None = None
    version_no: int
    state: str
    billing_interval: str
    currency: str
    restricted_to_org_id: uuid.UUID | None = None
    prices: list[InternalPriceOut] = Field(default_factory=list)
    #: The version's feature sheet, so a caller can read the packaging declarations
    #: (`bundled_products`, `bundle_tier`) without a second round trip. Unfiltered, like the
    #: rest of this response.
    features: list[FeatureOut] = Field(default_factory=list)
    #: The seat classes this version sells. A subscription that wants per-class seat counts
    #: needs them at pin time, and a pin is the only moment they are guaranteed to be read.
    seat_classes: list[SeatClassOut] = Field(default_factory=list)


class InternalPlanVersionNameOut(BaseModel):
    """One plan version's display names — `GET /internal/v1/plan-versions` (batch)."""

    plan_version_id: uuid.UUID
    plan_id: uuid.UUID
    plan_name: str
    product_key: str


class InternalPlanVersionNamesOut(BaseModel):
    """The names for whichever of the requested ids exist. An id catalog does not know is simply
    absent — the batch's answer to "no such version", not a 404 for the whole call."""

    versions: list[InternalPlanVersionNameOut]


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


class SeatClassIn(BaseModel):
    """A class of seat to author on the new version.

    `price_index` points at a position in the **same payload's** `prices` list rather than at a
    price id, because the prices do not exist yet when the version is drafted. Left NULL the
    class is free — which is the shape of the view-only seat the market now expects, and it is
    free by having no price at all rather than a zero-amount one.
    """

    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    price_index: int | None = Field(
        default=None, ge=0, description="Index into this payload's `prices`; NULL means free"
    )
    included_count: int | None = Field(default=None, ge=0)
    can_write: bool = True
    sort_order: int = 100
    description: str | None = Field(default=None, max_length=300)


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


class BespokePriceIn(BaseModel):
    """Console's request to clone-and-publish an audience-of-one flat price for one organization.

    Deliberately narrower than `VersionCreateIn`: this is the one shape console's
    `/admin/internal/catalog` endpoint accepts, so the operator console cannot author anything
    beyond "this org, this product, this flat price" through it — a bundle, a per-seat plan or an
    arbitrary feature sheet still goes through the full authoring API on `/admin/catalog`.
    `extra="forbid"` so a caller cannot smuggle a field this endpoint does not account for.
    """

    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(gt=0, le=10_000_000_000)
    currency: str = Field(min_length=3, max_length=3)
    billing_interval: str = Field(pattern="^(month|year)$")

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, value: str) -> str:
        upper = value.upper()
        if not re.fullmatch(r"[A-Z]{3}", upper):
            raise ValueError("currency must be a 3-letter ISO-4217 code")
        return upper


class PublicFlatPlanOut(BaseModel):
    """One product's public flat plan, as the console pricing screen reads it: what a product
    costs before any client-specific negotiation."""

    product_key: str
    product_name: str
    plan_id: uuid.UUID
    plan_version_id: uuid.UUID
    amount_minor: int
    currency: str
    billing_interval: str


class PublicFlatPlansOut(BaseModel):
    plans: list[PublicFlatPlanOut] = Field(default_factory=list)


class VersionCreateIn(BaseModel):
    """Clone the plan's active version into a new draft, with these prices and features.

    `restricted_to_org_id` set makes it a bespoke, audience-of-one version (ADR-0004 §2);
    left NULL it is a public version every customer of the product can see once published. The
    same field, pointed at a reseller's organization, is how a partner wholesale price is
    authored — one mechanism, two commercial uses (docs/commerce-packaging.md).

    A **bundle** is authored here too, with no special field: its `features` declare
    `bundled_products` and `bundle_tier`, and its `prices` are the bundled rate. A bundle is a
    plan version, not a discount layered over one, which is what keeps a bundled invoice
    reconstructable from the version it pinned.
    """

    billing_interval: str = Field(default="month", pattern="^(month|year|one_time)$")
    currency: str = Field(min_length=3, max_length=3)
    restricted_to_org_id: uuid.UUID | None = None
    prices: list[PriceIn] = Field(default_factory=list)
    features: list[FeatureIn] = Field(default_factory=list)
    seat_classes: list[SeatClassIn] = Field(default_factory=list)


# --------------------------------------------------------------------- operator Plans & prices page
#
# The `/admin/catalog` reads (owner and billing_ops) and the reason-carrying write bodies (owner
# only, `Idempotency-Key` required). Spec: docs/superpowers/specs/2026-10-06-admin-plans-page-
# design.md. The authoring inputs above stay as they are for the service-level callers (console's
# bespoke-price endpoint builds a `VersionCreateIn` itself); the HTTP route takes these.

#: Why an operator made this change. Stored on the audit row (`AuditLog.reason`), never in an event
#: payload: it may name a customer negotiation. Trimmed, 3 to 500 characters.
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]

#: Every product key on this platform is this shape (`hrms`, `crm`, `storefront`, …) — the same
#: pattern `console_internal` enforces on its path parameter.
PRODUCT_KEY_PATTERN = r"^[a-z][a-z0-9_-]{0,39}$"

Audience = Literal["public", "restricted", "all"]


class ReasonIn(BaseModel):
    """The body of a write that carries nothing but its justification (publish's flag aside)."""

    model_config = ConfigDict(extra="forbid")

    reason: Reason


class PublishIn(ReasonIn):
    """Publishing is irreversible. `allow_zero_amounts` is the explicit acknowledgement a zero
    flat, per-seat or usage amount needs — without it a zero is refused as a likely typo."""

    allow_zero_amounts: bool = False


class AdminPlanCreateIn(PlanCreateIn):
    """`POST /admin/catalog/plans`. The product key must be a registered, non-platform product."""

    model_config = ConfigDict(extra="forbid")

    product_key: str = Field(min_length=1, max_length=40, pattern=PRODUCT_KEY_PATTERN)
    code: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]{0,59}$")
    reason: Reason


class AdminPlanPatchIn(PlanPatchIn):
    """`PATCH /admin/catalog/plans/{id}` — presentation only, with a reason."""

    model_config = ConfigDict(extra="forbid")

    reason: Reason


class AdminVersionCreateIn(VersionCreateIn):
    """`POST /admin/catalog/plans/{id}/versions`. The console always sends the **whole** sheet —
    prices, features and seat classes — prefilled from the latest version; an empty sheet still
    clones the latest version of the same audience, as `VersionCreateIn` always has.
    `extra="forbid"` so a misspelt key (`feature` for `features`) is refused, not dropped."""

    model_config = ConfigDict(extra="forbid")

    reason: Reason


class VersionCountsOut(BaseModel):
    """How many versions a plan has, by state, for its **public** audience — and how many
    audience-of-one (bespoke or partner) versions sit beside them, in any state."""

    draft: int = 0
    published: int = 0
    grandfathered: int = 0
    bespoke: int = 0


class VersionSummaryOut(BaseModel):
    """One version without its sheet — the plan detail lists these and the screen fetches a
    version's prices, features and seat classes only when it is opened (`GET /versions/{id}`)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    version_no: int
    state: str
    billing_interval: str
    currency: str
    restricted_to_org_id: uuid.UUID | None = None
    published_at: datetime | None = None
    created_at: datetime
    price_count: int = 0
    feature_count: int = 0


class PlanListItemOut(BaseModel):
    """One row of the Plans & prices table."""

    id: uuid.UUID
    product_key: str
    code: str
    name: str
    state: str
    sort_order: int
    created_at: datetime
    counts: VersionCountsOut
    #: The newest published public version, with its sheet — what the plan sells today. NULL
    #: when nothing public is published (a new plan, or one only sold bespoke).
    current: PlanVersionOut | None = None
    #: The highest version number on the plan, any audience — the number the next draft follows.
    latest_version_no: int = 0


class PlanListOut(BaseModel):
    items: list[PlanListItemOut]
    #: Pass back as `cursor` for the next page; NULL on the last page.
    next_cursor: str | None = None


class PlanDetailOut(BaseModel):
    """`GET /admin/catalog/plans/{id}` — the plan, its counts, and one page of version summaries,
    newest first, filtered to an audience."""

    id: uuid.UUID
    product_key: str
    code: str
    name: str
    state: str
    sort_order: int
    created_at: datetime
    updated_at: datetime
    counts: VersionCountsOut
    latest_version_no: int = 0
    versions: list[VersionSummaryOut]
    next_cursor: str | None = None


class DeletedVersionOut(BaseModel):
    """What `DELETE /admin/catalog/versions/{id}` removed — a draft, never anything published."""

    id: uuid.UUID
    plan_id: uuid.UUID
    version_no: int
    deleted: Literal[True] = True
