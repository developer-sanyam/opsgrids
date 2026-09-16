"""The SLA escalation sweep for approval (ADR-0006 §8). Runs as its own process (`bin/cron`).

Same claim-and-drain shape as `services/subscription/app/cron.py`'s dunning sweep and the outbox
relay itself — ADR-0002's split, applied a third time: **discovery** runs on the BYPASSRLS outbox
role (`get_outbox_sessionmaker`), because it exists to find out *which organizations* have overdue
steps, and there is no tenant to bind while asking; as the NOBYPASSRLS app role it would select
nothing, log nothing, and every overdue step in the platform would sit unescalated forever, looking
exactly like a healthy, idle sweep. **Every write** runs on the app role inside
`session_scope(organization_id=...)`, under the tenant policy — the bypass buys discovery, never
authority.

`SELECT ... FOR UPDATE SKIP LOCKED` in the action transaction (`app/selectors.py`'s
`get_request_for_escalation`) is what makes two concurrent sweepers idempotent: the loser of a race
skips a request another replica already holds, and re-checks the step's own `outcome`/`due_at` and
the request's own `status` under the lock rather than trusting discovery's now-stale read — a step
already escalated (or decided, or no longer due) in the gap between discovery and here is a skip,
never a second escalation.

**One clock, read once per tick, and it is the database's** (ADR-0006 §8's own amendment —
`app/services.py`'s `db_now` carries the full argument for why approval, unlike subscription,
cannot read the app pod's clock: `opened_at`/`due_at`/`closed_at` and the row's own
`server_default=func.now()` timestamps would be written by two different machines, and a CHECK
comparing them becomes a heisenbug on ordinary clock skew). Discovery evaluates `due_at <= now()`
against the database's own clock, read once at the top of `tick`; each action transaction
(`app/services.escalate_step`) then reads its **own**, deliberately later `now()` when it
re-checks the step under the lock — the re-check must ask whether the step is *still* due now, not
whether it was due when discovery ran a moment (or, on a slow tick, much longer) earlier.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
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

log = get_logger("approval.cron")

#: A tenant-bound transaction for one organization. `session_scope` in production; a test
#: substitutes its own so the sweep can be driven without a process-wide engine — the same seam
#: `subscription/app/cron.py`'s `WorkScope` documents.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class SweepReport:
    """What one pass did. Returned so a test can assert on it and the loop can log it."""

    considered: int = 0
    escalated: int = 0
    unassigned: int = 0
    skipped: int = 0
    #: One organization's own escalation raising is not the rest of the tick's problem
    #: (`EscalationSweep._escalate`'s own `try`/`except`) — counted here rather than left to
    #: propagate, because discovery orders every tick's candidates by `due_at` platform-wide
    #: (`app/selectors.py`'s `_DUE_ESCALATION_STEPS_SQL`): an organization whose step keeps
    #: raising is head-of-line on every restart of an unguarded loop, and an exception out of
    #: `tick()` kills the whole process rather than only that one organization's work — a
    #: permanent, platform-wide stall dressed up as a crash loop, not a contained failure.
    failed: int = 0

    @property
    def acted(self) -> int:
        return self.escalated


class EscalationSweep:
    """Drains the due SLA escalations, one pass at a time. See the module docstring.

    Constructed with its two session factories rather than reaching for the module-level engines,
    for the same reason every other seam in this codebase is injected: a test drives real
    PostgreSQL through the same code the process runs, with no clock and no engine patched.
    """

    def __init__(
        self,
        *,
        discovery_factory: async_sessionmaker[AsyncSession],
        work_scope: WorkScope,
        per_org_cap: int = 20,
        batch_size: int = 100,
        poll_interval: float = 60.0,
    ) -> None:
        self._discovery_factory = discovery_factory
        self._work_scope = work_scope
        self._per_org_cap = per_org_cap
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        log.info(
            "approval.sweep.started",
            poll_interval=self._poll_interval,
            per_org_cap=self._per_org_cap,
            batch_size=self._batch_size,
        )
        while not self._stop:
            report = await self.tick()
            if report.acted or report.skipped or report.failed:
                log.info(
                    "approval.sweep.pass",
                    considered=report.considered,
                    escalated=report.escalated,
                    unassigned=report.unassigned,
                    skipped=report.skipped,
                    failed=report.failed,
                )
            await asyncio.sleep(self._poll_interval)

    async def tick(self, now: datetime | None = None) -> SweepReport:
        """One pass: discover due steps, then escalate each under its own tenant binding.

        `now` exists **only** for tests: `main()` below never passes it, so production always
        reads the database's own clock. A test may pass a synthetic instant to exercise an
        SLA-minutes-long ladder in milliseconds against real rows and real locks, without sleeping
        or patching a process-wide clock — the same seam `subscription/app/cron.py`'s `tick`
        offers, for the same reason. It does not weaken "one clock, and it is the database's": the
        override only ever changes what the **discovery** read compares `due_at` against, and
        discovery never writes a row. Every write this function leads to still gets its own
        instant from `app/services.db_now`, called fresh inside `escalate_step`'s own transaction
        — so every row this tick writes carries a database-supplied instant regardless of what a
        test passed here.
        """
        from app import selectors

        async with self._discovery_factory() as session, session.begin():
            if now is not None:
                at = now
            else:
                at = (await session.execute(select(func.now()))).scalar_one()
            due = await selectors.due_escalation_steps(
                session,
                now=at,
                per_org_cap=self._per_org_cap,
                limit=self._batch_size,
            )

        report = SweepReport(considered=len(due))
        for organization_id, request_id in due:
            await self._escalate(organization_id, request_id, report)
        return report

    async def _escalate(
        self, organization_id: uuid.UUID, request_id: uuid.UUID, report: SweepReport
    ) -> None:
        """Act on one step, in its own tenant-bound transaction.

        Discovery orders every tick's candidates by `due_at` **platform-wide**
        (`app/selectors.py`'s `_DUE_ESCALATION_STEPS_SQL`), so a step that keeps raising —
        malformed data an earlier bug wrote, a transient database error, anything — is
        head-of-line on every tick until it is fixed. Left unguarded, that exception would
        propagate out of `tick()`, past every candidate ordered after it, and (via `run()`'s own
        loop) kill the sweep process entirely: a single poisoned step becomes a permanent,
        platform-wide stall that looks like a crash loop rather than one organization's problem.
        Caught, logged with the identifiers a correlation needs and none a leak would (CLAUDE.md:
        "log with correlation and tenant identifiers, never with secrets or personal data" — a
        request id and an organization id are neither), and counted as `SweepReport.failed` so the
        tick moves on to the next candidate and the pass's own log line shows the failure rather
        than silently under-reporting `considered`.
        """
        from app import services

        try:
            async with self._work_scope(organization_id) as session:
                result = await services.escalate_step(
                    session, organization_id=organization_id, request_id=request_id
                )
        except Exception:
            log.exception(
                "approval.sweep.escalation_failed",
                organization_id=str(organization_id),
                request_id=str(request_id),
            )
            report.failed += 1
            return
        if result.escalated:
            report.escalated += 1
            if result.unassigned:
                report.unassigned += 1
        else:
            report.skipped += 1


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Every write this process makes runs on the app role under a bound tenant, so the same
    # assertion the consumers make applies: started on a BYPASSRLS DSN by mistake, row-level
    # security would be silently inert (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery is cross-tenant and must bypass. Without the split it runs as the app role, sees
    # no rows, and every overdue step in the platform stays unescalated forever — the relay's
    # silent stall, in the path that decides who is still allowed to act on a request.
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = EscalationSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
    )
    await sweep.run()


if __name__ == "__main__":
    asyncio.run(main())
