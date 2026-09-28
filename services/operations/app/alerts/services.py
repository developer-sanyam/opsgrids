"""The operational alert sweep (fulfilment plan §4; F12) — low stock, and a pick task or shipment
sitting past its SLA. The same shape `app.inventory.reconcile.reconcile_organization` already
established for a "detect and record drift, alert once, never repair" sweep: one open
`operational_alerts` row per subject, refreshed (never duplicated) while the condition holds, and
a domain event staged **only the first time a row opens** — that single moment is what an
automation (`services/notification`'s A1 engine, once `SUBSCRIBABLE_EVENTS` names these three
events) hangs an alert off. A condition that clears — stock replenished, the task or shipment
reaching a terminal status — closes its row automatically on the next pass; nothing here writes to
`inventory_items`, `pick_tasks` or `shipments` themselves.

Runs from `app.cron.AlertSweep`, on the same discovery/work-scope split `ReconcileSweep` uses: one
transaction per organization, one organization's failure never stopping the rest of a pass.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, literal_column, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import selectors as alert_selectors
from app.inventory import quantity
from app.models import OperationalAlert, OutboxEvent
from platform_core.events import publish_event
from platform_core.logging import get_logger

log = get_logger("operations.alerts")

EVT_LOW_STOCK = "operations.inventory.item.low_stock"
EVT_TASK_DELAYED = "operations.fulfilment.task.delayed"
EVT_SHIPMENT_DELAYED = "operations.dispatch.shipment.delayed"

ACTOR = {"type": "system", "id": "operations.alerts.sweep"}
RESOLVED_BY_SWEEP = "system:alert_sweep"


@dataclass(slots=True)
class AlertReport:
    organization_id: uuid.UUID
    opened: int = 0
    refreshed: int = 0
    resolved: int = 0
    findings: list[str] = field(default_factory=list)


async def _upsert_open(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    kind: str,
    subject_type: str,
    subject_id: uuid.UUID,
    warehouse_id: uuid.UUID | None,
    detail: dict[str, object],
) -> tuple[uuid.UUID, bool]:
    """Open (or refresh) one alert row. `ON CONFLICT ... WHERE resolved_at IS NULL` is the same
    partial-unique-index upsert `app.inventory.reconcile._RECORD_SQL` uses: a subject that
    already has an open alert of this kind gets its `detail`/`last_seen_at` touched, never a
    second row; `inserted` (`xmax = 0`, the same "was this row just inserted" check
    `reconcile._RECORD_SQL` returns) says whether this pass is the one that opened it."""
    row: Any = (
        await session.execute(
            pg_insert(OperationalAlert)
            .values(
                id=uuid.uuid4(),
                organization_id=organization_id,
                kind=kind,
                subject_type=subject_type,
                subject_id=subject_id,
                warehouse_id=warehouse_id,
                detail=detail,
            )
            .on_conflict_do_update(
                index_elements=["organization_id", "kind", "subject_type", "subject_id"],
                index_where=OperationalAlert.resolved_at.is_(None),
                set_={"detail": detail, "last_seen_at": datetime.now(UTC)},
            )
            .returning(OperationalAlert.id, literal_column("(xmax = 0)"))
        )
    ).one()
    return row[0], bool(row[1])


async def _resolve_missing(
    session: AsyncSession, organization_id: uuid.UUID, kind: str, still_open: set[uuid.UUID]
) -> int:
    """Close every open alert of `kind` whose subject this pass did not find breaching. A single
    bulk `UPDATE`, not a per-row round trip."""
    stmt = (
        update(OperationalAlert)
        .where(
            OperationalAlert.organization_id == organization_id,
            OperationalAlert.kind == kind,
            OperationalAlert.resolved_at.is_(None),
        )
        .values(resolved_at=datetime.now(UTC), resolved_by=RESOLVED_BY_SWEEP)
    )
    if still_open:
        stmt = stmt.where(OperationalAlert.subject_id.not_in(still_open))
    # `session.execute` is typed to return the generic `Result`, but an UPDATE statement always
    # produces a `CursorResult` at runtime — which is what actually carries `rowcount`
    # (`services/approval/app/services.py`'s own cast, same reasoning).
    result = cast("CursorResult[Any]", await session.execute(stmt))
    return int(result.rowcount or 0)


def _publish(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    aggregate_id: uuid.UUID,
    payload: dict[str, object],
) -> None:
    publish_event(
        session,
        OutboxEvent,
        organization_id=organization_id,
        event_type=event_type,
        aggregate_type="operational_alert",
        aggregate_id=aggregate_id,
        payload=payload,
        actor=ACTOR,
    )


async def sweep_low_stock(
    session: AsyncSession, organization_id: uuid.UUID, report: AlertReport
) -> None:
    items = await alert_selectors.low_stock_items(session, organization_id)
    settings = await alert_selectors.resolve_alert_settings(session, organization_id, None)
    still_open: set[uuid.UUID] = set()
    for item in items:
        still_open.add(item.id)
        detail = {
            "organization_id": str(organization_id),
            "inventory_item_id": str(item.id),
            "sku": item.sku,
            "name": item.name,
            "warehouse": item.warehouse,
            "uom": item.uom,
            "on_hand": quantity.whole_units(item.on_hand_milli),
            "reorder_point": item.reorder_point,
            "alert_contact": {"email": settings.alert_email, "phone": settings.alert_phone},
        }
        alert_id, inserted = await _upsert_open(
            session,
            organization_id=organization_id,
            kind="low_stock",
            subject_type="inventory_item",
            subject_id=item.id,
            warehouse_id=None,
            detail=detail,
        )
        report.opened += inserted
        report.refreshed += not inserted
        if inserted:
            report.findings.append(f"low_stock:{item.sku}")
            _publish(
                session,
                organization_id=organization_id,
                event_type=EVT_LOW_STOCK,
                aggregate_id=alert_id,
                payload={"alert_id": str(alert_id), **detail},
            )
    report.resolved += await _resolve_missing(session, organization_id, "low_stock", still_open)


async def sweep_delayed_tasks(
    session: AsyncSession, organization_id: uuid.UUID, report: AlertReport, *, now: datetime
) -> None:
    tasks = await alert_selectors.delayed_pick_tasks(session, organization_id, now=now)
    still_open: set[uuid.UUID] = set()
    for task in tasks:
        still_open.add(task.id)
        settings = await alert_selectors.resolve_alert_settings(
            session, organization_id, task.warehouse_id
        )
        age_minutes = round((now - task.created_at).total_seconds() / 60, 1)
        detail = {
            "organization_id": str(organization_id),
            "task_id": str(task.id),
            "order_id": str(task.order_id),
            "warehouse_id": str(task.warehouse_id),
            "status": task.status,
            "age_minutes": age_minutes,
            "sla_minutes": settings.task_sla_minutes,
            "alert_contact": {"email": settings.alert_email, "phone": settings.alert_phone},
        }
        alert_id, inserted = await _upsert_open(
            session,
            organization_id=organization_id,
            kind="task_delayed",
            subject_type="pick_task",
            subject_id=task.id,
            warehouse_id=task.warehouse_id,
            detail=detail,
        )
        report.opened += inserted
        report.refreshed += not inserted
        if inserted:
            report.findings.append(f"task_delayed:{task.id}")
            _publish(
                session,
                organization_id=organization_id,
                event_type=EVT_TASK_DELAYED,
                aggregate_id=alert_id,
                payload={"alert_id": str(alert_id), **detail},
            )
    report.resolved += await _resolve_missing(session, organization_id, "task_delayed", still_open)


async def sweep_delayed_shipments(
    session: AsyncSession, organization_id: uuid.UUID, report: AlertReport, *, now: datetime
) -> None:
    shipments = await alert_selectors.delayed_shipments(session, organization_id, now=now)
    still_open: set[uuid.UUID] = set()
    for shipment in shipments:
        still_open.add(shipment.id)
        settings = await alert_selectors.resolve_alert_settings(
            session, organization_id, shipment.warehouse_id
        )
        age_minutes = round((now - shipment.created_at).total_seconds() / 60, 1)
        detail = {
            "organization_id": str(organization_id),
            "shipment_id": str(shipment.id),
            "order_id": str(shipment.order_id),
            "warehouse_id": str(shipment.warehouse_id),
            "status": shipment.status,
            "age_minutes": age_minutes,
            "sla_minutes": settings.task_sla_minutes,
            "alert_contact": {"email": settings.alert_email, "phone": settings.alert_phone},
        }
        alert_id, inserted = await _upsert_open(
            session,
            organization_id=organization_id,
            kind="shipment_delayed",
            subject_type="shipment",
            subject_id=shipment.id,
            warehouse_id=shipment.warehouse_id,
            detail=detail,
        )
        report.opened += inserted
        report.refreshed += not inserted
        if inserted:
            report.findings.append(f"shipment_delayed:{shipment.id}")
            _publish(
                session,
                organization_id=organization_id,
                event_type=EVT_SHIPMENT_DELAYED,
                aggregate_id=alert_id,
                payload={"alert_id": str(alert_id), **detail},
            )
    report.resolved += await _resolve_missing(
        session, organization_id, "shipment_delayed", still_open
    )


async def sweep_organization(
    session: AsyncSession, organization_id: uuid.UUID, *, now: datetime | None = None
) -> AlertReport:
    """One organization's whole alert pass: low stock, then delayed tasks, then delayed
    shipments. Runs in the caller's transaction, which must be bound to `organization_id` (every
    table read or written here is tenant-policied)."""
    now = now or datetime.now(UTC)
    report = AlertReport(organization_id=organization_id)
    await sweep_low_stock(session, organization_id, report)
    await sweep_delayed_tasks(session, organization_id, report, now=now)
    await sweep_delayed_shipments(session, organization_id, report, now=now)
    return report


async def resolve_alert(
    session: AsyncSession, organization_id: uuid.UUID, alert_id: uuid.UUID, *, resolved_by: str
) -> OperationalAlert | None:
    """A person dismissing an alert by hand (`POST /operations/alerts/{id}/resolve`) — the same
    row the sweep itself would close, just closed early. Idempotent: resolving an already-resolved
    alert is a no-op, not a 409, since two operators dismissing the same alert is not a conflict
    worth surfacing."""
    alert = await alert_selectors.get_alert(session, organization_id, alert_id)
    if alert is None:
        return None
    if alert.resolved_at is None:
        alert.resolved_at = datetime.now(UTC)
        alert.resolved_by = resolved_by
        await session.flush()
    return alert
