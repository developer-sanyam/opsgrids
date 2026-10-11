"""The scheduled job-work overdue sweep (Inventory plan Phase 6) — the same discovery/work split
`app.cron.ReconcileSweep`, `app.cron.AlertSweep` and `app.channels.sweep.ChannelSyncSweep` use:
**discovery** runs on the BYPASSRLS outbox role, because its only question — *which organizations
have material overdue at a vendor?* — has no tenant to bind; every read and write that matters
(the order rows, the supplier name, the audit row, the outbox event) runs on the app role inside a
tenant-bound session.

The outbox role reads exactly the columns `_OVERDUE_ORGANIZATIONS_SQL` names and nothing else
(`migrations/env.py`'s `RECONCILE_DISCOVERY_TABLES["job_work_orders"]`). Add a column to that query
without adding it there and PostgreSQL refuses the read, the sweep fails every tick, and nothing
else reports an error — the F11/F12 incident `env.py` describes. `tests/test_jobwork_sweep.py`
runs this discovery as the real outbox role for that reason.

**One notification per order.** `overdue_notified_at` is set in the same transaction as the event,
under the order's row lock (`FOR UPDATE SKIP LOCKED`, so an overlapping pass skips what another is
announcing). The event is published by the relay only after that transaction commits. One
organization's failure is counted and logged, never allowed to stop the next.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.inventory.quantity import format_qty
from app.jobwork import domain, selectors
from app.jobwork.services import EVT_OVERDUE
from app.models import SCHEMA, AuditLog, JobWorkOrder, OutboxEvent
from platform_core.audit import record_audit
from platform_core.events import publish_event
from platform_core.logging import get_logger
from platform_core.supervise import GuardedSweep

log = get_logger("operations.jobwork.sweep")

WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]

#: Once an hour unless told otherwise — an overdue order is a conversation with a vendor, not a
#: page; a day's granularity would be enough, an hour is cheap and lands the message in working
#: hours wherever the due date falls.
DEFAULT_INTERVAL_SECONDS = 3600

ACTOR = {"type": "system", "id": "operations.jobwork.overdue_sweep"}

_OVERDUE_ORGANIZATIONS_SQL = text(
    f"""
    SELECT DISTINCT organization_id FROM {SCHEMA}.job_work_orders
    WHERE status IN ('ISSUED', 'PARTIALLY_RECEIVED')
      AND due_date < :today
      AND overdue_notified_at IS NULL
      AND deleted_at IS NULL
    """  # noqa: S608 — only the SCHEMA constant is interpolated
)


@dataclass(slots=True)
class OverdueSweepReport:
    run_id: uuid.UUID
    organizations: int = 0
    announced: int = 0
    failed: int = 0


async def announce_overdue(
    session: AsyncSession, organization_id: uuid.UUID, *, today: date
) -> int:
    """Announce every overdue, not-yet-announced order of one organization. Returns how many.

    Idempotent: an order already announced (`overdue_notified_at` set) is not selected, and one
    another pass holds is skipped."""
    stmt = (
        select(JobWorkOrder)
        .where(
            JobWorkOrder.organization_id == organization_id,
            JobWorkOrder.status.in_(("ISSUED", "PARTIALLY_RECEIVED")),
            JobWorkOrder.due_date.is_not(None),
            JobWorkOrder.due_date < today,
            JobWorkOrder.overdue_notified_at.is_(None),
            JobWorkOrder.deleted_at.is_(None),
        )
        .order_by(JobWorkOrder.due_date, JobWorkOrder.id)
        .with_for_update(skip_locked=True)
    )
    orders = list((await session.execute(stmt)).scalars().all())
    if not orders:
        return 0
    names = await selectors.supplier_names(
        session, organization_id, [o.supplier_id for o in orders]
    )
    now = datetime.now(UTC)
    for order in orders:
        materials = await selectors.materials_for(session, organization_id, order.id)
        outstanding = sum(
            m.sent_milli - m.received_milli - m.rejected_milli - m.shortage_milli for m in materials
        )
        order.overdue_notified_at = now
        record_audit(
            session,
            AuditLog,
            organization_id=organization_id,
            actor_id=None,
            action="jobwork.order.overdue",
            entity="job_work_order",
            entity_id=order.id,
            after={
                "due_date": order.due_date.isoformat() if order.due_date else None,
                "outstanding": format_qty(outstanding),
            },
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=organization_id,
            event_type=EVT_OVERDUE,
            aggregate_type="job_work_order",
            aggregate_id=order.id,
            payload={
                "organization_id": str(organization_id),
                "order_id": str(order.id),
                "order_number": order.number,
                "supplier_id": str(order.supplier_id),
                "supplier_name": names.get(order.supplier_id, ""),
                "process": order.process,
                "due_date": order.due_date.isoformat() if order.due_date else None,
                "days_overdue": domain.days_overdue(order.status, order.due_date, today),
                "outstanding": format_qty(outstanding),
                "status": order.status,
            },
            actor=ACTOR,
        )
    await session.flush()
    return len(orders)


class JobWorkOverdueSweep(GuardedSweep):
    """One pass finds every organization with overdue material at a vendor and announces each
    order once. Injected session factories and an injectable clock, so a test drives the real
    code against real PostgreSQL with nothing patched."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        today: Callable[[], date] = domain.ist_today,
    ) -> None:
        super().__init__(poll_interval=interval)
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._today = today

    @property
    def _log_name(self) -> str:
        return "operations.jobwork.overdue"

    async def tick(self) -> OverdueSweepReport:
        report = OverdueSweepReport(run_id=uuid.uuid4())
        today = self._today()
        async with self._discovery_factory() as discovery:
            organizations = [
                row[0]
                for row in await discovery.execute(_OVERDUE_ORGANIZATIONS_SQL, {"today": today})
            ]
        report.organizations = len(organizations)

        for organization_id in organizations:
            try:
                async with self._work_scope(organization_id) as session:
                    report.announced += await announce_overdue(
                        session, organization_id, today=today
                    )
            except Exception:
                report.failed += 1
                log.exception(
                    "operations.jobwork.overdue.organization_failed",
                    organization_id=str(organization_id),
                    run_id=str(report.run_id),
                )
                continue

        level = log.error if report.failed else log.info
        level(
            "operations.jobwork.overdue.pass",
            run_id=str(report.run_id),
            organizations=report.organizations,
            announced=report.announced,
            failed=report.failed,
        )
        return report
