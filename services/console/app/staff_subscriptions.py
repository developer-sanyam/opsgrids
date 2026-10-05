"""Staff Add product and Extend trial — `.../add-product-options`, `POST .../subscriptions` and
`POST .../subscriptions/{product_key}/extend-trial` (ADR-0030).

The second and third staff subscription actions, built on everything the plan switch built
(ADR-0029) rather than beside it: the same `console.idempotency_keys` claim written before the
downstream call, the same coded refusals passed through with subscription's own sentence, the same
`outcome_unknown` and reconcile, the same `operator:<sub>` id, the same note-not-reason rule. Like
`plan_change.py`, this is network composition across catalog and subscription; console owns no
commercial truth and never writes another service's schema (ADR-0003 §2).

## Add product (owner and billing_ops)

* **A trial by default.** A paid add starts paying at once: subscription claims the first period
  for billing in the same transaction, and nothing — not subscription, not console — checks that
  the organization has a usable payment mandate first. A failed charge opens dunning. So a paid
  add is refused here (422 `charge_not_acknowledged`, before anything is claimed or called) unless
  the request says, in `acknowledge_immediate_charge`, that the operator was told.
* **One row per product, whatever its state.** Subscription refuses an existing row — `cancelled`
  included, since a cancelled subscription cannot be reopened yet — with 409 `already_subscribed`,
  and the picker never offers such a product. It also refuses a `one_time` version (never billed)
  and a currency other than the organization's live subscriptions' (`org_currency_mismatch`); the
  picker marks both up front.
* **Only what is on offer.** The confirm re-reads catalog's offerable set for the product and
  refuses any other version (`not_offerable`, recorded and replayed like any refusal; subscription
  is never called) — the plan switch's own rule (ADR-0029): subscription's pin lookup sees only
  that a version is published and whose it is, not that a newer one superseded it or its plan was
  retired. A catalogue outage is 503 `catalog_unavailable` and claims nothing.
* Bundles are sold here too, through the same operator subscribe; a bundle whose members the
  organization already pays for separately is marked, because both are billed with no credit —
  and so, the other way round, is a product a live bundle already entitles it to
  (`entitled_through`). Subscription accepts either; the picker says so before the confirm.

## Extend trial (owner only)

* **Absolute.** The request names the new end and the end the operator saw; subscription compares
  the second under its row lock. A retry can never add the time twice.
* Refused unless trialling (`not_trial`), while the billing run holds a claim on the conversion
  (`claim_in_flight`), when the new end is not later (`trial_not_extended`), or past
  `MAX_TOTAL_TRIAL_DAYS` from the trial's start (`trial_cap`) — subscription's rules, surfaced
  verbatim. The cap itself is read off subscription's listing (`trial_max_ends_at`), never restated
  here.

## The confirm — claim, call, record

Exactly `plan_change.switch_plan`'s contract: a *required* `Idempotency-Key`; the claim and an
intent audit row committed before the call; the call with no connection held; the status, the
replayable response and the outcome audit row in one transaction. A retry with the same key and body
replays; a different body is 409 `idempotency_key_reused`; a key in flight is 409
`request_in_progress`; an unknown outcome is 503 `outcome_unknown`, and a retry reconciles from a
live read before calling.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, plan_change, services
from app.catalog_client import CatalogError
from app.catalog_client import get_offerable_versions as _get_offerable_versions
from app.catalog_client import get_public_flat_plans as _get_public_flat_plans
from app.config import Settings
from app.models import IDEMPOTENCY_REFUSED, IDEMPOTENCY_SUCCEEDED, IDEMPOTENCY_UNKNOWN
from app.schemas import (
    AddProductCandidateOut,
    AddProductIn,
    AddProductOptionOut,
    AddProductOptionsOut,
    AddProductOut,
    ExistingSubscriptionOut,
    ExtendTrialIn,
    ExtendTrialOut,
    PlanOptionPriceOut,
)
from app.subscription_client import SubscriptionChangeError
from app.subscription_client import extend_trial as _extend_trial
from app.subscription_client import list_subscriptions as _list_subscriptions
from app.subscription_client import subscribe as _subscribe
from platform_core.auth import Principal
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    ProblemError,
    UpstreamUnavailableProblem,
)
from platform_core.logging import get_logger

log = get_logger("console.staff_subscriptions")

ADD_ACTION = "add_product"
EXTEND_ACTION = "extend_trial"

#: The audit actions each flow writes: an attempt, then exactly one outcome.
ADD_REQUESTED = "subscription.add_product.requested"
ADD_APPLIED = "subscription.add_product.applied"
ADD_REFUSED = "subscription.add_product.refused"
ADD_UNKNOWN = "subscription.add_product.outcome_unknown"
EXTEND_REQUESTED = "subscription.trial_extension.requested"
EXTEND_APPLIED = "subscription.trial_extension.applied"
EXTEND_REFUSED = "subscription.trial_extension.refused"
EXTEND_UNKNOWN = "subscription.trial_extension.outcome_unknown"

#: The states that still bill, or will again — what sets an organization's currency.
_LIVE = frozenset({"trial", "active", "past_due", "suspended"})

_IN_FLIGHT_DETAIL = "this request is already being processed; wait a moment and refresh the account"

# The refusal plumbing is the plan switch's own (`plan_change.py`), shared rather than restated:
# one mapping from subscription's answer to console's problem, one recorded shape, one replay.
_problem = plan_change._problem
_problem_dump = plan_change._problem_dump
_replay_problem = plan_change._replay_problem
_subscription_for = plan_change._subscription_for
_feature = plan_change._feature
#: The plan switch's own "only what is on offer now" rule (ADR-0029 review fix), applied to the add.
_offerable_ids = plan_change._offerable_ids
CODE_NOT_OFFERABLE = plan_change.CODE_NOT_OFFERABLE
NOT_OFFERABLE_DETAIL = plan_change.NOT_OFFERABLE_DETAIL


def _in_flight() -> ProblemError:
    return _problem(ConflictProblem, "request_in_progress", _IN_FLIGHT_DETAIL)


# ------------------------------------------------------------------------------- the picker


async def add_product_options(
    settings: Settings, *, organization_id: uuid.UUID, client: httpx.AsyncClient
) -> AddProductOptionsOut:
    """What the Add product dialog may offer: every product that publicly sells a plan and that
    this organization has **no row for in any state**, each with the versions catalog says this
    organization may take (public, or negotiated for it alone). A version the organization could
    not take is listed with the reason (`org_currency_mismatch`, `one_time`); subscription refuses
    it regardless."""
    try:
        subs = await _list_subscriptions(settings, organization_id, client=client)
        public = await _get_public_flat_plans(settings, client=client)
    except (SubscriptionChangeError, CatalogError) as exc:
        raise UpstreamUnavailableProblem(str(exc)) from exc

    existing = {str(s["product_key"]): str(s["state"]) for s in subs}
    live = [s for s in subs if str(s.get("state")) in _LIVE]
    live_currencies = sorted({str(s["currency"]).upper() for s in live})
    entitled_now = {str(p) for s in live for p in (s.get("entitled_products") or [])}

    names: dict[str, str] = {}
    for plan in public:
        names.setdefault(str(plan["product_key"]), str(plan.get("product_name") or ""))
    candidates = sorted(key for key in names if key not in existing)

    try:
        offered = await asyncio.gather(
            *(
                _get_offerable_versions(settings, organization_id, key, client=client)
                for key in candidates
            )
        )
    except CatalogError as exc:
        raise UpstreamUnavailableProblem(str(exc)) from exc

    products: list[AddProductCandidateOut] = []
    for key, versions in zip(candidates, offered, strict=True):
        options = [_option(v, live_currencies, entitled_now) for v in versions]
        if options:
            products.append(
                AddProductCandidateOut(
                    product_key=key,
                    product_name=names[key] or key,
                    # A candidate has no row of its own, so any live subscription that entitles it
                    # is a bundle covering it: adding it standalone bills both, with no credit.
                    entitled_through=sorted(
                        str(s["product_key"])
                        for s in live
                        if key in {str(p) for p in (s.get("entitled_products") or [])}
                    ),
                    versions=options,
                )
            )
    return AddProductOptionsOut(
        organization_id=organization_id,
        currency=live_currencies[0] if len(live_currencies) == 1 else None,
        existing=[
            ExistingSubscriptionOut(product_key=k, state=v) for k, v in sorted(existing.items())
        ],
        products=products,
    )


def _option(
    version: dict[str, Any], live_currencies: list[str], entitled_now: set[str]
) -> AddProductOptionOut:
    currency = str(version["currency"]).upper()
    blocked_code: str | None = None
    blocked_reason: str | None = None
    if version["billing_interval"] == "one_time":
        blocked_code = "one_time"
        blocked_reason = "a one-time plan has no billing period and would never be invoiced"
    elif live_currencies and currency not in live_currencies:
        blocked_code = "org_currency_mismatch"
        blocked_reason = (
            f"this organization is billed in {', '.join(live_currencies)}; this plan is in "
            f"{currency}"
        )
    bundled = _feature(version, "bundled_products")
    members = [str(m) for m in bundled] if isinstance(bundled, list) else None
    trial_days = _feature(version, "trial_days")
    return AddProductOptionOut(
        plan_id=uuid.UUID(str(version["plan_id"])),
        plan_name=str(version["plan_name"]),
        plan_version_id=uuid.UUID(str(version["plan_version_id"])),
        version_no=int(version["version_no"]),
        billing_interval=str(version["billing_interval"]),
        currency=currency,
        is_bespoke=version.get("restricted_to_org_id") is not None,
        recurring_minor=domain.recurring_list_price_minor(version.get("prices", [])),
        prices=[PlanOptionPriceOut.model_validate(p) for p in version.get("prices", [])],
        bundled_products=members,
        trial_days=trial_days if isinstance(trial_days, int) else None,
        offers_trial=not (isinstance(trial_days, int) and trial_days == 0),
        overlapping_products=[m for m in (members or []) if m in entitled_now],
        blocked_code=blocked_code,
        blocked_reason=blocked_reason,
    )


# ------------------------------------------------------------------------------ add product


def _added_out(opened: dict[str, Any], *, replayed: bool = False) -> AddProductOut:
    """What was opened, read off subscription's own answer — never assumed from the request."""
    state = str(opened["state"])
    return AddProductOut(
        subscription_id=uuid.UUID(str(opened["id"])),
        product_key=str(opened["product_key"]),
        state=state,
        plan_version_id=uuid.UUID(str(opened["plan_version_id"])),
        currency=str(opened["currency"]),
        trial_ends_at=opened.get("trial_ends_at"),
        current_period_start=opened.get("current_period_start"),
        current_period_end=opened.get("current_period_end"),
        entitled_products=list(opened.get("entitled_products") or [str(opened["product_key"])]),
        charge_claimed=state != "trial",
        replayed=replayed,
    )


