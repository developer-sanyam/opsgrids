"""Entry point for the attendance service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from app.scope import register as register_scope
from platform_core.app import create_app

# The HRMS scope resolver is registered at import (routes.py calls it too); doing it here as
# well keeps the wiring visible at the composition root and is idempotent.
register_scope()

app = create_app(
    settings(),
    title="Attendance service",
    description="HRMS attendance, shifts and field geo-fencing — part of the Atmora platform.",
    routers=[router],
    # Redis on, to match the rest of the platform: the app factory opens and readiness-checks
    # exactly the dependencies a service declares. This service does not bump a permission
    # version itself, but keeping the flag consistent means a later write that needs Redis is a
    # one-line change rather than a production RuntimeError.
    redis=True,
    # A stalled relay is silent. `/health/outbox` is what makes it visible, and it counts
    # through the outbox engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
)
