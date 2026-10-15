"""Reading CRM contacts for Engage's projection — customer's `contacts:export` (C53).

The `crm.book.contact.*` events are thin triggers: they carry ids, never a name, address or number,
so `project_crm_contact` asks customer for the state under a service token addressed to it, with
the organization in the `org` claim. Customer opens the sealed number for this one caller and
audits the read.

**Two outcomes, never collapsed.** `None` / an absent id is a definite answer ("that contact is
gone"); `CrmUnavailable` is "could not ask" and is raised so the consumer's redeliver-once-then-
dead-letter handling gives it a second attempt instead of acknowledging nothing.

Nothing here logs a name, an address or a number.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings
from platform_core.internal_auth import mint_service_token

CUSTOMER_AUDIENCE: Final = "customer"


class CrmUnavailable(Exception):
    """Customer could not answer; the event must be retried, not acknowledged."""


@dataclass(frozen=True, slots=True)
class CrmContact:
    contact_id: uuid.UUID
    company_id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    status: str
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class CrmContactPage:
    items: list[CrmContact]
    #: `(updated_at, id)` to pass back as the cursor; None on the last page.
    next_cursor: tuple[datetime, uuid.UUID] | None


class _Item(BaseModel):
    contact_id: uuid.UUID
    company_id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    status: str
    deleted_at: datetime | None


class _Body(BaseModel):
    items: list[_Item]
    next_after_updated_at: datetime | None = None
    next_after_id: uuid.UUID | None = None


#: `(organization_id, crm_contact_id) -> CrmContact | None`; injected so tests need no network.
CrmContactFetcher = Callable[[str, str], Awaitable[CrmContact | None]]


async def _export(
    settings: Settings,
    organization_id: str,
    params: dict[str, str],
    client: httpx.AsyncClient | None,
) -> _Body:
    url = (
        f"{settings.customer_internal_url}/internal/v1/organizations/{organization_id}"
        "/contacts:export"
    )
    headers = {
        "X-Internal-Token": mint_service_token(
            settings,
            audience=CUSTOMER_AUDIENCE,
            caller=settings.service_name,
            claims={"org": organization_id},
        )
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.customer_timeout_seconds)
    try:
        response = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise CrmUnavailable(type(exc).__name__) from None
    finally:
        if owns_client:
            await client.aclose()
    if response.status_code != 200:
        raise CrmUnavailable(f"customer returned {response.status_code}")
    try:
        return _Body.model_validate(response.json())
    except (ValueError, ValidationError):
        raise CrmUnavailable("unparseable customer response") from None


def _contact(item: _Item) -> CrmContact:
    return CrmContact(**item.model_dump())


async def fetch_crm_contact(
    settings: Settings,
    organization_id: str,
    crm_contact_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> CrmContact | None:
    body = await _export(settings, organization_id, {"ids": crm_contact_id}, client)
    return next((_contact(i) for i in body.items if str(i.contact_id) == crm_contact_id), None)


async def fetch_crm_contact_page(
    settings: Settings,
    organization_id: str,
    after: tuple[datetime, uuid.UUID] | None = None,
    *,
    limit: int = 200,
    client: httpx.AsyncClient | None = None,
) -> CrmContactPage:
    """One keyset page of an organization's CRM contacts, for the owner-run backfill."""
    params = {"limit": str(limit)}
    if after is not None:
        params |= {"after_updated_at": after[0].isoformat(), "after_id": str(after[1])}
    body = await _export(settings, organization_id, params, client)
    cursor = (
        (body.next_after_updated_at, body.next_after_id)
        if body.next_after_updated_at and body.next_after_id
        else None
    )
    return CrmContactPage([_contact(i) for i in body.items], cursor)


def make_crm_contact_fetcher(settings: Settings) -> CrmContactFetcher:
    async def _fetch(organization_id: str, crm_contact_id: str) -> CrmContact | None:
        return await fetch_crm_contact(settings, organization_id, crm_contact_id)

    return _fetch
