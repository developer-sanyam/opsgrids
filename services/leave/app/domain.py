"""Pure functions, no I/O — the leave-day arithmetic and the one place this service resolves
"today" in something other than UTC.

**Why a timezone function lives here at all.** The rebuild's one stated defect to fix is a
server computing "today" from `datetime.now(UTC).date()` — correct only for a caller in UTC, and
silently wrong by a day for everyone else near midnight (see `app/models.py`'s module docstring,
and `attendance.services.check_in`'s `work_date = datetime.now(UTC).date()` for where it still
lives). `today_in` is the one function in this service allowed to answer "what day is it", and
it always takes the timezone as an explicit argument rather than reaching for `UTC` by default —
so a reviewer can see, from the call site alone, that the resolved date is at least
timezone-aware rather than merely being *some* date.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Saturday and Sunday, Python's `date.weekday()` numbering (Monday = 0). No holiday calendar
#: exists yet in this slice — see the module docstring on `services.py`'s
#: `apply_for_leave` for the documented scope of that gap.
WEEKEND_DAYS = frozenset({5, 6})


class DomainError(Exception):
    """Base for the pure, no-I/O errors this module raises. Callers in `services.py` translate
    these to the platform's typed HTTP problems — this module raises nothing HTTP-shaped."""


class InvalidRange(DomainError):
    """The date range or its half-day flags are not a request that can exist."""


class InvalidTimezone(DomainError):
    """`LeaveSettings.timezone` (or a caller-supplied one) is not a real IANA zone."""


def zone(timezone: str) -> ZoneInfo:
    """Resolve an IANA name, refusing an unknown one loudly rather than silently falling back to
    UTC — the same discipline `storefront.domain._zone` uses for the identical reason: a bad
    timezone on a settings row is a configuration error, and answering in UTC anyway would look
    like a well-formed, simply wrong, answer."""
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezone(f"{timezone!r} is not a known IANA timezone") from exc


def today_in(timezone: str, *, now: dt.datetime) -> dt.date:
    """The organization's own calendar day. `now` is UTC and comes from the caller (never this
    module's own clock), so a test can pin it and prove the boundary around midnight — the
    scenario the naive `datetime.now(UTC).date()` this rebuild is fixing gets wrong."""
    return now.astimezone(zone(timezone)).date()


def count_half_days(
    start_date: dt.date,
    end_date: dt.date,
    *,
    half_day_start: bool,
    half_day_end: bool,
    weekend_days: frozenset[int] = WEEKEND_DAYS,
) -> int:
    """How many half-days a date range is worth, in the employee's own calendar — weekends
    excluded, no holiday calendar in this slice (documented gap; see `services.py`).

    Half-day flags apply only to the edges of a multi-day range; a single-day request is a half
    day only if both flags agree, because there is only one day for them to describe (also
    enforced at the database — `ck_leave_requests_single_day_half_flags_agree`). A day that
    falls on a weekend contributes nothing, whole or half, even if it is the range's first or
    last day — the half-day flag on a weekend edge is simply moot, never an error, since a range
    that starts on a Saturday and ends on a working day is a legitimate (if unusual) request.

    Raises `InvalidRange` for an end before a start, a single-day range whose two half-day flags
    disagree, or a range with no working days in it at all (a request for a plain weekend).
    """
    if end_date < start_date:
        raise InvalidRange("end_date must not be before start_date")
    if start_date == end_date and half_day_start != half_day_end:
        raise InvalidRange(
            "a single-day request's half_day_start and half_day_end must agree — there is only "
            "one day for them to describe"
        )

    total = 0
    current = start_date
    one_day = dt.timedelta(days=1)
    while current <= end_date:
        if current.weekday() not in weekend_days:
            if (current == start_date and half_day_start) or (current == end_date and half_day_end):
                total += 1
            else:
                total += 2
        current += one_day

    if total == 0:
        raise InvalidRange("this date range has no working days in it")
    return total
