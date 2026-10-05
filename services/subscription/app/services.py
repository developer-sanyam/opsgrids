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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.catalog import PlanCatalog
from app.models import (
    DUNNING_ABANDONED,
    DUNNING_EXHAUSTED,
    DUNNING_OPEN,
    DUNNING_RECOVERED,
    PERIOD_FINALIZE_PENDING,
    PERIOD_FINALIZED,
    PERIOD_PENDING,
    PERIOD_SKIPPED,
    PERIOD_VOID_PENDING,
    PERIOD_VOIDED,
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
    ChangePlanPreviewOut,
    ExpectedState,
    OperatorChangePlanIn,
    OperatorChangePlanPreviewIn,
    OperatorExtendTrialIn,
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
    """One period's recurring list price, for classifying a plan change.

    Sums the recurring components (`flat`/`per_seat`, at quantity one); a usage rate has no fixed
    amount to compare. It decides the change's **direction** — upgrade (prorated now), downgrade
    (deferred to the next period) or equal (no money) — via `domain.plan_change_direction`, which
    compares it per day. It never prices anything: billing prices the proration itself from
    catalog and the subscription's items.
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
        raise domain.CodedNotFound(domain.CODE_NOT_FOUND, "No such plan version")
    if version.restricted_to_org_id is not None and version.restricted_to_org_id != organization_id:
        # The same body a nonexistent version gets, code included: another organization's
        # negotiated price must not be distinguishable from no such version. Decided before the
        # state check, so that holds whatever its state — a draft prepared for someone else must
        # not answer `not_published` and so confirm it exists.
        raise domain.CodedNotFound(domain.CODE_NOT_FOUND, "No such plan version")
    if version.state != VERSION_PUBLISHED:
        raise domain.CodedRefusal(
            domain.CODE_NOT_PUBLISHED,
            "that plan version is not published and cannot be subscribed to",
        )
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
    note: str | None = None,
    audit_before: dict[str, Any] | None = None,
    audit_after: dict[str, Any] | None = None,
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

    `note` is a person's reason, in words. It is **not** the transition's `reason`: that column is
    sixty characters of machine code (`decide` types the event from it, and a free-text word could
    name the wrong event). The note is the audit row's `reason` (text, unbounded here) and nothing
    else: it is **never put on the event**. The event is stamped with the customer's organization,
    and notification renders a customer org's own templates from the whole payload — a rule on
    `commerce.subscription.*` with `{{note}}` in its body would mail staff's internal reason to the
    customer. Staff read the why on the audit trail, which is where it stays.

    `audit_before`/`audit_after` add facts to the audit row's before/after for a move whose state
    alone does not say what changed — a trial extension stays `trial`, and its row names the end
    it moved from and to.
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
        before=(
            {"state": decision.from_state, **(audit_before or {})} if decision.from_state else None
        ),
        after={
            "state": decision.to_state,
            "plan_version_id": str(subscription.plan_version_id),
            **(audit_after or {}),
        },
        reason=note or decision.reason,
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
        raise domain.CodedRefusal(
            domain.CODE_PRODUCT_MISMATCH, "that plan version names no product and cannot be pinned"
        )
    if version.product_key != product_key:
        raise domain.CodedRefusal(
            domain.CODE_PRODUCT_MISMATCH,
            f"plan version belongs to product {version.product_key!r}, not {product_key!r}",
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
        raise domain.CodedRefusal(
            domain.CODE_BUNDLE, "cannot sell this bundle: " + "; ".join(problems)
        )


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
    """The platform team opens a subscription for a customer — the one path that sells a bundle,
    and console's "Add product" (ADR-0030).

    Stricter than the customer path in two ways a staff surface needs (the customer path is left
    as it was — see ADR-0030, "Not changed"): a `one_time` version is refused (it has no period
    and the run never bills it, so it would be a free subscription), and so is a currency other
    than the one the organization's live subscriptions are billed in (console sums one currency
    per organization). Serialised per organization with a transaction-scoped advisory lock, so two
    staff adds racing each other cannot both pass the currency check, or both pass the
    one-row-per-product check and leave one of them to the unique index.
    """
    domain.assert_free_text_allowed(payload.note)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{payload.organization_id}:operator-subscribe"},
    )
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
        note=payload.note,
        operator_rules=True,
    )


async def _assert_org_currency(
    session: AsyncSession, organization_id: uuid.UUID, currency: str
) -> None:
    """Refuse a currency the organization's other **live** subscriptions are not billed in.

    Live is every state but `cancelled`: a past-due or suspended subscription still bills (or will
    again). One currency per organization is what console's MRR assumes — it sums amounts and
    takes the first currency — so a mixed-currency add would corrupt every figure for the account.
    """
    from app import selectors

    live = {
        s.currency.upper()
        for s in await selectors.list_by_organization(session, organization_id)
        if s.state != STATE_CANCELLED
    }
    if live and currency not in live:
        billed = ", ".join(sorted(live))
        raise domain.CodedRefusal(
            domain.CODE_ORG_CURRENCY_MISMATCH,
            f"this organization's subscriptions are billed in {billed}; a {currency} plan cannot "
            "be added beside them",
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
    note: str | None = None,
    operator_rules: bool = False,
) -> Subscription:
    """Start a subscription pinned to a validated catalog version.

    A trial opens in `trial` with an end date (a subscription state, never a separate object —
    ADR-0004 §4), its length the version's and not the caller's. One subscription per product per
    organization: an existing one — in any state, `cancelled` included — is a 409
    `already_subscribed`, not a second row. `operator_rules` adds the staff path's refusals
    (`one_time`, `org_currency_mismatch`; see `subscribe_as_operator`).
    """
    from app import selectors

    existing = await selectors.get_by_product(session, organization_id, product_key)
    if existing is not None:
        raise _already_subscribed(product_key, existing.state)

    version = _validate_new_pin(await catalog.get_plan_version(plan_version_id), organization_id)
    _require_product(version, product_key)
    if operator_rules:
        if version.billing_interval == domain.INTERVAL_ONE_TIME:
            raise domain.CodedRefusal(
                domain.CODE_ONE_TIME,
                "a one_time plan has no billing period and would never be invoiced; "
                "choose a monthly or yearly plan",
            )
        await _assert_org_currency(session, organization_id, version.currency.upper())

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
    try:
        await session.flush()
    except IntegrityError as exc:
        # A concurrent subscribe for the same product won the unique index between the check
        # above and this insert: the same answer the check gives, not a 500.
        if "uq_subscription_org_product" in str(exc.orig):
            raise _already_subscribed(product_key, None) from exc
        raise
    if to_state == STATE_ACTIVE and subscription.current_period_end is not None:
        # A subscription that starts paid owes its first period now (review round 3): nothing
        # else ever charges it — the run charges a period when it *opens* a later one.
        await claim_opening(
            session,
            subscription,
            at=now,
            full_period_start=now,
            end=subscription.current_period_end,
        )

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
        note=note,
    )
    await session.flush()
    return subscription


def _already_subscribed(product_key: str, state: str | None) -> domain.CodedConflict:
    """409 `already_subscribed`: one row per product per organization, whatever its state."""
    if state == STATE_CANCELLED:
        detail = (
            f"this organization's {product_key!r} subscription was cancelled; a cancelled "
            "subscription cannot be reopened or replaced yet"
        )
    else:
        suffix = f" ({state})" if state else ""
        detail = f"this organization already has a subscription for {product_key!r}{suffix}"
    return domain.CodedConflict(domain.CODE_ALREADY_SUBSCRIBED, detail)


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
    """The platform team re-pins a subscription — how a bundle moves between rungs, and how staff
    switch a plan. `note` is the operator's reason in words; `expected` is the caller's picture of
    the subscription, refused with a 409 `state_changed` if it no longer holds."""
    domain.assert_free_text_allowed(payload.note)
    return await _change_plan(
        session,
        organization_id=payload.organization_id,
        actor=_operator_actor(payload.operator_id),
        catalog=catalog,
        registry=registry,
        subscription=subscription,
        plan_version_id=payload.plan_version_id,
        request=request,
        forced_timing=payload.timing,
        note=payload.note,
        expected=payload.expected,
    )


async def preview_change_plan_as_operator(
    session: AsyncSession,
    catalog: PlanCatalog,
    registry: ProductRegistry,
    subscription: Subscription,
    payload: OperatorChangePlanPreviewIn,
) -> ChangePlanPreviewOut:
    """What `change_plan_as_operator` would do to this subscription right now. Writes nothing and
    takes no row lock: it is the same `_evaluate_change_plan` the write runs, so the preview and
    the write cannot disagree about direction, timing or refusal.

    Always evaluated as the staff path is — `timing="next_period"` — because staff never force a
    paid change to apply now (ADR-0029): a trial switches immediately and free, a paying
    subscription switches at its next period, and an equal-price change applies now at no charge.
    A refusal is an *answer* (`refusal_code`), not an error; only "no such version" (404) and an
    unreachable catalog (503) raise.
    """
    expected = ExpectedState(
        state=subscription.state,
        plan_version_id=subscription.plan_version_id,
        pending_plan_version_id=subscription.pending_plan_version_id,
    )
    try:
        change = await _evaluate_change_plan(
            session,
            organization_id=payload.organization_id,
            catalog=catalog,
            registry=registry,
            subscription=subscription,
            plan_version_id=payload.plan_version_id,
            forced_timing="next_period",
            lock_claims=False,
        )
    except domain.CodedNotFound:
        raise
    except (BusinessRuleProblem, ConflictProblem) as exc:
        return ChangePlanPreviewOut(
            expected=expected,
            refusal_code=getattr(exc, "code", "refused"),
            refusal_detail=exc.detail,
        )

    current = subscription.entitled_products
    withdrawing = change.action == _ACT_WITHDRAW
    direction_label = {1: "upgrade", -1: "downgrade", 0: "equal"}[change.direction]
    return ChangePlanPreviewOut(
        expected=expected,
        action="withdraw_scheduled" if withdrawing else "switch",
        direction=None if withdrawing else direction_label,
        effective="next_period" if change.action == _ACT_SCHEDULE else "now",
        effective_at=change.effective_at if change.action == _ACT_SCHEDULE else None,
        would_clear_pending=subscription.pending_change is not None,
        entitled_added=[m for m in change.members if m not in current],
        entitled_removed=[m for m in current if m not in change.members],
        usage_priced=change.usage_priced,
        usage_closed_now=change.action == _ACT_APPLY and change.usage_closed_now,
    )


def _assert_expected(subscription: Subscription, expected: ExpectedState) -> None:
    """The caller's picture of the subscription must still be the subscription's own — compared
    under the row lock, before anything is decided. Anything that moved it in between (a customer's
    own change, a period roll, dunning, another operator) is a conflict the operator re-reads and
    decides again, not a change they confirm blind."""
    if (
        subscription.state != expected.state
        or subscription.plan_version_id != expected.plan_version_id
        or subscription.pending_plan_version_id != expected.pending_plan_version_id
    ):
        raise domain.CodedConflict(
            domain.CODE_STATE_CHANGED,
            "this subscription changed since you looked at it; refresh and review it again",
        )


#: What an evaluated change-plan does. `withdraw`: the target is the pin the subscription is on and
#: a change is waiting, so the waiting change is cancelled. `schedule`: the target waits for the
#: next period (nothing about entitlement or money changes now). `apply`: the pin moves now.
_ACT_WITHDRAW = "withdraw"
_ACT_SCHEDULE = "schedule"
_ACT_APPLY = "apply"


@dataclass(frozen=True)
class _PlanChange:
    """The evaluated outcome of a proposed change-plan — shared by the write and the preview."""

    action: str
    new_version: CatalogPlanVersion
    members: list[str]
    direction: int
    #: `upgrade` or `downgrade` (the machine reason the event is typed from); None for a withdraw.
    reason: str | None
    claim: BillingPeriod | None
    paid: bool
    #: When a scheduled change lands: the end of the period it waits for.
    effective_at: datetime | None
    usage_priced: bool
    #: Applying it now closes the usage metered so far on the current pin, at that pin's usage
    #: rates — the `schedule_closing_usage` the apply branch calls before the pin moves, decided by
    #: the same rule (paid time since the period began, and a usage price on the current version).
    usage_closed_now: bool = False


def _has_usage_price(version: CatalogPlanVersion | None) -> bool:
    return version is not None and any(p.kind == "usage" for p in version.prices)


async def _evaluate_change_plan(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    catalog: PlanCatalog,
    registry: ProductRegistry | None,
    subscription: Subscription,
    plan_version_id: uuid.UUID,
    forced_timing: str | None,
    lock_claims: bool,
) -> _PlanChange:
    """Decide what a change-plan would do, or raise the refusal it would get.

    **Error precedence is deliberate.** Every refusal that is a property of the request or of the
    subscription's *state* — a past_due subscription, an unknown version, another product's, a
    different currency or cadence — comes before `billing_catching_up`, which is the one refusal
    that is transient. A subscription that will never be allowed to change must not be told to
    "try again shortly" (and while the billing run is off, that message is indefinite).

    `lock_claims` is False for the preview: the "has the run claimed the due period" check then
    reads the claim row instead of taking `FOR UPDATE` on it.
    """
    from app import selectors

    # Only a trial or a paid-up subscription changes plan. `past_due -> past_due` is a legal edge
    # for the billing run's renewal, and `decide` would therefore accept a self-edge re-pin from
    # `past_due` — so the rule is stated here, explicitly, rather than left to the state machine:
    # a customer who owes money does not move to a dearer plan until they have paid.
    if subscription.state not in (STATE_TRIAL, STATE_ACTIVE):
        raise domain.CodedRefusal(
            domain.CODE_STATE,
            f"a {subscription.state} subscription cannot change plan; "
            "only a trial or an active one can",
        )
    new_version = _validate_new_pin(
        await catalog.get_plan_version(plan_version_id), organization_id
    )
    if new_version.id == subscription.plan_version_id:
        if subscription.pending_change is None:
            raise domain.CodedRefusal(
                domain.CODE_ALREADY_PINNED,
                "the subscription is already pinned to that plan version",
            )
        # Back to the pin they are on: the waiting change is withdrawn.
        await assert_billing_caught_up(session, subscription, lock=lock_claims)
        return _PlanChange(
            action=_ACT_WITHDRAW,
            new_version=new_version,
            members=subscription.entitled_products,
            direction=0,
            reason=None,
            claim=None,
            paid=subscription.state == STATE_ACTIVE,
            effective_at=None,
            usage_priced=_has_usage_price(new_version),
        )
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
        raise domain.CodedRefusal(
            domain.CODE_CURRENCY,
            f"cannot change currency from {subscription.currency} "
            f"to {new_version.currency.upper()}",
        )

    old_version = await catalog.get_plan_version(subscription.plan_version_id)
    # One cadence for the life of a subscription (review round 2, fix B1): a monthly and a yearly
    # period do not line up, so no proration can price one against the other honestly.
    current_interval = (
        subscription.billing_interval
        or domain.infer_interval(subscription.current_period_start, subscription.current_period_end)
        or (old_version.billing_interval if old_version else None)
    )
    domain.assert_same_cadence(current_interval, new_version.billing_interval)
    # The transient refusal comes last (see the docstring).
    await assert_billing_caught_up(session, subscription, lock=lock_claims)
    direction = domain.plan_change_direction(
        _list_price_minor(new_version),
        new_version.billing_interval,
        _list_price_minor(old_version) if old_version else 0,
        current_interval or new_version.billing_interval,
    )
    reason = domain.REASON_DOWNGRADE if direction < 0 else domain.REASON_UPGRADE

    # A lifecycle claim already made — a renewal, or a trial conversion — prices a period this
    # change falls inside (review round 2, fix B2). The change is money against *that* period.
    claim = await selectors.pending_lifecycle_claim(
        session, subscription.organization_id, subscription.id
    )
    # Money moves on an active subscription, and on a trial whose conversion is already claimed:
    # its first paid period is priced at the claimed pin. A trial with nothing claimed moves none.
    paid = subscription.state == STATE_ACTIVE or claim is not None

    # PAID-UPGRADE TIMING (fix round 1): an operator-forced next-period re-pin overrides the
    # ordinary direction-based rule entirely — a staff price change on a paying subscription
    # always waits for the next period, whichever way it moves the price, never prorating an
    # upgrade in immediately the way a customer's own change-plan does. `direction != 0` guards the
    # no-op case (an equal-price switch moves no money regardless).
    #
    # ADR-0005 addendum §4. A paid **downgrade waits for the next period** whoever asks — the
    # customer paid for this one — and is applied by the billing run when it claims a period not
    # yet claimed.
    schedule = paid and direction != 0 and (forced_timing == "next_period" or direction < 0)
    # A claimed period keeps the snapshot it was claimed with, so a change made while a claim is
    # pending lands at the end of *that* period (`apply_lapsed_change` uses the same boundary).
    boundary = (
        claim.period_end
        if claim is not None and claim.period_end is not None
        else subscription.current_period_end
    )
    # Mirrors the apply branch's `schedule_closing_usage`: usage since the period began is paid
    # time on an active subscription, and on a trial from its claimed conversion; it closes at the
    # current pin, so it is billed only when that pin has a usage price.
    usage_paid_time = subscription.state == STATE_ACTIVE or (
        claim is not None and claim.kind == domain.KIND_TRIAL_CONVERSION
    )
    return _PlanChange(
        action=_ACT_SCHEDULE if schedule else _ACT_APPLY,
        new_version=new_version,
        members=members,
        direction=direction,
        reason=reason,
        claim=claim,
        paid=paid,
        effective_at=boundary,
        usage_priced=_has_usage_price(new_version) or _has_usage_price(old_version),
        usage_closed_now=not schedule and usage_paid_time and _has_usage_price(old_version),
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
    forced_timing: str | None = None,
    note: str | None = None,
    expected: ExpectedState | None = None,
) -> Subscription:
    """Re-pin a subscription to a different published version — an upgrade or a downgrade.

    The previous pin is preserved on the transition row, never overwritten: the version a past
    invoice was computed against still exists and is still named, exactly as ADR-0004 §2 requires.
    An upgrade and a downgrade are the same state (`active`/`trial` unchanged), told apart by
    whether the new list price is higher — which decides only the event, not the money.
    """
    from app import selectors

    # Held for the whole change (review round 2, fix M4): the billing run's claim and record lock
    # the same row, so a change can never interleave with a claim it would then misprice.
    await selectors.lock_subscription(
        session, subscription.organization_id, subscription.id, skip_locked=False
    )
    if expected is not None:
        _assert_expected(subscription, expected)
    change = await _evaluate_change_plan(
        session,
        organization_id=organization_id,
        catalog=catalog,
        registry=registry,
        subscription=subscription,
        plan_version_id=plan_version_id,
        forced_timing=forced_timing,
        lock_claims=True,
    )
    new_version = change.new_version
    members = change.members

    if change.action == _ACT_WITHDRAW:
        _clear_pending_change(session, subscription, actor=actor, request=request, note=note)
        await session.flush()
        return subscription

    assert change.reason is not None  # only a withdraw has none
    if change.action == _ACT_SCHEDULE:
        _schedule_downgrade(
            session,
            subscription,
            new_version,
            members,
            actor=actor,
            request=request,
            reason=change.reason,
            note=note,
            effective_at=change.effective_at,
        )
        await session.flush()
        return subscription

    claim = change.claim
    decision = domain.decide(
        from_state=subscription.state, to_state=subscription.state, reason=change.reason
    )

    at = _wall()
    from_pin = subscription.plan_version_id
    from_items = _items_snapshot(subscription)
    # Usage so far was consumed on the old pin: close it there before the pin moves (round 2, M3).
    # A trial's usage is free — unless its conversion is already claimed, in which case the time
    # since the trial's end is paid time at the claimed pin (round 3).
    await schedule_closing_usage(
        session,
        subscription,
        at=at,
        paid_from=claim.period_start
        if claim is not None and claim.kind == domain.KIND_TRIAL_CONVERSION
        else None,
    )

    # A change that applies now supersedes any change that was waiting — and says so on the audit
    # trail, so a scheduled change never disappears without a row naming who replaced it.
    if subscription.pending_change is not None:
        _clear_pending_change(
            session,
            subscription,
            actor=actor,
            request=request,
            note=note,
            superseded_by=new_version.id,
        )
    subscription.plan_version_id = new_version.id
    subscription.billing_interval = new_version.billing_interval
    # Re-snapshotted with the pin, in the same transaction: the event below carries the new
    # member set, and product-service diffs it against what this source entitled before.
    subscription.entitled_products_snapshot = members
    subscription.pending_change = None
    subscription.pending_plan_version_id = None

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
        note=note,
    )
    # A paid upgrade is charged **now**, for the rest of the period it falls in: the prorated
    # difference between the new and the old recurring price. An equal-price switch moves no
    # money and raises no proration (round 2, m8). Claimed in the change's own transaction.
    if change.paid and change.direction > 0:
        await _claim_proration(
            session,
            subscription,
            from_plan_version_id=from_pin,
            from_items=from_items,
            claim=claim,
            at=at,
        )
    elif claim is not None:
        # An equal-price switch against a pending claim moves no money — but the pin did move,
        # and the record step's check prices from the last pin moved to against the claim. This
        # marker (settled, never billed) tells it so, so it cannot charge the earlier upgrade a
        # second time (review round 3, fix B2).
        await _claim_proration(
            session,
            subscription,
            from_plan_version_id=from_pin,
            from_items=from_items,
            claim=claim,
            at=at,
            settled_as="equal_price",
        )
    await session.flush()
    return subscription


def _schedule_downgrade(
    session: AsyncSession,
    subscription: Subscription,
    version: CatalogPlanVersion,
    members: list[str],
    *,
    actor: dict[str, Any],
    request: Request | None,
    reason: str = domain.REASON_DOWNGRADE,
    note: str | None = None,
    effective_at: datetime | None = None,
) -> None:
    """Pin a re-pin to the next period (addendum §4). Nothing about entitlement or money changes
    now — the customer keeps what they paid for until the period ends — so this is an audit row,
    not a transition or an event; the transition is written when the run applies it.

    `reason` is the machine reason the change will be typed from when the run applies it:
    `downgrade` or `upgrade` (fix round 1, PAID-UPGRADE TIMING — an operator-forced
    `timing="next_period"` on what would otherwise be an immediate, prorated upgrade also
    schedules through here). It is stored with the pending change, so the event the run emits at
    the roll is labelled by direction, and it is the audit row's action name too, so the trail does
    not call a deferred price *increase* a "downgrade". `note` is the operator's reason in words:
    it becomes the audit row's `reason`, and rides the pending change (subscription's own row) to
    the audit row written when it is applied — never onto an event.

    A second scheduled change replaces the first; the replaced one is named in `before`.
    """
    replaced = subscription.pending_plan_version_id
    subscription.pending_change = {
        "plan_version_id": str(version.id),
        "billing_interval": version.billing_interval,
        "items": [{"price_id": str(p.id), "quantity": 1} for p in version.prices],
        "entitled_products": members,
        "requested_at": datetime.now(UTC).isoformat(),
        "actor": actor,
        "reason": reason,
        **({"note": note} if note else {}),
    }
    subscription.pending_plan_version_id = version.id
    action = (
        "commerce.subscription.downgrade_scheduled"
        if reason == domain.REASON_DOWNGRADE
        else "commerce.subscription.change_scheduled"
    )
    effective = effective_at or subscription.current_period_end
    record_audit(
        session,
        AuditLog,
        organization_id=subscription.organization_id,
        actor_id=uuid.UUID(actor["id"]) if actor.get("type") == "user" else None,
        action=action,
        entity="subscription",
        entity_id=subscription.id,
        before={
            "plan_version_id": str(subscription.plan_version_id),
            **({"pending_plan_version_id": str(replaced)} if replaced else {}),
        },
        after={
            "pending_plan_version_id": str(version.id),
            "effective_at": effective.isoformat() if effective else None,
            "reason_code": reason,
        },
        reason=note or reason,
        acting_as=actor.get("id") if actor.get("type") == "operator" else None,
        request=request,
    )


def _clear_pending_change(
    session: AsyncSession,
    subscription: Subscription,
    *,
    actor: dict[str, Any],
    request: Request | None,
    note: str | None = None,
    superseded_by: uuid.UUID | None = None,
) -> None:
    """Drop the change that is waiting for the next period, and say so on the audit trail.

    Two ways it happens: withdrawn (a change back to the pin the subscription is on) or
    superseded (a change that applies now replaces it). Each is its own action, and each names the
    operator that did it — a scheduled change must never vanish without a row saying who ended it.
    """
    before = subscription.pending_plan_version_id
    subscription.pending_change = None
    subscription.pending_plan_version_id = None
    record_audit(
        session,
        AuditLog,
        organization_id=subscription.organization_id,
        actor_id=uuid.UUID(actor["id"]) if actor.get("type") == "user" else None,
        action=(
            "commerce.subscription.downgrade_withdrawn"
            if superseded_by is None
            else "commerce.subscription.change_superseded"
        ),
        entity="subscription",
        entity_id=subscription.id,
        before={"pending_plan_version_id": str(before) if before else None},
        after={
            "plan_version_id": str(subscription.plan_version_id),
            **({"superseded_by_plan_version_id": str(superseded_by)} if superseded_by else {}),
        },
        reason=note,
        acting_as=actor.get("id") if actor.get("type") == "operator" else None,
        request=request,
    )


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
    from app import selectors

    # A customer's words must not borrow a reason only the machinery writes: `decide` types the
    # event from the transition reason, so "renew" here used to emit a `renewed` event for a
    # cancellation. The words now ride as a note, and the transition keeps its own machine code.
    domain.assert_free_text_allowed(reason)
    # The row lock the billing run's claim and record take (review round 2, fix M4): a cancel
    # never computes its closing usage from a period a concurrent record is about to roll.
    await selectors.lock_subscription(
        session, subscription.organization_id, subscription.id, skip_locked=False
    )
    decision = domain.decide(
        from_state=subscription.state, to_state=STATE_CANCELLED, reason="customer_cancel"
    )
    # Never refused for a lagging run (final review): a customer can always leave. A period due
    # and unclaimed at the cancel is simply never opened — the run bills the closing usage only.
    subscription.cancel_at = datetime.now(UTC)
    await apply_lapsed_change(session, subscription, at=subscription.cancel_at)
    # Usage bills in arrears at the next roll, which a cancelled subscription never reaches: the
    # cancellation claims its own closing period (addendum, review fix 5).
    await schedule_closing_usage(session, subscription, at=subscription.cancel_at)
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
        note=reason.strip() if reason and reason.strip() else None,
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
    from app import selectors

    domain.assert_free_text_allowed(reason)
    await selectors.lock_subscription(
        session, subscription.organization_id, subscription.id, skip_locked=False
    )
    # The transition's `reason` is a short machine code (a 60-character column, and the event is
    # typed from it); the operator's words ride as the note — a 200-character reason used to be a
    # 500 here.
    decision = domain.decide(
        from_state=subscription.state, to_state=STATE_SUSPENDED, reason="operator_suspend"
    )
    # An operator suspension overrides the ladder rather than racing it: the window is over
    # because a human said so, and a sweep must not later "suspend" an already suspended
    # subscription on an edge the state machine forbids.
    await _close_open_cycle(session, subscription, reason="operator_suspend")
    subscription.suspended_at = datetime.now(UTC)
    await apply_lapsed_change(session, subscription, at=subscription.suspended_at)
    await schedule_closing_usage(session, subscription, at=subscription.suspended_at)
    _transition(
        session,
        subscription,
        decision,
        actor={"type": "operator", "id": operator_id},
        request=request,
        from_plan_version_id=subscription.plan_version_id,
        to_plan_version_id=subscription.plan_version_id,
        note=reason.strip() if reason and reason.strip() else None,
    )
    await session.flush()
    return subscription


async def reactivate(
    session: AsyncSession,
    subscription: Subscription,
    *,
    operator_id: str,
    reason: str,
    request: Request | None = None,
) -> Subscription:
    """Lift a suspension — `suspend`'s inverse, the write the operator console delegates over
    /internal.

    Only a `suspended` subscription may be reactivated. Any other state is a **409**, not the
    state machine's own 422: the request is well-formed and the lifecycle does allow *some*
    moves from there, it is just that this one is not applicable to what the subscription
    currently is — the caller's picture of it is stale, which is what a conflict means.

    Emits `commerce.subscription.activated`, which the product-service projection turns back
    into an entitling activation and console's fold into a live, MRR-paying subscription again.
    Money follows the platform's existing "back from suspension mid-period" policy exactly, by
    calling the same `charge_uncovered_remainder` the dunning-recovery resume calls: a period
    that rolled while suspended was not charged (the customer was not served), and the rest of
    the current one, from now, is — never a second, operator-only rule for the same situation.
    Anyone with the billing-ops role may lift a suspension whoever placed it; the operator's id is
    on the transition row and their words are the audit row's reason (never the event's).
    """
    from app import selectors

    domain.assert_free_text_allowed(reason)
    await selectors.lock_subscription(
        session, subscription.organization_id, subscription.id, skip_locked=False
    )
    if subscription.state != STATE_SUSPENDED:
        raise domain.CodedConflict(
            domain.CODE_STATE,
            f"cannot reactivate a subscription that is {subscription.state}: "
            "only a suspended one can be reactivated",
        )
    decision = domain.decide(
        from_state=subscription.state, to_state=STATE_ACTIVE, reason="operator_reactivate"
    )
    subscription.suspended_at = None
    _transition(
        session,
        subscription,
        decision,
        actor={"type": "operator", "id": operator_id},
        request=request,
        from_plan_version_id=subscription.plan_version_id,
        to_plan_version_id=subscription.plan_version_id,
        note=reason.strip() or None,
    )
    await charge_uncovered_remainder(session, subscription, at=datetime.now(UTC))
    await session.flush()
    return subscription


async def extend_trial_as_operator(
    session: AsyncSession,
    subscription: Subscription,
    payload: OperatorExtendTrialIn,
    *,
    request: Request | None = None,
) -> Subscription:
    """Move a trialling subscription's end later — console's owner-only "Extend trial" (ADR-0030).

    Under the row lock the billing run's claim takes, in this order:

    1. **The operator's picture still holds** — `expected_trial_ends_at` is the end now
       (409 `state_changed` otherwise). This is what makes a retry safe: the target is absolute,
       so a second attempt after the first landed finds the end already moved and adds nothing.
    2. **It is a trial** (422 `not_trial`).
    3. **The run has not claimed its conversion** (422 `claim_in_flight`): `record_billing_period`
       voids a claimed conversion whose trial end moved (`still_due_for`), so extending under a
       pending claim would turn an invoice already being priced into a void.
    4. **The new end is later, in the future, and within `MAX_TOTAL_TRIAL_DAYS` of the trial's
       start** (422 `trial_not_extended` / `trial_cap`; `domain.trial_extension_refusal`).

    Then one `trial -> trial` transition typed `trial_extended` — never `trial_started`, which
    console counts and notification rules fire on — carrying the lifecycle payload (whose
    `trial_ends_at` is the new end, which product's projection folds) plus
    `previous_trial_ends_at`. Nothing about money moves: the trial was free and stays free, and the
    run converts it at the new end exactly as it would have at the old one.
    """
    from app import selectors

    domain.assert_free_text_allowed(payload.note)
    await selectors.lock_subscription(
        session, subscription.organization_id, subscription.id, skip_locked=False
    )
    if subscription.trial_ends_at != payload.expected_trial_ends_at:
        raise domain.CodedConflict(
            domain.CODE_STATE_CHANGED,
            "this subscription's trial end changed since you looked at it; refresh and review it "
            "again",
        )
    if subscription.state != STATE_TRIAL:
        raise domain.CodedRefusal(
            domain.CODE_NOT_TRIAL,
            f"a {subscription.state} subscription is not trialling; only a trial can be extended",
        )
    claim = await selectors.pending_lifecycle_claim(
        session, subscription.organization_id, subscription.id
    )
    if claim is not None:
        raise domain.CodedRefusal(
            domain.CODE_CLAIM_IN_FLIGHT,
            "billing has already claimed this trial's conversion and is pricing it; the trial can "
            "no longer be extended",
        )
    new_end = payload.new_trial_ends_at.astimezone(UTC)
    refusal = domain.trial_extension_refusal(
        trial_started_at=subscription.current_period_start,
        current_trial_ends_at=subscription.trial_ends_at,
        new_trial_ends_at=new_end,
        now=_wall(),
    )
    if refusal is not None:
        raise domain.CodedRefusal(*refusal)

    previous = subscription.trial_ends_at
    subscription.trial_ends_at = new_end
    _transition(
        session,
        subscription,
        domain.decide(
            from_state=STATE_TRIAL, to_state=STATE_TRIAL, reason=domain.REASON_TRIAL_EXTENDED
        ),
        actor=_operator_actor(payload.operator_id),
        request=request,
        from_plan_version_id=subscription.plan_version_id,
        to_plan_version_id=subscription.plan_version_id,
        extra={"previous_trial_ends_at": previous.isoformat() if previous else None},
        note=payload.note,
        audit_before={"trial_ends_at": previous.isoformat() if previous else None},
        audit_after={"trial_ends_at": new_end.isoformat()},
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


async def _lock_for_payment(
    session: AsyncSession, subscription: Subscription
) -> Subscription | None:
    """Take the subscription's row lock and re-read it — the payment handlers' first statement.

    Every writer of a subscription (cancel, suspend, change-plan, the billing run's claim and
    record, the dunning sweep) locks the row before it reads anything, so they serialise and each
    decides from committed state. The payment consumer arrives with a row it read unlocked;
    `populate_existing` on the lock refreshes that instance, so the state and the open cycle read
    after this are the ones the last committed writer left. None if the row is gone.
    """
    from app import selectors

    return await selectors.lock_subscription(
        session, subscription.organization_id, subscription.id, skip_locked=False
    )


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

    # First, before any state is read: the row lock every other writer takes first (final money
    # review). The consumer read the row unlocked; a cancel committing meanwhile must be seen
    # here, or a cancelled customer is put into dunning.
    locked = await _lock_for_payment(session, subscription)
    if locked is None:
        return subscription
    subscription = locked

    at = now or datetime.now(UTC)
    if subscription.state in (STATE_TRIAL, STATE_SUSPENDED, STATE_CANCELLED):
        # Nothing to dun: a trial owes no period yet, a suspended or cancelled subscription has
        # nothing left to suspend. But a charge that failed here — an upgrade's proration against
        # a claimed conversion while still in trial, a closing period's usage or a proration after
        # the customer left — is money owed and not collected: stamped on its claim for the health
        # probe and a person (review round 2 m9, round 3 m7/m8; ADR-0005 Deferred).
        if outcome.invoice_id is not None:
            owed = await selectors.chargeable_period_for_invoice(
                session, subscription.organization_id, subscription.id, outcome.invoice_id
            )
            if owed is not None and owed.payment_failed_at is None:
                owed.payment_failed_at = at
                owed.payment_failure_reason = (outcome.failure_reason or "payment_failed")[:200]
                await session.flush()
        return subscription

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
    outcome: PaymentOutcome | None = None,
    *,
    now: datetime | None = None,
    request: Request | None = None,
) -> Subscription:
    """Money arrived: close the cycle and put the subscription back to `active`.

    Recovery works from **either** rung — `past_due` during grace, and `suspended` after it, which
    is why `suspended -> active` is a legal edge and `cancelled -> active` is not. A customer who
    pays in week six comes back to the same subscription, the same pin and the same history rather
    than a new row that has forgotten what they used to have.

    From `suspended`, only a capture of the invoice whose grace window expired resumes — the
    suspension in force is dunning's (the last move into `suspended` was written by the sweep's
    service actor — never judged by the free-text reason) and the latest cycle closed
    `grace_expired` for exactly that invoice. Any other capture (the run's
    closing-usage invoice, an older invoice) is recorded by payment and moves nothing here, and an
    operator's suspension is never lifted by a payment at all.

    A capture for a subscription that is already `active` is the ordinary case — a renewal that
    worked — and is deliberately not a transition: nothing changed, so nothing is emitted, and the
    projection is not woken to write the row it already holds.
    """
    from app import selectors

    # First, before any state is read (final money review): an operator's suspension or a cancel
    # committing while this capture was in flight must be what it decides from — otherwise a
    # fraud hold is lifted, or a cancelled subscription revived, by a payment that raced it.
    locked = await _lock_for_payment(session, subscription)
    if locked is None:
        return subscription
    subscription = locked

    at = now or datetime.now(UTC)
    open_cycle = await selectors.get_open_cycle(session, subscription.id)
    captured_invoice = outcome.invoice_id if outcome is not None else None
    if open_cycle is not None and open_cycle.invoice_id != captured_invoice:
        # Money for a *different* invoice — a renewal paid while an older one is still in
        # dunning — settles nothing the window is about (review fix 3). The window stays open
        # and the ladder carries on; recovering here would hand back a product whose unpaid
        # invoice is still unpaid.
        log.info(
            "dunning.capture.other_invoice",
            subscription_id=str(subscription.id),
            cycle_invoice_id=str(open_cycle.invoice_id) if open_cycle.invoice_id else None,
            captured_invoice_id=str(captured_invoice) if captured_invoice else None,
        )
        return subscription
    cycle = await _close_open_cycle(session, subscription, reason="payment_captured", now=at)

    if subscription.state not in (STATE_PAST_DUE, STATE_SUSPENDED):
        if cycle is not None:
            await session.flush()
        return subscription

    resuming = subscription.state == STATE_SUSPENDED
    if resuming and cycle is None:
        # Both suspension paths close the cycle first, so a capture on a suspended subscription
        # never finds one open. Money is not a key to every suspension (final review, blocker):
        # the run's closing-usage invoice is captured routinely after a suspension, and neither
        # it nor any other invoice may lift an operator's suspension — a fraud or abuse hold is
        # lifted by a person, never by a payment — nor a dunning suspension, whose customer owes
        # the invoice that window was about. Only paying **that** invoice resumes.
        exhausted = await selectors.latest_cycle(
            session, subscription.organization_id, subscription.id
        )
        # The suspension in force must be dunning's own: a customer resumed from an earlier
        # dunning suspension and since held by an operator has an exhausted cycle too. Decided by
        # who wrote the suspension (the sweep's service actor), never by its free-text reason.
        actor = await selectors.latest_suspension_actor(
            session, subscription.organization_id, subscription.id
        )
        suspended_by = (actor or {}).get("id")
        by_dunning = (actor or {}).get("type") == _SWEEP_ACTOR["type"] and (
            suspended_by == _SWEEP_ACTOR["id"]
        )
        if (
            not by_dunning
            or exhausted is None
            or exhausted.closed_reason != "grace_expired"
            or exhausted.invoice_id is None
            or exhausted.invoice_id != captured_invoice
        ):
            log.info(
                "dunning.capture.no_resume",
                subscription_id=str(subscription.id),
                captured_invoice_id=str(captured_invoice) if captured_invoice else None,
                cycle_invoice_id=(
                    str(exhausted.invoice_id)
                    if exhausted is not None and exhausted.invoice_id
                    else None
                ),
                cycle_closed_reason=exhausted.closed_reason if exhausted is not None else None,
                suspended_by=suspended_by,
            )
            return subscription
        cycle = exhausted
    subscription.suspended_at = None
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
    if resuming:
        # Back from suspension mid-period. A period rolled while suspended was not charged (the
        # customer was not served); the rest of it, from now, is (review round 3).
        await charge_uncovered_remainder(session, subscription, at=at)
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
    from app import selectors

    await selectors.lock_subscription(
        session, subscription.organization_id, subscription.id, skip_locked=False
    )
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

    # The window closed at its stored instant: that is where served usage stops.
    await apply_lapsed_change(session, subscription, at=cycle.grace_ends_at)
    await schedule_closing_usage(session, subscription, at=cycle.grace_ends_at)
    subscription.suspended_at = cycle.grace_ends_at
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


async def require_subscription(
    session: AsyncSession,
    subscription_id: uuid.UUID,
    *,
    organization_id: uuid.UUID | None = None,
) -> Subscription:
    """The subscription, or a 404. `organization_id` (the `/internal` routes, which are told the
    tenant by the payload) filters the query on it too, so a wrong pairing is a 404 even if the RLS
    net were ever missing."""
    from app import selectors

    subscription = await selectors.get_subscription(
        session, subscription_id, organization_id=organization_id
    )
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


def _wall() -> datetime:
    """The wall clock, for leases, backoff and `recorded_at` — how long a process may be mid-call
    and when it may retry are facts about now, never about the synthetic instant a test (or a
    late sweep) decides due periods against."""
    return datetime.now(UTC)


def _billing_facts(period: BillingPeriod) -> dict[str, Any]:
    """The invoice a transition was raised with, on the event it rides — so a notification can
    say "your first invoice, ₹X for 1 Oct to 31 Oct" from the event alone."""
    return {
        "billing": {
            "period_id": str(period.id),
            "kind": period.kind,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat() if period.period_end else None,
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


def _billing_audit(
    session: AsyncSession,
    subscription_or_period: Subscription | BillingPeriod,
    action: str,
    after: dict[str, Any],
    *,
    reason: str | None = None,
) -> None:
    entity_id = (
        subscription_or_period.id
        if isinstance(subscription_or_period, Subscription)
        else subscription_or_period.subscription_id
    )
    record_audit(
        session,
        AuditLog,
        organization_id=subscription_or_period.organization_id,
        actor_id=None,
        acting_as=_BILLING_ACTOR["id"],
        action=action,
        entity="subscription",
        entity_id=entity_id,
        after=after,
        reason=reason,
    )


async def _insert_period(session: AsyncSession, values: dict[str, Any]) -> None:
    """Insert a claim row, or do nothing if the period of that kind is already claimed."""
    await session.execute(
        pg_insert(BillingPeriod)
        .values(id=uuid.uuid4(), state=PERIOD_PENDING, attempts=0, **values)
        .on_conflict_do_nothing()
    )


class BillingCatchingUp(domain.CodedConflict):
    """A plan change while a period or trial end is due and not yet claimed.

    Deciding money against a period the run has not claimed yet — pricing a proration against a
    period that has not been opened — is how the earlier rounds' defects were made. So a change
    waits for the run (review round 3, fix M3). A cancellation never does (final review): leaving
    must always work, and a cancel against an unopened period bills only its closing usage.

    Code `billing_catching_up`. Transient only while the billing run is running: with
    `BILLING_RUN_ENABLED=false` nothing ever claims the due period, so this is indefinite there and
    an operator surface must say so rather than "try again shortly".
    """

    title = "Billing is catching up"

    def __init__(self, detail: str) -> None:
        super().__init__(domain.CODE_BILLING_CATCHING_UP, detail)


async def assert_billing_caught_up(
    session: AsyncSession, subscription: Subscription, *, lock: bool = True
) -> None:
    """409 if a period or a trial end is due for the subscription and the run has not claimed it.

    Due is decided on the stored boundaries against the wall clock; claimed means a claim row of
    that kind exists for that period. While the billing run is held (`BILLING_RUN_ENABLED=false`)
    this refuses plan changes on every overdue subscription — the runbook says so. A claim that
    fails permanently stays pending; once its own period has ended too, the subscription is more
    than one period behind and its plan changes are refused until a person fixes the claim.

    `lock=False` is the read-only preview's: it asks whether the claim row exists without taking
    `FOR UPDATE` on it.
    """
    from app import selectors

    due = domain.due_period(
        state=subscription.state,
        trial_ends_at=subscription.trial_ends_at,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        billing_interval=subscription.billing_interval,
        now=_wall(),
        usage_billed_through=subscription.usage_billed_through,
    )
    # The run is behind by more than one period: a claim is pending for a period that has already
    # ended, so the period after it is due and cannot even be claimed yet.
    pending = await selectors.pending_lifecycle_claim(
        session, subscription.organization_id, subscription.id
    )
    if pending is not None and pending.period_end is not None and pending.period_end <= _wall():
        raise BillingCatchingUp("billing is catching up on this subscription — try again shortly")
    if due is None or not due.billable:
        return
    find = selectors.lock_billing_period if lock else selectors.find_billing_period
    claimed = await find(
        session, subscription.organization_id, subscription.id, due.kind, due.start
    )
    if claimed is None:
        raise BillingCatchingUp("billing is catching up on this subscription — try again shortly")


async def claim_opening(
    session: AsyncSession,
    subscription: Subscription,
    *,
    at: datetime,
    full_period_start: datetime,
    end: datetime,
) -> None:
    """Claim the recurring charge for `[at, end)` at the current pin — time no period charge
    covers (review round 3). Billed as a proration from nothing: the share of the period's
    recurring price for the time remaining, keyed on its own change id."""
    if at >= end:
        return
    await _insert_period(
        session,
        {
            "organization_id": subscription.organization_id,
            "subscription_id": subscription.id,
            "kind": domain.KIND_OPENING,
            "period_start": at,
            "period_end": end,
            "full_period_start": full_period_start,
            "plan_version_id": subscription.plan_version_id,
            "currency": subscription.currency,
            "items": _items_snapshot(subscription),
            "change_id": uuid.uuid4(),
            "from_plan_version_id": subscription.plan_version_id,
            "from_items": [],
        },
    )


def served_into(subscription: Subscription, period_start: datetime) -> bool:
    """Whether a suspended subscription was still served when a period opened: its suspension
    came after the period's start. Such a period is charged in advance like any other — the run
    being late to roll it must not make it free (review round 3). With no recorded instant (a row
    from before the column) the suspension is taken to predate the period, as before."""
    return subscription.suspended_at is not None and subscription.suspended_at > period_start


async def charge_uncovered_remainder(
    session: AsyncSession, subscription: Subscription, *, at: datetime
) -> None:
    """On resumption: charge the rest of the current period if no live charge covers `at`.

    Not inside the period that straddles the billing cutover (second post-review fixup): that
    period is free, so a customer resumed inside it is not charged its remainder either — known
    from the configured cutover, or from a `pre_cutover` row the run already wrote for it.
    """
    from app import selectors
    from app.config import settings

    start, end = subscription.current_period_start, subscription.current_period_end
    if start is None or end is None or at >= end:
        return
    cutover = settings().billing_run_not_before
    if cutover is not None and start < cutover:
        return
    if await selectors.pre_cutover_covering(
        session, subscription.organization_id, subscription.id, at
    ):
        return
    if await selectors.base_charge_covering(
        session, subscription.organization_id, subscription.id, at
    ):
        return
    await claim_opening(session, subscription, at=at, full_period_start=start, end=end)


#: The namespace a pending claim's change ids are derived in (review round 2, fix B2).
_CHANGE_NAMESPACE = uuid.UUID("5f0c9e7a-3b1d-4c2e-9a8f-6d7e1b2c3a4f")


def _change_id(claim: BillingPeriod | None, to_pin: uuid.UUID) -> uuid.UUID:
    """A fresh id — or, against a pending lifecycle claim, one derived from the claim and the pin
    moved to, so the change-plan path and the record step's check name the same change."""
    if claim is None:
        return uuid.uuid4()
    return uuid.uuid5(_CHANGE_NAMESPACE, f"{claim.id}:{to_pin}")


