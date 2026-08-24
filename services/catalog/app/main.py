"""Entry point for the catalog service. No logic lives here."""

from __future__ import annotations

from app.api.admin import router as admin_router
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

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
)
