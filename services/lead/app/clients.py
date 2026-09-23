"""Callers for `:convert` — customer's and deal's `/internal/v1`, under a short-lived service
token addressed to each by name. Same shape notification-service's `targets.py` already takes
to ask customer for a messaging address: mint a token, make one call, translate the response
into a plain return value or a raised exception. Neither client decrypts anything — the raw
email and phone lead-service forwards were opened from its own sealed columns by the caller
(`services.convert_lead`), the one place that read is audited.
"""

from __future__ import annotations

import uuid
from typing import Final

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("lead.clients")

CUSTOMER_AUDIENCE: Final = "customer"
DEAL_AUDIENCE: Final = "deal"


class ConversionUnavailable(Exception):
    """The target service could not be asked — unreachable, timeout, 5xx. The caller should
    let the client retry `:convert` rather than treating this as a permanent failure; the
    idempotent-by-id design on both sides makes a retry safe."""


class OwnerUnknown(Exception):
    """`owner_id` does not resolve to an active rep in customer's directory. Permanent —
    retrying `:convert` will not fix it; the lead needs an owner known to the CRM book."""


async def ensure_company_and_contact(
    settings: Settings,
    organization_id: uuid.UUID,
    *,
    company_id: uuid.UUID,
    contact_id: uuid.UUID,
    company_name: str,
    owner_id: uuid.UUID,
    contact_full_name: str,
    contact_email: str | None,
    contact_raw_phone: str | None,
    client: httpx.AsyncClient | None = None,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Ask customer-service to ensure a company (+ contact) for this lead. Returns
    `(company_id, contact_id)` — `contact_id` is `None` when the lead carried no email."""
    base = f"{settings.customer_internal_url}/internal/v1/organizations/{organization_id}"
    url = f"{base}/leads:convert"
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=CUSTOMER_AUDIENCE, caller=settings.service_name
        )
    }
    body = {
        "company_id": str(company_id),
        "contact_id": str(contact_id),
        "company_name": company_name,
        "owner_id": str(owner_id),
        "contact_full_name": contact_full_name,
        "contact_email": contact_email,
        "contact_raw_phone": contact_raw_phone,
    }

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.customer_timeout_seconds)
    try:
        response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise ConversionUnavailable(f"customer-service unreachable: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code == 404:
        raise OwnerUnknown(f"{owner_id} is not an active owner in this organization's CRM book")
    if response.status_code >= 500:
        raise ConversionUnavailable(f"customer-service returned {response.status_code}")
    if response.status_code != 200:
        raise ConversionUnavailable(
            f"customer-service refused the conversion: {response.status_code} {response.text}"
        )

    data = response.json()
    log.info("lead.clients.company_ensured", company_id=data["company_id"])
    return uuid.UUID(data["company_id"]), (
        uuid.UUID(data["contact_id"]) if data["contact_id"] else None
    )


async def ensure_deal(
    settings: Settings,
    organization_id: uuid.UUID,
    *,
    deal_id: uuid.UUID,
    company_id: uuid.UUID,
    contact_id: uuid.UUID | None,
    owner_id: uuid.UUID,
    name: str,
    close_date: str,
    source: str,
    client: httpx.AsyncClient | None = None,
) -> uuid.UUID:
    """Ask deal-service to ensure a deal for this lead's company (+ contact). Returns the
    deal id — the caller's own `deal_id` on both a fresh open and a replayed one."""
    base = f"{settings.deal_internal_url}/internal/v1/organizations/{organization_id}"
    url = f"{base}/leads:convert"
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=DEAL_AUDIENCE, caller=settings.service_name
        )
    }
    body = {
        "deal_id": str(deal_id),
        "company_id": str(company_id),
        "contact_id": str(contact_id) if contact_id else None,
        "owner_id": str(owner_id),
        "name": name,
        "close_date": close_date,
        "source": source,
    }

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.deal_timeout_seconds)
    try:
        response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise ConversionUnavailable(f"deal-service unreachable: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code >= 500:
        raise ConversionUnavailable(f"deal-service returned {response.status_code}")
    if response.status_code != 200:
        raise ConversionUnavailable(
            f"deal-service refused the conversion: {response.status_code} {response.text}"
        )

    return uuid.UUID(response.json()["deal_id"])
