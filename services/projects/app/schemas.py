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

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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


class MemberOut(BaseModel):
    """The full members-list row (unlike the embedded `Person`, which is only ever a
    projection carried on a project/milestone/task). `id` is the stable `ref` (`p-01`) — the
    same value the SPA's owner/assignee pickers submit as `owner_ref` / `assignee_ref`.
    `directory_id` is the linked account-directory id, when this member has a platform login."""

    id: str
    name: str
    role: str
    directory_id: str | None


class MemberIn(BaseModel):
    """Add a delivery-org member. `ref` (`p-01`) is the stable handle every `owner_ref` /
    `assignee_ref` on the wire carries — the same pattern as a project's `code`."""

    ref: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(default="", max_length=120)
    directory_id: uuid.UUID | None = None


class MemberUpdate(BaseModel):
    """Edit a member. Absent means "leave alone"; `ref` is immutable — it is the API identity
    every owner/assignee picker submits."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=120)
    directory_id: uuid.UUID | None = None


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


class ProjectUpdate(BaseModel):
    """Edit a project. Absent means "leave alone"; an explicit `null` on `owner_ref` clears the
    owner. `code` is immutable — it is the URL key and travels in every already-published
    event. `open_tasks` is not here: it is a stored headline figure, not user-editable."""

    name: str | None = Field(default=None, min_length=1, max_length=300)
    client: str | None = Field(default=None, max_length=200)
    owner_ref: str | None = Field(default=None, max_length=40)
    phase: ProjectPhase | None = None
    health: ProjectHealth | None = None
    progress_pct: int | None = Field(default=None, ge=0, le=100)
    start_date: date | None = None
    target_date: date | None = None
    budget_minor: int | None = Field(default=None, ge=0)
    spent_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    team_size: int | None = Field(default=None, ge=0)
    summary: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _dates_in_order(self) -> ProjectUpdate:
        """Only enforceable when both ends travel in the same PATCH — a single-field date edit
        is checked against the stored counterpart's semantics by the service layer instead,
        which has the existing row to compare against."""
        if (
            self.start_date is not None
            and self.target_date is not None
            and self.target_date < self.start_date
        ):
            raise ValueError("target_date must be on or after start_date")
        return self


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
    #: Nullable, as the column is: an undated milestone is legitimate, and typing this as
    #: required made every list containing one answer 500.
    due_date: date | None
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


class MilestoneUpdate(BaseModel):
    """Edit a milestone. Absent means "leave alone"; an explicit `null` on `owner_ref` clears
    the owner. Not `project_code` — a milestone never changes project through this route."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    owner_ref: str | None = Field(default=None, max_length=40)
    due_date: date | None = None
    status: MilestoneStatus | None = None
    progress_pct: int | None = Field(default=None, ge=0, le=100)


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


class TaskUpdate(BaseModel):
    """Move or edit a board card — this is what persists a Kanban drag. Absent means "leave
    alone"; an explicit `null` on `assignee_ref` or `due_date` clears it. Not `project_code` —
    a card never changes project through this route."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    assignee_ref: str | None = Field(default=None, max_length=40)
    stage: TaskStage | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None
    points: int | None = Field(default=None, ge=0)


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
    `milestones_due` counts unfinished milestones due within the next fortnight.

    `utilization_pct` and the `burndown_*` fields are `None`, not a representative figure: this
    service has no capacity source (a member carries no weekly hours) and no dated history of a
    task's remaining points (a task carries its current `stage`, never a per-day snapshot), so
    neither is derivable from data that exists. Rendering a placeholder number here previously
    looked live and was not; `None` is the honest answer until a capacity source and a delivery
    snapshot both exist (see the dashboard-data audit, §3.9 and §3.4 row 14-15).
    """

    active_projects: int
    on_time_pct: float
    milestones_due: int
    utilization_pct: int | None
    avg_progress_pct: int
    total_budget_minor: int
    total_spent_minor: int
    currency: str
    health_breakdown: list[HealthSlice]
    burndown_sprint: list[BurndownPoint] | None
    burndown_release: list[BurndownPoint] | None
    burndown_delta_sprint: float | None
    burndown_delta_release: float | None
