"""Entry point for the account service. No logic lives here."""

from __future__ import annotations

from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Account service",
    description="Account service — part of the Atmora platform.",
    routers=[router],
)

# Mounted at the root rather than under `/api/v1`: service-to-service only, excluded from
# the public schema, and reachable solely with the internal token. auth-service resolves
# sign-in through it and both gateways resolve permissions through it — this router
# failing takes the platform's front door with it.
app.include_router(internal_router)
