"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so
importing *from* it is fragile — this module is a plain import that works the same way
everywhere.
"""

from __future__ import annotations

import uuid

from app.models import Membership
from platform_core.auth import Principal, Scope, ScopeContext


class FakeRedis:
    """Enough Redis for the permission-version counter.

    A grant change bumps `pv:<org>` and **raises if it cannot** — losing the bump would
    leave a revoked role working until its token expired. That makes Redis a hard
    dependency of every write that changes what somebody may do, so the tests need a
    stand-in rather than an absent client.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        #: Keys that must raise instead of incrementing — a test's way of simulating the
        #: Redis outage `bump_permission_version` is required to fail loudly on rather
        #: than swallow (ADR-0007 §5).
        self.poisoned: set[str] = set()

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def incr(self, key: str) -> int:
        if key in self.poisoned:
            raise ConnectionError(f"simulated redis outage for {key!r}")
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)

    async def aclose(self) -> None:
        return None


def principal_for(
    org_id: uuid.UUID,
    membership: Membership,
    perms: list[str],
    *,
    locations: list[str] | None = None,
) -> Principal:
    """The principal the gateway would have forwarded for this member."""
    return Principal(
        sub=membership.user_id,
        organization_id=org_id,
        membership_id=membership.id,
        subject_ref=membership.id,
        perms=perms,
        locations=locations or [],
    )


def ctx_for(
    org_id: uuid.UUID,
    membership: Membership,
    permission: str,
    scope: Scope,
    *,
    locations: list[str] | None = None,
) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(
        org_id, membership, [f"{permission}:{scope.value}"], locations=locations
    )
    return ScopeContext(principal=principal, scope=scope, permission=permission)
