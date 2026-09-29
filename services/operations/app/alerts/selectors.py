"""Reads for the operational alert sweep and the insight dashboards (fulfilment plan §4; F12).

Every query filters on `organization_id` explicitly, the same net-not-filter convention every
other selector in this service follows. Nothing here writes; `app.alerts.services` does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain
from app.inventory.quantity import QTY_SCALE
from app.models import (
    AssignmentOffer,
    FulfilmentSettings,
    InventoryDiscrepancy,
    InventoryItem,
    OperationalAlert,
    PickTask,
    Shipment,
    Worker,
)
from app.schemas import OperationalAlertOut
from platform_core.auth import ScopeContext

#: The hardcoded default when an organization has no `fulfilment_settings` row at all yet
#: (ADR-0024 §4's own fallback shape, mirrored here rather than imported from `app.orders` —
#: `app.orders` may not import `fulfilment`/`dispatch`, ADR-0024 §1, but the reverse is fine;
#: this module deliberately does not reach into `app.orders.selectors.resolve_settings` at all,
#: since that dataclass and its callers are `orders`' own surface and this sweep needs only three
#: of its columns, not the whole resolved settings shape).
DEFAULT_TASK_SLA_MINUTES = 60


@dataclass(slots=True, frozen=True)
class AlertSettings:
    task_sla_minutes: int
    alert_email: str | None
    alert_phone: str | None


async def resolve_alert_settings(
    session: AsyncSession, organization_id: uuid.UUID, warehouse_id: uuid.UUID | None
) -> AlertSettings:
    """The effective SLA and alert contact for `warehouse_id`: prefer a live row scoped to that
    warehouse, else the organization's default row (`warehouse_id IS NULL`), else the hardcoded
    default — the same three-step preference `app.orders.selectors.resolve_settings` documents,
    kept independent of it (see module docstring)."""
    if warehouse_id is not None:
        row = (
            await session.execute(
                select(FulfilmentSettings).where(
                    FulfilmentSettings.organization_id == organization_id,
                    FulfilmentSettings.warehouse_id == warehouse_id,
                    FulfilmentSettings.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return AlertSettings(row.task_sla_minutes, row.alert_email, row.alert_phone)
    row = (
        await session.execute(
            select(FulfilmentSettings).where(
                FulfilmentSettings.organization_id == organization_id,
                FulfilmentSettings.warehouse_id.is_(None),
                FulfilmentSettings.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return AlertSettings(row.task_sla_minutes, row.alert_email, row.alert_phone)
    return AlertSettings(DEFAULT_TASK_SLA_MINUTES, None, None)


async def low_stock_items(session: AsyncSession, organization_id: uuid.UUID) -> list[InventoryItem]:
    """Active items at or below their reorder point — `domain.is_low_stock`'s own reading,
    the same derivation `app.inventory.selectors.inventory_stmt(low_stock=True)` uses for the
    list screen, applied here for the sweep."""
    stmt = select(InventoryItem).where(
        InventoryItem.organization_id == organization_id,
        InventoryItem.deleted_at.is_(None),
        InventoryItem.is_active.is_(True),
        InventoryItem.on_hand_milli <= InventoryItem.reorder_point * QTY_SCALE,
    )
    return list((await session.execute(stmt)).scalars().all())


async def delayed_pick_tasks(
    session: AsyncSession, organization_id: uuid.UUID, *, now: datetime
) -> list[PickTask]:
    """Pick tasks still open (`domain.PICK_TASK_OPEN_STATUSES`) whose own warehouse's SLA has
    already passed since `created_at`. One SLA lookup per warehouse in play, not per task."""
    candidates = list(
        (
            await session.execute(
                select(PickTask).where(
                    PickTask.organization_id == organization_id,
                    PickTask.status.in_(domain.PICK_TASK_OPEN_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        return []
    sla_by_warehouse: dict[uuid.UUID, int] = {}
    overdue: list[PickTask] = []
    for task in candidates:
        sla = sla_by_warehouse.get(task.warehouse_id)
        if sla is None:
            settings = await resolve_alert_settings(session, organization_id, task.warehouse_id)
            sla = sla_by_warehouse[task.warehouse_id] = settings.task_sla_minutes
        if task.created_at <= now - timedelta(minutes=sla):
            overdue.append(task)
    return overdue


async def delayed_shipments(
    session: AsyncSession, organization_id: uuid.UUID, *, now: datetime
) -> list[Shipment]:
    """Shipments not yet terminal (`domain.SHIPMENT_TERMINAL_STATUSES`) whose own warehouse's SLA
    has already passed since `created_at`."""
    candidates = list(
        (
            await session.execute(
                select(Shipment).where(
                    Shipment.organization_id == organization_id,
                    Shipment.deleted_at.is_(None),
                    Shipment.status.not_in(domain.SHIPMENT_TERMINAL_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        return []
    sla_by_warehouse: dict[uuid.UUID, int] = {}
    overdue: list[Shipment] = []
    for shipment in candidates:
        sla = sla_by_warehouse.get(shipment.warehouse_id)
        if sla is None:
            settings = await resolve_alert_settings(session, organization_id, shipment.warehouse_id)
            sla = sla_by_warehouse[shipment.warehouse_id] = settings.task_sla_minutes
        if shipment.created_at <= now - timedelta(minutes=sla):
            overdue.append(shipment)
    return overdue


def alerts_stmt(ctx: ScopeContext, *, kind: str | None, open_only: bool) -> Select[Any]:
    stmt = select(OperationalAlert).where(OperationalAlert.organization_id == ctx.organization_id)
    if kind:
        stmt = stmt.where(OperationalAlert.kind == kind)
    if open_only:
        stmt = stmt.where(OperationalAlert.resolved_at.is_(None))
    return stmt.order_by(OperationalAlert.detected_at.desc(), OperationalAlert.id)


async def get_alert(
    session: AsyncSession, organization_id: uuid.UUID, alert_id: uuid.UUID
) -> OperationalAlert | None:
    return (
        await session.execute(
            select(OperationalAlert).where(
                OperationalAlert.id == alert_id, OperationalAlert.organization_id == organization_id
            )
        )
    ).scalar_one_or_none()


def alert_out(row: OperationalAlert) -> OperationalAlertOut:
    return OperationalAlertOut(
        id=str(row.id),
        kind=row.kind,
        subject_type=row.subject_type,
        subject_id=str(row.subject_id),
        warehouse_id=str(row.warehouse_id) if row.warehouse_id else None,
        detail=row.detail,
        detected_at=row.detected_at,
        last_seen_at=row.last_seen_at,
        resolved_at=row.resolved_at,
        resolved_by=row.resolved_by,
    )


# ------------------------------------------------------------------ insight dashboards (plan §4)


async def task_backlog_by_warehouse(
    session: AsyncSession, organization_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Open pick-task count and the average age (minutes) of the open ones, per warehouse — the
    backlog/throughput view. Real aggregates over `pick_tasks`, computed at read time; nothing
    here is sampled or hardcoded."""
    age_minutes = func.extract("epoch", func.now() - PickTask.created_at) / 60
    rows = (
        await session.execute(
            select(
                PickTask.warehouse_id,
                func.count(),
                func.avg(age_minutes),
                func.max(age_minutes),
            )
            .where(
                PickTask.organization_id == organization_id,
                PickTask.status.in_(domain.PICK_TASK_OPEN_STATUSES),
            )
            .group_by(PickTask.warehouse_id)
            .order_by(func.count().desc())
        )
    ).all()
    return [
        {
            "warehouse_id": str(r[0]),
            "open_tasks": int(r[1]),
            "avg_age_minutes": round(float(r[2]), 1) if r[2] is not None else 0.0,
            "max_age_minutes": round(float(r[3]), 1) if r[3] is not None else 0.0,
        }
        for r in rows
    ]


