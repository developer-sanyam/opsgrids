"""Entry point for the storefront service. No logic lives here.

The one thing that is not a wiring line is `_startup`, and it is not defensive polish: with no
directory role configured, every anonymous buyer is told the store does not deliver to them,
with HTTP 200, a well-formed body and nothing in a log. A startup assertion is the only thing
standing between that and production (ADR-0010 §4, §13).
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.internal import router as internal_router
from app.api.public import router as public_router
from app.api.routes import router
from app.config import assert_directory_role_configured, settings
from app.cron import DomainSweep
from app.models import OutboxEvent
from platform_core.app import create_app
from platform_core.db import bulkhead_scope, create_bulkhead_engine, get_directory_sessionmaker
from platform_core.supervise import start_background_sweep, stop_background_sweep


async def _startup(app: FastAPI) -> None:
    """Refuse to serve without the bypass reader the anonymous route depends on, then — if
    `Settings.run_sweeps_in_api` is on (the edge box: `storefront-cron` no longer exists as its
    own container, the memory-headroom slice's fix round 2) — start the custom-domain sweep as a
    supervised background task in this same process.

    Runs after the lifespan has opened the engines (including the directory one this sweep's
    own discovery reads through — `create_app` initialises it unconditionally, the same reader
    `resolve_public_store_by_host` and `tls-authorize` already use), so by the time a request
    can arrive the process has either the directory role or no process at all.
    """
    cfg = settings()
    assert_directory_role_configured(cfg)
    app.state.domain_sweep_task = None
    app.state.sweep_engine = None
    if not cfg.run_sweeps_in_api:
        return
    # A one-connection engine of its own (`create_bulkhead_engine`): a claim's probes run while
    # its transaction is open, for up to ~15 s a domain, and the shop's request pool is 3 + 2 —
    # the sweep must not be able to take a fifth of it, or be starved by the buyer traffic.
    engine = create_bulkhead_engine(cfg, name="sweep")
    app.state.sweep_engine = engine
    sweep = DomainSweep(
        discovery_factory=get_directory_sessionmaker(),
        work_scope=bulkhead_scope(engine),
        batch_size=cfg.domain_sweep_batch_size,
        poll_interval=cfg.domain_sweep_poll_interval_seconds,
    )
    app.state.domain_sweep_task = start_background_sweep(
        sweep.run(), name="storefront.domain.sweep"
    )


async def _shutdown(app: FastAPI) -> None:
    await stop_background_sweep(getattr(app.state, "domain_sweep_task", None))
    engine = getattr(app.state, "sweep_engine", None)
    if engine is not None:
        await engine.dispose()


app = create_app(
    settings(),
    title="Storefront service",
    description=(
        "The storefront product's first service, and the platform's **first anonymous read of "
        "tenant data** (ADR-0010). A buyer with no token, no account and no session asks "
        "whether a store delivers to their postal code, and gets a real answer: served with a "
        "promise, served with slots (slice 2), or deliberately not served — with the reason. "
        "The tenant is resolved from a store slug in the request body on a BYPASSRLS, "
        "SELECT-only reader confined to one table, and every statement after that runs "
        "tenant-bound under row-level security. **A check that fails renders as 'we couldn't "
        "check', never as 'we don't deliver here'** — the server never returns an outcome it "
        "did not compute. Behind the public route, the merchant configures zones, maps postal "
        "codes, imports them in bulk, tests one, and reads which postal codes were turned away."
    ),
    routers=[router, public_router, internal_router],
    on_startup=_startup,
    on_shutdown=_shutdown,
    # Redis is a declared dependency because the store-slug lookup is cached in it (ADR-0010
    # §12) — the cache degrades to a query and is never fatal — and because the gateway's
    # anonymous rate limiter in front of the public route fails closed by design.
    redis=True,
    outbox_model=OutboxEvent,
)
