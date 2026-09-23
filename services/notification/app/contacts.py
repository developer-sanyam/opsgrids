"""Resolving *who sent this* — a phone number becomes a contact id, or it does not.

The reverse of `app/targets.py`. That module asks "what is this contact's number" to send a
proactive message; this asks "which contact holds this number" to attribute an inbound one,
against customer-service's `POST .../contacts:resolve` — the keyed-blind-index lookup that reads
no PII and writes no sensitive-read audit row (see that endpoint's docstring).

**Degrades to unknown sender on anything short of a clean match.** No CRM entitlement, customer-
service unreachable, a timeout, an unexpected status — all of them come back `None`, logged, and
never raised. That is deliberately the opposite of `targets.resolve_contact_target`, which raises
`TargetUnavailable` so the broker retries: there, a customer is owed a notification and silence
would mean they never hear it. Here, the conversation and its message are already committed by
the time this runs (`app/consumers.py`'s `fold_inbound` calls it *after* the webhook fold, in its
own idempotent step) — there is nothing to roll back, and a CRM that is not there is not a
customer's fault to retry into existence. A conversation left with `contact_id = None` still
works: the inbox shows the phone number instead of a name, and any later run of the consumer
(a redelivery, an operator's manual retry) resolves it as soon as customer-service can answer.

Nothing in this module logs a phone number, for the same reason `targets.py` does not: it is one
of the few places on this side of the platform that holds one in the clear.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("notification.contacts")

#: The audience the token is addressed to. A token minted for customer cannot be replayed
#: against another service.
CUSTOMER_AUDIENCE: Final = "customer"

#: `(organization_id, phone_e164) -> contact_id or None`. A callable rather than a class so tests
#: can substitute one without a network, matching `targets.TargetResolver`.
ContactResolver = Callable[[str, str], Awaitable[str | None]]


async def resolve_contact_by_phone(
    settings: Settings,
    organization_id: str,
    phone: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Ask customer-service which contact holds `phone`. Never raises — see the module docstring
    for why silence is the right degradation here and not in `targets.py`."""
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
        return None
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        # Not entitled, not deployed reachable, a rejected token, a 5xx — every one of them is
        # "customer-service could not answer", and every one degrades the same way here.
        log.warning("contacts.refused", status=response.status_code)
        return None

    contact_id = response.json().get("contact_id")
    log.info("contacts.resolved", matched=contact_id is not None)
    return str(contact_id) if contact_id else None


def make_contact_resolver(settings: Settings) -> ContactResolver:
    """The resolver the consumer folds with — each call opens and closes its own client."""

    async def _resolve(organization_id: str, phone: str) -> str | None:
        return await resolve_contact_by_phone(settings, organization_id, phone)

    return _resolve
