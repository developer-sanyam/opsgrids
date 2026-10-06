"""Staff plan switching — the picker, the preview and the confirm (`.../plan-options`,
`.../plan-change:preview`, `.../plan-change`).

Network composition across catalog and subscription, kept out of `selectors.py`/`services.py` for
the same reason `pricing.py` is: console owns no commercial truth and never writes another
service's schema (ADR-0003 §2). The one thing it owns here is the **claim** of a staff action
(`services.claim_idempotency_key`), and the audit rows around it.

## What staff may and may not do (ADR-0029)

* A **trial** switches immediately and free.
* A **paying** subscription switches at its **next billing period**. Staff cannot force "now,
  prorated": that would charge a card for a plan the customer did not choose, move entitlement
  before payment, and has no preview or mandate check behind it. Goodwill for a customer who
  should get the new plan sooner is the existing comp-credit action.
* An **equal-price** paid change applies now at no charge — the *server* computes the direction,
  and this module never decides it: the UI shows what the preview says.
* So `timing` is `auto` or `next_period` and the two mean the same thing; both reach subscription
  as `timing="next_period"`, which is a no-op guard on a trial (nothing to defer) and the rule on a
  paid change. There is no immediate-proration and no immediate-downgrade path to ask for.

## The flow of a confirm — claim, call, record (docs/io-outside-transactions.md)

1. **Claim.** `Idempotency-Key` is *required*. A row keyed `(organization, key)` and an *intent*
   audit row are committed before anything is called. Of two concurrent requests with one key,
   exactly one inserts; the other is told `request_in_progress` (409).
2. **Call.** Subscription's change-plan, carrying the operator's reason as `note` and the state
   they confirmed against as `expected` (compared under subscription's row lock: a mismatch is
   409 `state_changed`). No database connection is held while it runs.
3. **Record.** The claim's status and replayable response, and the outcome audit row, in one
   transaction.

A retry with the same key and body **replays** the recorded result — success or refusal — without
a second call. The same key with a different body is a 409 `idempotency_key_reused`.

**Outcome unknown.** A timeout, a transport failure or a 5xx after the request left means the
change may or may not have landed. The claim is marked `unknown`, the operator gets a 503 saying
exactly that, and a retry with the same key *reconciles*: it reads the live subscription, and if
the target is already pinned or scheduled it records and returns that result; if not, it makes the
call (the expected-state guard keeps a late first attempt from applying twice).

**Reason, not the transition's reason.** The operator's words are stored as the subscription
audit row's `reason` (subscription's `note`) and never on the event — the event is the customer
org's, and its own notification templates can render any payload key. The transition's own
`reason` stays a short machine code. The reason is excluded from the idempotency fingerprint, so
rephrasing it on a retry is still a replay.

**Only what is on offer.** The picker lists what catalog offers this organization now; the preview
and the confirm re-read that set and refuse any other target (`not_offerable`) — a picker loaded
before a newer version was published, the version itself was withdrawn on the Plans page
(ADR-0030) or the plan was retired, or a direct call with any published id of the product, must
not pin a price the catalogue has withdrawn (a UI gate hides a control; it does not protect data).
The one exception is withdrawing a scheduled change, which moves back to the pin the subscription
is already on.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, services
from app.catalog_client import CatalogError
from app.catalog_client import get_offerable_versions as _get_offerable_versions
from app.catalog_client import get_plan_version as _get_plan_version
from app.config import Settings
from app.models import (
    IDEMPOTENCY_REFUSED,
    IDEMPOTENCY_SUCCEEDED,
    IDEMPOTENCY_UNKNOWN,
)
from app.schemas import (
    ExpectedStateIn,
    PlanChangeIn,
    PlanChangeOut,
    PlanChangePreviewIn,
    PlanChangePreviewOut,
    PlanOptionOut,
    PlanOptionPriceOut,
    PlanOptionsOut,
    PlanOptionsSubscriptionOut,
)
from app.subscription_client import SubscriptionChangeError
from app.subscription_client import change_plan as _change_plan
from app.subscription_client import list_subscriptions as _list_subscriptions
from app.subscription_client import preview_change_plan as _preview_change_plan
from platform_core.auth import Principal
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    NotFoundProblem,
    ProblemError,
    UpstreamUnavailableProblem,
)
from platform_core.logging import get_logger

log = get_logger("console.plan_change")

ACTION = "plan_change"

#: The audit actions the flow writes, in order: an attempt, then exactly one of the outcomes.
AUDIT_REQUESTED = "subscription.plan_change.requested"
AUDIT_APPLIED = "subscription.plan_change.applied"
AUDIT_REFUSED = "subscription.plan_change.refused"
AUDIT_UNKNOWN = "subscription.plan_change.outcome_unknown"

#: Subscription answers that are definitive "no, and a retry cannot change that": recorded as
#: `refused` and replayed to a retry with the same key. Anything else (a transport failure, a 5xx,
#: a refused credential) leaves the outcome unknown — or, for a credential, at least un-final — so
#: a retry with the same key reconciles and tries again.
DEFINITIVE_REFUSALS = frozenset({404, 409, 422})

#: Subscription states that may change plan at all (mirrors subscription's own rule; the picker
#: uses it only to say so up front — subscription's preview and write are the authority).
CHANGEABLE_STATES = frozenset({"trial", "active"})

#: The target is not in the set catalog offers this organization now: a newer version of its plan
#: superseded it, the version was withdrawn, or the plan was retired. Subscription cannot tell the
#: first and the last apart from an offerable pin (its pin lookup sees only that the version is
#: published and whose it is), so console applies the picker's own filter again. A withdrawn
#: version is no longer published, so subscription would refuse it too; this answers first.
CODE_NOT_OFFERABLE = "not_offerable"
NOT_OFFERABLE_DETAIL = (
    "that plan version is no longer on offer for this organization: a newer version has replaced "
    "it, it was withdrawn, or its plan was retired. Choose again from the plans on offer now."
)


def operator_label(operator: Principal) -> str:
    """The one format for an operator on a staff action: `operator:<sub>`. (The suspend/reactivate
    delegation predates it and sends the bare `sub`; new flows use this one.)"""
    return f"operator:{operator.sub}"


def _problem(cls: type[ProblemError], code: str, detail: str) -> ProblemError:
    return cls(detail, errors=[{"code": code, "message": detail}])


def problem_for(exc: SubscriptionChangeError) -> ProblemError:
    """subscription's refusal as console's own problem, code and detail preserved.

    404 (no such subscription or version) and the coded 409/422 refusals pass through as what they
    are. A refused service credential is console's own misconfiguration, never the operator's fault
    and never a 401/403 to them. Everything else is an unreachable or failing upstream — and for a
    *write* that means the outcome is unknown, which is said plainly (`outcome_unknown`).
    """
    status = exc.status_code
    if status == 404:
        return _problem(NotFoundProblem, exc.code or "not_found", str(exc))
    if status == 409:
        return _problem(ConflictProblem, exc.code or "conflict", str(exc))
    if status == 422:
        return _problem(BusinessRuleProblem, exc.code or "refused", str(exc))
    if status in (401, 403):
        return _problem(
            UpstreamUnavailableProblem,
            "upstream_refused",
            "subscription refused console's own service credentials; the change was not made",
        )
    return _problem(
        UpstreamUnavailableProblem,
        "outcome_unknown",
        "subscription did not answer, so the change may or may not have been made. Refresh this "
        "account to see where it stands; repeating the same request (same Idempotency-Key) "
        "is safe.",
    )


def preview_problem_for(exc: SubscriptionChangeError) -> ProblemError:
    """subscription's failure to *preview* as console's problem. Not `problem_for`: a preview is a
    read with no Idempotency-Key, so "the change may or may not have been made" would send an
    operator checking for a change nobody attempted. 404 and a coded 409/422 pass through; anything
    else says plainly that nothing was changed."""
    status = exc.status_code
    if status in (404, 409, 422):
        return problem_for(exc)
    if status in (401, 403):
        return _problem(
            UpstreamUnavailableProblem,
            "upstream_refused",
            "subscription refused console's own service credentials, so this change could not be "
            "previewed; nothing was changed",
        )
    return _problem(
        UpstreamUnavailableProblem,
        "preview_unavailable",
        "could not ask subscription what this change would do; nothing was changed. Try again "
        "shortly.",
    )


def _problem_dump(problem: ProblemError) -> dict[str, Any]:
    """What is recorded on a `refused` claim so a retry replays the same refusal."""
    errors = problem.errors or []
    return {
        "status": problem.status_code,
        "detail": problem.detail,
        "code": errors[0].get("code") if errors else None,
    }


def _replay_problem(recorded: dict[str, Any] | None) -> ProblemError:
    recorded = recorded or {}
    status = int(recorded.get("status") or 422)
    cls: type[ProblemError] = {
        404: NotFoundProblem,
        409: ConflictProblem,
        422: BusinessRuleProblem,
    }.get(status, UpstreamUnavailableProblem)
    return _problem(cls, str(recorded.get("code") or "refused"), str(recorded.get("detail") or ""))


async def _subscription_for(
    settings: Settings, organization_id: uuid.UUID, product_key: str, *, client: httpx.AsyncClient
) -> dict[str, Any]:
    """The organization's subscription for `product_key`, resolved server-side — the path names
    `(organization, product)`, never a subscription id, so there is nothing to point at another
    organization's row. 404 when there is none."""
    try:
        subs = await _list_subscriptions(settings, organization_id, client=client)
    except SubscriptionChangeError as exc:
        raise UpstreamUnavailableProblem(str(exc)) from exc
    sub = next((s for s in subs if s.get("product_key") == product_key), None)
    if sub is None:
        raise _problem(
            NotFoundProblem,
            "no_subscription",
            f"This organization has no subscription for {product_key!r}",
        )
    return sub


