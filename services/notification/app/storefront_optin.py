"""Fetching a storefront buyer's WhatsApp opt-in — the number behind `storefront.enquiry.
whatsapp_opted_in` (ENGAGE-02).

The event carries the enquiry id only, because the number is sealed PII and never travels on an
event. Storefront answers `GET /internal/v1/enquiries/{id}/whatsapp-opt-in` to this service alone,
under a token addressed to it whose `org` claim is the envelope's organization, and audits every
read. A 404 is a definite "no such ticked enquiry" (`None`); anything else that is not a 200 is
`StorefrontUnavailable`, which the consumer raises so the event is redelivered rather than lost.

Nothing here logs the number or the address.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import httpx
from pydantic import AwareDatetime, BaseModel, ValidationError

from app.config import Settings
from platform_core.internal_auth import mint_service_token

STOREFRONT_AUDIENCE: Final = "storefront"


class StorefrontUnavailable(Exception):
    """Storefront could not answer; the event must be retried, not acknowledged."""


@dataclass(frozen=True, slots=True)
class StorefrontOptIn:
    name: str
    email: str | None
    phone: str
    opted_in_at: datetime


class _Body(BaseModel):
    name: str
    email: str | None
    phone: str
    opted_in_at: AwareDatetime


#: `(organization_id, enquiry_id) -> StorefrontOptIn | None`; injected so tests need no network.
StorefrontOptInFetcher = Callable[[str, str], Awaitable[StorefrontOptIn | None]]


async def fetch_storefront_opt_in(
    settings: Settings,
    organization_id: str,
    enquiry_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> StorefrontOptIn | None:
    url = f"{settings.storefront_internal_url}/internal/v1/enquiries/{enquiry_id}/whatsapp-opt-in"
    headers = {
        "X-Internal-Token": mint_service_token(
            settings,
            audience=STOREFRONT_AUDIENCE,
            caller=settings.service_name,
            claims={"org": organization_id},
        )
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.storefront_timeout_seconds)
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise StorefrontUnavailable(type(exc).__name__) from None
    finally:
        if owns_client:
            await client.aclose()
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise StorefrontUnavailable(f"storefront returned {response.status_code}")
    try:
        body = _Body.model_validate(response.json())
    except (ValueError, ValidationError):
        raise StorefrontUnavailable("unparseable storefront response") from None
    return StorefrontOptIn(
        name=body.name, email=body.email, phone=body.phone, opted_in_at=body.opted_in_at
    )


def make_storefront_opt_in_fetcher(settings: Settings) -> StorefrontOptInFetcher:
    async def _fetch(organization_id: str, enquiry_id: str) -> StorefrontOptIn | None:
        return await fetch_storefront_opt_in(settings, organization_id, enquiry_id)

    return _fetch
