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
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.pipeline import KEY_PATTERN


def _reject_explicit_null(instance: BaseModel, fields: tuple[str, ...]) -> None:
    """A PATCH field typed `X | None` is optional two different ways: omitted (leave alone) or
    explicitly `null` (the caller's own JSON says so). For a field the database column never
    allows NULL, only the first meaning is valid — the second is a malformed request (422),
    not a value that should reach an `IntegrityError` or a silent no-op write.
    `primary_contact_id` is genuinely nullable (a deal may have no primary contact) and is
    never passed here. Same helper `operations.app.schemas._reject_explicit_null` uses."""
    for field in fields:
        if field in instance.model_fields_set and getattr(instance, field) is None:
            raise ValueError(f"{field} cannot be set to null")


# ------------------------------------------------------------------ vocabularies (match the SPA)

#: A stage key as the wire carries it: an immutable slug. Which keys exist is the organization's
#: pipeline (`GET /deals/stages`), not a promise of this module, so the six-value enum this used
#: to be is gone. A well-formed key the pipeline does not contain is refused by the service
#: (422, `unknown_stage`), as a malformed one is refused here.
StageKey = Annotated[str, StringConstraints(pattern=KEY_PATTERN)]
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
    to, which is a 422, not a row owned by nothing.

    `stage` is optional too: an omitted one is the pipeline's entry stage, the first active open
    stage by position. Naming one that the pipeline does not contain, or has archived, is a 422."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    company_id: uuid.UUID
    owner_id: uuid.UUID | None = None
    value_minor: int = Field(ge=0)
    close_date: date
    currency: str = Field(default="INR", min_length=3, max_length=3)
    stage: StageKey | None = None
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
    #: Genuinely nullable in the domain (a deal may have no primary contact), so this one is
    #: deliberately not passed to `_reject_explicit_null` below.
    primary_contact_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    value_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    close_date: date | None = None
    probability: int | None = Field(default=None, ge=0, le=100)
    source: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        _reject_explicit_null(
            self,
            (
                "name",
                "company_id",
                "owner_id",
                "value_minor",
                "currency",
                "close_date",
                "probability",
                "source",
            ),
        )
        return self


class StageMoveIn(BaseModel):
    """Move a deal through the pipeline. `domain.can_transition` is the enforcement
    point — an illegal move (out of a closed stage, or to the same stage) answers 409. A key the
    pipeline does not contain, or has archived, answers 422; a closing move whose `probability`
    is not the pin (100 for a won stage, 0 for a lost one) answers 422, and omitting it takes
    the pin."""

    model_config = ConfigDict(extra="forbid")

    stage: StageKey
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
    stage: StageKey
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
    it happens. A value with no UTC offset is read as UTC, not as the caller's local time, so
    a client outside UTC must send its offset: a past wall clock sent without one is stored
    as UTC, wrong by that client's offset.

    `owner_id` is optional: an ORG-scoped caller may attribute the entry to another rep, while
    an OWN-scoped caller can only ever log against themselves and the route forces it, whatever
    the body says — see `app/api/routes.py`.
    """

    model_config = ConfigDict(extra="forbid")

    type: ActivityType
    subject: str = Field(min_length=1, max_length=300)
    body: str | None = Field(default=None, max_length=10_000)
    deal_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    direction: ActivityDirection | None = None
    occurred_at: datetime | None = Field(
        default=None,
        description=(
            "RFC 3339 instant. A value with no UTC offset is read as UTC, so send an offset "
            "such as 2026-09-02T14:45:00+05:30 or 2026-09-02T09:15:00Z."
        ),
    )
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

    stage: StageKey
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
    it. Headline figures are computed live from the deals and activities.

    `trend` is `None`, not a placeholder history: an open-pipeline-value-over-time chart needs a
    dated snapshot of what was open on a past day, and this service keeps only each deal's
    *current* stage and value, never a history of either. That is a genuinely missing capability
    (a stage-history/insights projection, dashboard-data audit §3.3 row 12), not something this
    endpoint can compute honestly today — so it reports `None` here instead of the fixed months
    it used to serve unflagged as if they were real.
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
    #: `None` until a dated pipeline-value history exists — see the class docstring.
    trend: list[TrendPointOut] | None = None
    #: The by-stage funnel over the four open stages, in board order.
    funnel: list[StageRollupOut] = Field(default_factory=list)


# ------------------------------------------------------------------ lead conversion (internal)


class LeadConversionDealIn(BaseModel):
    """What lead-service sends `:convert` — its own deterministic `deal_id`, so opening a
    deal is idempotent by id (`app/api/internal.py`)."""

    model_config = ConfigDict(extra="forbid")

    deal_id: uuid.UUID
    company_id: uuid.UUID
    contact_id: uuid.UUID | None = None
    owner_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    close_date: date
    source: str = Field(default="lead", max_length=60)


class LeadConversionDealOut(BaseModel):
    deal_id: str