async def _claim_proration(
    session: AsyncSession,
    subscription: Subscription,
    *,
    from_plan_version_id: uuid.UUID,
    from_items: list[dict[str, Any]],
    claim: BillingPeriod | None,
    at: datetime,
    to_items: list[dict[str, Any]] | None = None,
    settled_as: str | None = None,
) -> None:
    """Claim the prorated difference an upgrade owes for the rest of the period it falls in.

    With no lifecycle claim pending that is the current period. With one pending — a renewal or a
    trial conversion claimed and not yet recorded — it is **the claim's** period (review round 2,
    fix B2): the change falls inside it, and it was priced at the claim's pin. Billing prices both
    pins from catalog and bills the positive difference, keyed on the change
    (`subscription:{id}:proration:{change_id}`); the row is inserted against every unique index, so
    one change is one row however many paths claim it.
    """
    start: datetime | None = subscription.current_period_start
    end: datetime | None = subscription.current_period_end
    if claim is not None and claim.period_end is not None:
        start, end = claim.period_start, claim.period_end
    if start is None or end is None or at >= end:
        return
    values: dict[str, Any] = {"state": PERIOD_PENDING}
    await session.execute(
        pg_insert(BillingPeriod)
        .values(
            id=uuid.uuid4(),
            attempts=0,
            organization_id=subscription.organization_id,
            subscription_id=subscription.id,
            kind=domain.KIND_PRORATION,
            period_start=max(at, start),
            period_end=end,
            full_period_start=start,
            plan_version_id=subscription.plan_version_id,
            currency=subscription.currency,
            items=to_items if to_items is not None else _items_snapshot_of_new(subscription),
            change_id=_change_id(claim, subscription.plan_version_id),
            against_period_id=claim.id if claim is not None else None,
            from_plan_version_id=from_plan_version_id,
            from_items=from_items,
            **(
                {"state": PERIOD_SKIPPED, "outcome": settled_as, "recorded_at": at}
                if settled_as is not None
                else values
            ),
        )
        .on_conflict_do_nothing()
    )


