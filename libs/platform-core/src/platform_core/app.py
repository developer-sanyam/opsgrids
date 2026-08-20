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
from sqlalchemy import text

from platform_core.config import ServiceSettings
from platform_core.db import dispose_engine, get_engine, init_engine
from platform_core.errors import register_error_handlers
from platform_core.logging import RequestContextMiddleware, configure_logging, get_logger


def build_health_router(settings: ServiceSettings) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health", summary="Liveness — is the process up?")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.service_name}

    @router.get("/health/ready", summary="Readiness — can it serve traffic?")
    async def ready() -> dict[str, Any]:
        """Route traffic on this; restart on /health.

        Conflating the two restarts a healthy process that is merely waiting on a
        dependency.
        """
        checks: dict[str, str] = {}
        healthy = True
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc}"
            healthy = False
        return {"status": "ok" if healthy else "degraded", "checks": checks}

    return router


def create_app(
    settings: ServiceSettings,
    *,
    title: str,
    description: str = "",
    routers: list[APIRouter] | None = None,
    on_startup: Callable[[FastAPI], Any] | None = None,
    on_shutdown: Callable[[FastAPI], Any] | None = None,
) -> FastAPI:
    configure_logging(
        settings.service_name, level=settings.log_level, json_output=settings.log_json
    )
    log = get_logger(settings.service_name)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_engine(settings)
        log.info("service.starting", environment=settings.environment)
        if on_startup:
            await _maybe_await(on_startup(app))
        yield
        if on_shutdown:
            await _maybe_await(on_shutdown(app))
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
    app.include_router(build_health_router(settings))
    for router in routers or []:
        app.include_router(router, prefix="/api/v1")

    return app


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
