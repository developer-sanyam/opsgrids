"""Pure rollups over campaigns — no I/O, no session, only sums and ratios.

This is the server-side twin of the SPA's `src/data/derive.ts`, kept byte-for-byte compatible on
the arithmetic: ROAS is `revenue / spend` and `0` when there is no spend; cost-per-lead and CAC
are integer minor units rounded half-away-from-zero, and `None` when their denominator is zero.
Computing them here as well as in the SPA means the Overview endpoint and the client that derives
from the campaign list produce the same number, because they apply the same rules.

`Money` (`platform_core.money`) is deliberately *not* used for the ratios: a ROAS is a
dimensionless ratio, not an amount, and CPL/CAC are amounts derived by division, which `Money`
does not offer. The inputs and outputs are plain integer minor units, so nothing here handles a
float.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

#: Stable presentation order so the channels never reshuffle between renders — mirrors
#: `derive.ts#CHANNEL_ORDER`.
CHANNEL_ORDER: tuple[str, ...] = ("search", "social", "email", "events")

#: A campaign's weekly performance is a fixed shape scaled to its totals, matching the SPA's
#: `CAMPAIGN_WEEK_SHAPE`: eight flight-weeks, each a fraction of the campaign's revenue and spend.
#: Living here means the live `/campaigns/{id}/performance` endpoint splits a campaign exactly the
#: way the fixture did, so the detail chart looks identical live and in fixtures.
CAMPAIGN_WEEK_SHAPE: tuple[tuple[str, float, float], ...] = (
    ("W1", 0.04, 0.09),
    ("W2", 0.08, 0.12),
    ("W3", 0.11, 0.13),
    ("W4", 0.14, 0.14),
    ("W5", 0.15, 0.13),
    ("W6", 0.16, 0.12),
    ("W7", 0.16, 0.14),
    ("W8", 0.16, 0.13),
)


def _round_half_up(value: Decimal) -> int:
    """Round to the nearest integer, halves away from zero — what `Math.round` does for the
    non-negative amounts here, and what an invoice does."""
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def roas(revenue_minor: int, spend_minor: int) -> float:
    """revenue / spend as a plain ratio; 0.0 when there is no spend (avoids divide-by-zero)."""
    if spend_minor == 0:
        return 0.0
    return revenue_minor / spend_minor


def cost_per_lead(spend_minor: int, leads: int) -> int | None:
    """spend / leads in minor units; None when there are no leads."""
    if leads == 0:
        return None
    return _round_half_up(Decimal(spend_minor) / Decimal(leads))


def cac(spend_minor: int, conversions: int) -> int | None:
    """spend / conversions in minor units; None when nothing converted."""
    if conversions == 0:
        return None
    return _round_half_up(Decimal(spend_minor) / Decimal(conversions))


@dataclass(frozen=True, slots=True)
class CampaignAmounts:
    """The subset of a campaign the rollups read. A tiny value object so `domain` never imports a
    SQLAlchemy model — it stays a pure layer under `models` in the import graph."""

    channel: str
    spent_minor: int
    revenue_minor: int
    pipeline_minor: int
    leads: int
    conversions: int


@dataclass(frozen=True, slots=True)
class ChannelRollup:
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


@dataclass(frozen=True, slots=True)
class OverviewRollup:
    revenue_minor: int
    spend_minor: int
    pipeline_minor: int
    leads: int
    conversions: int
    roas: float
    cpl_minor: int | None


def overview_totals(campaigns: Iterable[CampaignAmounts]) -> OverviewRollup:
    """The org-wide snapshot behind the four Overview KPI headlines."""
    revenue = spend = pipeline = leads = conversions = 0
    for c in campaigns:
        revenue += c.revenue_minor
        spend += c.spent_minor
        pipeline += c.pipeline_minor
        leads += c.leads
        conversions += c.conversions
    return OverviewRollup(
        revenue_minor=revenue,
        spend_minor=spend,
        pipeline_minor=pipeline,
        leads=leads,
        conversions=conversions,
        roas=roas(revenue, spend),
        cpl_minor=cost_per_lead(spend, leads),
    )


def channel_performance(campaigns: Sequence[CampaignAmounts]) -> list[ChannelRollup]:
    """Per-channel performance, one row per channel that has at least one campaign, largest
    revenue first. Draft campaigns (no spend, no revenue) still count toward the tally — matching
    `derive.ts#channelPerformance`."""
    rollups: list[ChannelRollup] = []
    for channel in CHANNEL_ORDER:
        rows = [c for c in campaigns if c.channel == channel]
        if not rows:
            continue
        spend = sum(c.spent_minor for c in rows)
        revenue = sum(c.revenue_minor for c in rows)
        pipeline = sum(c.pipeline_minor for c in rows)
        leads = sum(c.leads for c in rows)
        conversions = sum(c.conversions for c in rows)
        rollups.append(
            ChannelRollup(
                channel=channel,
                campaigns=len(rows),
                spend_minor=spend,
                revenue_minor=revenue,
                pipeline_minor=pipeline,
                leads=leads,
                conversions=conversions,
                roas=roas(revenue, spend),
                cac_minor=cac(spend, conversions),
                cpl_minor=cost_per_lead(spend, leads),
            )
        )
    rollups.sort(key=lambda r: r.revenue_minor, reverse=True)
    return rollups


@dataclass(frozen=True, slots=True)
class WeekPoint:
    label: str
    revenue_minor: int
    spend_minor: int


def campaign_weekly(revenue_minor: int, spent_minor: int) -> list[WeekPoint]:
    """Distribute a campaign's totals across its flight-weeks with the fixed shape — the live
    twin of the SPA's `getCampaignWeekly` fixture derivation."""
    return [
        WeekPoint(
            label=label,
            revenue_minor=_round_half_up(Decimal(revenue_minor) * Decimal(str(rev_frac))),
            spend_minor=_round_half_up(Decimal(spent_minor) * Decimal(str(spend_frac))),
        )
        for label, rev_frac, spend_frac in CAMPAIGN_WEEK_SHAPE
    ]