async def throughput_by_warehouse(
    session: AsyncSession, organization_id: uuid.UUID, *, since: datetime
) -> list[dict[str, Any]]:
    """Pick tasks that reached `CLOSED` since `since`, and their average cycle time
    (`updated_at - created_at`, minutes) — the completed side of the same backlog view."""
    cycle_minutes = func.extract("epoch", PickTask.updated_at - PickTask.created_at) / 60
    rows = (
        await session.execute(
            select(PickTask.warehouse_id, func.count(), func.avg(cycle_minutes))
            .where(
                PickTask.organization_id == organization_id,
                PickTask.status == "CLOSED",
                PickTask.updated_at >= since,
            )
            .group_by(PickTask.warehouse_id)
            .order_by(func.count().desc())
        )
    ).all()
    return [
        {
            "warehouse_id": str(r[0]),
            "closed_tasks": int(r[1]),
            "avg_cycle_minutes": round(float(r[2]), 1) if r[2] is not None else 0.0,
        }
        for r in rows
    ]


async def zone_bottlenecks(
    session: AsyncSession, organization_id: uuid.UUID, *, since: datetime, limit: int = 20
) -> list[dict[str, Any]]:
    """Which zone's tasks take longest to close, most recently — average and count of `CLOSED`
    tasks per `zone_location_id` since `since`, slowest first. A task with no resolved zone
    (`zone_location_id IS NULL` — an order whose lines could not all be grouped under one, per
    `PickTask.zone_location_id`'s own docstring) is excluded: there is no single zone to blame."""
    cycle_minutes = func.extract("epoch", PickTask.updated_at - PickTask.created_at) / 60
    rows = (
        await session.execute(
            select(PickTask.zone_location_id, func.count(), func.avg(cycle_minutes))
            .where(
                PickTask.organization_id == organization_id,
                PickTask.status == "CLOSED",
                PickTask.updated_at >= since,
                PickTask.zone_location_id.is_not(None),
            )
            .group_by(PickTask.zone_location_id)
            .order_by(func.avg(cycle_minutes).desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "zone_location_id": str(r[0]),
            "closed_tasks": int(r[1]),
            "avg_cycle_minutes": round(float(r[2]), 1) if r[2] is not None else 0.0,
        }
        for r in rows
    ]


async def worker_workload(
    session: AsyncSession, organization_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Open assignment-offer count per worker (`ACCEPTED` offers on a still-open task) — the
    workload-distribution view. Joins `assignment_offers` to `pick_tasks` so a worker whose task
    already closed drops off the list."""
    rows = (
        await session.execute(
            select(Worker.id, Worker.display_name, func.count(AssignmentOffer.id))
            .join(AssignmentOffer, AssignmentOffer.worker_id == Worker.id)
            .join(PickTask, PickTask.id == AssignmentOffer.task_id)
            .where(
                Worker.organization_id == organization_id,
                AssignmentOffer.organization_id == organization_id,
                AssignmentOffer.state == "ACCEPTED",
                PickTask.status.in_(domain.PICK_TASK_OPEN_STATUSES),
            )
            .group_by(Worker.id, Worker.display_name)
            .order_by(func.count(AssignmentOffer.id).desc())
        )
    ).all()
    return [{"worker_id": str(r[0]), "worker_name": r[1], "open_tasks": int(r[2])} for r in rows]


async def discrepancy_trend(
    session: AsyncSession, organization_id: uuid.UUID, *, since: datetime
) -> list[dict[str, Any]]:
    """Inventory discrepancies detected since `since`, per day and kind — the exception-trend
    view over the reconciler's own findings (`app.inventory.reconcile`), never a synthetic
    series."""
    day = func.date(func.timezone("UTC", InventoryDiscrepancy.detected_at))
    rows = (
        await session.execute(
            select(day, InventoryDiscrepancy.kind, func.count())
            .where(
                InventoryDiscrepancy.organization_id == organization_id,
                InventoryDiscrepancy.detected_at >= since,
            )
            .group_by(day, InventoryDiscrepancy.kind)
            .order_by(day)
        )
    ).all()
    return [{"date": str(r[0]), "kind": r[1], "count": int(r[2])} for r in rows]
