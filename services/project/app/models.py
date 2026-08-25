"""Tables owned by the project service — PulseBoard's engagements and board.

Everything here lives in the `project` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). Links to the account directory are plain UUIDs; links **within** this
schema (`projects.owner_id`, `tasks.project_id`, `tasks.assignee_id`) are real foreign keys,
because only cross-schema foreign keys are banned.

Three concerns, one schema:

* **people** — the delivery-team roster the whole product renders against: a project owner, a
  task assignee, a timesheet row, a standup author. `ref` (`p01`) is the stable human handle
  the SPA carries as `Person.id`; the primary key is an opaque UUID, and the timesheet and
  standup services carry that same UUID as a plain column (never a cross-schema FK).
  `capacity_hours` is the billable-target hours for a standard week — the denominator of the
  utilization math.
* **projects** — one client engagement, with its delivery `status` (the semantic traffic
  light plus `done`, stored as a word), progress, dates and money. Budget and spend are
  integer minor units in an ISO currency (ADR-0000 §8), never a float.
* **tasks** — a card on the board, in one of four stages, with a priority, an hours estimate
  and free-form tags.

The human identifiers the SPA speaks in — `PB-01`, `PB-231`, `p01` — are stored as their own
unique-per-org columns (`code` / `key` / `ref`); the API exposes those alongside the opaque id.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "project"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Person(Base, TenantBase):
    """A member of the delivery team — an owner, an assignee, a timesheet row.

    `ref` (`p01`) is the handle the SPA renders as `Person.id`; it is unique within an
    organization, not globally. `directory_id` links to the account directory (a plain UUID,
    never a cross-schema FK) for the day scope resolves against a real login; it is nullable
    because a roster member need not have a platform seat. `capacity_hours` is the weekly
    billable target the utilization percentage divides by.
    """

    __tablename__ = "people"

    ref: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    location: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: Billable-target hours for a standard week — the utilization denominator.
    capacity_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    #: The account directory id of this person, when they have a platform login. A plain UUID.
    directory_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint("organization_id", "ref", name="uq_people_org_ref"),
        Index("ix_people_org_directory", "organization_id", "directory_id"),
    )


class Project(Base, TenantBase):
    """One client engagement.

    `code` (`PB-01`) is the human identity the API keys on, unique within an organization.
    `owner_id` is a real in-schema FK to `people` — it is also the OWN scope column, so a
    caller scoped to their own work sees the engagements they own. `status` is the delivery
    traffic light plus the delivered state (`on_track` / `at_risk` / `blocked` / `done`),
    stored as a word, never a colour.

    `budget_minor` / `spent_minor` are integer minor units in `currency` (ISO-4217). No float
    ever touches money (ADR-0000 §8). `open_tasks` is the engagement's own open-work headline
    (its full backlog size), a stored figure independent of the board's cards.
    """

    __tablename__ = "projects"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    client: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    #: The engagement owner — an in-schema FK, and the OWN scope column.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("project.people.id", ondelete="SET NULL")
    )

    #: on_track | at_risk | blocked | done — the delivery traffic light, a stored word.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="on_track")
    #: Delivery progress, 0-100.
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    start_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)

    budget_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    spent_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    #: Whether this engagement bills the client (an internal project does not).
    billable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: The engagement's own open-work headline (its backlog size), a stored figure.
    open_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        # Every index leads with `organization_id`: under row-level security the policy qual is
        # ANDed into every query, so an index that did not lead with the tenant column would
        # scan the whole platform's rows and discard almost all of them (ADR-0002).
        UniqueConstraint("organization_id", "code", name="uq_projects_org_code"),
        Index("ix_projects_org_status", "organization_id", "status"),
        Index("ix_projects_org_owner", "organization_id", "owner_id"),
        Index("ix_projects_org_due", "organization_id", "due_date"),
    )


class Task(Base, TenantBase):
    """A card on the board.

    `project_id` and `assignee_id` are real in-schema FKs. `key` (`PB-231`) is the human card
    identity, unique within an organization. `stage` places the card in one of the four board
    columns (`backlog` / `in_progress` / `review` / `done`); `priority` is the urgency band;
    `estimate_hours` sizes the card; `tags` is a free-form label list. `assignee_id` is the
    OWN scope column for task reads. `due_date` is legitimately null for an un-dated card.
    """

    __tablename__ = "tasks"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("project.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("project.people.id", ondelete="SET NULL")
    )
    #: backlog | in_progress | review | done.
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="backlog")
    #: low | medium | high.
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    #: Estimate in whole hours.
    estimate_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(60)), nullable=False, default=list)
    due_date: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="uq_tasks_org_key"),
        Index("ix_tasks_org_project", "organization_id", "project_id"),
        Index("ix_tasks_org_stage", "organization_id", "stage"),
        Index("ix_tasks_org_assignee", "organization_id", "assignee_id"),
        Index("ix_tasks_org_due", "organization_id", "due_date"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
