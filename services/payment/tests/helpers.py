"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.

`razorpay_webhook_body` builds a Razorpay-shaped webhook (the `notes` metadata payment stamps at
charge-create time is echoed back here, which is how a callback names its own tenant). `sign` is
provided by the adapter itself, so a test produces an authentic signature without a real Razorpay
and no live keys — the whole point of the stubbed seam.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.config import settings
from platform_core.internal_auth import mint_service_token


def service_token() -> str:
    """A short-lived service token addressed to payment — what billing would present to the
    internal charge endpoint."""
    return mint_service_token(settings(), audience="payment", caller="billing")


def razorpay_webhook_body(
    *,
    event: str,
    provider_payment_id: str,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    subscription_id: uuid.UUID | None = None,
    product_key: str | None = None,
    event_id: str | None = None,
    amount_minor: int = 1000,
    currency: str = "USD",
    error_description: str | None = None,
    order_id: str | None = None,
) -> bytes:
    """A Razorpay webhook body as raw bytes, shaped exactly as `parse_webhook` expects.

    `notes` carries the platform metadata Razorpay echoes on every callback about a payment; the
    tenant and charge are read from there during reconciliation.
    """
    notes: dict[str, str] = {
        "organization_id": str(organization_id),
        "charge_id": str(charge_id),
    }
    if subscription_id is not None:
        notes["subscription_id"] = str(subscription_id)
    if product_key is not None:
        notes["product_key"] = product_key

    entity: dict[str, Any] = {
        "id": provider_payment_id,
        "amount": amount_minor,
        "currency": currency,
        "notes": notes,
    }
    if error_description is not None:
        entity["error_description"] = error_description
    if order_id is not None:
        entity["order_id"] = order_id

    body = {
        "id": event_id or f"evt_{uuid.uuid4().hex[:16]}",
        "event": event,
        "payload": {"payment": {"entity": entity}},
    }
    return json.dumps(body).encode()
