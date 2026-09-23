"""Entry point for the operations service."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.cron import ReconcileSweep
from platform_core.app import create_app
from platform_core.db import (
    assert_outbox_role,
    get_outbox_engine,
    get_outbox_sessionmaker,
    init_outbox_engine,
    session_scope,
)
from platform_core.supervise import start_background_sweep, stop_background_sweep


async def _start_reconcile_sweep(app: FastAPI) -> None:
    """Starts the nightly stock reconciliation as a supervised background task inside this
    process, when `Settings.run_sweeps_in_api` is on (the edge box: `operations-cron` no longer
    exists as its own container — the memory-headroom slice's fix round 2). Off by default
    everywhere else: local development and any other deployment keeps running `bin/cron` as its
    own process unchanged, and `python -m app.cron` still works standalone for debugging it in
    isolation.

    The outbox engine is this sweep's own dependency, not the API's — `create_app` never opens
    one here because `operations` passes no `outbox_model` (it exposes no `/health/outbox`), so
    a service with the sweep turned off never pays for a connection pool it does not use.
    """
    app.state.reconcile_sweep_task = None
    cfg = settings()
    if not cfg.run_sweeps_in_api:
        return
    init_outbox_engine(cfg)
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)
    sweep = ReconcileSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        interval=cfg.reconcile_interval_seconds,
    )
    app.state.reconcile_sweep_task = start_background_sweep(sweep.run())


async def _stop_reconcile_sweep(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "reconcile_sweep_task", None))


app = create_app(
    settings(),
    title="Operations service",
    description="Operations service — part of the OpsGrids platform.",
    routers=[router],
    on_startup=_start_reconcile_sweep,
    on_shutdown=_stop_reconcile_sweep,
)
