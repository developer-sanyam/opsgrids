"""The nightly stock reconciliation sweep (ADR-0014 §3). Runs as its own process (`bin/cron`).

The same discovery/work split as `approval`'s SLA sweep (ADR-0002): **discovery** runs on the
BYPASSRLS outbox role, because its only question — *which organizations hold stock?* — has no
tenant to bind, and as the NOBYPASSRLS app role it would find nobody and report a clean night.
That role holds `SELECT (organization_id)` on the ledger and balances and nothing more
(`migrations/env.py`'s `grant_reconcile_discovery`). **Every read that matters** — the ledger sums,
the balances, the reservations — and every write runs on the app role inside
`session_scope(organization_id=...)`, under the tenant policy.

One organization failing is counted and logged, never allowed to stop the rest of the pass.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.inventory.reconcile import reconcile_organization
from app.models import SCHEMA
from platform_core.db import (
    assert_app_role,
    assert_outbox_role,
    get_outbox_engine,
    get_outbox_sessionmaker,
    init_engine,
    init_outbox_engine,
    session_scope,
)
from platform_core.logging import get_logger

log = get_logger("operations.cron")

WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]

_ORGANIZATIONS_SQL = text(
    f"""
    SELECT organization_id FROM {SCHEMA}.inventory_balances
    UNION
    SELECT organization_id FROM {SCHEMA}.inventory_ledger
    """  # noqa: S608 — only the SCHEMA constant is interpolated
)

#: Once a day unless told otherwise.
DEFAULT_INTERVAL_SECONDS = 86_400


@dataclass(slots=True)
class SweepReport:
    run_id: uuid.UUID
    organizations: int = 0
    buckets_checked: int = 0
    findings: int = 0
    new_findings: int = 0
    failed: int = 0


class ReconcileSweep:
    """One pass reconciles every organization that holds stock. Injected session factories, so a
    test drives the real code against real PostgreSQL with nothing patched."""

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        interval: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._interval = interval
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        log.info("inventory.reconcile.started", interval=self._interval)
        while not self._stop:
            await self.tick()
            await asyncio.sleep(self._interval)

    async def tick(self) -> SweepReport:
        report = SweepReport(run_id=uuid.uuid4())
        async with self._discovery_factory() as discovery:
            organizations = [row[0] for row in await discovery.execute(_ORGANIZATIONS_SQL)]
        report.organizations = len(organizations)

        for organization_id in organizations:
            try:
                async with self._work_scope(organization_id) as session:
                    result = await reconcile_organization(
                        session, organization_id, run_id=report.run_id
                    )
            except Exception:
                report.failed += 1
                log.exception(
                    "inventory.reconcile.organization_failed",
                    organization_id=str(organization_id),
                    run_id=str(report.run_id),
                )
                continue
            report.buckets_checked += result.buckets_checked
            report.findings += len(result.findings)
            report.new_findings += len(result.new)

        level = log.error if report.findings or report.failed else log.info
        level(
            "inventory.reconcile.pass",
            run_id=str(report.run_id),
            organizations=report.organizations,
            buckets_checked=report.buckets_checked,
            findings=report.findings,
            new_findings=report.new_findings,
            failed=report.failed,
        )
        return report


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Every read and write that matters runs on the app role under a bound tenant; started on a
    # BYPASSRLS DSN by mistake, the tenant policy would be silently inert (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = ReconcileSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        interval=cfg.reconcile_interval_seconds,
    )
    await sweep.run()


if __name__ == "__main__":
    asyncio.run(main())
