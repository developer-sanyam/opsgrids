"""Request and response contracts for the projects service.

This module is the published API surface: a change here is a change to the OpenAPI contract,
and the Projects SPA generates its client from it. The field names, nesting and enums mirror
`frontend/product-projects/src/api/types.ts` exactly, so wiring the SPA from fixtures to live is
a flag flip rather than a rewrite.

The human identifiers the SPA speaks in are the API's identity: a project's `id` is its opaque
UUID but it is also addressed by `code` (`PRJ-104`), and an embedded `Person`'s `id` is a
member's `ref` (`p-01`). Money (`budget_minor` / `spent_minor`) is integer minor units in
`currency` (ISO-4217), never a float.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# ------------------------------------------------------------------ vocabularies (match the SPA)

ProjectPhase = Literal["discovery", "design", "build", "stabilize", "launch", "live"]
ProjectHealth = Literal["on_track", "at_risk", "delayed"]
MilestoneStatus = Literal["completed", "on_track", "due_soon", "at_risk", "slipping"]
TaskStage = Literal["backlog", "in_progress", "review", "done"]
TaskPriority = Literal["low", "medium", "high", "urgent"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# ------------------------------------------------------------------ people


class Person(BaseModel):
    """A member of the delivery org — a project owner or a task assignee. Mirrors the SPA's
    `Person`; `id` is the member's stable `ref` (`p-01`)."""

    id: str
    name: str
    role: str


# ------------------------------------------------------------------ projects


class ProjectOut(BaseModel):
    """Mirrors the SPA's `ProjectOut`. `owner` is the embedded delivery lead; `milestone_count`
    is derived from the project's milestones; money is minor units in `currency`."""

    id: str
    code: str
    name: str
    client: str
    owner: Person
    phase: ProjectPhase
    health: ProjectHealth
    progress_pct: int
    start_date: date
    target_date: date
    budget_minor: int
    spent_minor: int
    currency: str
    team_size: int
    open_tasks: int
    milestone_count: int
    summary: str


class ProjectIn(BaseModel):
    """Create a project. `owner_ref` names an existing member by their `ref` (`p-01`)."""

    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=300)
    client: str = Field(default="", max_length=200)
    owner_ref: str | None = Field(default=None, max_length=40)
    phase: ProjectPhase = "discovery"
    health: ProjectHealth = "on_track"
    progress_pct: int = Field(default=0, ge=0, le=100)
    start_date: date
    target_date: date
    budget_minor: int = Field(default=0, ge=0)
    spent_minor: int = Field(default=0, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    team_size: int = Field(default=0, ge=0)
    open_tasks: int = Field(default=0, ge=0)
    summary: str = Field(default="", max_length=2000)


# ------------------------------------------------------------------ milestones


class MilestoneOut(BaseModel):
    """Mirrors the SPA's `MilestoneOut`. `project_id` is the owning project's opaque id;
    `project_code` / `project_name` are denormalised for the list view."""

    id: str
    project_id: str
    project_code: str
    project_name: str
    title: str
    owner: Person
    due_date: date
    status: MilestoneStatus
    progress_pct: int


class MilestoneIn(BaseModel):
    """Create a milestone on a project (named by its `code`)."""

    project_code: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=300)
    owner_ref: str | None = Field(default=None, max_length=40)
    due_date: date
    status: MilestoneStatus = "on_track"
    progress_pct: int = Field(default=0, ge=0, le=100)


# ------------------------------------------------------------------ tasks


class TaskOut(BaseModel):
    """Mirrors the SPA's `TaskOut` — one card on the workflow board."""

    id: str
    project_id: str
    project_code: str
    title: str
    assignee: Person
    stage: TaskStage
    priority: TaskPriority
    due_date: date | None
    points: int


class TaskIn(BaseModel):
    """Create a board card on a project (named by its `code`)."""

    project_code: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=300)
    assignee_ref: str | None = Field(default=None, max_length=40)
    stage: TaskStage = "backlog"
    priority: TaskPriority = "medium"
    due_date: date | None = None
    points: int = Field(default=0, ge=0)


# ------------------------------------------------------------------ board


class BoardColumn(BaseModel):
    """One column of the workflow board — a stage and its ordered cards."""

    stage: TaskStage
    label: str
    tasks: list[TaskOut] = Field(default_factory=list)


class BoardOut(BaseModel):
    """The whole board, its four stages in flow order. The SPA can derive this from the task
    list; exposing it once lets a reporting view share the same grouping."""

    columns: list[BoardColumn]


# ------------------------------------------------------------------ timeline


class TimelineMilestone(BaseModel):
    """A milestone marker on the timeline — the minimum a Gantt row needs."""

    id: str
    title: str
    due_date: date
    status: MilestoneStatus


class TimelineProject(BaseModel):
    """A project bar on the timeline, with its milestone markers."""

    id: str
    code: str
    name: str
    phase: ProjectPhase
    health: ProjectHealth
    progress_pct: int
    start_date: date
    target_date: date
    milestones: list[TimelineMilestone] = Field(default_factory=list)


class TimelineWeek(BaseModel):
    """One column of the timeline's week axis."""

    label: str
    start_date: date


class TimelineOut(BaseModel):
    """The delivery timeline: every project as a bar spanning its dates, its milestones as
    markers, plus the week axis that spans the whole portfolio."""

    weeks: list[TimelineWeek]
    projects: list[TimelineProject]


# ------------------------------------------------------------------ overview rollups


class HealthSlice(BaseModel):
    """One slice of the delivery-health breakdown. Mirrors the SPA's `HealthSlice`."""

    key: ProjectHealth
    label: str
    count: int
    #: Share of the portfolio, 0-100.
    pct: int


class BurndownPoint(BaseModel):
    """A point on the release/sprint burndown. Mirrors the SPA's `BurndownPoint`."""

    label: str
    ideal: int
    actual: int | None


class OverviewOut(BaseModel):
    """The Overview dashboard's server-side rollup. The SPA derives these from the project and
    milestone lists; this is the same arithmetic exposed once, so a reporting view can share it.

    `on_time_pct` is the share of milestones that are on time (completed, on track or due soon);
    `milestones_due` counts unfinished milestones due within the next fortnight;
    `utilization_pct` is a representative resource-utilization figure (no capacity service yet).
    """

    active_projects: int
    on_time_pct: float
    milestones_due: int
    utilization_pct: int
    avg_progress_pct: int
    total_budget_minor: int
    total_spent_minor: int
    currency: str
    health_breakdown: list[HealthSlice]
    burndown_sprint: list[BurndownPoint]
    burndown_release: list[BurndownPoint]
    burndown_delta_sprint: float
    burndown_delta_release: float
