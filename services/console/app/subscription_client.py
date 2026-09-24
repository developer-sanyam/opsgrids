"""Calling subscription for the pricing screen's read and its operator re-pin.

Both calls travel under a service token addressed to subscription by name, `caller="console"` —
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
from platform_core.logging import get_logger

log = get_logger("console.subscription_client")

SUBSCRIPTION_AUDIENCE = "subscription"


class SubscriptionChangeError(Exception):
    """subscription refused the re-pin, or was unreachable.

    `status_code` mirrors subscription's own answer (404 unknown subscription, 422 a business
    rule refused the change — e.g. a `past_due`/`suspended`/`cancelled` subscription cannot
    change plan) when it gave one; `None` for a transport failure or an unexpected status.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "X-Internal-Token": mint_service_token(
            settings, audience=SUBSCRIPTION_AUDIENCE, caller="console"
        )
    }


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
) -> dict[str, Any]:
    """Re-pin `subscription_id` to `plan_version_id` on the operator's behalf. Subscription's own
    change-plan rule decides the timing (immediate for a trial or a paid upgrade, deferred to the
    next period for a paid downgrade); this call only carries the request."""
    url = (
        f"{settings.subscription_internal_url}/internal/v1/subscriptions/"
        f"{subscription_id}/change-plan"
    )
    body = {
        "organization_id": str(organization_id),
        "plan_version_id": str(plan_version_id),
        "operator_id": operator_id,
    }
    try:
        response = await client.post(url, json=body, headers=_headers(settings))
    except httpx.HTTPError as exc:
        raise SubscriptionChangeError(f"subscription is unavailable: {exc}") from exc
    if response.status_code >= 400:
        detail = _detail(response)
        raise SubscriptionChangeError(detail, status_code=response.status_code)
    result: dict[str, Any] = response.json()
    return result


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail) if detail else response.text[:300]
