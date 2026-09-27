"""Entry point for the lead service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Lead service",
    description="Lead service — part of the OpsGrids platform.",
    routers=[router],
    # Only the reveal budget (`services.enforce_reveal_budget`) uses Redis. Declared here so the
    # lifespan opens the client and `/health/ready` reports it (`create_app` explains why a
    # service that uses Redis without declaring it is a crash waiting for its first request).
    redis=True,
)
