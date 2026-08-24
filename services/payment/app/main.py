"""Entry point for the payment service. No logic lives here."""

from __future__ import annotations

from app.api.internal import router as internal_router
from app.api.routes import router
from app.api.webhooks import router as webhooks_router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Payment service",
    description=(
        "The provider-facing edge of commerce: mandates, charges, refunds and the webhook inbox. "
        "Razorpay-first, with the provider seam isolated so no other service knows which processor "
        "took the money — only that a charge was captured or failed. A failed charge is what "
        "suspends a subscription and, in turn, de-entitles a product (ADR-0004 §5)."
    ),
    # The customer info route and the signature-guarded provider webhook are the public surface;
    # the webhook is authenticated by its provider signature, not a token.
    routers=[router, webhooks_router],
    # A stalled relay is silent. `/health/outbox` counts through the outbox engine so it is not
    # blind to the rows the app role cannot see.
    outbox_model=OutboxEvent,
)

# Root-mounted and excluded from the public schema: billing raises charges through this
# server-to-server, guarded by a service token rather than a user principal.
app.include_router(internal_router)
