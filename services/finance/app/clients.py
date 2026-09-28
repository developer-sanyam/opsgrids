"""Callers for other services' `/internal/v1` — the identity bridge's own half of "call", under
a short-lived service token addressed to the callee by name. Same shape `services/lead/app/
clients.py` already takes for `:convert`, and `services/notification/app/contacts.py` for a
messaging-target lookup: mint a token, make one call, translate the response into a plain
return value or a raised exception, never the response body into an exception message.

**Never put an upstream response body into an exception message.** A 4xx or 5xx from
customer-service carries no field this module has any business repeating — see `lead/app/
clients.py`'s own docstring for the exact shape of mistake that guards against. Every
exception below carries a fixed, generic message; the status code (never the body) is the
only thing written to the log.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("finance.clients")

CUSTOMER_AUDIENCE: Final = "customer"


class CrmContactUnavailable(Exception):
    """customer-service could not be asked — unreachable, timeout, or a 5xx. Safe, and meant,
    to be retried: `ensure_customer_for_crm_contact`'s replay path (an existing `crm_contact_id`
    match) makes no network call at all, so a retry after this costs one more lookup, never a
    second row."""


class CrmContactNotFound(Exception):
    """`crm_contact_id` does not resolve to a live contact in customer-service's book — the
    404 that route already gives for an unknown or cross-tenant id. Permanent for this id."""


@dataclass(frozen=True, slots=True)
class CrmContactSummary:
    """The directory identity customer-service's `contacts/{id}` route answers with — enough
    to seed a `finance.Customer` row. A plain container, not a schema: this is an internal
    client's return value, never a wire contract this service itself publishes."""

    contact_id: uuid.UUID
    full_name: str
    email: str
    company_id: uuid.UUID
    company_name: str


async def fetch_crm_contact(
    settings: Settings,
    organization_id: uuid.UUID,
    crm_contact_id: uuid.UUID,
    *,
    client: httpx.AsyncClient | None = None,
) -> CrmContactSummary:
    """Ask customer-service who this CRM contact is — `ensure_customer_for_crm_contact`'s own
    "call" step, made with no database transaction open (D3): this is a plain network call, and
    the claim/record steps either side of it are what a database lock actually belongs to."""
    base = f"{settings.customer_internal_url}/internal/v1/organizations/{organization_id}"
    url = f"{base}/contacts/{crm_contact_id}"
    headers = {
        "X-Internal-Token": mint_service_token(
            settings,
            audience=CUSTOMER_AUDIENCE,
            caller=settings.service_name,
            claims={"org": str(organization_id)},
        )
    }

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.customer_timeout_seconds)
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError:
        log.error("finance.clients.customer_unreachable")
        raise CrmContactUnavailable("customer-service could not be reached") from None
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code == 404:
        log.warning("finance.clients.crm_contact_not_found")
        raise CrmContactNotFound("that CRM contact is not part of this organization")
    if response.status_code >= 500:
        log.error("finance.clients.customer_upstream_error", status=response.status_code)
        raise CrmContactUnavailable("customer-service is temporarily unavailable")
    if response.status_code != 200:
        log.error("finance.clients.customer_rejected", status=response.status_code)
        raise CrmContactUnavailable("customer-service could not answer this request")

    try:
        data = response.json()
        return CrmContactSummary(
            contact_id=uuid.UUID(data["contact_id"]),
            full_name=data["full_name"],
            email=data["email"],
            company_id=uuid.UUID(data["company_id"]),
            company_name=data["company_name"],
        )
    except (ValueError, KeyError, TypeError):
        # A 200 that does not parse into the shape this client expects is not a success — a
        # bad deploy or a half-finished response, treated as transient rather than crashing
        # the caller on a parse error it never asked for.
        log.error("finance.clients.customer_malformed_response", status=response.status_code)
        raise CrmContactUnavailable("customer-service returned an unexpected response") from None
