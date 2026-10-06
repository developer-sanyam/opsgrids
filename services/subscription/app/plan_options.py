"""What the customer's Subscriptions page reads about plans (ADR-0033) — read-only.

Two reads, both org-scoped and both composed from this organization's own subscription rows and
catalog's internal reads:

* `plan_summaries` — the plan line of every row: the plan in force, and the one a scheduled change
  moves it to and when.
* `plan_options` — the Change plan dialog's picker: catalog's offerable set for this organization
  (active plans' published versions, public or negotiated for it, the newest per plan, audience,
  cadence and currency — never a superseded, withdrawn or retired-plan version), each marked with
  whether this subscription can take it, and the current pin marked even when it is no longer on
  offer itself.

**What a tenant may see of an amount is decided here, once** (`amount_visible`). Catalog's reads
carry every amount; this module passes one through only when the version is **negotiated for this
organization** (`restricted_to_org_id` is the caller's organization) **and** the caller may read
this organization's invoices (`core.billing.invoice.read` at ORG — the people who already see what
the organization is billed). A public version's amount is a list price, and list prices are never
sent to a tenant — the owner's rule since 2026-09-24 ("pricing is agreed per client, not listed";
catalog's tenant reads carry no amounts). That holds even for the public version the organization
is on: a trial is pinned to one, and showing its amount would publish the list price to every
trialist the Marketplace deliberately does not show it to.

Nothing here writes. The writes stay in `services.py`; the reads that need only this service's
tables stay in `selectors.py`. This module exists because these two are neither: each composes the
subscription row with catalog's answer and applies a disclosure rule to the result.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable, Sequence
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.catalog import PlanCatalog
from app.models import STATE_ACTIVE, STATE_CANCELLED, STATE_TRIAL, Subscription
from app.schemas import (
    CatalogOfferableVersion,
    CatalogPlanVersion,
    CatalogPrice,
    PlanOptionOut,
    PlanOptionsOut,
    PlanPriceOut,
    PlanRefOut,
    SubscriptionPlanSummaryOut,
)
from platform_core.auth import Principal, Scope

#: Who may see an amount: the people who may read what this organization is billed.
AMOUNTS_PERMISSION = "core.billing.invoice.read"

#: Only these states change plan (the write refuses every other with `state`).
_CHANGEABLE_STATES = (STATE_TRIAL, STATE_ACTIVE)

BLOCKED_CURRENT = "current"
BLOCKED_BUNDLE = "bundle"
BLOCKED_PENDING = "pending"


# ------------------------------------------------------------------------------ the pure rules


def may_see_amounts(principal: Principal) -> bool:
    """The caller may read this organization's invoices, organization-wide."""
    scope = principal.scope_for(AMOUNTS_PERMISSION)
    return scope is not None and scope.rank >= Scope.ORG.rank


def amount_visible(
    restricted_to_org_id: uuid.UUID | None, organization_id: uuid.UUID, *, may_see: bool
) -> bool:
    """An amount is shown only for this organization's own negotiated price, and only to a caller
    who may read its invoices. A public version (`restricted_to_org_id` NULL) is a list price and
    is never shown; another organization's version is never returned at all."""
    return may_see and restricted_to_org_id is not None and restricted_to_org_id == organization_id


def recurring_minor(prices: Iterable[CatalogPrice]) -> int:
    """Flat plus per-seat at quantity one, usage excluded — the same sum the write classifies
    direction by (`services._list_price_minor`). Display only."""
    return sum(p.amount_minor or 0 for p in prices if p.kind in ("flat", "per_seat"))


def _ref(
    *,
    version_id: uuid.UUID,
    plan_id: uuid.UUID | None,
    plan_name: str | None,
    version_no: int | None,
    billing_interval: str | None,
    restricted_to_org_id: uuid.UUID | None,
    currency: str | None,
    prices: Sequence[CatalogPrice],
    organization_id: uuid.UUID,
    may_see: bool,
) -> PlanRefOut:
    visible = amount_visible(restricted_to_org_id, organization_id, may_see=may_see)
    return PlanRefOut(
        plan_version_id=version_id,
        plan_id=plan_id,
        plan_name=plan_name,
        version_no=version_no,
        billing_interval=billing_interval,
        negotiated=restricted_to_org_id is not None and restricted_to_org_id == organization_id,
        amount_visible=visible,
        currency=currency.upper() if visible and currency else None,
        recurring_minor=recurring_minor(prices) if visible else None,
        prices=(
            [
                PlanPriceOut(
                    kind=p.kind,
                    amount_minor=p.amount_minor,
                    unit=p.unit,
                    unit_price_micros=p.unit_price_micros,
                    meter_key=p.meter_key,
                )
                for p in prices
            ]
            if visible
            else None
        ),
    )


