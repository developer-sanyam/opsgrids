"""Pure functions for metering — no I/O.

The one real piece of domain logic here is the billing period. "Current-period usage" needs a
definition, and until a subscription's own period is wired in (a later phase), metering cuts on
the **calendar month in UTC**. It is a deterministic, timezone-stable default that a test can
assert against and that lines up with the monthly `billing_interval` the seeded catalogue uses.
Keeping it here, pure and named, means the day a subscription supplies its real
`current_period_start/end` the change is one call site, not scattered date arithmetic.
"""

from __future__ import annotations

from datetime import UTC, datetime


def current_period(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The half-open `[start, end)` of the calendar month containing `now`, in UTC.

    `start` is midnight on the first of the month; `end` is midnight on the first of the next
    month. Half-open so an event at exactly `end` belongs to the next period, never both.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end
