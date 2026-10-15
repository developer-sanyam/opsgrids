"""Entry point for the attendance service. No logic lives here, except folding the worker in."""

from __future__ import annotations

from fastapi import FastAPI

from app import consumers
from app.api.audit import router as audit_router
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from app.scope import register as register_scope
from platform_core.app import create_app
from platform_core.db import bulkhead_scope, create_bulkhead_engine
from platform_core.logging import get_logger
from platform_core.supervise import (
    run_supervised_forever,
    start_background_sweep,
    stop_background_sweep,
)

# The HRMS scope resolver is registered at import (routes.py calls it too); doing it here as
# well keeps the wiring visible at the composition root and is idempotent.
register_scope()

log = get_logger("attendance.main")


async def _start_background_worker(app: FastAPI) -> None:
    """Starts this service's one event consumer, exactly what `bin/worker` (`python -m
    app.consumers`) runs, as a supervised background task inside this process, when
    `Settings.run_consumers_in_api` is on (the edge box: the `attendance-worker` container no
    longer runs by default). Off by default everywhere else: local development and any other
    deployment keeps running `bin/worker` as its own process unchanged.

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
        log.exception("attendance.worker.not_started")
        return
    app.state.worker_bulkhead_engine = engine
    app.state.background_worker_task = start_background_sweep(
        run_supervised_forever(
            lambda: {"consumer": consumers.consumer.run()}, name="attendance.worker"
        ),
        name="attendance.worker",
    )


async def _stop_background_worker(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "background_worker_task", None))
    engine = getattr(app.state, "worker_bulkhead_engine", None)
    if engine is not None:
        await engine.dispose()


app = create_app(
    settings(),
    title="Attendance service",
    description="HRMS attendance, shifts and field geo-fencing — part of the OpsGrids platform.",
    routers=[audit_router, router],
    # Redis on, to match the rest of the platform: the app factory opens and readiness-checks
    # exactly the dependencies a service declares. This service does not bump a permission
    # version itself, but keeping the flag consistent means a later write that needs Redis is a
    # one-line change rather than a production RuntimeError.
    redis=True,
    # A stalled relay is silent. `/health/outbox` is what makes it visible, and it counts
    # through the outbox engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
    on_startup=_start_background_worker,
    on_shutdown=_stop_background_worker,
)
