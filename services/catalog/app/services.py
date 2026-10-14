"""Writes for catalog.

Business rules, audit rows and outbox rows. Nothing here opens or commits a transaction: an
operator route takes its session from `get_operator_session` (app/api/admin.py), which opens
the request's one transaction and binds it to the platform sentinel organization first, so the
change, its audit entry and any event it emits all commit or all roll back together.

The one rule this module exists to protect is that **a published plan version is immutable**
(ADR-0000 §9, ADR-0004 §2). Editing a plan clones its latest version into a new draft; the
published row is never mutated. The database enforces it with a `BEFORE UPDATE` trigger; this
layer refuses it first, with a message a person can act on rather than a raw trigger error.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Table, delete, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import (
    FEATURE_BUNDLED_PRODUCTS,
    FEATURE_TRIAL_DAYS,
    PLAN_ACTIVE,
    PLAN_RETIRED,
    PLATFORM_ORG,
    PRICE_PER_SEAT,
    VERSION_DRAFT,
    VERSION_GRANDFATHERED,
    VERSION_PUBLISHED,
    AuditLog,
    Feature,
    IdempotencyKey,
    OutboxEvent,
    Plan,
    PlanVersion,
    Price,
    SeatClass,
)
from app.registry import ProductSnapshot
from app.schemas import PlanCreateIn, PlanPatchIn, SeatClassIn, VersionCreateIn
from platform_core.audit import record_audit
from platform_core.auth import Principal
from platform_core.db import constraint_name
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

EVT_VERSION_PUBLISHED = "catalog.plan_version.published"
EVT_VERSION_GRANDFATHERED = "catalog.plan_version.grandfathered"
EVT_PLAN_RETIRED = "catalog.plan.retired"
EVT_PLAN_REACTIVATED = "catalog.plan.reactivated"

#: How many times drafting a version re-reads the next number after losing the
#: `uq_plan_versions_plan_version` race to a concurrent draft before giving up with a 409.
DRAFT_ATTEMPTS = 3


def _actor(principal: Principal) -> dict[str, Any]:
    """Who made this catalogue change. Staff act across customers, so this is the accountable
    human, not a tenant membership — recorded on every audit and outbox row."""
    return {
        "type": "operator",
        "id": str(principal.sub),
        "audience": principal.audience,
    }


def _ensure_draft(version: PlanVersion) -> None:
    """The service-layer half of the immutability rule (the trigger is the other half).

    Refused here so the caller gets a business-rule message rather than a raw
    `plan_version is published and immutable` from the database.
    """
    if version.state != VERSION_DRAFT:
        raise BusinessRuleProblem(
            f"plan version {version.version_no} is {version.state} and cannot be changed; "
            "clone it into a new draft instead"
        )


async def create_plan(
    session: AsyncSession,
    principal: Principal,
    payload: PlanCreateIn,
    *,
    request: Request | None,
    reason: str | None = None,
    products: ProductSnapshot | None = None,
) -> Plan:
    """A new, version-less plan under a product. With a registry snapshot (the operator route
    always takes one, before its transaction opens) the product key must be one product-service
    has registered and not a platform product every customer already has — a typo here would be a
    plan nobody can ever buy."""
    if products is not None:
        _require_sellable_product(payload.product_key, products)
    plan = Plan(
        product_key=payload.product_key,
        code=payload.code,
        name=payload.name,
        sort_order=payload.sort_order,
    )
    session.add(plan)
    try:
        await session.flush()
    except Exception as exc:  # unique (product_key, code)
        # Surface a clean 409 rather than letting the IntegrityError handler guess.
        raise ConflictProblem(
            f"a plan {payload.code!r} already exists for product {payload.product_key!r}"
        ) from exc

    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=principal.sub,
        action="catalog.plan.created",
        entity="plan",
        entity_id=plan.id,
        after={"product_key": plan.product_key, "code": plan.code, "name": plan.name},
        reason=reason,
        request=request,
    )
    return plan


def _require_sellable_product(product_key: str, snapshot: ProductSnapshot) -> None:
    entry = snapshot.require("checking a plan's product").get(product_key)
    if entry is None:
        message = f"{product_key!r} is not a registered product"
        raise BusinessRuleProblem(
            message,
            errors=[{"field": "product_key", "code": "unknown_product", "message": message}],
        )
    if entry.is_platform:
        message = f"{product_key!r} is a platform product every customer already has; not sold"
        raise BusinessRuleProblem(
            message,
            errors=[{"field": "product_key", "code": "platform_product", "message": message}],
        )


async def update_plan(
    session: AsyncSession,
    principal: Principal,
    plan: Plan,
    patch: PlanPatchIn,
    *,
    request: Request | None,
    reason: str | None = None,
) -> Plan:
    """Edit a plan's presentation. Never its pricing — that is a new version (ADR-0000 §9)."""
    before = {"name": plan.name, "sort_order": plan.sort_order}
    if patch.name is not None:
        plan.name = patch.name
    if patch.sort_order is not None:
        plan.sort_order = patch.sort_order
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=principal.sub,
        action="catalog.plan.updated",
        entity="plan",
        entity_id=plan.id,
        before=before,
        after={"name": plan.name, "sort_order": plan.sort_order},
        reason=reason,
        request=request,
    )
    return plan


