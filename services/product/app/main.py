"""Entry point for the product service. No logic lives here."""

from __future__ import annotations

from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Product service",
    description=(
        "The registry of what exists to sell and who has activated it. Every 402 in the "
        "platform traces back to a row this service owns."
    ),
    routers=[router],
    outbox_model=OutboxEvent,
)

# Root-mounted and excluded from the public schema: auth-service reads entitlements through
# this while minting a token, at a moment when no tenant context exists yet.
app.include_router(internal_router)
