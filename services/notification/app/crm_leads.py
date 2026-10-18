"""Reading CRM leads for a cold-email campaign — lead's `leads:export`.

Notification never holds the key that opens a lead's sealed address. It asks lead for the leads it
may mail under a service token addressed to lead, with the organization in the `org` claim, and lead
audits the read. The caller's scope (an OWN reader sees only their own leads) travels as `owner_id`
and is applied by lead in the query.

`LeadsUnavailable` means "could not ask" and becomes a 503 at the route; it is never read as "there
are no leads". Nothing here logs a name or an address.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings
from platform_core.internal_auth import mint_service_token

LEAD_AUDIENCE: Final = "lead"
_PAGE: Final = 200


class LeadsUnavailable(Exception):
    """Lead could not answer."""


class TooManyLeads(Exception):
    """The selection is larger than one campaign may target."""


@dataclass(frozen=True, slots=True)
class LeadRow:
    lead_id: uuid.UUID
    full_name: str
    company: str | None
    email: str | None
    status: str
    source: str


class _Item(BaseModel):
    lead_id: uuid.UUID
    full_name: str
    company: str | None
    email: str | None
    status: str
    source: str
    owner_id: uuid.UUID | None = None


class _Body(BaseModel):
    items: list[_Item]
    next_after_id: uuid.UUID | None = None


async def _fetch_page(
    settings: Settings,
    organization_id: uuid.UUID,
    params: dict[str, str],
    client: httpx.AsyncClient,
) -> _Body:
    url = f"{settings.lead_internal_url}/internal/v1/organizations/{organization_id}/leads:export"
    headers = {
        "X-Internal-Token": mint_service_token(
            settings,
            audience=LEAD_AUDIENCE,
            caller=settings.service_name,
            claims={"org": str(organization_id)},
        )
    }
    try:
        response = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise LeadsUnavailable(type(exc).__name__) from None
    if response.status_code != 200:
        raise LeadsUnavailable(f"lead returned {response.status_code}")
    try:
        return _Body.model_validate(response.json())
    except (ValueError, ValidationError):
        raise LeadsUnavailable("unparseable lead response") from None


async def fetch_leads(
    settings: Settings,
    organization_id: uuid.UUID,
    *,
    ids: list[uuid.UUID] | None = None,
    status: str | None = None,
    source: str | None = None,
    owner_id: uuid.UUID | None = None,
    emails: bool = True,
    client: httpx.AsyncClient | None = None,
) -> list[LeadRow]:
    """The leads an explicit id list names, or those matching a status/source filter, narrowed to
    `owner_id` for an OWN reader. At most `engage_cold_audience_max`; one more raises `TooManyLeads`
    (a filter that matches thousands is a narrower filter, not a silent truncation). Ids go in
    pages of 100, a filter in keyset pages of 200 — a handful of requests, never one per lead."""
    ceiling = settings.engage_cold_audience_max
    base: dict[str, str] = {"emails": "true" if emails else "false", "limit": str(_PAGE)}
    if status:
        base["status"] = status
    if source:
        base["source"] = source
    if owner_id is not None:
        base["owner_id"] = str(owner_id)
    out: list[LeadRow] = []
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.lead_timeout_seconds)
    try:
        if ids is not None:
            if len(ids) > ceiling:
                raise TooManyLeads
            for start in range(0, len(ids), 100):
                chunk = ",".join(str(i) for i in ids[start : start + 100])
                body = await _fetch_page(settings, organization_id, {**base, "ids": chunk}, client)
                out += [LeadRow(**i.model_dump(exclude={"owner_id"})) for i in body.items]
            return out
        after: uuid.UUID | None = None
        while True:
            params = {**base, **({"after_id": str(after)} if after else {})}
            body = await _fetch_page(settings, organization_id, params, client)
            out += [LeadRow(**i.model_dump(exclude={"owner_id"})) for i in body.items]
            if len(out) > ceiling:
                raise TooManyLeads
            if body.next_after_id is None:
                return out
            after = body.next_after_id
    finally:
        if owns_client:
            await client.aclose()
