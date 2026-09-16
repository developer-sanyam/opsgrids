"""Entry point for the approval service. No logic lives here."""

from __future__ import annotations

from app.api.routes import router
from app.config import settings
from app.models import OutboxEvent
from platform_core.app import create_app

app = create_app(
    settings(),
    title="Approval service",
    description=(
        "The platform's routing engine for who may say yes, who says it when they do not, and "
        "how the answer gets back to the product that asked (ADR-0006). Slice 1: the read-only "
        "copy of the reporting line (`hierarchy_nodes`) and the resolved chain endpoint. "
        "Requests, steps, delegations and SLA policy land in slice 2."
    ),
    routers=[router],
    outbox_model=OutboxEvent,
)
