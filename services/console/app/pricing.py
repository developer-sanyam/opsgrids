"""Composing a client's price across catalog and subscription — `GET`/`POST .../pricing`.

Kept out of `selectors.py` and `services.py`: this is network composition across two other
services, the same shape `directory_client` is for account and `delegation` is for overrides,
not a local query or a local write. The one local write this flow makes —
`services.record_bespoke_price` — stays in `services.py` with everything else that writes
console's own schema.

## Timing semantics, decided once here rather than re-derived at each call site

Subscription's own `_change_plan` already decides all of this (ADR-0005 addendum §4); this module
never re-implements the rule, it only reads the outcome off the response:

* a **trial** re-pins immediately, at no charge — `_change_plan`'s `paid` flag is false with no
  lifecycle claim outstanding, so neither the proration branch nor the deferred-downgrade branch
  fires. `effective="now"`.
* a paid **upgrade** (the bespoke price is higher than the pin it replaces) re-pins immediately
  and prorates the difference now — subscription's existing, unmodified rule. `effective="now"`.
* a paid **downgrade** is scheduled for `current_period_end` via subscription's existing
  `pending_plan_version_id` mechanism, reused exactly as it already exists rather than building a
  new "scheduled upgrade" path subscription does not have — the brief's own instruction where the
  service already supports a scheduled re-pin. `effective="next_period"`.
* `past_due`, `suspended` and `cancelled` subscriptions refuse a change-plan outright
  (subscription's `BusinessRuleProblem`, surfaced here as a 422 from `change_plan`). **This is a
  deviation from a literal reading of the API contract**: its `effective` enum has only three
  members, and there is no fourth one for "refused by a business rule". Folded into
  `not_subscribed` as the least misleading of the three available — the wire truth the UI needs
  ("the live subscription is not on this price") is the same one `not_subscribed` already states
  for "no subscription at all" and "`apply_to_subscription` was false". The bespoke price is still
  created and published in every case; only the re-pin is skipped. Called out again on
  `SetPricingOut.effective`'s own docstring.
* re-requesting a price the subscription is **already** pinned to (or already has scheduled) is
  answered without a second call to subscription at all — read straight off the subscription list
  this flow already fetched — rather than relying on subscription's own "already pinned" refusal,
  which would otherwise report a redundant, idempotent request as `not_subscribed`.

## Idempotency

`Idempotency-Key` is optional. When given, a repeat with the same key and the same request body
(`domain.pricing_request_fingerprint`) returns the first attempt's recorded result without calling
catalog or subscription again; the same key with a *different* body is a 409. There is no separate
claim row: the two downstream calls are themselves safe to repeat (catalog reuses a matching
bespoke version by content; a subscription already on the target pin is detected before a second
`change_plan` call, above) so a crash between the calls and this module's own record step is
recovered by the client's retry redoing idempotent work, not by a resumable claim. This is a
narrower reading of docs/io-outside-transactions.md's claim/call/record than the finance/payment
slices' resumable claims — reasonable here because a staff pricing change is a low-frequency,
staff-initiated act with an operator watching the response, not a high-volume automated retry
ladder. Concurrent identical requests racing before either has recorded anything can still each
reach catalog and, rarely, both create a version before either publishes; this is called out as an
accepted residual (see the handback report) rather than solved with claim-row locking here.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors, services
from app.catalog_client import CatalogError
from app.catalog_client import ensure_bespoke_price as _ensure_bespoke_price
from app.catalog_client import get_plan_version as _get_plan_version
from app.catalog_client import get_public_flat_plans as _get_public_flat_plans
from app.config import Settings
from app.models import AuditLog
from app.schemas import (
    OrganizationPricingOut,
    PriceHistoryEntryOut,
    ProductPriceOut,
    ProductPricingOut,
    ProductSubscriptionOut,
    SetPricingIn,
    SetPricingOut,
)
from app.subscription_client import SubscriptionChangeError
from app.subscription_client import change_plan as _change_plan
from app.subscription_client import list_subscriptions as _list_subscriptions
from platform_core.auth import Principal
from platform_core.errors import (
    ConflictProblem,
    NotFoundProblem,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.logging import get_logger

log = get_logger("console.pricing")


def _price_out(doc: dict[str, Any], *, is_bespoke: bool) -> ProductPriceOut | None:
    flat = next((p for p in doc.get("prices", []) if p.get("kind") == "flat"), None)
    if flat is None:
        return None
    return ProductPriceOut(
        plan_version_id=uuid.UUID(str(doc["id"])),
        amount_minor=int(flat.get("amount_minor") or 0),
        currency=str(flat.get("currency") or doc.get("currency")),
        billing_interval=str(doc["billing_interval"]),
        is_bespoke=is_bespoke,
    )


def _history_entry(row: AuditLog) -> PriceHistoryEntryOut:
    after = row.after or {}
    return PriceHistoryEntryOut(
        plan_version_id=uuid.UUID(str(row.entity_id)),
        amount_minor=int(after.get("amount_minor", 0)),
        currency=str(after.get("currency", "")),
        billing_interval=str(after.get("billing_interval", "")),
        created_at=row.at,
        created_by_email_masked=after.get("created_by_email_masked"),
        reason=row.reason,
    )


async def build_pricing_view(
    settings: Settings,
    directory_session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    client: httpx.AsyncClient,
) -> OrganizationPricingOut:
    """Every product's pricing picture for one organization (`GET .../pricing`)."""
    try:
        public_plans = await _get_public_flat_plans(settings, client=client)
        subs = await _list_subscriptions(settings, organization_id, client=client)
    except (CatalogError, SubscriptionChangeError) as exc:
        raise UpstreamUnavailableProblem(str(exc)) from exc

    public_by_key = {p["product_key"]: p for p in public_plans}
    subs_by_key = {s["product_key"]: s for s in subs}
    product_keys = sorted(set(public_by_key) | set(subs_by_key))

    products: list[ProductPricingOut] = []
    for key in product_keys:
        public = public_by_key.get(key)
        sub = subs_by_key.get(key)

        current_price: ProductPriceOut | None = None
        if sub is not None:
            try:
                version_doc = await _get_plan_version(
                    settings, sub["plan_version_id"], client=client
                )
            except CatalogError as exc:
                raise UpstreamUnavailableProblem(str(exc)) from exc
            if version_doc is not None:
                current_price = _price_out(
                    version_doc,
                    is_bespoke=version_doc.get("restricted_to_org_id") is not None,
                )

        history_rows = await selectors.pricing_history(directory_session, organization_id, key)

        products.append(
            ProductPricingOut(
                product_key=key,
                product_name=str((public or {}).get("product_name") or key),
                subscription=(
                    ProductSubscriptionOut(
                        id=uuid.UUID(str(sub["id"])),
                        state=str(sub["state"]),
                        plan_version_id=uuid.UUID(str(sub["plan_version_id"])),
                        trial_ends_at=sub.get("trial_ends_at"),
                        current_period_end=sub.get("current_period_end"),
                    )
                    if sub is not None
                    else None
                ),
                current_price=current_price,
                public_price=(
                    ProductPriceOut(
                        plan_version_id=uuid.UUID(str(public["plan_version_id"])),
                        amount_minor=int(public["amount_minor"]),
                        currency=str(public["currency"]),
                        billing_interval=str(public["billing_interval"]),
                        is_bespoke=False,
                    )
                    if public is not None
                    else None
                ),
                history=[_history_entry(row) for row in history_rows],
            )
        )
    return OrganizationPricingOut(organization_id=organization_id, products=products)


