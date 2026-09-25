"""Entry point for the insights service. No logic lives here, except folding the worker in."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers
from app.api.routes import router
from app.backfill import BackfillRunner
from app.config import settings
from app.models import OutboxEvent
from app.snapshot import SnapshotSweep
from platform_core.app import create_app
from platform_core.db import (
    assert_outbox_role,
    bulkhead_scope,
    create_bulkhead_engine,
    get_outbox_engine,
    get_outbox_sessionmaker,
    init_outbox_engine,
)
from platform_core.logging import get_logger
from platform_core.supervise import (
    run_supervised_forever,
    start_background_sweep,
    stop_background_sweep,
)

log = get_logger("insights.main")


async def _start_background_worker(app: FastAPI) -> None:
    """Starts this service's three worker loops — the event consumer, the hourly snapshot sweep
    and the backfill runner, exactly what `app/worker.py` runs — as one supervised background
    task inside this process, when `Settings.run_consumers_in_api` is on (the edge box's own
    default: the `insights-worker` container does not run there — ADR-0020 §1, the same
    worker-folding pattern `account`/`approval`/`console` already use). Off by default
    everywhere else: local development and any other deployment keeps running `bin/worker`
    (`app/worker.py`) as its own process unchanged.

    All three loops move onto one bulkhead engine (`create_bulkhead_engine`), never the API's
    request pool — a message's or a sweep tick's transaction must neither take a connection from
    a request nor be starved by one. The bulkhead is also what the two sweeps' cross-tenant
    discovery reads from, on the outbox role's own sessionmaker, asserted here the same way
    `operations`'s in-API sweep already asserts it: a failed precondition skips only this
    background group and never stops the API itself from serving.
    """
    app.state.background_worker_task = None
    app.state.worker_bulkhead_engine = None
    cfg = settings()
    if not cfg.run_consumers_in_api:
        return
    try:
        init_outbox_engine(cfg)
        await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)
    except Exception:
        log.exception("insights.worker.not_started", reason="outbox role precondition failed")
        return
    try:
        engine = create_bulkhead_engine(cfg, name="worker")
        consumers.use_session_scope(bulkhead_scope(engine))

        @asynccontextmanager
        async def work_scope(organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
            async with bulkhead_scope(engine)(organization_id) as session:
                yield session

        snapshot_sweep = SnapshotSweep(
            poll_interval=cfg.snapshot_sweep_poll_seconds,
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=work_scope,
        )
        backfill_runner = BackfillRunner(
            settings=cfg, discovery_factory=get_outbox_sessionmaker(), work_scope=work_scope
        )
        # ADR-0020 §3, step 2: bound before the backfill runner's discovery makes its first
        # export call, or an event published mid-backfill is dropped by the topic exchange
        # rather than queued.
        await consumers.ensure_queue_bound(cfg.rabbitmq_url)
    except Exception:
        log.exception("insights.worker.not_started")
        return
    app.state.worker_bulkhead_engine = engine
    # Each sweep is a `GuardedSweep` and self-registers its own `sweep_alive{sweep=...}` gauge at
    # construction — but only `start_background_sweep` ever flips a gauge's `alive` bit, and here
    # that call wraps the *group* (`insights.worker`), not either sweep directly, so both would
    # otherwise stay `False` forever while genuinely running inside the group (the same fix
    # `approval/app/main.py` applies to its own single sweep).
    snapshot_sweep.health.alive = True
    backfill_runner.health.alive = True
    app.state.worker_health_flags = [snapshot_sweep.health, backfill_runner.health]
    app.state.background_worker_task = start_background_sweep(
        run_supervised_forever(
            lambda: {
                "consumer": consumers.consumer.run(),
                "snapshot": snapshot_sweep.run(),
                "backfill": backfill_runner.run(),
            },
            name="insights.worker",
        ),
        name="insights.worker",
    )


async def _stop_background_worker(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "background_worker_task", None))
    for health in getattr(app.state, "worker_health_flags", []):
        health.alive = False
    engine = getattr(app.state, "worker_bulkhead_engine", None)
    if engine is not None:
        await engine.dispose()


app = create_app(
    settings(),
    title="Insights service",
    description=(
        "The cross-product insights projection behind the role dashboards (ADR-0020): a "
        "read-only, per-organization history of what the source services only ever hold as "
        "current state. A leaf — no service ever calls it, and it reads no other schema "
        "directly, only each source's own internal export route. I1: the schema, tenancy, the "
        "API shell (every widget returns its real state; no figures until I2-I9 land their own "
        "fact tables), the resumable backfill runner and the hourly snapshot sweep."
    ),
    routers=[router],
    outbox_model=OutboxEvent,
    on_startup=_start_background_worker,
    on_shutdown=_stop_background_worker,
)
