"""Cost arithmetic, the date window and rate-card precedence — the parts of M1 with no I/O.

Pure on purpose: the two rules that decide whether a dashboard number is *right* (half-up
rounding on exact halves, and which rate wins) should not need Postgres to be verified.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from app import m1_analytics, m1_cost
from app.m1_cost import DateWindow, RangeError, estimate_minor, resolve_window
from app.m1_models import PLACEHOLDER_LABEL, PLATFORM_ORG_ID, MessagingRateCard

# --------------------------------------------------------------------- rounding


@pytest.mark.parametrize(
    ("messages", "rate", "expected"),
    [
        (0, 78460, 0),
        (1000, 78460, 78460),  # exactly one thousand: the rate itself
        (1, 78460, 78),  # 78.46 -> 78
        (5, 78460, 392),  # 392.30 -> 392
        (1, 500, 1),  # exactly 0.5 rounds UP (banker's rounding would give 0)
        (3, 500, 2),  # exactly 1.5 -> 2
        (5, 500, 3),  # exactly 2.5 -> 3 (banker's would give 2)
        (1, 499, 0),  # 0.499 -> 0
        (1, 11500, 12),  # 11.5 -> 12
        (2, 11500, 23),  # 23.0 exactly
        (1, 0, 0),  # service: free
        (10**9, 78460, 78_460_000_000),  # large, still exact integers
    ],
)
def test_estimate_is_half_up_integer_minor_units(messages: int, rate: int, expected: int) -> None:
    result = estimate_minor(messages, rate)
    assert result == expected
    assert isinstance(result, int)


def test_estimate_refuses_negative_inputs() -> None:
    with pytest.raises(ValueError, match="negative"):
        estimate_minor(-1, 100)
    with pytest.raises(ValueError, match="negative"):
        estimate_minor(1, -100)


# --------------------------------------------------------------------- the window


def test_default_window_is_the_last_thirty_utc_days_ending_today() -> None:
    today = date(2026, 9, 26)
    window = resolve_window(None, None, today=today)
    assert window.date_to == today
    assert window.date_from == date(2026, 8, 28)
    assert len(window.days()) == 30


def test_window_bounds_are_utc_midnight_and_end_is_exclusive() -> None:
    window = DateWindow(date(2026, 9, 10), date(2026, 9, 12))
    assert window.start == datetime(2026, 9, 10, 0, 0, tzinfo=UTC)
    # The first instant after the 12th, so 23:59:59.999999 on the 12th is inside and midnight
    # on the 13th is not.
    assert window.end == datetime(2026, 9, 13, 0, 0, tzinfo=UTC)
    assert window.days() == [date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)]


def test_single_day_window_is_valid() -> None:
    window = resolve_window(date(2026, 9, 10), date(2026, 9, 10))
    assert window.days() == [date(2026, 9, 10)]


def test_only_from_given_ends_today_and_only_to_given_starts_thirty_days_before() -> None:
    today = date(2026, 9, 26)
    assert resolve_window(date(2026, 9, 1), None, today=today).date_to == today
    only_to = resolve_window(None, date(2026, 9, 30), today=today)
    assert only_to.date_from == date(2026, 9, 1)


def test_inverted_range_is_refused() -> None:
    with pytest.raises(RangeError) as excinfo:
        resolve_window(date(2026, 9, 12), date(2026, 9, 10))
    assert excinfo.value.code == "range_inverted"


def test_range_longer_than_the_maximum_is_refused_and_the_maximum_is_allowed() -> None:
    start = date(2026, 1, 1)
    ok = resolve_window(start, date(2026, 12, 31))  # 365 days
    assert len(ok.days()) == 365
    edge = resolve_window(date(2025, 12, 31), date(2026, 12, 31))  # exactly 366 days
    assert len(edge.days()) == m1_cost.MAX_RANGE_DAYS
    with pytest.raises(RangeError) as excinfo:
        resolve_window(date(2025, 12, 30), date(2026, 12, 31))  # 367
    assert excinfo.value.code == "range_too_long"


# --------------------------------------------------------------------- rate precedence

ORG = uuid.uuid4()
OTHER_ORG = uuid.uuid4()
PLATFORM = uuid.UUID(PLATFORM_ORG_ID)


def rate(org: uuid.UUID, category: str, amount: int, effective: date) -> MessagingRateCard:
    is_default = org == PLATFORM
    return MessagingRateCard(
        organization_id=org,
        channel="whatsapp",
        category=category,
        amount_minor_per_1000_messages=amount,
        currency="INR",
        effective_from=effective,
        label=PLACEHOLDER_LABEL if is_default else "organization",
        is_placeholder=is_default,
    )


def test_an_organization_override_beats_the_platform_default() -> None:
    rows = [
        rate(PLATFORM, "marketing", 78460, date(2026, 9, 1)),
        rate(ORG, "marketing", 90000, date(2026, 9, 5)),
    ]
    picked = m1_analytics.pick_rates(rows, ORG, date(2026, 9, 10))
    assert picked["marketing"].amount_minor_per_1000_messages == 90000
    assert picked["marketing"].organization_id == ORG


def test_an_override_that_is_not_yet_in_force_does_not_apply() -> None:
    rows = [
        rate(PLATFORM, "marketing", 78460, date(2026, 9, 1)),
        rate(ORG, "marketing", 90000, date(2026, 9, 20)),
    ]
    picked = m1_analytics.pick_rates(rows, ORG, date(2026, 9, 10))
    assert picked["marketing"].amount_minor_per_1000_messages == 78460
    assert picked["marketing"].is_placeholder is True


def test_the_override_wins_even_when_the_default_is_newer() -> None:
    rows = [
        rate(ORG, "utility", 20000, date(2026, 1, 1)),
        rate(PLATFORM, "utility", 11500, date(2026, 9, 1)),
    ]
    picked = m1_analytics.pick_rates(rows, ORG, date(2026, 9, 10))
    assert picked["utility"].amount_minor_per_1000_messages == 20000


def test_the_latest_price_point_in_force_wins_within_the_organization() -> None:
    rows = [
        rate(ORG, "utility", 20000, date(2026, 1, 1)),
        rate(ORG, "utility", 25000, date(2026, 6, 1)),
        rate(ORG, "utility", 30000, date(2026, 12, 1)),  # future
    ]
    assert (
        m1_analytics.pick_rates(rows, ORG, date(2026, 9, 10))[
            "utility"
        ].amount_minor_per_1000_messages
        == 25000
    )
    assert (
        m1_analytics.pick_rates(rows, ORG, date(2026, 12, 2))[
            "utility"
        ].amount_minor_per_1000_messages
        == 30000
    )
    # Before every row: the earliest known price rather than a zero estimate.
    assert (
        m1_analytics.pick_rates(rows, ORG, date(2025, 1, 1))[
            "utility"
        ].amount_minor_per_1000_messages
        == 20000
    )


def test_another_organizations_override_is_never_picked() -> None:
    rows = [
        rate(PLATFORM, "marketing", 78460, date(2026, 9, 1)),
        rate(OTHER_ORG, "marketing", 1, date(2026, 9, 1)),
    ]
    # `_rate_rows` never returns another tenant's rows (the query and RLS both exclude them), but
    # `pick_rates` must not treat a stray row as a platform default either: it would price this
    # organization at someone else's rate. Only the platform tenant's rows are defaults.
    picked = m1_analytics.pick_rates(rows, ORG, date(2026, 9, 10))
    assert picked["marketing"].amount_minor_per_1000_messages == 78460


def test_cost_lines_add_up_to_the_total_from_rounded_per_category_amounts() -> None:
    picked = m1_analytics.pick_rates(
        [
            rate(PLATFORM, "marketing", 78460, date(2026, 9, 1)),
            rate(PLATFORM, "utility", 11500, date(2026, 9, 1)),
            rate(PLATFORM, "authentication", 11500, date(2026, 9, 1)),
            rate(PLATFORM, "service", 0, date(2026, 9, 1)),
        ],
        ORG,
        date(2026, 9, 10),
    )
    cost = m1_analytics.build_cost(
        {"marketing": 5, "utility": 1, "authentication": 0, "service": 9}, picked, ORG
    )
    lines = {line.category: line.amount.amount_minor for line in cost.by_category}
    assert lines == {"marketing": 392, "utility": 12, "authentication": 0, "service": 0}
    assert cost.total.amount_minor == sum(lines.values()) == 404
    assert cost.total.currency == "INR"
    assert cost.estimated is True
    assert cost.rate_card.placeholder is True
    assert cost.rate_card.source == "platform_default"
    assert cost.rate_card.version == PLACEHOLDER_LABEL
    assert [line.category for line in cost.by_category] == list(m1_cost.CATEGORIES)


def test_failure_labels_are_never_empty() -> None:
    assert m1_analytics.failure_label("131026") == "Message undeliverable"
    assert "503" in m1_analytics.failure_label("http_503")
    assert "999999" in m1_analytics.failure_label("999999")
    assert m1_analytics.failure_label("dispatch_failed")
