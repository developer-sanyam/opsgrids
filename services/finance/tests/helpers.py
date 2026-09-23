"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.
"""

from __future__ import annotations

import uuid

from app.config import settings
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.internal_auth import mint_service_token

FINANCE_READS = (
    "finance.billing.invoice.read",
    "finance.billing.payment.read",
    "finance.billing.customer.read",
    "finance.billing.purchase.read",
    "finance.reports.summary.read",
    "finance.reports.ledger.read",
    "finance.compliance.einvoice.read",
    "finance.compliance.registration.read",
    "finance.compliance.ims.read",
    "finance.compliance.returns.read",
    "finance.compliance.eway.read",
    "finance.compliance.withholding.read",
    "finance.compliance.itc.read",
)

#: The writes on the public finance surface. Kept apart from the reads so a test can hand a caller
#: the whole read book without also handing them the ability to change who the organization files
#: as, to accept a supplier's claim on it, or to tell a government what a tax period contained.
FINANCE_WRITES = (
    "finance.compliance.registration.write",
    "finance.compliance.ims.decide",
    "finance.compliance.returns.generate",
    "finance.compliance.returns.file",
    "finance.billing.invoice.issue",
    "finance.billing.payment.record",
    "finance.billing.customer.write",
    "finance.billing.purchase.write",
)

FINANCE_PERMISSIONS = FINANCE_READS + FINANCE_WRITES


def service_token_headers(*, audience: str | None = None) -> dict[str, str]:
    """A **real** short-lived service token addressed to finance — what an internal caller sends.

    Minted rather than stubbed so `require_service_token` is exercised for real, including the
    audience binding: a token minted for another service must not open this one's internal API.
    """
    cfg = settings()
    token = mint_service_token(cfg, audience=audience or cfg.service_name, caller="tests")
    return {"X-Internal-Token": token}


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
    """A context holding every finance permission at ORG — what the demo seed and rollups run
    under. The seed sets the org's GST registration, so it needs the write too."""
    principal = principal_for(org_id, [f"{p}:ORG" for p in FINANCE_PERMISSIONS])
    return ScopeContext(
        principal=principal, scope=Scope.ORG, permission="finance.billing.invoice.read"
    )
