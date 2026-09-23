"""Writes for product.

Business rules, audit rows and outbox rows. The change, its audit entry and any event it emits
all commit or all roll back — the tenant chokepoint owns the transaction, so nothing here opens
one of its own.

An activation's status is never written directly any more. It is **recomputed from its sources**
(`activation_sources`) — the subscription that provides it, a bundle that includes it, or a hand
activation — and it is on while any of them is (ADR-0004 §5, addendum). Every write path below
changes a source and then calls `_recompute`, so the rule lives in exactly one place.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import (
    SOURCE_ACTIVE,
    SOURCE_REMOVED,
    SOURCE_SUSPENDED,
    SOURCE_TRIAL,
    Activation,
    ActivationSource,
    AuditLog,
    OutboxEvent,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem, NotFoundProblem
from platform_core.events import publish_event
from platform_core.logging import get_logger

log = get_logger("product.services")

EVT_ACTIVATED = "core.product.activated"
EVT_DEACTIVATED = "core.product.deactivated"

#: The subscription states that keep a source entitling, and the ones that switch it off. The
#: source vocabulary is narrower than the subscription's (`trial | active | suspended`), so
#: several lifecycle states fold onto `active`, and both terminal states fold onto `suspended` —
#: `Activation.entitles` then evaluates trial expiry at read time exactly as it does for a
#: hand-activated trial (ADR-0004 §5).
_PROJECTION_TRIAL = "trial"
_PROJECTION_DE_ENTITLING = ("suspended", "cancelled")


def _source_status(state: str) -> str:
    if state in _PROJECTION_DE_ENTITLING:
        return SOURCE_SUSPENDED
    if state == _PROJECTION_TRIAL:
        return SOURCE_TRIAL
    # active, past_due, and the renew/upgrade/downgrade moves that stay active.
    return SOURCE_ACTIVE


def _members(product_key: str, entitled_products: list[str] | None) -> list[str]:
    """The activations one subscription event speaks for, de-duplicated, in order.

    `entitled_products` absent is an event from before bundles fanned out, and it speaks for the
    product it names — the default every consumer of the additive field agreed on.
    """
    members: list[str] = []
    for key in entitled_products or [product_key]:
        if key not in members:
            members.append(key)
    return members


async def _lock(session: AsyncSession, organization_id: uuid.UUID, kind: str, key: str) -> None:
    """A transaction-scoped advisory lock on `org:kind:key`, released at commit or rollback.

    `src` locks serialise every event for one subscription; `act` locks serialise every write
    that recomputes one activation. Callers that need several take them in a fixed order —
    the source first, then activations sorted by key — so two folds can never wait on each
    other in a cycle.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{organization_id}:{kind}:{key}"},
    )


async def _honoured_members(
    session: AsyncSession, product_key: str, entitled_products: list[str] | None
) -> list[str]:
    """The members an event may actually switch on, judged against the product registry.

    A member list is only honoured on a subscription bought under a **bundle** product — a plain
    product's event speaks for that product alone, whatever list it carries. And a member that is
    not a registered, sellable (`status != 'soon'`), non-platform, non-bundle product is dropped
    rather than activated: catalog refuses such a bundle when it is authored, and this is the net
    under that for a version authored before the rule, or a product whose launch was rolled back.
    """
    members = _members(product_key, entitled_products)
    registry = {p.key: p for p in await selectors.list_products(session)}
    source = registry.get(product_key)
    if source is not None and source.is_bundle and members == [product_key]:
        # A bundle speaking only for itself entitles a key no route checks. Nothing to project.
        log.warning("subscription.projection.bundle_without_members", source=product_key)
        return []
    if members == [product_key]:
        return members
    if source is None or not source.is_bundle:
        log.warning(
            "subscription.projection.members_ignored",
            source=product_key,
            reason="not a bundle product",
            entitled_products=members,
        )
        return [product_key]
    honoured = []
    for member in members:
        entry = registry.get(member)
        if entry is None or entry.is_platform or entry.is_bundle or entry.status == "soon":
            log.warning(
                "subscription.projection.member_refused",
                source=product_key,
                member=member,
                status=entry.status if entry else None,
            )
            continue
        honoured.append(member)
    return honoured


