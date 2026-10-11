"""Pure functions for the messaging-analytics slice: the cost arithmetic and the date window.

No I/O. Money here is **integer minor units** end to end; there is no float anywhere in this
module, and a test asserts the rounding on the exact half-way cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

#: The four categories a WhatsApp message is billed under, in the order every response lists
#: them. `service` is a free-text reply inside the 24-hour window, which Meta does not charge for.
CATEGORIES: Final[tuple[str, ...]] = ("marketing", "utility", "authentication", "service")

#: Longest range one request may ask for. One grouped row per day comes back, so this bounds both
#: the query and the response.
MAX_RANGE_DAYS: Final = 366

#: The default range when a caller sends neither bound: the last 30 UTC days including today.
DEFAULT_RANGE_DAYS: Final = 30


class RangeError(ValueError):
    """The requested window is not one this endpoint will compute. `code` is machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DateWindow:
    """An inclusive range of UTC calendar days.

    Every bucket in this slice is a **UTC** day. A message sent at 23:30 on the 25th in
    Asia/Kolkata (18:00 UTC) is on the 25th; one sent at 00:10 IST on the 26th (18:40 UTC on the
    25th) is *also* on the 25th. That is deliberate and documented rather than fixed with a
    per-request timezone: every stored timestamp is UTC, and a dashboard that re-buckets by the
    viewer's zone would show a different total for the same range depending on who asked.
    """

    date_from: date
    date_to: date

    @property
    def start(self) -> datetime:
        """The first instant inside the window (inclusive), midnight UTC of `date_from`."""
        return datetime(self.date_from.year, self.date_from.month, self.date_from.day, tzinfo=UTC)

    @property
    def end(self) -> datetime:
        """The first instant *after* the window (exclusive), midnight UTC of the day after."""
        nxt = self.date_to + timedelta(days=1)
        return datetime(nxt.year, nxt.month, nxt.day, tzinfo=UTC)

    def days(self) -> list[date]:
        """Every day in the range, oldest first — what a dense series is built from."""
        span = (self.date_to - self.date_from).days + 1
        return [self.date_from + timedelta(days=i) for i in range(span)]


def resolve_window(
    date_from: date | None, date_to: date | None, *, today: date | None = None
) -> DateWindow:
    """Fill defaults and refuse a window this endpoint will not compute.

    Both bounds are inclusive UTC days. Omitting both gives the last 30 days ending today;
    omitting only `to` ends today, and omitting only `from` starts `DEFAULT_RANGE_DAYS - 1` days
    before `to`. `from` after `to`, or a span over `MAX_RANGE_DAYS`, is refused.
    """
    today = today or datetime.now(UTC).date()
    end = date_to or today
    try:
        start = date_from or (end - timedelta(days=DEFAULT_RANGE_DAYS - 1))
        # `DateWindow.end` is the day after `end`; a date at the calendar's edge has none.
        _day_after_end = end + timedelta(days=1)
    except OverflowError as exc:
        raise RangeError("range_out_of_bounds", "A date is outside the supported range.") from exc
    if start > end:
        raise RangeError("range_inverted", "`from` must not be after `to`.")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise RangeError(
            "range_too_long", f"The range may not be longer than {MAX_RANGE_DAYS} days."
        )
    return DateWindow(start, end)


def estimate_minor(messages: int, rate_per_1000_minor: int) -> int:
    """`messages x rate / 1000` in integer minor units, rounded **half up**.

    Half-up on the exact integer quotient, not `round()` (banker's rounding) and not float
    division: 1 message at 500 per 1000 is exactly 0.5 minor units and must be 1, and 3 at 500 is
    1.5 and must be 2. Rounded once per category by the caller, never per message.
    """
    if messages < 0 or rate_per_1000_minor < 0:
        raise ValueError("messages and rate must not be negative")
    return (messages * rate_per_1000_minor + 500) // 1000


#: Meta's per-message `pricing.category` values that are priced as one of the four categories
#: above under another name. Anything else Meta reports (`service`, `referral_conversion`, a
#: category added later) is a free message, priced as `service`.
META_CATEGORY_ALIASES: Final[dict[str, str]] = {
    "marketing": "marketing",
    "marketing_lite": "marketing",
    "utility": "utility",
    "authentication": "authentication",
    # shortcut: priced at the domestic authentication rate; Meta's authentication-international
    # rate is higher in some markets. Add a fifth category if a customer sends OTPs abroad.
    "authentication_international": "authentication",
}


def billing_category(meta_category: str) -> str:
    """The rate-card category a message Meta billed as `meta_category` is priced under."""
    return META_CATEGORY_ALIASES.get(meta_category, "service")


def market_code(number: str, codes: set[str] | frozenset[str]) -> str:
    """The longest calling code in `codes` that prefixes `number`'s digits, else `''` (Other).

    Calling codes are at most three digits, but a market can be narrower than its code (+77
    Kazakhstan inside +7 Russia), so the longest *listed* prefix wins, up to four digits.
    """
    digits = "".join(ch for ch in number if ch.isdigit())
    for length in range(min(4, len(digits)), 0, -1):
        if digits[:length] in codes:
            return digits[:length]
    return ""
