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

The dunning writes (ADR-0005) obey both: a cycle opening is a `past_due` transition, a window
closing is a `suspended` one, a collected payment is an `activated` one, and each carries the
dunning facts the customer needs to be told on the same event the projection reads.
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
    DUNNING_ABANDONED,
    DUNNING_EXHAUSTED,
    DUNNING_OPEN,
    DUNNING_RECOVERED,
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    STATE_TRIAL,
    AuditLog,
    DunningCycle,
    OutboxEvent,
    Subscription,
    SubscriptionItem,
    SubscriptionTransition,
)
from app.schemas import CatalogPlanVersion, ChangePlanIn, PaymentOutcome, SubscribeIn
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event
from platform_core.logging import get_logger

log = get_logger("subscription.services")

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
    extra: dict[str, Any] | None = None,
) -> None:
    """Apply one validated transition: move the state, write the append-only row, stage the event.

    The subscription's `state` is set here from the decision; the transition row records where it
    came from and — for a change-plan — the pin it moved off, which is what keeps the previous
    version reconstructable without ever mutating catalog (ADR-0004 §2).

    `extra` is merged into the event payload for facts that belong to *this* move and to no
    other: the dunning window a `past_due` opened, the attempt count a `suspended` exhausted.
    They ride the same event the projection consumes rather than a second one, because a customer
    being told "we will retry on Tuesday" and their access being kept on are the same fact
    arriving in two services, and two events would let one land without the other.
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
    payload: dict[str, Any] = {
        "organization_id": str(subscription.organization_id),
        "product_key": subscription.product_key,
        "plan_version_id": str(subscription.plan_version_id),
        "state": subscription.state,
        "trial_ends_at": subscription.trial_ends_at.isoformat()
        if subscription.trial_ends_at
        else None,
    }
    if extra:
        payload.update(extra)
    publish_event(
        session,
        OutboxEvent,
        organization_id=subscription.organization_id,
        event_type=decision.event_type,
        aggregate_type="subscription",
        aggregate_id=subscription.id,
        payload=payload,
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
    # A customer who cancels mid-dunning has settled the question the ladder was asking. Leaving
    # the cycle open would leave a sweep scheduled against a terminal subscription, and the
    # suspension it eventually tried would be an illegal edge — a handler that raises forever and
    # dead-letters, for a subscription nobody is billing.
    await _close_open_cycle(session, subscription, reason="subscription_cancelled")
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
    # An operator suspension overrides the ladder rather than racing it: the window is over
    # because a human said so, and a sweep must not later "suspend" an already suspended
    # subscription on an edge the state machine forbids.
    await _close_open_cycle(session, subscription, reason="operator_suspend")
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


# ----------------------------------------------------------------------- dunning (ADR-0005)

#: The service actor on every write the dunning machinery makes. There is no human in this path:
#: payment observed a failure, or the sweep observed a clock.
_PAYMENT_ACTOR = {"type": "service", "id": "payment"}
_SWEEP_ACTOR = {"type": "service", "id": "subscription-dunning"}


def _dunning_facts(cycle: DunningCycle) -> dict[str, Any]:
    """The dunning state, shaped for an event payload.

    Every lifecycle event a dunning move emits carries these, so a notification service can write
    "we could not collect ₹X; we will try again on the 14th; access continues until the 25th"
    from the event alone, without asking this service anything. The dates are the **stored**
    ones — the same instants the sweep will act on, not a second computation that could disagree.
    """
    return {
        "dunning": {
            "cycle_id": str(cycle.id),
            "state": cycle.state,
            "opened_at": cycle.opened_at.isoformat(),
            "grace_ends_at": cycle.grace_ends_at.isoformat(),
            "next_attempt_at": cycle.next_action_at.isoformat()
            if cycle.next_action_at and cycle.state == DUNNING_OPEN
            else None,
            "attempts_made": cycle.attempts_made,
            "max_attempts": domain.POLICY.max_attempts,
            "failures_seen": cycle.failures_seen,
            "invoice_id": str(cycle.invoice_id) if cycle.invoice_id else None,
            "amount_minor": cycle.amount_minor,
            "currency": cycle.currency,
        }
    }


async def _close_open_cycle(
    session: AsyncSession, subscription: Subscription, *, reason: str, now: datetime | None = None
) -> DunningCycle | None:
    """End whatever cycle is running, without touching the subscription's state.

    Called from every path that settles the question dunning was asking by other means — a
    cancellation, an operator suspension, a collected payment. Closing is what takes the cycle out
    of the sweep's due set (`next_action_at` NULL, `state` no longer `open`); the caller decides
    separately what the subscription's state should become.
    """
    from app import selectors

    cycle = await selectors.get_open_cycle(session, subscription.id)
    if cycle is None:
        return None
    cycle.state = DUNNING_RECOVERED if reason == "payment_captured" else DUNNING_ABANDONED
    cycle.closed_at = now or datetime.now(UTC)
    cycle.closed_reason = reason
    cycle.next_action_at = None
    return cycle


async def record_payment_failure(
    session: AsyncSession,
    subscription: Subscription,
    outcome: PaymentOutcome,
    *,
    now: datetime | None = None,
    request: Request | None = None,
) -> Subscription:
    """A failed collection: open the grace window, or count against the one already open.

    This is the subscription-service half of ADR-0004 §5, under the policy ADR-0005 finally
    fixes. It **no longer suspends**. The first failure moves `active -> past_due`, which
    product-service projects to an activation that still entitles — grace that takes the product
    away is not grace — and schedules the ladder. Suspension is the sweep's job, at the window's
    close, and nowhere else.

    A failure arriving against a cycle that is already open is the outcome of a retry, or a second
    charge for the same period. It is *counted* and nothing more: the schedule was fixed when the
    cycle opened, and re-deriving it here would let a customer's window be extended by the very
    failures it exists to survive.

    Three states are left alone, each for its own reason:

    * **`suspended` / `cancelled`** — the ladder is over. The state machine forbids the edges
      anyway; refusing here means a late failure event is a no-op rather than a 422 that
      dead-letters.
    * **`trial`** — a trial carries no debt. A failed conversion charge does not end a trial
      early; the trial lapses on its own date, which `Activation.entitles` already evaluates at
      read time. Retrying the conversion is the ladder's job only once the subscription is paid.
    """
    from app import selectors

    if subscription.state in (STATE_SUSPENDED, STATE_CANCELLED, STATE_TRIAL):
        return subscription

    at = now or datetime.now(UTC)
    cycle = await selectors.get_open_cycle(session, subscription.id)
    if cycle is not None:
        cycle.failures_seen += 1
        record_audit(
            session,
            AuditLog,
            organization_id=subscription.organization_id,
            actor_id=None,
            acting_as="payment",
            action="commerce.dunning.failure_recorded",
            entity="dunning_cycle",
            entity_id=cycle.id,
            after={"failures_seen": cycle.failures_seen, "attempts_made": cycle.attempts_made},
            reason=domain.REASON_PAYMENT_FAILED,
            request=request,
        )
        await session.flush()
        return subscription

    window_ends_at = domain.grace_ends_at(at)
    cycle = DunningCycle(
        organization_id=subscription.organization_id,
        subscription_id=subscription.id,
        invoice_id=outcome.invoice_id,
        amount_minor=outcome.amount_minor,
        currency=(outcome.currency or subscription.currency).upper(),
        state=DUNNING_OPEN,
        opened_at=at,
        grace_ends_at=window_ends_at,
        next_action_at=domain.next_action_at(at, 0, window_ends_at),
        attempts_made=0,
        failures_seen=1,
    )
    session.add(cycle)
    await session.flush()

    decision = domain.decide(
        from_state=subscription.state,
        to_state=STATE_PAST_DUE,
        reason=domain.REASON_PAYMENT_FAILED,
    )
    _transition(
        session,
        subscription,
        decision,
        actor=_PAYMENT_ACTOR,
        request=request,
        from_plan_version_id=subscription.plan_version_id,
        to_plan_version_id=subscription.plan_version_id,
        extra=_dunning_facts(cycle),
    )
    await session.flush()
    return subscription


async def record_payment_captured(
    session: AsyncSession,
    subscription: Subscription,
    *,
    now: datetime | None = None,
    request: Request | None = None,
) -> Subscription:
    """Money arrived: close the cycle and put the subscription back to `active`.

    Recovery works from **either** rung — `past_due` during grace, and `suspended` after it, which
    is why `suspended -> active` is a legal edge and `cancelled -> active` is not. A customer who
    pays in week six comes back to the same subscription, the same pin and the same history rather
    than a new row that has forgotten what they used to have.

    A capture for a subscription that is already `active` is the ordinary case — a renewal that
    worked — and is deliberately not a transition: nothing changed, so nothing is emitted, and the
    projection is not woken to write the row it already holds.
    """
    at = now or datetime.now(UTC)
    cycle = await _close_open_cycle(session, subscription, reason="payment_captured", now=at)

    if subscription.state not in (STATE_PAST_DUE, STATE_SUSPENDED):
        if cycle is not None:
            await session.flush()
        return subscription

    decision = domain.decide(
        from_state=subscription.state,
        to_state=STATE_ACTIVE,
        reason=domain.REASON_DUNNING_RECOVERED,
    )
    _transition(
        session,
        subscription,
        decision,
        actor=_PAYMENT_ACTOR,
        request=request,
        from_plan_version_id=subscription.plan_version_id,
        to_plan_version_id=subscription.plan_version_id,
        extra=_dunning_facts(cycle) if cycle is not None else None,
    )
    await session.flush()
    return subscription


async def request_dunning_retry(
    session: AsyncSession,
    subscription: Subscription,
    cycle: DunningCycle,
    *,
    now: datetime,
    request: Request | None = None,
) -> DunningCycle:
    """Ask payment for the next debit on the ladder. Not a lifecycle transition.

    The subscription stays `past_due` while a retry is in flight — asking for money is not a
    change in what the customer has — so this stages `commerce.subscription.payment_retry_due`
    and no transition row. payment-service raises the charge under a key derived from the cycle
    and the attempt number, because reusing the failed charge's key would be deduplicated into
    the charge that already failed and debit nobody (ADR-0004 §6: Razorpay has no idempotency key
    of its own, so ours is the only one).

    `next_action_at` is recomputed from the cycle's **stored** `opened_at`, never from `now`, so a
    sweep that runs six hours late schedules attempt 3 for its original instant instead of six
    hours after that.
    """
    attempt_no = cycle.attempts_made + 1
    scheduled_for = domain.attempt_due_at(cycle.opened_at, attempt_no)
    idempotency_key = domain.retry_idempotency_key(cycle.id, attempt_no)

    cycle.attempts_made = attempt_no
    cycle.next_action_at = domain.next_action_at(cycle.opened_at, attempt_no, cycle.grace_ends_at)

    record_audit(
        session,
        AuditLog,
        organization_id=subscription.organization_id,
        actor_id=None,
        acting_as="subscription-dunning",
        action=domain.EVT_PAYMENT_RETRY_DUE,
        entity="dunning_cycle",
        entity_id=cycle.id,
        after={
            "attempt_no": attempt_no,
            "idempotency_key": idempotency_key,
            "next_action_at": cycle.next_action_at.isoformat() if cycle.next_action_at else None,
        },
        reason="dunning_retry",
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=subscription.organization_id,
        event_type=domain.EVT_PAYMENT_RETRY_DUE,
        aggregate_type="subscription",
        aggregate_id=subscription.id,
        payload={
            "organization_id": str(subscription.organization_id),
            "subscription_id": str(subscription.id),
            "product_key": subscription.product_key,
            "cycle_id": str(cycle.id),
            "attempt_no": attempt_no,
            "max_attempts": domain.POLICY.max_attempts,
            "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
            "grace_ends_at": cycle.grace_ends_at.isoformat(),
            "invoice_id": str(cycle.invoice_id) if cycle.invoice_id else None,
            "amount_minor": cycle.amount_minor,
            "currency": cycle.currency,
            "idempotency_key": idempotency_key,
        },
        actor=_SWEEP_ACTOR,
    )
    await session.flush()
    return cycle


async def suspend_for_dunning(
    session: AsyncSession,
    subscription: Subscription,
    cycle: DunningCycle,
    *,
    now: datetime,
    request: Request | None = None,
) -> Subscription:
    """The window closed with nothing collected: stop entitling.

    The end of the loop ADR-0004 §5 describes, arrived at on a schedule instead of on the first
    failure. `commerce.subscription.suspended` is what product-service projects to a de-entitled
    activation, which the gateway turns into a 402 — unchanged; only *when* it is emitted moved.

    The cycle is closed first and unconditionally, so a subscription that has meanwhile been
    cancelled or suspended by hand still leaves the sweep's due set rather than being retried
    forever. The state move is then skipped for exactly those cases: `suspended` again would be an
    illegal self-edge, and there is no such thing as suspending a cancelled subscription.
    """
    cycle.state = DUNNING_EXHAUSTED
    cycle.closed_at = now
    cycle.closed_reason = "grace_expired"
    cycle.next_action_at = None

    if subscription.state in (STATE_SUSPENDED, STATE_CANCELLED):
        log.info(
            "dunning.suspend.skipped",
            subscription_id=str(subscription.id),
            state=subscription.state,
        )
        await session.flush()
        return subscription

    decision = domain.decide(
        from_state=subscription.state,
        to_state=STATE_SUSPENDED,
        reason=domain.REASON_DUNNING_EXHAUSTED,
    )
    _transition(
        session,
        subscription,
        decision,
        actor=_SWEEP_ACTOR,
        request=request,
        from_plan_version_id=subscription.plan_version_id,
        to_plan_version_id=subscription.plan_version_id,
        extra=_dunning_facts(cycle),
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
