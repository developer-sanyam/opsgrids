"""Production entrypoint: the event consumer and the due-job sweep in one process.

`bin/worker` runs this, and `bin/cron` still runs `app.cron` alone (and `make worker`/`make cron`
still run `app.consumers`/`app.cron` alone) for local development and debugging — this module
only exists because the edge box is RAM-bound (`infrastructure/edge/compose.prod.yml`'s
`x-background` fuse) and this service's two background roles are both light enough, and
independent enough of each other, to share one process rather than pay for a second idle
container: the consumer only ever inserts a row, the sweep only ever fires or purges one, and
neither blocks the other for longer than one small transaction.

Both role assertions run once, up front, exactly as `app/consumers.py:main` and
`app/cron.py:main` each do alone — this process holds both the app engine (consumer writes,
sweep firing) and the outbox engine (sweep discovery), so it must pass both checks before either
loop starts.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app.config import settings
from app.consumers import consumer
from app.cron import Sweep
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

log = get_logger("scheduler.worker")


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    sweep = Sweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        retention=timedelta(days=cfg.scheduled_job_retention_days),
    )
    log.info("scheduler.worker.started")
    await asyncio.gather(consumer.run(), sweep.run())


if __name__ == "__main__":
    asyncio.run(main())
