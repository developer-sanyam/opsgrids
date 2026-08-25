"""Request and response contracts for the project service.

This module is the published API surface: a change here is a change to the OpenAPI contract,
and the PulseBoard SPA wires its client against it. The field names, nesting and enums mirror
`frontend/product-pulseboard/src/api/types.ts` exactly, so flipping the SPA from fixtures to
live is a base-path change rather than a rewrite.

Identity on the wire follows the SPA's joins: a project's `id` and a task's `project_id` are
the same opaque UUID (so the board's task→project join holds), while `owner_id` / `assignee_id`
carry a person's stable `ref` (`p01`) — the id the SPA's roster is keyed by. Money
(`budget_minor` / `spent_minor`) is integer minor units in `currency` (ISO-4217), never a float.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# ------------------------------------------------------------------ vocabularies (match the SPA)

ProjectStatus = Literal["on_track", "at_risk", "blocked", "done"]
TaskStatus = Literal["backlog", "in_progress", "review", "done"]
Priority = Literal["low", "medium", "high"]
AttentionSeverity = Literal["at_risk", "blocked", "over_budget", "overdue"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# ------------------------------------------------------------------ people


class PersonOut(BaseModel):
    """Mirrors the SPA's `Person`. `id` is the person's stable `ref` (`p01`) — the value every
    `owner_id` / `assignee_id` / timesheet `person_id` on the wire joins against."""

    id: str
    name: str
    role: str
    location: str
    capacity_hours: int


# ------------------------------------------------------------------ projects


class ProjectOut(BaseModel):
    """Mirrors the SPA's `Project`. `owner_id` is a person `ref`; `open_tasks` is the stored
    open-work headline; money is minor units in `currency`."""

    id: str
    code: str
    name: str
    client: str
    status: ProjectStatus
    progress_pct: int
    budget_minor: int
    spent_minor: int
    currency: str
    owner_id: str | None
    start_date: date | None
    due_date: date | None
    billable: bool
    open_tasks: int


class ProjectIn(BaseModel):
    """Create an engagement. `owner_ref` names an existing person by their `ref` (`p01`)."""

    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=300)
    client: str = Field(default="", max_length=200)
    status: ProjectStatus = "on_track"
    progress_pct: int = Field(default=0, ge=0, le=100)
    budget_minor: int = Field(default=0, ge=0)
    spent_minor: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    owner_ref: str | None = Field(default=None, max_length=40)
    start_date: date | None = None
    due_date: date | None = None
    billable: bool = True
    open_tasks: int = Field(default=0, ge=0)


# ------------------------------------------------------------------ tasks


class TaskOut(BaseModel):
    """Mirrors the SPA's `Task` — one card on the board. `status` is the board stage;
    `project_id` is the owning engagement's opaque id; `assignee_id` is a person `ref`."""

    id: str
    key: str
    title: str
    status: TaskStatus
    assignee_id: str | None
    project_id: str
    estimate_hours: int
    priority: Priority
    tags: list[str]
    due_date: date | None


class TaskIn(BaseModel):
    """Create a board card on an engagement (named by its `code`)."""

    key: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    project_code: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=300)
    status: TaskStatus = "backlog"
    assignee_ref: str | None = Field(default=None, max_length=40)
    estimate_hours: int = Field(default=0, ge=0)
    priority: Priority = "medium"
    tags: list[str] = Field(default_factory=list)
    due_date: date | None = None


# ------------------------------------------------------------------ overview rollups


class HealthBucket(BaseModel):
    """One bucket of the project-health breakdown. Mirrors the SPA's `HealthBucket` —
    `pct` is the share of the *active* portfolio, 0-100."""

    status: Literal["on_track", "at_risk", "blocked"]
    label: str
    count: int
    pct: int


class AttentionItem(BaseModel):
    """An engagement on the "needs attention" list, with the reason it surfaced. Mirrors the
    SPA's `AttentionItem`; ordered worst first."""

    project_id: str
    code: str
    name: str
    client: str
    reason: str
    severity: AttentionSeverity


class ProjectSummaryOut(BaseModel):
    """The Overview's engagement-side rollup — the same arithmetic the SPA's `derive.ts` runs
    over the project and task lists, exposed once so a reporting view can share it. The
    time-side numbers (utilization, billable hours) live on the timesheet service's summary.

    `tasks_due_this_week` counts open board cards due in the week containing `today`.
    """

    active_projects: int
    tasks_due_this_week: int
    health: list[HealthBucket]
    needs_attention: list[AttentionItem]
