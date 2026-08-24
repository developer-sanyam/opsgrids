"""Entry point for the catalog service. No logic lives here."""

from __future__ import annotations

from app.api.admin import router as admin_router
from app.api.internal import router as internal_router
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

# Root-mounted and excluded from the public schema: subscription resolves a plan version through
# this over a service token when it pins one, before any tenant context of a person's own exists.
app.include_router(internal_router)
