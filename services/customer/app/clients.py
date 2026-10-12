"""Outbound calls to other services' `/internal/v1`, under a short-lived service token addressed
to each by name (the shape of lead's `app/clients.py`).

**Never put an upstream response body into an exception message or a log line.** A 4xx body can
carry a validation message, and a validation message can carry a name or an address. Exceptions
carry a fixed message; the status code is the only thing logged.

Every function here is a network call and must never run inside a database transaction
(`tests/infra/test_io_outside_transaction.py`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("customer.clients")

DEAL_AUDIENCE: Final = "deal"


class ReferencesProbeUnavailable(Exception):
    """deal's references probe could not answer: unreachable, a timeout, a non-200 or a body that
    does not parse. The aborted-conversion handler re-raises it so the message is redelivered and
    then dead-lettered; acknowledging would lose the cleanup silently."""


@dataclass(frozen=True)
class DealReferences:
    company_deal_count: int
    contact_deal_count: int
    company_activity_count: int
    contact_activity_count: int


async def deal_references(
    settings: Settings,
    organization_id: uuid.UUID,
    *,
    company_id: uuid.UUID,
    contact_id: uuid.UUID | None,
    exclude_deal_id: uuid.UUID | None,
    client: httpx.AsyncClient | None = None,
) -> DealReferences:
    """How many live deals and activities in deal name the company and the contact, leaving out
    `exclude_deal_id` when deal itself would treat that deal as disposable."""
    url = f"{settings.deal_internal_url}/internal/v1/organizations/{organization_id}/references"
    params = {"company_id": str(company_id)}
    if contact_id is not None:
        params["contact_id"] = str(contact_id)
    if exclude_deal_id is not None:
        params["exclude_deal_id"] = str(exclude_deal_id)
    headers = {
        "X-Internal-Token": mint_service_token(
            settings,
            audience=DEAL_AUDIENCE,
            caller=settings.service_name,
            claims={"org": str(organization_id)},
        )
    }

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.deal_timeout_seconds)
    try:
        response = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError:
        log.error("customer.clients.deal_unreachable")
        raise ReferencesProbeUnavailable("deal-service could not be reached") from None
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        log.error("customer.clients.deal_references_failed", status=response.status_code)
        raise ReferencesProbeUnavailable("deal-service could not answer the references probe")
    try:
        data = response.json()
        counts = DealReferences(
            company_deal_count=data["company_deal_count"],
            contact_deal_count=data["contact_deal_count"],
            company_activity_count=data["company_activity_count"],
            contact_activity_count=data["contact_activity_count"],
        )
    except (ValueError, KeyError, TypeError):
        counts = None
    if counts is None or not all(type(n) is int and n >= 0 for n in vars(counts).values()):
        log.error("customer.clients.deal_references_malformed", status=response.status_code)
        raise ReferencesProbeUnavailable("deal-service returned an unexpected response")
    return counts