def _items_snapshot_of_new(subscription: Subscription) -> list[dict[str, Any]]:
    # The items were just rebuilt in memory and are not flushed yet; they carry no id, only the
    # price and quantity the snapshot needs.
    return sorted(
        ({"price_id": str(i.price_id), "quantity": int(i.quantity)} for i in subscription.items),
        key=lambda d: d["price_id"],
    )


async def schedule_closing_usage(
    session: AsyncSession,
    subscription: Subscription,
    *,
    at: datetime,
    paid_from: datetime | None = None,
) -> None:
    """Claim the usage-only closing period a cancellation or suspension at `at` leaves behind.

    Usage bills in arrears at the next roll; a cancelled subscription never rolls again, and a
    suspended one rolls without an invoice. So the stop claims `[period start, at)` itself — keyed
    `subscription:{id}:{period_start}:close` at billing — and the run bills it like any other
    claim. `usage_billed_through` moves to `at`, so a later renewal (after a suspension recovers)
    starts its usage window there and never bills the same reading twice. Call it before the
    state moves: only a served state has usage to close (`domain.closing_usage_window`).
    """
    from app import selectors

    window = domain.closing_usage_window(
        state=STATE_ACTIVE if paid_from is not None else subscription.state,
        current_period_start=paid_from or subscription.current_period_start,
        usage_billed_through=subscription.usage_billed_through,
        at=at,
        claimed_usage_end=await selectors.max_claimed_usage_end(
            session, subscription.organization_id, subscription.id
        ),
    )
    if window is None:
        return
    start, end = window
    await _insert_period(
        session,
        {
            "organization_id": subscription.organization_id,
            "subscription_id": subscription.id,
            "kind": domain.KIND_CLOSE,
            "period_start": start,
            "period_end": end,
            "usage_start": start,
            "usage_end": end,
            "plan_version_id": subscription.plan_version_id,
            "currency": subscription.currency,
            "items": _items_snapshot(subscription),
        },
    )
    subscription.usage_billed_through = end


