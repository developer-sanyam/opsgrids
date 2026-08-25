"""Entry point for the auth service.

Three things are attached to app state at startup: the signing key, the directory client and
the identity-provider client single sign-on talks to. All are process-wide, and all are
replaceable in tests through `dependency_overrides` — the sign-in flows are worth exercising
without a key on disk, a second service running, or an identity provider on the network.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router, wellknown
from app.config import settings
from app.directory import HttpDirectory
from app.entitlements import HttpEntitlements
from app.keys import active_key
from app.models import OutboxEvent
from app.oidc import HttpIdpClient
from platform_core.app import create_app

cfg = settings()


async def _startup(application: FastAPI) -> None:
    # Fails loudly here rather than on the first sign-in attempt: a service that cannot
    # sign tokens should not report itself healthy.
    application.state.signing_key = active_key(cfg.jwt_private_key_path)
    application.state.directory = HttpDirectory(cfg.account_service_url, cfg)
    application.state.entitlements = HttpEntitlements(cfg.product_service_url, cfg)
    application.state.idp = HttpIdpClient()


async def _shutdown(application: FastAPI) -> None:
    directory = getattr(application.state, "directory", None)
    if isinstance(directory, HttpDirectory):
        await directory.aclose()
    entitlements = getattr(application.state, "entitlements", None)
    if isinstance(entitlements, HttpEntitlements):
        await entitlements.aclose()
    idp = getattr(application.state, "idp", None)
    if isinstance(idp, HttpIdpClient):
        await idp.aclose()


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
    # Declared since single sign-on landed. The pending-authorization store is Redis, it
    # fails closed rather than degrading (a state check that cannot run must not be read as
    # "not replayed"), so a process whose Redis is gone cannot complete an SSO sign-in and
    # should say so on readiness rather than accepting traffic and refusing it.
    redis=True,
    # A stalled relay is silent. `/health/outbox` is what makes it visible.
    outbox_model=OutboxEvent,
)

# Mounted at the root: `/.well-known/jwks.json` is where clients look, not under /api/v1.
app.include_router(wellknown)
