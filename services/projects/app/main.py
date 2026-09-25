"""Entry point for the projects service. No logic lives here."""

from __future__ import annotations

from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Projects service",
    description="The delivery portfolio — projects, milestones and the workflow board, "
    "part of the OpsGrids platform.",
    routers=[router],
    # A stalled relay is silent; `/health/outbox` makes it visible, counting through the
    # outbox engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
)
# Mounted directly, not through `routers=` — that list gets `/api/v1` prefixed
# (`platform_core.app.create_app`), and `internal_router` already carries its own
# `/internal/v1` prefix (`app/api/internal.py`), same as `account`'s and `subscription`'s.
app.include_router(internal_router)