async def project_subscription(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    product_key: str,
    state: str,
    trial_ends_at: datetime | None,
    occurred_at: datetime | None = None,
    entitled_products: list[str] | None = None,
    subscription_id: uuid.UUID | None = None,
) -> list[Activation]:
    """Fold one subscription lifecycle event into the activations it entitles.

    Called by the event consumer, once per `commerce.subscription.*` message, inside a
    `session_scope(organization_id=...)` so the tenant GUC is set and these NOBYPASSRLS writes
    pass the forced policies on `activation_sources` and `activations`.

    The subscription is **one source**, named by the key it was bought under (`product_key`). It
    entitles `entitled_products` — its bundle's members, or just itself — and the fold is:

    1. **Guard, per source.** If this source has already applied an event at or after this one's
       `occurred_at`, the whole event is stale and is dropped. Idempotency (the processed-events
       ledger) catches an exact redelivery; this catches a *distinct older* event folded late —
       multi-replica relays, consumers sharing a queue, a DLQ replay. It is per source because a
       standalone subscription and a bundle are independent histories. The guard is only sound
       because the read and the write it permits happen under a transaction-scoped advisory
       lock on the source: without it, two consumer replicas folding an older and a newer event
       at once both read the same watermark and the later *commit* wins, not the later *event*.
    2. **Upsert one source row per member** at the state the event carries.
    3. **De-source what the event no longer names.** A trio→duo downgrade names two members; the
       third member's row of this source becomes `removed` — a tombstone stamped with this event's
       time, so an older trio event arriving late is dropped by step 1 rather than re-adding it.
    4. **Recompute every activation touched**, as the OR over its sources. An activation another
       live source still feeds — the standalone HRMS under a cancelled suite — stays on. Each
       recompute runs under an advisory lock on that activation, which the hand activate and
       deactivate paths take too, so two sources folding at once (or a fold racing an owner's
       deactivation) cannot each OR together a view that excludes the other's uncommitted write.

    Driven by the subscription's `state`, not the event name, so renew/upgrade/downgrade all fold
    as `active`. No product outbox event is emitted: this is a projection *of* the lifecycle, and
    re-emitting would risk a loop. De-entitlement is **eventual** — see the consumer.
    """
    members = await _honoured_members(session, product_key, entitled_products)

    # Serialise before reading anything. The source lock orders every event for this
    # subscription (so the watermark read below cannot be overtaken by a concurrent older or
    # newer event), and the activation locks — taken in sorted order over the union of the
    # members this source feeds now and fed before — order this fold against every other
    # source's fold and the hand activate/deactivate paths for the same products.
    await _lock(session, organization_id, "src", product_key)
    previous = await selectors.sources_from(session, organization_id, product_key)
    for member in sorted({*members, *(r.product_key for r in previous)}):
        await _lock(session, organization_id, "act", member)

    rows = await selectors.sources_from(session, organization_id, product_key, fresh=True)
    watermark = max((r.state_occurred_at for r in rows if r.state_occurred_at), default=None)
    if occurred_at is not None and watermark is not None and occurred_at <= watermark:
        log.info(
            "subscription.projection.stale_dropped",
            organization_id=str(organization_id),
            source=product_key,
            event_occurred_at=occurred_at.isoformat(),
            source_reflects=watermark.isoformat(),
        )
        return []

    status = _source_status(state)
    trial_ends = trial_ends_at if status == SOURCE_TRIAL else None
    by_member = {r.product_key: r for r in rows}
    touched: list[str] = []

    for member in members:
        row = by_member.get(member)
        if row is None:
            session.add(
                ActivationSource(
                    organization_id=organization_id,
                    product_key=member,
                    source_product_key=product_key,
                    source_subscription_id=subscription_id,
                    status=status,
                    trial_ends_at=trial_ends,
                    state_occurred_at=occurred_at,
                )
            )
        else:
            row.status = status
            row.trial_ends_at = trial_ends
            if subscription_id is not None:
                row.source_subscription_id = subscription_id
            if occurred_at is not None:
                row.state_occurred_at = occurred_at
        touched.append(member)

    for row in rows:
        if row.product_key in members or row.status == SOURCE_REMOVED:
            continue
        row.status = SOURCE_REMOVED
        row.trial_ends_at = None
        if occurred_at is not None:
            row.state_occurred_at = occurred_at
        touched.append(row.product_key)
    await session.flush()

    return [
        await _recompute(
            session,
            organization_id=organization_id,
            product_key=member,
            acting_as="subscription-projection",
            cause={"source": product_key, "from_state": state},
        )
        for member in touched
    ]