async def retire_plan(
    session: AsyncSession,
    principal: Principal,
    plan: Plan,
    *,
    request: Request | None,
    reason: str | None = None,
) -> Plan:
    """Stop offering a plan. Never deletes it — a subscription pinned to one of its versions
    must stay reconstructable — and emits `catalog.plan.retired` for downstream consumers.

    Retiring hides the plan from the customer read (`visible_plans` filters on `active`). It does
    **not** yet stop a pin by version id: subscription's `_validate_new_pin` does not read plan
    state, which is a later slice (docs/superpowers/specs/2026-10-06-admin-plans-page-design.md).
    """
    if plan.state == PLAN_RETIRED:
        raise BusinessRuleProblem(f"plan {plan.code!r} is already retired")
    plan.state = PLAN_RETIRED
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=principal.sub,
        action="catalog.plan.retired",
        entity="plan",
        entity_id=plan.id,
        before={"state": PLAN_ACTIVE},
        after={"state": PLAN_RETIRED, "product_key": plan.product_key, "code": plan.code},
        reason=reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=PLATFORM_ORG,
        event_type=EVT_PLAN_RETIRED,
        aggregate_type="plan",
        aggregate_id=plan.id,
        payload={"plan_id": str(plan.id), "product_key": plan.product_key, "code": plan.code},
        actor=_actor(principal),
    )
    return plan


async def reactivate_plan(
    session: AsyncSession,
    principal: Principal,
    plan: Plan,
    *,
    request: Request | None,
    reason: str | None = None,
) -> Plan:
    """Undo a retire: offer the plan again. Its versions were never touched by retiring, so every
    published one is offered again as it was; a version that should stay off sale is withdrawn
    individually instead. Emits `catalog.plan.reactivated`."""
    if plan.state != PLAN_RETIRED:
        raise BusinessRuleProblem(f"plan {plan.code!r} is not retired")
    plan.state = PLAN_ACTIVE
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=principal.sub,
        action="catalog.plan.reactivated",
        entity="plan",
        entity_id=plan.id,
        before={"state": PLAN_RETIRED},
        after={"state": PLAN_ACTIVE, "product_key": plan.product_key, "code": plan.code},
        reason=reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=PLATFORM_ORG,
        event_type=EVT_PLAN_REACTIVATED,
        aggregate_type="plan",
        aggregate_id=plan.id,
        payload={"plan_id": str(plan.id), "product_key": plan.product_key, "code": plan.code},
        actor=_actor(principal),
    )
    return plan


