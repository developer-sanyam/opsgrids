"""Entry point for the metering service. No logic lives here."""

from __future__ import annotations

from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Metering service",
    description=(
        "The append-only usage sink of commerce: product services record consumption here from "
        "day one with is_billable=false, and billing reads a meter only once a plan prices it."
    ),
    routers=[router],
    # A stalled relay is silent. `/health/outbox` is what makes it visible, and it counts through
    # the outbox engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
)

# Root-mounted and excluded from the public schema: product services post usage through this
# server-to-server, guarded by a service token rather than a user principal.
app.include_router(internal_router)
