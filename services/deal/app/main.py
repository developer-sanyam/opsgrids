"""Entry point for the deal service. No logic lives here, except folding the worker in."""

from __future__ import annotations

from fastapi import FastAPI

from app import consumers
from app.api.audit import router as audit_router
from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from platform_core.app import create_app
from platform_core.db import bulkhead_scope, create_bulkhead_engine
from platform_core.logging import get_logger
from platform_core.supervise import (
    run_supervised_forever,
    start_background_sweep,
    stop_background_sweep,
)

log = get_logger("deal.main")


async def _start_background_worker(app: FastAPI) -> None:
    """Starts this service's event consumer, exactly what `bin/worker` (`python -m
    app.consumers`) runs, as a supervised background task inside this process, when
    `Settings.run_consumers_in_api` is on (the edge box). Off by default everywhere else.

    The consumer moves onto its own bulkhead engine, never the API's request pool: a message's
    transaction must neither take a connection from a request nor be starved by one.
    """
    app.state.background_worker_task = None
    app.state.worker_bulkhead_engine = None
    cfg = settings()
    if not cfg.run_consumers_in_api:
        return
    try:
        engine = create_bulkhead_engine(cfg, name="worker")
        consumers.use_session_scope(bulkhead_scope(engine))
    except Exception:
        log.exception("deal.worker.not_started")
        return
    app.state.worker_bulkhead_engine = engine
    app.state.background_worker_task = start_background_sweep(
        run_supervised_forever(lambda: {"consumer": consumers.consumer.run()}, name="deal.worker"),
        name="deal.worker",
    )
    log.info("deal.worker.started", cleanup_mode=cfg.crm_conversion_cleanup_mode)


async def _stop_background_worker(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "background_worker_task", None))
    engine = getattr(app.state, "worker_bulkhead_engine", None)
    if engine is not None:
        await engine.dispose()


app = create_app(
    settings(),
    title="Deal service",
    description="Deal service — part of the OpsGrids platform.",
    routers=[audit_router, router],
    on_startup=_start_background_worker,
    on_shutdown=_stop_background_worker,
)

# The internal API is mounted at the root, outside `/api/v1` and outside the OpenAPI
# document: service-to-service only, guarded by a token addressed to deal by name.
app.include_router(internal_router)
