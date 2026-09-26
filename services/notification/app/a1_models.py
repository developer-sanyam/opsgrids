"""Tables for the automation workflow engine (slice A1).

`Automation` is the editable shell: name, status and the **draft** graph. Publishing copies the
draft into an `AutomationVersion`, which is immutable (the database refuses UPDATE and DELETE on
it, see the migration) and is what runs execute. A `AutomationRun` pins the version it started on,
so editing or re-publishing an automation never changes a run already in flight.

`AutomationRun` is unique per `(organization_id, automation_id, source_event_id)`: that constraint
*is* the idempotency guarantee, so a redelivered event cannot start a second run whatever the
consumer's own ledger says. Its context (the slice of the trigger payload the graph refers to, and
what earlier steps produced) is sealed, because it can hold an address or a name.

`AutomationRunStep` is one row per node visit and is only ever appended or advanced by the run
that owns it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from platform_core.models import OrgScopedMixin, TenantBase, UUIDPrimaryKeyMixin

AUTOMATION_STATUSES = ("draft", "active", "paused")
RUN_STATES = ("running", "waiting", "completed", "failed", "skipped")
STEP_STATES = ("waiting", "running", "done", "failed", "skipped")


class Automation(Base, TenantBase):
    __tablename__ = "a1_automations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: draft | active | paused.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    #: The trigger node's event, denormalised so the consumer's lookup is one indexed read.
    trigger_event: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: The product prefix of `trigger_event` (`support`, `commerce`, ...), for the list filter.
    module: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    draft_graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: The published version runs start on. Null until the first publish.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    current_version: Mapped[int | None] = mapped_column(Integer)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active', 'paused')", name="status"),
        Index("ix_a1_automations_org_status", "organization_id", "status"),
        Index("ix_a1_automations_org_trigger", "organization_id", "trigger_event", "status"),
    )


class AutomationVersion(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One published graph. Immutable: never updated, never deleted."""

    __tablename__ = "a1_versions"

    automation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(120), nullable=False)
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "automation_id", "version", name="uq_a1_versions_org_auto_version"
        ),
    )


class AutomationRun(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    __tablename__ = "a1_runs"

    automation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_event_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: `<automation_id>:<source_event_id>` — shown on the run detail.
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Masked recipient once a send step resolved one; the trigger's event type until then.
    subject: Mapped[str] = mapped_column(String(254), nullable=False, default="")
    failure_code: Mapped[str | None] = mapped_column(String(80))
    #: The node the run is parked on or about to execute.
    current_node_id: Mapped[str | None] = mapped_column(String(64))
    next_wake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Sealed JSON: `{"payload": {...referenced slice...}, "run": {...}}`.
    context_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: When a wake-up scheduled through the scheduler is due, this run is woken by
    #: `wake_token` exactly once: the token rotates every time a wait is entered.
    wake_token: Mapped[str | None] = mapped_column(String(64))
    #: How many node visits this run has recorded; the next step's `seq`.
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "state IN ('running', 'waiting', 'completed', 'failed', 'skipped')", name="state"
        ),
        UniqueConstraint(
            "organization_id", "automation_id", "source_event_id", name="uq_a1_runs_org_auto_event"
        ),
        Index("ix_a1_runs_org_auto_started", "organization_id", "automation_id", "started_at"),
        Index("ix_a1_runs_org_state_wake", "organization_id", "state", "next_wake_at"),
    )


class AutomationRunStep(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    __tablename__ = "a1_run_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: Visit order within the run. `started_at` alone cannot order steps written in one
    #: transaction, which all share a timestamp.
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    detail: Mapped[str | None] = mapped_column(String(500))
    message_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint(
            "state IN ('waiting', 'running', 'done', 'failed', 'skipped')", name="state"
        ),
        Index("ix_a1_run_steps_org_run", "organization_id", "run_id", "seq"),
    )
