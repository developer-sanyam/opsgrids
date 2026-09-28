"""Two sweeps, run as their own process (`bin/cron`): the nightly stock reconciliation (ADR-0014
§3) and the operational alert sweep (fulfilment plan §4; F12).

The same discovery/work split as `approval`'s SLA sweep (ADR-0002): **discovery** runs on the
BYPASSRLS outbox role, because its only question — *which organizations hold stock?* — has no
tenant to bind, and as the NOBYPASSRLS app role it would find nobody and report a clean night.
That role holds `SELECT (organization_id)` on the ledger and balances and nothing more
(`migrations/env.py`'s `grant_reconcile_discovery`). **Every read that matters** — the ledger sums,
the balances, the reservations, the pick tasks, the shipments — and every write runs on the app
role inside `session_scope(organization_id=...)`, under the tenant policy.

One organization failing is counted and logged, never allowed to stop the rest of a pass, and one
sweep failing (or being stopped) never touches the other — `run_supervised` ends the whole process
if either loop *returns*, which `GuardedSweep.run()` never does on its own, but each `tick()` still
guards itself exactly the way `ReconcileSweep.tick()` already did.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.services import sweep_organization
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
from platform_core.supervise import GuardedSweep, run_supervised

log = get_logger("operations.cron")

WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]

_ORGANIZATIONS_SQL = text(
    f"""
    SELECT organization_id FROM {SCHEMA}.inventory_balances
    UNION
    SELECT organization_id FROM {SCHEMA}.inventory_ledger
    """  # noqa: S608 — only the SCHEMA constant is interpolated
)

#: Which organizations have anything the alert sweep might care about — low-stock items, open
#: pick tasks, or non-terminal shipments. Broader than `_ORGANIZATIONS_SQL` on purpose: an
#: organization can have open fulfilment work with no inventory drift risk at all (nothing posted
#: to the ledger yet, say) and still needs its tasks and shipments checked.
_ALERT_ORGANIZATIONS_SQL = text(
    f"""
    SELECT organization_id FROM {SCHEMA}.inventory_items WHERE deleted_at IS NULL
    UNION
    SELECT organization_id FROM {SCHEMA}.pick_tasks
    UNION
    SELECT organization_id FROM {SCHEMA}.shipments WHERE deleted_at IS NULL
    """  # noqa: S608 — only the SCHEMA constant is interpolated
)

#: Once a day unless told otherwise.
DEFAULT_INTERVAL_SECONDS = 86_400
#: The alert sweep runs far more often than the nightly reconciliation: a low-stock or delayed-
#: task condition an operator should hear about within minutes, not by the next calendar day.
DEFAULT_ALERT_INTERVAL_SECONDS = 900


@dataclass(slots=True)
class SweepReport:
    run_id: uuid.UUID
    organizations: int = 0
    buckets_checked: int = 0
    findings: int = 0
    new_findings: int = 0
    failed: int = 0


class ReconcileSweep(GuardedSweep):
    """One pass reconciles every organization that holds stock. Injected session factories, so a
    test drives the real code against real PostgreSQL with nothing patched.

    `run()` is `GuardedSweep`'s own (the memory-headroom slice's fix round 2): this sweep now
    also runs as a supervised background task inside the `operations` API process itself
    (`app/main.py`, gated on `Settings.run_sweeps_in_api`), where an unguarded discovery failure
    propagating out of the old inline loop below would have taken the customer-facing API down
    with it — not just this sweep. `reconcile_organization` is read-mostly and append-only
    (findings, never a repair), so two overlapping passes — the standalone `bin/cron` and the
    in-API copy, or two concurrent in-API replicas — cost at most a duplicate finding row, never
    a wrong one; the edge itself runs exactly one `operations` replica, so that never happens
    there in practice.
    """

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
        return "inventory.reconcile"

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


@dataclass(slots=True)
class AlertSweepReport:
    run_id: uuid.UUID
    organizations: int = 0
    opened: int = 0
    refreshed: int = 0
    resolved: int = 0
    failed: int = 0


class AlertSweep(GuardedSweep):
    """One pass checks every organization with any inventory, pick tasks or shipments for a low-
    stock item, a delayed pick task, or a delayed shipment (fulfilment plan §4; F12) — the same
    injected-session-factory shape `ReconcileSweep` uses, so a test drives this against real
    PostgreSQL too. `app.alerts.services.sweep_organization` is read-mostly and idempotent per
    subject (one open row, refreshed not duplicated), so two overlapping passes cost at most a
    duplicate `last_seen_at` touch, never a duplicate alert event.
    """

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        interval: float = DEFAULT_ALERT_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(poll_interval=interval)
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope

    @property
    def _log_name(self) -> str:
        return "operations.alerts"

    async def tick(self) -> AlertSweepReport:
        report = AlertSweepReport(run_id=uuid.uuid4())
        async with self._discovery_factory() as discovery:
            organizations = [row[0] for row in await discovery.execute(_ALERT_ORGANIZATIONS_SQL)]
        report.organizations = len(organizations)
        now = datetime.now(UTC)

        for organization_id in organizations:
            try:
                async with self._work_scope(organization_id) as session:
                    result = await sweep_organization(session, organization_id, now=now)
            except Exception:
                report.failed += 1
                log.exception(
                    "operations.alerts.organization_failed",
                    organization_id=str(organization_id),
                    run_id=str(report.run_id),
                )
                continue
            report.opened += result.opened
            report.refreshed += result.refreshed
            report.resolved += result.resolved

        level = log.error if report.opened or report.failed else log.info
        level(
            "operations.alerts.pass",
            run_id=str(report.run_id),
            organizations=report.organizations,
            opened=report.opened,
            refreshed=report.refreshed,
            resolved=report.resolved,
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

    def work_scope(organization_id: uuid.UUID) -> AbstractAsyncContextManager[AsyncSession]:
        return session_scope(organization_id=organization_id)

    reconcile = ReconcileSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=work_scope,
        interval=cfg.reconcile_interval_seconds,
    )
    alerts = AlertSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=work_scope,
        interval=cfg.alert_interval_seconds,
    )
    # `run_supervised`, not two independent `.run()` calls: either loop returning (it never should
    # — `GuardedSweep.run()` runs forever short of cancellation) or raising ends the process the
    # same way one going down silently must not be allowed to, `bin/cron`'s own container restart
    # then bringing both back rather than leaving one sweep quietly dead.
    await run_supervised(reconcile=reconcile.run(), alerts=alerts.run())


if __name__ == "__main__":
    asyncio.run(main())
