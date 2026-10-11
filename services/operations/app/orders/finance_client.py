"""Operations' door into `services/finance`'s identity bridge (INVENTORY-01).

An order names its buyer by CRM contact (`Order.customer_id`, a `services/customer` id); the
invoice request must name finance's own billed party. Finance owns that mapping and answers it at
`POST /internal/v1/organizations/{org}/customers:resolve-for-crm-contact`, creating the billed
party the first time and returning the same one on every later call — so a repeat buyer is one
finance customer, never one per order.

Called only from `app.consumers.order_invoice_due`, with no database transaction open
(`docs/io-outside-transactions.md`). Never logs or relays a response body.
"""

from __future__ import annotations

import uuid
from typing import Final

import httpx

from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("operations.orders.finance_client")

FINANCE_AUDIENCE: Final = "finance"


class FinanceUnavailable(Exception):
    """Finance could not answer (unreachable, a timeout, a 5xx, a refused token). The caller lets
    it propagate so the broker retries the message, then dead-letters it — never a skipped
    invoice."""


async def resolve_finance_customer(
    cfg: Settings,
    organization_id: uuid.UUID,
    crm_contact_id: uuid.UUID,
    *,
    client: httpx.AsyncClient | None = None,
) -> uuid.UUID | None:
    """Finance's billed-party id for this CRM contact, or None when finance answered that the
    contact is not part of this organization (404) — a definite answer, not an outage."""
    url = (
        f"{cfg.finance_internal_url}/internal/v1/organizations/{organization_id}"
        "/customers:resolve-for-crm-contact"
    )
    headers = {
        "X-Internal-Token": mint_service_token(
            cfg, audience=FINANCE_AUDIENCE, claims={"org": str(organization_id)}
        )
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=cfg.finance_timeout_seconds)
    try:
        response = await client.post(
            url, json={"crm_contact_id": str(crm_contact_id)}, headers=headers
        )
    except httpx.HTTPError as exc:
        log.warning("orders.finance_client.unreachable", error=type(exc).__name__)
        raise FinanceUnavailable("finance could not be reached") from None
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        log.warning("orders.finance_client.refused", status=response.status_code)
        raise FinanceUnavailable(f"finance answered {response.status_code}")
    try:
        return uuid.UUID(str(response.json()["customer_id"]))
    except (ValueError, KeyError, TypeError):
        log.warning("orders.finance_client.unparseable_response")
        raise FinanceUnavailable("finance answered unexpectedly") from None
