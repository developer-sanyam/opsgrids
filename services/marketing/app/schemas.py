"""Request and response contracts for the marketing service.

This module is the published API surface: a change here is a contract change, and the frontend
generates its client from it. The shapes mirror `frontend/product-marketing/src/api/types.ts`
exactly, so flipping the SPA from fixtures to live data is a flag flip and not a rewrite.

Money is always an **integer amount of minor units** (paise for INR) plus an ISO-4217
`currency` — never a float. Ratios (ROAS) and per-lead / per-conversion costs are **derived**,
computed in `app.domain` and returned on the rollup shapes only; they are never accepted on the
way in and never stored.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Channel = Literal["email", "social", "search", "events"]
CampaignStatus = Literal["draft", "live", "paused", "ended"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- audiences


class AudienceIn(BaseModel):
    """Create a saved audience segment."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    size: int = Field(ge=0)
    criteria: str = Field(default="", max_length=400)
    #: The date the segment was defined — surfaced to the UI as `created_at`.
    created_at: date


class AudiencePatch(BaseModel):
    """Edit a saved audience segment. Every field optional; only what is sent is applied.
    Renaming re-checks the org-scoped uniqueness the create path enforces (`services.py`)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    size: int | None = Field(default=None, ge=0)
    criteria: str | None = Field(default=None, max_length=400)
    created_at: date | None = None


class AudienceOut(BaseModel):
    """A saved audience segment. `created_at` is the segment's definition date (a plain date),
    not the row's write timestamp."""

    id: uuid.UUID
    name: str
    size: int
    criteria: str
    created_at: date


# --------------------------------------------------------------------- campaigns


class CampaignIn(BaseModel):
    """Create a campaign.

    Money arrives as minor units, matching how it is stored and returned. `audience_id` must name
    an audience in the same organization — enforced in `services`, so naming another tenant's
    audience reads as "not found" rather than resolving.

    `currency` accepts any 3-letter code with no ISO allow-list. The Overview and Channels
    rollups (`domain.overview_totals`, `domain.channel_performance`) sum `*_minor` across every
    in-scope campaign assuming one currency per organization (`api/routes.py#overview` reads
    `campaigns[0].currency` as *the* org currency) — a campaign created in a second currency
    would sum into that total without conversion and silently misstate it. This is a known,
    unresolved risk (see the audit's Risks section): rejecting a second currency outright is a
    product decision nobody has made, since a legitimate multi-currency org would then have no
    way to record a campaign at all, so it is documented rather than enforced here. A future
    guard belongs in this validator once the product decides what a mismatch should do (refuse,
    convert, or partition the rollups by currency).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    channel: Channel
    status: CampaignStatus = "draft"
    currency: str = Field(default="INR", min_length=3, max_length=3)
    budget_minor: int = Field(default=0, ge=0)
    spent_minor: int = Field(default=0, ge=0)
    revenue_minor: int = Field(default=0, ge=0)
    pipeline_minor: int = Field(default=0, ge=0)
    leads: int = Field(default=0, ge=0)
    conversions: int = Field(default=0, ge=0)
    goal: str = Field(default="", max_length=200)
    audience_id: uuid.UUID
    start_date: date
    end_date: date | None = None
    owner: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def _dates_in_order(self) -> CampaignIn:
        """`end_date` is optional (an open-ended flight), but when it is set it cannot be
        before the campaign starts."""
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class CampaignPatch(BaseModel):
    """Edit a campaign. Every field optional; only what is sent is applied
    (`model_dump(exclude_unset=True)`). `currency` is deliberately not here — changing it would
    silently re-denominate every stored minor-unit amount. `status` moves only through the
    domain's allowed lifecycle transitions (`app.domain`); an illegal move is refused with 409,
    not accepted and coerced. `end_date >= start_date` is checked in `services.update_campaign`
    against the merged (existing + patched) values, because a single-field PATCH still has to
    make sense against the stored counterpart — it cannot be a `model_validator` here the way it
    is on `CampaignIn`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    channel: Channel | None = None
    status: CampaignStatus | None = None
    budget_minor: int | None = Field(default=None, ge=0)
    spent_minor: int | None = Field(default=None, ge=0)
    revenue_minor: int | None = Field(default=None, ge=0)
    pipeline_minor: int | None = Field(default=None, ge=0)
    leads: int | None = Field(default=None, ge=0)
    conversions: int | None = Field(default=None, ge=0)
    goal: str | None = Field(default=None, max_length=200)
    audience_id: uuid.UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    owner: str | None = Field(default=None, max_length=120)


class CampaignOut(BaseModel):
    """A campaign, shaped exactly as `types.ts#Campaign`. All amounts are minor units of
    `currency`; the derived ratios are computed client-side (and server-side in the rollups)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    channel: str
    status: str
    currency: str
    budget_minor: int
    spent_minor: int
    revenue_minor: int
    pipeline_minor: int
    leads: int
    conversions: int
    goal: str
    audience_id: uuid.UUID
    start_date: date
    end_date: date | None
    owner: str


# --------------------------------------------------------------------- analytics rollups


class RevenueSpendPoint(BaseModel):
    """A point on a revenue-vs-spend time series (minor units)."""

    label: str
    revenue_minor: int
    spend_minor: int


class ChannelPerformance(BaseModel):
    """One channel's rolled-up performance across the org's campaigns.

    `roas` is a plain ratio (revenue / spend), `0` when there is no spend. `cac_minor` and
    `cpl_minor` are minor units and `null` when their denominator (conversions, leads) is zero —
    the same divide-by-zero contract the SPA's `derive.ts` uses.
    """

    channel: str
    campaigns: int
    spend_minor: int
    revenue_minor: int
    pipeline_minor: int
    leads: int
    conversions: int
    roas: float
    cac_minor: int | None
    cpl_minor: int | None


class OverviewSummary(BaseModel):
    """The Overview screen in one payload: the four KPI headlines, the revenue-vs-spend series,
    and the per-channel breakdown. Every figure is derived server-side from the same campaign set
    and monthly series the list endpoints expose, so the number here and the number the SPA
    derives from the lists cannot disagree."""

    currency: str
    #: Closed-won revenue attributed across all campaigns, minor units.
    revenue_minor: int
    #: Total spend across all campaigns, minor units.
    spend_minor: int
    #: Open pipeline generated across all campaigns, minor units.
    pipeline_minor: int
    leads: int
    conversions: int
    #: revenue / spend, a plain ratio; 0 when there is no spend.
    roas: float
    #: spend / leads, minor units; null when there are no leads.
    cpl_minor: int | None
    #: The monthly revenue-vs-spend series behind the chart.
    revenue_vs_spend: list[RevenueSpendPoint]
    #: Per-channel performance, largest revenue first.
    channels: list[ChannelPerformance]