async def create_version(
    session: AsyncSession,
    principal: Principal,
    plan: Plan,
    payload: VersionCreateIn,
    *,
    request: Request | None,
    products: ProductSnapshot | None = None,
    reason: str | None = None,
) -> PlanVersion:
    """Clone the plan's latest version into a new **draft**, never mutating a published one.

    Prices, features and seat classes are taken from the payload when supplied; otherwise all
    three are cloned from the latest version, so bumping a version does not mean re-typing an
    unchanged price sheet. `restricted_to_org_id` set makes this a bespoke, audience-of-one
    version — the same table, the same mechanism, as every public plan (ADR-0004 §2), and the
    same one a partner wholesale price uses (docs/commerce-packaging.md).
    """
    next_no = await selectors.latest_version_no(session, plan.id) + 1
    # Capture the clone source *before* inserting the new version, or it would find itself as
    # the latest and clone nothing. MAJOR-5 (fix round 1): filtered to the *same audience* this
    # new version is being authored for — a public bump clones the newest public version, never
    # a bespoke one a version-number bump happened to outrank; a bespoke re-version without an
    # explicit price sheet clones that same organization's own previous bespoke version.
    supplied = bool(payload.prices or payload.features or payload.seat_classes)
    source = (
        None
        if supplied
        else await selectors.clone_source(
            session, plan.id, restricted_to_org_id=payload.restricted_to_org_id
        )
    )

    # A bundle's members are checked before anything is written, while this is still a draft an
    # operator is looking at: once published, a mistake in the list is immutable and the first
    # person to find it is a customer entitled to less than they paid for.
    declared = (
        [(f.feature_key, f.value) for f in payload.features]
        if supplied
        else [(f.feature_key, f.value) for f in (source.features if source else [])]
    )
    _validate_bundle(plan, declared, products)

    version = PlanVersion(
        plan_id=plan.id,
        version_no=next_no,
        state=VERSION_DRAFT,
        billing_interval=payload.billing_interval,
        currency=payload.currency.upper(),
        restricted_to_org_id=payload.restricted_to_org_id,
    )
    session.add(version)
    await session.flush()

    if supplied:
        prices = [
            Price(
                plan_version_id=version.id,
                kind=p.kind,
                amount_minor=p.amount_minor,
                currency=p.currency.upper(),
                unit=p.unit,
                unit_price_micros=p.unit_price_micros,
                meter_key=p.meter_key,
            )
            for p in payload.prices
        ]
        features = [
            Feature(plan_version_id=version.id, feature_key=f.feature_key, value=f.value)
            for f in payload.features
        ]
        seat_drafts = list(payload.seat_classes)
    else:
        # `source` is the plan's previous latest version (or None for a first version) —
        # captured above, so cloning copies the real predecessor's price sheet.
        prices, features, seat_drafts = _clone_children(version, source)

    for price in prices:
        _reject_currency_mismatch(version, price)
    _reject_unresolvable_seat_prices(seat_drafts, prices)

    # Two flushes, deliberately: a seat class carries a real foreign key to the price it is
    # charged at, and a price's id is only assigned when its row is flushed. Building the
    # classes first would point them at NULL.
    session.add_all([*prices, *features])
    await session.flush()
    session.add_all(_build_seat_classes(version, seat_drafts, prices))
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=principal.sub,
        action="catalog.plan_version.drafted",
        entity="plan_version",
        entity_id=version.id,
        after={
            "plan_id": str(plan.id),
            "version_no": version.version_no,
            "restricted_to_org_id": str(version.restricted_to_org_id)
            if version.restricted_to_org_id
            else None,
        },
        reason=reason,
        request=request,
    )
    return version


