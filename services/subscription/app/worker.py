"""The worker process: the payment-event consumer and the dunning sweep, side by side.

Two containers used to run this service's background work — `subscription-worker` on
`app.consumers` and `subscription-cron` on `app.cron` — at 192 MB apiece (ADR-0013's
memory-budget erratum, 2026-09-23) for two processes that are each idle almost all the time.
They are combined here the same way `notification/app/worker.py` already combines its two
consumers: one asyncio process, two independent loops, `gather`ed so either one raising brings
the whole process down for the orchestrator to restart, rather than leaving the other running
alone against a half-dead deploy.

The consumer and the sweep have never shared a database role or a schedule assumption, and
combining them changes neither: the consumer runs NOBYPASSRLS per event
(`app.consumers.main`'s own role assertion, unchanged); the sweep still splits BYPASSRLS
discovery from NOBYPASSRLS action every `poll_interval` (`app.cron.DunningSweep`, unchanged).
This file only starts both in one process instead of two.

`app/cron.py` keeps its own `main()` and remains directly runnable (`python -m app.cron`) for
debugging the sweep in isolation on a laptop; `bin/cron` still points at it. Only `bin/worker`
moved.
"""

from __future__ import annotations

import asyncio

from app import consumers
from app.config import settings
from app.cron import DunningSweep
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

log = get_logger("subscription.worker")


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if row-level
    # security can bind this role. Started on the admin or outbox DSN by mistake, RLS would be
    # silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery is cross-tenant and must bypass. Without the split it runs as the app role, sees
    # no rows, and every past-due customer in the platform keeps their product for free forever
    # (app/cron.py's own module docstring).
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = DunningSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
    )

    log.info("subscription.worker.starting", queue="subscription.events", sweep="dunning")
    # `gather` rather than sequential awaits: either loop returning is a failure, and the first
    # exception should bring the process down so the orchestrator restarts it, rather than
    # leaving one half running against a half-dead deploy (same reasoning as
    # notification/app/worker.py).
    await asyncio.gather(consumers.consumer.run(), sweep.run())


if __name__ == "__main__":
    asyncio.run(main())
