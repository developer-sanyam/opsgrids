"""Entry point for the product service. No logic lives here, except folding the worker in."""

from __future__ import annotations

from fastapi import FastAPI

from app import consumers
from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app
from platform_core.db import bulkhead_scope, create_bulkhead_engine
from platform_core.logging import get_logger
from platform_core.supervise import (
    run_supervised_forever,
    start_background_sweep,
    stop_background_sweep,
)

log = get_logger("product.main")


async def _start_background_worker(app: FastAPI) -> None:
    """Starts this service's one event consumer, exactly what `bin/worker` (`python -m
    app.consumers`) runs, as a supervised background task inside this process, when
    `Settings.run_consumers_in_api` is on (the edge box: the `product-worker` container no longer
    runs by default). Off by default everywhere else: local development and any other deployment
    keeps running `bin/worker` as its own process unchanged.

    The consumer moves onto its own bulkhead engine (`create_bulkhead_engine`), never the API's
    request pool: a message's transaction must neither take a connection from a request nor be
    starved by one.
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
        log.exception("product.worker.not_started")
        return
    app.state.worker_bulkhead_engine = engine
    app.state.background_worker_task = start_background_sweep(
        run_supervised_forever(
            lambda: {"consumer": consumers.consumer.run()}, name="product.worker"
        ),
        name="product.worker",
    )


async def _stop_background_worker(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "background_worker_task", None))
    engine = getattr(app.state, "worker_bulkhead_engine", None)
    if engine is not None:
        await engine.dispose()


app = create_app(
    settings(),
    title="Product service",
    description=(
        "The registry of what exists to sell and who has activated it. Every 402 in the "
        "platform traces back to a row this service owns."
    ),
    routers=[router],
    outbox_model=OutboxEvent,
    on_startup=_start_background_worker,
    on_shutdown=_stop_background_worker,
)

# Root-mounted and excluded from the public schema: auth-service reads entitlements through
# this while minting a token, at a moment when no tenant context exists yet.
app.include_router(internal_router)