async def draft_version(
    session: AsyncSession,
    principal: Principal,
    plan: Plan,
    payload: VersionCreateIn,
    *,
    request: Request | None,
    products: ProductSnapshot | None,
    reason: str | None,
) -> PlanVersion:
    """`create_version` for the operator route, safe against a concurrent draft on the same plan.

    The next `version_no` is read, then inserted; two drafts racing (an owner here, console's
    bespoke-price endpoint there) both read the same number and the loser hits
    `uq_plan_versions_plan_version`. A SAVEPOINT around each attempt rolls back only the losing
    insert — the request's tenant binding and its idempotency claim survive — and the next attempt
    re-reads the number, as `console_internal` does. After `DRAFT_ATTEMPTS` it is a 409.
    """
    for _attempt in range(DRAFT_ATTEMPTS):
        try:
            async with session.begin_nested():
                return await create_version(
                    session,
                    principal,
                    plan,
                    payload,
                    request=request,
                    products=products,
                    reason=reason,
                )
        except IntegrityError as exc:
            if constraint_name(exc) != "uq_plan_versions_plan_version":
                raise
    raise ConflictProblem(
        "other drafts on this plan kept taking the next version number; reload and try again"
    )


def _validate_trial_days(value: Any) -> None:
    """A version's trial length, the only one subscription will grant (the caller no longer
    chooses). Whole days, 0 (no trial) to 365."""
    message = domain.trial_days_problem(value)
    if message is not None:
        raise BusinessRuleProblem(message)


def _validate_bundle(
    plan: Plan, declared: list[tuple[str, Any]], snapshot: ProductSnapshot | None
) -> None:
    """Refuse a `bundled_products` declaration product-service's registry cannot vouch for.

    Judged against a snapshot read before the transaction opened (D3 #10): only a version that
    declares a bundle needs it, so an unreachable registry fails only such a version (503). With
    no snapshot at all — a service-level caller that never consulted the registry — a bundle is
    refused rather than trusted.
    """
    # Presence of the key, not truthiness of its value: an explicit `"bundled_products": null`
    # is a malformed bundle declaration, and must be refused rather than read as "not a bundle".
    features = dict(declared)
    if FEATURE_TRIAL_DAYS in features:
        _validate_trial_days(features[FEATURE_TRIAL_DAYS])
    if FEATURE_BUNDLED_PRODUCTS not in features:
        # The other direction (ADR-0030 §3): a bundle product's version that names no members
        # would entitle only the bundle's own key, which routes nowhere. Checked here only when
        # the registry was read; with it down a plain draft still authors, and publish — which
        # always needs the registry — refuses the gap there.
        known = snapshot.products if snapshot is not None else None
        entry = known.get(plan.product_key) if known is not None else None
        if entry is not None and entry.is_bundle:
            message = _bundle_members_missing(plan.product_key)
            raise BusinessRuleProblem(
                message,
                errors=[
                    {
                        "field": "features.bundled_products",
                        "code": "bundle_members_missing",
                        "message": message,
                    }
                ],
            )
        return
    members = features[FEATURE_BUNDLED_PRODUCTS]
    if snapshot is None:
        raise BusinessRuleProblem(
            "a bundle cannot be authored without the product registry to check its members"
        )
    products = snapshot.require("authoring a bundle")
    problems = domain.bundle_member_problems(
        products.get(plan.product_key), plan.product_key, members, products
    )
    if problems:
        raise BusinessRuleProblem("bundled_products: " + "; ".join(problems))


def _bundle_members_missing(product_key: str) -> str:
    return (
        f"{product_key!r} is a bundle, so every version must declare bundled_products; "
        "without it a subscription is entitled to nothing it can open"
    )


