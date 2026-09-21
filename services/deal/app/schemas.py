"""Request and response contracts for the deal service.

This module is the published API surface: a change here is a change to the OpenAPI
contract, and the CRM SPA's client maps onto it. The field names, nesting and enums mirror
`frontend/product-crm/src/api/types.ts` exactly — `DealOut` is the SPA's `Deal`,
`ActivityOut` its `Activity`, and the summary's `StageRollupOut` / `TrendPointOut` its
`StageRollup` / `TrendPoint` — so wiring the SPA from fixtures to live is a flag flip
rather than a rewrite.

Money (`value_minor`) is integer minor units in `currency` (ISO-4217), never a float.
Ids are opaque row UUIDs, exchanged as strings.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ------------------------------------------------------------------ vocabularies (match the SPA)

DealStage = Literal["lead", "qualified", "proposal", "negotiation", "closed_won", "closed_lost"]
ActivityType = Literal["call", "email", "meeting", "note", "task"]
ActivityDirection = Literal["inbound", "outbound"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# ------------------------------------------------------------------ deals


class DealIn(BaseModel):
    """Open an opportunity. `company_id` / `primary_contact_id` / `owner_id` are plain
    UUIDs into customer-service's schema — deal-service cannot verify they resolve there
    (no cross-schema FK, no cross-schema query), so none of the three is checked here.

    `owner_id` is optional: an omitted one defaults to the calling rep, matching how
    activities attribute themselves. Naming a different rep is an ORG-scoped act — the
    route re-applies the caller's scope exactly as it does on a detail route — and a
    caller with no rep identity who also names no owner has nobody to attribute the deal
    to, which is a 422, not a row owned by nothing."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    company_id: uuid.UUID
    owner_id: uuid.UUID | None = None
    value_minor: int = Field(ge=0)
    close_date: date
    currency: str = Field(default="INR", min_length=3, max_length=3)
    stage: DealStage = "lead"
    probability: int | None = Field(default=None, ge=0, le=100)
    primary_contact_id: uuid.UUID | None = None
    source: str = Field(default="", max_length=60)


