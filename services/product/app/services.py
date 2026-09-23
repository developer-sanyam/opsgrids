"""Writes for product.

Business rules, audit rows and outbox rows. The change, its audit entry and any event it emits
all commit or all roll back — the tenant chokepoint owns the transaction, so nothing here opens
one of its own.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import PLATFORM_ORG, Activation, AuditLog, OutboxEvent, Product
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem, NotFoundProblem
from platform_core.events import publish_event
from platform_core.logging import get_logger

log = get_logger("product.services")

#: The only launch states an operator may set by hand. `beta` exists in the schema
#: (`Product.status`'s own comment names it) but is not one of these: nothing in this codebase
#: today drives a product into or out of `beta` outside a migration, and naming it here would
#: let the CLI put a product into a state nothing else expects to reconcile out of.
LAUNCH_STATUSES = ("available", "soon")

EVT_ACTIVATED = "core.product.activated"
EVT_DEACTIVATED = "core.product.deactivated"

#: The subscription states that keep an activation entitling, and the ones that switch it off.
#: The activation's own vocabulary is narrower than the subscription's (`trial | active |
#: suspended`), so several lifecycle states fold onto `active`, and both terminal states fold
#: onto `suspended` + `deactivated_at` — `Activation.entitles` then evaluates trial expiry at
#: read time exactly as it does for a hand-activated trial (ADR-0004 §5).
_PROJECTION_TRIAL = "trial"
_PROJECTION_DE_ENTITLING = ("suspended", "cancelled")


async def project_subscription(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    product_key: str,
    state: str,
    trial_ends_at: datetime | None,
    occurred_at: datetime | None = None,
) -> Activation:
    """Upsert the activation from a subscription lifecycle state — the entitlement projection.

    Called by the event consumer, once per `commerce.subscription.*` message, inside a
    `session_scope(organization_id=...)` so the tenant GUC is set and this NOBYPASSRLS write
    passes the forced policy on `activations`. Reuses the write shape of `activate`/`deactivate`:
    one row per `(organization_id, product_key)`, the row kept and updated rather than duplicated.

    The mapping is deliberately driven off the subscription's `state`, not the event name, so a
    renew, an upgrade and a downgrade — all of which leave the subscription `active` — all keep
    the activation entitling without the projection needing to know the difference.

    No product outbox event is emitted here: this is a projection *of* the lifecycle, not a new
    fact about it, and re-emitting would risk a loop. An audit row is written so the switch has a
    trail. De-entitlement is **eventual** — see the consumer for why the permission-version is
    left untouched.

    `occurred_at` is the ordering guard. Idempotency (the `processed_events` ledger) catches an
    exact redelivery, but not a *distinct older* event folded late — multi-replica relays and
    consumers, and a DLQ replay, can all deliver transitions out of order, and last-write-wins
    would then leave a reactivated customer suspended (or a suspended one entitled) until the
    next transition. An event older than what the row already reflects is dropped.
    """
    if state in _PROJECTION_DE_ENTITLING:
        new_status, deactivated, trial_ends = "suspended", datetime.now(UTC), None
    elif state == _PROJECTION_TRIAL:
        new_status, deactivated, trial_ends = "trial", None, trial_ends_at
    else:
        # active, past_due, and the renew/upgrade/downgrade moves that stay active.
        new_status, deactivated, trial_ends = "active", None, None

    activation = await selectors.get_activation(session, organization_id, product_key)
    if (
        activation is not None
        and occurred_at is not None
        and activation.state_occurred_at is not None
        and occurred_at <= activation.state_occurred_at
    ):
        log.info(
            "subscription.projection.stale_dropped",
            organization_id=str(organization_id),
            product_key=product_key,
            event_occurred_at=occurred_at.isoformat(),
            row_reflects=activation.state_occurred_at.isoformat(),
        )
        return activation

    before = {"status": activation.status} if activation else None
    if activation is None:
        activation = Activation(
            organization_id=organization_id,
            product_key=product_key,
            status=new_status,
            trial_ends_at=trial_ends,
            deactivated_at=deactivated,
            state_occurred_at=occurred_at,
        )
        session.add(activation)
    else:
        activation.status = new_status
        activation.trial_ends_at = trial_ends
        activation.deactivated_at = deactivated
        if occurred_at is not None:
            activation.state_occurred_at = occurred_at
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        acting_as="subscription-projection",
        action="product.projected",
        entity="activation",
        entity_id=activation.id,
        before=before,
        after={"status": activation.status, "product_key": product_key, "from_state": state},
    )
    return activation


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


async def set_launch_status(
    session: AsyncSession,
    *,
    product_key: str,
    status: str,
    acting_as: str,
) -> Product:
    """Flip what the marketplace sells — the only write `products` ever needs outside a
    migration, and the one this service's CLI (`app/launch.py`) exists to make an operator go
    through rather than a hand-run `UPDATE`.

    `products` carries no tenant policy (`app/models.py`), so the row itself needs no GUC. Its
    `audit_logs` does, and there is no organization this act belongs to, so the caller is
    expected to have opened `session_scope(organization_id=PLATFORM_ORG)` — this function does
    not open its own transaction, matching every other write in this module.

    Refuses a key the catalogue does not already know (a launch flip is not the place to invent
    a product — that is a migration's job) and a status outside `LAUNCH_STATUSES` (`beta` is a
    real value in the column but not one anything here knows how to reconcile out of).
    """
    if status not in LAUNCH_STATUSES:
        raise BusinessRuleProblem(
            f"{status!r} is not a launch status this command may set — choose one of "
            f"{LAUNCH_STATUSES}"
        )
    product = await selectors.get_product(session, product_key)
    if product is None:
        raise NotFoundProblem(f"no product registered with key {product_key!r}")

    before = {"status": product.status}
    if product.status == status:
        return product
    product.status = status
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=None,
        acting_as=acting_as,
        action="product.launch_status_set",
        entity="product",
        entity_id=product.id,
        before=before,
        after={"status": status, "product_key": product_key},
    )
    return product