def _clone_children(
    version: PlanVersion, source: PlanVersion | None
) -> tuple[list[Price], list[Feature], list[SeatClassIn]]:
    """The previous version's price sheet, feature sheet and seat classes, as fresh material.

    Seat classes come back as *drafts* rather than rows: each names its price by position in the
    cloned price list, because the new price rows do not have ids until they are flushed. The
    positions are stable because `PlanVersion.prices` is explicitly ordered, and the clone builds
    the new list in exactly that order.
    """
    if source is None or source.id == version.id:
        return [], [], []
    prices = [
        Price(
            plan_version_id=version.id,
            kind=p.kind,
            amount_minor=p.amount_minor,
            currency=p.currency,
            unit=p.unit,
            unit_price_micros=p.unit_price_micros,
            meter_key=p.meter_key,
        )
        for p in source.prices
    ]
    features = [
        Feature(plan_version_id=version.id, feature_key=f.feature_key, value=f.value)
        for f in source.features
    ]
    position = {price.id: index for index, price in enumerate(source.prices)}
    seat_classes = [
        SeatClassIn(
            code=sc.code,
            name=sc.name,
            price_index=position.get(sc.price_id) if sc.price_id else None,
            included_count=sc.included_count,
            can_write=sc.can_write,
            sort_order=sc.sort_order,
            description=sc.description,
        )
        for sc in source.seat_classes
    ]
    return prices, features, seat_classes


def _reject_unresolvable_seat_prices(seat_classes: list[SeatClassIn], prices: list[Price]) -> None:
    """A seat class must point at a real `per_seat` price of this same version, or at nothing.

    Refused here, before anything is written, because both failures are silent otherwise: an
    index past the end of the price list would raise a bare `IndexError` from inside a flush,
    and a class charged at a `flat` or `usage` price would bill one amount however many seats
    were bought — which is not a seat class at all.
    """
    for seat_class in seat_classes:
        if seat_class.price_index is None:
            continue
        if seat_class.price_index >= len(prices):
            raise BusinessRuleProblem(
                f"seat class {seat_class.code!r} names price #{seat_class.price_index}, "
                f"but this version has {len(prices)} price(s)"
            )
        priced_at = prices[seat_class.price_index]
        if priced_at.kind != PRICE_PER_SEAT:
            raise BusinessRuleProblem(
                f"seat class {seat_class.code!r} must be charged at a {PRICE_PER_SEAT} price, "
                f"not a {priced_at.kind!r} one"
            )


def _build_seat_classes(
    version: PlanVersion, seat_classes: list[SeatClassIn], prices: list[Price]
) -> list[SeatClass]:
    """Materialize the seat classes once their prices have ids. Validated already."""
    return [
        SeatClass(
            plan_version_id=version.id,
            code=sc.code,
            name=sc.name,
            price_id=None if sc.price_index is None else prices[sc.price_index].id,
            included_count=sc.included_count,
            can_write=sc.can_write,
            sort_order=sc.sort_order,
            description=sc.description,
        )
        for sc in seat_classes
    ]


def _reject_currency_mismatch(version: PlanVersion, price: Price) -> None:
    if price.currency != version.currency:
        raise BusinessRuleProblem(
            f"price currency {price.currency} does not match the version currency "
            f"{version.currency}"
        )


async def publish_version(
    session: AsyncSession,
    principal: Principal,
    version: PlanVersion,
    *,
    request: Request | None,
    reason: str | None = None,
    products: ProductSnapshot | None = None,
    allow_zero_amounts: bool = False,
) -> PlanVersion:
    """Freeze a draft. From here the row is immutable (trigger + `_ensure_draft`), and
    `catalog.plan_version.published` is emitted for subscription and console to consume.

    Validated first (`domain.publish_problems`, plus the bundle members against the registry),
    because this is the last moment a mistake in the sheet is cheap: every problem is returned at
    once as the problem document's `errors[]`, so an operator fixes the draft in one pass.
    """
    _ensure_draft(version)
    await _ensure_publishable(session, version, products, allow_zero_amounts=allow_zero_amounts)

    version.state = VERSION_PUBLISHED
    version.published_at = datetime.now(UTC)
    await session.flush()

    plan = await selectors.get_plan(session, version.plan_id)
    product_key = plan.product_key if plan else None

    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=principal.sub,
        action="catalog.plan_version.published",
        entity="plan_version",
        entity_id=version.id,
        before={"state": VERSION_DRAFT},
        after={
            "plan_id": str(version.plan_id),
            "version_no": version.version_no,
            "state": VERSION_PUBLISHED,
            "allow_zero_amounts": allow_zero_amounts,
        },
        reason=reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=PLATFORM_ORG,
        event_type=EVT_VERSION_PUBLISHED,
        aggregate_type="plan_version",
        aggregate_id=version.id,
        payload={
            "plan_id": str(version.plan_id),
            "plan_version_id": str(version.id),
            "product_key": product_key,
            "version_no": version.version_no,
            "currency": version.currency,
            "billing_interval": version.billing_interval,
            "restricted_to_org_id": str(version.restricted_to_org_id)
            if version.restricted_to_org_id
            else None,
            "published_at": version.published_at.isoformat() if version.published_at else None,
        },
        actor=_actor(principal),
    )
    return version


