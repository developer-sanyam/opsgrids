"""Entry point for the subscription service. No logic lives here."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.api.routes import router
from app.catalog import HttpPlanCatalog
from app.config import settings
from app.models import OutboxEvent
from app.registry import HttpProductRegistry
from platform_core.app import create_app


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


app = create_app(
    settings(),
    title="Subscription service",
    description=(
        "The per-customer commercial lifecycle: trial → active → renew/upgrade/downgrade → "
        "past_due/suspended/cancelled. The state machine where entitlement is decided; every "
        "transition emits a commerce.subscription.* event that product-service projects onto the "
        "activation the 402 gate reads."
    ),
    routers=[router],
    outbox_model=OutboxEvent,
    on_startup=_start,
    on_shutdown=_stop,
)

# Root-mounted and excluded from the public schema: the operator console delegates a suspension
# through this over a service token, with no tenant context of a person's own.
app.include_router(internal_router)

# Root-mounted beside `/health/outbox`: the billing run's backlog, counted cross-tenant.
app.include_router(health_router)
