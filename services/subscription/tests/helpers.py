"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.

`customer_principal` forges a token the way the gateway tests forge theirs: it carries the
platform permission the subscription routes reuse (`core.product.activation.write`/`read`) at ORG
scope. `FakeCatalog` stands in for the catalog pin lookup so no test makes a real HTTP call.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.catalog import catalog_unavailable
from app.registry import RegisteredProduct
from app.schemas import CatalogFeature, CatalogOfferableVersion, CatalogPlanVersion, CatalogPrice
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
    features: dict[str, Any] | None = None,
    plan_id: uuid.UUID | None = None,
    plan_name: str | None = None,
    version_no: int = 1,
) -> CatalogPlanVersion:
    """A catalog plan version as the internal pin lookup would return it. Each call is its own
    plan unless `plan_id` names one, so two versions only supersede each other when a test says
    they are the same plan."""
    vid = version_id or uuid.uuid4()
    return CatalogPlanVersion(
        id=vid,
        plan_id=plan_id or uuid.uuid4(),
        product_key=product_key,
        plan_name=plan_name if plan_name is not None else f"{product_key.title()} plan",
        version_no=version_no,
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
        features=[CatalogFeature(feature_key=k, value=v) for k, v in (features or {}).items()],
    )


def make_bundle(
    members: list[str], *, tier: str, amount_minor: int, **kwargs: Any
) -> CatalogPlanVersion:
    """A `suite` rung as catalog seeds it: a plan version whose features name its members."""
    return make_version(
        product_key="suite",
        amount_minor=amount_minor,
        features={"bundled_products": members, "bundle_tier": tier},
        **kwargs,
    )


class FakeCatalog:
    """A dict-backed stand-in for `HttpPlanCatalog`. Register versions, then resolve them.

    `offerable_versions` applies catalog's own rule (`selectors.offerable_versions`) to what is
    registered: the product's published versions on plans not retired, public or restricted to the
    asking organization, dropping a version when a higher-numbered one of the same plan has the
    same audience, cadence and currency. `offerable_calls` counts the reads, so a test can show a
    refused request never asked.
    """

    def __init__(self) -> None:
        self._versions: dict[uuid.UUID, CatalogPlanVersion] = {}
        self.retired_plans: set[uuid.UUID] = set()
        self.offerable_calls = 0
        #: Set to make the offerable read fail the way an unreachable catalog does.
        self.offerable_down = False

    def add(self, version: CatalogPlanVersion) -> CatalogPlanVersion:
        self._versions[version.id] = version
        return version

    async def get_plan_version(self, plan_version_id: uuid.UUID) -> CatalogPlanVersion | None:
        return self._versions.get(plan_version_id)

    async def offerable_versions(
        self, organization_id: uuid.UUID, product_key: str
    ) -> list[CatalogOfferableVersion]:
        self.offerable_calls += 1
        if self.offerable_down:
            raise catalog_unavailable("catalog is unavailable; cannot check what is on offer")
        visible = [
            v
            for v in self._versions.values()
            if v.product_key == product_key
            and v.state == "published"
            and v.plan_id not in self.retired_plans
            and v.restricted_to_org_id in (None, organization_id)
        ]
        newest: dict[tuple[Any, ...], int] = {}
        for v in visible:
            key = (v.plan_id, v.restricted_to_org_id, v.billing_interval, v.currency)
            newest[key] = max(newest.get(key, 0), v.version_no)
        return [
            CatalogOfferableVersion(
                plan_id=v.plan_id,
                plan_code=(v.plan_name or "plan").lower().replace(" ", "-"),
                plan_name=v.plan_name or "Plan",
                product_key=product_key,
                plan_version_id=v.id,
                version_no=v.version_no,
                state=v.state,
                billing_interval=v.billing_interval,
                currency=v.currency,
                restricted_to_org_id=v.restricted_to_org_id,
                prices=v.prices,
                features=v.features,
            )
            for v in visible
            if v.version_no
            == newest[(v.plan_id, v.restricted_to_org_id, v.billing_interval, v.currency)]
        ]


class FakeRegistry:
    """product-service's registry as the bundle sale path reads it: `suite` a bundle, `core` a
    platform product, the trio's members launched, `projects` still `soon`."""

    def __init__(self, overrides: dict[str, RegisteredProduct] | None = None) -> None:
        self._entries = {
            "core": RegisteredProduct("core", is_platform=True),
            "suite": RegisteredProduct("suite", is_bundle=True),
            "hrms": RegisteredProduct("hrms"),
            "crm": RegisteredProduct("crm"),
            "finance": RegisteredProduct("finance"),
            "projects": RegisteredProduct("projects", status="soon"),
            **(overrides or {}),
        }
        self.calls = 0

    async def products(self) -> dict[str, RegisteredProduct]:
        self.calls += 1
        return dict(self._entries)
