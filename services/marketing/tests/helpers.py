"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.
"""

from __future__ import annotations

import uuid

from platform_core.auth import Principal, Scope, ScopeContext


class FakeRedis:
    """Enough Redis for the app factory's `redis=True` readiness check.

    This service does not bump a permission version, but the factory opens and pings Redis for any
    service that declares it, so the tests need a stand-in rather than an absent client.
    """

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


def principal_for(
    org_id: uuid.UUID,
    perms: list[str],
    *,
    entitlements: list[str] | None = None,
) -> Principal:
    """The principal the gateway would have forwarded for a marketing user.

    Marketing has no per-person `subject_ref`: its data is org-wide analytics read at ORG scope, so
    the principal carries the org, the resolved permission grants, and the product entitlement, and
    nothing person-specific.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        perms=perms,
        entitlements=entitlements if entitlements is not None else ["marketing"],
    )


def ctx_for(
    org_id: uuid.UUID,
    permission: str,
    scope: Scope,
    *,
    entitlements: list[str] | None = None,
) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(org_id, [f"{permission}:{scope.value}"], entitlements=entitlements)
    return ScopeContext(principal=principal, scope=scope, permission=permission)
