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
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# ------------------------------------------------------------------ vocabularies (match the SPA)

DealStage = Literal["lead", "qualified", "proposal", "negotiation", "closed_won", "closed_lost"]
ActivityType = Literal["call", "email", "meeting", "note", "task"]
ActivityDirection = Literal["inbound", "outbound"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# ------------------------------------------------------------------ deals


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


class DealIn(BaseModel):
    """Create a deal. `value_minor` is integer minor units of `currency` (ISO-4217) — no
    float, ever. `owner_id` is optional: omitted, the deal belongs to the calling rep;
    naming another rep is an object-level scope question the route answers. `probability`
    omitted takes the stage's board default. `last_activity_at` is the server's staleness
    clock and `organization_id` comes from the tenant context, so neither is accepted."""

    name: str = Field(min_length=1, max_length=200)
    company_id: uuid.UUID
    #: Minor units of `currency`. Never negative, never a float.
    value_minor: int = Field(ge=0)
    close_date: date
    currency: str = Field(default="INR", min_length=3, max_length=3)
    stage: DealStage = "lead"
    owner_id: uuid.UUID | None = None
    probability: int | None = Field(default=None, ge=0, le=100)
    primary_contact_id: uuid.UUID | None = None
    source: str = Field(default="", max_length=60)


class StageIn(BaseModel):
    """Move a deal to another stage. A transition, not a field edit: which moves are legal
    is `domain.can_transition`, enforced by the service. `probability` omitted takes the
    new stage's default — 100 once won, 0 once lost."""

    stage: DealStage
    probability: int | None = Field(default=None, ge=0, le=100)


# ------------------------------------------------------------------ activities


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


class ActivityIn(BaseModel):
    """Log one touch against a deal. Append-only — there is no update or delete, by
    design — so every field here describes what happened, not what it should become.

    `type` is the wire name for the touch kind, matching `ActivityOut`. The deal comes
    from the path and the company from that deal, so neither is accepted in the body.
    `owner_id` omitted attributes the touch to the calling rep. `occurred_at` omitted is
    now; it is carried explicitly so a touch logged after the fact keeps its real time.
    """

    subject: str = Field(min_length=1, max_length=300)
    type: ActivityType = "note"
    body: str | None = Field(default=None, max_length=8000)
    owner_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    direction: ActivityDirection | None = None
    occurred_at: datetime | None = None


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
