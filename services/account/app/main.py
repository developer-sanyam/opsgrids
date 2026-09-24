"""Entry point for the account service. No logic lives here, except folding the worker in."""

from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI

from app import consumers
from app.api.admin import router as admin_router
from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from app.cron import DomainRecheckSweep
from app.models import OutboxEvent
from platform_core.app import create_app
from platform_core.cache import init_redis
from platform_core.db import bulkhead_scope, create_bulkhead_engine, get_directory_sessionmaker
from platform_core.logging import get_logger
from platform_core.supervise import (
    run_supervised_forever,
    start_background_sweep,
    stop_background_sweep,
)

log = get_logger("account.main")


async def _start_background_worker(app: FastAPI) -> None:
    """Starts this service's worker loops — the activation consumer and the domain
    re-verification sweep, exactly what `app/worker.py` runs — as one supervised background task
    inside this process, when `Settings.run_consumers_in_api` is on (the edge box: the
    `account-worker` container no longer runs by default — the worker-folding follow-up to the
    memory-headroom slice, which already folded every service's *cron* sweep the same way). Off
    by default everywhere else: local development and any other deployment keeps running
    `bin/worker` as its own process unchanged, and `python -m app.worker`/`app.consumers`/
    `app.cron` all still work standalone.

    Both loops move onto their own bulkhead engine (`create_bulkhead_engine`), never the API's
    request pool: the consumer's per-message transaction and the sweep's per-domain write must
    neither take a connection from a request nor be starved by one. Discovery stays on the
    directory role's own sessionmaker (`get_directory_sessionmaker`) — already opened
    unconditionally by `create_app`'s lifespan, the same BYPASSRLS, SELECT-only reader
    `api/internal.py`'s SSO lookup already uses, so folding this loop in opens no new read path.

    A failure building the group is caught and logged rather than left to fail API startup — the
    same "a background loop's own precondition must not take request traffic down with it"
    reasoning `operations`/`storefront`/`document`'s in-API sweeps already follow.
    """
    app.state.background_worker_task = None
    app.state.worker_bulkhead_engine = None
    cfg = settings()
    if not cfg.run_consumers_in_api:
        return
    try:
        init_redis(cfg.redis_url)
        engine = create_bulkhead_engine(cfg, name="worker")
        consumers.use_session_scope(bulkhead_scope(engine))
        sweep = DomainRecheckSweep(
            discovery_factory=get_directory_sessionmaker(),
            work_scope=bulkhead_scope(engine),
            interval=timedelta(seconds=cfg.domain_recheck_interval_seconds),
            batch_size=cfg.domain_recheck_batch_size,
            poll_interval=cfg.domain_recheck_poll_interval_seconds,
        )
    except Exception:
        log.exception("account.worker.not_started")
        return
    app.state.worker_bulkhead_engine = engine
    app.state.background_worker_task = start_background_sweep(
        run_supervised_forever(
            lambda: {"consumer": consumers.consumer.run(), "domain_recheck": sweep.run()},
            name="account.worker",
        ),
        name="account.worker",
    )


async def _stop_background_worker(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "background_worker_task", None))
    engine = getattr(app.state, "worker_bulkhead_engine", None)
    if engine is not None:
        await engine.dispose()


app = create_app(
    settings(),
    title="Account service",
    description="Account service — part of the OpsGrids platform.",
    routers=[router, admin_router],
    # Redis is not optional here. Every write that changes what somebody may do bumps the
    # organization's permission version, and that bump *raises* rather than degrading —
    # telling an administrator the revocation failed beats reporting success while the
    # person keeps their access. Without this the first role change raises RuntimeError.
    redis=True,
    # A stalled relay is silent. `/health/outbox` is what makes it visible.
    outbox_model=OutboxEvent,
    on_startup=_start_background_worker,
    on_shutdown=_stop_background_worker,
)

# Mounted at the root rather than under `/api/v1`: service-to-service only, excluded from
# the public schema, and reachable solely with the internal token. auth-service resolves
# sign-in through it and both gateways resolve permissions through it — this router
# failing takes the platform's front door with it.
app.include_router(internal_router)
