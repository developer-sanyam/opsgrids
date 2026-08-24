"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.

`customer_principal` forges a token the way the gateway tests forge theirs: it carries the
platform permission the subscription routes reuse (`core.product.activation.write`/`read`) at ORG
scope. `FakeCatalog` stands in for the catalog pin lookup so no test makes a real HTTP call.
"""

from __future__ import annotations

import uuid

from app.schemas import CatalogPlanVersion, CatalogPrice
from platform_core.auth import Principal

APP_AUDIENCE = "app"

#: The write and read permissions the subscription routes reuse from the core catalogue.
MANAGE = "core.product.activation.write"
READ = "core.product.activation.read"


def customer_principal(org_id: uuid.UUID, *, perms: list[str] | None = None) -> Principal:
    """A customer as the product gateway would have forwarded them, org-owner by default.

    Holds both the manage and read grants at ORG so a test can drive the full lifecycle; pass
    `perms=[]` (or a narrower list) for the negative-authz cases.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        audience=APP_AUDIENCE,
        entitlements=["core"],
        perms=perms if perms is not None else [f"{MANAGE}:ORG", f"{READ}:ORG"],
    )


def make_version(
    *,
    version_id: uuid.UUID | None = None,
    product_key: str = "hrms",
    currency: str = "USD",
    amount_minor: int = 1000,
    state: str = "published",
    billing_interval: str = "month",
    restricted_to_org_id: uuid.UUID | None = None,
    price_id: uuid.UUID | None = None,
) -> CatalogPlanVersion:
    """A catalog plan version as the internal pin lookup would return it."""
    vid = version_id or uuid.uuid4()
    return CatalogPlanVersion(
        id=vid,
        plan_id=uuid.uuid4(),
        product_key=product_key,
        version_no=1,
        state=state,
        billing_interval=billing_interval,
        currency=currency,
        restricted_to_org_id=restricted_to_org_id,
        prices=[
            CatalogPrice(
                id=price_id or uuid.uuid4(),
                kind="flat",
                amount_minor=amount_minor,
                currency=currency,
            )
        ],
    )


class FakeCatalog:
    """A dict-backed stand-in for `HttpPlanCatalog`. Register versions, then resolve them."""

    def __init__(self) -> None:
        self._versions: dict[uuid.UUID, CatalogPlanVersion] = {}

    def add(self, version: CatalogPlanVersion) -> CatalogPlanVersion:
        self._versions[version.id] = version
        return version

    async def get_plan_version(self, plan_version_id: uuid.UUID) -> CatalogPlanVersion | None:
        return self._versions.get(plan_version_id)
