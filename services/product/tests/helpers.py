"""Helpers shared by product's test modules.

`real_envelope` builds a `commerce.subscription.*` envelope the way the relay puts one on the wire:
an outbox row's fields through `build_envelope`, the platform's own function, so nothing about the
wire shape is restated in a test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from platform_core.events import build_envelope

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def real_envelope(
    org_id: uuid.UUID,
    *,
    state: str,
    product_key: str = "hrms",
    members: list[str] | None = None,
    minutes: int = 0,
    subscription_id: uuid.UUID | None = None,
    event_type: str | None = None,
    carry_subscription_id: bool = True,
) -> dict[str, Any]:
    """A `commerce.subscription.*` envelope as the relay puts it on the wire.

    The row is an outbox row in everything the envelope reads, passed through `build_envelope` —
    the function the real relay calls — so `occurred_at` is the row's `created_at` and the payload
    is the lifecycle payload subscription stages. Nothing about the wire shape is restated here.
    """
    sid = subscription_id or uuid.uuid4()
    payload: dict[str, Any] = {
        "organization_id": str(org_id),
        "subscription_id": str(sid),
        "product_key": product_key,
        "plan_version_id": str(uuid.uuid4()),
        "state": state,
        "trial_ends_at": None,
        "entitled_products": members if members is not None else [product_key],
    }
    if not carry_subscription_id:
        del payload["subscription_id"]
    row = SimpleNamespace(
        id=uuid.uuid4(),
        event_type=event_type or f"commerce.subscription.{state}",
        event_version=1,
        organization_id=org_id,
        aggregate_type="subscription",
        aggregate_id=sid,
        created_at=T0 + timedelta(minutes=minutes),
        correlation_id=None,
        causation_id=None,
        actor={"type": "service", "id": "subscription-billing-run"},
        payload=payload,
    )
    return build_envelope(row, producer="subscription")
