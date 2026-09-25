"""Pure period/comparison arithmetic and the opaque activity cursor (`docs/insights/metrics.md`
§2). No database, no broker — every case here is the doc's own worked example.
"""

from __future__ import annotations

from datetime import date

import pytest

from app import domain


class TestResolvePeriod:
    def test_today(self) -> None:
        resolved = domain.resolve_period("today", today=date(2026, 9, 25))
        assert resolved.window == domain.Window(date(2026, 9, 25), date(2026, 9, 25))

    def test_this_week_starts_monday(self) -> None:
        # 2026-09-25 is a Friday.
        resolved = domain.resolve_period("this_week", today=date(2026, 9, 25))
        assert resolved.window.from_ == date(2026, 9, 21)  # the Monday
        assert resolved.window.to == date(2026, 9, 25)

    def test_this_month(self) -> None:
        resolved = domain.resolve_period("this_month", today=date(2026, 9, 25))
        assert resolved.window == domain.Window(date(2026, 9, 1), date(2026, 9, 25))

    def test_this_quarter_default_fiscal_year(self) -> None:
        # April default: Jan-Mar is a quarter, so 25 Feb sits inside it.
        resolved = domain.resolve_period(
            "this_quarter", today=date(2027, 2, 25), fiscal_year_start_month=4
        )
        assert resolved.window == domain.Window(date(2027, 1, 1), date(2027, 2, 25))

    def test_last_30_days(self) -> None:
        resolved = domain.resolve_period("last_30_days", today=date(2026, 9, 25))
        assert resolved.window == domain.Window(date(2026, 8, 27), date(2026, 9, 25))

    def test_last_month(self) -> None:
        resolved = domain.resolve_period("last_month", today=date(2026, 9, 25))
        assert resolved.window == domain.Window(date(2026, 8, 1), date(2026, 8, 31))

    def test_custom_window(self) -> None:
        resolved = domain.resolve_period(
            "custom", today=date(2026, 9, 25), from_=date(2026, 1, 1), to=date(2026, 1, 31)
        )
        assert resolved.window == domain.Window(date(2026, 1, 1), date(2026, 1, 31))

    def test_custom_requires_from_and_to(self) -> None:
        with pytest.raises(domain.InvalidPeriodError):
            domain.resolve_period("custom", today=date(2026, 9, 25))

    def test_custom_refuses_a_window_past_today(self) -> None:
        with pytest.raises(domain.InvalidPeriodError):
            domain.resolve_period(
                "custom", today=date(2026, 9, 25), from_=date(2026, 9, 1), to=date(2026, 9, 26)
            )

    def test_custom_refuses_more_than_a_year(self) -> None:
        with pytest.raises(domain.InvalidPeriodError):
            domain.resolve_period(
                "custom", today=date(2027, 9, 25), from_=date(2026, 1, 1), to=date(2027, 9, 25)
            )

    def test_unknown_period(self) -> None:
        with pytest.raises(domain.InvalidPeriodError):
            domain.resolve_period("fortnight", today=date(2026, 9, 25))


