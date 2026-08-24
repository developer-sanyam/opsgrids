"""Writes for subscription — the lifecycle, transactionally.

Business rules, the append-only transition row, the audit row and the outbox event. Nothing here
opens or commits a transaction: a customer route takes its session from `get_tenant_session` and
the internal route binds its own, so the state change, its transition, its audit entry and the
`commerce.subscription.*` event it emits all commit or all roll back together (ADR-0002 §1).

Two disciplines are load-bearing:

* **The pin is validated, never trusted.** A subscribe or change-plan resolves the
  `plan_version_id` against catalog and refuses it unless it exists, is published, and is visible
  to this organization (public or negotiated for it, ADR-0004 §2). The subscription pins an
  *immutable* version (ADR-0000 §9); a change-plan re-points to a new one and preserves the old
  pin on the transition row — history is never overwritten.
* **Every accepted move is one event.** `domain.decide` validates the edge and names the event;
  `_transition` writes the transition row and stages the event in the same breath, so the stream
  the operator console and the product-service projection consume is complete (ADR-0004 §4/§5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.catalog import PlanCatalog
from app.models import (
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_SUSPENDED,
    STATE_TRIAL,
    AuditLog,
    OutboxEvent,
    Subscription,
    SubscriptionItem,
    SubscriptionTransition,
)
from app.schemas import CatalogPlanVersion, ChangePlanIn, SubscribeIn
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

#: The one catalog state this service asserts. A pin is only valid against a published version
#: (ADR-0000 §9); catalog owns the vocabulary, so this mirrors the single value we depend on.
VERSION_PUBLISHED = "published"


def _customer_actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id),
    }


def _period_end(start: datetime, billing_interval: str) -> datetime | None:
    if billing_interval == "year":
        return start + timedelta(days=365)
    if billing_interval == "one_time":
        return None
    return start + timedelta(days=30)


def _list_price_minor(version: CatalogPlanVersion) -> int:
    """A comparable list total for classifying a plan change as an upgrade or a downgrade.

    Sums the recurring components (`flat`/`per_seat`); a usage rate has no fixed amount to
    compare. Deliberately coarse — it decides only which *event* a change emits, never money.
    """
    return sum(p.amount_minor or 0 for p in version.prices if p.kind in ("flat", "per_seat"))


def _validate_new_pin(
    version: CatalogPlanVersion | None, organization_id: uuid.UUID
) -> CatalogPlanVersion:
    """A pin a customer may actually subscribe to: it exists, is published, and is theirs to see.

    A bespoke version negotiated for another organization is refused with the same 404 a
    nonexistent one gets, so its existence is never disclosed — the audience-of-one rule from
    ADR-0004 §2, defended here the way catalog defends its own read.
    """
    if version is None:
        raise NotFoundProblem("No such plan version")
    if version.state != VERSION_PUBLISHED:
        raise BusinessRuleProblem("that plan version is not published and cannot be subscribed to")
    if version.restricted_to_org_id is not None and version.restricted_to_org_id != organization_id:
        raise NotFoundProblem("No such plan version")
    return version


def _append_items(subscription: Subscription, version: CatalogPlanVersion) -> None:
    """Append one item per catalog price, through the relationship.

    Managed through the collection (cascade `all, delete-orphan`) rather than by bare inserts, so
    the in-memory collection matches the database and the route can serialize `subscription.items`
    straight after. Async SQLAlchemy forbids implicit lazy loading, so callers append only while
    the collection is available: on a transient subscription (subscribe) or an eager-loaded one
    (change-plan, which loads items through its selector).
    """
    for price in version.prices:
        subscription.items.append(
            SubscriptionItem(
                organization_id=subscription.organization_id,
                price_id=price.id,
                quantity=1,
            )
        )


def _replace_items(subscription: Subscription, version: CatalogPlanVersion) -> None:
    """Swap the items for a new pin's. The collection must already be loaded (the change-plan
    selector eager-loads it), because clearing it otherwise triggers a lazy load async forbids."""
    subscription.items.clear()
    _append_items(subscription, version)


def _transition(
    session: AsyncSession,
    subscription: Subscription,
    decision: domain.Decision,
    *,
    actor: dict[str, Any],
    request: Request | None,
    from_plan_version_id: uuid.UUID | None,
    to_plan_version_id: uuid.UUID | None,
) -> None:
    """Apply one validated transition: move the state, write the append-only row, stage the event.

    The subscription's `state` is set here from the decision; the transition row records where it
    came from and — for a change-plan — the pin it moved off, which is what keeps the previous
    version reconstructable without ever mutating catalog (ADR-0004 §2).
    """
    subscription.state = decision.to_state

    session.add(
        SubscriptionTransition(
            organization_id=subscription.organization_id,
            subscription_id=subscription.id,
            from_state=decision.from_state,
            to_state=decision.to_state,
            from_plan_version_id=from_plan_version_id,
            to_plan_version_id=to_plan_version_id,
            reason=decision.reason,
            actor=actor,
        )
    )

    record_audit(
        session,
        AuditLog,
        organization_id=subscription.organization_id,
        actor_id=uuid.UUID(actor["id"]) if actor.get("type") == "user" else None,
        action=decision.event_type,
        entity="subscription",
        entity_id=subscription.id,
        before={"state": decision.from_state} if decision.from_state else None,
        after={"state": decision.to_state, "plan_version_id": str(subscription.plan_version_id)},
        reason=decision.reason,
        acting_as=actor.get("id") if actor.get("type") == "operator" else None,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=subscription.organization_id,
        event_type=decision.event_type,
        aggregate_type="subscription",
        aggregate_id=subscription.id,
        payload={
            "organization_id": str(subscription.organization_id),
            "product_key": subscription.product_key,
            "plan_version_id": str(subscription.plan_version_id),
            "state": subscription.state,
            "trial_ends_at": subscription.trial_ends_at.isoformat()
            if subscription.trial_ends_at
            else None,
        },
        actor=actor,
    )


async def subscribe(
    session: AsyncSession,
    ctx: ScopeContext,
    catalog: PlanCatalog,
    payload: SubscribeIn,
    *,
    request: Request | None = None,
) -> Subscription:
    """Start a subscription for a product, pinned to a validated catalog version.

    `trial_days` set opens it in `trial` with an end date (a trial is a subscription state, never
    a separate object — ADR-0004 §4); otherwise it opens `active`. One subscription per product
    per organization: an existing one is a conflict, not a second row.
    """
    from app import selectors

    existing = await selectors.get_by_product(session, ctx.organization_id, payload.product_key)
    if existing is not None:
        raise ConflictProblem(
            f"this organization already has a subscription for {payload.product_key!r}"
        )

    version = _validate_new_pin(
        await catalog.get_plan_version(payload.plan_version_id), ctx.organization_id
    )
    if version.product_key and version.product_key != payload.product_key:
        raise BusinessRuleProblem(
            f"plan version belongs to product {version.product_key!r}, not {payload.product_key!r}"
        )

    now = datetime.now(UTC)
    to_state = STATE_TRIAL if payload.trial_days else STATE_ACTIVE
    trial_ends_at = now + timedelta(days=payload.trial_days) if payload.trial_days else None

    subscription = Subscription(
        organization_id=ctx.organization_id,
        product_key=payload.product_key,
        plan_version_id=version.id,
        state=to_state,
        trial_ends_at=trial_ends_at,
        current_period_start=now,
        current_period_end=_period_end(now, version.billing_interval),
        currency=version.currency.upper(),
    )
    # Appended while the subscription is transient, so the collection is available without a lazy
    # load (async forbids one), and populated for the response the route serializes.
    _append_items(subscription, version)
    session.add(subscription)
    await session.flush()

    decision = domain.decide(
        from_state=None,
        to_state=to_state,
        reason="subscribe" if to_state == STATE_ACTIVE else "trial",
    )
    _transition(
        session,
        subscription,
        decision,
        actor=_customer_actor(ctx),
        request=request,
        from_plan_version_id=None,
        to_plan_version_id=version.id,
    )
    await session.flush()
    return subscription


async def change_plan(
    session: AsyncSession,
    ctx: ScopeContext,
    catalog: PlanCatalog,
    subscription: Subscription,
    payload: ChangePlanIn,
    *,
    request: Request | None = None,
) -> Subscription:
    """Re-pin a subscription to a different published version — an upgrade or a downgrade.

    The previous pin is preserved on the transition row, never overwritten: the version a past
    invoice was computed against still exists and is still named, exactly as ADR-0004 §2 requires.
    An upgrade and a downgrade are the same state (`active`/`trial` unchanged), told apart by
    whether the new list price is higher — which decides only the event, not the money.
    """
    new_version = _validate_new_pin(
        await catalog.get_plan_version(payload.plan_version_id), ctx.organization_id
    )
    if new_version.id == subscription.plan_version_id:
        raise BusinessRuleProblem("the subscription is already pinned to that plan version")
    if new_version.currency.upper() != subscription.currency:
        # One currency per subscription for now (ADR-0004 defaults). A cross-currency change is a
        # cancel-and-resubscribe decision, not a silent re-pin.
        raise BusinessRuleProblem(
            f"cannot change currency from {subscription.currency} to {new_version.currency.upper()}"
        )

    old_version = await catalog.get_plan_version(subscription.plan_version_id)
    old_total = _list_price_minor(old_version) if old_version else 0
    reason = (
        domain.REASON_UPGRADE
        if _list_price_minor(new_version) >= old_total
        else domain.REASON_DOWNGRADE
    )

    decision = domain.decide(
        from_state=subscription.state, to_state=subscription.state, reason=reason
    )

    from_pin = subscription.plan_version_id
    subscription.plan_version_id = new_version.id

    # Rebuild the items against the new pin. The old items go with the old pin, but that pin is
    # preserved on the transition row below, so history stays intact (ADR-0004 §2).
    _replace_items(subscription, new_version)

    _transition(
        session,
        subscription,
        decision,
        actor=_customer_actor(ctx),
        request=request,
        from_plan_version_id=from_pin,
        to_plan_version_id=new_version.id,
    )
    await session.flush()
    return subscription


async def cancel(
    session: AsyncSession,
    ctx: ScopeContext,
    subscription: Subscription,
    *,
    reason: str | None = None,
    request: Request | None = None,
) -> Subscription:
    """Cancel a subscription immediately. Terminal — a cancelled subscription cannot be revived
    (the state machine forbids `cancelled -> active`), and the row is kept so "what did they have,
    and when?" stays a `SELECT`."""
    decision = domain.decide(
        from_state=subscription.state, to_state=STATE_CANCELLED, reason=reason or "customer_cancel"
    )
    subscription.cancel_at = datetime.now(UTC)
    _transition(
        session,
        subscription,
        decision,
        actor=_customer_actor(ctx),
        request=request,
        from_plan_version_id=subscription.plan_version_id,
        to_plan_version_id=subscription.plan_version_id,
    )
    await session.flush()
    return subscription