async def _ensure_publishable(
    session: AsyncSession,
    version: PlanVersion,
    snapshot: ProductSnapshot | None,
    *,
    allow_zero_amounts: bool,
) -> None:
    fresh = await selectors.get_version(session, version.id)
    if fresh is None:  # pragma: no cover - the caller holds the row
        raise NotFoundProblem("No such plan version")
    features = {f.feature_key: f.value for f in fresh.features}
    # In the order every read returns (`PlanVersionOut` sorts by `domain.price_sort_key`), so a
    # problem's `prices.{n}` and "price #n" name the row the operator sees as #n — not the
    # relationship's `created_at, id` order, which ties inside one transaction and falls to uuid.
    ordered = sorted(
        fresh.prices,
        key=lambda p: domain.price_sort_key(
            p.kind, p.unit, p.meter_key, p.amount_minor, p.unit_price_micros, str(p.id)
        ),
    )
    problems = domain.publish_problems(
        currency=fresh.currency,
        prices=[
            domain.PublishablePrice(
                kind=p.kind,
                amount_minor=p.amount_minor,
                currency=p.currency,
                unit=p.unit,
                unit_price_micros=p.unit_price_micros,
                meter_key=p.meter_key,
            )
            for p in ordered
        ],
        features=features,
        allow_zero_amounts=allow_zero_amounts,
    )
    newer = await selectors.superseding_version_no(session, fresh)
    if newer is not None:
        # Published, it would be superseded at once: live on paper (audit row, event), never
        # offered and never shown as the price — a re-price that silently does nothing. Reachable
        # whenever a draft outlives a newer publish (two drafts, or a release that publishes
        # versions above an open one, ADR-0035).
        problems.append(
            domain.PublishProblem(
                "version_no",
                "superseded",
                f"v{newer} of this plan is already published for the same audience, cadence and "
                f"currency, so v{fresh.version_no} would never be offered or shown as the price; "
                f"delete this draft and start a new one from v{newer}",
            )
        )
    plan = fresh.plan
    if snapshot is not None and FEATURE_BUNDLED_PRODUCTS not in features:
        # ADR-0030 §3: whether this product is a bundle is the registry's to say, so publishing
        # needs it for every version, not only one that declares members. A failed read raises
        # its 503 here — retry once product-service is back — rather than freezing a bundle
        # version that names nothing. (`None` is a service-level caller that never consulted the
        # registry; every HTTP route that publishes passes a snapshot.)
        registered = snapshot.require(f"checking whether {plan.product_key!r} is a bundle")
        entry = registered.get(plan.product_key)
        if entry is not None and entry.is_bundle:
            problems.append(
                domain.PublishProblem(
                    "features.bundled_products",
                    "bundle_members_missing",
                    _bundle_members_missing(plan.product_key),
                )
            )
    if FEATURE_BUNDLED_PRODUCTS in features:
        # Re-checked at publish, not only at draft: the registry may have moved on since the
        # draft was written, and once frozen a wrong member list is a customer's problem.
        if snapshot is None:
            problems.append(
                domain.PublishProblem(
                    "features.bundled_products",
                    "bundle_unverifiable",
                    "a bundle cannot be published without the product registry to check it",
                )
            )
        else:
            products = snapshot.require("publishing a bundle")
            for message in domain.bundle_member_problems(
                products.get(plan.product_key),
                plan.product_key,
                features[FEATURE_BUNDLED_PRODUCTS],
                products,
            ):
                problems.append(
                    domain.PublishProblem("features.bundled_products", "bundle_member", message)
                )
    if problems:
        raise BusinessRuleProblem(
            f"plan version {fresh.version_no} cannot be published: "
            + "; ".join(p.message for p in problems),
            errors=[{"field": p.field, "code": p.code, "message": p.message} for p in problems],
        )