class DealPatch(BaseModel):
    """Partial update of the plain deal fields. Deliberately **no `stage`** — a stage
    move only ever goes through `POST /deals/{id}/stage`, so the transition rule and its
    event can never be bypassed by a patch. Reassigning `owner_id` is scope-checked in
    `app.services.update_deal` exactly like naming one on create — an OWN-scoped caller
    may only set it to themselves."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    company_id: uuid.UUID | None = None
    primary_contact_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    value_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    close_date: date | None = None
    probability: int | None = Field(default=None, ge=0, le=100)
    source: str | None = Field(default=None, max_length=60)


class StageMoveIn(BaseModel):
    """Move a deal through the pipeline. `domain.can_transition` is the enforcement
    point — an illegal move (out of a closed stage, or to the same stage) answers 409."""

    model_config = ConfigDict(extra="forbid")

    stage: DealStage
    probability: int | None = Field(default=None, ge=0, le=100)


class DealOut(BaseModel):
    """Mirrors the SPA's `Deal`. `value_minor` is minor units of `currency`; `probability`
    is 0-100; a past `close_date` on an open deal reads as overdue; `last_activity_at` is
    the staleness clock (NULL: never touched)."""

    id: str
    name: str
    company_id: str
    primary_contact_id: str | None
    value_minor: int
    currency: str
    stage: DealStage
    owner_id: str
    probability: int
    close_date: date
    created_at: datetime
    last_activity_at: datetime | None
    source: str


# ------------------------------------------------------------------ activities


class ActivityIn(BaseModel):
    """Log one entry on the timeline. Append-only: there is no matching `Patch` — a wrong
    entry is corrected by logging a new one, not by editing this one.

    At least one of `deal_id` / `company_id` / `contact_id` must be given — an activity
    naming nothing is not a timeline entry. `direction` only means something for a call or
    an email; naming it on a meeting, note or task is refused. `occurred_at` defaults to
    now and may backfill the past, but never the future — a touch cannot be logged before
    it happens.

    `owner_id` is deliberately absent here: an OWN-scoped caller can only ever log against
    themselves, and the route forces it; only an ORG-scoped caller may attribute the entry
    to someone else, which is why the field lives on the model but not on this contract —
    see `app/api/routes.py`.
    """

    model_config = ConfigDict(extra="forbid")

    type: ActivityType
    subject: str = Field(min_length=1, max_length=300)
    body: str | None = Field(default=None, max_length=10_000)
    deal_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    direction: ActivityDirection | None = None
    occurred_at: datetime | None = None
    #: ORG-scoped callers may attribute the entry to a different rep; OWN-scoped callers
    #: never may — the route always overrides this for them.
    owner_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _at_least_one_target(self) -> Self:
        if self.deal_id is None and self.company_id is None and self.contact_id is None:
            raise ValueError("Name at least one of deal_id, company_id or contact_id")
        return self

    @model_validator(mode="after")
    def _direction_only_for_call_or_email(self) -> Self:
        if self.direction is not None and self.type not in ("call", "email"):
            raise ValueError("direction only applies to a call or an email")
        return self

    @model_validator(mode="after")
    def _not_in_the_future(self) -> Self:
        if self.occurred_at is not None:
            at = (
                self.occurred_at
                if self.occurred_at.tzinfo
                else self.occurred_at.replace(tzinfo=UTC)
            )
            if at > datetime.now(UTC):
                raise ValueError("occurred_at cannot be in the future")
        return self


class ActivityOut(BaseModel):
    """Mirrors the SPA's `Activity`. `type` is the wire name for the touch kind; exactly
    one of the target ids is usually set, and the owner always is."""

    id: str
    type: ActivityType
    subject: str
    body: str | None
    deal_id: str | None
    company_id: str | None
    contact_id: str | None
    owner_id: str
    occurred_at: datetime
    direction: ActivityDirection | None


# ------------------------------------------------------------------ summary rollups


class StageRollupOut(BaseModel):
    """One column of the pipeline funnel — count and summed open value per stage, with
    `pct` scaled to the richest stage so the bars read as a funnel. Mirrors the SPA's
    `StageRollup` (and its `stageFunnel` arithmetic in `data/derive.ts`)."""

    stage: DealStage
    label: str
    count: int
    value_minor: int
    pct: int


class TrendPointOut(BaseModel):
    """A point on the pipeline-value-over-time chart. Mirrors the SPA's `TrendPoint`."""

    label: str
    value_minor: int


class SummaryOut(BaseModel):
    """The Overview dashboard's server-side rollup: the same arithmetic the SPA's
    `data/derive.ts` runs over the deal list, exposed once so a reporting view can share
    it. Headline figures are computed live from the deals and activities; the trend's
    earlier months are representative history (the seam a reporting service replaces —
    see `app.domain.pipeline_trend`), with the final point always the live open pipeline.
    """

    #: Total open pipeline value, in minor units of `currency`.
    pipeline_value_minor: int
    #: Probability-weighted open pipeline (the forecast), in minor units.
    weighted_pipeline_minor: int
    #: How many deals are open (not closed-won or closed-lost).
    open_deals: int
    #: closed-won / decided, 0-100. Open deals do not drag the rate down.
    win_rate_pct: int
    #: Total value won across closed-won deals, in minor units.
    won_value_minor: int
    #: Touches logged in the trailing seven days.
    activities_this_week: int
    currency: str
    #: Open pipeline value over time, oldest first; the last point is live.
    trend: list[TrendPointOut] = Field(default_factory=list)
    #: The by-stage funnel over the four open stages, in board order.
    funnel: list[StageRollupOut] = Field(default_factory=list)
