"""Entry point for the notification service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Notification service",
    description="Templates, automation rules and delivery — part of the OpsGrids platform.",
    routers=[router],
    # A stalled relay is silent, and here it stalls *sending*: messages stage as `pending` and
    # never queue. `/health/outbox` is what makes that visible, counted through the outbox
    # engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
)