async def withdraw_version(
    session: AsyncSession,
    principal: Principal,
    version: PlanVersion,
    *,
    request: Request | None,
    reason: str | None = None,
) -> PlanVersion:
    """Stop selling one published version: `published -> grandfathered`.

    Every subscription already pinned to it keeps it — its prices, features and seat classes are
    frozen exactly as before — but it is no longer published, so no customer browse and no
    published-version check finds it. The only change the row trigger allows on a published
    version (migration b7e4d2a9c613) is `state`, and nothing else, so `updated_at` is set to
    itself here to keep the column's `onupdate` out of the statement. Emits
    `catalog.plan_version.grandfathered`.
    """
    if version.state != VERSION_PUBLISHED:
        raise BusinessRuleProblem(
            f"plan version {version.version_no} is {version.state}; only a published version "
            "can be withdrawn"
        )
    table = cast(Table, PlanVersion.__table__)
    await session.execute(
        update(table)
        .where(table.c.id == version.id, table.c.state == VERSION_PUBLISHED)
        .values(state=VERSION_GRANDFATHERED, updated_at=table.c.updated_at)
    )
    await session.refresh(version)
    plan = await selectors.get_plan(session, version.plan_id)
    product_key = plan.product_key if plan else None
    withdrawn_at = datetime.now(UTC)

    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=principal.sub,
        action="catalog.plan_version.grandfathered",
        entity="plan_version",
        entity_id=version.id,
        before={"state": VERSION_PUBLISHED},
        after={
            "plan_id": str(version.plan_id),
            "version_no": version.version_no,
            "state": VERSION_GRANDFATHERED,
        },
        reason=reason,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=PLATFORM_ORG,
        event_type=EVT_VERSION_GRANDFATHERED,
        aggregate_type="plan_version",
        aggregate_id=version.id,
        payload={
            "plan_id": str(version.plan_id),
            "plan_version_id": str(version.id),
            "product_key": product_key,
            "version_no": version.version_no,
            "currency": version.currency,
            "billing_interval": version.billing_interval,
            "restricted_to_org_id": str(version.restricted_to_org_id)
            if version.restricted_to_org_id
            else None,
            "withdrawn_at": withdrawn_at.isoformat(),
        },
        actor=_actor(principal),
    )
    return version