def _add_landed(
    subs: list[dict[str, Any]], product_key: str, target: uuid.UUID, *, trial: bool
) -> dict[str, Any] | None:
    """Did an earlier attempt whose answer was lost open the subscription? A row for the product,
    pinned to the target, trialling exactly when a trial was asked for. Otherwise there is no sign
    it did, and the call is made — which subscription answers with 409 `already_subscribed` if
    some other row is there."""
    sub = next((s for s in subs if s.get("product_key") == product_key), None)
    if sub is None or str(sub.get("plan_version_id")) != str(target):
        return None
    return sub if (str(sub.get("state")) == "trial") == trial else None


async def add_product(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    payload: AddProductIn,
    idempotency_key: str,
    operator: Principal,
    app_session: AsyncSession,
    client: httpx.AsyncClient,
    request: Request | None,
) -> AddProductOut:
    """Open the subscription: acknowledge, claim, call, record. See the module docstring."""
    if not payload.trial and not payload.acknowledge_immediate_charge:
        raise _problem(
            BusinessRuleProblem,
            "charge_not_acknowledged",
            "A paid subscription starts billing now: its first period is claimed for payment at "
            "once, and no payment mandate is checked first. Confirm that before adding it, or add "
            "it as a trial.",
        )
    label = plan_change.operator_label(operator)
    target = payload.plan_version_id
    # The version must be one catalog offers this organization *now* — the picker's own set, read
    # again rather than trusted from a picker loaded earlier (a newer version may have superseded
    # it, or its plan been retired; a direct call may name any published id of the product). Read
    # before the claim, so a catalogue outage claims nothing; enforced after the replay branches, so
    # a request that already succeeded still replays as a success after the catalogue moves on.
    offered = await _offerable_ids(settings, organization_id, payload.product_key, client=client)
    fingerprint = domain.add_product_fingerprint(
        organization_id=str(organization_id),
        product_key=payload.product_key,
        plan_version_id=str(target),
        trial=payload.trial,
    )
    claim = await services.claim_idempotency_key(
        app_session,
        organization_id=organization_id,
        key=idempotency_key,
        action=ADD_ACTION,
        fingerprint=fingerprint,
        actor_id=operator.sub,
        operator_label=label,
        intent_action=ADD_REQUESTED,
        intent_entity="subscription",
        intent_entity_id=None,
        intent_after={
            "product_key": payload.product_key,
            "plan_version_id": str(target),
            "trial": payload.trial,
            "acknowledged_immediate_charge": payload.acknowledge_immediate_charge,
        },
        reason=payload.reason,
        request=request,
    )

    if claim.outcome == "succeeded":
        return AddProductOut.model_validate({**(claim.response or {}), "replayed": True})
    if claim.outcome == "refused":
        raise _replay_problem(claim.response)
    if claim.outcome == "in_flight":
        raise _in_flight()

    async def _record_reconciled(landed: dict[str, Any]) -> AddProductOut:
        out = _added_out(landed, replayed=True)
        await _record(
            app_session,
            organization_id=organization_id,
            key=idempotency_key,
            status=IDEMPOTENCY_SUCCEEDED,
            response=out.model_dump(mode="json", exclude={"replayed"}),
            operator=operator,
            entity_id=out.subscription_id,
            action=ADD_APPLIED,
            after={**out.model_dump(mode="json", exclude={"replayed"}), "reconciled": True},
            reason=payload.reason,
            request=request,
        )
        return out

    async def _landed_now() -> dict[str, Any] | None:
        try:
            subs = await _list_subscriptions(settings, organization_id, client=client)
        except SubscriptionChangeError as exc:
            raise UpstreamUnavailableProblem(str(exc)) from exc
        return _add_landed(subs, payload.product_key, target, trial=payload.trial)

    reconciling = claim.outcome == "reconcile"
    if reconciling:
        assert claim.seen_updated_at is not None
        landed = await _landed_now()
        if landed is not None:
            return await _record_reconciled(landed)
        if not await services.retake_idempotency_key(
            app_session,
            organization_id=organization_id,
            key=idempotency_key,
            seen_updated_at=claim.seen_updated_at,
        ):
            raise _in_flight()

    if str(target) not in offered:
        # Definitive, and recorded like any other refusal: the claim replays it, and the trail
        # reads requested, then refused with the code that says why. Subscription is never called.
        problem = _problem(BusinessRuleProblem, CODE_NOT_OFFERABLE, NOT_OFFERABLE_DETAIL)
        log.warning(
            "add_product.not_offerable",
            organization_id=str(organization_id),
            product_key=payload.product_key,
            plan_version_id=str(target),
        )
        await _record(
            app_session,
            organization_id=organization_id,
            key=idempotency_key,
            status=IDEMPOTENCY_REFUSED,
            response=_problem_dump(problem),
            operator=operator,
            entity_id=None,
            action=ADD_REFUSED,
            after={"status": problem.status_code, "code": CODE_NOT_OFFERABLE},
            reason=payload.reason,
            request=request,
        )
        raise problem

    try:
        opened = await _subscribe(
            settings,
            organization_id=organization_id,
            product_key=payload.product_key,
            plan_version_id=target,
            trial=payload.trial,
            operator_id=label,
            note=payload.reason,
            client=client,
        )
    except SubscriptionChangeError as exc:
        reread_failed = False
        if reconciling and exc.status_code == 409 and exc.code == "already_subscribed":
            # The attempt whose answer was lost may only have been late: it can commit between the
            # live read above and this call, which then finds the row it made. Look again before
            # calling that a refusal, or the key would replay "already has it" for this very add.
            # A look that fails tells neither apart, so the outcome stays unknown (the plan
            # switch's own rule) and the next retry with the key reconciles.
            try:
                late = await _landed_now()
            except UpstreamUnavailableProblem:
                late, reread_failed = None, True
            if late is not None:
                return await _record_reconciled(late)
        raise await _record_failure(
            app_session,
            exc,
            organization_id=organization_id,
            key=idempotency_key,
            operator=operator,
            entity_id=None,
            refused_action=ADD_REFUSED,
            unknown_action=ADD_UNKNOWN,
            reason=payload.reason,
            request=request,
            reread_failed=reread_failed,
        ) from exc

    out = _added_out(opened)
    await _record(
        app_session,
        organization_id=organization_id,
        key=idempotency_key,
        status=IDEMPOTENCY_SUCCEEDED,
        response=out.model_dump(mode="json", exclude={"replayed"}),
        operator=operator,
        entity_id=out.subscription_id,
        action=ADD_APPLIED,
        after=out.model_dump(mode="json", exclude={"replayed"}),
        reason=payload.reason,
        request=request,
    )
    log.info(
        "add_product.applied",
        organization_id=str(organization_id),
        subscription_id=str(out.subscription_id),
        product_key=payload.product_key,
        operator=label,
        state=out.state,
        charge_claimed=out.charge_claimed,
    )
    return out


