"""Entry point for the employee service. No logic lives here."""

from __future__ import annotations

from app.api.audit import router as audit_router
from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Employee service",
    description="The HRMS Employee Master — part of the OpsGrids platform.",
    routers=[audit_router, router],
    # Redis on, to match account and the rest of the platform: the app factory opens and
    # readiness-checks exactly the dependencies a service declares. This service does not bump
    # a permission version itself, but keeping the flag consistent means a later write that
    # needs Redis is a one-line change rather than a production RuntimeError.
    redis=True,
    # A stalled relay is silent. `/health/outbox` is what makes it visible, and it counts
    # through the outbox engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
)

# Mounted at the root rather than under `/api/v1`: service-to-service only, excluded from the
# public schema, and reachable solely with the internal token (ADR-0006 §2.1).
app.include_router(internal_router)