async def apply_pending_change(
    session: AsyncSession, subscription: Subscription, *, boundary: datetime
) -> bool:
    """Apply a downgrade that was waiting for this roll (addendum §4). True if one was applied.

    Called by the run's claim, in the claim's transaction, before the period is snapshotted — so
    the new period is priced at the lower plan and rolled on its cadence. It is the moment the
    change *happens*, so it is the moment the transition and its event are written; a suspended
    subscription takes the pin without an event (no edge, nothing served).

    **The event is labelled by the change's direction**, which the schedule stored with it: a
    deferred price increase (a staff change on a paying subscription) emits `upgraded` here, not
    `downgraded`. Every consumer folds the two identically (they read the payload's state, pin and
    members) so the label is for the humans and the reports that read it. A pending change
    scheduled before the direction was stored has none and keeps the old label, `downgraded`,
    exactly as it always did — nothing already waiting changes meaning. The operator's note, when
    the change had one, is the applied transition's audit reason; it is not put on the event.
    """
    change = subscription.pending_change
    if not change:
        return False
    reason = change.get("reason")
    if reason not in (domain.REASON_UPGRADE, domain.REASON_DOWNGRADE):
        reason = domain.REASON_DOWNGRADE
    note = change.get("note")
    # The period that is ending was consumed on the old pin: its usage is closed there, at the
    # old rate, before the pin moves — and the renewal about to be claimed then carries no usage
    # window, so the new (lower) rate never prices usage from before the change (round 2, M3).
    await schedule_closing_usage(session, subscription, at=boundary)
    from_pin = subscription.plan_version_id
    to_pin = uuid.UUID(str(change["plan_version_id"]))
    subscription.plan_version_id = to_pin
    subscription.billing_interval = change.get("billing_interval") or subscription.billing_interval
    subscription.entitled_products_snapshot = list(change.get("entitled_products") or []) or None
    subscription.items.clear()
    for item in change.get("items") or []:
        subscription.items.append(
            SubscriptionItem(
                organization_id=subscription.organization_id,
                price_id=uuid.UUID(str(item["price_id"])),
                quantity=int(item.get("quantity", 1)),
            )
        )
    subscription.pending_change = None
    subscription.pending_plan_version_id = None
    if subscription.state == STATE_SUSPENDED:
        _billing_audit(
            session,
            subscription,
            "commerce.subscription.downgrade_applied"
            if reason == domain.REASON_DOWNGRADE
            else "commerce.subscription.change_applied",
            {"from": str(from_pin), "to": str(to_pin)},
            reason=note or reason,
        )
    else:
        _transition(
            session,
            subscription,
            domain.decide(
                from_state=subscription.state,
                to_state=subscription.state,
                reason=reason,
            ),
            actor=_BILLING_ACTOR,
            request=None,
            from_plan_version_id=from_pin,
            to_plan_version_id=to_pin,
            note=note,
        )
    await session.flush()
    return True


