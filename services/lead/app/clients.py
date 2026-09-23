"""Callers for `:convert` — customer's and deal's `/internal/v1`, under a short-lived service
token addressed to each by name. Same shape notification-service's `targets.py` already takes
to ask customer for a messaging address: mint a token, make one call, translate the response
into a plain return value or a raised exception. Neither client decrypts anything — the raw
email and phone lead-service forwards were opened from its own sealed columns by the caller
(`services.convert_lead`), the one place that read is audited.

**Never put an upstream response body into an exception message.** A 4xx from customer or deal
can — validation errors are shaped to carry the offending field's message, and depending on
what was being validated that message can itself have carried PII forwarded from this
service's own decrypted lead data (this is exactly how a phone number to another service's
error and back into this one's own 503 detail, before this module drew the line at status
codes only). Every exception below carries a fixed, generic message; the status code (never
the body) is the only thing written to the log.
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
    """The target service could not be asked — unreachable, timeout, or a 5xx. Safe, and
    meant, to be retried: the idempotent-by-id design on both sides makes a retry safe, and
    nothing about the request itself was the problem."""


class ConversionRejected(Exception):
    """The target service reached the request and refused it — a 4xx other than the specific
    "unknown owner" case below. Permanent: retrying the same input will not help, so the
    caller maps this to a 422 rather than a 503."""


class OwnerUnknown(ConversionRejected):
    """`owner_id` does not resolve to an active rep in customer's directory. Permanent —
    retrying `:convert` will not fix it; the lead needs an owner known to the CRM book."""


class CompanyDeleted(ConversionRejected):
    """The company this lead would convert into has been deleted from customer's book since
    an earlier attempt used its id. Permanent, and distinct from the generic rejection: the
    caller needs to know *why* — this is not "check the lead's details", it is "that account
    is gone, point this lead somewhere else"."""


class DealNameConflict(ConversionRejected):
    """deal-service refused the deal with a 409 — the only 409 `POST leads:convert` can answer
    on the deal side is "a deal with that name already exists". Permanent for this name, and
    distinct from the generic rejection so the caller can say so instead of "check the lead's
    details", which would send the rep looking at fields that are not the problem."""


class ContactDeleted(ConversionRejected):
    """The contact-side twin of `CompanyDeleted`: the contact an earlier attempt created for
    this lead has since been deleted. Same handling — permanent for *these* ids, recoverable
    by converting again under a fresh generation of them."""