async def set_organization_pricing(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    payload: SetPricingIn,
    idempotency_key: str | None,
    operator: Principal,
    operator_token: str,
    directory_session: AsyncSession,
    app_session: AsyncSession,
    client: httpx.AsyncClient,
    request: Request | None,
) -> SetPricingOut:
    """Set one organization's price for one product (`POST .../pricing`) — clone-and-publish a
    bespoke plan version at catalog, optionally re-pin the live subscription, and record both acts
    on console's own audit trail. See the module docstring for the timing rule and the idempotency
    shape.
    """
    fingerprint = domain.pricing_request_fingerprint(
        organization_id=str(organization_id),
        product_key=payload.product_key,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        billing_interval=payload.billing_interval,
        apply_to_subscription=payload.apply_to_subscription,
    )

    if idempotency_key:
        existing = await selectors.find_pricing_change_by_idempotency_key(
            directory_session, organization_id, idempotency_key
        )
        if existing is not None:
            return _replay(existing, fingerprint)

    try:
        version_doc = await _ensure_bespoke_price(
            settings,
            organization_id=organization_id,
            product_key=payload.product_key,
            amount_minor=payload.amount_minor,
            currency=payload.currency,
            billing_interval=payload.billing_interval,
            operator_token=operator_token,
            client=client,
        )
    except CatalogError as exc:
        raise _catalog_problem(exc) from exc

    plan_version_id = uuid.UUID(str(version_doc["id"]))
    applied_to_subscription_id: uuid.UUID | None = None
    effective = "not_subscribed"

    if payload.apply_to_subscription:
        applied_to_subscription_id, effective = await _apply_to_subscription(
            settings,
            organization_id=organization_id,
            product_key=payload.product_key,
            plan_version_id=plan_version_id,
            operator=operator,
            client=client,
        )

    await services.record_bespoke_price(
        app_session,
        organization_id=organization_id,
        actor_id=operator.sub,
        operator_label=f"operator:{operator.sub}",
        product_key=payload.product_key,
        plan_version_id=plan_version_id,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        billing_interval=payload.billing_interval,
        applied_to_subscription_id=applied_to_subscription_id,
        effective=effective,
        reason=payload.reason,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        request=request,
    )

    return SetPricingOut(
        plan_version_id=plan_version_id,
        applied_to_subscription_id=applied_to_subscription_id,
        effective=effective,
    )


