"""Entry point for the itsm service. No logic lives here."""

from __future__ import annotations

from app.api.audit import router as audit_router
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="ITSM service",
    description="The IT service desk and CMDB — incidents, service requests and assets, "
    "part of the OpsGrids platform.",
    routers=[audit_router, router],
    # A stalled relay is silent; `/health/outbox` makes it visible, counting through the
    # outbox engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
)
