"""Pure functions, no I/O (CLAUDE.md's service anatomy).

Two independent things live here, both real algorithms worth one tested definition rather than
copy-pasted per endpoint (`docs/insights/metrics.md` §1-§2):

- **period and comparison window arithmetic** — every rule `GET /summary`/`/series` needs to
  turn `period=this_month&compare=previous_period` into two concrete date ranges, entirely
  independent of any fact table, so it lands and is tested in I1 even though nothing yet
  computes a value over the windows it resolves;
- **the opaque keyset cursor** `/activity` pages with, encode/decode only — the query it
  paginates is not built until a slice has a fact table to union over.

Nothing here reads a clock itself: `today` always arrives as a parameter, because "today" is the
organization-local date (`org_profile.timezone`), computed once by the caller
(`app/selectors.py`) and passed down, never re-derived mid-calculation.
"""

from __future__ import annotations

import base64
import json
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

PeriodKey = Literal[
    "today", "this_week", "this_month", "this_quarter", "last_30_days", "last_month", "custom"
]
CompareKey = Literal["previous_period", "same_period_last_year", "none"]

PERIOD_KEYS: tuple[PeriodKey, ...] = (
    "today",
    "this_week",
    "this_month",
    "this_quarter",
    "last_30_days",
    "last_month",
    "custom",
)
COMPARE_KEYS: tuple[CompareKey, ...] = ("previous_period", "same_period_last_year", "none")

#: `docs/insights/api.md` §2: a custom window spans at most a year.
MAX_CUSTOM_SPAN_DAYS = 366


class InvalidPeriodError(ValueError):
    """A period/compare/from/to combination the caller must fix.

    Raised, never guessed past — `app/api/routes.py` turns this into a 422
    (`ValidationProblem`), the same as any other malformed request parameter.
    """


@dataclass(frozen=True, slots=True)
class Window:
    """An inclusive local-date range, `from_ <= to`."""

    from_: date
    to: date

    def __post_init__(self) -> None:
        if self.from_ > self.to:
            raise InvalidPeriodError(f"window is empty: {self.from_} is after {self.to}")

    @property
    def days(self) -> int:
        return (self.to - self.from_).days + 1


@dataclass(frozen=True, slots=True)
class ResolvedPeriod:
    key: PeriodKey
    window: Window
    label: str


def window_label(window: Window) -> str:
    """`"1–25 Sep 2026"` for a window inside one month, `"28 Feb–3 Mar 2026"` across months or
    years — `docs/insights/api.md` §3's own example. An en dash, not a hyphen, between the two
    day markers; the SPA renders this verbatim and never recomputes a date (api.md §2). Public:
    `app/selectors.py` builds the same label for a comparison window, which resolves outside
    `resolve_period` and so has no `ResolvedPeriod` of its own to carry one."""
    start, end = window.from_, window.to
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {end:%b %Y}"
    if start.year == end.year:
        return f"{start:%-d %b}–{end:%-d %b %Y}"
    return f"{start:%-d %b %Y}–{end:%-d %b %Y}"


def _iso_week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _month_end(day: date) -> date:
    _, last = monthrange(day.year, day.month)
    return day.replace(day=last)


def _add_months(day: date, months: int) -> date:
    """The first of the month `months` away from `day`'s own month — callers that need the
    month's *end* ask `_month_end` of the result, never add a day count here."""
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)


