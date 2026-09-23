"""A bundle's members are checked against the product registry when it is authored.

A subscription to a bundle entitles exactly the keys its `bundled_products` names. A typo there
is a customer who pays for three products and is entitled to two; a platform key is a charge for
something everybody already has; a nested bundle is a key the gateway routes nowhere. Each is
refused while the version is still a draft — once published it is immutable, and the only person
left to notice would be the customer.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, services
from app.api.admin import get_product_registry
from app.domain import RegisteredProduct
from app.main import app
from app.schemas import FeatureIn, PlanCreateIn, PriceIn, VersionCreateIn
from platform_core.auth import Principal, get_principal
from platform_core.errors import BusinessRuleProblem, UpstreamUnavailableProblem
from tests.helpers import FakeRegistry, operator_principal

pytestmark = pytest.mark.integration

REGISTRY = FakeRegistry.default()


def _problems(bundle_key: str, members: Any) -> list[str]:
    registry = {
        "core": RegisteredProduct("core", is_platform=True),
        "suite": RegisteredProduct("suite", is_bundle=True),
        "hrms": RegisteredProduct("hrms"),
        "crm": RegisteredProduct("crm"),
    }
    return domain.bundle_member_problems(registry.get(bundle_key), bundle_key, members, registry)


class TestTheRules:
    def test_a_list_of_registered_products_is_fine(self) -> None:
        assert _problems("suite", ["hrms", "crm"]) == []

    @pytest.mark.parametrize(
        ("members", "fragment"),
        [
            (["hrms", "hrsm"], "'hrsm' is not a registered product"),
            (["hrms", "core"], "'core' is a platform product"),
            (["hrms", "suite"], "'suite' is itself a bundle"),
            (["hrms", "hrms"], "'hrms' is named twice"),
            ([], "non-empty list"),
            ("hrms,crm", "non-empty list"),
            (["hrms", 7], "7 is not a product key"),
        ],
    )
    def test_each_way_a_member_list_goes_wrong_is_named(self, members: Any, fragment: str) -> None:
        assert any(fragment in p for p in _problems("suite", members)), _problems("suite", members)

    def test_only_a_bundle_product_may_declare_members(self) -> None:
        assert any("not a bundle product" in p for p in _problems("crm", ["hrms"]))

    def test_the_seeded_ladder_passes_its_own_validation(self) -> None:
        """Every rung migration f78c86fcec78 shipped names members the registry knows."""
        from tests.test_packaging import _seed_module

        seed = _seed_module()
        registry = {e.key: e for e in [*REGISTRY._entries.values()]}
        for _id, _version, key, value in seed.FEATURES:
            if key != "bundled_products":
                continue
            assert domain.bundle_member_problems(registry["suite"], "suite", value, registry) == []


async def _suite_plan(session: AsyncSession, code: str) -> Any:
    return await services.create_plan(
        session,
        operator_principal(),
        PlanCreateIn(product_key="suite", code=code, name=code.title()),
        request=None,
    )


def _bundle(members: Any) -> VersionCreateIn:
    return VersionCreateIn(
        currency="INR",
        prices=[PriceIn(kind="per_seat", amount_minor=80700, currency="INR", unit="seat")],
        features=[
            FeatureIn(feature_key="bundled_products", value=members),
            FeatureIn(feature_key="bundle_tier", value="trio"),
        ],
    )


class TestAuthoring:
    async def test_a_valid_bundle_authors(self, platform_session: AsyncSession) -> None:
        async with platform_session.begin():
            plan = await _suite_plan(platform_session, "trio-ok")
            version = await services.create_version(
                platform_session,
                operator_principal(),
                plan,
                _bundle(["hrms", "crm", "finance"]),
                request=None,
                registry=REGISTRY,
            )
        assert version.state == "draft"

    async def test_an_unknown_member_is_refused_before_anything_is_written(
        self, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            plan = await _suite_plan(platform_session, "trio-typo")
            plan_id = plan.id
        with pytest.raises(BusinessRuleProblem, match="'fnance' is not a registered product"):
            async with platform_session.begin():
                plan = await services.require_plan(platform_session, plan_id)
                await services.create_version(
                    platform_session,
                    operator_principal(),
                    plan,
                    _bundle(["hrms", "crm", "fnance"]),
                    request=None,
                    registry=REGISTRY,
                )

    async def test_a_clone_of_a_bundle_is_revalidated(self, platform_session: AsyncSession) -> None:
        """Cloning copies the previous version's features, so a member retired from the
        registry since then is caught on the next version too."""
        async with platform_session.begin():
            plan = await _suite_plan(platform_session, "trio-clone")
            await services.create_version(
                platform_session,
                operator_principal(),
                plan,
                _bundle(["hrms", "crm", "finance"]),
                request=None,
                registry=REGISTRY,
            )
        shrunk = FakeRegistry([e for e in REGISTRY._entries.values() if e.key != "finance"])
        with pytest.raises(BusinessRuleProblem, match="'finance' is not a registered product"):
            async with platform_session.begin():
                plan = await services.require_plan(platform_session, plan.id)
                await services.create_version(
                    platform_session,
                    operator_principal(),
                    plan,
                    VersionCreateIn(currency="INR"),
                    request=None,
                    registry=shrunk,
                )

    async def test_a_plain_plan_never_asks_the_registry(
        self, platform_session: AsyncSession
    ) -> None:
        registry = FakeRegistry.default()
        async with platform_session.begin():
            plan = await services.create_plan(
                platform_session,
                operator_principal(),
                PlanCreateIn(product_key="hrms", code="plain", name="Plain"),
                request=None,
            )
            await services.create_version(
                platform_session,
                operator_principal(),
                plan,
                VersionCreateIn(
                    currency="INR",
                    prices=[PriceIn(kind="flat", amount_minor=1000, currency="INR")],
                    features=[FeatureIn(feature_key="seats", value=10)],
                ),
                request=None,
                registry=registry,
            )
        assert registry.calls == 0


@contextlib.contextmanager
def _as(principal: Principal, registry: Any) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_product_registry] = lambda: registry
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_product_registry, None)


class _DownRegistry:
    async def products(self) -> dict[str, RegisteredProduct]:
        raise UpstreamUnavailableProblem("product-service is unavailable")


class TestOverHttp:
    async def _plan_id(self, client: Any) -> str:
        with _as(operator_principal(), REGISTRY):
            resp = await client.post(
                "/api/v1/admin/catalog/plans",
                json={"product_key": "suite", "code": "http-trio", "name": "HTTP Trio"},
            )
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])

    async def test_a_bad_member_is_a_422_naming_it(self, client: Any) -> None:
        plan_id = await self._plan_id(client)
        with _as(operator_principal(), REGISTRY):
            resp = await client.post(
                f"/api/v1/admin/catalog/plans/{plan_id}/versions",
                json=_bundle(["hrms", "core"]).model_dump(mode="json"),
            )
        assert resp.status_code == 422
        assert "'core' is a platform product" in resp.text

    async def test_a_good_bundle_is_a_201(self, client: Any) -> None:
        plan_id = await self._plan_id(client)
        with _as(operator_principal(), REGISTRY):
            resp = await client.post(
                f"/api/v1/admin/catalog/plans/{plan_id}/versions",
                json=_bundle(["hrms", "crm"]).model_dump(mode="json"),
            )
        assert resp.status_code == 201, resp.text

    async def test_an_unreachable_registry_refuses_rather_than_trusting(self, client: Any) -> None:
        plan_id = await self._plan_id(client)
        with _as(operator_principal(), _DownRegistry()):
            resp = await client.post(
                f"/api/v1/admin/catalog/plans/{plan_id}/versions",
                json=_bundle(["hrms", "crm"]).model_dump(mode="json"),
            )
        assert resp.status_code == 503

    async def test_a_customer_token_cannot_author_a_bundle(self, client: Any) -> None:
        plan_id = await self._plan_id(client)
        with _as(operator_principal(staff=False), REGISTRY):
            resp = await client.post(
                f"/api/v1/admin/catalog/plans/{plan_id}/versions",
                json=_bundle(["hrms", "crm"]).model_dump(mode="json"),
            )
        assert resp.status_code == 404