async def apply_lapsed_change(
    session: AsyncSession, subscription: Subscription, *, at: datetime
) -> None:
    """A stop at `at` while the run lags: apply a downgrade whose boundary has already passed.

    A deferred downgrade takes effect at the first period boundary nobody has claimed — the run
    applies it when it claims that roll. If the run is behind and the subscription stops (a
    cancellation, an operator's or dunning's suspension) after that boundary, the stop's closing
    usage would otherwise run across it at the old, dearer pin (found by the property test,
    final review). So the stop applies it first, at the boundary: usage up to it closes at the
    old pin, and the stop closes the rest at the new one. The rolls the run makes later find no
    change waiting and price at the new pin, as they would have.
    """
    if (
        not subscription.pending_change
        or subscription.current_period_end is None
        or subscription.state not in (STATE_ACTIVE, STATE_PAST_DUE, STATE_SUSPENDED)
    ):
        return
    from app import selectors

    claim = await selectors.pending_lifecycle_claim(
        session, subscription.organization_id, subscription.id
    )
    boundary = (
        claim.period_end
        if claim is not None and claim.period_end is not None
        else subscription.current_period_end
    )
    if boundary > at:
        return
    await apply_pending_change(session, subscription, boundary=boundary)


async def claim_billing_period(
    session: AsyncSession,
    subscription: Subscription,
    due: domain.DuePeriod,
) -> BillingPeriod | None:
    """The **claim** of claim → call → record for a lifecycle period: write or re-find the row
    and lease it.

    The caller holds the subscription `FOR UPDATE SKIP LOCKED`. The row is inserted
    `ON CONFLICT DO NOTHING`, so a crash-and-retry finds the row it wrote last time — with the pin
    and items it snapshotted then. Returns None when another replica's lease is live, or the
    backoff after a failure has not elapsed (both on the wall clock), or the row is recorded.
    """
    from app import selectors

    await _insert_period(
        session,
        {
            "organization_id": subscription.organization_id,
            "subscription_id": subscription.id,
            "kind": due.kind,
            "period_start": due.start,
            "period_end": due.end,
            "usage_start": due.usage_start,
            "usage_end": due.usage_end,
            "plan_version_id": subscription.plan_version_id,
            "currency": subscription.currency,
            "items": _items_snapshot(subscription),
        },
    )
    period = await selectors.lock_billing_period(
        session, subscription.organization_id, subscription.id, due.kind, due.start
    )
    return _lease(period)


