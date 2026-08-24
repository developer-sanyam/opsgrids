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

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import (
    PLAN_RETIRED,
    PLATFORM_ORG,
    VERSION_DRAFT,
    VERSION_PUBLISHED,
    AuditLog,
    Feature,
    OutboxEvent,
    Plan,
    PlanVersion,
    Price,
)
from app.schemas import PlanCreateIn, PlanPatchIn, VersionCreateIn
from platform_core.audit import record_audit
from platform_core.auth import Principal
from platform_core.errors import BusinessRuleProblem, ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

EVT_VERSION_PUBLISHED = "catalog.plan_version.published"
EVT_PLAN_RETIRED = "catalog.plan.retired"


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
    session: AsyncSession, principal: Principal, payload: PlanCreateIn, *, request: Request
) -> Plan:
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
        request=request,
    )
    return plan


async def update_plan(
    session: AsyncSession,
    principal: Principal,
    plan: Plan,
    patch: PlanPatchIn,
    *,
    request: Request,
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
        request=request,
    )
    return plan


async def retire_plan(
    session: AsyncSession, principal: Principal, plan: Plan, *, request: Request
) -> Plan:
    """Stop offering a plan. Never deletes it — a subscription pinned to one of its versions
    must stay reconstructable — and emits `catalog.plan.retired` for downstream consumers."""
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
        after={"product_key": plan.product_key, "code": plan.code},
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


async def create_version(
    session: AsyncSession,
    principal: Principal,
    plan: Plan,
    payload: VersionCreateIn,
    *,
    request: Request,
) -> PlanVersion:
    """Clone the plan's latest version into a new **draft**, never mutating a published one.

    Prices and features are taken from the payload when supplied; otherwise they are cloned
    from the latest version, so bumping a version does not mean re-typing an unchanged price
    sheet. `restricted_to_org_id` set makes this a bespoke, audience-of-one version — the same
    table, the same mechanism, as every public plan (ADR-0004 §2).
    """
    next_no = await selectors.latest_version_no(session, plan.id) + 1
    # Capture the clone source *before* inserting the new version, or it would find itself as
    # the latest and clone nothing.
    supplied = bool(payload.prices or payload.features)
    source = None if supplied else await selectors.clone_source(session, plan.id)

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
    else:
        # `source` is the plan's previous latest version (or None for a first version) —
        # captured above, so cloning copies the real predecessor's price sheet.
        prices, features = _clone_children(version, source)

    for price in prices:
        _reject_currency_mismatch(version, price)
    session.add_all([*prices, *features])
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
        request=request,
    )
    return version


def _clone_children(
    version: PlanVersion, source: PlanVersion | None
) -> tuple[list[Price], list[Feature]]:
    if source is None or source.id == version.id:
        return [], []
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
    return prices, features


def _reject_currency_mismatch(version: PlanVersion, price: Price) -> None:
    if price.currency != version.currency:
        raise BusinessRuleProblem(
            f"price currency {price.currency} does not match the version currency "
            f"{version.currency}"
        )


async def publish_version(
    session: AsyncSession, principal: Principal, version: PlanVersion, *, request: Request
) -> PlanVersion:
    """Freeze a draft. From here the row is immutable (trigger + `_ensure_draft`), and
    `catalog.plan_version.published` is emitted for subscription and console to consume."""
    _ensure_draft(version)

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
        after={"plan_id": str(version.plan_id), "version_no": version.version_no},
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
