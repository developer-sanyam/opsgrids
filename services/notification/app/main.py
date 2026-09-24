"""Entry point for the notification service. No logic lives here, except folding the worker in."""

from __future__ import annotations

from fastapi import FastAPI

from app import consumers, dispatcher
from app.api.engage import public_router as engage_public_router
from app.api.engage import router as engage_router
from app.api.routes import router
from app.api.webhooks import router as webhooks_router
from app.config import settings
from app.cron import CampaignDriver
from app.models import OutboxEvent
from app.targets import make_resolver as make_target_resolver
from app.transports import CAMPAIGN_SMTP_EXECUTOR
from app.transports import build as build_transports
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

log = get_logger("notification.main")


async def _start_background_worker(app: FastAPI) -> None:
    """Starts this service's worker loops — the two event consumers and the campaign send
    driver, exactly what `app/worker.py` runs — as one supervised background task inside this
    process, when `Settings.run_consumers_in_api` is on (the edge box: the
    `notification-worker` container no longer runs by default — the worker-folding follow-up to
    the memory-headroom slice, which already folded this service's own `*-cron` driver the same
    way). Off by default everywhere else: local development and any other deployment keeps
    running `bin/worker` as its own process unchanged.

    Every loop moves onto one bulkhead engine (`create_bulkhead_engine`), never the API's
    request pool. The driver's discovery stays on the outbox role's own sessionmaker, asserted
    here the same way `operations`'s in-API sweep already asserts it — a failed precondition
    skips only this background group and never stops the API itself from serving.
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
        log.exception("notification.worker.not_started", reason="outbox role precondition failed")
        return
    try:
        engine = create_bulkhead_engine(cfg, name="worker")
        consumers.use_session_scope(bulkhead_scope(engine))
        dispatcher.use_session_scope(bulkhead_scope(engine))
        consumers.register(make_target_resolver(cfg))
        # Raises rather than downgrading to `log` when a real transport is named without
        # credentials, so a misconfigured deploy fails here instead of going quiet.
        dispatcher.register(build_transports(cfg), cfg)
        driver = CampaignDriver(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=bulkhead_scope(engine),
            batch_size=cfg.engage_campaign_batch_size,
            lease_seconds=cfg.engage_campaign_claim_lease_seconds,
            pause_retry_seconds=cfg.engage_campaign_pause_retry_seconds,
            poll_interval=cfg.engage_campaign_poll_interval_seconds,
        )
    except Exception:
        log.exception("notification.worker.not_started")
        return
    app.state.worker_bulkhead_engine = engine
    # `CampaignDriver` is a `GuardedSweep` and self-registers its own `sweep_alive{sweep=
    # "notification.cron"}` gauge at construction (unchanged since before this fold) — but only
    # `start_background_sweep` ever flips a gauge's `alive` bit, and here that call wraps the
    # *group* (`notification.worker`), not this one sweep directly, so `driver.health.alive`
    # would otherwise stay `False` forever while the driver is, in fact, running inside the
    # group — indistinguishable on `/metrics` from a driver that never started. Toggled by hand
    # here and reset in `_stop_background_worker`, tracking the group's own lifetime.
    driver.health.alive = True
    app.state.worker_health_flags = [driver.health]
    app.state.background_worker_task = start_background_sweep(
        run_supervised_forever(
            lambda: {
                "events": consumers.consumer.run(),
                "dispatch": dispatcher.consumer.run(),
                "campaign_driver": driver.run(),
            },
            name="notification.worker",
        ),
        name="notification.worker",
    )


async def _stop_background_worker(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "background_worker_task", None))
    for health in getattr(app.state, "worker_health_flags", []):
        health.alive = False
    engine = getattr(app.state, "worker_bulkhead_engine", None)
    if engine is not None:
        await engine.dispose()
    # `cancel_futures=True` drops anything still queued; `wait=False` does not block shutdown on
    # an SMTP call already in flight on one of the pool's two threads — the same trade
    # `app/worker.py`'s own shutdown makes, and for the same reason (its docstring has the
    # duplicate-send window this does not widen). A no-op when the sweep was never started: the
    # executor is created lazily by the transport that first uses it.
    CAMPAIGN_SMTP_EXECUTOR.shutdown(wait=False, cancel_futures=True)


app = create_app(
    settings(),
    title="Notification service",
    description="Templates, automation rules and delivery — part of the OpsGrids platform.",
    # Two routers because this process serves two products: `notification`, the platform's
    # transactional plumbing, and `engage`, the sending identity a customer buys. They are
    # gated on different entitlements and built from different catalogues, so they stay two
    # routers rather than one with a prefix.
    routers=[router, engage_router, engage_public_router],
    # A stalled relay is silent, and here it stalls *sending*: messages stage as `pending` and
    # never queue. `/health/outbox` is what makes that visible, counted through the outbox
    # engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
    # `services.send_test` rate-limits per organization through `platform_core.cache`, which
    # fails closed if Redis was never opened — so this process must open it, the same as every
    # other service that carries a limiter or a cache.
    redis=True,
    on_startup=_start_background_worker,
    on_shutdown=_stop_background_worker,
)

# Meta's webhooks, mounted at the root rather than under `/api/v1`: this is the provider's
# endpoint, not public API, and it authenticates by signature rather than by token.
app.include_router(webhooks_router)
