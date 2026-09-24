"""The worker process: the filing consumer and the filing-reclaim sweep, side by side.

Finance's reclaim sweep (`app/cron.py`) is what finishes compliance work a crash or an outage left
half-done (D3): an invoice whose `filing_requested` never arrived, a `pending` filing whose
scheduled retry was lost, an IRN withdrawal or e-Way generation/withdrawal whose claim committed
but whose portal answer was never recorded. The consumer marks a message processed in its *claim*
transaction, so the queue never retries a crashed claim — the sweep is the only thing that does.
It therefore has to run wherever finance runs, and it does so here, inside `finance-worker`,
rather than in a `finance-cron` container of its own: no new container, no change to the memory
budget, the same fold `subscription`, `account` and `approval` made (ADR-0013's memory-budget
erratum).

The two loops are supervised (`app/supervise.py`): either one returning or raising ends the
process non-zero so the orchestrator restarts it, and the sweep's own per-tick guard
(`FilingReclaimSweep.run`) means a failing tick backs off instead of ending anything — so a bad
pass never takes the consumer down with it.

The consumer runs NOBYPASSRLS with a per-message tenant GUC; the sweep splits BYPASSRLS discovery
(the outbox role's column grants) from NOBYPASSRLS action. `bin/cron` (`python -m app.cron`) still
runs the sweep alone for local debugging.
"""

from __future__ import annotations

import asyncio

from app import consumers
from app.config import settings
from app.cron import FilingReclaimSweep
from app.supervise import run_supervised
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

log = get_logger("finance.worker")


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if row-level
    # security can bind this role (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # The sweep's discovery is cross-tenant and must bypass; on the app role it would see nothing
    # and every lost filing would stay lost, silently (app/cron.py's module docstring).
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = FilingReclaimSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        poll_interval=cfg.filing_reclaim_poll_interval_seconds,
    )
    log.info("finance.worker.starting", queue="finance.events", sweep="filing_reclaim")
    await run_supervised(consumer=consumers.consumer.run(), filing_reclaim=sweep.run())


if __name__ == "__main__":
    asyncio.run(main())
