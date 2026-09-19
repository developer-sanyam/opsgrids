"""Resolving *who* to message — a contact id becomes an address, or it does not.

This is the seam the WhatsApp pipeline was missing. A rule can already say "send this template
when that event happens", but a proactive message needs a real phone number, and the CRM keeps
contacts' numbers sealed (`platform_core.pii`). Notification does not hold the key and must not:
decryption belongs to the service that owns the data.

So this asks. `GET /internal/v1/organizations/{org}/contacts/{id}/messaging-target` on
customer-service, under a short-lived token addressed to customer by name — the same shape every
cross-service read on this platform takes, and the rule (ADR-0000 §10) that keeps a later
database-per-service split a configuration change. Customer decrypts, audits the read, and hands
back an address. The automation engine never sees a key.

**Two outcomes, and conflating them would be the bug.**

  `None`              the contact has no sealed number. Ordinary, and most contacts are like
                      this. The rule skips, exactly as it does for an event carrying no
                      recipient. Retrying would never help.

  `TargetUnavailable` customer-service could not answer — unreachable, timeout, 5xx. The event
                      must be *redelivered*, not skipped, because the notification is still owed.
                      Raising is what makes the broker try again.

Console's price resolver tolerates its downstream being absent by returning zero, because a
missing price only understates a metric. A missing phone number means a customer is not told
something, so silence is not an acceptable degradation here.

Nothing in this module logs a phone number. It is the one place on this side of the platform
that holds one, and a log line is the easiest way for PII to escape a service.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("notification.targets")

#: The audience the token is addressed to. A token minted for customer cannot be replayed
#: against another service.
CUSTOMER_AUDIENCE: Final = "customer"


class TargetUnavailable(Exception):
    """Customer-service could not be asked. Transient — the caller should let the event retry."""


#: `(organization_id, contact_id, channel) -> address or None`. A callable rather than a class so
#: tests can substitute one without a network, matching how console injects its price resolver.
TargetResolver = Callable[[str, str, str], Awaitable[str | None]]


async def resolve_contact_target(
    settings: Settings,
    organization_id: str,
    contact_id: str,
    channel: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Ask customer-service for one contact's address on one channel.

    Returns the address, or None when the contact has no sealed number. Raises
    `TargetUnavailable` when the question could not be answered — see the module docstring for
    why those two cases must not collapse into one.
    """
    url = (
        f"{settings.customer_internal_url}/internal/v1/organizations/{organization_id}"
        f"/contacts/{contact_id}/messaging-target"
    )
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=CUSTOMER_AUDIENCE, caller=settings.service_name
        )
    }

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.customer_timeout_seconds)
    try:
        response = await client.get(url, params={"channel": channel}, headers=headers)
    except httpx.HTTPError as exc:
        raise TargetUnavailable(f"customer-service unreachable: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code == 404:
        # The contact does not exist in this organization — including the case where it exists
        # in another, which customer deliberately reports as not-found rather than forbidden.
        # Permanent: no retry will conjure the contact.
        log.info("target.contact_unknown", contact_id=contact_id, channel=channel)
        return None
    if response.status_code >= 500:
        raise TargetUnavailable(f"customer-service returned {response.status_code}")
    if response.status_code != 200:
        # 4xx other than 404: a bad channel, a rejected token. Our mistake, not a blip, so it
        # is permanent — retrying a malformed request forever only hides it.
        log.error(
            "target.refused",
            contact_id=contact_id,
            channel=channel,
            status=response.status_code,
        )
        return None

    address = response.json().get("phone")
    log.info(
        "target.resolved",
        contact_id=contact_id,
        channel=channel,
        # Whether, never what.
        resolved=address is not None,
    )
    return str(address) if address else None


def make_resolver(settings: Settings) -> TargetResolver:
    """The resolver the consumer folds with — each call opens and closes its own client."""

    async def _resolve(organization_id: str, contact_id: str, channel: str) -> str | None:
        return await resolve_contact_target(settings, organization_id, contact_id, channel)

    return _resolve