async def _offerable_ids(
    settings: Settings, organization_id: uuid.UUID, product_key: str, *, client: httpx.AsyncClient
) -> frozenset[str]:
    """The version ids catalog offers this organization for `product_key` right now — the
    picker's own set (audience, active plan, newest version per audience, cadence and currency),
    re-read for every preview and confirm rather than trusted from a picker loaded earlier."""
    try:
        offered = await _get_offerable_versions(
            settings, organization_id, product_key, client=client
        )
    except CatalogError as exc:
        raise _problem(
            UpstreamUnavailableProblem,
            "catalog_unavailable",
            "could not check this plan against the catalogue, so this request changed nothing. "
            "Try again shortly.",
        ) from exc
    return frozenset(str(v["plan_version_id"]) for v in offered)


# --------------------------------------------------------------------------------- the picker


def _feature(version: dict[str, Any], key: str) -> Any:
    return next(
        (f.get("value") for f in version.get("features", []) if f.get("feature_key") == key), None
    )


async def plan_options(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    product_key: str,
    client: httpx.AsyncClient,
) -> PlanOptionsOut:
    """Everything the Change plan dialog's first step needs, from live reads: the subscription as it
    is now, and the versions catalog says this organization may be moved onto — each marked with
    whether this subscription could actually take it (one currency and one cadence per
    subscription). The picker's amounts are display; subscription's preview decides direction."""
    sub = await _subscription_for(settings, organization_id, product_key, client=client)
    current_id = uuid.UUID(str(sub["plan_version_id"]))
    pending_id = (
        uuid.UUID(str(sub["pending_plan_version_id"]))
        if sub.get("pending_plan_version_id")
        else None
    )
    try:
        offered = await _get_offerable_versions(
            settings, organization_id, product_key, client=client
        )
        current_doc = await _get_plan_version(settings, current_id, client=client)
        pending_doc = (
            await _get_plan_version(settings, pending_id, client=client) if pending_id else None
        )
    except CatalogError as exc:
        raise UpstreamUnavailableProblem(str(exc)) from exc

    currency = str(sub["currency"]).upper()
    interval = current_doc.get("billing_interval") if current_doc else None
    state = str(sub["state"])

    options: list[PlanOptionOut] = []
    for version in offered:
        version_id = uuid.UUID(str(version["plan_version_id"]))
        blocked_code: str | None = None
        blocked_reason: str | None = None
        if str(version["currency"]).upper() != currency:
            blocked_code = "currency"
            blocked_reason = (
                f"this subscription is billed in {currency}; this plan is in "
                f"{str(version['currency']).upper()}"
            )
        elif version["billing_interval"] == "one_time" or (
            interval is not None and version["billing_interval"] != interval
        ):
            blocked_code = "cadence"
            renews = f"every {interval}" if interval else "on its own cadence"
            blocked_reason = (
                f"this subscription renews {renews}; a {version['billing_interval']} plan "
                "cannot replace it mid-subscription"
            )
        bundled = _feature(version, "bundled_products")
        trial_days = _feature(version, "trial_days")
        options.append(
            PlanOptionOut(
                plan_id=uuid.UUID(str(version["plan_id"])),
                plan_name=str(version["plan_name"]),
                plan_version_id=version_id,
                version_no=int(version["version_no"]),
                billing_interval=str(version["billing_interval"]),
                currency=str(version["currency"]).upper(),
                is_bespoke=version.get("restricted_to_org_id") is not None,
                recurring_minor=domain.recurring_list_price_minor(version.get("prices", [])),
                prices=[PlanOptionPriceOut.model_validate(p) for p in version.get("prices", [])],
                bundled_products=[str(m) for m in bundled] if isinstance(bundled, list) else None,
                trial_days=trial_days if isinstance(trial_days, int) else None,
                is_current=version_id == current_id,
                is_pending=version_id == pending_id,
                blocked_code=blocked_code,
                blocked_reason=blocked_reason,
            )
        )

    return PlanOptionsOut(
        organization_id=organization_id,
        product_key=product_key,
        subscription=PlanOptionsSubscriptionOut(
            id=uuid.UUID(str(sub["id"])),
            state=state,
            plan_version_id=current_id,
            plan_name=current_doc.get("plan_name") if current_doc else None,
            pending_plan_version_id=pending_id,
            pending_plan_name=pending_doc.get("plan_name") if pending_doc else None,
            currency=currency,
            billing_interval=interval,
            trial_ends_at=sub.get("trial_ends_at"),
            current_period_end=sub.get("current_period_end"),
            change_blocked_code=None if state in CHANGEABLE_STATES else "state",
        ),
        options=options,
    )


