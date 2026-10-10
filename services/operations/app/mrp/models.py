"""Tables owned by MRP (inventory plan Phase 7) — all in the `operations` schema.

* **mrp_runs** — one planning run: what it was asked (`params`), who asked, and how it ended.
  `QUEUED` (a large organization's run waiting for the worker) -> `COMPLETED` | `FAILED`.
* **mrp_demands** — the demand the run saw, frozen at execution: one row per sales-order line,
  open production-order material or manual line. Results can always be traced to what was
  planned from, even after the orders themselves change.
* **mrp_shortages** — one row per item the run could not cover (`BUY` for purchased items,
  `MAKE` for produced ones), with the whole netting arithmetic on the row.
* **mrp_shortage_lines** — pegging: which demand (or which produced parent) asked for how much of
  a shortage's gross requirement.

Everything stored per run is **immutable once the run is finished** except the requisition
linkage on a shortage (`requisition_request_id` / `requisition_id`). `item_id` columns are plain
indexed UUIDs, never foreign keys (the same rule manufacturing follows); the only foreign keys are
between MRP's own tables. `requisition_id` is a plain UUID because purchase requisitions (inventory
Phase 2) live on another branch and are wired in later.

Quantities are integer milli-units. No float, no `Numeric` anywhere.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from platform_core.models import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin

RUN_STATUSES = ("QUEUED", "COMPLETED", "FAILED")
DEMAND_SOURCES = ("SALES_ORDER", "PRODUCTION_ORDER", "MANUAL")
SHORTAGE_ACTIONS = ("BUY", "MAKE")

_RUN_STATUS_SQL = ", ".join(f"'{s}'" for s in RUN_STATUSES)
_SOURCE_SQL = ", ".join(f"'{s}'" for s in DEMAND_SOURCES)
_ACTION_SQL = ", ".join(f"'{s}'" for s in SHORTAGE_ACTIONS)


def _uuid(*, nullable: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), nullable=nullable)


def _milli(*, default: int | None = 0) -> Mapped[int]:
    return mapped_column(BigInteger, nullable=False, default=default)


class MrpRun(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """One MRP run. `params` is what was asked (sources, manual demand, chosen substitutes) so a
    queued run can be executed later by the worker and any run can be explained afterwards."""

    __tablename__ = "mrp_runs"

    #: Per-organization sequence behind `number` (`MRP-00042`).
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="QUEUED")
    #: `SYNC` ran inside the request; `WORKER` was queued and picked up by the sweep.
    execution: Mapped[str] = mapped_column(String(6), nullable=False, default="SYNC")
    #: The date effective dates (BOM versions, component windows) are evaluated on.
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    demand_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shortage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_planned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: SHA-256 of the canonical shortage list — two runs over the same data give the same hash.
    result_hash: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    requested_by: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"status IN ({_RUN_STATUS_SQL})", name="status_known"),
        CheckConstraint("execution IN ('SYNC', 'WORKER')", name="execution_known"),
        UniqueConstraint("organization_id", "seq", name="uq_mrp_runs_seq"),
        UniqueConstraint("organization_id", "number", name="uq_mrp_runs_number"),
        Index("ix_mrp_runs_org_created", "organization_id", "created_at"),
        Index(
            "ix_mrp_runs_queued",
            "status",
            "created_at",
            postgresql_where=text("status = 'QUEUED'"),
        ),
    )


class MrpDemand(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """A demand the run planned from. `quantity_milli` is the **open** quantity: what is still
    unmet after whatever the source has already reserved from stock."""

    __tablename__ = "mrp_demands"

    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.mrp_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    source_ref: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    item_id: Mapped[uuid.UUID] = _uuid()
    #: What the source asked for in total, and the part of it already reserved from stock.
    requested_milli: Mapped[int] = _milli(default=None)
    reserved_milli: Mapped[int] = _milli()
    quantity_milli: Mapped[int] = _milli(default=None)
    due_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    __table_args__ = (
        CheckConstraint(f"source_type IN ({_SOURCE_SQL})", name="source_known"),
        CheckConstraint(
            "requested_milli > 0 AND reserved_milli >= 0 AND quantity_milli > 0 "
            "AND quantity_milli = requested_milli - reserved_milli",
            name="quantities_consistent",
        ),
        Index("ix_mrp_demands_org_run", "organization_id", "run_id"),
    )


class MrpShortage(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """An item the run could not cover.

    `gross = demanded directly + demanded by produced parents`; `supply = max(0, on_hand -
    reserved) + open_po + open_production`; `shortage = max(0, gross - supply)` and is always
    positive on a stored row. `level` is the item's low-level code (0 = nothing above it): an item
    is only netted once every parent that could ask for it has been."""

    __tablename__ = "mrp_shortages"

    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.mrp_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = _uuid()
    action: Mapped[str] = mapped_column(String(4), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gross_milli: Mapped[int] = _milli(default=None)
    on_hand_milli: Mapped[int] = _milli()
    reserved_milli: Mapped[int] = _milli()
    open_po_milli: Mapped[int] = _milli()
    open_production_milli: Mapped[int] = _milli()
    shortage_milli: Mapped[int] = _milli(default=None)
    #: Earliest due date among the demand behind it, if any carried one.
    need_by: Mapped[date | None] = mapped_column(Date)
    #: Set when "create requisition from shortages" took this row: the request that carries it
    #: (a plain UUID — the event `operations.mrp.requisition_requested` names the same id) and,
    #: once the purchasing side has made a real requisition, that requisition's id.
    requisition_request_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    requisition_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requisition_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)

    __table_args__ = (
        CheckConstraint(f"action IN ({_ACTION_SQL})", name="action_known"),
        CheckConstraint(
            "gross_milli > 0 AND shortage_milli > 0 AND on_hand_milli >= 0 AND reserved_milli >= 0 "
            "AND open_po_milli >= 0 AND open_production_milli >= 0 AND level >= 0",
            name="quantities_sane",
        ),
        UniqueConstraint("organization_id", "run_id", "item_id", name="uq_mrp_shortages_item"),
        Index("ix_mrp_shortages_org_run", "organization_id", "run_id"),
    )


class MrpShortageLine(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """Pegging: one contributor to a shortage's gross requirement. `parent_item_id` is null for
    demand that named the item directly, else the produced parent whose net requirement
    exploded into it (then `source_*` are the root demand it descends from)."""

    __tablename__ = "mrp_shortage_lines"

    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.mrp_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    shortage_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("operations.mrp_shortages.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    source_ref: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    parent_item_id: Mapped[uuid.UUID | None] = _uuid(nullable=True)
    quantity_milli: Mapped[int] = _milli(default=None)
    due_date: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint(f"source_type IN ({_SOURCE_SQL})", name="source_known"),
        CheckConstraint("quantity_milli > 0", name="quantity_positive"),
        Index("ix_mrp_shortage_lines_org_shortage", "organization_id", "shortage_id"),
    )
