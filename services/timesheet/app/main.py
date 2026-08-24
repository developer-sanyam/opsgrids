"""Entry point for the timesheet service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Timesheet service",
    description="Timesheet service — part of the Atmora platform.",
    routers=[router],
)
