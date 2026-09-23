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

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.catalog import PlanCatalog
from app.models import (
    DUNNING_ABANDONED,
    DUNNING_EXHAUSTED,
    DUNNING_OPEN,
    DUNNING_RECOVERED,
    PERIOD_INVOICED,
    PERIOD_PENDING,
    PERIOD_SKIPPED,
    STATE_ACTIVE,
    STATE_CANCELLED,
    STATE_PAST_DUE,
    STATE_SUSPENDED,
    STATE_TRIAL,
    AuditLog,
    BillingPeriod,
    DunningCycle,
    OutboxEvent,
    Subscription,
    SubscriptionItem,
    SubscriptionTransition,
)
from app.registry import ProductRegistry
from app.schemas import (
    CatalogPlanVersion,
    ChangePlanIn,
    OperatorChangePlanIn,
    OperatorSubscribeIn,
    PaymentOutcome,
    SubscribeIn,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
)
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


def _lifecycle_payload(subscription: Subscription) -> dict[str, Any]:
    """What every `commerce.subscription.*` lifecycle event carries.

    `entitled_products` is the fan-out a bundle needs: product-service projects one activation
    source per member, account-service installs each member's manifest. Additive — a consumer
    written before it existed reads `product_key` as it always did, and one written after falls
    back to `[product_key]` when an older event lacks it. `subscription_id` names the source the
    projection records; it is also the envelope's `aggregate_id`, stated here so the payload is
    self-describing.
    """
    return {
        "organization_id": str(subscription.organization_id),
        "subscription_id": str(subscription.id),
        "product_key": subscription.product_key,
        "plan_version_id": str(subscription.plan_version_id),
        "state": subscription.state,
        "trial_ends_at": subscription.trial_ends_at.isoformat()
        if subscription.trial_ends_at
        else None,
        "entitled_products": subscription.entitled_products,
    }


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
    payload = _lifecycle_payload(subscription)
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


def _operator_actor(operator_id: str) -> dict[str, Any]:
    return {"type": "operator", "id": operator_id}


def _require_product(version: CatalogPlanVersion, product_key: str) -> None:
    """The version must belong to the product the subscription is for — and must *say* so.

    Fails closed: a catalog read that names no product is refused rather than trusted, because
    it could be pinned under any key and entitle whatever that key's members are.
    """
    if version.product_key is None:
        raise BusinessRuleProblem("that plan version names no product and cannot be pinned")
    if version.product_key != product_key:
        raise BusinessRuleProblem(
            f"plan version belongs to product {version.product_key!r}, not {product_key!r}"
        )


async def _check_bundle(
    registry: ProductRegistry | None, product_key: str, members: list[str]
) -> None:
    """Refuse a bundle sale the product registry cannot vouch for, at the moment of sale.

    `registry` is None on the customer path, which is exactly where a bundle is refused: bundles
    are sold by the platform team (docs/commerce-packaging.md, reversible). On the operator path
    the registry must confirm the version's product is a bundle and every member is a registered,
    launched (`status != 'soon'`), non-platform, non-bundle product.
    """
    if registry is None:
        raise ForbiddenProblem(
            "bundles are sold by the OpsGrids team; ask your account manager to set one up"
        )
    products = await registry.products()
    bundle = products.get(product_key)
    problems: list[str] = []
    if bundle is None or not bundle.is_bundle:
        problems.append(f"{product_key!r} is not a bundle product")
    for member in members:
        entry = products.get(member)
        if entry is None:
            problems.append(f"{member!r} is not a registered product")
        elif entry.is_platform or entry.is_bundle:
            problems.append(f"{member!r} cannot be a bundle member")
        elif entry.status == "soon":
            problems.append(f"{member!r} has not launched")
    if problems:
        raise BusinessRuleProblem("cannot sell this bundle: " + "; ".join(problems))


async def subscribe(
    session: AsyncSession,
    ctx: ScopeContext,
    catalog: PlanCatalog,
    payload: SubscribeIn,
    *,
    request: Request | None = None,
) -> Subscription:
    """A customer starts a subscription for one product. Bundles are refused here."""
    return await _subscribe(
        session,
        organization_id=ctx.organization_id,
        actor=_customer_actor(ctx),
        catalog=catalog,
        registry=None,
        product_key=payload.product_key,
        plan_version_id=payload.plan_version_id,
        trial_requested=payload.trial_days,
        request=request,
    )


