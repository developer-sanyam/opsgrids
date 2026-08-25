"""Entry point for the finance service. No logic lives here."""

from __future__ import annotations

from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Finance service",
    description="Finance service — part of the Atmora platform.",
    routers=[router],
    # A stalled relay is silent, and a stalled relay here means an auto-filing agent never hears
    # that an invoice was issued. `/health/outbox` counts through the outbox engine so it is not
    # blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
)

# Root-mounted and excluded from the public schema: an auto-filing agent drives e-invoice
# registration through this server-to-server, guarded by a service token rather than a principal.
app.include_router(internal_router)
