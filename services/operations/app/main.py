"""Entry point for the operations service."""

from __future__ import annotations

from fastapi import FastAPI

from app import consumers
from app.api.routes import router
from app.channels.sweep import ChannelSyncSweep
from app.config import settings
from app.cron import AlertSweep, ReconcileSweep
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
    run_supervised_forever,
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


async def _start_alert_sweep(app: FastAPI) -> None:
    """Starts the operational alert sweep (fulfilment plan §4; F12) as a supervised background
    task, the same shape and the same `Settings.run_sweeps_in_api` gate `_start_reconcile_sweep`
    uses — a separate task, not folded into that one, so one sweep's own failure (or a future
    independent restart) never touches the other's schedule."""
    app.state.alert_sweep_task = None
    app.state.alert_sweep_engine = None
    cfg = settings()
    if not cfg.run_sweeps_in_api:
        return
    try:
        init_outbox_engine(cfg)
        await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)
    except Exception:
        log.exception("operations.alerts.not_started", reason="outbox role precondition failed")
        register_sweep_health("operations.alerts").record_failure()
        return
    engine = create_bulkhead_engine(cfg, name="alert-sweep")
    app.state.alert_sweep_engine = engine
    sweep = AlertSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=bulkhead_scope(engine),
        interval=cfg.alert_interval_seconds,
    )
    app.state.alert_sweep_task = start_background_sweep(sweep.run(), name="operations.alerts")


async def _stop_alert_sweep(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "alert_sweep_task", None))
    engine = getattr(app.state, "alert_sweep_engine", None)
    if engine is not None:
        await engine.dispose()


async def _start_channel_sync_sweep(app: FastAPI) -> None:
    """The channel order-import/inventory-push sweep (Fulfilment plan F11), folded into this
    process on the same `Settings.run_sweeps_in_api` flag and the identical shape
    `_start_reconcile_sweep` uses just above — its own bulkhead engine (never the reconcile
    sweep's, and never the request pool), a failed outbox-role precondition skips only this
    sweep, and it is visible on `/metrics` under its own name (`channels.sync`)."""
    app.state.channel_sweep_task = None
    app.state.channel_sweep_engine = None
    cfg = settings()
    if not cfg.run_sweeps_in_api:
        return
    try:
        init_outbox_engine(cfg)
        await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)
    except Exception:
        log.exception("channels.sync.not_started", reason="outbox role precondition failed")
        register_sweep_health("channels.sync").record_failure()
        return
    engine = create_bulkhead_engine(cfg, name="channel-sweep")
    app.state.channel_sweep_engine = engine
    sweep = ChannelSyncSweep(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=bulkhead_scope(engine),
        interval=cfg.channel_sync_interval_seconds,
    )
    app.state.channel_sweep_task = start_background_sweep(sweep.run(), name="channels.sync")


async def _stop_channel_sync_sweep(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "channel_sweep_task", None))
    engine = getattr(app.state, "channel_sweep_engine", None)
    if engine is not None:
        await engine.dispose()


async def _start_background_worker(app: FastAPI) -> None:
    """Starts this service's two event consumers (`app/consumers.py` — the fulfilment offer-
    expiry sweep and the order-cancellation cascade, ADR-0025 §3/§1), exactly what `bin/worker`
    (`python -m app.consumers`) runs, as one supervised background task inside this process, when
    `Settings.run_consumers_in_api` is on (MAJOR 2, security review — `operations` had no
    `*-worker` container and no in-API flag at all, so neither consumer ever ran in production
    regardless of how correct the handler code was; this is the exact worker-folding pattern
    eleven other services already use — `services/leave/app/main.py`'s own
    `_start_background_worker` is the template this mirrors). Off by default everywhere else:
    local development and any other deployment keeps running `bin/worker` as its own process
    unchanged.

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
        # Same engine, so `dispatch_shipment_transitioned`'s raw session shares this one
        # bulkhead with the other three handlers' ambient-transaction one (`bulkhead_scope`'s
        # own docstring: "two background loops... still an order below the request pool").
        consumers.use_raw_session_scope(consumers.bulkhead_raw_scope(engine))
    except Exception:
        log.exception("operations.worker.not_started")
        return
    app.state.worker_bulkhead_engine = engine
    app.state.background_worker_task = start_background_sweep(
        run_supervised_forever(
            lambda: {"consumer": consumers.consumer.run()}, name="operations.worker"
        ),
        name="operations.worker",
    )


async def _stop_background_worker(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "background_worker_task", None))
    engine = getattr(app.state, "worker_bulkhead_engine", None)
    if engine is not None:
        await engine.dispose()


async def _startup(app: FastAPI) -> None:
    await _start_reconcile_sweep(app)
    await _start_alert_sweep(app)
    await _start_channel_sync_sweep(app)
    await _start_background_worker(app)


async def _shutdown(app: FastAPI) -> None:
    await _stop_background_worker(app)
    await _stop_alert_sweep(app)
    await _stop_channel_sync_sweep(app)
    await _stop_reconcile_sweep(app)


app = create_app(
    settings(),
    title="Operations service",
    description="Operations service — part of the OpsGrids platform.",
    routers=[router],
    on_startup=_startup,
    on_shutdown=_shutdown,
)
