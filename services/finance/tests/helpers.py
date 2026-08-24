"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.
"""

from __future__ import annotations

import uuid

from platform_core.auth import Principal, Scope, ScopeContext

FINANCE_READS = (
    "finance.billing.invoice.read",
    "finance.billing.payment.read",
    "finance.billing.customer.read",
    "finance.reports.summary.read",
    "finance.reports.ledger.read",
)


class FakeRedis:
    """Enough Redis for the app factory's `redis=True` readiness check."""

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
    """The principal the gateway would have forwarded for a finance caller.

    Finance is an ORG-level product, so there is no product-specific `subject_ref` for its
    reads; the grant scope is what matters."""
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        perms=perms,
        entitlements=entitlements if entitlements is not None else ["finance"],
    )


def ctx_for(
    org_id: uuid.UUID,
    permission: str = "finance.billing.invoice.read",
    scope: Scope = Scope.ORG,
) -> ScopeContext:
    """A scope context as `require(...)` would have built it — ORG by default, the only scope
    finance grants."""
    principal = principal_for(org_id, [f"{permission}:{scope.value}"])
    return ScopeContext(principal=principal, scope=scope, permission=permission)


def admin_ctx(org_id: uuid.UUID) -> ScopeContext:
    """A context holding every finance read at ORG — what the demo seed and rollups run under."""
    principal = principal_for(org_id, [f"{p}:ORG" for p in FINANCE_READS])
    return ScopeContext(
        principal=principal, scope=Scope.ORG, permission="finance.billing.invoice.read"
    )