def _fiscal_quarter_start(day: date, fiscal_year_start_month: int) -> date:
    """The first day of the fiscal quarter containing `day`. Quarters are three calendar months
    long, starting at `fiscal_year_start_month` and repeating (`docs/insights/metrics.md` §2):
    April default gives Apr-Jun, Jul-Sep, Oct-Dec, Jan-Mar."""
    offset = (day.month - fiscal_year_start_month) % 12
    quarter_start_month = fiscal_year_start_month + (offset // 3) * 3
    start_year = day.year
    if quarter_start_month > 12:
        quarter_start_month -= 12
    if quarter_start_month > day.month:
        start_year -= 1
    return date(start_year, quarter_start_month, 1)


def _clipped_elapsed_window(
    period_start: date, today: date, prev_start: date, prev_end: date
) -> Window:
    """The "same elapsed span" rule every to-date period's comparison shares
    (`docs/insights/metrics.md` §2): the same number of elapsed days, counted from `prev_start`,
    clipped so it never runs past the previous period's own end — "1-31 Mar -> 1-28 Feb", never
    "1-31 Mar -> 1 Feb-3 Mar"."""
    elapsed = (today - period_start).days
    candidate_end = prev_start + timedelta(days=elapsed)
    return Window(prev_start, min(candidate_end, prev_end))


def resolve_period(
    period: str,
    *,
    today: date,
    from_: date | None = None,
    to: date | None = None,
    fiscal_year_start_month: int = 4,
) -> ResolvedPeriod:
    """`docs/insights/metrics.md` §2's table, exactly. `today` is the organization-local date."""
    if period not in PERIOD_KEYS:
        raise InvalidPeriodError(f"unknown period {period!r}")

    if period == "today":
        window = Window(today, today)
    elif period == "this_week":
        window = Window(_iso_week_monday(today), today)
    elif period == "this_month":
        window = Window(_month_start(today), today)
    elif period == "this_quarter":
        window = Window(_fiscal_quarter_start(today, fiscal_year_start_month), today)
    elif period == "last_30_days":
        window = Window(today - timedelta(days=29), today)
    elif period == "last_month":
        last_month_end = _month_start(today) - timedelta(days=1)
        window = Window(_month_start(last_month_end), last_month_end)
    else:  # custom
        if from_ is None or to is None:
            raise InvalidPeriodError("period=custom requires both from and to")
        if from_ > to:
            raise InvalidPeriodError("from must not be after to")
        if to > today:
            raise InvalidPeriodError("to must not be in the future")
        if (to - from_).days + 1 > MAX_CUSTOM_SPAN_DAYS:
            raise InvalidPeriodError(f"a custom window spans at most {MAX_CUSTOM_SPAN_DAYS} days")
        window = Window(from_, to)

    return ResolvedPeriod(key=period, window=window, label=window_label(window))  # type: ignore[arg-type]


def _shift_year(day: date, years: int) -> date:
    """29 Feb becomes 28 Feb (`docs/insights/metrics.md` §2) — never raises on a leap-day
    source in a non-leap target year."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def _is_whole_calendar_months(window: Window) -> bool:
    return window.from_.day == 1 and window.to == _month_end(window.to)


def comparison_window(
    period: ResolvedPeriod, compare: str, *, fiscal_year_start_month: int = 4
) -> Window | None:
    """The comparison window for an already-resolved period, or `None` for `compare="none"`.

    `compare="same_period_last_year"` shifts both ends back a year, whatever the period. Every
    other period gets its own "previous" rule (`docs/insights/metrics.md` §2); the to-date
    periods (`today`, `this_week`, `this_month`, `this_quarter`) share the "same elapsed span,
    clipped" shape via `_clipped_elapsed_window`.
    """
    if compare not in COMPARE_KEYS:
        raise InvalidPeriodError(f"unknown compare {compare!r}")
    if compare == "none":
        return None

    window = period.window
    if compare == "same_period_last_year":
        return Window(_shift_year(window.from_, -1), _shift_year(window.to, -1))

    # compare == "previous_period"
    if period.key == "today":
        return Window(window.from_ - timedelta(days=1), window.from_ - timedelta(days=1))
    if period.key == "this_week":
        monday = window.from_
        prev_monday = monday - timedelta(days=7)
        return _clipped_elapsed_window(monday, window.to, prev_monday, monday - timedelta(days=1))
    if period.key == "this_month":
        month_start = window.from_
        prev_month_start = _add_months(month_start, -1)
        return _clipped_elapsed_window(
            month_start, window.to, prev_month_start, month_start - timedelta(days=1)
        )
    if period.key == "this_quarter":
        quarter_start = window.from_
        prev_quarter_start = _fiscal_quarter_start(
            quarter_start - timedelta(days=1), fiscal_year_start_month
        )
        return _clipped_elapsed_window(
            quarter_start, window.to, prev_quarter_start, quarter_start - timedelta(days=1)
        )
    if period.key == "last_30_days":
        return Window(window.from_ - timedelta(days=30), window.from_ - timedelta(days=1))
    if period.key == "last_month":
        prev_month_end = window.from_ - timedelta(days=1)
        return Window(_month_start(prev_month_end), prev_month_end)

    # custom
    if _is_whole_calendar_months(window):
        months_spanned = (
            (window.to.year - window.from_.year) * 12 + (window.to.month - window.from_.month) + 1
        )
        prev_to = window.from_ - timedelta(days=1)
        prev_from = _add_months(window.from_, -months_spanned)
        return Window(prev_from, prev_to)
    span = window.days
    return Window(window.from_ - timedelta(days=span), window.from_ - timedelta(days=1))


def delta_pct(current: float, previous: float) -> float | None:
    """`round((current - previous) / |previous| * 100, 1)`, half-even
    (`docs/insights/metrics.md` §1). `None` — "no baseline" — when `previous` is 0: there is no
    percentage a `+∞%` badge would honestly report."""
    if previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def delta_pp(current: float, previous: float) -> float:
    """A percentage metric's own delta: a plain difference, never a ratio, so a 0% baseline is
    not a "no baseline" case the way `delta_pct`'s is (`docs/insights/metrics.md` §1)."""
    return round(current - previous, 1)


#: `docs/insights/api.md` §4: the point ceiling per grain for `GET /series`.
SERIES_GRAIN_LIMITS: dict[str, int] = {"day": 92, "week": 104, "month": 36}


def validate_series_window(grain: str, from_: date, to: date, *, today: date) -> None:
    """`GET /series`'s own bounds (`docs/insights/api.md` §4): `from <= to <= today`, and no
    more than the grain's own point ceiling. Raises `InvalidPeriodError` — the route turns that
    into a 422 — on anything else. Bucket-snapping ("dates ... snapped outward to whole
    buckets") is a later slice's concern: I1 computes no series point at all, so there is
    nothing yet for a snapped boundary to change the answer of.
    """
    if grain not in SERIES_GRAIN_LIMITS:
        raise InvalidPeriodError(f"unknown grain {grain!r}")
    if from_ > to:
        raise InvalidPeriodError("from must not be after to")
    if to > today:
        raise InvalidPeriodError("to must not be in the future")
    if grain == "day":
        points = (to - from_).days + 1
    elif grain == "week":
        points = (to - from_).days // 7 + 1
    else:
        points = (to.year - from_.year) * 12 + (to.month - from_.month) + 1
    limit = SERIES_GRAIN_LIMITS[grain]
    if points > limit:
        raise InvalidPeriodError(f"a {grain} series spans at most {limit} points")


class InvalidCursorError(ValueError):
    """An opaque `/activity` cursor that does not decode to what `encode_cursor` produces —
    tampered, truncated, or simply stale across a contract change. Turned into a 422 at the API
    layer, never trusted past this."""


def encode_cursor(payload: dict[str, Any]) -> str:
    """An opaque, URL-safe keyset cursor (`docs/insights/api.md` §5: `occurred_at, kind, id`).
    Deliberately unpadded base64 — a trailing `=` is percent-encoded noise in a query string."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        decoded: Any = json.loads(raw)
    except Exception as exc:
        raise InvalidCursorError(f"malformed cursor: {exc}") from exc
    if not isinstance(decoded, dict):
        raise InvalidCursorError("cursor did not decode to an object")
    return decoded
