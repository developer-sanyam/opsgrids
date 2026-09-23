"""Entry point for the deal service. No logic lives here."""

from __future__ import annotations

from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Deal service",
    description="Deal service — part of the OpsGrids platform.",
    routers=[router],
)

# The internal API is mounted at the root, outside `/api/v1` and outside the OpenAPI
# document: service-to-service only, guarded by a token addressed to deal by name.
app.include_router(internal_router)
