"""Pure time arithmetic for PulseBoard's timesheets. No I/O, no session — minutes in, plain
values out, so this is trivially testable and mirrors the SPA's `data/derive.ts` so the numbers
agree whichever side computes them.

Hours never exist here as floats until the last serialising step: storage and arithmetic are
integer minutes, and `hours()` converts exactly (a minute is 1/60 — the halves and quarters the
grid speaks in are exact binary fractions, so the wire value round-trips).
"""

from __future__ import annotations

from datetime import date, timedelta

# ------------------------------------------------------------------ vocabulary

STATUS_ORDER: tuple[str, ...] = ("draft", "submitted", "approved")
STATUS_LABELS: dict[str, str] = {
    "draft": "Draft",
    "submitted": "Submitted",
    "approved": "Approved",
}

MINUTES_PER_HOUR = 60

#: Forward through the approval flow, plus a reopen step back to `draft` from either rung —
#: a submitted or approved week can be pulled back for correction, but never skips a stage.
STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"submitted"}),
    "submitted": frozenset({"approved", "draft"}),
    "approved": frozenset({"draft"}),
}


def can_transition(current: str, target: str) -> bool:
    """Whether a week may move from `current` to `target` — the single source of truth for
    the status endpoint, so an illegal move is refused by name rather than free-form."""
    return target in STATUS_TRANSITIONS.get(current, frozenset())


# ------------------------------------------------------------------ week arithmetic


def week_monday(day: date) -> date:
    """The Monday of the week containing `day` — the SPA's `mondayOf`."""
    return day - timedelta(days=day.weekday())


def week_dates(monday: date) -> list[date]:
    """The seven dates of the week starting `monday`, Monday first — the SPA's `WEEK_DATES`."""
    return [monday + timedelta(days=i) for i in range(7)]


def week_label(monday: date) -> str:
    """The ISO-week label the utilization chart uses for a point (`W35`)."""
    return f"W{monday.isocalendar().week}"


# ------------------------------------------------------------------ hours (wire) ↔ minutes (db)


def hours(minutes: int) -> float:
    """Integer minutes → decimal hours, for the wire. Exact for the grid's half-hours."""
    return minutes / MINUTES_PER_HOUR


def minutes(hrs: float) -> int:
    """Decimal hours → integer minutes, for storage. Rounded to the nearest minute."""
    return round(hrs * MINUTES_PER_HOUR)


# ------------------------------------------------------------------ utilization


def utilization_pct(billable_minutes: int, capacity_minutes: int) -> float:
    """Billable share of capacity, as a percentage with one decimal — `derive.ts
    teamUtilization` (`round(x * 1000) / 10`)."""
    if capacity_minutes == 0:
        return 0.0
    return round(billable_minutes / capacity_minutes * 1000) / 10


#: Representative utilization history (%) for the weeks before the demo's live one, mirroring
#: the SPA's `data/samples.ts` UTILIZATION_TREND minus its final (current-week) point. The
#: summary endpoint appends the live current-week figure, so the chart is real where data
#: exists and representative where it does not — the same treatment the projects service gives
#: its burndown. Replaced by real history as weeks accumulate.
UTILIZATION_HISTORY: tuple[int, ...] = (71, 69, 74, 76, 73, 78, 80, 77, 81, 79, 83)