async def delete_draft_version(
    session: AsyncSession,
    principal: Principal,
    version: PlanVersion,
    *,
    request: Request | None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Discard a draft — it was never an offer, so nothing can be pinned to it.

    A published or grandfathered version is refused here and by the delete trigger underneath
    (migration e3f8a91c7b2d). Children go first and explicitly — seat classes, then prices and
    features, then the version — because `seat_classes.price_id` is `ON DELETE RESTRICT` and the
    order a cascade would take is not one to rely on. A discarded draft's number is reused by
    the next draft only if it was the newest; nothing outside catalog ever saw it.
    """
    if version.state != VERSION_DRAFT:
        raise BusinessRuleProblem(
            f"plan version {version.version_no} is {version.state}; only a draft can be deleted "
            "— withdraw a published version instead"
        )
    gone: dict[str, Any] = {
        "id": version.id,
        "plan_id": version.plan_id,
        "version_no": version.version_no,
    }
    before = {
        "plan_id": str(version.plan_id),
        "version_no": version.version_no,
        "state": VERSION_DRAFT,
        "restricted_to_org_id": str(version.restricted_to_org_id)
        if version.restricted_to_org_id
        else None,
    }
    for model in (SeatClass, Price, Feature):
        child = cast(Table, model.__table__)
        await session.execute(delete(child).where(child.c.plan_version_id == version.id))
    versions = cast(Table, PlanVersion.__table__)
    await session.execute(delete(versions).where(versions.c.id == version.id))
    session.expunge(version)

    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=principal.sub,
        action="catalog.plan_version.draft_deleted",
        entity="plan_version",
        entity_id=gone["id"],
        before=before,
        reason=reason,
        request=request,
    )
    return gone


# --------------------------------------------------------------------- idempotency keys


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """Either a fresh claim (`claim_id`) the caller must complete, or a recorded answer to
    replay (`replay_status` + `replay_body`). Never both."""

    claim_id: uuid.UUID | None = None
    replay_status: int | None = None
    replay_body: Any = None


async def claim_idempotency_key(
    session: AsyncSession, principal: Principal, *, key: str, action: str, fingerprint: str
) -> IdempotencyClaim:
    """Claim `key` for this request **before** the change, in the change's own transaction.

    `ON CONFLICT DO NOTHING` on `(organization_id, key)`: a concurrent request with the same key
    waits on the unique index until the first commits or rolls back. Rolled back — the claim was
    never real, and this one takes it. Committed — its row is read and either replayed (same
    operator, same fingerprint) or refused with a 409 (anything else). A claim is completed in
    the same transaction as the change, so a committed one always carries its answer.
    """
    claimed = (
        await session.execute(
            pg_insert(IdempotencyKey)
            .values(
                id=uuid.uuid4(),
                organization_id=PLATFORM_ORG,
                key=key,
                action=action,
                actor_id=principal.sub,
                fingerprint=fingerprint,
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_keys_org_key")
            .returning(IdempotencyKey.id)
        )
    ).scalar_one_or_none()
    if claimed is not None:
        return IdempotencyClaim(claim_id=claimed)

    existing = await selectors.get_idempotency_claim(session, key=key)
    if (
        existing is None
        or existing.actor_id != principal.sub
        or existing.fingerprint != fingerprint
    ):
        raise ConflictProblem(
            "This Idempotency-Key was already used for a different request; "
            "send a new key for a new change"
        )
    if existing.status_code is None:  # pragma: no cover - completed in the claiming transaction
        raise ConflictProblem("A request with this Idempotency-Key is still in progress")
    return IdempotencyClaim(replay_status=existing.status_code, replay_body=existing.response)


async def complete_idempotency_key(
    session: AsyncSession, claim_id: uuid.UUID, *, status_code: int, response: Any
) -> None:
    """Record the answer on the claim, in the same transaction as the change it answers."""
    await session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.id == claim_id)
        .values(status_code=status_code, response=response, completed_at=func.now())
    )


async def require_plan(session: AsyncSession, plan_id: Any) -> Plan:
    plan = await selectors.get_plan(session, plan_id)
    if plan is None:
        raise NotFoundProblem("No such plan")
    return plan


async def require_version(session: AsyncSession, version_id: Any) -> PlanVersion:
    version = await selectors.get_version(session, version_id)
    if version is None:
        raise NotFoundProblem("No such plan version")
    return version


async def require_plan_for_update(session: AsyncSession, plan_id: Any) -> Plan:
    plan = await selectors.get_plan_for_update(session, plan_id)
    if plan is None:
        raise NotFoundProblem("No such plan")
    return plan


async def require_version_for_update(session: AsyncSession, version_id: Any) -> PlanVersion:
    version = await selectors.get_version_for_update(session, version_id)
    if version is None:
        raise NotFoundProblem("No such plan version")
    return version
