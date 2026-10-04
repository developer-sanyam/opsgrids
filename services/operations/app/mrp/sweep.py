"""The MRP worker: executes runs a large organization queued (`status = 'QUEUED'`).

The same discovery/work split every operations sweep uses (`app.cron`'s module docstring):
**discovery** runs on the BYPASSRLS outbox role, because its only question — *which organizations
have a queued run?* — has no tenant to bind, and as the NOBYPASSRLS app role it would find nobody
and report an idle night. That role holds `SELECT (organization_id, status)` on `mrp_runs` and
nothing more (`migrations/env.py`, `RECONCILE_DISCOVERY_TABLES`). **Every read that matters** —
the orders, balances, BOMs — and every write runs on the app role in
`session_scope(organization_id)` under the tenant policy.

One organization failing is counted and logged, never allowed to stop the rest of a pass; within an
organization one failing run becomes `FAILED` with its reason (`services.process_queued_runs`).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import SCHEMA
from app.mrp import services
from platform_core.logging import get_logger
from platform_core.supervise import GuardedSweep

log = get_logger("operations.mrp.sweep")

WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]

_QUEUED_ORGANIZATIONS_SQL = text(
    f"""
    SELECT DISTINCT organization_id FROM {SCHEMA}.mrp_runs WHERE status = 'QUEUED'
    """  # noqa: S608 — only the SCHEMA constant is interpolated
)

#: A queued run should start within about half a minute.
DEFAULT_INTERVAL_SECONDS = 30


@dataclass(slots=True)
class MrpSweepReport:
    run_id: uuid.UUID
    organizations: int = 0
    completed: int = 0
    failed_runs: int = 0
    failed: int = 0


class MrpRunSweep(GuardedSweep):
    """One pass executes the queued runs of every organization that has any. Injected session
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
        return "operations.mrp"

    async def tick(self) -> MrpSweepReport:
        report = MrpSweepReport(run_id=uuid.uuid4())
        async with self._discovery_factory() as discovery:
            organizations = [row[0] for row in await discovery.execute(_QUEUED_ORGANIZATIONS_SQL)]
        report.organizations = len(organizations)
        for organization_id in organizations:
            try:
                async with self._work_scope(organization_id) as session:
                    result = await services.process_queued_runs(session, organization_id)
            except Exception:
                report.failed += 1
                log.exception(
                    "operations.mrp.organization_failed",
                    organization_id=str(organization_id),
                    run_id=str(report.run_id),
                )
                continue
            report.completed += result.completed
            report.failed_runs += result.failed
        if report.organizations or report.failed:
            level = log.error if report.failed or report.failed_runs else log.info
            level(
                "operations.mrp.pass",
                run_id=str(report.run_id),
                organizations=report.organizations,
                completed=report.completed,
                failed_runs=report.failed_runs,
                failed=report.failed,
            )
        return report
