"""Resolving *who sent this* — a phone number becomes a contact id, or it does not.

The reverse of `app/targets.py`. That module asks "what is this contact's number" to send a
proactive message; this asks "which contact holds this number" to attribute an inbound one,
against customer-service's `POST .../contacts:resolve` — the keyed-blind-index lookup that reads
no PII and writes no sensitive-read audit row (see that endpoint's docstring).

**Never raises, but never collapses "no match" into "could not ask" either.** No CRM entitlement,
customer-service unreachable, a timeout, an unrecognisable body — none of them raise, because
`app/consumers.py`'s `fold_inbound_contact` runs after the webhook fold has already durably
committed the conversation and its message; there is nothing to roll back, and a CRM that is not
there is not a customer's fault to retry into existence the way `targets.
resolve_contact_target`'s `TargetUnavailable` retries an owed notification.

But those two outcomes are not the same fact, and conflating them was a bug: a stranger messaging
the business (`answered=True, contact_id=None`) is a **definite** answer that should be written and
never asked again, while a CRM that could not be reached (`answered=False`) is **no answer at
all** and must leave whatever the conversation already held alone — including a contact id an
earlier, successful resolution wrote. `ContactResolution` is the tri-state that keeps a transient
outage from silently erasing a match `fold_inbound_contact` already made.

Nothing in this module logs a phone number, for the same reason `targets.py` does not: it is one
of the few places on this side of the platform that holds one in the clear.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("notification.contacts")

#: The audience the token is addressed to. A token minted for customer cannot be replayed
#: against another service.
CUSTOMER_AUDIENCE: Final = "customer"


@dataclass(frozen=True, slots=True)
class ContactResolution:
    """The outcome of asking customer-service which contact holds a phone number.

    `answered=False` means the question could not be asked at all — `contact_id` is meaningless
    on that branch and the caller must not act on it. `answered=True, contact_id=None` is the
    other, easily confused case: the question **was** answered, and the answer is "nobody".
    """

    answered: bool
    contact_id: str | None = None


#: `(organization_id, phone_e164) -> ContactResolution`. A callable rather than a class so tests
#: can substitute one without a network, matching `targets.TargetResolver`.
ContactResolver = Callable[[str, str], Awaitable[ContactResolution]]

_UNREACHABLE: Final = ContactResolution(answered=False)


async def resolve_contact_by_phone(
    settings: Settings,
    organization_id: str,
    phone: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> ContactResolution:
    """Ask customer-service which contact holds `phone`. Never raises — see the module docstring
    for the tri-state this returns instead."""
    url = (
        f"{settings.customer_internal_url}/internal/v1/organizations/{organization_id}"
        "/contacts:resolve"
    )
    headers = {
        "X-Internal-Token": mint_service_token(
            settings, audience=CUSTOMER_AUDIENCE, caller=settings.service_name
        )
    }

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.customer_timeout_seconds)
    try:
        response = await client.post(url, json={"phone": phone}, headers=headers)
    except httpx.HTTPError as exc:
        log.warning("contacts.unreachable", reason=str(exc))
        return _UNREACHABLE
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        # Not entitled, not deployed reachable, a rejected token, a 5xx — every one of them is
        # "customer-service could not answer", never "the answer is nobody".
        log.warning("contacts.refused", status=response.status_code)
        return _UNREACHABLE

    try:
        body = response.json()
    except ValueError:
        # A 200 that is not JSON is not an answer either — a caller reading `contact_id` off it
        # would crash the consumer transaction on a malformed body it cannot control.
        log.warning("contacts.unparseable_response")
        return _UNREACHABLE

    if not isinstance(body, dict):
        log.warning("contacts.unparseable_response")
        return _UNREACHABLE

    contact_id = body.get("contact_id")
    log.info("contacts.resolved", matched=contact_id is not None)
    return ContactResolution(answered=True, contact_id=str(contact_id) if contact_id else None)


def make_contact_resolver(settings: Settings) -> ContactResolver:
    """The resolver the consumer folds with — each call opens and closes its own client."""

    async def _resolve(organization_id: str, phone: str) -> ContactResolution:
        return await resolve_contact_by_phone(settings, organization_id, phone)

    return _resolve
