"""Entry point for the document service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Document service",
    description="Document service — part of the OpsGrids platform.",
    routers=[router],
)
