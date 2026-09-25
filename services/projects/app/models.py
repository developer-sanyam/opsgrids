"""Tables owned by the projects service — the delivery portfolio.

Everything here lives in the `projects` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). Links to the account directory are plain UUIDs; links **within** this
schema (`projects.owner_id`, `milestones.project_id`, `tasks.assignee_id`) are real foreign
keys, because only cross-schema foreign keys are banned.

Four concerns, one schema:

* **members** — a person in the delivery org: a project owner or a task assignee. `ref`
  (`p-01`) is the stable human handle the SPA carries as `Person.id`; the primary key is an
  opaque UUID. Kept as its own table so the embedded `Person` object is built once and every
  project / milestone / task references the same person by id.
* **projects** — one project in the portfolio, with its phase, delivery `health` (the semantic
  traffic light, stored as a word), progress, dates and money. Budget and spend are integer
  minor units in an ISO currency (ADR-0000 §8), never a float.
* **milestones** — a dated checkpoint on a project, with its own owner, lifecycle status and
  completion.
* **tasks** — a card on the workflow board, in one of four stages, with a priority and a
  story-point estimate.

The human identifiers the SPA speaks in — `PRJ-104`, `p-01` — are stored as their own
unique-per-org columns (`code` / `ref`); the API exposes those, not the opaque primary key.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "projects"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Member(Base, TenantBase):
    """A person in the delivery org — a project owner or a task assignee.

    `ref` (`p-01`) is the stable handle the SPA renders as `Person.id`; it is unique within an
    organization, not globally. `directory_id` links to the account directory (a plain UUID,
    never a cross-schema FK) for the day scope resolves against a real login; it is nullable
    because a delivery-org member need not have a platform seat.
    """

    __tablename__ = "members"

    ref: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: The account directory id of this member, when they have a platform login. A plain UUID.
    directory_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint("organization_id", "ref", name="uq_members_org_ref"),
        Index("ix_members_org_directory", "organization_id", "directory_id"),
    )


class Project(Base, TenantBase):
    """One project in the delivery portfolio.

    `code` (`PRJ-104`) is the human identity the API keys on, unique within an organization.
    `owner_id` is a real in-schema FK to `members` — it is also the OWN scope column, so a
    caller scoped to their own work sees the projects they own. `health` is the semantic
    traffic light (`on_track` / `at_risk` / `delayed`) stored as a word, never a colour.

    `budget_minor` / `spent_minor` are integer minor units in `currency` (ISO-4217). No float
    ever touches money (ADR-0000 §8). `team_size` and `open_tasks` are stored headline figures
    the SPA renders directly — `open_tasks` is the project's own backlog size, independent of
    the board's sample cards.
    """

    __tablename__ = "projects"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    client: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    #: The project owner — an in-schema FK, and the OWN scope column.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.members.id", ondelete="SET NULL")
    )

    #: discovery | design | build | stabilize | launch | live.
    phase: Mapped[str] = mapped_column(String(20), nullable=False, default="discovery")
    #: on_track | at_risk | delayed — the delivery traffic light, a stored word.
    health: Mapped[str] = mapped_column(String(20), nullable=False, default="on_track")
    #: Delivery progress, 0-100.
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    start_date: Mapped[date | None] = mapped_column(Date)
    target_date: Mapped[date | None] = mapped_column(Date)

    budget_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    spent_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    team_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: The project's own open-work headline (its backlog size), a stored figure.
    open_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        # Every index leads with `organization_id`: under row-level security the policy qual is
        # ANDed into every query, so an index that did not lead with the tenant column would
        # scan the whole platform's rows and discard almost all of them (ADR-0002).
        UniqueConstraint("organization_id", "code", name="uq_projects_org_code"),
        Index("ix_projects_org_health", "organization_id", "health"),
        Index("ix_projects_org_phase", "organization_id", "phase"),
        Index("ix_projects_org_owner", "organization_id", "owner_id"),
        Index("ix_projects_org_target", "organization_id", "target_date"),
    )


class Milestone(Base, TenantBase):
    """A dated checkpoint on a project.

    `project_id` and `owner_id` are real in-schema FKs. `status` is the lifecycle word the SPA
    labels (`completed` / `on_track` / `due_soon` / `at_risk` / `slipping`) — `slipping` is past
    due and unfinished, `at_risk` is trending late. `progress_pct` is the checklist completion,
    0-100. `owner_id` is the OWN scope column for milestone reads.
    """

    __tablename__ = "milestones"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.members.id", ondelete="SET NULL")
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    #: completed | on_track | due_soon | at_risk | slipping.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="on_track")
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_milestones_org_project", "organization_id", "project_id"),
        Index("ix_milestones_org_status", "organization_id", "status"),
        Index("ix_milestones_org_due", "organization_id", "due_date"),
        Index("ix_milestones_org_owner", "organization_id", "owner_id"),
    )


class Task(Base, TenantBase):
    """A card on the workflow board.

    `project_id` and `assignee_id` are real in-schema FKs. `stage` places the card in one of the
    four board columns (`backlog` / `in_progress` / `review` / `done`); `priority` is the ITIL-ish
    urgency band; `points` is the story-point estimate. `assignee_id` is the OWN scope column for
    task reads. `due_date` is legitimately null for an un-dated backlog card.
    """

    __tablename__ = "tasks"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.members.id", ondelete="SET NULL")
    )
    #: backlog | in_progress | review | done.
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="backlog")
    #: low | medium | high | urgent.
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    due_date: Mapped[date | None] = mapped_column(Date)
    #: Estimate in story points.
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Set when the card enters `done`, cleared when it leaves (ADR-0020 §3, slice I6) — the
    #: timestamp `insights.task_facts` needs for "tasks completed" and "points completed", which
    #: no other column on this row records. Backdated by migration `_backfill_task_completed_at`
    #: from this organization's own `audit_logs` for a task already `done` before this column
    #: existed; `NULL` where no audit row said when.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_tasks_org_project", "organization_id", "project_id"),
        Index("ix_tasks_org_stage", "organization_id", "stage"),
        Index("ix_tasks_org_assignee", "organization_id", "assignee_id"),
        Index("ix_tasks_org_completed_at", "organization_id", "completed_at"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
