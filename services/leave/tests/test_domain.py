"""Pure functions, no database. `domain.count_half_days` and `domain.today_in` are exercised
directly here — no I/O, so every case is a plain assertion rather than a fixture."""

from __future__ import annotations

import datetime as dt

import pytest

from app import domain


class TestCountHalfDays:
    def test_a_plain_working_week_is_ten_half_days(self) -> None:
        # Monday 2026-09-21 through Friday 2026-09-25 — five working days, no weekend.
        total = domain.count_half_days(
            dt.date(2026, 9, 21), dt.date(2026, 9, 25), half_day_start=False, half_day_end=False
        )
        assert total == 10

    def test_a_single_full_day_is_two_half_days(self) -> None:
        total = domain.count_half_days(
            dt.date(2026, 9, 21), dt.date(2026, 9, 21), half_day_start=False, half_day_end=False
        )
        assert total == 2

    def test_a_single_half_day_is_one_half_day(self) -> None:
        total = domain.count_half_days(
            dt.date(2026, 9, 21), dt.date(2026, 9, 21), half_day_start=True, half_day_end=True
        )
        assert total == 1

    def test_a_single_day_with_disagreeing_flags_is_rejected(self) -> None:
        with pytest.raises(domain.InvalidRange, match="agree"):
            domain.count_half_days(
                dt.date(2026, 9, 21), dt.date(2026, 9, 21), half_day_start=True, half_day_end=False
            )

    def test_weekends_are_excluded_even_when_they_are_the_range_edges(self) -> None:
        # Saturday 2026-09-19 through Monday 2026-09-21: one working day (Monday) plus a
        # weekend that contributes nothing.
        total = domain.count_half_days(
            dt.date(2026, 9, 19), dt.date(2026, 9, 21), half_day_start=False, half_day_end=False
        )
        assert total == 2

    def test_a_half_day_flag_on_a_weekend_edge_is_moot_not_an_error(self) -> None:
        # Saturday through Monday, `half_day_start=True` — the Saturday it would apply to
        # contributes nothing regardless, so the flag changes nothing about the total.
        total = domain.count_half_days(
            dt.date(2026, 9, 19), dt.date(2026, 9, 21), half_day_start=True, half_day_end=False
        )
        assert total == 2

    def test_a_pure_weekend_range_has_no_working_days_and_is_rejected(self) -> None:
        with pytest.raises(domain.InvalidRange, match="no working days"):
            domain.count_half_days(
                dt.date(2026, 9, 19), dt.date(2026, 9, 20), half_day_start=False, half_day_end=False
            )

    def test_end_before_start_is_rejected(self) -> None:
        with pytest.raises(domain.InvalidRange, match="end_date"):
            domain.count_half_days(
                dt.date(2026, 9, 21), dt.date(2026, 9, 20), half_day_start=False, half_day_end=False
            )

    def test_half_day_start_and_end_on_a_multi_day_range_shave_one_half_day_each(self) -> None:
        # Monday through Wednesday, half day at both ends: 1 + 2 + 1 = 4.
        total = domain.count_half_days(
            dt.date(2026, 9, 21), dt.date(2026, 9, 23), half_day_start=True, half_day_end=True
        )
        assert total == 4


class TestTodayIn:
    """The one place this service resolves "today" outside UTC — and the exact boundary the
    rebuild's stated defect (`datetime.now(UTC).date()`, see `app/models.py`) gets wrong."""

    def test_an_instant_ahead_of_utc_can_already_be_tomorrow_locally(self) -> None:
        # 19:00 UTC on the 21st is 00:30 IST on the 22nd (UTC+5:30) — a UTC-naive "today" would
        # answer the 21st, which is wrong for this organization's own calendar.
        now = dt.datetime(2026, 9, 21, 19, 0, tzinfo=dt.UTC)
        assert domain.today_in("Asia/Kolkata", now=now) == dt.date(2026, 9, 22)
        # The naive comparison this rebuild does not repeat, spelled out so the contrast reads:
        assert now.date() == dt.date(2026, 9, 21)

    def test_an_instant_behind_utc_can_still_be_yesterday_locally(self) -> None:
        # 02:00 UTC on the 22nd is 19:00 the previous day in America/Los_Angeles (UTC-7 in
        # September, DST) — a UTC-naive "today" would answer the 22nd, one day too early for
        # this organization.
        now = dt.datetime(2026, 9, 22, 2, 0, tzinfo=dt.UTC)
        assert domain.today_in("America/Los_Angeles", now=now) == dt.date(2026, 9, 21)
        assert now.date() == dt.date(2026, 9, 22)

    def test_an_unknown_timezone_is_refused_loudly(self) -> None:
        with pytest.raises(domain.InvalidTimezone):
            domain.today_in("Not/AZone", now=dt.datetime.now(dt.UTC))
