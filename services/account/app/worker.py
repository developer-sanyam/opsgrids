"""The worker process: the event consumer and the domain re-verification sweep, side by side.

Combined into one process for the same reason `services/subscription/app/worker.py` and
`services/notification/app/worker.py` combine theirs (ADR-0013's memory-budget erratum,
2026-09-23): two containers at ~192 MB apiece for two processes that are each idle almost all
the time is a cost this box does not have to pay. One asyncio process, two independent loops,
`gather`ed so either one raising brings the whole process down for the orchestrator to restart
rather than leaving the other running alone against a half-dead deploy.

The consumer and the sweep have never shared a database role or a schedule assumption, and
combining them changes neither: the consumer runs NOBYPASSRLS per event (`app.consumers.main`'s
own role assertion, unchanged); the sweep still splits the BYPASSRLS directory role's discovery
from the NOBYPASSRLS app role's per-domain write every `poll_interval` (`app.cron.
DomainRecheckSweep`, unchanged).

`app/cron.py` keeps its own `main()` and remains directly runnable (`python -m app.cron`) for
running the sweep alone; `bin/cron` still points at it. Only `bin/worker` moved.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app import consumers
from app.config import settings
from app.cron import DomainRecheckSweep
from platform_core.cache import init_redis
from platform_core.db import (
    assert_app_role,
    get_directory_sessionmaker,
    init_directory_engine,
    init_engine,
    session_scope,
)
from platform_core.logging import get_logger

log = get_logger("account.worker")


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if row-level
    # security can bind this role. Started on the admin or outbox DSN by mistake, RLS would be
    # silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    # Granting the admin role to an owner bumps the organization's permission version, and that
    # bump raises rather than degrading — losing it would leave stale tokens working.
    init_redis(cfg.redis_url)
    # The sweep's discovery role. Costs no new grant: `opsgrids_directory` already reads
    # `org_domains` for the internal SSO lookup (`app.cron`'s own module docstring).
    init_directory_engine(cfg)

    sweep = DomainRecheckSweep(
        discovery_factory=get_directory_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        interval=timedelta(seconds=cfg.domain_recheck_interval_seconds),
        batch_size=cfg.domain_recheck_batch_size,
        poll_interval=cfg.domain_recheck_poll_interval_seconds,
    )

    log.info("account.worker.starting", queue="account.events", sweep="domain-recheck")
    # `gather` rather than sequential awaits: either loop returning is a failure, and the first
    # exception should bring the process down so the orchestrator restarts it, rather than
    # leaving one half running against a half-dead deploy (same reasoning as
    # subscription/app/worker.py and notification/app/worker.py).
    await asyncio.gather(consumers.consumer.run(), sweep.run())


if __name__ == "__main__":
    asyncio.run(main())
