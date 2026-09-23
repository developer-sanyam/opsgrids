"""Proving a WhatsApp connection before this service trusts it.

`POST /engage/whatsapp/connection` takes a WABA id, a phone number id and an access token the
caller typed in. Nothing before this module checked that the token could actually see that
number at Meta — an operator (or an attacker who found the form) could name any `phone_number_id`
at all, including one belonging to a different business entirely, and this service would record
it as `connected` and start routing that number's inbound webhooks here. Meta's own API is the
only authority on "does this token control this number", so this module asks it, twice: once
directly (`GET /{phone_number_id}`) and once through the WABA it is claimed to belong to
(`GET /{waba_id}/phone_numbers`) — a token that can read the number but the number is not actually
listed under that WABA is exactly as unproven as one that cannot read it at all.

The Graph API base is platform configuration (`Settings.whatsapp_api_base`), not something a
caller supplies per request, so this does not route through `platform_core.egress`'s SSRF guard —
that guard exists for a URL a tenant admin picks (an OIDC discovery document, say), and pinning a
fixed, operator-configured base is a different problem it would add ceremony to without adding
safety to.
"""

from __future__ import annotations

from typing import Final

import httpx

from app.config import Settings
from platform_core.logging import get_logger

log = get_logger("notification.whatsapp_provider")

#: Graph API responses are small JSON objects; there is no reason to read more of one, and a
#: provider that somehow returned megabytes must not be read fully into memory over this call.
_MAX_RESPONSE_BYTES: Final = 200_000


class WhatsAppOwnershipError(Exception):
    """The supplied access token could not prove it controls this WABA/phone-number pair."""


async def verify_ownership(
    settings: Settings,
    *,
    waba_id: str,
    phone_number_id: str,
    access_token: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Raises `WhatsAppOwnershipError` unless the token can read the phone number directly **and**
    see it listed under the named WABA. Never silently accepts a Graph API it could not reach —
    a connection this service could not verify is not one it should record as `connected`.
    """
    base = settings.whatsapp_api_base.rstrip("/")
    headers = {"Authorization": f"Bearer {access_token}"}

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.whatsapp_timeout_seconds)
    try:
        try:
            number_response = await client.get(
                f"{base}/{phone_number_id}", headers=headers, params={"fields": "id"}
            )
        except httpx.HTTPError as exc:
            raise WhatsAppOwnershipError(f"could not reach the Graph API: {exc}") from exc

        if number_response.status_code != 200:
            log.warning("whatsapp_provider.number_unverified", status=number_response.status_code)
            raise WhatsAppOwnershipError(
                "this access token could not read that phone number from Meta"
            )
        number_body = _json_object(number_response)
        if number_body is None or str(number_body.get("id") or "") != str(phone_number_id):
            raise WhatsAppOwnershipError("the phone number Meta returned did not match")

        try:
            waba_response = await client.get(
                f"{base}/{waba_id}/phone_numbers", headers=headers, params={"fields": "id"}
            )
        except httpx.HTTPError as exc:
            raise WhatsAppOwnershipError(f"could not reach the Graph API: {exc}") from exc

        if waba_response.status_code != 200:
            log.warning("whatsapp_provider.waba_unverified", status=waba_response.status_code)
            raise WhatsAppOwnershipError(
                "this access token could not list that WhatsApp Business Account's numbers"
            )
        waba_body = _json_object(waba_response)
        numbers = (waba_body or {}).get("data")
        listed = isinstance(numbers, list) and any(
            isinstance(entry, dict) and str(entry.get("id")) == str(phone_number_id)
            for entry in numbers
        )
        if not listed:
            raise WhatsAppOwnershipError(
                "that phone number is not listed under the given WhatsApp Business Account"
            )
    finally:
        if owns_client:
            await client.aclose()


def _json_object(response: httpx.Response) -> dict[str, object] | None:
    """`response.json()`, or `None` for anything that is not a JSON object — never raised past
    this function, since a malformed body is exactly as unproven as an honest refusal."""
    if len(response.content) > _MAX_RESPONSE_BYTES:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None
