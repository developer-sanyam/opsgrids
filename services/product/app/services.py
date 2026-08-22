"""Writes for product.

Business rules, audit rows and outbox rows. The change, its audit entry and any event it emits
all commit or all roll back — the tenant chokepoint owns the transaction, so nothing here opens
one of its own.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import Activation, AuditLog, OutboxEvent
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem, NotFoundProblem
from platform_core.events import publish_event

EVT_ACTIVATED = "core.product.activated"
EVT_DEACTIVATED = "core.product.deactivated"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id),
    }


async def activate(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    product_key: str,
    trial_days: int | None = None,
    request: Request | None = None,
) -> Activation:
    """Turn a product on for this organization.

    Reactivating updates the existing row rather than inserting a second one. The unique
    constraint enforces that, but doing it deliberately here means a returning customer keeps
    the feature switches they configured the first time instead of silently starting over.
    """
    product = await selectors.get_product(session, product_key)
    if product is None:
        raise NotFoundProblem(f"No product named {product_key!r}")
    if product.status == "soon":
        # Refused rather than allowed-and-broken: activating something with no service behind
        # it produces a customer who has paid for 404s.
        raise BusinessRuleProblem(
            f"{product.name} is not available yet. It can be activated once it ships."
        )

    existing = await selectors.get_activation(session, ctx.organization_id, product_key)
    trial_ends = datetime.now(UTC) + timedelta(days=trial_days) if trial_days is not None else None
    before = {"status": existing.status} if existing else None

    if existing is not None:
        existing.status = "trial" if trial_days else "active"
        existing.trial_ends_at = trial_ends
        existing.deactivated_at = None
        activation = existing
    else:
        activation = Activation(
            organization_id=ctx.organization_id,
            product_key=product_key,
            status="trial" if trial_days else "active",
            trial_ends_at=trial_ends,
            activated_by=ctx.principal.membership_id,
        )
        session.add(activation)

    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="product.activated",
        entity="activation",
        entity_id=activation.id,
        before=before,
        after={"status": activation.status, "product_key": product_key},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ACTIVATED,
        aggregate_type="activation",
        aggregate_id=activation.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "product_key": product_key,
            "status": activation.status,
        },
        actor=_actor(ctx),
    )
    return activation


async def deactivate(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    product_key: str,
    request: Request | None = None,
) -> Activation:
    """Turn a product off, keeping the row.

    A deleted row cannot answer "when did they have this?", which is the first question every
    billing dispute asks — and a customer who comes back should find their configuration rather
    than a blank slate.
    """
    activation = await selectors.get_activation(session, ctx.organization_id, product_key)
    if activation is None:
        raise NotFoundProblem(f"{product_key} is not activated for this organization")

    activation.deactivated_at = datetime.now(UTC)
    activation.status = "suspended"

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="product.deactivated",
        entity="activation",
        entity_id=activation.id,
        after={"product_key": product_key},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DEACTIVATED,
        aggregate_type="activation",
        aggregate_id=activation.id,
        payload={"organization_id": str(ctx.organization_id), "product_key": product_key},
        actor=_actor(ctx),
    )
    return activation
