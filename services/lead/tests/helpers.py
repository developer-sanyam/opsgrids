"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.

A lead caller's `subject_ref` is their rep id: OWN scope reads/touches the leads whose
`owner_id` matches it. The helpers therefore take a bare `subject_ref` uuid, which the fixtures
set to a seeded lead's owner id.
"""

from __future__ import annotations

import uuid

from platform_core.auth import Principal, Scope, ScopeContext


class FakeRedis:
    """Enough Redis for `enforce_rate_limit` (`incr` + `expire`) and the readiness ping.

    `poisoned` makes every call raise, which is how a test simulates the outage the limiter is
    required to fail closed on rather than swallow."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.poisoned = False

    def _check(self) -> None:
        if self.poisoned:
            raise ConnectionError("simulated redis outage")

    async def incr(self, key: str) -> int:
        self._check()
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key: str, seconds: int) -> None:
        self._check()
        self.expiries[key] = seconds

    async def ping(self) -> bool:
        self._check()
        return True

    async def aclose(self) -> None:
        return None


def principal_for(
    org_id: uuid.UUID,
    perms: list[str],
    *,
    subject_ref: uuid.UUID | None = None,
    entitlements: list[str] | None = None,
    locations: list[str] | None = None,
    amr: list[str] | None = None,
    sub: uuid.UUID | None = None,
    session_id: str | None = None,
) -> Principal:
    """The principal the gateway would have forwarded for a CRM caller. `sub` pins the user
    (a fresh one per call otherwise) and `session_id` is the token's `sid`."""
    return Principal(
        sub=sub or uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        subject_ref=subject_ref,
        perms=perms,
        entitlements=entitlements if entitlements is not None else ["crm"],
        locations=locations or [],
        amr=amr or [],
        session_id=session_id,
    )


def ctx_for(
    org_id: uuid.UUID,
    permission: str,
    scope: Scope,
    *,
    subject_ref: uuid.UUID | None = None,
    locations: list[str] | None = None,
) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(
        org_id, [f"{permission}:{scope.value}"], subject_ref=subject_ref, locations=locations
    )
    return ScopeContext(principal=principal, scope=scope, permission=permission)
