"""Entry point for the console service. No logic lives here, except folding the worker in."""

from __future__ import annotations

from fastapi import FastAPI

from app import consumers
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from app.price import make_resolver
from platform_core.app import create_app
from platform_core.db import bulkhead_scope, create_bulkhead_engine
from platform_core.logging import get_logger
from platform_core.supervise import (
    run_supervised_forever,
    start_background_sweep,
    stop_background_sweep,
)

log = get_logger("console.main")


async def _start_background_worker(app: FastAPI) -> None:
    """Starts this service's one event consumer, exactly what `bin/worker` (`python -m
    app.consumers`) runs, as a supervised background task inside this process, when
    `Settings.run_consumers_in_api` is on (the edge box: the `console-worker` container no longer
    runs by default — the worker-folding follow-up to the memory-headroom slice, which already
    folded every other service's own worker the same way). Off by default everywhere else: local
    development and any other deployment keeps running `bin/worker` as its own process unchanged.

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
        consumers.register(make_resolver(cfg))
    except Exception:
        log.exception("console.worker.not_started")
        return
    app.state.worker_bulkhead_engine = engine
    app.state.background_worker_task = start_background_sweep(
        run_supervised_forever(
            lambda: {"consumer": consumers.consumer.run()}, name="console.worker"
        ),
        name="console.worker",
    )


async def _stop_background_worker(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "background_worker_task", None))
    engine = getattr(app.state, "worker_bulkhead_engine", None)
    if engine is not None:
        await engine.dispose()


app = create_app(
    settings(),
    title="Operator console service",
    description=(
        "The operator console's read model of commerce: it folds the commerce transition-event "
        "stream (subscription, invoice, credit, usage) into cross-tenant projections and serves "
        "the staff read API on the admin gateway — MRR, trial-conversion, churn, per-org state — "
        "with overrides that delegate to the owning service. Never writes another service's schema."
    ),
    routers=[router],
    # A stalled relay is silent; `/health/outbox` makes it visible, counted through the outbox
    # engine so it is not blind to rows the app role cannot see. Console publishes nothing today,
    # so this stays at zero — which is the honest reading, not a broken one.
    outbox_model=OutboxEvent,
    on_startup=_start_background_worker,
    on_shutdown=_stop_background_worker,
)