# --------------------------------------------------------------------------------- the preview


async def preview(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    product_key: str,
    payload: PlanChangePreviewIn,
    client: httpx.AsyncClient,
) -> PlanChangePreviewOut:
    """What the change would do, as subscription itself computes it. Read-only: no claim, no audit
    row, nothing written anywhere. A refusal is part of the answer (`refusal_code`); only a missing
    subscription or version (404) and an unreachable upstream (503) are errors.

    Subscription's own refusals come first (a past_due subscription cannot change at all, whatever
    the target); a target it would accept but catalog no longer offers this organization is then
    refused as `not_offerable` — the same rule the confirm enforces."""
    sub = await _subscription_for(settings, organization_id, product_key, client=client)
    try:
        answer = await _preview_change_plan(
            settings,
            organization_id=organization_id,
            subscription_id=uuid.UUID(str(sub["id"])),
            plan_version_id=payload.plan_version_id,
            client=client,
        )
    except SubscriptionChangeError as exc:
        raise preview_problem_for(exc) from exc
    out = PlanChangePreviewOut.model_validate(answer)
    if out.refusal_code is None and out.action != "withdraw_scheduled":
        offered = await _offerable_ids(settings, organization_id, product_key, client=client)
        if str(payload.plan_version_id) not in offered:
            return PlanChangePreviewOut(
                expected=out.expected,
                refusal_code=CODE_NOT_OFFERABLE,
                refusal_detail=NOT_OFFERABLE_DETAIL,
            )
    return out


