"""Pure domain logic for the support service — no I/O.

Two real algorithms live here, kept apart from `services.py` because they are rules, not
transactions, and so are exhaustively testable without a database:

* the ticket lifecycle state machine — which status transitions the desk allows; and
* the overview rollups — the KPIs and breakdowns the product's Overview shows, computed here so
  the server can answer them identically to the way the SPA derives them from the ticket list
  (`frontend/product-support/src/data/derive.ts`). Keeping them pure means the two never drift.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


class TicketStatus(enum.StrEnum):
    """The states a ticket moves through. `solved` is terminal for this slice; `on_hold` stops
    the SLA clock."""

    NEW = "new"
    OPEN = "open"
    PENDING = "pending"
    ON_HOLD = "on_hold"
    SOLVED = "solved"


#: The transitions the desk allows. A new ticket is picked up (`open`); an open ticket waits on
#: the customer (`pending`), is parked (`on_hold`) or is `solved`; a pending or held ticket
#: comes back to `open` or is solved; a solved ticket reopens to `open`. Reopening is deliberate
#: — a solved ticket the customer replies to should not need a brand-new record.
ALLOWED_TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.NEW: frozenset({TicketStatus.OPEN, TicketStatus.PENDING, TicketStatus.SOLVED}),
    TicketStatus.OPEN: frozenset({TicketStatus.PENDING, TicketStatus.ON_HOLD, TicketStatus.SOLVED}),
    TicketStatus.PENDING: frozenset({TicketStatus.OPEN, TicketStatus.ON_HOLD, TicketStatus.SOLVED}),
    TicketStatus.ON_HOLD: frozenset({TicketStatus.OPEN, TicketStatus.SOLVED}),
    TicketStatus.SOLVED: frozenset({TicketStatus.OPEN}),
}


class IllegalTransition(ValueError):
    """A transition the lifecycle does not permit. Carries the offending pair."""

    def __init__(self, current: TicketStatus, target: TicketStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"A ticket cannot move from {current.value!r} to {target.value!r}")


def can_transition(current: TicketStatus, target: TicketStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def assert_can_transition(current: TicketStatus, target: TicketStatus) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(current, target)


def is_active(status: str) -> bool:
    """A ticket is active until it is solved — everything still in the queue."""
    return status != TicketStatus.SOLVED.value


# --------------------------------------------------------------------- overview rollups
#
# A minimal read-model of one ticket, so the rollups depend on plain values rather than ORM
# rows and stay trivially testable.


@dataclass(frozen=True, slots=True)
class TicketFacts:
    status: str
    priority: str
    created_at: datetime
    updated_at: datetime
    first_response_at: datetime | None
    sla_breached: bool
    satisfaction: str | None


@dataclass(frozen=True, slots=True)
class Breakdown:
    key: str
    label: str
    count: int
    pct: int


@dataclass(frozen=True, slots=True)
class VolumePoint:
    label: str
    date: str
    opened: int
    solved: int


@dataclass(frozen=True, slots=True)
class Overview:
    open_tickets: int
    breaching_sla: int
    first_response_median_min: int
    csat_pct: int | None
    volume: list[VolumePoint]
    priority_breakdown: list[Breakdown]
    status_breakdown: list[Breakdown]


PRIORITY_ORDER = ("urgent", "high", "normal", "low")
PRIORITY_LABEL = {"urgent": "Urgent", "high": "High", "normal": "Normal", "low": "Low"}
STATUS_ORDER = ("new", "open", "pending", "on_hold", "solved")
STATUS_LABEL = {
    "new": "New",
    "open": "Open",
    "pending": "Pending",
    "on_hold": "On Hold",
    "solved": "Solved",
}
_WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _median(values: Sequence[float]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return round(ordered[mid])
    return round((ordered[mid - 1] + ordered[mid]) / 2)


def open_count(tickets: Sequence[TicketFacts]) -> int:
    return sum(1 for t in tickets if is_active(t.status))


def breaching_count(tickets: Sequence[TicketFacts]) -> int:
    """Active tickets whose SLA clock has already been missed."""
    return sum(1 for t in tickets if is_active(t.status) and t.sla_breached)


def first_response_median_min(tickets: Sequence[TicketFacts]) -> int:
    """Median minutes from creation to first agent reply, across tickets that have replied."""
    spans = [
        (t.first_response_at - t.created_at).total_seconds() / 60
        for t in tickets
        if t.first_response_at is not None
    ]
    return _median(spans)


def csat_pct(tickets: Sequence[TicketFacts]) -> int | None:
    """Share of rated tickets marked good, 0-100. None when nothing is rated yet."""
    rated = [t for t in tickets if t.satisfaction is not None]
    if not rated:
        return None
    good = sum(1 for t in rated if t.satisfaction == "good")
    return round(good / len(rated) * 100)


def _slices(
    order: Sequence[str], label: dict[str, str], counts: dict[str, int], total: int
) -> list[Breakdown]:
    out: list[Breakdown] = []
    for key in order:
        count = counts.get(key, 0)
        if count == 0:
            continue
        out.append(
            Breakdown(key, label[key], count, 0 if total == 0 else round(count / total * 100))
        )
    return out


def priority_breakdown(tickets: Sequence[TicketFacts]) -> list[Breakdown]:
    active = [t for t in tickets if is_active(t.status)]
    counts: dict[str, int] = {}
    for t in active:
        counts[t.priority] = counts.get(t.priority, 0) + 1
    return _slices(PRIORITY_ORDER, PRIORITY_LABEL, counts, len(active))


def status_breakdown(tickets: Sequence[TicketFacts]) -> list[Breakdown]:
    counts: dict[str, int] = {}
    for t in tickets:
        counts[t.status] = counts.get(t.status, 0) + 1
    return _slices(STATUS_ORDER, STATUS_LABEL, counts, len(tickets))


def volume_series(
    tickets: Sequence[TicketFacts], now: datetime, days: int = 7
) -> list[VolumePoint]:
    """Opened- and solved-per-day over the trailing `days`, oldest first.

    A ticket counts as opened on its `created_at` date and, when solved, as solved on its
    `updated_at` date — the same shaping the SPA's chart uses.
    """
    today = now.astimezone(UTC).date()
    start = today - timedelta(days=days - 1)
    opened: dict[str, int] = {}
    solved: dict[str, int] = {}
    for t in tickets:
        c = t.created_at.astimezone(UTC).date()
        if start <= c <= today:
            opened[c.isoformat()] = opened.get(c.isoformat(), 0) + 1
        if t.status == TicketStatus.SOLVED.value:
            s = t.updated_at.astimezone(UTC).date()
            if start <= s <= today:
                solved[s.isoformat()] = solved.get(s.isoformat(), 0) + 1
    points: list[VolumePoint] = []
    for i in range(days):
        d = start + timedelta(days=i)
        iso = d.isoformat()
        points.append(
            VolumePoint(_WEEKDAY[d.weekday()], iso, opened.get(iso, 0), solved.get(iso, 0))
        )
    return points


def build_overview(tickets: Sequence[TicketFacts], now: datetime) -> Overview:
    return Overview(
        open_tickets=open_count(tickets),
        breaching_sla=breaching_count(tickets),
        first_response_median_min=first_response_median_min(tickets),
        csat_pct=csat_pct(tickets),
        volume=volume_series(tickets, now),
        priority_breakdown=priority_breakdown(tickets),
        status_breakdown=status_breakdown(tickets),
    )