async def _recompute(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    product_key: str,
    acting_as: str,
    action: str = "product.projected",
    cause: dict[str, Any],
    actor_id: uuid.UUID | None = None,
    activated_by: uuid.UUID | None = None,
    request: Request | None = None,
) -> Activation:
    """Set an activation from its sources: on while **any** source is.

    * any `active` source → `active`;
    * otherwise any `trial` source → `trial`, ending at the latest of their ends (an open-ended
      trial wins). A lapsed trial is left as a lapsed `trial` rather than flipped to `suspended`:
      `Activation.entitles` evaluates expiry at read time, and that is the only clock this
      platform trusts for it;
    * otherwise → `suspended`, with `deactivated_at` kept from the first time it went off, so
      "when did they lose this?" has one answer.

    The row is kept and updated, never duplicated or deleted (one per organization per product).
    `state_occurred_at` mirrors the newest source, for an operator reading the row; the ordering
    guard itself lives on the sources.
    """
    sources = await selectors.sources_for(session, organization_id, product_key)
    active = [s for s in sources if s.status == SOURCE_ACTIVE]
    trials = [s for s in sources if s.status == SOURCE_TRIAL]

    trial_ends: datetime | None = None
    if active:
        new_status = "active"
    elif trials:
        new_status = "trial"
        ends = [s.trial_ends_at for s in trials]
        trial_ends = None if any(e is None for e in ends) else max(e for e in ends if e)
    else:
        new_status = "suspended"

    activation = await selectors.get_activation(session, organization_id, product_key)
    before = {"status": activation.status} if activation else None
    newest = max((s.state_occurred_at for s in sources if s.state_occurred_at), default=None)
    if activation is None:
        activation = Activation(
            organization_id=organization_id,
            product_key=product_key,
            status=new_status,
            trial_ends_at=trial_ends,
            deactivated_at=datetime.now(UTC) if new_status == "suspended" else None,
            state_occurred_at=newest,
            activated_by=activated_by,
        )
        session.add(activation)
    else:
        activation.status = new_status
        activation.trial_ends_at = trial_ends
        if new_status == "suspended":
            activation.deactivated_at = activation.deactivated_at or datetime.now(UTC)
        else:
            activation.deactivated_at = None
        if newest is not None:
            activation.state_occurred_at = newest
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=actor_id,
        acting_as=acting_as,
        action=action,
        entity="activation",
        entity_id=activation.id,
        before=before,
        after={
            "status": activation.status,
            "product_key": product_key,
            **cause,
            "sources": {s.source_product_key: s.status for s in sources},
        },
        request=request,
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

    A hand activation is the product's **own source** — the same source row its standalone
    subscription folds into, exactly as the two always shared the activation.
    """
    product = await selectors.get_product(session, product_key)
    if product is None:
        raise NotFoundProblem(f"No product named {product_key!r}")
    if product.is_bundle:
        # A bundle has nothing of its own to switch on. It is bought as a subscription, and the
        # projection activates its members.
        raise BusinessRuleProblem(
            f"{product.name} is a bundle. Subscribe to one of its plans to activate its products."
        )
    if product.status == "soon":
        # Refused rather than allowed-and-broken: activating something with no service behind
        # it produces a customer who has paid for 404s.
        raise BusinessRuleProblem(
            f"{product.name} is not available yet. It can be activated once it ships."
        )

    trial_ends = datetime.now(UTC) + timedelta(days=trial_days) if trial_days is not None else None
    # The same activation lock the projection takes, before anything is read.
    await _lock(session, ctx.organization_id, "act", product_key)
    # `state_occurred_at` is left alone: this path is not event-driven and carries no ordering to
    # guard, exactly as it never stamped the activation.
    source = await selectors.get_source(session, ctx.organization_id, product_key, product_key)
    if source is None:
        session.add(
            ActivationSource(
                organization_id=ctx.organization_id,
                product_key=product_key,
                source_product_key=product_key,
                status=SOURCE_TRIAL if trial_days else SOURCE_ACTIVE,
                trial_ends_at=trial_ends,
            )
        )
    else:
        source.status = SOURCE_TRIAL if trial_days else SOURCE_ACTIVE
        source.trial_ends_at = trial_ends
    await session.flush()

    activation = await _recompute(
        session,
        organization_id=ctx.organization_id,
        product_key=product_key,
        acting_as="product-activation",
        action="product.activated",
        cause={"source": product_key, "manual": "activate"},
        actor_id=ctx.principal.sub,
        activated_by=ctx.principal.membership_id,
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

    Refused while another live source — a bundle that includes the product — still feeds it:
    switching off the product's own source would leave it on anyway, and a 200 that changes
    nothing is a lie.
    """
    # The same activation lock the projection takes, before anything is read: a suite fold
    # committing between the check below and the write would otherwise be ORed away.
    await _lock(session, ctx.organization_id, "act", product_key)
    activation = await selectors.get_activation(session, ctx.organization_id, product_key)
    if activation is None:
        raise NotFoundProblem(f"{product_key} is not activated for this organization")

    sources = await selectors.sources_for(session, ctx.organization_id, product_key)
    elsewhere = sorted(
        s.source_product_key for s in sources if s.source_product_key != product_key and s.is_live
    )
    if elsewhere:
        raise BusinessRuleProblem(
            f"{product_key} is included in your {', '.join(elsewhere)} subscription; "
            "change or cancel that instead"
        )
    own = next((s for s in sources if s.source_product_key == product_key), None)
    if own is not None:
        own.status = SOURCE_SUSPENDED
        own.trial_ends_at = None
        await session.flush()
    activation = await _recompute(
        session,
        organization_id=ctx.organization_id,
        product_key=product_key,
        acting_as="product-deactivation",
        action="product.deactivated",
        cause={"source": product_key, "manual": "deactivate"},
        actor_id=ctx.principal.sub,
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