def _lease(period: BillingPeriod | None) -> BillingPeriod | None:
    if period is None or period.state not in (
        PERIOD_PENDING,
        PERIOD_VOID_PENDING,
        PERIOD_FINALIZE_PENDING,
    ):
        return None
    wall = _wall()
    if period.lease_until is not None and period.lease_until > wall:
        return None
    if period.next_attempt_at is not None and period.next_attempt_at > wall:
        return None
    period.lease_until = wall + domain.BILLING_LEASE
    period.attempts += 1
    return period


async def claim_actionable(session: AsyncSession, period: BillingPeriod) -> BillingPeriod | None:
    """Lease a row that is its own work — a proration, a close, a void to make."""
    leased = _lease(period)
    if leased is not None:
        await session.flush()
    return leased


async def record_not_billable(
    session: AsyncSession, subscription: Subscription, due: domain.DuePeriod
) -> BillingPeriod | None:
    """A due period with no period to bill (`one_time`): persisted as a permanent failure.

    A row, not a log line, so the subscription backs off like any other failure — discovery skips
    it until `next_attempt_at` — instead of being rediscovered, and skipped, at the head of every
    batch; and so the health probe's `failing_permanent` names it for a person.
    """
    from app import selectors

    await _insert_period(
        session,
        {
            "organization_id": subscription.organization_id,
            "subscription_id": subscription.id,
            "kind": due.kind,
            "period_start": due.start,
            "period_end": None,
            "plan_version_id": subscription.plan_version_id,
            "currency": subscription.currency,
            "items": _items_snapshot(subscription),
        },
    )
    period = await selectors.lock_billing_period(
        session, subscription.organization_id, subscription.id, due.kind, due.start
    )
    if period is None or period.state != PERIOD_PENDING:
        return None
    period.attempts += 1
    await fail_billing_period(session, period, error=due.error or "not billable", permanent=True)
    return period


