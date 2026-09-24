"""Entry point for the payment service. No logic lives here, except folding the worker in."""

from __future__ import annotations

from fastapi import FastAPI

from app import consumers
from app.api.internal import router as internal_router
from app.api.routes import router
from app.api.webhooks import router as webhooks_router
from app.config import settings
from app.cron import build_sweep
from app.models import OutboxEvent
from platform_core.app import create_app
from platform_core.db import (
    assert_outbox_role,
    bulkhead_scope,
    create_bulkhead_engine,
    get_outbox_engine,
    init_outbox_engine,
)
from platform_core.logging import get_logger
from platform_core.supervise import (
    run_supervised_forever,
    start_background_sweep,
    stop_background_sweep,
)

log = get_logger("payment.main")


async def _start_background_worker(app: FastAPI) -> None:
    """Starts this service's worker loops — the charge consumers and the stuck-charge sweep,
    exactly what `app/worker.py` runs — as one supervised background task inside this process,
    when `Settings.run_consumers_in_api` is on (the edge box: the `payment-worker` container no
    longer runs by default). Off by default everywhere else: local development and any other
    deployment keeps running `bin/worker` as its own process unchanged.

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
        log.exception("payment.worker.not_started", reason="outbox role precondition failed")
        return
    try:
        engine = create_bulkhead_engine(cfg, name="worker")
        consumers.use_session_scope(bulkhead_scope(engine))
        sweep = build_sweep(work_scope=bulkhead_scope(engine))
    except Exception:
        log.exception("payment.worker.not_started")
        return
    app.state.worker_bulkhead_engine = engine
    app.state.background_worker_task = start_background_sweep(
        run_supervised_forever(
            lambda: {"consumer": consumers.consumer.run(), "stuck_charge": sweep.run()},
            name="payment.worker",
        ),
        name="payment.worker",
    )


async def _stop_background_worker(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "background_worker_task", None))
    engine = getattr(app.state, "worker_bulkhead_engine", None)
    if engine is not None:
        await engine.dispose()


app = create_app(
    settings(),
    title="Payment service",
    description=(
        "The provider-facing edge of commerce: mandates, charges, refunds and the webhook inbox. "
        "Razorpay-first, with the provider seam isolated so no other service knows which processor "
        "took the money — only that a charge was captured or failed. A failed charge is what "
        "suspends a subscription and, in turn, de-entitles a product (ADR-0004 §5)."
    ),
    # The customer info route and the signature-guarded provider webhook are the public surface;
    # the webhook is authenticated by its provider signature, not a token.
    routers=[router, webhooks_router],
    # A stalled relay is silent. `/health/outbox` counts through the outbox engine so it is not
    # blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
    on_startup=_start_background_worker,
    on_shutdown=_stop_background_worker,
)

# Root-mounted and excluded from the public schema: billing raises charges through this
# server-to-server, guarded by a service token rather than a user principal.
app.include_router(internal_router)
