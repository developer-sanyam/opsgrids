"""The pure rollups mirror the SPA's `data/derive.ts` — same arithmetic, same numbers.

No database: model instances are constructed in memory, which is exactly the point of
`domain.py` being pure.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app import domain
from app.models import Activity, Deal

NOW = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)


def deal(stage: str, value: int, probability: int = 50) -> Deal:
    return Deal(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name=f"deal-{uuid.uuid4().hex[:6]}",
        company_id=uuid.uuid4(),
        value_minor=value,
        currency="INR",
        stage=stage,
        owner_id=uuid.uuid4(),
        probability=probability,
        close_date=NOW.date(),
        source="",
    )


def activity(occurred_at: datetime) -> Activity:
    return Activity(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        activity_type="call",
        subject="x",
        owner_id=uuid.uuid4(),
        occurred_at=occurred_at,
    )


class TestRollups:
    def test_pipeline_value_counts_only_open_deals(self) -> None:
        deals = [deal("lead", 100), deal("negotiation", 200), deal("closed_won", 1_000)]
        assert domain.pipeline_value_minor(deals) == 300
        assert domain.open_count(deals) == 2

    def test_weighted_pipeline_rounds_per_deal(self) -> None:
        deals = [deal("lead", 100, probability=33), deal("closed_lost", 500, probability=0)]
        assert domain.weighted_pipeline_minor(deals) == 33

    def test_win_rate_is_over_decided_deals_only(self) -> None:
        deals = [
            deal("closed_won", 1),
            deal("closed_won", 1),
            deal("closed_lost", 1),
            deal("lead", 1),
            deal("proposal", 1),
        ]
        assert domain.win_rate_pct(deals) == 67  # 2 of 3 decided; open deals don't drag
        assert domain.win_rate_pct([deal("lead", 1)]) == 0

    def test_won_value_sums_closed_won(self) -> None:
        deals = [deal("closed_won", 70), deal("closed_won", 30), deal("closed_lost", 999)]
        assert domain.won_value_minor(deals) == 100

    def test_the_funnel_scales_to_the_richest_stage(self) -> None:
        deals = [
            deal("lead", 100),
            deal("qualified", 400),
            deal("proposal", 200),
            deal("closed_won", 10_000),  # excluded: the funnel is the open stages
        ]
        funnel = domain.stage_funnel(deals)
        assert [row["stage"] for row in funnel] == list(domain.OPEN_STAGES)
        by_stage = {row["stage"]: row for row in funnel}
        assert by_stage["qualified"]["pct"] == 100
        assert by_stage["lead"]["pct"] == 25
        assert by_stage["proposal"]["pct"] == 50
        assert by_stage["negotiation"] == {
            "stage": "negotiation",
            "label": "Negotiation",
            "count": 0,
            "value_minor": 0,
            "pct": 0,
        }

    def test_an_empty_book_yields_an_all_zero_funnel(self) -> None:
        funnel = domain.stage_funnel([])
        assert all(row["count"] == 0 and row["pct"] == 0 for row in funnel)

    def test_activities_this_week_is_a_trailing_window(self) -> None:
        acts = [
            activity(NOW - timedelta(days=1)),
            activity(NOW - timedelta(days=6, hours=23)),
            activity(NOW - timedelta(days=8)),  # outside the window
        ]
        assert domain.activities_this_week(acts, now=NOW) == 2

    def test_the_trend_ends_on_the_live_open_pipeline(self) -> None:
        trend = domain.pipeline_trend(123_456)
        assert len(trend) == 8
        assert trend[-1] == {"label": "Aug", "value_minor": 123_456}
        assert trend[0]["label"] == "Jan"


class TestTransitions:
    def test_the_stage_vocabulary_is_the_boards(self) -> None:
        assert domain.STAGE_ORDER == (
            "lead",
            "qualified",
            "proposal",
            "negotiation",
            "closed_won",
            "closed_lost",
        )
        assert domain.is_open("negotiation")
        assert not domain.is_open("closed_lost")
