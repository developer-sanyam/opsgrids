"""Entry point for the projects service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Projects service",
    description="Projects service — part of the Atmora platform.",
    routers=[router],
)
