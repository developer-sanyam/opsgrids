"""Pure rollups and vocabulary for ITSM. No I/O, no session — only model instances in,
plain values out, so this is trivially testable and mirrors the SPA's `data/derive.ts`.

The Overview's KPIs and breakdowns all derive from the same incident / request / asset rows the
tables render, exactly as the SPA derives them, so the numbers agree whichever side computes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from app.models import Asset, Incident, ServiceRequest

# ------------------------------------------------------------------ vocabulary (labels match SPA)

PRIORITY_ORDER: tuple[str, ...] = ("P1", "P2", "P3", "P4")
PRIORITY_RANK: dict[str, int] = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
PRIORITY_LABELS: dict[str, str] = {
    "P1": "P1 · Critical",
    "P2": "P2 · High",
    "P3": "P3 · Moderate",
    "P4": "P4 · Low",
}

CATEGORY_ORDER: tuple[str, ...] = (
    "network",
    "hardware",
    "software",
    "access",
    "email",
    "security",
)
CATEGORY_LABELS: dict[str, str] = {
    "network": "Network",
    "hardware": "Hardware",
    "software": "Software",
    "access": "Access",
    "email": "Email",
    "security": "Security",
}

#: Statuses that count as "still on someone's plate".
OPEN_STATUSES: frozenset[str] = frozenset({"new", "in_progress", "on_hold"})
RESOLVED_STATUSES: frozenset[str] = frozenset({"resolved", "closed"})

#: The incident lifecycle: `new` can start work, go on hold or resolve directly; `in_progress`
#: and `on_hold` can each resolve or swap into the other; `resolved` either closes or reopens
#: back to `in_progress`; `closed` is terminal — a corrected ticket is a new one, not a reopened
#: old one. A no-op "move" to the current status is not in its own frozenset, so it is a 409 too.
INCIDENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"in_progress", "on_hold", "resolved"}),
    "in_progress": frozenset({"on_hold", "resolved"}),
    "on_hold": frozenset({"in_progress", "resolved"}),
    "resolved": frozenset({"closed", "in_progress"}),
    "closed": frozenset(),
}


def can_transition_incident(current: str, new: str) -> bool:
    """Whether an incident may move `current` -> `new`. Every other move answers 409."""
    return new in INCIDENT_TRANSITIONS.get(current, frozenset())


def is_open(incident: Incident) -> bool:
    """An incident still on someone's plate — anything not yet resolved or closed."""
    return incident.status not in RESOLVED_STATUSES


def open_incident_count(incidents: Sequence[Incident]) -> int:
    return sum(1 for i in incidents if is_open(i))


def sla_compliance_pct(incidents: Sequence[Incident]) -> float:
    """Share of incidents whose clock has not been breached, 0-100 (one decimal). A breach is
    the only failing outcome; met / on_track / at_risk all still count as compliant."""
    if not incidents:
        return 100.0
    breached = sum(1 for i in incidents if i.sla_state == "breached")
    return round((len(incidents) - breached) / len(incidents) * 1000) / 10


def requests_awaiting_approval(requests: Sequence[ServiceRequest]) -> int:
    return sum(1 for r in requests if r.status == "approval")


def assets_under_management(assets: Sequence[Asset]) -> int:
    """Assets the org actively manages — everything not retired."""
    return sum(1 for a in assets if a.status != "retired")


def _pct(part: int, whole: int) -> int:
    return 0 if whole == 0 else round(part / whole * 100)


def breakdown_by_priority(incidents: Sequence[Incident]) -> list[dict[str, object]]:
    """Open-incident count grouped by priority, in P1→P4 order."""
    open_incs = [i for i in incidents if is_open(i)]
    total = len(open_incs)
    out: list[dict[str, object]] = []
    for p in PRIORITY_ORDER:
        count = sum(1 for i in open_incs if i.priority == p)
        if count:
            out.append(
                {"key": p, "label": PRIORITY_LABELS[p], "count": count, "pct": _pct(count, total)}
            )
    return out


def breakdown_by_category(incidents: Sequence[Incident]) -> list[dict[str, object]]:
    """Open-incident count grouped by category, largest first."""
    open_incs = [i for i in incidents if is_open(i)]
    total = len(open_incs)
    rows = [
        {"key": c, "label": CATEGORY_LABELS[c], "count": count, "pct": _pct(count, total)}
        for c in CATEGORY_ORDER
        if (count := sum(1 for i in open_incs if i.category == c))
    ]
    rows.sort(key=lambda r: r["count"], reverse=True)  # type: ignore[arg-type,return-value]
    return rows


def volume_series(
    incidents: Sequence[Incident], *, weeks: int = 6, now: datetime | None = None
) -> list[dict[str, object]]:
    """Weekly opened/resolved counts over the trailing `weeks` weeks.

    `opened` counts incidents by `created_at`, `resolved` counts those now resolved/closed by
    `updated_at` (the resolution time in this model). Representative rather than authoritative
    until a reporting service ships — the SPA marks it "Sample data" when live.
    """
    now = now or datetime.now(UTC)
    # Anchor to the start of the current week (Monday) so buckets are stable.
    start_of_week = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    buckets: list[dict[str, object]] = []
    for w in range(weeks - 1, -1, -1):
        lo = start_of_week - timedelta(weeks=w)
        hi = lo + timedelta(weeks=1)
        opened = sum(1 for i in incidents if lo <= _aware(i.created_at) < hi)
        resolved = sum(
            1
            for i in incidents
            if i.status in RESOLVED_STATUSES and lo <= _aware(i.updated_at) < hi
        )
        buckets.append({"label": lo.strftime("%d %b"), "opened": opened, "resolved": resolved})
    return buckets


def _aware(dt: datetime) -> datetime:
    """Treat a naive timestamp as UTC so comparisons never raise."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
