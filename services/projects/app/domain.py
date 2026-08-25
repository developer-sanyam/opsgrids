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


# ------------------------------------------------------------------ burndown (sample series)

#: Release / sprint burndown of remaining scope (story points), mirroring the SPA's `BURNDOWN`.
#: The ideal line falls straight to zero; the actual line trails it. The last actual point is the
#: live "remaining" figure and later ideal points have no actual yet (None). Representative until
#: a reporting service ships — the SPA marks it "Sample data" when live.
BURNDOWN_SPRINT: list[dict[str, object]] = [
    {"label": "Mon", "ideal": 96, "actual": 96},
    {"label": "Tue", "ideal": 80, "actual": 88},
    {"label": "Wed", "ideal": 64, "actual": 71},
    {"label": "Thu", "ideal": 48, "actual": 58},
    {"label": "Fri", "ideal": 32, "actual": 44},
    {"label": "Mon", "ideal": 16, "actual": 27},
    {"label": "Tue", "ideal": 0, "actual": None},
]
BURNDOWN_RELEASE: list[dict[str, object]] = [
    {"label": "W1", "ideal": 420, "actual": 420},
    {"label": "W2", "ideal": 372, "actual": 388},
    {"label": "W3", "ideal": 324, "actual": 349},
    {"label": "W4", "ideal": 276, "actual": 300},
    {"label": "W5", "ideal": 228, "actual": 247},
    {"label": "W6", "ideal": 180, "actual": 205},
    {"label": "W7", "ideal": 132, "actual": 158},
    {"label": "W8", "ideal": 84, "actual": 118},
    {"label": "W9", "ideal": 36, "actual": None},
    {"label": "W10", "ideal": 0, "actual": None},
]
BURNDOWN_DELTA_SPRINT: float = -14.2
BURNDOWN_DELTA_RELEASE: float = -9.6

#: A representative resource-utilization figure for the KPI row (no capacity service yet).
RESOURCE_UTILIZATION_PCT: int = 87
