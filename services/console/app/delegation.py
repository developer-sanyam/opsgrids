"""Operator overrides delegate to the owning service — console never writes a foreign schema.

An operator override is a *write into a tenant*, and ADR-0003 §2 is explicit about how those go:
the operator surface resolves the target on its bypass read, then the write happens in the service
that owns the data, under that service's own rules — never by console reaching across a schema
boundary it holds no foreign key into. So a suspend is a call to subscription's
`POST /internal/v1/subscriptions/{id}/suspend`; a comp grant is a call to billing's
`POST /internal/v1/credit`. Each goes under a short-lived service token addressed to that service
by name (`platform_core.internal_auth`), exactly like every other server-to-server hop.

Console's own record of the act is a local `console.audit_logs` row bound to the target
organization (see `services.record_override`) — the row `/admin/audit` reads back. That row is the
only thing console writes for an override, and it is in console's own schema.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("console.delegation")

#: The overrides the operator console offers, each mapped to the service that owns the write.
ACTION_SUSPEND = "suspend"
ACTION_COMP_GRANT = "comp_grant"


class DelegationError(Exception):
    """The owning service refused or was unreachable. Surfaced to the operator as a 502."""


@dataclass(frozen=True, slots=True)
class DelegationSpec:
    """One resolved outbound call: which service, which endpoint, what body."""

    audience: str
    method: str
    url: str
    json: dict[str, object]


def resolve_spec(
    settings: Settings,
    *,
    action: str,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID | None,
    amount_minor: int | None,
    currency: str | None,
    reason: str | None,
) -> DelegationSpec:
    """Map an override to the owning service's internal endpoint. Raises on an unknown action."""
    if action == ACTION_SUSPEND:
        if subscription_id is None:
            raise DelegationError("suspend requires subscription_id")
        return DelegationSpec(
            audience="subscription",
            method="POST",
            url=f"{settings.subscription_internal_url}/internal/v1/subscriptions/{subscription_id}/suspend",
            json={"reason": reason or "operator override"},
        )
    if action == ACTION_COMP_GRANT:
        if amount_minor is None or currency is None:
            raise DelegationError("comp_grant requires amount_minor and currency")
        return DelegationSpec(
            audience="billing",
            method="POST",
            url=f"{settings.billing_internal_url}/internal/v1/credit",
            json={
                "organization_id": str(organization_id),
                "amount_minor": int(amount_minor),
                "currency": currency,
                "reason": reason or "operator comp grant",
            },
        )
    raise DelegationError(f"unknown override action: {action}")


async def deliver(
    settings: Settings, spec: DelegationSpec, *, client: httpx.AsyncClient | None = None
) -> dict[str, object]:
    """Make the delegated call under a service token addressed to the owning service.

    The token's audience is the owning service's name, so a token minted here for subscription
    cannot be replayed against billing (`platform_core.internal_auth`). Returns the owning
    service's JSON response; raises `DelegationError` on a transport failure or a non-2xx.
    """
    headers = {
        "X-Internal-Token": mint_service_token(settings, audience=spec.audience, caller="console")
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.request(spec.method, spec.url, json=spec.json, headers=headers)
        response.raise_for_status()
        log.info("override.delegated", audience=spec.audience, url=spec.url)
        payload: dict[str, object] = response.json() if response.content else {}
        return payload
    except httpx.HTTPStatusError as exc:
        raise DelegationError(
            f"{spec.audience} refused the override: {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise DelegationError(f"{spec.audience} was unreachable: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()