# ----------------------------------------------------------------------------- extend trial


def _instant(value: Any) -> datetime | None:
    if not value:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


async def extend_trial(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    product_key: str,
    payload: ExtendTrialIn,
    idempotency_key: str,
    operator: Principal,
    app_session: AsyncSession,
    client: httpx.AsyncClient,
    request: Request | None,
) -> ExtendTrialOut:
    """Move the trial's end: claim, call, record. The subscription is resolved server-side from
    `(organization, product_key)`; the body never names it."""
    sub = await _subscription_for(settings, organization_id, product_key, client=client)
    subscription_id = uuid.UUID(str(sub["id"]))
    label = plan_change.operator_label(operator)
    new_end = payload.new_trial_ends_at
    expected = payload.expected_trial_ends_at

    fingerprint = domain.extend_trial_fingerprint(
        organization_id=str(organization_id),
        product_key=product_key,
        subscription_id=str(subscription_id),
        new_trial_ends_at=new_end,
        expected_trial_ends_at=expected,
    )
    claim = await services.claim_idempotency_key(
        app_session,
        organization_id=organization_id,
        key=idempotency_key,
        action=EXTEND_ACTION,
        fingerprint=fingerprint,
        actor_id=operator.sub,
        operator_label=label,
        intent_action=EXTEND_REQUESTED,
        intent_entity="subscription",
        intent_entity_id=subscription_id,
        intent_after={
            "product_key": product_key,
            "new_trial_ends_at": new_end.isoformat(),
            "expected_trial_ends_at": expected.isoformat(),
        },
        reason=payload.reason,
        request=request,
    )

    if claim.outcome == "succeeded":
        return ExtendTrialOut.model_validate({**(claim.response or {}), "replayed": True})
    if claim.outcome == "refused":
        raise _replay_problem(claim.response)
    if claim.outcome == "in_flight":
        raise _in_flight()

    def _extended_to_target(live: dict[str, Any]) -> bool:
        """The trial already ends where it was asked to: the earlier attempt landed."""
        live_end = _instant(live.get("trial_ends_at"))
        return str(live.get("state")) == "trial" and live_end is not None and live_end == new_end

    async def _record_reconciled(live: dict[str, Any]) -> ExtendTrialOut:
        out = _extended_out(live, product_key, previous=expected, replayed=True)
        await _record(
            app_session,
            organization_id=organization_id,
            key=idempotency_key,
            status=IDEMPOTENCY_SUCCEEDED,
            response=out.model_dump(mode="json", exclude={"replayed"}),
            operator=operator,
            entity_id=subscription_id,
            action=EXTEND_APPLIED,
            after={**out.model_dump(mode="json", exclude={"replayed"}), "reconciled": True},
            reason=payload.reason,
            request=request,
        )
        return out

    reconciling = claim.outcome == "reconcile"
    if reconciling:
        assert claim.seen_updated_at is not None
        if _extended_to_target(sub):
            return await _record_reconciled(sub)
        if not await services.retake_idempotency_key(
            app_session,
            organization_id=organization_id,
            key=idempotency_key,
            seen_updated_at=claim.seen_updated_at,
        ):
            raise _in_flight()

    try:
        extended = await _extend_trial(
            settings,
            organization_id=organization_id,
            subscription_id=subscription_id,
            new_trial_ends_at=new_end.isoformat(),
            expected_trial_ends_at=expected.isoformat(),
            operator_id=label,
            note=payload.reason,
            client=client,
        )
    except SubscriptionChangeError as exc:
        reread_failed = False
        if reconciling and exc.status_code == 409 and exc.code == "state_changed":
            # A late first attempt can commit between the live read above and this call, which
            # then finds the trial end moved — by that very extension. Look again before calling it
            # a refusal, or the key would replay "it changed" for the change that was made. A look
            # that fails tells neither apart, so the outcome stays unknown (the plan switch's own
            # rule) and the next retry with the key reconciles.
            try:
                live = await _subscription_for(
                    settings, organization_id, product_key, client=client
                )
            except ProblemError:
                live, reread_failed = None, True
            if live is not None and _extended_to_target(live):
                return await _record_reconciled(live)
        raise await _record_failure(
            app_session,
            exc,
            organization_id=organization_id,
            key=idempotency_key,
            operator=operator,
            entity_id=subscription_id,
            refused_action=EXTEND_REFUSED,
            unknown_action=EXTEND_UNKNOWN,
            reason=payload.reason,
            request=request,
            reread_failed=reread_failed,
        ) from exc

    out = _extended_out(extended, product_key, previous=expected)
    await _record(
        app_session,
        organization_id=organization_id,
        key=idempotency_key,
        status=IDEMPOTENCY_SUCCEEDED,
        response=out.model_dump(mode="json", exclude={"replayed"}),
        operator=operator,
        entity_id=subscription_id,
        action=EXTEND_APPLIED,
        after=out.model_dump(mode="json", exclude={"replayed"}),
        reason=payload.reason,
        request=request,
    )
    log.info(
        "extend_trial.applied",
        organization_id=str(organization_id),
        subscription_id=str(subscription_id),
        operator=label,
        trial_ends_at=out.trial_ends_at.isoformat() if out.trial_ends_at else None,
    )
    return out