# ---------------------------------------------------------------------------------- the confirm


def _out_from(
    changed: dict[str, Any], target: uuid.UUID, *, replayed: bool = False
) -> PlanChangeOut:
    """What happened, read off subscription's own response — never assumed from what was asked.

    Pinned to the target now: `now`. Waiting for the target: `next_period`, landing at the end of
    the current period."""
    pending = changed.get("pending_plan_version_id")
    scheduled = bool(pending) and str(pending) == str(target)
    return PlanChangeOut(
        effective="next_period" if scheduled else "now",
        state=str(changed["state"]),
        plan_version_id=uuid.UUID(str(changed["plan_version_id"])),
        pending_plan_version_id=uuid.UUID(str(pending)) if pending else None,
        effective_at=changed.get("current_period_end") if scheduled else None,
        replayed=replayed,
    )


def _landed(
    live: dict[str, Any], target: uuid.UUID, expected: ExpectedStateIn
) -> dict[str, Any] | None:
    """Did an earlier attempt whose outcome was lost already land? Read off the live subscription.

    Pinned to the target when it was not before: applied now. Scheduled for the target: applied for
    the next period. A withdrawal (the target is the pin it was already on) landed when the
    waiting change that was there is gone. Otherwise there is no sign it did, and the caller makes
    the call — which `expected` makes safe if the first attempt turns out to be merely late."""
    live_pin = str(live.get("plan_version_id"))
    live_pending = live.get("pending_plan_version_id")
    if str(target) == str(expected.plan_version_id):
        withdrew = expected.pending_plan_version_id is not None and not live_pending
        return live if withdrew and live_pin == str(target) else None
    if live_pin == str(target) or (live_pending and str(live_pending) == str(target)):
        return live
    return None


