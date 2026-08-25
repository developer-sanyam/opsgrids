"""The derived numbers, checked in isolation — no database, no HTTP.

These are the server-side twin of the SPA's `derive.ts`, and the point of the test is that the
two agree on the awkward cases: a zero denominator returns None or 0 rather than raising, draft
campaigns count toward a channel's tally without moving its money, and the channel order is by
revenue, largest first.
"""

from __future__ import annotations

from app import domain
from app.domain import CampaignAmounts


def _c(
    channel: str, spent: int, revenue: int, pipeline: int, leads: int, conv: int
) -> CampaignAmounts:
    return CampaignAmounts(
        channel=channel,
        spent_minor=spent,
        revenue_minor=revenue,
        pipeline_minor=pipeline,
        leads=leads,
        conversions=conv,
    )


class TestRatios:
    def test_roas_is_revenue_over_spend(self) -> None:
        assert domain.roas(1000, 250) == 4.0

    def test_roas_is_zero_when_there_is_no_spend(self) -> None:
        """The divide-by-zero contract: no spend means 0, never an error."""
        assert domain.roas(1000, 0) == 0.0

    def test_cost_per_lead_rounds_to_minor_units(self) -> None:
        assert domain.cost_per_lead(1000, 3) == 333

    def test_cost_per_lead_is_none_with_no_leads(self) -> None:
        assert domain.cost_per_lead(1000, 0) is None

    def test_cac_rounds_to_minor_units(self) -> None:
        assert domain.cac(1000, 8) == 125

    def test_cac_is_none_with_no_conversions(self) -> None:
        assert domain.cac(1000, 0) is None


class TestChannelPerformance:
    def test_rows_are_ordered_by_revenue_largest_first(self) -> None:
        rows = domain.channel_performance(
            [
                _c("email", 100, 200, 0, 10, 2),
                _c("search", 100, 900, 0, 10, 2),
                _c("social", 100, 500, 0, 10, 2),
            ]
        )
        assert [r.channel for r in rows] == ["search", "social", "email"]

    def test_a_channel_with_no_campaigns_is_absent(self) -> None:
        rows = domain.channel_performance([_c("email", 100, 200, 0, 10, 2)])
        assert {r.channel for r in rows} == {"email"}

    def test_a_draft_counts_toward_the_tally_without_moving_money(self) -> None:
        """A draft has no spend and no revenue but is still one of the channel's campaigns."""
        rows = domain.channel_performance(
            [
                _c("social", 100, 500, 20, 10, 5),
                _c("social", 0, 0, 0, 0, 0),  # a draft
            ]
        )
        (social,) = rows
        assert social.campaigns == 2
        assert social.spend_minor == 100
        assert social.revenue_minor == 500
        assert social.roas == 5.0
        assert social.cac_minor == 20  # 100 / 5
        assert social.cpl_minor == 10  # 100 / 10


class TestOverviewTotals:
    def test_totals_sum_the_amounts_and_derive_the_ratios(self) -> None:
        totals = domain.overview_totals(
            [
                _c("search", 300, 1200, 500, 30, 6),
                _c("email", 100, 300, 100, 20, 4),
            ]
        )
        assert totals.spend_minor == 400
        assert totals.revenue_minor == 1500
        assert totals.pipeline_minor == 600
        assert totals.leads == 50
        assert totals.conversions == 10
        assert totals.roas == 1500 / 400
        assert totals.cpl_minor == 8  # 400 / 50

    def test_empty_input_has_zero_roas_and_null_cpl(self) -> None:
        totals = domain.overview_totals([])
        assert totals.roas == 0.0
        assert totals.cpl_minor is None


class TestCampaignWeekly:
    def test_it_splits_the_totals_across_eight_weeks(self) -> None:
        weekly = domain.campaign_weekly(revenue_minor=1_000_000, spent_minor=800_000)
        assert len(weekly) == 8
        assert [w.label for w in weekly] == ["W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8"]

    def test_the_first_week_matches_the_fixed_shape(self) -> None:
        """W1 is 4% of revenue and 9% of spend — the same fractions the SPA fixture uses."""
        weekly = domain.campaign_weekly(revenue_minor=1_000_000, spent_minor=1_000_000)
        assert weekly[0].revenue_minor == 40_000
        assert weekly[0].spend_minor == 90_000