async def subscribe_as_operator(
    session: AsyncSession,
    catalog: PlanCatalog,
    registry: ProductRegistry,
    payload: OperatorSubscribeIn,
    *,
    request: Request | None = None,
) -> Subscription:
    """The platform team opens a subscription for a customer — the one path that sells a bundle."""
    return await _subscribe(
        session,
        organization_id=payload.organization_id,
        actor=_operator_actor(payload.operator_id),
        catalog=catalog,
        registry=registry,
        product_key=payload.product_key,
        plan_version_id=payload.plan_version_id,
        # "A trial" means the version's own trial; the cap below makes that exact.
        trial_requested=domain.MAX_TRIAL_DAYS if payload.trial else None,
        request=request,
    )


async def _subscribe(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor: dict[str, Any],
    catalog: PlanCatalog,
    registry: ProductRegistry | None,
    product_key: str,
    plan_version_id: uuid.UUID,
    trial_requested: int | None,
    request: Request | None,
) -> Subscription:
    """Start a subscription pinned to a validated catalog version.

    A trial opens in `trial` with an end date (a subscription state, never a separate object —
    ADR-0004 §4), its length the version's and not the caller's. One subscription per product per
    organization: an existing one is a conflict, not a second row.
    """
    from app import selectors

    existing = await selectors.get_by_product(session, organization_id, product_key)
    if existing is not None:
        raise ConflictProblem(f"this organization already has a subscription for {product_key!r}")

    version = _validate_new_pin(await catalog.get_plan_version(plan_version_id), organization_id)
    _require_product(version, product_key)

    features = version.feature_pairs()
    # Read off the pinned version and nothing else — never the request body.
    members = domain.entitled_products(product_key, features)
    if domain.declares_bundle(features):
        await _check_bundle(registry, product_key, members)
    trial_days = domain.trial_days_granted(trial_requested, domain.trial_allowance(features))

    now = datetime.now(UTC)
    to_state = STATE_TRIAL if trial_days else STATE_ACTIVE
    trial_ends_at = now + timedelta(days=trial_days) if trial_days else None

    subscription = Subscription(
        organization_id=organization_id,
        product_key=product_key,
        plan_version_id=version.id,
        state=to_state,
        trial_ends_at=trial_ends_at,
        current_period_start=now,
        current_period_end=domain.period_end(now, version.billing_interval),
        billing_interval=version.billing_interval,
        currency=version.currency.upper(),
        entitled_products_snapshot=members,
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
        actor=actor,
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
    """A customer re-pins their own subscription. A bundle subscription — or a move onto a
    bundle rung — is the platform team's to make, so both are refused here."""
    if subscription.entitled_products != [subscription.product_key]:
        raise ForbiddenProblem("a bundle's plan is changed by the OpsGrids team")
    return await _change_plan(
        session,
        organization_id=ctx.organization_id,
        actor=_customer_actor(ctx),
        catalog=catalog,
        registry=None,
        subscription=subscription,
        plan_version_id=payload.plan_version_id,
        request=request,
    )


async def change_plan_as_operator(
    session: AsyncSession,
    catalog: PlanCatalog,
    registry: ProductRegistry,
    subscription: Subscription,
    payload: OperatorChangePlanIn,
    *,
    request: Request | None = None,
) -> Subscription:
    """The platform team re-pins a subscription — how a bundle moves between rungs."""
    return await _change_plan(
        session,
        organization_id=payload.organization_id,
        actor=_operator_actor(payload.operator_id),
        catalog=catalog,
        registry=registry,
        subscription=subscription,
        plan_version_id=payload.plan_version_id,
        request=request,
    )


async def _change_plan(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor: dict[str, Any],
    catalog: PlanCatalog,
    registry: ProductRegistry | None,
    subscription: Subscription,
    plan_version_id: uuid.UUID,
    request: Request | None,
) -> Subscription:
    """Re-pin a subscription to a different published version — an upgrade or a downgrade.

    The previous pin is preserved on the transition row, never overwritten: the version a past
    invoice was computed against still exists and is still named, exactly as ADR-0004 §2 requires.
    An upgrade and a downgrade are the same state (`active`/`trial` unchanged), told apart by
    whether the new list price is higher — which decides only the event, not the money.
    """
    new_version = _validate_new_pin(
        await catalog.get_plan_version(plan_version_id), organization_id
    )
    if new_version.id == subscription.plan_version_id:
        raise BusinessRuleProblem("the subscription is already pinned to that plan version")
    # A change-plan moves along one product's ladder. Re-pinning a `suite` subscription to an
    # `hrms` version would change what the row is *for* while keeping its key — and with
    # bundles, silently change which products it entitles.
    _require_product(new_version, subscription.product_key)
    features = new_version.feature_pairs()
    members = domain.entitled_products(subscription.product_key, features)
    if domain.declares_bundle(features):
        await _check_bundle(registry, subscription.product_key, members)
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
    # Money moves from the next period, not now (ADR-0005 addendum, 2026-09-23: no mid-period
    # proration). The billing run prices the period it opens off whatever is pinned when it
    # claims it, and rolls the period on the cadence recorded here — so neither the current
    # period's boundaries nor anything already invoiced changes under this re-pin.
    subscription.billing_interval = new_version.billing_interval
    # Re-snapshotted with the pin, in the same transaction: the event below carries the new
    # member set, and product-service diffs it against what this source entitled before — a
    # trio→duo downgrade de-sources the member the duo does not include.
    subscription.entitled_products_snapshot = members

    # Rebuild the items against the new pin. The old items go with the old pin, but that pin is
    # preserved on the transition row below, so history stays intact (ADR-0004 §2).
    _replace_items(subscription, new_version)

    _transition(
        session,
        subscription,
        decision,
        actor=actor,
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


# ------------------------------------------------------------------ bundles (commerce-packaging)

#: The service actor on the one-off snapshot backfill. No human and no customer: an operator ran a
#: reviewed deploy step, and what it writes is derived entirely from the pinned catalog version.
_BACKFILL_ACTOR = {"type": "service", "id": "subscription-backfill"}


async def resync_entitlements(
    session: AsyncSession,
    subscription: Subscription,
    version: CatalogPlanVersion | None,
    *,
    request: Request | None = None,
) -> bool:
    """Fill in `entitled_products` for a subscription that predates it. True if it wrote.

    A no-op for a row that already has its snapshot — so the backfill is safe to re-run, and safe
    to run while the API is writing snapshots of its own. For a non-bundle the snapshot is just
    `[product_key]`, which is what the projection already believed, so nothing is emitted. For a
    **bundle** it is the fix: before this, product-service projected a single activation for the
    bundle's own key, which no route checks — the customer paid for three products and was
    entitled to none. `commerce.subscription.entitlements_resynced` carries the full lifecycle
    payload, so the projection fans the members out (and retires the dead bundle-key activation)
    with the same handler it uses for every transition, and account installs their manifests.

    Not a transition: the state and the pin are unchanged, so no transition row is written and
    the operator console's MRR/churn stream is not disturbed by a data repair.
    """
    if subscription.entitled_products_snapshot is not None:
        return False
    if version is None:
        # The pin no longer resolves in catalog. There is nothing honest to snapshot; leave the
        # row NULL (read as `[product_key]`) and let the operator see it in the report.
        log.warning(
            "entitlements.resync.unresolvable_pin",
            subscription_id=str(subscription.id),
            plan_version_id=str(subscription.plan_version_id),
        )
        return False

    members = domain.entitled_products(subscription.product_key, version.feature_pairs())
    if version.id != subscription.plan_version_id:
        return False
    # Conditional, not read-then-write: a change-plan that committed after this row was read has
    # already snapshotted the members of the *new* pin, and writing the old pin's members over
    # them would entitle the plan the customer just left. The WHERE clause is the guard — no row
    # matched means somebody else got there first, and that write is the right one.
    written = await session.execute(
        update(Subscription)
        .where(
            Subscription.id == subscription.id,
            Subscription.entitled_products_snapshot.is_(None),
            Subscription.plan_version_id == version.id,
        )
        .values(entitled_products_snapshot=members)
        .execution_options(synchronize_session="fetch")
    )
    if getattr(written, "rowcount", 0) != 1:
        log.info("entitlements.resync.superseded", subscription_id=str(subscription.id))
        return False
    subscription.entitled_products_snapshot = members

    record_audit(
        session,
        AuditLog,
        organization_id=subscription.organization_id,
        actor_id=None,
        acting_as=_BACKFILL_ACTOR["id"],
        action=domain.EVT_ENTITLEMENTS_RESYNCED,
        entity="subscription",
        entity_id=subscription.id,
        after={"entitled_products": members, "plan_version_id": str(subscription.plan_version_id)},
        reason="entitled_products_backfill",
        request=request,
    )
    if members != [subscription.product_key]:
        publish_event(
            session,
            OutboxEvent,
            organization_id=subscription.organization_id,
            event_type=domain.EVT_ENTITLEMENTS_RESYNCED,
            aggregate_type="subscription",
            aggregate_id=subscription.id,
            payload=_lifecycle_payload(subscription),
            actor=_BACKFILL_ACTOR,
        )
    await session.flush()
    return True


# ------------------------------------------------------ the billing run (ADR-0005 addendum)

#: The service actor on every write the billing run makes. No human: a clock passed a stored
#: boundary, and billing priced what the pin says.
_BILLING_ACTOR = {"type": "service", "id": "subscription-billing-run"}


def _billing_facts(period: BillingPeriod) -> dict[str, Any]:
    """The invoice a transition was raised with, on the event it rides — so a notification can
    say "your first invoice, ₹X for 1 Oct to 31 Oct" from the event alone."""
    return {
        "billing": {
            "period_id": str(period.id),
            "kind": period.kind,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
            "invoice_id": str(period.invoice_id) if period.invoice_id else None,
            "total_minor": period.total_minor,
            "currency": period.currency,
        }
    }


def _items_snapshot(subscription: Subscription) -> list[dict[str, Any]]:
    return [
        {"price_id": str(item.price_id), "quantity": int(item.quantity)}
        for item in sorted(subscription.items, key=lambda i: str(i.price_id))
    ]


async def claim_billing_period(
    session: AsyncSession,
    subscription: Subscription,
    due: domain.DuePeriod,
    *,
    now: datetime,
) -> BillingPeriod | None:
    """The **claim** of claim → call → record: write (or re-find) the period's row and lease it.

    The caller holds the subscription `FOR UPDATE SKIP LOCKED`, so this runs for one replica at a
    time per subscription. The row is inserted `ON CONFLICT DO NOTHING` on the period, which makes
    a crash-and-retry find the row it wrote last time — with the pin and items it snapshotted
    then — rather than a new one priced off whatever changed since. Returns None when another
    replica's lease is still live (its billing call is out) or the backoff after a failure has not
    elapsed; the next pass tries again.
    """
    from app import selectors

    stmt = (
        pg_insert(BillingPeriod)
        .values(
            id=uuid.uuid4(),
            organization_id=subscription.organization_id,
            subscription_id=subscription.id,
            kind=due.kind,
            period_start=due.start,
            period_end=due.end,
            usage_start=due.usage_start,
            usage_end=due.usage_end,
            plan_version_id=subscription.plan_version_id,
            currency=subscription.currency,
            items=_items_snapshot(subscription),
            state=PERIOD_PENDING,
            attempts=0,
        )
        .on_conflict_do_nothing(
            index_elements=["organization_id", "subscription_id", "period_start"]
        )
    )
    await session.execute(stmt)
    period = await selectors.lock_billing_period(session, subscription.id, due.start)
    if period is None or period.state != PERIOD_PENDING:
        # Recorded already, yet the subscription still reads as due for it — only possible if the
        # period was closed as superseded. Nothing to call; the stale-claim pass explains it.
        return None
    if period.lease_until is not None and period.lease_until > now:
        return None
    period.lease_until = now + domain.BILLING_LEASE
    period.attempts += 1
    await session.flush()
    return period


async def skip_suspended_period(
    session: AsyncSession,
    subscription: Subscription,
    due: domain.DuePeriod,
    *,
    now: datetime,
) -> BillingPeriod:
    """Roll a suspended subscription's period without invoicing it.

    A suspended customer is not served and is not billed for the time (ADR-0005 §4: the ladder
    stops at `suspended`; nothing accrues after it). The period still advances, so a customer who
    pays in week six comes back on a current period rather than being invoiced, one pass at a
    time, for every month they were switched off. Not a transition — the state does not change —
    so it is an audit row and a `skipped` period row, and no event.
    """
    period = BillingPeriod(
        organization_id=subscription.organization_id,
        subscription_id=subscription.id,
        kind=due.kind,
        period_start=due.start,
        period_end=due.end,
        usage_start=due.usage_start,
        usage_end=due.usage_end,
        plan_version_id=subscription.plan_version_id,
        currency=subscription.currency,
        items=_items_snapshot(subscription),
        state=PERIOD_SKIPPED,
        attempts=0,
        outcome="skipped_suspended",
        recorded_at=now,
    )
    session.add(period)
    subscription.current_period_start = due.start
    subscription.current_period_end = due.end
    record_audit(
        session,
        AuditLog,
        organization_id=subscription.organization_id,
        actor_id=None,
        acting_as=_BILLING_ACTOR["id"],
        action="commerce.billing_run.period_skipped",
        entity="subscription",
        entity_id=subscription.id,
        after={
            "period_start": due.start.isoformat(),
            "period_end": due.end.isoformat(),
            "state": subscription.state,
        },
        reason="suspended",
    )
    await session.flush()
    return period


def still_due_for(subscription: Subscription, period: BillingPeriod) -> bool:
    """Is the subscription still exactly where it was when this period was claimed?"""
    if period.kind == domain.KIND_TRIAL_CONVERSION:
        return (
            subscription.state == STATE_TRIAL and subscription.trial_ends_at == period.period_start
        )
    return (
        subscription.state in (STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED)
        and subscription.current_period_end == period.period_start
    )


async def record_billing_period(
    session: AsyncSession,
    subscription: Subscription,
    period: BillingPeriod,
    *,
    invoice_id: uuid.UUID,
    total_minor: int,
    collectable: bool | None,
    now: datetime,
) -> str:
    """The **record** of claim → call → record: write billing's answer and move the subscription.

    One transaction, the subscription held `FOR UPDATE`. Returns the outcome it wrote.

    * **Already recorded** (another replica got here first): nothing, `already_recorded`.
    * **The subscription moved on** since the claim — cancelled, or advanced by hand — the
      invoice exists in billing, so it is recorded here and the subscription is left alone:
      `superseded`, logged at warning for a person to void it.
    * **A trial conversion** opens the first paid period at the trial's end. With nothing to
      collect, or a mandate that can collect it, `trial -> active` (`activated`). Otherwise
      `trial -> past_due` and a dunning cycle anchored at the trial's end (ADR-0013 §6,
      ADR-0005): the product keeps working for the grace window, and the ladder asks payment to
      retry this invoice.
    * **A renewal** rolls the period and emits `renewed` (`active` or `past_due` unchanged). One
      that became suspended between claim and record rolls with no event.
    """
    if period.state != PERIOD_PENDING:
        return "already_recorded"

    period.invoice_id = invoice_id
    period.total_minor = total_minor
    period.state = PERIOD_INVOICED
    period.recorded_at = now
    period.lease_until = None
    period.last_error = None

    if not still_due_for(subscription, period):
        period.outcome = "superseded"
        log.warning(
            "billing_run.period.superseded",
            organization_id=str(subscription.organization_id),
            subscription_id=str(subscription.id),
            invoice_id=str(invoice_id),
            state=subscription.state,
            hint="the subscription moved between claim and record; review and void the invoice",
        )
        record_audit(
            session,
            AuditLog,
            organization_id=subscription.organization_id,
            actor_id=None,
            acting_as=_BILLING_ACTOR["id"],
            action="commerce.billing_run.period_superseded",
            entity="subscription",
            entity_id=subscription.id,
            after={"invoice_id": str(invoice_id), "state": subscription.state},
            reason="superseded",
        )
        await session.flush()
        return "superseded"

    subscription.current_period_start = period.period_start
    subscription.current_period_end = period.period_end

    if period.kind == domain.KIND_TRIAL_CONVERSION:
        if total_minor == 0 or collectable:
            decision = domain.decide(
                from_state=STATE_TRIAL, to_state=STATE_ACTIVE, reason=domain.REASON_TRIAL_CONVERT
            )
            period.outcome = "activated"
            _transition(
                session,
                subscription,
                decision,
                actor=_BILLING_ACTOR,
                request=None,
                from_plan_version_id=subscription.plan_version_id,
                to_plan_version_id=subscription.plan_version_id,
                extra=_billing_facts(period),
            )
        else:
            # The anchor is the trial's end — the moment the debt arose — never `now`: a sweep
            # that ran late must not hand the customer a longer window than the one promised.
            opened_at = period.period_start
            window_ends_at = domain.grace_ends_at(opened_at)
            cycle = DunningCycle(
                organization_id=subscription.organization_id,
                subscription_id=subscription.id,
                invoice_id=invoice_id,
                amount_minor=total_minor,
                currency=period.currency,
                state=DUNNING_OPEN,
                opened_at=opened_at,
                grace_ends_at=window_ends_at,
                next_action_at=domain.next_action_at(opened_at, 0, window_ends_at),
                attempts_made=0,
                failures_seen=1,
            )
            session.add(cycle)
            await session.flush()
            decision = domain.decide(
                from_state=STATE_TRIAL, to_state=STATE_PAST_DUE, reason=domain.REASON_TRIAL_UNPAID
            )
            period.outcome = "past_due"
            _transition(
                session,
                subscription,
                decision,
                actor=_BILLING_ACTOR,
                request=None,
                from_plan_version_id=subscription.plan_version_id,
                to_plan_version_id=subscription.plan_version_id,
                extra={**_dunning_facts(cycle), **_billing_facts(period)},
            )
    elif subscription.state == STATE_SUSPENDED:
        period.outcome = "rolled_while_suspended"
    else:
        decision = domain.decide(
            from_state=subscription.state,
            to_state=subscription.state,
            reason=domain.REASON_RENEW,
        )
        period.outcome = "renewed"
        _transition(
            session,
            subscription,
            decision,
            actor=_BILLING_ACTOR,
            request=None,
            from_plan_version_id=subscription.plan_version_id,
            to_plan_version_id=subscription.plan_version_id,
            extra=_billing_facts(period),
        )
    await session.flush()
    return period.outcome or "recorded"


async def fail_billing_period(
    session: AsyncSession, period: BillingPeriod, *, error: str, now: datetime
) -> None:
    """A billing or payment call failed: keep the claim, say why, and back off.

    The row stays `pending` — the only state the run retries — with the error a person can act
    on and a lease pushed out by `domain.retry_backoff`, so a data defect is not hammered every
    pass and a sibling's outage is retried soon after it ends.
    """
    period.last_error = error[:300]
    period.lease_until = now + domain.retry_backoff(period.attempts)
    await session.flush()


async def close_stale_period(
    session: AsyncSession, period: BillingPeriod, *, now: datetime
) -> None:
    """A pending claim whose subscription is no longer due for it — cancelled, say, after the
    claim and before the record. It is closed `skipped`/`superseded` so the health probe stops
    counting it, with the key billing would have used named in the error for a person to check:
    if the crash came after billing committed, an invoice exists there that nothing recorded."""
    period.state = PERIOD_SKIPPED
    period.outcome = "superseded"
    period.recorded_at = now
    period.lease_until = None
    period.last_error = (
        f"subscription moved before record; check billing for key "
        f"subscription:{period.subscription_id}:{period.period_start.isoformat()}"
    )[:300]
    log.warning(
        "billing_run.period.stale_closed",
        organization_id=str(period.organization_id),
        subscription_id=str(period.subscription_id),
        period_start=period.period_start.isoformat(),
    )
    await session.flush()
