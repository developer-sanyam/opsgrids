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

from pydantic import BaseModel, ConfigDict, Field

Channel = Literal["email", "social", "search", "events"]
CampaignStatus = Literal["draft", "live", "paused", "ended"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- audiences


class AudienceIn(BaseModel):
    """Create a saved audience segment."""

    name: str = Field(min_length=1, max_length=160)
    size: int = Field(ge=0)
    criteria: str = Field(default="", max_length=400)
    #: The date the segment was defined — surfaced to the UI as `created_at`.
    created_at: date


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
    """

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