async def skip_suspended_period(
    session: AsyncSession,
    subscription: Subscription,
    due: domain.DuePeriod,
) -> BillingPeriod:
    """Roll a suspended subscription's period without invoicing it.

    A suspended customer is not served and is not billed for the time (ADR-0005 §4). The period
    still advances, so a customer who pays in week six comes back on a current period rather than
    being invoiced, one pass at a time, for every month they were switched off. Its usage up to
    the suspension was already claimed as a closing period when it was suspended.
    """
    assert due.end is not None
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
        recorded_at=_wall(),
    )
    session.add(period)
    subscription.current_period_start = due.start
    subscription.current_period_end = due.end
    _billing_audit(
        session,
        subscription,
        "commerce.billing_run.period_skipped",
        {
            "period_start": due.start.isoformat(),
            "period_end": due.end.isoformat(),
            "state": subscription.state,
        },
        reason="suspended",
    )
    await session.flush()
    return period


async def skip_pre_cutover_period(
    session: AsyncSession,
    subscription: Subscription,
    due: domain.DuePeriod,
) -> BillingPeriod:
    """A lifecycle period that began before the billing cutover: rolled — or, for a trial that
    ended before it, converted — with no invoice (final review, fix 3).

    The row is written `skipped` with outcome `pre_cutover`, so the period is claimed (the change
    guard and discovery both see it done) and says why it was not billed. A trial converts to
    `active` exactly as a zero-price conversion would: no mandate question, no dunning, the first
    paid period opening at the trial's end — the customer starts paying at the first period that
    begins after the cutover.
    """
    assert due.end is not None
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
        outcome=domain.OUTCOME_PRE_CUTOVER,
        recorded_at=_wall(),
    )
    session.add(period)
    subscription.current_period_start = due.start
    subscription.current_period_end = due.end
    facts = {
        "billing": {
            "period_start": due.start.isoformat(),
            "period_end": due.end.isoformat(),
            "invoiced": False,
            "outcome": domain.OUTCOME_PRE_CUTOVER,
        }
    }
    if due.kind == domain.KIND_TRIAL_CONVERSION and subscription.state == STATE_TRIAL:
        _transition(
            session,
            subscription,
            domain.decide(
                from_state=STATE_TRIAL, to_state=STATE_ACTIVE, reason=domain.REASON_TRIAL_CONVERT
            ),
            actor=_BILLING_ACTOR,
            request=None,
            from_plan_version_id=subscription.plan_version_id,
            to_plan_version_id=subscription.plan_version_id,
            extra=facts,
        )
    else:
        _billing_audit(
            session,
            subscription,
            "commerce.billing_run.period_skipped",
            {**facts["billing"], "state": subscription.state},
            reason=domain.OUTCOME_PRE_CUTOVER,
        )
    await session.flush()
    return period


async def settle_pre_cutover_row(session: AsyncSession, period: BillingPeriod) -> None:
    """A proration, opening or closing row the cutover says is not billed: settled `skipped`
    (`pre_cutover`) without a call to billing (final review, fix 3)."""
    period.state = PERIOD_SKIPPED
    period.outcome = domain.OUTCOME_PRE_CUTOVER
    period.recorded_at = _wall()
    period.lease_until = None
    period.next_attempt_at = None
    period.last_error = None
    period.permanent_failure = False
    _billing_audit(
        session,
        period,
        "commerce.billing_run.pre_cutover",
        {"kind": period.kind, "period_start": period.period_start.isoformat()},
        reason=domain.OUTCOME_PRE_CUTOVER,
    )
    await session.flush()


