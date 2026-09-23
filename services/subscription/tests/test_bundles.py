"""A bundle subscription entitles its members — the subscription-service half.

A `suite/trio` subscription used to emit `product_key: "suite"` and nothing else, and every
consumer downstream dutifully entitled `suite`: a key no gateway route checks. The customer paid
for three products and was entitled to none. The members are declared on the pinned catalog
version (`bundled_products`); these tests prove subscription reads them from there and from
nowhere else, snapshots them with the pin, and puts them on every lifecycle event:

* subscribe snapshots the version's members; a non-bundle snapshots `[product_key]`;
* a member list in the request body is ignored — the pinned version is the only authority;
* change-plan re-snapshots with the pin, so a duo→trio event carries the trio's members;
* a change-plan cannot hop to another product's version (it would change what the row entitles);
* a malformed `bundled_products` is refused rather than pinned to a subscription that entitles
  nothing;
* the one-off backfill fills in legacy rows from catalog, emits for bundles only, and re-runs as
  a no-op.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import null, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import services
from app.backfill import backfill
from app.config import settings
from app.main import app
from app.models import SCHEMA, OutboxEvent, Subscription
from app.registry import RegisteredProduct
from app.schemas import ChangePlanIn, OperatorChangePlanIn, OperatorSubscribeIn, SubscribeIn
from platform_core.auth import Scope, ScopeContext
from platform_core.db import TENANT_GUC
from platform_core.errors import (
    BusinessRuleProblem,
    ForbiddenProblem,
    UpstreamUnavailableProblem,
)
from platform_core.internal_auth import mint_service_token
from tests.helpers import (
    FakeCatalog,
    FakeRegistry,
    customer_principal,
    make_bundle,
    make_version,
)

pytestmark = pytest.mark.integration

DUO = ["hrms", "crm"]
TRIO = ["hrms", "crm", "finance"]


def _service_headers() -> dict[str, str]:
    return {
        "X-Internal-Token": mint_service_token(settings(), audience="subscription", caller="tests"),
        "X-Calling-Service": "tests",
    }


def _ctx(org_id: uuid.UUID) -> ScopeContext:
    return ScopeContext(
        principal=customer_principal(org_id),
        scope=Scope.ORG,
        permission="core.product.activation.write",
    )


async def _outbox(admin_session: AsyncSession, org_id: uuid.UUID) -> list[OutboxEvent]:
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.organization_id == org_id)
        .order_by(OutboxEvent.created_at)
    )
    return list((await admin_session.execute(stmt)).scalars().all())


async def _subscribe(
    session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID, product_key: str, version: Any
) -> uuid.UUID:
    """A bundle through the operator path (the only one that sells it); anything else as the
    customer would."""
    async with session.begin():
        if product_key == "suite":
            sub = await services.subscribe_as_operator(
                session,
                catalog,
                FakeRegistry(),
                OperatorSubscribeIn(
                    organization_id=org_id,
                    product_key=product_key,
                    plan_version_id=version.id,
                    operator_id="op-1",
                ),
            )
        else:
            sub = await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key=product_key, plan_version_id=version.id),
            )
        return sub.id


def _repin(org_id: uuid.UUID, version: Any) -> OperatorChangePlanIn:
    return OperatorChangePlanIn(
        organization_id=org_id, plan_version_id=version.id, operator_id="op-1"
    )


class TestSnapshot:
    async def test_a_trio_subscription_entitles_its_three_members(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        trio = catalog.add(make_bundle(TRIO, tier="trio", amount_minor=80700))
        sub_id = await _subscribe(session, catalog, org_id, "suite", trio)

        async with session.begin():
            sub = await services.require_subscription(session, sub_id)
        assert sub.entitled_products == TRIO
        assert sub.entitled_products_snapshot == TRIO

        rows = await _outbox(admin_session, org_id)
        assert [r.event_type for r in rows] == ["commerce.subscription.activated"]
        payload = rows[0].payload
        assert payload["product_key"] == "suite"
        assert payload["entitled_products"] == TRIO
        assert payload["subscription_id"] == str(sub_id)

    async def test_a_standalone_subscription_entitles_its_own_product(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        version = catalog.add(make_version(product_key="hrms"))
        await _subscribe(session, catalog, org_id, "hrms", version)
        rows = await _outbox(admin_session, org_id)
        assert rows[0].payload["entitled_products"] == ["hrms"]

    async def test_every_lifecycle_event_carries_the_members(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """A suspension or cancellation must name the same members, or the projection could not
        tell which activations to switch off."""
        trio = catalog.add(make_bundle(TRIO, tier="trio", amount_minor=80700))
        sub_id = await _subscribe(session, catalog, org_id, "suite", trio)
        async with session.begin():
            sub = await services.require_subscription(session, sub_id)
            await services.suspend(session, sub, operator_id="op")
        async with session.begin():
            sub = await services.require_subscription(session, sub_id)
            await services.cancel(session, _ctx(org_id), sub)

        rows = await _outbox(admin_session, org_id)
        assert [r.event_type.rsplit(".", 1)[1] for r in rows] == [
            "activated",
            "suspended",
            "cancelled",
        ]
        assert all(r.payload["entitled_products"] == TRIO for r in rows)

    async def test_a_legacy_row_reads_as_its_own_product(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        """NULL is what every row written before this change holds. Read as `[product_key]`,
        which is exactly what the projection believed about it."""
        version = catalog.add(make_version(product_key="crm"))
        sub_id = await _subscribe(session, catalog, org_id, "crm", version)
        async with session.begin():
            await session.execute(
                update(Subscription)
                .where(Subscription.id == sub_id)
                .values(entitled_products_snapshot=null())
            )
        async with session.begin():
            sub = await services.require_subscription(session, sub_id)
        assert sub.entitled_products_snapshot is None
        assert sub.entitled_products == ["crm"]


class TestTheMembersComeOnlyFromCatalog:
    async def test_a_member_list_in_the_request_body_is_ignored(
        self, client: Any, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        """Selling the duo and naming four products must still entitle two — over the real
        internal route, with a real service token."""
        from app.api.internal import get_product_registry

        duo = catalog.add(make_bundle(DUO, tier="duo", amount_minor=58200))
        app.dependency_overrides[get_product_registry] = lambda: FakeRegistry()
        resp = await client.post(
            "/internal/v1/subscriptions",
            headers=_service_headers(),
            json={
                "organization_id": str(org_id),
                "product_key": "suite",
                "plan_version_id": str(duo.id),
                "operator_id": "op-1",
                "entitled_products": ["hrms", "crm", "finance", "projects"],
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["entitled_products"] == DUO

    async def test_a_malformed_bundle_is_refused_not_pinned(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        broken = catalog.add(
            make_version(product_key="suite", features={"bundled_products": "hrms,crm"})
        )
        with pytest.raises(BusinessRuleProblem):
            async with session.begin():
                await services.subscribe(
                    session,
                    _ctx(org_id),
                    catalog,
                    SubscribeIn(product_key="suite", plan_version_id=broken.id),
                )
        async with session.begin():
            assert (await session.execute(select(Subscription))).scalars().all() == []


class TestChangePlan:
    async def test_duo_to_trio_resnapshots_and_the_event_carries_the_trio(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        duo = catalog.add(make_bundle(DUO, tier="duo", amount_minor=58200))
        trio = catalog.add(make_bundle(TRIO, tier="trio", amount_minor=80700))
        sub_id = await _subscribe(session, catalog, org_id, "suite", duo)

        async with session.begin():
            sub = await services.require_subscription(session, sub_id)
            await services.change_plan_as_operator(
                session, catalog, FakeRegistry(), sub, _repin(org_id, trio)
            )
        async with session.begin():
            sub = await services.require_subscription(session, sub_id)
        assert sub.entitled_products == TRIO

        rows = await _outbox(admin_session, org_id)
        assert rows[-1].event_type == "commerce.subscription.upgraded"
        assert rows[-1].payload["entitled_products"] == TRIO

        # And back down: the downgrade event names the duo only.
        async with session.begin():
            sub = await services.require_subscription(session, sub_id)
            await services.change_plan_as_operator(
                session, catalog, FakeRegistry(), sub, _repin(org_id, duo)
            )
        rows = await _outbox(admin_session, org_id)
        assert rows[-1].event_type == "commerce.subscription.downgraded"
        assert rows[-1].payload["entitled_products"] == DUO

    async def test_a_change_plan_cannot_hop_to_another_products_version(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        hrms = catalog.add(make_version(product_key="hrms"))
        trio = catalog.add(make_bundle(TRIO, tier="trio", amount_minor=80700))
        sub_id = await _subscribe(session, catalog, org_id, "hrms", hrms)
        with pytest.raises(BusinessRuleProblem, match="belongs to product"):
            async with session.begin():
                sub = await services.require_subscription(session, sub_id)
                await services.change_plan(
                    session, _ctx(org_id), catalog, sub, ChangePlanIn(plan_version_id=trio.id)
                )


class TestBackfill:
    @pytest.fixture
    async def engines(self, session: AsyncSession) -> AsyncIterator[tuple[Any, Any]]:
        """Discovery on the admin DSN (as the tool runs), writes on the app role."""
        cfg = settings()
        args = {"server_settings": {"search_path": f"{SCHEMA},public"}}
        discovery = create_async_engine(cfg.admin_dsn, connect_args=args)
        work = create_async_engine(cfg.app_dsn, connect_args=args)
        yield discovery, work
        await discovery.dispose()
        await work.dispose()

    @staticmethod
    def _scope(work_engine: Any) -> Any:
        maker = async_sessionmaker(work_engine, expire_on_commit=False)

        @asynccontextmanager
        async def work_scope(organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
            async with maker() as s, s.begin():
                await s.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                    {"org": str(organization_id)},
                )
                yield s

        return work_scope

    async def _legacy(
        self,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        product_key: str,
        version: Any,
    ) -> uuid.UUID:
        sub_id = await _subscribe(session, catalog, org_id, product_key, version)
        async with session.begin():
            await session.execute(
                update(Subscription)
                .where(Subscription.id == sub_id)
                .values(entitled_products_snapshot=null())
            )
        return sub_id

    async def test_fills_bundles_from_catalog_and_emits_only_for_them(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        engines: tuple[Any, Any],
    ) -> None:
        trio = catalog.add(make_bundle(TRIO, tier="trio", amount_minor=80700))
        hrms = catalog.add(make_version(product_key="hrms"))
        suite_id = await self._legacy(session, catalog, org_id, "suite", trio)
        hrms_id = await self._legacy(session, catalog, org_id, "hrms", hrms)
        before = len(await _outbox(admin_session, org_id))

        discovery, work = engines
        report = await backfill(
            discovery_factory=async_sessionmaker(discovery, expire_on_commit=False),
            work_scope=self._scope(work),
            catalog=catalog,
        )
        assert (report.considered, report.written, report.bundles, report.failed) == (2, 2, 1, 0)

        session.expire_all()  # the backfill wrote on its own connection
        async with session.begin():
            suite = await services.require_subscription(session, suite_id)
            standalone = await services.require_subscription(session, hrms_id)
        assert suite.entitled_products_snapshot == TRIO
        assert standalone.entitled_products_snapshot == ["hrms"]

        emitted = (await _outbox(admin_session, org_id))[before:]
        assert [e.event_type for e in emitted] == ["commerce.subscription.entitlements_resynced"]
        assert emitted[0].payload["entitled_products"] == TRIO
        assert emitted[0].payload["state"] == "active"
        assert emitted[0].aggregate_id == suite_id

        # A second run finds nothing to do and emits nothing.
        again = await backfill(
            discovery_factory=async_sessionmaker(discovery, expire_on_commit=False),
            work_scope=self._scope(work),
            catalog=catalog,
        )
        assert again.considered == 0
        assert len(await _outbox(admin_session, org_id)) == before + 1

    async def test_dry_run_writes_nothing_and_an_unresolvable_pin_stays_null(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        engines: tuple[Any, Any],
    ) -> None:
        trio = catalog.add(make_bundle(TRIO, tier="trio", amount_minor=80700))
        sub_id = await self._legacy(session, catalog, org_id, "suite", trio)
        discovery, work = engines

        dry = await backfill(
            discovery_factory=async_sessionmaker(discovery, expire_on_commit=False),
            work_scope=self._scope(work),
            catalog=catalog,
            dry_run=True,
        )
        assert (dry.considered, dry.written, dry.bundles) == (1, 0, 1)

        empty = FakeCatalog()  # catalog no longer knows the pin
        report = await backfill(
            discovery_factory=async_sessionmaker(discovery, expire_on_commit=False),
            work_scope=self._scope(work),
            catalog=empty,
        )
        assert (report.written, report.unresolved) == (0, 1)
        session.expire_all()
        async with session.begin():
            sub = await services.require_subscription(session, sub_id)
        assert sub.entitled_products_snapshot is None

    async def test_a_change_plan_that_committed_first_is_not_overwritten(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
    ) -> None:
        """The backfill read the row on the duo; a change-plan to the trio committed before its
        write. The conditional UPDATE must find nothing to do rather than put the duo back."""
        duo = catalog.add(make_bundle(DUO, tier="duo", amount_minor=58200))
        trio = catalog.add(make_bundle(TRIO, tier="trio", amount_minor=80700))
        sub_id = await self._legacy(session, catalog, org_id, "suite", duo)
        async with session.begin():
            stale = await services.require_subscription(session, sub_id)
        session.expunge(stale)  # what the backfill holds: the row as it was read

        # The change-plan commits on another connection, snapshotting the trio.
        async with session.begin():
            fresh = await services.require_subscription(session, sub_id)
            await services.change_plan_as_operator(
                session, catalog, FakeRegistry(), fresh, _repin(org_id, trio)
            )
        before = len(await _outbox(admin_session, org_id))

        async with session.begin():
            wrote = await services.resync_entitlements(session, stale, duo)
        assert wrote is False
        session.expire_all()
        async with session.begin():
            after = await services.require_subscription(session, sub_id)
        assert after.entitled_products_snapshot == TRIO
        assert len(await _outbox(admin_session, org_id)) == before, "a stale resync emitted"


class TestBundlesAreSalesLed:
    """Bundles are sold by the platform team (docs/commerce-packaging.md, reversible)."""

    async def test_a_customer_cannot_subscribe_to_a_bundle(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        trio = catalog.add(make_bundle(TRIO, tier="trio", amount_minor=80700))
        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                await services.subscribe(
                    session,
                    _ctx(org_id),
                    catalog,
                    SubscribeIn(product_key="suite", plan_version_id=trio.id),
                )

    async def test_the_customer_route_answers_403(
        self, client: Any, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        from platform_core.auth import get_principal

        trio = catalog.add(make_bundle(TRIO, tier="trio", amount_minor=80700))
        app.dependency_overrides[get_principal] = lambda: customer_principal(org_id)
        resp = await client.post(
            "/api/v1/subscriptions",
            json={"product_key": "suite", "plan_version_id": str(trio.id)},
        )
        assert resp.status_code == 403, resp.text

    async def test_a_customer_cannot_change_a_bundles_plan(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        duo = catalog.add(make_bundle(DUO, tier="duo", amount_minor=58200))
        trio = catalog.add(make_bundle(TRIO, tier="trio", amount_minor=80700))
        sub_id = await _subscribe(session, catalog, org_id, "suite", duo)
        with pytest.raises(ForbiddenProblem):
            async with session.begin():
                sub = await services.require_subscription(session, sub_id)
                await services.change_plan(
                    session, _ctx(org_id), catalog, sub, ChangePlanIn(plan_version_id=trio.id)
                )

    async def test_the_operator_route_needs_a_service_token(self, client: Any) -> None:
        resp = await client.post("/internal/v1/subscriptions", json={})
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        ("registry", "fragment"),
        [
            (FakeRegistry(), "'projects' has not launched"),
            (
                FakeRegistry({"suite": RegisteredProduct("suite")}),
                "'suite' is not a bundle product",
            ),
            (
                FakeRegistry({"projects": RegisteredProduct("projects", is_platform=True)}),
                "'projects' cannot be a bundle member",
            ),
        ],
    )
    async def test_the_operator_path_refuses_what_the_registry_cannot_vouch_for(
        self,
        session: AsyncSession,
        catalog: FakeCatalog,
        org_id: uuid.UUID,
        registry: FakeRegistry,
        fragment: str,
    ) -> None:
        full = catalog.add(make_bundle([*TRIO, "projects"], tier="full", amount_minor=95600))
        with pytest.raises(BusinessRuleProblem, match=fragment):
            async with session.begin():
                await services.subscribe_as_operator(
                    session,
                    catalog,
                    registry,
                    OperatorSubscribeIn(
                        organization_id=org_id,
                        product_key="suite",
                        plan_version_id=full.id,
                        operator_id="op-1",
                    ),
                )

    async def test_an_unreachable_registry_refuses_the_sale(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        class Down:
            async def products(self) -> dict[str, RegisteredProduct]:
                raise UpstreamUnavailableProblem("down")

        trio = catalog.add(make_bundle(TRIO, tier="trio", amount_minor=80700))
        with pytest.raises(UpstreamUnavailableProblem):
            async with session.begin():
                await services.subscribe_as_operator(
                    session,
                    catalog,
                    Down(),
                    OperatorSubscribeIn(
                        organization_id=org_id,
                        product_key="suite",
                        plan_version_id=trio.id,
                        operator_id="op-1",
                    ),
                )


class TestTrialLengthComesFromTheVersion:
    async def _trial(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID, **kw: Any
    ) -> Any:
        requested = kw.pop("requested")
        version = catalog.add(make_version(product_key="crm", **kw))
        async with session.begin():
            return await services.subscribe(
                session,
                _ctx(org_id),
                catalog,
                SubscribeIn(product_key="crm", plan_version_id=version.id, trial_days=requested),
            )

    @staticmethod
    def _days(sub: Any) -> int:
        return round((sub.trial_ends_at - sub.current_period_start).total_seconds() / 86400)

    async def test_a_customer_asking_for_a_year_gets_the_default_fortnight(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        sub = await self._trial(session, catalog, org_id, requested=365)
        assert sub.state == "trial"
        assert self._days(sub) == 14

    async def test_the_version_sets_the_length(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        sub = await self._trial(session, catalog, org_id, requested=365, features={"trial_days": 7})
        assert self._days(sub) == 7

    async def test_a_shorter_request_is_honoured(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        sub = await self._trial(session, catalog, org_id, requested=3)
        assert self._days(sub) == 3

    async def test_a_version_with_no_trial_refuses_one(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        with pytest.raises(BusinessRuleProblem, match="does not offer a trial"):
            await self._trial(session, catalog, org_id, requested=14, features={"trial_days": 0})

    async def test_a_malformed_trial_length_is_refused(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        with pytest.raises(BusinessRuleProblem, match="trial_days"):
            await self._trial(
                session, catalog, org_id, requested=14, features={"trial_days": "forever"}
            )


class TestAPinMustNameItsProduct:
    async def test_subscribe_refuses_a_version_that_names_no_product(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        version = make_version(product_key="hrms").model_copy(update={"product_key": None})
        catalog.add(version)
        with pytest.raises(BusinessRuleProblem, match="names no product"):
            async with session.begin():
                await services.subscribe(
                    session,
                    _ctx(org_id),
                    catalog,
                    SubscribeIn(product_key="hrms", plan_version_id=version.id),
                )

    async def test_change_plan_refuses_a_version_that_names_no_product(
        self, session: AsyncSession, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        v1 = catalog.add(make_version(product_key="hrms"))
        v2 = make_version(product_key="hrms").model_copy(update={"product_key": None})
        catalog.add(v2)
        sub_id = await _subscribe(session, catalog, org_id, "hrms", v1)
        with pytest.raises(BusinessRuleProblem, match="names no product"):
            async with session.begin():
                sub = await services.require_subscription(session, sub_id)
                await services.change_plan(
                    session, _ctx(org_id), catalog, sub, ChangePlanIn(plan_version_id=v2.id)
                )
