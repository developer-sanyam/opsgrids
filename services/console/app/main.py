"""Entry point for the console service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Operator console service",
    description=(
        "The operator console's read model of commerce: it folds the commerce transition-event "
        "stream (subscription, invoice, credit, usage) into cross-tenant projections and serves "
        "the staff read API on the admin gateway — MRR, trial-conversion, churn, per-org state — "
        "with overrides that delegate to the owning service. Never writes another service's schema."
    ),
    routers=[router],
    # A stalled relay is silent; `/health/outbox` makes it visible, counted through the outbox
    # engine so it is not blind to rows the app role cannot see. Console publishes nothing today,
    # so this stays at zero — which is the honest reading, not a broken one.
    outbox_model=OutboxEvent,
)
