"""Pure rollups and vocabulary for PulseBoard's engagements and board. No I/O, no session —
only model instances in, plain values out, so this is trivially testable and mirrors the SPA's
`data/derive.ts` so the numbers agree whichever side computes them.

The Overview's KPIs, the health breakdown and the needs-attention list all derive from the same
project / task rows the Projects and Board screens render, exactly as the SPA derives them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta

from app.models import Project, Task

# ------------------------------------------------------------------ vocabulary (labels match SPA)

STATUS_ORDER: tuple[str, ...] = ("on_track", "at_risk", "blocked", "done")
STATUS_LABELS: dict[str, str] = {
    "on_track": "On track",
    "at_risk": "At risk",
    "blocked": "Blocked",
    "done": "Done",
}

#: The three *live* states the health breakdown covers — a delivered project never surfaces.
HEALTH_ORDER: tuple[str, ...] = ("on_track", "at_risk", "blocked")

STAGE_ORDER: tuple[str, ...] = ("backlog", "in_progress", "review", "done")
STAGE_LABELS: dict[str, str] = {
    "backlog": "Backlog",
    "in_progress": "In progress",
    "review": "Review",
    "done": "Done",
}

PRIORITY_ORDER: tuple[str, ...] = ("low", "medium", "high")

#: Worst first, mirroring derive.ts `SEVERITY_RANK`: blocked > over budget > overdue > at risk.
SEVERITY_RANK: dict[str, int] = {"blocked": 0, "over_budget": 1, "overdue": 2, "at_risk": 3}


# ------------------------------------------------------------------ week arithmetic


def week_dates(today: date) -> list[date]:
    """The seven dates of the week containing `today`, Monday first — the SPA's `WEEK_DATES`."""
    monday = today - timedelta(days=today.weekday())
    return [monday + timedelta(days=i) for i in range(7)]


# ------------------------------------------------------------------ project rollups


def active_projects(projects: Sequence[Project]) -> int:
    """A project is active while still running — anything but delivered (derive.ts)."""
    return sum(1 for p in projects if p.status != "done")


def health_breakdown(projects: Sequence[Project]) -> list[dict[str, object]]:
    """The project-health breakdown across the three live states, in a stable order.

    Percentages are of the **active** portfolio, so a delivered project does not dilute the
    picture — exactly `derive.ts projectHealth`.
    """
    active = active_projects(projects)
    out: list[dict[str, object]] = []
    for key in HEALTH_ORDER:
        count = sum(1 for p in projects if p.status == key)
        out.append(
            {
                "status": key,
                "label": STATUS_LABELS[key],
                "count": count,
                "pct": 0 if active == 0 else round(count / active * 100),
            }
        )
    return out


def tasks_due_in(tasks: Sequence[Task], days: Iterable[date]) -> int:
    """Open tasks (not done) whose due date falls on one of `days` (derive.ts)."""
    window = set(days)
    return sum(
        1 for t in tasks if t.stage != "done" and t.due_date is not None and t.due_date in window
    )


def needs_attention(projects: Sequence[Project], today: date) -> list[dict[str, object]]:
    """Engagements that need a second look, worst first — `derive.ts needsAttention`.

    A project can trip several rules; the most serious one wins and sets the reason:
    blocked > over budget > overdue > at risk. Delivered projects never surface.
    """
    items: list[dict[str, object]] = []
    for p in projects:
        if p.status == "done":
            continue

        severity: str | None = None
        reason = ""
        if p.status == "blocked":
            severity = "blocked"
            reason = "Blocked — work has stalled and needs unblocking"
        elif p.spent_minor > p.budget_minor:
            severity = "over_budget"
            over = round((p.spent_minor - p.budget_minor) / p.budget_minor * 100)
            reason = f"Over budget by {over}% — spend has passed the approved budget"
        elif p.due_date is not None and p.due_date < today:
            severity = "overdue"
            reason = "Past its due date and still open"
        elif p.status == "at_risk":
            severity = "at_risk"
            reason = "Flagged at risk — timeline or scope is under pressure"

        if severity is not None:
            items.append(
                {
                    "project_id": str(p.id),
                    "code": p.code,
                    "name": p.name,
                    "client": p.client,
                    "reason": reason,
                    "severity": severity,
                }
            )
    items.sort(key=lambda item: SEVERITY_RANK[str(item["severity"])])
    return items