def still_due_for(subscription: Subscription, period: BillingPeriod) -> bool:
    """Is the subscription still exactly where it was when this lifecycle period was claimed?"""
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
) -> str:
    """The **record** of claim → call → record for a lifecycle period: write billing's answer and
    move the subscription. One transaction, the subscription held `FOR UPDATE`.

    * **Already recorded** (another replica got here first): nothing, `already_recorded`.
    * **The subscription moved on** since the claim — cancelled, or advanced by hand: the invoice
      exists in billing and must not stand, so the row goes to `void_pending` and the run voids it
      through billing next (review fix 11): `superseded`.
    * **A trial conversion** opens the first paid period at the trial's end. With nothing to
      collect, or a mandate that can collect it **and no failure of its first charge already
      recorded** (review fix 1), `trial -> active`. Otherwise `trial -> past_due` and a dunning
      cycle anchored at the trial's end carrying this invoice.
    * **A renewal** rolls the period and emits `renewed` (`active` or `past_due` unchanged). One
      that became suspended between claim and record rolls with no event.
    """
    if period.state != PERIOD_PENDING:
        return "already_recorded"

    period.invoice_id = invoice_id
    period.total_minor = total_minor
    period.recorded_at = _wall()
    period.lease_until = None
    period.next_attempt_at = None
    period.last_error = None
    period.permanent_failure = False
    # Each step — call, finalize, void — counts its own attempts: backoff and the "fifth failure"
    # alarm measure the step that is failing, not the history of the claim.
    period.attempts = 0

    if (
        period.kind == domain.KIND_RENEWAL
        and subscription.state == STATE_SUSPENDED
        and still_due_for(subscription, period)
        and not served_into(subscription, period.period_start)
    ):
        # Suspended between claim and record (review round 2, fix M4): the renewal's advance
        # recurring charge is for a period the customer is not served, so its draft is voided —
        # the period still rolls, exactly as a suspended roll does — and the closed period's
        # usage it carried is re-claimed on its own, so voiding it does not drop that usage.
        assert period.period_end is not None
        subscription.current_period_start = period.period_start
        subscription.current_period_end = period.period_end
        period.state = PERIOD_VOID_PENDING
        period.outcome = "superseded_suspended"
        await _reclaim_usage(session, period)
        await cascade_to_prorations(session, period)
        _billing_audit(
            session,
            subscription,
            "commerce.billing_run.period_superseded",
            {"invoice_id": str(invoice_id), "state": subscription.state},
            reason="suspended",
        )
        await session.flush()
        return "superseded"

    if not still_due_for(subscription, period):
        period.state = PERIOD_VOID_PENDING
        period.outcome = "superseded"
        await _reclaim_usage(session, period)
        await cascade_to_prorations(session, period)
        log.warning(
            "billing_run.period.superseded",
            organization_id=str(subscription.organization_id),
            subscription_id=str(subscription.id),
            invoice_id=str(invoice_id),
            state=subscription.state,
            hint="the subscription moved between claim and record; the run voids the invoice",
        )
        _billing_audit(
            session,
            subscription,
            "commerce.billing_run.period_superseded",
            {"invoice_id": str(invoice_id), "state": subscription.state},
            reason="superseded",
        )
        await session.flush()
        return "superseded"

    # Recorded; the invoice is still a draft in billing. The run finalizes it next (round 2, M5).
    period.state = PERIOD_FINALIZE_PENDING
    assert period.period_end is not None
    subscription.current_period_start = period.period_start
    subscription.current_period_end = period.period_end
    await _prorate_missed_change(session, subscription, period)

    if period.kind == domain.KIND_TRIAL_CONVERSION:
        charge_failed = period.payment_failed_at is not None
        if total_minor == 0 or (collectable and not charge_failed):
            period.outcome = "activated"
            _transition(
                session,
                subscription,
                domain.decide(
                    from_state=STATE_TRIAL,
                    to_state=STATE_ACTIVE,
                    reason=domain.REASON_TRIAL_CONVERT,
                ),
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
            period.outcome = "past_due"
            extra = {**_dunning_facts(cycle), **_billing_facts(period)}
            if charge_failed:
                extra["billing"]["charge_failed_reason"] = period.payment_failure_reason
            _transition(
                session,
                subscription,
                domain.decide(
                    from_state=STATE_TRIAL,
                    to_state=STATE_PAST_DUE,
                    reason=domain.REASON_TRIAL_UNPAID,
                ),
                actor=_BILLING_ACTOR,
                request=None,
                from_plan_version_id=subscription.plan_version_id,
                to_plan_version_id=subscription.plan_version_id,
                extra=extra,
            )
    elif subscription.state == STATE_SUSPENDED:
        period.outcome = "rolled_while_suspended"
    else:
        period.outcome = "renewed"
        _transition(
            session,
            subscription,
            domain.decide(
                from_state=subscription.state,
                to_state=subscription.state,
                reason=domain.REASON_RENEW,
            ),
            actor=_BILLING_ACTOR,
            request=None,
            from_plan_version_id=subscription.plan_version_id,
            to_plan_version_id=subscription.plan_version_id,
            extra=_billing_facts(period),
        )
    await session.flush()
    return period.outcome or "recorded"


async def record_actionable_invoice(
    session: AsyncSession,
    period: BillingPeriod,
    *,
    invoice_id: uuid.UUID | None,
    total_minor: int,
) -> str:
    """Record billing's answer for a proration or a closing period. Neither moves the
    subscription: the upgrade already happened; the cancellation or suspension already did.

    No invoice is a proration that netted to nothing — no zero-line invoice is ever written for
    one (round 2, m8) — and the row closes `skipped`."""
    if period.state != PERIOD_PENDING:
        return "already_recorded"
    if invoice_id is None:
        period.state = PERIOD_SKIPPED
        period.outcome = "nothing_to_prorate"
        period.recorded_at = _wall()
        period.lease_until = None
        period.next_attempt_at = None
        period.last_error = None
        await session.flush()
        return "nothing_to_prorate"
    period.invoice_id = invoice_id
    period.total_minor = total_minor
    period.attempts = 0
    period.state = PERIOD_FINALIZE_PENDING
    period.outcome = {
        domain.KIND_PRORATION: "prorated",
        domain.KIND_OPENING: "opened",
    }.get(period.kind, "closed")
    period.recorded_at = _wall()
    period.lease_until = None
    period.next_attempt_at = None
    period.last_error = None
    period.permanent_failure = False
    _billing_audit(
        session,
        period,
        f"commerce.billing_run.{period.outcome}",
        {"invoice_id": str(invoice_id), "total_minor": total_minor, "kind": period.kind},
    )
    await session.flush()
    return period.outcome


async def record_finalized(session: AsyncSession, period: BillingPeriod) -> str:
    """Billing finalized the recorded draft: payment may charge it now (round 2, M5)."""
    if period.state != PERIOD_FINALIZE_PENDING:
        return "already_recorded"
    period.state = PERIOD_FINALIZED
    period.lease_until = None
    period.next_attempt_at = None
    period.last_error = None
    period.permanent_failure = False
    await session.flush()
    return "finalized"


async def _reclaim_usage(session: AsyncSession, period: BillingPeriod) -> None:
    """A renewal whose draft is being voided carried the closed period's usage: claim that usage
    as a closing period of its own, at the pin and items the renewal was claimed with, so the void
    takes back the advance charge and nothing else (review round 2, fix M4)."""
    if period.kind != domain.KIND_RENEWAL or period.usage_start is None or period.usage_end is None:
        return
    await _insert_period(
        session,
        {
            "organization_id": period.organization_id,
            "subscription_id": period.subscription_id,
            "kind": domain.KIND_CLOSE,
            "period_start": period.usage_start,
            "period_end": period.usage_end,
            "usage_start": period.usage_start,
            "usage_end": period.usage_end,
            "plan_version_id": period.plan_version_id,
            "currency": period.currency,
            "items": list(period.items),
        },
    )


async def _prorate_missed_change(
    session: AsyncSession, subscription: Subscription, period: BillingPeriod
) -> None:
    """Belt and braces for review round 2, fix B2: the subscription's pin moved off the one this
    claim was priced at. The change-plan path already claims the proration against a pending
    claim; this claims it again under the **same** derived change id, so the insert is a no-op
    when that happened and a charge for the remainder when it somehow did not. Billing prices it
    and writes nothing if the move netted to no more money."""
    if period.period_end is None:
        return
    from app import selectors

    # Priced from the **last** pin already moved to against this claim — a proration row, billed
    # or a settled marker — never from the claim's own pin once something has moved it: after an
    # upgrade then an equal-price switch, the claim's pin is two moves back, and pricing from it
    # would charge the upgrade twice (review round 3, fix B2).
    latest = await selectors.latest_proration_against(session, period.organization_id, period.id)
    from_pin = latest.plan_version_id if latest is not None else period.plan_version_id
    from_items = list(latest.items) if latest is not None else list(period.items)
    if subscription.plan_version_id == from_pin:
        return
    await _claim_proration(
        session,
        subscription,
        from_plan_version_id=from_pin,
        from_items=from_items,
        claim=period,
        at=_wall(),
        to_items=_items_snapshot(subscription),
    )


async def cascade_to_prorations(session: AsyncSession, parent: BillingPeriod) -> None:
    """The parent claim is being voided or skipped: nothing priced against it may stand (review
    round 3, fix B1). A pending proration is skipped; a recorded draft goes to be voided (it was
    never announced); a finalized one cannot exist — prorations are billed only once their parent
    is finalized — and is logged loudly and counted by the health probe if it ever does."""
    from app import selectors

    for child in await selectors.prorations_against(session, parent.organization_id, parent.id):
        if child.state == PERIOD_PENDING:
            child.state = PERIOD_SKIPPED
            child.outcome = "parent_voided"
            child.recorded_at = _wall()
            child.lease_until = None
            child.next_attempt_at = None
        elif child.state == PERIOD_FINALIZE_PENDING:
            child.state = PERIOD_VOID_PENDING
            child.outcome = "parent_voided"
            child.lease_until = None
            child.next_attempt_at = None
            child.attempts = 0
        elif child.state == PERIOD_FINALIZED:
            log.error(
                "billing_run.proration.finalized_on_voided_parent",
                organization_id=str(parent.organization_id),
                subscription_id=str(parent.subscription_id),
                proration_id=str(child.id),
                parent_id=str(parent.id),
            )
    await session.flush()


async def record_void(
    session: AsyncSession, period: BillingPeriod, *, voided: bool, invoice_id: uuid.UUID | None
) -> str:
    """Billing voided the superseded period's invoice — or there was none to void (the stale
    claim never reached billing's commit)."""
    if period.state != PERIOD_VOID_PENDING:
        return "already_recorded"
    await cascade_to_prorations(session, period)
    period.state = PERIOD_VOIDED if voided else PERIOD_SKIPPED
    period.outcome = "voided" if voided else "nothing_to_void"
    if invoice_id is not None:
        period.invoice_id = invoice_id
    period.recorded_at = _wall()
    period.lease_until = None
    period.next_attempt_at = None
    period.last_error = None
    _billing_audit(
        session,
        period,
        "commerce.billing_run.period_voided",
        {"invoice_id": str(invoice_id) if invoice_id else None, "voided": voided},
        reason="superseded",
    )
    await session.flush()
    return period.outcome


async def fail_billing_period(
    session: AsyncSession, period: BillingPeriod, *, error: str, permanent: bool = False
) -> bool:
    """A billing or payment call failed: keep the claim, say why, and back off. True if written.

    Guarded on the row still being work — a failure reported for a row another replica recorded
    in the meantime must not stamp an error and a backoff onto a finished period (review fix 8).
    The lease is released and the retry scheduled on the **wall clock**
    (`domain.retry_backoff`), so a data defect is not hammered every pass and a sibling's
    outage is retried soon after it ends.
    """
    if period.state not in (PERIOD_PENDING, PERIOD_VOID_PENDING, PERIOD_FINALIZE_PENDING):
        return False
    period.last_error = error[:300]
    period.permanent_failure = permanent
    period.lease_until = None
    period.next_attempt_at = _wall() + domain.retry_backoff(period.attempts)
    await session.flush()
    return True


async def close_stale_period(session: AsyncSession, period: BillingPeriod) -> None:
    """A pending lifecycle claim whose subscription is no longer due for it — cancelled, say,
    between the claim and the record. If the crash came after billing committed, an invoice
    exists that nothing will ever record; the row goes to `void_pending` and the run asks billing
    to void whatever it holds under the period's key (review fix 11)."""
    period.state = PERIOD_VOID_PENDING
    period.outcome = "superseded"
    period.lease_until = None
    period.next_attempt_at = None
    period.attempts = 0
    await _reclaim_usage(session, period)
    await cascade_to_prorations(session, period)
    log.warning(
        "billing_run.period.stale_closed",
        organization_id=str(period.organization_id),
        subscription_id=str(period.subscription_id),
        period_start=period.period_start.isoformat(),
    )
    await session.flush()
