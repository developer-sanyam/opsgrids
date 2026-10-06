"""Calling subscription for the pricing screen's read, its operator re-pin, and the staff Add
product and Extend trial (ADR-0031).

Every call travels under a service token addressed to subscription by name, `caller="console"` —
`api/internal.py:require_console_caller` refuses anything else. Console never writes
subscription's schema directly (ADR-0003 §2): the re-pin is the existing operator delegate
`change_plan_as_operator` already built for bundle sales, reused here for a staff price change.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("console.subscription_client")

SUBSCRIPTION_AUDIENCE = "subscription"


class SubscriptionChangeError(Exception):
    """subscription refused the re-pin, or was unreachable.

    `status_code` mirrors subscription's own answer (404 unknown subscription, 422 a business
    rule refused the change — e.g. a `past_due`/`suspended`/`cancelled` subscription cannot
    change plan) when it gave one; `None` for a transport failure or an unexpected status.

    `code` is the stable machine code subscription put on its refusal (`state`, `cadence`,
    `state_changed`, `billing_catching_up`, ...), read from the problem body's `errors[0].code`;
    `None` when it gave none. The message is subscription's own `detail`, written for a client.
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _headers(settings: Settings) -> dict[str, str]:
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=SUBSCRIPTION_AUDIENCE, caller="console"
        )
    }
    # The request's correlation id travels with the call, so one staff action can be followed
    # from console's log into the owning service's (the same header every internal client sends).
    correlation = correlation_id_ctx.get()
    if correlation:
        headers["X-Correlation-ID"] = correlation
    return headers


async def list_subscriptions(
    settings: Settings, organization_id: uuid.UUID, *, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    """Every subscription this organization holds, with the ids and dates console's own
    projection does not carry."""
    url = (
        f"{settings.subscription_internal_url}/internal/v1/organizations/"
        f"{organization_id}/subscriptions"
    )
    try:
        response = await client.get(url, headers=_headers(settings))
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SubscriptionChangeError(f"subscription is unavailable: {exc}") from exc
    rows: list[dict[str, Any]] = response.json()
    return rows


async def change_plan(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
    plan_version_id: uuid.UUID,
    operator_id: str,
    client: httpx.AsyncClient,
    timing: str | None = None,
    note: str | None = None,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-pin `subscription_id` to `plan_version_id` on the operator's behalf.

    `timing="next_period"` (fix round 1, PAID-UPGRADE TIMING) forces subscription's existing
    scheduled-change mechanism regardless of direction — a staff price change on a paying
    subscription always waits for the next period, up or down, never prorating an upgrade in
    immediately the way a customer's own change-plan does. Left `None`, subscription applies its
    own default rule (immediate for a trial or an upgrade, deferred for a downgrade) — used for a
    trial, where there is no money to defer.

    `note` is the operator's reason in words (stored by subscription as the audit reason and on the
    event) and `expected` the subscription state the operator saw — subscription compares it under
    its row lock and answers 409 `state_changed` if it no longer holds. Both are optional here so
    the pricing flow, which predates them, is unchanged.
    """
    url = (
        f"{settings.subscription_internal_url}/internal/v1/subscriptions/"
        f"{subscription_id}/change-plan"
    )
    body: dict[str, Any] = {
        "organization_id": str(organization_id),
        "plan_version_id": str(plan_version_id),
        "operator_id": operator_id,
    }
    if timing is not None:
        body["timing"] = timing
    if note is not None:
        body["note"] = note
    if expected is not None:
        body["expected"] = expected
    try:
        response = await client.post(url, json=body, headers=_headers(settings))
    except httpx.HTTPError as exc:
        raise SubscriptionChangeError(f"subscription is unavailable: {exc}") from exc
    if response.status_code >= 400:
        raise _refusal(response)
    result: dict[str, Any] = response.json()
    return result


async def preview_change_plan(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
    plan_version_id: uuid.UUID,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """What re-pinning `subscription_id` to `plan_version_id` would do, from subscription itself:
    direction, when it lands, what it replaces, the products it adds or removes, and the live
    `expected` state to send with the write. Read-only on subscription's side; a refusal comes back
    as data (`refusal_code`), and only 404 (no such subscription or version) raises."""
    url = (
        f"{settings.subscription_internal_url}/internal/v1/subscriptions/"
        f"{subscription_id}/change-plan/preview"
    )
    body = {"organization_id": str(organization_id), "plan_version_id": str(plan_version_id)}
    try:
        response = await client.post(url, json=body, headers=_headers(settings))
    except httpx.HTTPError as exc:
        raise SubscriptionChangeError(f"subscription is unavailable: {exc}") from exc
    if response.status_code >= 400:
        raise _refusal(response)
    result: dict[str, Any] = response.json()
    return result


async def subscribe(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    product_key: str,
    plan_version_id: uuid.UUID,
    trial: bool,
    operator_id: str,
    note: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Open a subscription on the operator's behalf — subscription's operator `POST
    /internal/v1/subscriptions`, the path that also sells bundles (ADR-0031's Add product).

    `trial=False` opens it `active` and subscription claims its first period for billing in the same
    transaction. Refusals keep subscription's code: 409 `already_subscribed`; 422 `one_time`,
    `org_currency_mismatch`, `trial_not_offered`, `not_published`, `product_mismatch`, `bundle`,
    `reserved_reason`; 404 `not_found`.
    """
    url = f"{settings.subscription_internal_url}/internal/v1/subscriptions"
    body = {
        "organization_id": str(organization_id),
        "product_key": product_key,
        "plan_version_id": str(plan_version_id),
        "trial": trial,
        "operator_id": operator_id,
        "note": note,
    }
    try:
        response = await client.post(url, json=body, headers=_headers(settings))
    except httpx.HTTPError as exc:
        raise SubscriptionChangeError(f"subscription is unavailable: {exc}") from exc
    if response.status_code >= 400:
        raise _refusal(response)
    result: dict[str, Any] = response.json()
    return result


async def extend_trial(
    settings: Settings,
    *,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
    new_trial_ends_at: str,
    expected_trial_ends_at: str,
    operator_id: str,
    note: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Move a trial's end to an absolute instant, guarded by the end the operator saw — subscription
    answers 409 `state_changed` when it moved, so a repeat never adds the time twice. Refusals keep
    their code: 422 `not_trial`, `claim_in_flight`, `trial_cap`, `trial_not_extended`."""
    url = (
        f"{settings.subscription_internal_url}/internal/v1/subscriptions/"
        f"{subscription_id}/extend-trial"
    )
    body = {
        "organization_id": str(organization_id),
        "new_trial_ends_at": new_trial_ends_at,
        "expected_trial_ends_at": expected_trial_ends_at,
        "operator_id": operator_id,
        "note": note,
    }
    try:
        response = await client.post(url, json=body, headers=_headers(settings))
    except httpx.HTTPError as exc:
        raise SubscriptionChangeError(f"subscription is unavailable: {exc}") from exc
    if response.status_code >= 400:
        raise _refusal(response)
    result: dict[str, Any] = response.json()
    return result


def _refusal(response: httpx.Response) -> SubscriptionChangeError:
    """Subscription's problem+json as an error that keeps its status, its detail and its code."""
    return SubscriptionChangeError(
        _detail(response), status_code=response.status_code, code=_code(response)
    )


def _code(response: httpx.Response) -> str | None:
    """The stable machine code on a coded refusal (`errors[0].code` of the body), or None."""
    try:
        errors = response.json().get("errors")
    except (ValueError, AttributeError):
        return None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        code = errors[0].get("code")
        return code if isinstance(code, str) and code else None
    return None


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail) if detail else response.text[:300]