def ref_from_pin(
    version_id: uuid.UUID,
    version: CatalogPlanVersion | None,
    organization_id: uuid.UUID,
    *,
    may_see: bool,
) -> PlanRefOut:
    """A subscription's own pin (current or scheduled), from catalog's pin lookup. A version
    catalog no longer knows is still named by its id, with nothing else."""
    if version is None:
        return PlanRefOut(plan_version_id=version_id)
    return _ref(
        version_id=version.id,
        plan_id=version.plan_id,
        plan_name=version.plan_name,
        version_no=version.version_no,
        billing_interval=version.billing_interval,
        restricted_to_org_id=version.restricted_to_org_id,
        currency=version.currency,
        prices=version.prices,
        organization_id=organization_id,
        may_see=may_see,
    )


def _ref_from_offer(
    offer: CatalogOfferableVersion, organization_id: uuid.UUID, *, may_see: bool
) -> PlanRefOut:
    return _ref(
        version_id=offer.plan_version_id,
        plan_id=offer.plan_id,
        plan_name=offer.plan_name,
        version_no=offer.version_no,
        billing_interval=offer.billing_interval,
        restricted_to_org_id=offer.restricted_to_org_id,
        currency=offer.currency,
        prices=offer.prices,
        organization_id=organization_id,
        may_see=may_see,
    )


def current_interval(subscription: Subscription, current: CatalogPlanVersion | None) -> str | None:
    """The cadence the subscription renews on — the same fallback chain the write uses."""
    return (
        subscription.billing_interval
        or domain.infer_interval(subscription.current_period_start, subscription.current_period_end)
        or (current.billing_interval if current else None)
    )


def blocked(
    offer: CatalogOfferableVersion, *, currency: str, interval: str | None
) -> tuple[str | None, str | None]:
    """Why this subscription cannot take `offer`, in the write's own codes — or `(None, None)`.

    One currency and one cadence per subscription (the write refuses both); a version that
    declares a bundle is sold by the OpsGrids team (the customer write refuses it). The sentences
    are for a customer: changing either is a conversation with OpsGrids, not a self-serve step.
    """
    if offer.currency.upper() != currency.upper():
        return (
            domain.CODE_CURRENCY,
            f"Billed in {offer.currency.upper()}; this subscription is billed in "
            f"{currency.upper()}.",
        )
    if offer.billing_interval not in ("month", "year") or (
        interval is not None and offer.billing_interval != interval
    ):
        return (
            domain.CODE_CADENCE,
            f"Billed {_cadence_word(offer.billing_interval)}; this subscription renews "
            f"{_cadence_word(interval)}. Contact OpsGrids to change how often you are billed.",
        )
    if domain.declares_bundle((f.feature_key, f.value) for f in offer.features):
        return (BLOCKED_BUNDLE, "A bundle is set up for you by the OpsGrids team.")
    return None, None


def _cadence_word(interval: str | None) -> str:
    return {"month": "monthly", "year": "yearly", "one_time": "once"}.get(
        interval or "", "on another cadence"
    )


def change_blocked_code(subscription: Subscription) -> str | None:
    """Why this subscription cannot change plan at all, in the write's own codes."""
    if subscription.entitled_products != [subscription.product_key]:
        return domain.CODE_BUNDLE
    if subscription.state not in _CHANGEABLE_STATES:
        return domain.CODE_STATE
    if scheduled_by(subscription) == domain.SCHEDULED_BY_OPSGRIDS:
        return domain.CODE_SCHEDULED_BY_OPSGRIDS
    return None


def waiting_pin(subscription: Subscription) -> uuid.UUID | None:
    """The plan a scheduled change moves this subscription to, as a customer is told it — None
    for a cancelled subscription, which never reaches another period. `services.cancel` clears the
    waiting change; a row cancelled before it did still carries one, and it is not reported."""
    if subscription.state == STATE_CANCELLED:
        return None
    return subscription.pending_plan_version_id


def scheduled_by(subscription: Subscription) -> Literal["opsgrids", "customer"] | None:
    """Who scheduled the waiting change (`opsgrids` or `customer`), None when none is reported."""
    if waiting_pin(subscription) is None:
        return None
    if domain.pending_scheduled_by(subscription.pending_change) == domain.SCHEDULED_BY_OPSGRIDS:
        return "opsgrids"
    return "customer"


