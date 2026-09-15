"""The service factory — one runnable FastAPI app with the platform wired in.

A new service costs minutes rather than hours because health, config, database session,
error shape, structured logging and request context all arrive assembled.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text

from platform_core.cache import close_redis, get_redis, init_redis
from platform_core.config import ServiceSettings
from platform_core.db import (
    assert_app_role,
    dispose_directory_engine,
    dispose_engine,
    dispose_outbox_engine,
    get_engine,
    get_outbox_engine,
    init_directory_engine,
    init_engine,
    init_outbox_engine,
)
from platform_core.errors import register_error_handlers
from platform_core.logging import RequestContextMiddleware, configure_logging, get_logger


def build_health_router(
    settings: ServiceSettings,
    *,
    database: bool = True,
    redis: bool = False,
    outbox_model: type[Any] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health", summary="Liveness — is the process up?")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.service_name}

    @router.get("/health/ready", summary="Readiness — can it serve traffic?")
    async def ready() -> dict[str, Any]:
        """Route traffic on this; restart on /health.

        Conflating the two restarts a healthy process that is merely waiting on a
        dependency.

        Only the dependencies a service actually declares are checked. A gateway owns no
        tables, so asking it for a database is asking a question whose answer means nothing
        — and a readiness probe that reports on something the service does not use is worse
        than no probe, because it will eventually fail for a reason nobody can act on.
        """
        checks: dict[str, str] = {}
        healthy = True

        if database:
            try:
                async with get_engine().connect() as conn:
                    await conn.execute(text("SELECT 1"))
                checks["database"] = "ok"
            except Exception as exc:
                checks["database"] = f"error: {exc}"
                healthy = False

        if redis:
            # Not cosmetic. Redis is a hard dependency of any write that changes what
            # somebody may do — the permission-version bump raises rather than degrading —
            # and of the gateway's rate limiter and staleness check, both of which fail
            # closed. Without this, a service whose Redis is gone reports itself ready and
            # then refuses or crashes every one of those requests.
            try:
                await get_redis().ping()
                checks["redis"] = "ok"
            except Exception as exc:
                checks["redis"] = f"error: {exc}"
                healthy = False

        return {"status": "ok" if healthy else "degraded", "checks": checks}

    if outbox_model is not None:

        @router.get("/health/outbox", summary="Outbox depth and age — scrape this")
        async def outbox() -> dict[str, Any]:
            """How far behind the relay is. **Alert on `oldest_seconds`.**

            A stalled relay is the platform's quietest failure. It selects, finds nothing,
            logs nothing, sleeps, and loops — `attempts` never increments and `last_error`
            stays null, so the process looks healthy indefinitely while the outbox grows
            without bound and every consumer downstream starves.

            Deliberately **not** part of readiness. A backlog is not a reason to take the
            service out of rotation: refusing traffic would stop new events being written
            without helping drain the ones already queued, which makes the incident worse.
            This is a metric to alert on, not a health state.

            Counted through the outbox engine, not the application one. The app role is
            NOBYPASSRLS and this request binds no tenant, so through it `pending` would be
            zero whatever the table holds — the probe that exists to expose the stall would
            be wired to the same blind spot that causes it.
            """
            table = outbox_model.__table__
            stmt = select(
                func.count().filter(table.c.published_at.is_(None)),
                func.extract(
                    "epoch",
                    func.now()
                    - func.min(table.c.created_at).filter(table.c.published_at.is_(None)),
                ),
            ).select_from(table)

            async with get_outbox_engine().connect() as conn:
                pending, oldest = (await conn.execute(stmt)).one()

            return {
                "pending": int(pending or 0),
                "oldest_seconds": float(oldest) if oldest is not None else 0.0,
            }

    return router


def create_app(
    settings: ServiceSettings,
    *,
    title: str,
    description: str = "",
    routers: list[APIRouter] | None = None,
    on_startup: Callable[[FastAPI], Any] | None = None,
    on_shutdown: Callable[[FastAPI], Any] | None = None,
    database: bool = True,
    redis: bool = False,
    outbox_model: type[Any] | None = None,
) -> FastAPI:
    """Assemble a service.

    `database` and `redis` declare what this service depends on. The lifespan opens and
    closes exactly those, and readiness checks exactly those.

    Declaring them here rather than in each service's own startup hook is deliberate. A
    service that used Redis without initialising it raised `RuntimeError` on the first
    request that touched it — and its tests passed, because they injected a fake client
    directly. Making the dependency a parameter means forgetting it is a visible omission
    at the call site instead of a crash in production.
    """
    configure_logging(
        settings.service_name, level=settings.log_level, json_output=settings.log_json
    )
    log = get_logger(settings.service_name)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if database:
            engine = init_engine(settings)
            # Before the first request, not lazily on it: a process that would serve every
            # tenant's rows to every tenant must never report itself healthy. The check
            # lives here rather than in `init_engine` because relays and scripts share
            # that function and are meant to bypass RLS (ADR-0002 §2). The directory
            # engine below is exempt for the same reason: bypassing RLS is its purpose.
            await assert_app_role(engine, environment=settings.environment)
            init_directory_engine(settings)
            # Exempt from the guard too, and for the same reason again: the outbox engine
            # connects as `opsgrids_outbox`, BYPASSRLS so `/health/outbox` can count every
            # tenant's unpublished rows. Opened only by a service that owns an outbox.
            if outbox_model is not None:
                init_outbox_engine(settings)
        if redis:
            init_redis(settings.redis_url)
        log.info(
            "service.starting",
            environment=settings.environment,
            database=database,
            redis=redis,
        )
        if on_startup:
            await _maybe_await(on_startup(app))
        yield
        if on_shutdown:
            await _maybe_await(on_shutdown(app))
        if redis:
            await close_redis()
        if database:
            await dispose_outbox_engine()
            await dispose_directory_engine()
            await dispose_engine()
        log.info("service.stopped")

    app = FastAPI(
        title=title,
        description=description,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
        swagger_ui_parameters={"defaultModelsExpandDepth": 0},
    )
    # How `get_principal` and `require_service_token` reach configuration. An app not
    # built here must set this itself or authentication raises AttributeError.
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Correlation-ID"],
    )

    register_error_handlers(app)
    app.include_router(
        build_health_router(settings, database=database, redis=redis, outbox_model=outbox_model)
    )
    for router in routers or []:
        app.include_router(router, prefix="/api/v1")

    return app


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
