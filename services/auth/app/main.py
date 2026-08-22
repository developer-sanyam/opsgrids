"""Entry point for the auth service.

Two things are attached to app state at startup: the signing key and the directory client.
Both are process-wide, and both are replaceable in tests through `dependency_overrides` —
the sign-in flow is worth exercising without a key on disk or a second service running.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router, wellknown
from app.config import settings
from app.directory import HttpDirectory
from app.keys import active_key
from app.models import OutboxEvent
from platform_core.app import create_app

cfg = settings()


async def _startup(application: FastAPI) -> None:
    # Fails loudly here rather than on the first sign-in attempt: a service that cannot
    # sign tokens should not report itself healthy.
    application.state.signing_key = active_key(cfg.jwt_private_key_path)
    application.state.directory = HttpDirectory(cfg.account_service_url, cfg)


async def _shutdown(application: FastAPI) -> None:
    directory = getattr(application.state, "directory", None)
    if isinstance(directory, HttpDirectory):
        await directory.aclose()


app = create_app(
    cfg,
    title="Auth service",
    description=(
        "Credentials, sessions and tokens. The only service in the platform that holds a "
        "private signing key — everything else verifies through JWKS."
    ),
    routers=[router],
    on_startup=_startup,
    on_shutdown=_shutdown,
    # A stalled relay is silent. `/health/outbox` is what makes it visible.
    outbox_model=OutboxEvent,
)

# Mounted at the root: `/.well-known/jwks.json` is where clients look, not under /api/v1.
app.include_router(wellknown)
