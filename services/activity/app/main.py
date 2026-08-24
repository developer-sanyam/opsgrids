"""Entry point for the activity service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Activity service",
    description="Activity service — part of the Atmora platform.",
    routers=[router],
)
