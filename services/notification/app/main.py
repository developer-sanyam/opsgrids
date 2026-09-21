"""Entry point for the notification service. No logic lives here."""

from __future__ import annotations

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
    routers=[router, engage_router],
    # A stalled relay is silent, and here it stalls *sending*: messages stage as `pending` and
    # never queue. `/health/outbox` is what makes that visible, counted through the outbox
    # engine so it is not blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
)

# Meta's webhooks, mounted at the root rather than under `/api/v1`: this is the provider's
# endpoint, not public API, and it authenticates by signature rather than by token.
app.include_router(webhooks_router)
