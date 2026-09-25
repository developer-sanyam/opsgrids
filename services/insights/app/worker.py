"""The worker process: the event consumer, the snapshot sweep and the backfill runner, side by
side (ADR-0020 §1: "three loops in one process"). Same shape as
`services/approval/app/worker.py` — one asyncio process, several independent loops, combined
through `platform_core.supervise.run_supervised` so any one of them returning or raising ends
the process for the orchestrator to restart, rather than leaving the others running alone
against a half-torn-down deploy.

`app/main.py` folds these same three loops into the API process instead
(`RUN_CONSUMERS_IN_API`, the edge box's own default); this file is what `bin/worker` runs
standalone, for local development and as the `split-workers` rollback (`insights-worker` in
`compose.prod.yml`).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers
from app.backfill import BackfillRunner
from app.config import settings
from app.snapshot import SNAPSHOT_CAPTURES, SnapshotSweep
from app.stock_capture import StockCapture
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
from platform_core.supervise import run_supervised

log = get_logger("insights.worker")


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers and the two sweeps' own action side run NOBYPASSRLS: the per-transaction tenant
    # GUC only means something if row-level security can bind this role. Started on the admin
    # or outbox DSN by mistake, RLS would be silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    init_outbox_engine(cfg)
    # Discovery (which organizations exist, which backfill runs are due) is cross-tenant and
    # must bypass — without the split it runs as the app role, sees no rows, and both loops
    # look like they are running while doing nothing at all (ADR-0002, "Relay stall", the same
    # failure mode applied to a sweep instead of the outbox relay).
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)

    # ADR-0020 §3, step 2: the queue must be bound before the backfill runner's discovery makes
    # its first export call, or an event published mid-backfill is dropped by the topic exchange
    # rather than queued.
    await consumers.ensure_queue_bound(cfg.rabbitmq_url)

    @asynccontextmanager
    async def work_scope(organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with session_scope(organization_id=organization_id) as session:
            yield session

    # I5's pulled stock capture is the one `SnapshotCapture` that needs a live HTTP client
    # (`app/stock_capture.py`) — its lifetime is this process's, closed in the `finally` below.
    async with httpx.AsyncClient() as stock_client:
        snapshot_sweep = SnapshotSweep(
            poll_interval=cfg.snapshot_sweep_poll_seconds,
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=work_scope,
            captures=(*SNAPSHOT_CAPTURES, StockCapture(cfg, stock_client)),
        )
        backfill_runner = BackfillRunner(
            settings=cfg, discovery_factory=get_outbox_sessionmaker(), work_scope=work_scope
        )

        log.info(
            "insights.worker.starting",
            routing_keys=list(consumers.ALL_ROUTING_KEYS),
            sweeps=["snapshot", "backfill"],
        )
        # Either loop returning or raising ends the group; `run_supervised` cancels the other two
        # before this raises, so the orchestrator restarts a process with no orphaned loop still
        # running against it (same reasoning as `services/approval/app/worker.py`).
        await run_supervised(
            consumer=consumers.consumer.run(),
            snapshot=snapshot_sweep.run(),
            backfill=backfill_runner.run(),
        )


if __name__ == "__main__":
    asyncio.run(main())
