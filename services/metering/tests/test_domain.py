"""The pure period arithmetic. No database."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain import current_period


def test_current_period_is_the_utc_calendar_month() -> None:
    start, end = current_period(datetime(2026, 3, 17, 9, 30, tzinfo=UTC))
    assert start == datetime(2026, 3, 1, tzinfo=UTC)
    assert end == datetime(2026, 4, 1, tzinfo=UTC)


def test_current_period_rolls_the_year_at_december() -> None:
    start, end = current_period(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    assert start == datetime(2026, 12, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


def test_current_period_is_half_open() -> None:
    """An event at exactly `end` belongs to the next period, never both."""
    start, end = current_period(datetime(2026, 6, 15, tzinfo=UTC))
    assert start <= datetime(2026, 6, 15, tzinfo=UTC) < end
    assert end == datetime(2026, 7, 1, tzinfo=UTC)
