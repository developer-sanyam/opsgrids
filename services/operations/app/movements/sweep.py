"""The reservation-expiry sweep (inventory Phase 3) — the same discovery/work split
`app.cron.ReconcileSweep` documents: **discovery** runs on the BYPASSRLS outbox role, because its
only question — *which organizations hold an active reservation past its `expires_at`?* — has no
tenant to bind (as the NOBYPASSRLS app role it would find nobody, and a sweep that releases nothing
looks exactly like one with nothing to release). That role holds column-level `SELECT` on exactly
`organization_id, status, expires_at` of `inventory_reservations` and nothing more
(`migrations/env.py`'s `RECONCILE_DISCOVERY_TABLES`). **Every read and write that matters** — the
reservations, the balances, the releases — runs on the app role inside a tenant-bound session.

One organization failing is counted and logged, never allowed to stop the pass.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import SCHEMA
from app.movements.reservations import expire_due
from platform_core.logging import get_logger
from platform_core.supervise import GuardedSweep

log = get_logger("operations.reservations.sweep")

WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]

_DUE_ORGANIZATIONS_SQL = text(
    f"""
    SELECT DISTINCT organization_id FROM {SCHEMA}.inventory_reservations
    WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= now()
    """  # noqa: S608 — only the SCHEMA constant is interpolated
)

#: Every five minutes unless told otherwise: a hold is released within minutes of lapsing, and the
#: pass is one indexed query when nothing is due.
DEFAULT_INTERVAL_SECONDS = 300


@dataclass(slots=True)
class SweepReport:
    run_id: uuid.UUID
    organizations: int = 0
    due: int = 0
    expired: int = 0
    skipped: int = 0
    failed: int = 0


class ReservationExpirySweep(GuardedSweep):
    """One pass releases every lapsed reservation, organization by organization. Injected session
    factories, so a test drives the real code against real PostgreSQL with nothing patched."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        interval: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(poll_interval=interval)
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope

    @property
    def _log_name(self) -> str:
        return "operations.reservations.expiry"

    async def tick(self) -> SweepReport:
        report = SweepReport(run_id=uuid.uuid4())
        async with self._discovery_factory() as discovery:
            organizations = [row[0] for row in await discovery.execute(_DUE_ORGANIZATIONS_SQL)]
        report.organizations = len(organizations)

        for organization_id in organizations:
            try:
                async with self._work_scope(organization_id) as session:
                    result = await expire_due(session, organization_id)
            except Exception:
                report.failed += 1
                log.exception(
                    "operations.reservations.expiry.organization_failed",
                    organization_id=str(organization_id),
                    run_id=str(report.run_id),
                )
                continue
            report.due += result.due
            report.expired += result.expired
            report.skipped += result.skipped

        level = log.error if report.failed else log.info
        level(
            "operations.reservations.expiry.pass",
            run_id=str(report.run_id),
            organizations=report.organizations,
            due=report.due,
            expired=report.expired,
            skipped=report.skipped,
            failed=report.failed,
        )
        return report
