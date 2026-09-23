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

    def test_the_seeded_ladder_passes_except_the_retired_full_rung(self) -> None:
        """duo and trio pass as seeded. `full` names `projects`, which is still `soon` — which is
        exactly why migration c5f1e8a2b7d4 retires that plan rather than selling it."""
        from tests.test_packaging import _seed_module

        seed = _seed_module()
        registry = {e.key: e for e in [*REGISTRY._entries.values()]}
        verdicts = {
            version: domain.bundle_member_problems(registry["suite"], "suite", value, registry)
            for _id, version, key, value in seed.FEATURES
            if key == "bundled_products"
        }
        full_version = next(v[0] for v in seed.VERSIONS if v[1] == _full_plan_id())
        assert verdicts.pop(full_version) == ["'projects' has not launched (status 'soon')"]
        assert all(problems == [] for problems in verdicts.values())

    def test_the_retirement_migration_names_the_seeded_full_plan(self) -> None:
        from tests.test_packaging import _seed_module

        full = next(p for p in _seed_module().PLANS if p[2] == "full")
        assert full[0] == _full_plan_id()

    def test_a_soon_member_is_named(self) -> None:
        registry = {
            "suite": RegisteredProduct("suite", is_bundle=True),
            "projects": RegisteredProduct("projects", status="soon"),
        }
        assert domain.bundle_member_problems(
            registry["suite"], "suite", ["projects"], registry
        ) == ["'projects' has not launched (status 'soon')"]


def _full_plan_id() -> str:
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "versions"
        / "c5f1e8a2b7d4_retire_full_suite_until_projects.py"
    )
    spec = importlib.util.spec_from_file_location("_retire_full", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.FULL_PLAN)


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

    async def test_an_explicit_null_member_list_is_refused_not_ignored(self, client: Any) -> None:
        """The key's presence makes it a bundle declaration; `null` is a malformed one."""
        plan_id = await self._plan_id(client)
        body = _bundle(["hrms"]).model_dump(mode="json")
        body["features"] = [{"feature_key": "bundled_products", "value": None}]
        with _as(operator_principal(), REGISTRY):
            resp = await client.post(f"/api/v1/admin/catalog/plans/{plan_id}/versions", json=body)
        assert resp.status_code == 422
        assert "non-empty list" in resp.text

    async def test_a_soon_member_is_refused(self, client: Any) -> None:
        plan_id = await self._plan_id(client)
        with _as(operator_principal(), REGISTRY):
            resp = await client.post(
                f"/api/v1/admin/catalog/plans/{plan_id}/versions",
                json=_bundle(["hrms", "projects"]).model_dump(mode="json"),
            )
        assert resp.status_code == 422
        assert "'projects' has not launched" in resp.text


class TestTrialLength:
    @pytest.mark.parametrize("value", [-1, 366, "14", 1.5, True, None])
    async def test_a_malformed_trial_length_is_refused(
        self, platform_session: AsyncSession, value: Any
    ) -> None:
        with pytest.raises(BusinessRuleProblem, match="trial_days"):
            async with platform_session.begin():
                plan = await services.create_plan(
                    platform_session,
                    operator_principal(),
                    PlanCreateIn(product_key="hrms", code="trial-bad", name="Trial"),
                    request=None,
                )
                await services.create_version(
                    platform_session,
                    operator_principal(),
                    plan,
                    VersionCreateIn(
                        currency="INR",
                        prices=[PriceIn(kind="flat", amount_minor=1000, currency="INR")],
                        features=[FeatureIn(feature_key="trial_days", value=value)],
                    ),
                    request=None,
                    registry=REGISTRY,
                )

    async def test_a_valid_trial_length_authors(self, platform_session: AsyncSession) -> None:
        async with platform_session.begin():
            plan = await services.create_plan(
                platform_session,
                operator_principal(),
                PlanCreateIn(product_key="hrms", code="trial-ok", name="Trial"),
                request=None,
            )
            version = await services.create_version(
                platform_session,
                operator_principal(),
                plan,
                VersionCreateIn(
                    currency="INR",
                    prices=[PriceIn(kind="flat", amount_minor=1000, currency="INR")],
                    features=[FeatureIn(feature_key="trial_days", value=0)],
                ),
                request=None,
                registry=REGISTRY,
            )
        assert version.state == "draft"


class TestTheDeployAudit:
    """`python -m app.bundle_audit` — every sellable bundle re-judged against the registry."""

    async def _bundle_version(
        self, session: AsyncSession, code: str, members: list[str], *, retire: bool = False
    ) -> None:
        """Author with a permissive registry (as if the members were live then), publish, and
        optionally retire the plan — the registry the audit sees is the one that moved on."""
        permissive = FakeRegistry(
            [RegisteredProduct("suite", is_bundle=True), *(RegisteredProduct(m) for m in members)]
        )
        plan = await _suite_plan(session, code)
        version = await services.create_version(
            session, operator_principal(), plan, _bundle(members), request=None, registry=permissive
        )
        await services.publish_version(session, operator_principal(), version, request=None)
        if retire:
            await services.retire_plan(session, operator_principal(), plan, request=None)

    async def test_an_offered_rung_with_a_bad_member_fails_a_retired_one_warns(
        self, platform_session: AsyncSession
    ) -> None:
        from app.bundle_audit import audit

        async with platform_session.begin():
            await self._bundle_version(platform_session, "audit-ok", ["hrms", "crm"])
            await self._bundle_version(platform_session, "audit-bad", ["hrms", "retired-one"])
            await self._bundle_version(
                platform_session, "audit-old", ["hrms", "projects"], retire=True
            )
        # The registry the audit sees: `retired-one` is gone, `projects` is still `soon`.
        async with platform_session.begin():
            report = await audit(platform_session, REGISTRY)

        assert report.audited == 3
        assert not report.ok
        assert [f.plan for f in report.errors] == ["suite/audit-bad"]
        assert report.errors[0].problems == ["'retired-one' is not a registered product"]
        assert [f.plan for f in report.warnings] == ["suite/audit-old"]

    async def test_a_clean_catalogue_passes(self, platform_session: AsyncSession) -> None:
        from app.bundle_audit import audit

        async with platform_session.begin():
            await self._bundle_version(platform_session, "audit-clean", ["hrms", "finance"])
        async with platform_session.begin():
            report = await audit(platform_session, REGISTRY)
        assert report.ok and report.audited == 1
