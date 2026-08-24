"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.
"""

from __future__ import annotations

import uuid

from app.config import settings
from app.schemas import UsageEventIn
from platform_core.auth import Principal
from platform_core.internal_auth import mint_service_token

#: What the product gateway stamps on a customer's token.
APP_AUDIENCE = "app"

#: The permission the customer usage read requires — a real, seeded `core` permission (the same
#: one that reads product activations), reused rather than minted so a real token carries it.
USAGE_READ_PERM = "core.product.activation.read"


class FakeRedis:
    """Enough Redis for the app factory's readiness check, if it is ever switched on here."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def customer_principal(org_id: uuid.UUID, *, perms: list[str] | None = None) -> Principal:
    """A customer as the product gateway would have forwarded them.

    Holds the usage-read permission at ORG by default, so a test can vary the organization while
    keeping the permission fixed — which is what the cross-tenant read test needs. Pass
    ``perms=[]`` for the negative-authz case.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        audience=APP_AUDIENCE,
        entitlements=["core"],
        perms=perms if perms is not None else [f"{USAGE_READ_PERM}:ORG"],
    )


def service_token_header(*, caller: str = "product", audience: str = "metering") -> dict[str, str]:
    """A minted service token addressed to metering, as a product service would present it."""
    token = mint_service_token(settings(), audience=audience, caller=caller)
    return {"X-Internal-Token": token}


def usage_event(
    org_id: uuid.UUID,
    *,
    meter_key: str = "core.api_call",
    quantity: int = 1,
    unit: str = "api_call",
    idempotency_key: str | None = None,
    is_billable: bool = False,
    subject_ref: uuid.UUID | None = None,
) -> UsageEventIn:
    """One usage event for `org_id`, with a random idempotency key unless one is given."""
    return UsageEventIn(
        organization_id=org_id,
        meter_key=meter_key,
        quantity=quantity,
        unit=unit,
        idempotency_key=idempotency_key or uuid.uuid4().hex,
        is_billable=is_billable,
        subject_ref=subject_ref,
    )
