"""Entry point for the catalog service. No logic lives here."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.console_internal import router as console_internal_router
from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from app.registry import HttpProductRegistry
from platform_core.app import create_app


async def _start(application: FastAPI) -> None:
    cfg = settings()
    application.state.product_registry = HttpProductRegistry(cfg.product_service_url, cfg)


async def _stop(application: FastAPI) -> None:
    registry = getattr(application.state, "product_registry", None)
    if isinstance(registry, HttpProductRegistry):
        await registry.aclose()


app = create_app(
    settings(),
    title="Catalog service",
    description=(
        "The definitional layer of commerce: plans, immutable priced versions, and features "
        "— what can be sold. Global platform vocabulary, read by everything downstream and "
        "authored only by staff."
    ),
    routers=[router, admin_router],
    outbox_model=OutboxEvent,
    on_startup=_start,
    on_shutdown=_stop,
)

# Root-mounted and excluded from the public schema: subscription resolves a plan version through
# this over a service token when it pins one, before any tenant context of a person's own exists.
app.include_router(internal_router)
# Root-mounted and excluded from the public schema, like `internal_router` — console's narrow
# clone-and-publish endpoint for a staff-set bespoke price (app/api/console_internal.py).
app.include_router(console_internal_router)