class TestComparisonWindow:
    def test_this_month_clips_to_the_shorter_previous_month(self) -> None:
        """`docs/insights/metrics.md` §2's own example: "1-31 Mar -> 1-28 Feb"."""
        resolved = domain.resolve_period("this_month", today=date(2026, 3, 31))
        comparison = domain.comparison_window(resolved, "previous_period")
        assert comparison == domain.Window(date(2026, 2, 1), date(2026, 2, 28))

    def test_this_month_same_length_previous_month(self) -> None:
        """ "1-25 Sep -> 1-25 Aug"."""
        resolved = domain.resolve_period("this_month", today=date(2026, 9, 25))
        comparison = domain.comparison_window(resolved, "previous_period")
        assert comparison == domain.Window(date(2026, 8, 1), date(2026, 8, 25))

    def test_today_previous_period_is_yesterday(self) -> None:
        resolved = domain.resolve_period("today", today=date(2026, 9, 25))
        comparison = domain.comparison_window(resolved, "previous_period")
        assert comparison == domain.Window(date(2026, 9, 24), date(2026, 9, 24))

    def test_this_week_previous_period(self) -> None:
        resolved = domain.resolve_period("this_week", today=date(2026, 9, 25))  # Friday
        comparison = domain.comparison_window(resolved, "previous_period")
        assert comparison == domain.Window(date(2026, 9, 14), date(2026, 9, 18))

    def test_this_quarter_clips_to_the_shorter_previous_quarter(self) -> None:
        # Fiscal Jan-Mar quarter, day 31 (last day) vs Oct-Dec (31, 30, 31 -> also 92 days) —
        # use a fiscal start where the previous quarter is shorter: fiscal_year_start_month=2
        # gives Feb-Apr, Nov-Jan (31+31+30=92) vs May-Jul (31+30+31=92)... choose a pair that
        # actually differs: fiscal_year_start_month=1 (calendar quarters), quarter Apr-Jun (30
        # days elapsed to 30 Jun) vs Jan-Mar (31+28=59 -> Jan 1 + 89 days clipped to Mar 31).
        resolved = domain.resolve_period(
            "this_quarter", today=date(2026, 6, 30), fiscal_year_start_month=1
        )
        comparison = domain.comparison_window(
            resolved, "previous_period", fiscal_year_start_month=1
        )
        assert comparison == domain.Window(date(2026, 1, 1), date(2026, 3, 31))

    def test_last_30_days_previous_period(self) -> None:
        resolved = domain.resolve_period("last_30_days", today=date(2026, 9, 25))
        comparison = domain.comparison_window(resolved, "previous_period")
        assert comparison == domain.Window(date(2026, 7, 28), date(2026, 8, 26))

    def test_last_month_previous_period(self) -> None:
        resolved = domain.resolve_period("last_month", today=date(2026, 9, 25))
        comparison = domain.comparison_window(resolved, "previous_period")
        assert comparison == domain.Window(date(2026, 7, 1), date(2026, 7, 31))

    def test_custom_whole_months_compares_the_same_span_of_whole_months(self) -> None:
        resolved = domain.resolve_period(
            "custom", today=date(2026, 9, 25), from_=date(2026, 7, 1), to=date(2026, 8, 31)
        )
        comparison = domain.comparison_window(resolved, "previous_period")
        assert comparison == domain.Window(date(2026, 5, 1), date(2026, 6, 30))

    def test_custom_partial_window_compares_the_same_day_count_immediately_before(self) -> None:
        resolved = domain.resolve_period(
            "custom", today=date(2026, 9, 25), from_=date(2026, 9, 10), to=date(2026, 9, 20)
        )
        comparison = domain.comparison_window(resolved, "previous_period")
        assert comparison == domain.Window(date(2026, 8, 30), date(2026, 9, 9))

    def test_same_period_last_year(self) -> None:
        resolved = domain.resolve_period("this_month", today=date(2026, 9, 25))
        comparison = domain.comparison_window(resolved, "same_period_last_year")
        assert comparison == domain.Window(date(2025, 9, 1), date(2025, 9, 25))

    def test_same_period_last_year_clamps_leap_day(self) -> None:
        resolved = domain.resolve_period(
            "custom", today=date(2028, 3, 1), from_=date(2028, 2, 29), to=date(2028, 2, 29)
        )
        comparison = domain.comparison_window(resolved, "same_period_last_year")
        assert comparison == domain.Window(date(2027, 2, 28), date(2027, 2, 28))

    def test_none_omits_the_comparison(self) -> None:
        resolved = domain.resolve_period("today", today=date(2026, 9, 25))
        assert domain.comparison_window(resolved, "none") is None

    def test_unknown_compare(self) -> None:
        resolved = domain.resolve_period("today", today=date(2026, 9, 25))
        with pytest.raises(domain.InvalidPeriodError):
            domain.comparison_window(resolved, "quarterly")


class TestDeltas:
    def test_delta_pct(self) -> None:
        assert domain.delta_pct(112.0, 100.0) == 12.0

    def test_delta_pct_no_baseline(self) -> None:
        assert domain.delta_pct(10.0, 0.0) is None

    def test_delta_pp(self) -> None:
        assert domain.delta_pp(57.1, 50.0) == 7.1

    def test_delta_pp_from_zero_baseline_is_not_no_baseline(self) -> None:
        assert domain.delta_pp(50.0, 0.0) == 50.0


class TestSeriesWindowValidation:
    def test_day_grain_within_limit(self) -> None:
        domain.validate_series_window(
            "day", date(2026, 6, 1), date(2026, 8, 29), today=date(2026, 9, 25)
        )

    def test_day_grain_beyond_limit(self) -> None:
        with pytest.raises(domain.InvalidPeriodError):
            domain.validate_series_window(
                "day", date(2026, 1, 1), date(2026, 9, 25), today=date(2026, 9, 25)
            )

    def test_unknown_grain(self) -> None:
        with pytest.raises(domain.InvalidPeriodError):
            domain.validate_series_window(
                "hour", date(2026, 9, 1), date(2026, 9, 25), today=date(2026, 9, 25)
            )

    def test_to_in_the_future(self) -> None:
        with pytest.raises(domain.InvalidPeriodError):
            domain.validate_series_window(
                "day", date(2026, 9, 1), date(2026, 9, 30), today=date(2026, 9, 25)
            )


class TestCursor:
    def test_round_trip(self) -> None:
        payload = {"occurred_at": "2026-09-25T09:58:02Z", "kind": "deal_won", "id": "abc"}
        cursor = domain.encode_cursor(payload)
        assert domain.decode_cursor(cursor) == payload

    def test_malformed_cursor_raises(self) -> None:
        with pytest.raises(domain.InvalidCursorError):
            domain.decode_cursor("not-a-real-cursor!!")

    def test_cursor_is_unpadded(self) -> None:
        cursor = domain.encode_cursor({"a": 1})
        assert "=" not in cursor
