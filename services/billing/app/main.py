"""Entry point for the billing service. No logic lives here."""

from __future__ import annotations

from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Billing service",
    description=(
        "The financial record of truth: append-only invoices frozen at finalize, and an "
        "append-only credit ledger whose balance is a windowed SUM, never a stored column. "
        "All money is integer minor units — no floats anywhere in billing."
    ),
    routers=[router, admin_router],
    outbox_model=OutboxEvent,
)

# Root-mounted and excluded from the public schema: the financial record is written by other
# services (a billing run, an operator delegate), never by a customer's own request.
app.include_router(internal_router)
# Root-mounted beside `/health/outbox`: run drafts never finalized, counted cross-tenant.
app.include_router(health_router)
