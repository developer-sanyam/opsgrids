"""Entry point for the standup service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Standup service",
    description="PulseBoard's daily check-ins — yesterday, today and blockers, part of the "
    "Atmora platform.",
    routers=[router],
    # A stalled relay is silent; `/health/outbox` makes it visible, counting through the
    # outbox engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
)