def _extended_out(
    sub: dict[str, Any], product_key: str, *, previous: datetime, replayed: bool = False
) -> ExtendTrialOut:
    return ExtendTrialOut(
        subscription_id=uuid.UUID(str(sub["id"])),
        product_key=product_key,
        state=str(sub["state"]),
        trial_ends_at=sub.get("trial_ends_at"),
        previous_trial_ends_at=previous,
        replayed=replayed,
    )


# --------------------------------------------------------------------------------- recording


async def _record_failure(
    session: AsyncSession,
    exc: SubscriptionChangeError,
    *,
    organization_id: uuid.UUID,
    key: str,
    operator: Principal,
    entity_id: uuid.UUID | None,
    refused_action: str,
    unknown_action: str,
    reason: str,
    request: Request | None,
    reread_failed: bool = False,
) -> ProblemError:
    """Record the failure and return the problem to raise.

    A definitive refusal (404/409/422) is recorded `refused` with its replayable problem; any other
    failure leaves the outcome `unknown`, which a retry with the same key reconciles. So does a
    reconcile's 409 whose confirming re-read failed (`reread_failed`): that 409 may be the lost
    attempt's own late landing, so it is not definitive — `plan_change.switch_plan`'s rule."""
    definitive = exc.status_code in plan_change.DEFINITIVE_REFUSALS and not reread_failed
    problem = plan_change.problem_for(
        SubscriptionChangeError("re-read failed") if reread_failed else exc
    )
    log.warning(
        "staff_subscription.not_applied" if definitive else "staff_subscription.outcome_unknown",
        organization_id=str(organization_id),
        action=refused_action,
        status=exc.status_code,
        code=exc.code,
        reread_failed=reread_failed,
    )
    after: dict[str, Any] = {
        "status": exc.status_code,
        "code": exc.code or _problem_dump(problem)["code"],
    }
    if reread_failed:
        after["reread_failed"] = True
    await _record(
        session,
        organization_id=organization_id,
        key=key,
        status=IDEMPOTENCY_REFUSED if definitive else IDEMPOTENCY_UNKNOWN,
        response=_problem_dump(problem) if definitive else None,
        operator=operator,
        entity_id=entity_id,
        action=refused_action if definitive else unknown_action,
        after=after,
        reason=reason,
        request=request,
    )
    return problem


async def _record(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    key: str,
    status: str,
    response: dict[str, Any] | None,
    operator: Principal,
    entity_id: uuid.UUID | None,
    action: str,
    after: dict[str, Any],
    reason: str,
    request: Request | None,
) -> None:
    await services.record_idempotency_outcome(
        session,
        organization_id=organization_id,
        key=key,
        status=status,
        response=response,
        actor_id=operator.sub,
        operator_label=plan_change.operator_label(operator),
        outcome_action=action,
        entity="subscription",
        entity_id=entity_id,
        outcome_after=after,
        reason=reason,
        request=request,
    )