def _problem_type(response: httpx.Response) -> str | None:
    """The RFC 7807 `type` field off a JSON error body, or `None` on anything that is not
    one — never the body itself, and never raises. `type` is a small, fixed enum-like string
    customer/deal each control (`.../company-deleted`, never a value built from request data),
    so branching on it is not the "upstream body into an exception" mistake this module's own
    docstring warns about; only a fixed string *we* wrote in response ever reaches a message.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    problem_type = body.get("type")
    return problem_type if isinstance(problem_type, str) else None


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
    actor_id: uuid.UUID | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Ask customer-service to ensure a company (+ contact) for this lead. Returns
    `(company_id, contact_id)` — `contact_id` is `None` when the lead carried no email."""
    base = f"{settings.customer_internal_url}/internal/v1/organizations/{organization_id}"
    url = f"{base}/leads:convert"
    claims: dict[str, object] = {"org": str(organization_id)}
    if actor_id is not None:
        claims["sub"] = str(actor_id)
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=CUSTOMER_AUDIENCE, caller=settings.service_name, claims=claims
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
    except httpx.HTTPError:
        log.error("lead.clients.customer_unreachable")
        raise ConversionUnavailable("customer-service could not be reached") from None
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code == 404:
        log.warning("lead.clients.owner_unknown", status=response.status_code)
        raise OwnerUnknown("that owner is not an active rep in this organization's CRM book")
    if response.status_code == 409:
        problem_type = _problem_type(response) or ""
        if problem_type.endswith("/company-deleted"):
            log.warning("lead.clients.company_deleted", status=response.status_code)
            raise CompanyDeleted("that company has been deleted from the CRM book")
        if problem_type.endswith("/contact-deleted"):
            log.warning("lead.clients.contact_deleted", status=response.status_code)
            raise ContactDeleted("that contact has been deleted from the CRM book")
    if response.status_code >= 500:
        log.error("lead.clients.customer_upstream_error", status=response.status_code)
        raise ConversionUnavailable("customer-service is temporarily unavailable")
    if response.status_code != 200:
        log.error("lead.clients.customer_rejected", status=response.status_code)
        raise ConversionRejected("customer-service could not complete the conversion")

    try:
        data = response.json()
        company_id_out = uuid.UUID(data["company_id"])
        contact_id_out = uuid.UUID(data["contact_id"]) if data["contact_id"] else None
    except (ValueError, KeyError, TypeError):
        # A 200 that does not parse into the shape this client expects is not a success —
        # treated as transient (a bad deploy, a half-finished response) rather than crashing
        # with a raw parse error the caller never asked for a stack trace on.
        log.error("lead.clients.customer_malformed_response", status=response.status_code)
        raise ConversionUnavailable("customer-service returned an unexpected response") from None

    log.info("lead.clients.company_ensured", company_id=str(company_id_out))
    return company_id_out, contact_id_out


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
    actor_id: uuid.UUID | None = None,
    client: httpx.AsyncClient | None = None,
) -> uuid.UUID:
    """Ask deal-service to ensure a deal for this lead's company (+ contact). Returns the
    deal id — the caller's own `deal_id` on both a fresh open and a replayed one."""
    base = f"{settings.deal_internal_url}/internal/v1/organizations/{organization_id}"
    url = f"{base}/leads:convert"
    claims: dict[str, object] = {"org": str(organization_id)}
    if actor_id is not None:
        claims["sub"] = str(actor_id)
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=DEAL_AUDIENCE, caller=settings.service_name, claims=claims
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
    except httpx.HTTPError:
        log.error("lead.clients.deal_unreachable")
        raise ConversionUnavailable("deal-service could not be reached") from None
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code == 409:
        log.warning("lead.clients.deal_name_conflict", status=response.status_code)
        raise DealNameConflict("a deal with that name already exists in the CRM book")
    if response.status_code >= 500:
        log.error("lead.clients.deal_upstream_error", status=response.status_code)
        raise ConversionUnavailable("deal-service is temporarily unavailable")
    if response.status_code != 200:
        log.error("lead.clients.deal_rejected", status=response.status_code)
        raise ConversionRejected("deal-service could not complete the conversion")

    try:
        return uuid.UUID(response.json()["deal_id"])
    except (ValueError, KeyError, TypeError):
        log.error("lead.clients.deal_malformed_response", status=response.status_code)
        raise ConversionUnavailable("deal-service returned an unexpected response") from None


class OwnersValidateUnavailable(Exception):
    """customer-service could not be asked which owners are active — unreachable, timeout, or
    a 5xx. Safe to retry; nothing about the request was the problem."""


async def invalid_owner_ids(
    settings: Settings,
    organization_id: uuid.UUID,
    owner_ids: list[uuid.UUID],
    *,
    client: httpx.AsyncClient | None = None,
) -> list[uuid.UUID]:
    """Which of `owner_ids` do **not** resolve to an active rep in customer's directory —
    lead-service's own gate before it writes an explicit `owner_id`, or accepts one into the
    round-robin's owner list. Empty means every id named is a known, active owner."""
    if not owner_ids:
        return []
    base = f"{settings.customer_internal_url}/internal/v1/organizations/{organization_id}"
    url = f"{base}/owners:validate"
    headers = {
        "X-Internal-Token": mint_service_token(
            settings,
            audience=CUSTOMER_AUDIENCE,
            caller=settings.service_name,
            claims={"org": str(organization_id)},
        )
    }
    body = {"owner_ids": [str(owner_id) for owner_id in owner_ids]}

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.customer_timeout_seconds)
    try:
        response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError:
        log.error("lead.clients.owners_validate_unreachable")
        raise OwnersValidateUnavailable("customer-service could not be reached") from None
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        log.error("lead.clients.owners_validate_rejected", status=response.status_code)
        raise OwnersValidateUnavailable("customer-service could not validate the owner list")

    try:
        return [uuid.UUID(o) for o in response.json()["invalid_owner_ids"]]
    except (ValueError, KeyError, TypeError):
        log.error("lead.clients.owners_validate_malformed_response", status=response.status_code)
        raise OwnersValidateUnavailable(
            "customer-service returned an unexpected response"
        ) from None
