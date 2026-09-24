"""Entry point for the notification service. No logic lives here."""

from __future__ import annotations

from app.api.engage import public_router as engage_public_router
from app.api.engage import router as engage_router
from app.api.routes import router
from app.api.webhooks import router as webhooks_router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Notification service",
    description="Templates, automation rules and delivery — part of the OpsGrids platform.",
    # Two routers because this process serves two products: `notification`, the platform's
    # transactional plumbing, and `engage`, the sending identity a customer buys. They are
    # gated on different entitlements and built from different catalogues, so they stay two
    # routers rather than one with a prefix.
    routers=[router, engage_router, engage_public_router],
    # A stalled relay is silent, and here it stalls *sending*: messages stage as `pending` and
    # never queue. `/health/outbox` is what makes that visible, counted through the outbox
    # engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
    # `services.send_test` rate-limits per organization through `platform_core.cache`, which
    # fails closed if Redis was never opened — so this process must open it, the same as every
    # other service that carries a limiter or a cache.
    redis=True,
)

# Meta's webhooks, mounted twice on purpose. Under `/api/v1` because that is the path the
# gateway forwards *unchanged* (`/api/v1/webhooks/whatsapp`, the URL Meta is configured with,
# through Caddy and app-gateway): mounted only at the root, the gateway's request landed on a
# path this service does not have and every delivery 404'd even once it was let through. At the
# root as well, for a developer pointing a tunnel straight at this service, which is the URL the
# tests and `docs/whatsapp/webhooks.md` grew up with. Either way it is the provider's endpoint,
# not public API, and it authenticates by signature rather than by token.
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(webhooks_router)
