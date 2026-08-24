"""Entry point for the support service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Support service",
    description="The support desk — part of the Atmora platform.",
    routers=[router],
    # A stalled relay is silent. `/health/outbox` is what makes it visible, and it counts
    # through the outbox engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
)
