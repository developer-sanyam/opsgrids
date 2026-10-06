"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so
importing *from* it is fragile — this module is a plain import that works the same way
everywhere.

The tokens minted here are forged directly, the way the gateway tests forge theirs: the real
`stf` staff-token minter does not exist yet (it arrives with the operator console, a later
phase). What matters for this service is the audience the operator gateway stamps, so these
principals carry it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from app.domain import RegisteredProduct
from app.registry import ProductSnapshot
from platform_core.auth import Principal

#: What the product gateway stamps on a customer's token.
APP_AUDIENCE = "app"
#: What the operator gateway stamps. `require_operator` accepts only this.
OPERATOR_AUDIENCE = "operator"


def customer_principal(org_id: uuid.UUID, *, perms: list[str] | None = None) -> Principal:
    """A customer as the product gateway would have forwarded them.

    Holds `core.product.catalogue.read` by default — the marketplace-browse permission the
    plan-read routes reuse — so a test can vary the organization while keeping the permission
    fixed, which is what the bespoke-visibility test needs.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        audience=APP_AUDIENCE,
        entitlements=["core"],
        perms=perms if perms is not None else ["core.product.catalogue.read:ORG"],
    )


def operator_principal(
    *, staff: bool = True, roles: list[str] | None = None, amr: list[str] | None = None
) -> Principal:
    """A staff operator as the operator gateway would have forwarded them.

    `audience="operator"` is the marker `require_operator` checks. `staff` toggles the audience
    so the same helper produces the negative case — a non-operator (product) token that must be
    refused with 404. Defaults to `["owner"]`: catalog's writes are owner-only (ADR-0003
    addendum), and every existing authoring test predates the role check, so the default keeps
    them exercising the case they were written for rather than the one the role gate adds.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        audience=OPERATOR_AUDIENCE if staff else APP_AUDIENCE,
        roles=roles if roles is not None else ["owner"],
        # Every real operator token carries `otp` once sign-in completes (MFA is mandatory for
        # staff, ADR-0003 addendum); defaulted on here so existing role/audience tests don't also
        # have to spell out the amr axis.
        amr=amr if amr is not None else ["pwd", "otp"],
    )


class FakeRegistry:
    """A dict-backed stand-in for product-service's registry read.

    `default()` mirrors the seeded registry: `core` and `notification` are platform, `suite` is
    the bundle, and the sellable products are plain. `with_bundle` adds an ad-hoc bundle key and
    its members, for tests that author their own ladders under throwaway product keys.
    """

    def __init__(self, entries: Iterable[RegisteredProduct]) -> None:
        self._entries = {e.key: e for e in entries}
        self.calls = 0

    @classmethod
    def default(cls) -> FakeRegistry:
        plain = ["hrms", "crm", "finance", "support", "operations", "engage"]
        return cls(
            [
                RegisteredProduct("core", is_platform=True),
                RegisteredProduct("notification", is_platform=True),
                RegisteredProduct("suite", is_bundle=True),
                # Seeded `soon`: no service behind it yet, so no bundle may sell it.
                RegisteredProduct("projects", status="soon"),
                *(RegisteredProduct(key) for key in plain),
            ]
        )

    def with_bundle(self, bundle_key: str, members: Iterable[str]) -> FakeRegistry:
        return FakeRegistry(
            [
                *self._entries.values(),
                RegisteredProduct(bundle_key, is_bundle=True),
                *(RegisteredProduct(m) for m in members if m not in self._entries),
            ]
        )

    async def products(self) -> dict[str, RegisteredProduct]:
        self.calls += 1
        return dict(self._entries)

    def snapshot(self) -> ProductSnapshot:
        """The registry as an operator route reads it before its transaction opens — what the
        service layer is handed instead of the client (D3 #10)."""
        self.calls += 1
        return ProductSnapshot(products=dict(self._entries))
