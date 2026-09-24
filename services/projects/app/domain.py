"""Pure rollups and vocabulary for the projects product. No I/O, no session — only model
instances in, plain values out, so this is trivially testable and mirrors the SPA's
`data/derive.ts` so the numbers agree whichever side computes them.

The Overview's KPIs and the health breakdown all derive from the same project / milestone rows
the portfolio and milestone screens render, exactly as the SPA derives them.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.models import Milestone, Project

# ------------------------------------------------------------------ vocabulary (labels match SPA)

PHASE_ORDER: tuple[str, ...] = ("discovery", "design", "build", "stabilize", "launch", "live")
PHASE_LABELS: dict[str, str] = {
    "discovery": "Discovery",
    "design": "Design",
    "build": "Build",
    "stabilize": "Stabilize",
    "launch": "Launch",
    "live": "Live",
}

HEALTH_ORDER: tuple[str, ...] = ("on_track", "at_risk", "delayed")
HEALTH_LABELS: dict[str, str] = {
    "on_track": "On track",
    "at_risk": "At risk",
    "delayed": "Delayed",
}

STAGE_ORDER: tuple[str, ...] = ("backlog", "in_progress", "review", "done")
STAGE_LABELS: dict[str, str] = {
    "backlog": "Backlog",
    "in_progress": "In Progress",
    "review": "Review",
    "done": "Done",
}

MILESTONE_STATUS_LABELS: dict[str, str] = {
    "completed": "Completed",
    "on_track": "On track",
    "due_soon": "Due soon",
    "at_risk": "At risk",
    "slipping": "Slipping",
}

#: Milestone statuses that count as "on time" for the on-time rate (mirrors derive.ts `ON_TIME`).
ON_TIME_STATUSES: frozenset[str] = frozenset({"completed", "on_track", "due_soon"})


# ------------------------------------------------------------------ project rollups


def active_projects(projects: Sequence[Project]) -> int:
    """A project is active while still being delivered — anything short of the run phase."""
    return sum(1 for p in projects if p.phase != "live")


def health_breakdown(projects: Sequence[Project]) -> list[dict[str, object]]:
    """The delivery-health breakdown, in a stable order, each health's share of the portfolio."""
    total = len(projects)
    out: list[dict[str, object]] = []
    for key in HEALTH_ORDER:
        count = sum(1 for p in projects if p.health == key)
        out.append(
            {
                "key": key,
                "label": HEALTH_LABELS[key],
                "count": count,
                "pct": 0 if total == 0 else round(count / total * 100),
            }
        )
    return out


def average_progress(projects: Sequence[Project]) -> int:
    """Mean delivery progress across the given projects, rounded to a whole percent."""
    if not projects:
        return 0
    return round(sum(p.progress_pct for p in projects) / len(projects))


def total_budget_minor(projects: Sequence[Project]) -> int:
    return sum(p.budget_minor for p in projects)


def total_spent_minor(projects: Sequence[Project]) -> int:
    return sum(p.spent_minor for p in projects)


# ------------------------------------------------------------------ milestone rollups


def on_time_fraction(milestones: Sequence[Milestone]) -> float:
    """Fraction (0-1) of milestones that are on time — completed or trending to hit their date."""
    if not milestones:
        return 0.0
    on_time = sum(1 for m in milestones if m.status in ON_TIME_STATUSES)
    return on_time / len(milestones)


def milestones_due(milestones: Sequence[Milestone], now: date, within_days: int = 14) -> int:
    """Unfinished milestones due within the next `within_days` (overdue-and-unfinished included).
    Completed milestones never count. Mirrors derive.ts `milestonesDue`."""
    count = 0
    for m in milestones:
        if m.status == "completed" or m.due_date is None:
            continue
        if (m.due_date - now).days <= within_days:
            count += 1
    return count


# ------------------------------------------------------------------ burndown and utilization
#
# Neither is derivable from data this service owns. A burndown needs a dated history of
# remaining scope — a task carries only its current `stage`, never a per-day snapshot of points
# remaining — and utilization needs a capacity source — a member carries no weekly-hours figure.
# Both were previously served as fixed constants with no wire flag distinguishing them from a
# real figure (dashboard-data audit §3.9); `app/api/routes.py`'s `overview` now serves `None` for
# all four fields instead. This is the seam a delivery-snapshot projection and a capacity model
# replace, not a computation this module can fake honestly.