def build_options(
    subscription: Subscription,
    offered: Sequence[CatalogOfferableVersion],
    current: CatalogPlanVersion | None,
    *,
    organization_id: uuid.UUID,
    may_see: bool,
) -> list[PlanOptionOut]:
    """The picker's rows: each offered version once, the current pin first and disabled.

    De-duplicated by version id (catalog's set has no repeats; this does not trust that it never
    will). When the current pin is no longer on offer — superseded, withdrawn, its plan retired —
    it still heads the list, so the customer can see what they are on beside what they could move
    to; the write accepts it only as "keep my plan" while a change is waiting. The change already
    scheduled is listed, marked and disabled (`pending`): picking it again would only "replace" it
    with itself.
    """
    currency = subscription.currency
    interval = current_interval(subscription, current)
    pending_id = waiting_pin(subscription)
    staff_scheduled = scheduled_by(subscription) == domain.SCHEDULED_BY_OPSGRIDS
    seen: set[uuid.UUID] = set()
    rows: list[PlanOptionOut] = []
    for offer in offered:
        if offer.plan_version_id in seen:
            continue
        seen.add(offer.plan_version_id)
        ref = _ref_from_offer(offer, organization_id, may_see=may_see)
        is_current = offer.plan_version_id == subscription.plan_version_id
        is_pending = offer.plan_version_id == pending_id
        code: str | None
        reason: str | None
        if is_current:
            code, reason = BLOCKED_CURRENT, "This is the plan you are on."
        elif is_pending:
            code, reason = (
                BLOCKED_PENDING,
                "Already scheduled, as agreed with the OpsGrids team."
                if staff_scheduled
                else "Already scheduled. To stay on your current plan instead, use Keep current "
                "plan.",
            )
        else:
            code, reason = blocked(offer, currency=currency, interval=interval)
        rows.append(
            PlanOptionOut(
                **ref.model_dump(),
                is_current=is_current,
                is_pending=is_pending,
                blocked_code=code,
                blocked_reason=reason,
            )
        )
    if subscription.plan_version_id not in seen:
        ref = ref_from_pin(subscription.plan_version_id, current, organization_id, may_see=may_see)
        rows.insert(
            0,
            PlanOptionOut(
                **ref.model_dump(),
                is_current=True,
                blocked_code=BLOCKED_CURRENT,
                blocked_reason="This is the plan you are on. It is no longer offered, but you "
                "keep it until you choose another.",
            ),
        )
    else:
        rows.sort(key=lambda row: not row.is_current)
    return rows


# ------------------------------------------------------------------------------ the reads


async def plan_options(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    principal: Principal,
    catalog: PlanCatalog,
    subscription: Subscription,
) -> PlanOptionsOut:
    """The Change plan picker for one subscription. The caller has already resolved the
    subscription under the tenant binding (another organization's id is a 404 before this)."""
    may_see = may_see_amounts(principal)
    pending_id = waiting_pin(subscription)
    current_doc, pending_doc, offered = await asyncio.gather(
        catalog.get_plan_version(subscription.plan_version_id),
        catalog.get_plan_version(pending_id) if pending_id else _none(),
        catalog.offerable_versions(organization_id, subscription.product_key),
    )
    return PlanOptionsOut(
        subscription_id=subscription.id,
        product_key=subscription.product_key,
        state=subscription.state,
        change_blocked_code=change_blocked_code(subscription),
        current=ref_from_pin(
            subscription.plan_version_id, current_doc, organization_id, may_see=may_see
        ),
        pending=(
            ref_from_pin(pending_id, pending_doc, organization_id, may_see=may_see)
            if pending_id
            else None
        ),
        pending_effective_at=await selectors.scheduled_change_effective_at(session, subscription),
        pending_scheduled_by=scheduled_by(subscription),
        options=build_options(
            subscription, offered, current_doc, organization_id=organization_id, may_see=may_see
        ),
    )


async def plan_summaries(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    principal: Principal,
    catalog: PlanCatalog,
    subscriptions: Sequence[Subscription],
) -> list[SubscriptionPlanSummaryOut]:
    """The plan line of every row on the page, one catalog lookup per distinct pin. A cancelled
    row reports no scheduled change (`waiting_pin`)."""
    may_see = may_see_amounts(principal)
    pins: list[uuid.UUID] = []
    for s in subscriptions:
        for pin in (s.plan_version_id, waiting_pin(s)):
            if pin is not None and pin not in pins:
                pins.append(pin)
    found = await asyncio.gather(*(catalog.get_plan_version(p) for p in pins))
    docs = dict(zip(pins, found, strict=True))
    out: list[SubscriptionPlanSummaryOut] = []
    for s in subscriptions:
        pending = waiting_pin(s)
        out.append(
            SubscriptionPlanSummaryOut(
                subscription_id=s.id,
                current=ref_from_pin(
                    s.plan_version_id, docs.get(s.plan_version_id), organization_id, may_see=may_see
                ),
                pending=(
                    ref_from_pin(pending, docs.get(pending), organization_id, may_see=may_see)
                    if pending
                    else None
                ),
                pending_effective_at=await selectors.scheduled_change_effective_at(session, s),
                pending_scheduled_by=scheduled_by(s),
            )
        )
    return out


async def _none() -> None:
    return None