async def suspend(
    session: AsyncSession,
    subscription: Subscription,
    *,
    operator_id: str,
    reason: str | None = None,
    request: Request | None = None,
) -> Subscription:
    """Suspend a subscription — the write the operator console delegates over /internal.

    Emits `commerce.subscription.suspended`, which the product-service projection turns into a
    de-entitled activation, which the gateway turns into a 402 (ADR-0004 §5). De-entitlement is
    EVENTUAL by default: it takes effect on the next access-token refresh, not instantly.
    """
    decision = domain.decide(
        from_state=subscription.state, to_state=STATE_SUSPENDED, reason=reason or "operator_suspend"
    )
    _transition(
        session,
        subscription,
        decision,
        actor={"type": "operator", "id": operator_id},
        request=request,
        from_plan_version_id=subscription.plan_version_id,
        to_plan_version_id=subscription.plan_version_id,
    )
    await session.flush()
    return subscription


async def suspend_on_payment_failure(
    session: AsyncSession,
    subscription: Subscription,
    *,
    request: Request | None = None,
) -> Subscription:
    """Suspend a subscription because a payment failed — the pivot of the entitlement loop.

    This is the subscription-service half of ADR-0004 §5: payment-service emits
    `commerce.payment.failed`; the consumer here turns that into a `suspended` transition, which
    emits `commerce.subscription.suspended`, which product-service already projects to a
    de-entitled `Activation`, which the gateway already turns into a 402. Each step happens in the
    service that owns it; this function owns exactly one — moving the state and emitting the event.

    **No-dunning MVP.** A failed payment suspends *immediately*. Whether a failure should instead
    open a grace window or trigger a retry schedule before suspending is a dunning policy ADR-0004
    explicitly leaves Open — the `suspended` state exists, the *policy* for entering it does not, so
    the simplest correct default is taken here and flagged for a later ADR.

    Idempotent by construction: a subscription already `suspended` or `cancelled` is left exactly as
    it is (the state machine forbids those edges anyway), so a redelivered failure — or two distinct
    failed charges for the same subscription — never raises and never double-emits.
    """
    if subscription.state in (STATE_SUSPENDED, STATE_CANCELLED):
        return subscription

    decision = domain.decide(
        from_state=subscription.state, to_state=STATE_SUSPENDED, reason="payment_failed"
    )
    _transition(
        session,
        subscription,
        decision,
        actor={"type": "service", "id": "payment"},
        request=request,
        from_plan_version_id=subscription.plan_version_id,
        to_plan_version_id=subscription.plan_version_id,
    )
    await session.flush()
    return subscription


async def require_subscription(session: AsyncSession, subscription_id: uuid.UUID) -> Subscription:
    from app import selectors

    subscription = await selectors.get_subscription(session, subscription_id)
    if subscription is None:
        # Invisible under RLS unless the transaction is bound to its organization — so another
        # tenant's id is a 404, never a disclosure.
        raise NotFoundProblem("No such subscription")
    return subscription
