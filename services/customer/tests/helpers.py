"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so
importing *from* it is fragile — this module is a plain import that works the same way
everywhere.

A CRM caller's `subject_ref` is their rep id: OWN scope reads the companies / contacts
whose `owner_id` is that id. The helpers take a bare `subject_ref` uuid.
"""

from __future__ import annotations

import asyncio
import uuid

from platform_core.auth import Principal, Scope, ScopeContext


class FakeRedis:
    """Enough Redis for the reveal budget (`EVAL` of `services._REVEAL_BUDGET_SCRIPT`), the
    single commands a test may use to stand in for the old two-step check, and the readiness ping.

    Every call the code under test sends is one **round trip**, counted in `round_trips`: the
    budget check is meant to be exactly one. `poisoned` makes every call raise, which is how a test
    simulates the outage the check is required to fail closed on rather than swallow.

    `hang_on` makes the Nth round trip (1-based) never answer — the request that "timed out". With
    `hang_after_apply` the server had already run it (the reply was lost); without, it never got
    there. Both are what a client-side timeout can leave behind, and a test asserts that neither
    leaves a counter without an expiry.

    `eval` runs a Python equivalent of the one script it knows. It is only as good as it agrees
    with the real one, which `tests/test_reveal_budget_redis.py` checks against a real server."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.poisoned = False
        self.round_trips = 0
        self.hang_on: int | None = None
        self.hang_after_apply = False

    def _check(self) -> None:
        if self.poisoned:
            raise ConnectionError("simulated redis outage")

    async def _round_trip(self, apply):  # type: ignore[no-untyped-def]
        self.round_trips += 1
        self._check()
        hung = self.hang_on == self.round_trips
        if hung and not self.hang_after_apply:
            await asyncio.sleep(3600)  # the request never reached the server
        result = apply()
        if hung:
            await asyncio.sleep(3600)  # the server ran it; the reply never came back
        return result

    async def eval(self, script: str, numkeys: int, *args):  # type: ignore[no-untyped-def]
        from app.services import _REVEAL_BUDGET_SCRIPT

        assert script == _REVEAL_BUDGET_SCRIPT, "this fake only knows the reveal budget script"
        keys = [str(k) for k in args[:numkeys]]
        limits_and_ttls = [int(a) for a in args[numkeys:]]
        return await self._round_trip(lambda: self._budget(keys, limits_and_ttls))

    def _budget(self, keys: list[str], argv: list[int]) -> list[int]:
        burst_key, daily_key = keys
        burst_limit, daily_limit, burst_ttl, daily_ttl = argv
        burst, daily = self.store.get(burst_key, 0), self.store.get(daily_key, 0)
        if burst >= burst_limit:
            return [1, burst, daily]
        if daily >= daily_limit:
            return [2, burst, daily]
        burst = self.store[burst_key] = burst + 1
        self.expiries.setdefault(burst_key, burst_ttl)
        daily = self.store[daily_key] = daily + 1
        self.expiries.setdefault(daily_key, daily_ttl)
        return [0, burst, daily]

    async def incr(self, key: str) -> int:
        def _apply() -> int:
            self.store[key] = self.store.get(key, 0) + 1
            return self.store[key]

        return await self._round_trip(_apply)

    async def expire(self, key: str, seconds: int) -> bool:
        def _apply() -> bool:
            self.expiries[key] = seconds
            return True

        return await self._round_trip(_apply)

    async def ping(self) -> bool:
        return await self._round_trip(lambda: True)

    async def aclose(self) -> None:
        return None


#: The `amr` of a token from `POST /auth/mfa/step-up`: `otp` and the marker only that
#: endpoint mints. What the reveal routes require (ADR-0023). A session that gave a code at
#: sign-in has `otp` alone.
STEPPED_UP = ["otp", "stepup"]
LOGIN_OTP = ["pwd", "otp"]


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
