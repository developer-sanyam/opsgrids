"""Entry point for the operations service."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.cron import ReconcileSweep
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
    register_sweep_health,
    start_background_sweep,
    stop_background_sweep,
)

log = get_logger("operations.main")


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
    app.state.sweep_engine = None
    cfg = settings()
    if not cfg.run_sweeps_in_api:
        return
    try:
        init_outbox_engine(cfg)
        await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)
    except Exception:
        # **The customer-facing API must not fail to start over the sweep's own precondition**
        # (round 3, LOW): the outbox role is this sweep's dependency, not the API's. An API that
        # refuses to boot because reconciliation cannot discover organizations takes stock
        # management down to protect a nightly report. Skip only the sweep, say so at error, and
        # leave the gauge saying so too (`sweep_alive 0`, one failure, never a success) — the
        # loudness `assert_outbox_role` was raising for, without the outage.
        log.exception("inventory.reconcile.not_started", reason="outbox role precondition failed")
        register_sweep_health("inventory.reconcile").record_failure()
        return
    # The sweep's per-tenant transactions run on their own one-connection engine, not the API's
    # request pool (`create_bulkhead_engine`): reconciliation must never take a request's
    # connection, or be starved by them.
    engine = create_bulkhead_engine(cfg, name="sweep")
    app.state.sweep_engine = engine
    sweep = ReconcileSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=bulkhead_scope(engine),
        interval=cfg.reconcile_interval_seconds,
    )
    app.state.reconcile_sweep_task = start_background_sweep(sweep.run(), name="inventory.reconcile")


async def _stop_reconcile_sweep(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "reconcile_sweep_task", None))
    engine = getattr(app.state, "sweep_engine", None)
    if engine is not None:
        await engine.dispose()


app = create_app(
    settings(),
    title="Operations service",
    description="Operations service — part of the OpsGrids platform.",
    routers=[router],
    on_startup=_start_reconcile_sweep,
    on_shutdown=_stop_reconcile_sweep,
)