def _catalog_problem(exc: CatalogError) -> Exception:
    if exc.status_code == 404:
        return NotFoundProblem(str(exc))
    if exc.status_code == 409:
        return ConflictProblem(str(exc))
    if exc.status_code == 422:
        return ValidationProblem(str(exc))
    return UpstreamUnavailableProblem(str(exc))


def _replay(existing: AuditLog, fingerprint: str) -> SetPricingOut:
    after = existing.after or {}
    if after.get("request_fingerprint") != fingerprint:
        raise ConflictProblem("Idempotency-Key was already used for a different pricing request")
    applied = after.get("applied_to_subscription_id")
    return SetPricingOut(
        plan_version_id=uuid.UUID(str(existing.entity_id)),
        applied_to_subscription_id=uuid.UUID(str(applied)) if applied else None,
        effective=str(after.get("effective", "not_subscribed")),
    )


async def _apply_to_subscription(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    product_key: str,
    plan_version_id: uuid.UUID,
    operator: Principal,
    client: httpx.AsyncClient,
) -> tuple[uuid.UUID | None, str]:
    """Re-pin the organization's subscription for `product_key`, or report why not.

    Checks the subscription's *current* state for the target pin before calling `change_plan`
    again — the idempotent-replay path for a retried apply that already succeeded, and for the
    ordinary case where a downgrade to this exact price was already scheduled by an earlier call.
    """
    try:
        subs = await _list_subscriptions(settings, organization_id, client=client)
    except SubscriptionChangeError as exc:
        raise UpstreamUnavailableProblem(str(exc)) from exc

    sub = next((s for s in subs if s["product_key"] == product_key), None)
    if sub is None:
        return None, "not_subscribed"

    target = str(plan_version_id)
    subscription_id = uuid.UUID(str(sub["id"]))
    if str(sub.get("plan_version_id")) == target:
        return subscription_id, "now"
    if sub.get("pending_plan_version_id") and str(sub["pending_plan_version_id"]) == target:
        return subscription_id, "next_period"

    try:
        changed = await _change_plan(
            settings,
            organization_id=organization_id,
            subscription_id=subscription_id,
            plan_version_id=plan_version_id,
            operator_id=f"operator:{operator.sub}",
            client=client,
        )
    except SubscriptionChangeError as exc:
        if exc.status_code is not None and 400 <= exc.status_code < 500:
            # A business rule refused the re-pin — most likely a past_due/suspended/cancelled
            # subscription, which subscription's own `_change_plan` never allows to change plan
            # at all. The bespoke price still exists; only the live subscription is untouched.
            log.warning(
                "pricing.repin_refused",
                organization_id=str(organization_id),
                product_key=product_key,
                detail=str(exc),
            )
            return None, "not_subscribed"
        raise UpstreamUnavailableProblem(str(exc)) from exc

    if str(changed.get("plan_version_id")) == target:
        return subscription_id, "now"
    if changed.get("pending_plan_version_id") and str(changed["pending_plan_version_id"]) == target:
        return subscription_id, "next_period"
    return subscription_id, "now"  # pragma: no cover - defensive; _change_plan always sets one
