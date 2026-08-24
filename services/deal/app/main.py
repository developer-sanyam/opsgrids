"""Entry point for the deal service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Deal service",
    description="Deal service — part of the Atmora platform.",
    routers=[router],
)