async def switch_plan(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    product_key: str,
    payload: PlanChangeIn,
    idempotency_key: str,
    operator: Principal,
    app_session: AsyncSession,
    client: httpx.AsyncClient,
    request: Request | None,
) -> PlanChangeOut:
    """Make the change: claim, call, record. See the module docstring for the whole contract."""
    sub = await _subscription_for(settings, organization_id, product_key, client=client)
    subscription_id = uuid.UUID(str(sub["id"]))
    label = operator_label(operator)
    expected = payload.expected
    target = payload.plan_version_id
    # A withdrawal moves back to the pin the subscription is on — often a superseded version, and
    # never a new price — so it is the one target that need not be on offer. Everything else must
    # be in catalog's offerable set *now*. Read before the claim, so a catalogue outage claims
    # nothing; enforced after the replay branches, so a request that already succeeded still
    # replays as a success after the catalogue moves on.
    withdrawing = str(target) in {str(expected.plan_version_id), str(sub["plan_version_id"])}
    offered = (
        None
        if withdrawing
        else await _offerable_ids(settings, organization_id, product_key, client=client)
    )

    fingerprint = domain.plan_change_fingerprint(
        organization_id=str(organization_id),
        product_key=product_key,
        subscription_id=str(subscription_id),
        plan_version_id=str(target),
        expected_state=expected.state,
        expected_plan_version_id=str(expected.plan_version_id),
        expected_pending_plan_version_id=(
            str(expected.pending_plan_version_id) if expected.pending_plan_version_id else None
        ),
    )
    claim = await services.claim_idempotency_key(
        app_session,
        organization_id=organization_id,
        key=idempotency_key,
        action=ACTION,
        fingerprint=fingerprint,
        actor_id=operator.sub,
        operator_label=label,
        intent_action=AUDIT_REQUESTED,
        intent_entity="subscription",
        intent_entity_id=subscription_id,
        intent_after={
            "product_key": product_key,
            "plan_version_id": str(target),
            "timing": payload.timing,
            "expected": expected.model_dump(mode="json"),
        },
        reason=payload.reason,
        request=request,
    )

    if claim.outcome == "succeeded":
        return PlanChangeOut.model_validate({**(claim.response or {}), "replayed": True})
    if claim.outcome == "refused":
        raise _replay_problem(claim.response)
    if claim.outcome == "in_flight":
        raise _problem(
            ConflictProblem,
            "request_in_progress",
            "this request is already being processed; wait a moment and refresh the account",
        )

    async def _record_reconciled(landed: dict[str, Any]) -> PlanChangeOut:
        out = _out_from(landed, target, replayed=True)
        await _record(
            app_session,
            organization_id=organization_id,
            key=idempotency_key,
            status=IDEMPOTENCY_SUCCEEDED,
            response=out.model_dump(mode="json", exclude={"replayed"}),
            operator=operator,
            subscription_id=subscription_id,
            action=AUDIT_APPLIED,
            after={**out.model_dump(mode="json", exclude={"replayed"}), "reconciled": True},
            reason=payload.reason,
            request=request,
        )
        return out

    reconciling = claim.outcome == "reconcile"
    if reconciling:
        assert claim.seen_updated_at is not None
        landed = _landed(sub, target, expected)
        if landed is not None:
            return await _record_reconciled(landed)
        if not await services.retake_idempotency_key(
            app_session,
            organization_id=organization_id,
            key=idempotency_key,
            seen_updated_at=claim.seen_updated_at,
        ):
            raise _problem(
                ConflictProblem,
                "request_in_progress",
                "this request is already being processed; wait a moment and refresh the account",
            )

    if offered is not None and str(target) not in offered:
        # Definitive, and recorded like any other refusal: the claim replays it, and the trail
        # reads requested, then refused with the code that says why. Subscription is never called.
        problem = _problem(BusinessRuleProblem, CODE_NOT_OFFERABLE, NOT_OFFERABLE_DETAIL)
        log.warning(
            "plan_change.not_offerable",
            organization_id=str(organization_id),
            subscription_id=str(subscription_id),
            plan_version_id=str(target),
        )
        await _record(
            app_session,
            organization_id=organization_id,
            key=idempotency_key,
            status=IDEMPOTENCY_REFUSED,
            response=_problem_dump(problem),
            operator=operator,
            subscription_id=subscription_id,
            action=AUDIT_REFUSED,
            after={"status": problem.status_code, "code": CODE_NOT_OFFERABLE},
            reason=payload.reason,
            request=request,
        )
        raise problem

    try:
        changed = await _change_plan(
            settings,
            organization_id=organization_id,
            subscription_id=subscription_id,
            plan_version_id=target,
            operator_id=label,
            client=client,
            # `auto` and `next_period` are one rule (module docstring); a trial ignores it.
            timing="next_period",
            note=payload.reason,
            expected=expected.model_dump(mode="json"),
        )
    except SubscriptionChangeError as exc:
        problem = problem_for(exc)
        definitive = exc.status_code in DEFINITIVE_REFUSALS
        if reconciling and exc.status_code == 409 and exc.code == "state_changed":
            # The attempt whose answer was lost may only have been late: it can commit between the
            # live read above and this call, which then finds the subscription moved — by that
            # very change. Look again before calling it a refusal, or the key would replay
            # "it changed since you looked" for a change that was made.
            try:
                live = await _subscription_for(
                    settings, organization_id, product_key, client=client
                )
            except ProblemError:
                live = None
            if live is None:
                definitive, problem = False, problem_for(SubscriptionChangeError("re-read failed"))
            elif (landed := _landed(live, target, expected)) is not None:
                return await _record_reconciled(landed)
        log.warning(
            "plan_change.not_applied" if definitive else "plan_change.outcome_unknown",
            organization_id=str(organization_id),
            subscription_id=str(subscription_id),
            status=exc.status_code,
            code=exc.code,
        )
        await _record(
            app_session,
            organization_id=organization_id,
            key=idempotency_key,
            status=IDEMPOTENCY_REFUSED if definitive else IDEMPOTENCY_UNKNOWN,
            response=_problem_dump(problem) if definitive else None,
            operator=operator,
            subscription_id=subscription_id,
            action=AUDIT_REFUSED if definitive else AUDIT_UNKNOWN,
            after={"status": exc.status_code, "code": exc.code or _problem_dump(problem)["code"]},
            reason=payload.reason,
            request=request,
        )
        raise problem from exc

    out = _out_from(changed, target)
    await _record(
        app_session,
        organization_id=organization_id,
        key=idempotency_key,
        status=IDEMPOTENCY_SUCCEEDED,
        response=out.model_dump(mode="json", exclude={"replayed"}),
        operator=operator,
        subscription_id=subscription_id,
        action=AUDIT_APPLIED,
        after={
            **out.model_dump(mode="json", exclude={"replayed"}),
            "product_key": product_key,
        },
        reason=payload.reason,
        request=request,
    )
    log.info(
        "plan_change.applied",
        organization_id=str(organization_id),
        subscription_id=str(subscription_id),
        operator=label,
        effective=out.effective,
    )
    return out


async def _record(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    key: str,
    status: str,
    response: dict[str, Any] | None,
    operator: Principal,
    subscription_id: uuid.UUID,
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
        operator_label=operator_label(operator),
        outcome_action=action,
        entity="subscription",
        entity_id=subscription_id,
        outcome_after=after,
        reason=reason,
        request=request,
    )
