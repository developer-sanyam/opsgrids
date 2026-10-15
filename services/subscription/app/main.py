"""Entry point for the subscription service. No logic lives here, except folding the worker in."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from fastapi import FastAPI

from app import consumers
from app.api.audit import router as audit_router
from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.api.routes import router
from app.billing_run import BillingRunSweep, HttpBilling, HttpPayment, billing_run_loop
from app.catalog import HttpPlanCatalog
from app.config import settings
from app.cron import DunningSweep
from app.models import OutboxEvent
from app.registry import HttpProductRegistry
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

log = get_logger("subscription.main")


async def _start(application: FastAPI) -> None:
    cfg = settings()
    application.state.catalog = HttpPlanCatalog(cfg.catalog_service_url, cfg)
    application.state.product_registry = HttpProductRegistry(cfg.product_service_url, cfg)


async def _stop(application: FastAPI) -> None:
    catalog = getattr(application.state, "catalog", None)
    if isinstance(catalog, HttpPlanCatalog):
        await catalog.aclose()
    registry = getattr(application.state, "product_registry", None)
    if isinstance(registry, HttpProductRegistry):
        await registry.aclose()


async def _start_background_worker(app: FastAPI) -> None:
    """Starts this service's worker loops — the payment-event consumer, the dunning sweep and,
    when enabled, the billing run — exactly what `app/worker.py` runs, as one supervised
    background task inside this process, when `Settings.run_consumers_in_api` is on (the edge
    box: the `subscription-worker` container no longer runs by default — the worker-folding
    follow-up to the memory-headroom slice, which already folded this service's own `*-cron`
    sweep the same way). Off by default everywhere else: local development and any other
    deployment keeps running `bin/worker` as its own process unchanged.

    Every loop moves onto one bulkhead engine (`create_bulkhead_engine`), never the API's request
    pool: the consumer's per-message transaction and each sweep's per-tenant write must neither
    take a connection from a request nor be starved by one. Discovery for both sweeps stays on
    the outbox role's own sessionmaker, asserted here the same way `operations`'s in-API sweep
    already asserts it — a failed precondition (the role misconfigured) skips only this
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
        log.exception("subscription.worker.not_started", reason="outbox role precondition failed")
        return
    try:
        engine = create_bulkhead_engine(cfg, name="worker")
        consumers.use_session_scope(bulkhead_scope(engine))
        sweep = DunningSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=bulkhead_scope(engine),
        )
        billing_run = BillingRunSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=bulkhead_scope(engine),
            billing=HttpBilling(cfg.billing_service_url, cfg),
            payment=HttpPayment(cfg.payment_service_url, cfg),
            poll_interval=cfg.billing_run_poll_interval,
            not_before=cfg.billing_run_not_before,
        )
    except Exception:
        log.exception("subscription.worker.not_started")
        return
    app.state.worker_bulkhead_engine = engine

    def _build_loops() -> dict[str, Coroutine[Any, Any, Any]]:
        loops: dict[str, Coroutine[Any, Any, Any]] = {
            "payment_consumer": consumers.consumer.run(),
            "dunning_sweep": sweep.run(),
        }
        # Off unless enabled, and refused without a cutover — the same gate `app/worker.py`
        # applies. Re-checked on every restart attempt, not just the first, so turning the run
        # on/off (a config change, not a code change) takes effect the next time the group is
        # rebuilt rather than only at the next deploy.
        run_loop = billing_run_loop(cfg, billing_run)
        if run_loop is not None:
            loops["billing_run"] = run_loop
        return loops

    app.state.background_worker_task = start_background_sweep(
        run_supervised_forever(_build_loops, name="subscription.worker"),
        name="subscription.worker",
    )


async def _stop_background_worker(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "background_worker_task", None))
    engine = getattr(app.state, "worker_bulkhead_engine", None)
    if engine is not None:
        await engine.dispose()


async def _startup(application: FastAPI) -> None:
    await _start(application)
    await _start_background_worker(application)


async def _shutdown(application: FastAPI) -> None:
    await _stop_background_worker(application)
    await _stop(application)


app = create_app(
    settings(),
    title="Subscription service",
    description=(
        "The per-customer commercial lifecycle: trial → active → renew/upgrade/downgrade → "
        "past_due/suspended/cancelled. The state machine where entitlement is decided; every "
        "transition emits a commerce.subscription.* event that product-service projects onto the "
        "activation the 402 gate reads."
    ),
    routers=[audit_router, router],
    outbox_model=OutboxEvent,
    on_startup=_startup,
    on_shutdown=_shutdown,
)

# Root-mounted and excluded from the public schema: the operator console delegates a suspension
# through this over a service token, with no tenant context of a person's own.
app.include_router(internal_router)

# Root-mounted beside `/health/outbox`: the billing run's backlog, counted cross-tenant.
app.include_router(health_router)
