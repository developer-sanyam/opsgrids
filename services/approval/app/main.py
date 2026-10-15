"""Entry point for the approval service. No logic lives here, except folding the worker in."""

from __future__ import annotations

from fastapi import FastAPI

from app import consumers
from app.api.audit import router as audit_router
from app.api.routes import router
from app.config import settings
from app.cron import EscalationSweep
from app.models import OutboxEvent
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

log = get_logger("approval.main")


async def _start_background_worker(app: FastAPI) -> None:
    """Starts this service's worker loops — the hierarchy/regularization consumer and the SLA
    escalation sweep, exactly what `app/worker.py` runs — as one supervised background task
    inside this process, when `Settings.run_consumers_in_api` is on (the edge box: the
    `approval-worker` container no longer runs by default — the worker-folding follow-up to the
    memory-headroom slice, which already folded this service's own `*-cron` sweep the same way).
    Off by default everywhere else: local development and any other deployment keeps running
    `bin/worker` as its own process unchanged.

    Both loops move onto one bulkhead engine (`create_bulkhead_engine`), never the API's request
    pool. The sweep's discovery stays on the outbox role's own sessionmaker, asserted here the
    same way `operations`'s in-API sweep already asserts it — a failed precondition skips only
    this background group and never stops the API itself from serving.
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
        log.exception("approval.worker.not_started", reason="outbox role precondition failed")
        return
    try:
        engine = create_bulkhead_engine(cfg, name="worker")
        consumers.use_session_scope(bulkhead_scope(engine))
        sweep = EscalationSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=bulkhead_scope(engine),
        )
    except Exception:
        log.exception("approval.worker.not_started")
        return
    app.state.worker_bulkhead_engine = engine
    # `EscalationSweep` is a `GuardedSweep` and self-registers its own `sweep_alive{sweep=
    # "approval.sweep"}` gauge at construction — but only `start_background_sweep` ever flips
    # a gauge's `alive` bit, and here that call wraps the *group* (`approval.worker`), not this
    # one sweep directly, so `sweep.health.alive` would otherwise stay `False` forever while the
    # sweep is, in fact, running inside the group. Toggled by hand here and reset in
    # `_stop_background_worker`, tracking the group's own lifetime.
    sweep.health.alive = True
    app.state.worker_health_flags = [sweep.health]
    app.state.background_worker_task = start_background_sweep(
        run_supervised_forever(
            lambda: {"consumer": consumers.consumer.run(), "sla_escalation": sweep.run()},
            name="approval.worker",
        ),
        name="approval.worker",
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
    title="Approval service",
    description=(
        "The platform's routing engine for who may say yes, who says it when they do not, and "
        "how the answer gets back to the product that asked (ADR-0006). Slice 1: the read-only "
        "copy of the reporting line (`hierarchy_nodes`) and the resolved chain endpoint. "
        "Slice 2: opening and deciding a `hrms.time.regularization` request — routing, "
        "delegation applied at open, the inbox, and the product's application-state return leg. "
        "No SLA clock yet: every step opens with `due_at = NULL` until slice 4."
    ),
    routers=[audit_router, router],
    outbox_model=OutboxEvent,
    on_startup=_start_background_worker,
    on_shutdown=_stop_background_worker,
)
