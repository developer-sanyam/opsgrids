"""The operator Plans & prices surface: reads for owner and billing_ops, writes for owner only.

Spec: docs/superpowers/specs/2026-10-06-admin-plans-page-design.md. What these tests pin:

1. **Authorization, route by route.** Every read refuses `support`, a session without its second
   factor, a product-audience token (404) and no token (401); every write additionally refuses
   `billing_ops`. A refused write changes nothing and never reaches the product registry.
2. **Idempotency.** A required key; the same key and body replays the first answer; the same key
   on a different body (or from another operator) is a 409; a refused request does not burn its
   key; two concurrent claims of one key execute once.
3. **Publish validation**, as a table of sheets and the codes each must produce.
4. **The trigger migration** (b7e4d2a9c613): exactly `published -> grandfathered` is allowed on a
   published row, nothing else is, and a grandfathered row cannot be updated at all.
5. **Withdraw, delete draft, retire and reactivate**, with their audit rows and events.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import domain, selectors, services
from app.api.admin import get_product_registry
from app.config import settings
from app.domain import RegisteredProduct
from app.main import app
from app.models import PLATFORM_ORG, SCHEMA, AuditLog, IdempotencyKey, OutboxEvent, Plan
from app.schemas import FeatureIn, PlanCreateIn, PriceIn, SeatClassIn, VersionCreateIn
from platform_core.auth import Principal, get_principal
from platform_core.db import TENANT_GUC
from platform_core.errors import BusinessRuleProblem, ConflictProblem
from tests.helpers import FakeRegistry, operator_principal

pytestmark = pytest.mark.integration

BASE = "/api/v1/admin/catalog"
REASON = "Quarterly price review"


@contextlib.contextmanager
def acting(principal: Principal | None, registry: Any = None) -> Iterator[None]:
    """Run requests as `principal` (None: unauthenticated), with a fake product registry."""
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_product_registry] = lambda: registry or FakeRegistry.default()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_product_registry, None)


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


OWNER = operator_principal()


async def _plan(
    session: AsyncSession, *, product_key: str = "hrms", code: str = "std", sort_order: int = 100
) -> Plan:
    return await services.create_plan(
        session,
        OWNER,
        PlanCreateIn(product_key=product_key, code=code, name=code.title(), sort_order=sort_order),
        request=None,
    )


async def _draft(
    session: AsyncSession,
    plan: Plan,
    *,
    prices: list[PriceIn] | None = None,
    features: list[FeatureIn] | None = None,
    seat_classes: list[SeatClassIn] | None = None,
    currency: str = "INR",
    org: uuid.UUID | None = None,
    registry: Any = None,
):
    return await services.create_version(
        session,
        OWNER,
        plan,
        VersionCreateIn(
            currency=currency,
            restricted_to_org_id=org,
            prices=prices
            if prices is not None
            else [PriceIn(kind="flat", amount_minor=99_900, currency=currency)],
            features=features or [],
            seat_classes=seat_classes or [],
        ),
        request=None,
        registry=registry,
    )


async def _published(session: AsyncSession, plan: Plan, **kwargs: Any):
    version = await _draft(session, plan, **kwargs)
    return await services.publish_version(
        session, OWNER, version, request=None, registry=kwargs.get("registry")
    )


async def _count(session: AsyncSession, model: Any, *where: Any) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()
    )


# --------------------------------------------------------------------- authorization


def _routes(plan_id: uuid.UUID, version_id: uuid.UUID) -> list[tuple[str, str, Any]]:
    """Every route on the surface: (method, path, body). Bodies are valid, so a refusal can only
    come from the gate under test."""
    reason = {"reason": REASON}
    return [
        ("GET", f"{BASE}/plans", None),
        ("GET", f"{BASE}/plans/{plan_id}", None),
        ("GET", f"{BASE}/versions/{version_id}", None),
        (
            "POST",
            f"{BASE}/plans",
            {"product_key": "crm", "code": "gate", "name": "Gate", "reason": REASON},
        ),
        ("PATCH", f"{BASE}/plans/{plan_id}", {"name": "Renamed", "reason": REASON}),
        ("POST", f"{BASE}/plans/{plan_id}/retire", reason),
        ("POST", f"{BASE}/plans/{plan_id}/reactivate", reason),
        (
            "POST",
            f"{BASE}/plans/{plan_id}/versions",
            {
                "currency": "INR",
                "prices": [{"kind": "flat", "amount_minor": 100, "currency": "INR"}],
                "reason": REASON,
            },
        ),
        ("POST", f"{BASE}/versions/{version_id}/publish", reason),
        ("POST", f"{BASE}/versions/{version_id}/withdraw", reason),
        ("DELETE", f"{BASE}/versions/{version_id}", reason),
    ]


class TestEveryRouteIsGated:
    @pytest_asyncio.fixture
    async def seeded(self, platform_session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        async with platform_session.begin():
            plan = await _plan(platform_session)
            version = await _draft(platform_session, plan)
        return plan.id, version.id

    @pytest.mark.parametrize(
        ("label", "principal", "expected"),
        [
            ("support", operator_principal(roles=["support"]), 403),
            ("no second factor", operator_principal(amr=["pwd"]), 403),
            ("product audience", operator_principal(staff=False), 404),
            ("unauthenticated", None, 401),
        ],
    )
    async def test_every_route_refuses(
        self,
        client: AsyncClient,
        platform_session: AsyncSession,
        seeded: tuple[uuid.UUID, uuid.UUID],
        label: str,
        principal: Principal | None,
        expected: int,
    ) -> None:
        registry = FakeRegistry.default()
        with acting(principal, registry):
            for method, path, body in _routes(*seeded):
                resp = await client.request(method, path, json=body, headers=key())
                assert resp.status_code == expected, (label, method, path, resp.text)
        assert registry.calls == 0, "a refused write must never reach the product registry"
        await self._nothing_changed(platform_session, *seeded)

    async def test_billing_ops_reads_but_every_write_is_403(
        self,
        client: AsyncClient,
        platform_session: AsyncSession,
        seeded: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        registry = FakeRegistry.default()
        with acting(operator_principal(roles=["billing_ops"]), registry):
            for method, path, body in _routes(*seeded):
                resp = await client.request(method, path, json=body, headers=key())
                expected = 200 if method == "GET" else 403
                assert resp.status_code == expected, (method, path, resp.text)
        assert registry.calls == 0
        await self._nothing_changed(platform_session, *seeded)

    async def _nothing_changed(
        self, session: AsyncSession, plan_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        async with session.begin():
            plan = await selectors.get_plan(session, plan_id)
            version = await selectors.get_version(session, version_id)
            assert plan is not None and plan.state == "active" and plan.name == "Std"
            assert version is not None and version.state == "draft"
            assert await _count(session, Plan) == 1
            assert await _count(session, IdempotencyKey) == 0


# --------------------------------------------------------------------- reads


class TestReads:
    async def test_the_list_defaults_to_public_and_filters_by_audience(
        self, client: AsyncClient, platform_session: AsyncSession, org_a: uuid.UUID
    ) -> None:
        async with platform_session.begin():
            public = await _plan(platform_session, code="public")
            await _published(platform_session, public)
            bespoke_only = await _plan(platform_session, code="bespoke-only")
            await _published(platform_session, bespoke_only, org=org_a)
            empty = await _plan(platform_session, code="empty")

        with acting(operator_principal(roles=["billing_ops"])):
            default = (await client.get(f"{BASE}/plans")).json()
            restricted = (
                await client.get(f"{BASE}/plans", params={"audience": "restricted"})
            ).json()
            every = (await client.get(f"{BASE}/plans", params={"audience": "all"})).json()

        assert {p["id"] for p in default["items"]} == {str(public.id), str(empty.id)}
        assert {p["id"] for p in restricted["items"]} == {str(bespoke_only.id)}
        assert len(every["items"]) == 3
        row = next(p for p in default["items"] if p["id"] == str(public.id))
        assert row["counts"] == {"draft": 0, "published": 1, "grandfathered": 0, "bespoke": 0}
        assert row["current"]["prices"][0]["amount_minor"] == 99_900
        assert row["current"]["plan_name"] == "Public"
        assert row["latest_version_no"] == 1

    async def test_filters_by_product_and_state(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            await _plan(platform_session, product_key="hrms", code="a")
            crm = await _plan(platform_session, product_key="crm", code="b")
            retired = await _plan(platform_session, product_key="crm", code="c")
            await services.retire_plan(platform_session, OWNER, retired, request=None)
            # The 422 below rolls the shared session back, which expires every loaded object.
            crm_id = crm.id

        with acting(OWNER):
            by_product = (await client.get(f"{BASE}/plans", params={"product_key": "crm"})).json()
            by_state = (
                await client.get(f"{BASE}/plans", params={"product_key": "crm", "state": "active"})
            ).json()
            bad = await client.get(f"{BASE}/plans", params={"product_key": "../x"})
        assert {p["code"] for p in by_product["items"]} == {"b", "c"}
        assert [p["id"] for p in by_state["items"]] == [str(crm_id)]
        assert bad.status_code == 422

    async def test_cursor_pagination_walks_every_plan_once(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            for index in range(5):
                await _plan(platform_session, code=f"p{index}", sort_order=10 * (5 - index))

        seen: list[str] = []
        cursor: str | None = None
        with acting(OWNER):
            for _page in range(5):
                params: dict[str, Any] = {"limit": 2}
                if cursor:
                    params["cursor"] = cursor
                body = (await client.get(f"{BASE}/plans", params=params)).json()
                seen += [p["code"] for p in body["items"]]
                cursor = body["next_cursor"]
                if cursor is None:
                    break
            bad = await client.get(f"{BASE}/plans", params={"cursor": "not-a-cursor"})
        # Ordered by sort_order inside the product: p4 has the lowest.
        assert seen == ["p4", "p3", "p2", "p1", "p0"]
        assert bad.status_code == 422

    async def test_plan_detail_lists_version_summaries_lazily_and_pages_them(
        self, client: AsyncClient, platform_session: AsyncSession, org_a: uuid.UUID
    ) -> None:
        async with platform_session.begin():
            plan = await _plan(platform_session)
            v1 = await _published(platform_session, plan)
            await _published(platform_session, plan, org=org_a)
            v3 = await _draft(platform_session, plan)
            # A refused request below rolls the shared session back, expiring loaded objects.
            plan_id, v1_id, v3_id = plan.id, v1.id, v3.id

        with acting(operator_principal(roles=["billing_ops"])):
            public = (await client.get(f"{BASE}/plans/{plan_id}", params={"limit": 1})).json()
            rest = (
                await client.get(
                    f"{BASE}/plans/{plan_id}", params={"limit": 1, "cursor": public["next_cursor"]}
                )
            ).json()
            every = (await client.get(f"{BASE}/plans/{plan_id}", params={"audience": "all"})).json()
            missing = await client.get(f"{BASE}/plans/{uuid.uuid4()}")

        assert [v["id"] for v in public["versions"]] == [str(v3_id)]
        assert "prices" not in public["versions"][0], "a summary carries no sheet"
        assert public["versions"][0]["price_count"] == 1
        assert [v["id"] for v in rest["versions"]] == [str(v1_id)]
        assert rest["next_cursor"] is None
        assert [v["version_no"] for v in every["versions"]] == [3, 2, 1]
        assert every["counts"] == {"draft": 1, "published": 1, "grandfathered": 0, "bespoke": 1}
        assert every["latest_version_no"] == 3
        assert missing.status_code == 404

    async def test_a_version_carries_its_plan_and_a_deterministic_price_order(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        """Authored usage-first; read back flat, per-seat, usage — and the same on every read.
        Each price carries its id, so a seat class's `price_id` can be joined to it."""
        async with platform_session.begin():
            plan = await _plan(platform_session, code="growth")
            version = await _draft(
                platform_session,
                plan,
                prices=[
                    PriceIn(
                        kind="usage", currency="INR", unit_price_micros=500, meter_key="api_calls"
                    ),
                    PriceIn(kind="per_seat", amount_minor=40_000, currency="INR", unit="seat"),
                    PriceIn(kind="flat", amount_minor=99_900, currency="INR"),
                ],
                features=[
                    FeatureIn(feature_key="trial_days", value=14),
                    FeatureIn(feature_key="api", value=True),
                ],
                seat_classes=[SeatClassIn(code="member", name="Member", price_index=1)],
            )
            plan_id, version_id = plan.id, version.id

        with acting(OWNER):
            first = (await client.get(f"{BASE}/versions/{version_id}")).json()
            second = (await client.get(f"{BASE}/versions/{version_id}")).json()
            missing = await client.get(f"{BASE}/versions/{uuid.uuid4()}")

        assert first == second
        assert first["plan_id"] == str(plan_id)
        assert first["plan_name"] == "Growth"
        assert first["product_key"] == "hrms"
        assert first["plan_state"] == "active"
        assert [p["kind"] for p in first["prices"]] == ["flat", "per_seat", "usage"]
        assert [f["feature_key"] for f in first["features"]] == ["api", "trial_days"]
        per_seat = first["prices"][1]
        assert first["seat_classes"][0]["price_id"] == per_seat["id"]
        assert missing.status_code == 404


# --------------------------------------------------------------------- idempotency


class TestIdempotency:
    def _create(self, code: str = "idem") -> dict[str, Any]:
        return {"product_key": "hrms", "code": code, "name": "Idem", "reason": REASON}

    async def test_the_same_key_and_body_replays_the_first_answer(
        self, client: AsyncClient, platform_session: AsyncSession, admin_session: AsyncSession
    ) -> None:
        registry = FakeRegistry.default()
        headers = key()
        with acting(OWNER, registry):
            first = await client.post(f"{BASE}/plans", json=self._create(), headers=headers)
            second = await client.post(f"{BASE}/plans", json=self._create(), headers=headers)

        assert first.status_code == 201, first.text
        assert second.status_code == 201
        assert second.json() == first.json()
        assert second.headers.get("Idempotent-Replayed") == "true"
        assert "Idempotent-Replayed" not in first.headers
        assert registry.calls == 1, "the replay must not run the change again"
        async with admin_session.begin():
            assert await _count(admin_session, Plan) == 1
            assert (
                await _count(admin_session, AuditLog, AuditLog.action == "catalog.plan.created")
                == 1
            )
            audit = (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.action == "catalog.plan.created")
                )
            ).scalar_one()
            assert audit.reason == REASON

    async def test_the_same_key_on_a_different_body_is_409(self, client: AsyncClient) -> None:
        headers = key()
        with acting(OWNER):
            first = await client.post(f"{BASE}/plans", json=self._create("one"), headers=headers)
            second = await client.post(f"{BASE}/plans", json=self._create("two"), headers=headers)
        assert first.status_code == 201
        assert second.status_code == 409
        assert "different request" in second.json()["detail"]

    async def test_the_same_key_from_another_operator_is_409(self, client: AsyncClient) -> None:
        headers = key()
        with acting(OWNER):
            first = await client.post(f"{BASE}/plans", json=self._create(), headers=headers)
        with acting(operator_principal()):
            second = await client.post(f"{BASE}/plans", json=self._create(), headers=headers)
        assert first.status_code == 201
        assert second.status_code == 409

    async def test_a_missing_or_short_key_is_422_and_changes_nothing(
        self, client: AsyncClient, admin_session: AsyncSession
    ) -> None:
        with acting(OWNER):
            missing = await client.post(f"{BASE}/plans", json=self._create())
            short = await client.post(
                f"{BASE}/plans", json=self._create(), headers={"Idempotency-Key": "abc"}
            )
        assert missing.status_code == 422
        assert any(e["field"] == "header.Idempotency-Key" for e in missing.json()["errors"])
        assert short.status_code == 422
        async with admin_session.begin():
            assert await _count(admin_session, Plan) == 0

    async def test_a_refused_request_does_not_burn_its_key(
        self, client: AsyncClient, admin_session: AsyncSession
    ) -> None:
        """Only a change that happened is recorded: a 422 rolls its claim back, so the same key
        with a corrected body goes through rather than being refused as a different request."""
        headers = key()
        with acting(OWNER):
            wrong = await client.post(
                f"{BASE}/plans", json={**self._create(), "product_key": "nosuch"}, headers=headers
            )
            right = await client.post(f"{BASE}/plans", json=self._create(), headers=headers)
        assert wrong.status_code == 422
        assert wrong.json()["errors"][0]["code"] == "unknown_product"
        assert right.status_code == 201
        async with admin_session.begin():
            assert await _count(admin_session, IdempotencyKey) == 1

    async def test_a_retry_after_publish_replays_instead_of_refusing(
        self, client: AsyncClient, platform_session: AsyncSession, admin_session: AsyncSession
    ) -> None:
        """Without the key a retried publish was a 422 'already published'; now it is the same
        answer again, and the published event is staged once."""
        async with platform_session.begin():
            plan = await _plan(platform_session)
            version = await _draft(platform_session, plan)
        headers = key()
        with acting(OWNER):
            first = await client.post(
                f"{BASE}/versions/{version.id}/publish", json={"reason": REASON}, headers=headers
            )
            again = await client.post(
                f"{BASE}/versions/{version.id}/publish", json={"reason": REASON}, headers=headers
            )
        assert first.status_code == 200, first.text
        assert again.status_code == 200
        assert again.json() == first.json()
        async with admin_session.begin():
            assert (
                await _count(
                    admin_session,
                    OutboxEvent,
                    OutboxEvent.event_type == "catalog.plan_version.published",
                )
                == 1
            )

    async def test_concurrent_claims_of_one_key_execute_once(self) -> None:
        """Two sessions claim one key at once: the second waits on the unique index, then reads
        the first's committed answer and replays it. If the first rolls back, the second wins."""
        cfg = settings()
        engine = create_async_engine(
            cfg.app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)

        def bind(session: AsyncSession) -> None:
            @event.listens_for(session.sync_session, "after_begin")
            def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(
                    f"SELECT set_config('{TENANT_GUC}', '{PLATFORM_ORG}', true)"
                )

        fp = domain.request_fingerprint("plan.create", None, {"x": 1})
        try:
            for first_commits in (True, False):
                the_key = str(uuid.uuid4())
                async with maker() as first, maker() as second:
                    bind(first)
                    bind(second)
                    await first.begin()
                    claim = await services.claim_idempotency_key(
                        first, OWNER, key=the_key, action="plan.create", fingerprint=fp
                    )
                    assert claim.claim_id is not None
                    await second.begin()
                    racer = asyncio.create_task(
                        services.claim_idempotency_key(
                            second, OWNER, key=the_key, action="plan.create", fingerprint=fp
                        )
                    )
                    await asyncio.sleep(0.2)
                    assert not racer.done(), "the second claim must wait for the first"
                    if first_commits:
                        await services.complete_idempotency_key(
                            first, claim.claim_id, status_code=201, response={"ok": True}
                        )
                        await first.commit()
                        replay = await racer
                        assert replay.claim_id is None
                        assert (replay.replay_status, replay.replay_body) == (201, {"ok": True})
                    else:
                        await first.rollback()
                        won = await racer
                        assert won.claim_id is not None, "a rolled-back claim was never real"
                    await second.rollback()
        finally:
            await engine.dispose()

    async def test_claims_are_invisible_to_any_other_tenant_binding(
        self, platform_session: AsyncSession
    ) -> None:
        """`idempotency_keys` is policied like `audit_logs`: bound to another organization, the
        app role sees none of the platform's claims and cannot write one in its name."""
        async with platform_session.begin():
            await services.claim_idempotency_key(
                platform_session, OWNER, key="k" * 12, action="plan.create", fingerprint="f" * 64
            )
        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        try:
            async with engine.connect() as conn, conn.begin():
                await conn.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                    {"org": str(uuid.uuid4())},
                )
                seen = (
                    await conn.execute(text("SELECT count(*) FROM catalog.idempotency_keys"))
                ).scalar_one()
                assert seen == 0
                with pytest.raises(Exception, match="row-level security"):
                    await conn.execute(
                        text(
                            "INSERT INTO catalog.idempotency_keys "
                            "(id, organization_id, key, action, actor_id, fingerprint) VALUES "
                            "(gen_random_uuid(), CAST(:org AS uuid), 'kkkkkkkkkk', 'x', "
                            "gen_random_uuid(), 'f')"
                        ),
                        {"org": str(PLATFORM_ORG)},
                    )
        finally:
            await engine.dispose()


# --------------------------------------------------------------------- publish validation


def _price(**kwargs: Any) -> PriceIn:
    return PriceIn(**{"currency": "INR", **kwargs})


PUBLISH_CASES = [
    ("no prices", [], [], False, {"no_prices"}),
    (
        "usage without a meter",
        [_price(kind="usage", unit_price_micros=250)],
        [],
        False,
        {"meter_missing"},
    ),
    (
        "per seat without a unit",
        [_price(kind="per_seat", amount_minor=500)],
        [],
        False,
        {"unit_missing"},
    ),
    ("a zero flat amount", [_price(kind="flat", amount_minor=0)], [], False, {"zero_amount"}),
    (
        "a zero usage rate",
        [_price(kind="usage", unit_price_micros=0, meter_key="api_calls")],
        [],
        False,
        {"zero_amount"},
    ),
    ("a zero flat amount, acknowledged", [_price(kind="flat", amount_minor=0)], [], True, set()),
    (
        "a clean sheet",
        [
            _price(kind="flat", amount_minor=99_900),
            _price(kind="per_seat", amount_minor=40_000, unit="seat"),
            _price(kind="usage", unit_price_micros=250, meter_key="api_calls"),
        ],
        [FeatureIn(feature_key="trial_days", value=14)],
        False,
        set(),
    ),
]


class TestPublishValidation:
    @pytest.mark.parametrize(
        ("label", "prices", "features", "allow_zero", "codes"),
        PUBLISH_CASES,
        ids=[case[0] for case in PUBLISH_CASES],
    )
    async def test_each_sheet_publishes_or_names_its_problems(
        self,
        platform_session: AsyncSession,
        label: str,
        prices: list[PriceIn],
        features: list[FeatureIn],
        allow_zero: bool,
        codes: set[str],
    ) -> None:
        async with platform_session.begin():
            plan = await _plan(platform_session)
            # An empty sheet would clone the previous version, so the no-price draft is written
            # with a price and then has it removed, the way only a draft's sheet can be.
            version = await _draft(platform_session, plan, prices=prices or None, features=features)
            if not prices:
                await platform_session.execute(
                    text("DELETE FROM catalog.prices WHERE plan_version_id = CAST(:id AS uuid)"),
                    {"id": str(version.id)},
                )
        try:
            async with platform_session.begin():
                fresh = await services.require_version_for_update(platform_session, version.id)
                await services.publish_version(
                    platform_session, OWNER, fresh, request=None, allow_zero_amounts=allow_zero
                )
        except BusinessRuleProblem as problem:
            assert {e["code"] for e in problem.errors or []} == codes, label
        else:
            assert codes == set(), f"{label}: expected a refusal"

    async def test_a_currency_outside_the_allow_list_cannot_be_published(
        self, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            plan = await _plan(platform_session)
            version = await _draft(platform_session, plan, currency="XTS")
        with pytest.raises(BusinessRuleProblem) as caught:
            async with platform_session.begin():
                fresh = await services.require_version_for_update(platform_session, version.id)
                await services.publish_version(platform_session, OWNER, fresh, request=None)
        assert {e["code"] for e in caught.value.errors or []} == {"currency_not_sellable"}

    async def test_trial_days_are_rechecked_at_publish(
        self, platform_session: AsyncSession
    ) -> None:
        """Validated at draft too; a draft's sheet is editable, so publish checks again."""
        async with platform_session.begin():
            plan = await _plan(platform_session)
            version = await _draft(
                platform_session, plan, features=[FeatureIn(feature_key="trial_days", value=14)]
            )
            await platform_session.execute(
                text(
                    "UPDATE catalog.features SET value = '900'::jsonb "
                    "WHERE plan_version_id = CAST(:id AS uuid)"
                ),
                {"id": str(version.id)},
            )
        with pytest.raises(BusinessRuleProblem) as caught:
            async with platform_session.begin():
                fresh = await services.require_version_for_update(platform_session, version.id)
                await services.publish_version(platform_session, OWNER, fresh, request=None)
        assert {e["code"] for e in caught.value.errors or []} == {"trial_days_invalid"}

    async def test_bundle_members_are_rechecked_against_todays_registry(
        self, platform_session: AsyncSession
    ) -> None:
        then = FakeRegistry(
            [
                RegisteredProduct("suite", is_bundle=True),
                RegisteredProduct("hrms"),
                RegisteredProduct("gone"),
            ]
        )
        now = FakeRegistry([RegisteredProduct("suite", is_bundle=True), RegisteredProduct("hrms")])
        async with platform_session.begin():
            plan = await _plan(platform_session, product_key="suite")
            version = await _draft(
                platform_session,
                plan,
                features=[FeatureIn(feature_key="bundled_products", value=["hrms", "gone"])],
                registry=then,
            )
            version_id = version.id
        for registry, code in ((now, "bundle_member"), (None, "bundle_unverifiable")):
            with pytest.raises(BusinessRuleProblem) as caught:
                async with platform_session.begin():
                    fresh = await services.require_version_for_update(platform_session, version_id)
                    await services.publish_version(
                        platform_session, OWNER, fresh, request=None, registry=registry
                    )
            assert {e["code"] for e in caught.value.errors or []} == {code}

    async def test_over_http_the_problems_arrive_as_errors(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            plan = await _plan(platform_session)
            version = await _draft(
                platform_session, plan, prices=[_price(kind="flat", amount_minor=0)]
            )
            version_id = version.id
        with acting(OWNER):
            refused = await client.post(
                f"{BASE}/versions/{version_id}/publish", json={"reason": REASON}, headers=key()
            )
            allowed = await client.post(
                f"{BASE}/versions/{version_id}/publish",
                json={"reason": REASON, "allow_zero_amounts": True},
                headers=key(),
            )
        assert refused.status_code == 422
        assert refused.json()["errors"][0]["field"] == "prices.0.amount_minor"
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["state"] == "published"


# --------------------------------------------------------------------- the trigger


class TestWithdrawTrigger:
    async def _published_id(self, session: AsyncSession) -> uuid.UUID:
        async with session.begin():
            plan = await _plan(session)
            version = await _published(session, plan)
        return version.id

    async def _raw(self, session: AsyncSession, sql: str, version_id: uuid.UUID) -> None:
        async with session.begin():
            await session.execute(text(sql), {"id": str(version_id)})

    async def test_exactly_published_to_grandfathered_is_allowed(
        self, platform_session: AsyncSession
    ) -> None:
        version_id = await self._published_id(platform_session)
        await self._raw(
            platform_session,
            "UPDATE catalog.plan_versions SET state = 'grandfathered' WHERE id = CAST(:id AS uuid)",
            version_id,
        )
        async with platform_session.begin():
            fresh = await selectors.get_version(platform_session, version_id)
            assert fresh is not None and fresh.state == "grandfathered"

    @pytest.mark.parametrize(
        "sql",
        [
            # The withdraw, smuggling a second change with it.
            "UPDATE catalog.plan_versions SET state = 'grandfathered', currency = 'USD' "
            "WHERE id = CAST(:id AS uuid)",
            "UPDATE catalog.plan_versions SET state = 'grandfathered', updated_at = now() "
            "+ interval '1 day' WHERE id = CAST(:id AS uuid)",
            "UPDATE catalog.plan_versions SET state = 'draft' WHERE id = CAST(:id AS uuid)",
            "UPDATE catalog.plan_versions SET restricted_to_org_id = gen_random_uuid() "
            "WHERE id = CAST(:id AS uuid)",
        ],
    )
    async def test_every_other_change_to_a_published_row_is_refused(
        self, platform_session: AsyncSession, sql: str
    ) -> None:
        version_id = await self._published_id(platform_session)
        with pytest.raises(IntegrityError, match="published and immutable"):
            await self._raw(platform_session, sql, version_id)

    @pytest.mark.parametrize(
        "sql",
        [
            "UPDATE catalog.plan_versions SET state = 'published' WHERE id = CAST(:id AS uuid)",
            "UPDATE catalog.plan_versions SET billing_interval = 'year' "
            "WHERE id = CAST(:id AS uuid)",
            "UPDATE catalog.plan_versions SET state = 'grandfathered' WHERE id = CAST(:id AS uuid)",
        ],
    )
    async def test_a_grandfathered_row_cannot_be_updated_at_all(
        self, platform_session: AsyncSession, sql: str
    ) -> None:
        version_id = await self._published_id(platform_session)
        await self._raw(
            platform_session,
            "UPDATE catalog.plan_versions SET state = 'grandfathered' WHERE id = CAST(:id AS uuid)",
            version_id,
        )
        with pytest.raises(IntegrityError, match="grandfathered and immutable"):
            await self._raw(platform_session, sql, version_id)

    async def test_a_draft_stays_editable(self, platform_session: AsyncSession) -> None:
        async with platform_session.begin():
            plan = await _plan(platform_session)
            draft = await _draft(platform_session, plan)
        await self._raw(
            platform_session,
            "UPDATE catalog.plan_versions SET billing_interval = 'year' "
            "WHERE id = CAST(:id AS uuid)",
            draft.id,
        )


# --------------------------------------------------------------------- withdraw / delete / retire


class TestLifecycleWrites:
    async def test_withdraw_grandfathers_audits_and_emits(
        self,
        client: AsyncClient,
        platform_session: AsyncSession,
        admin_session: AsyncSession,
        org_a: uuid.UUID,
    ) -> None:
        async with platform_session.begin():
            plan = await _plan(platform_session)
            version = await _published(platform_session, plan)
            version_id = version.id
        with acting(OWNER):
            resp = await client.post(
                f"{BASE}/versions/{version_id}/withdraw", json={"reason": REASON}, headers=key()
            )
            again = await client.post(
                f"{BASE}/versions/{version_id}/withdraw", json={"reason": REASON}, headers=key()
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "grandfathered"
        assert resp.json()["prices"][0]["amount_minor"] == 99_900, "the sheet is untouched"
        assert again.status_code == 422, "a grandfathered version cannot be withdrawn twice"

        async with platform_session.begin():
            offered = await selectors.visible_plans(
                platform_session, product_key="hrms", org_id=org_a
            )
            assert [v for _, vs in offered for v in vs] == [], "no customer is offered it now"
        async with admin_session.begin():
            audit = (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.action == "catalog.plan_version.grandfathered")
                )
            ).scalar_one()
            assert audit.reason == REASON
            assert audit.entity_id == version_id
            emitted = (
                await admin_session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "catalog.plan_version.grandfathered"
                    )
                )
            ).scalar_one()
            assert emitted.aggregate_id == version_id

    async def test_withdraw_refuses_a_draft(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            plan = await _plan(platform_session)
            draft = await _draft(platform_session, plan)
        with acting(OWNER):
            resp = await client.post(
                f"{BASE}/versions/{draft.id}/withdraw", json={"reason": REASON}, headers=key()
            )
            missing = await client.post(
                f"{BASE}/versions/{uuid.uuid4()}/withdraw", json={"reason": REASON}, headers=key()
            )
        assert resp.status_code == 422
        assert missing.status_code == 404

    async def test_delete_removes_a_draft_and_its_sheet(
        self, client: AsyncClient, platform_session: AsyncSession, admin_session: AsyncSession
    ) -> None:
        """Including a seat class charged at one of the draft's own per-seat prices — the
        `ON DELETE RESTRICT` key that makes the delete order matter."""
        async with platform_session.begin():
            plan = await _plan(platform_session)
            draft = await _draft(
                platform_session,
                plan,
                prices=[_price(kind="per_seat", amount_minor=40_000, unit="seat")],
                features=[FeatureIn(feature_key="api", value=True)],
                seat_classes=[SeatClassIn(code="member", name="Member", price_index=0)],
            )
        with acting(OWNER):
            resp = await client.request(
                "DELETE", f"{BASE}/versions/{draft.id}", json={"reason": REASON}, headers=key()
            )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "id": str(draft.id),
            "plan_id": str(plan.id),
            "version_no": 1,
            "deleted": True,
        }
        async with admin_session.begin():
            for table, sql in (
                ("plan_versions", "SELECT count(*) FROM catalog.plan_versions"),
                ("prices", "SELECT count(*) FROM catalog.prices"),
                ("features", "SELECT count(*) FROM catalog.features"),
                ("seat_classes", "SELECT count(*) FROM catalog.seat_classes"),
            ):
                left = (await admin_session.execute(text(sql))).scalar_one()
                assert left == 0, table
            audit = (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.action == "catalog.plan_version.draft_deleted")
                )
            ).scalar_one()
            assert audit.reason == REASON

    async def test_delete_refuses_a_published_version(
        self, client: AsyncClient, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            plan = await _plan(platform_session)
            version = await _published(platform_session, plan)
        with acting(OWNER):
            resp = await client.request(
                "DELETE", f"{BASE}/versions/{version.id}", json={"reason": REASON}, headers=key()
            )
        assert resp.status_code == 422
        assert "withdraw" in resp.json()["detail"]

    async def test_retire_then_reactivate(
        self, client: AsyncClient, platform_session: AsyncSession, admin_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            plan = await _plan(platform_session)
        with acting(OWNER):
            retired = await client.post(
                f"{BASE}/plans/{plan.id}/retire", json={"reason": REASON}, headers=key()
            )
            back = await client.post(
                f"{BASE}/plans/{plan.id}/reactivate", json={"reason": REASON}, headers=key()
            )
            twice = await client.post(
                f"{BASE}/plans/{plan.id}/reactivate", json={"reason": REASON}, headers=key()
            )
        assert retired.status_code == 200 and retired.json()["state"] == "retired"
        assert back.status_code == 200 and back.json()["state"] == "active"
        assert twice.status_code == 422
        async with admin_session.begin():
            assert (
                await _count(
                    admin_session, OutboxEvent, OutboxEvent.event_type == "catalog.plan.reactivated"
                )
                == 1
            )

    async def test_a_reason_is_required_and_bounded(self, client: AsyncClient) -> None:
        body = {"product_key": "hrms", "code": "why", "name": "Why"}
        with acting(OWNER):
            for reason in (None, "  a ", "x" * 501):
                payload = body if reason is None else {**body, "reason": reason}
                resp = await client.post(f"{BASE}/plans", json=payload, headers=key())
                assert resp.status_code == 422, reason

    async def test_a_plan_needs_a_registered_non_platform_product(
        self, client: AsyncClient
    ) -> None:
        with acting(OWNER):
            unknown = await client.post(
                f"{BASE}/plans",
                json={"product_key": "hrsm", "code": "x", "name": "X", "reason": REASON},
                headers=key(),
            )
            platform = await client.post(
                f"{BASE}/plans",
                json={"product_key": "core", "code": "x", "name": "X", "reason": REASON},
                headers=key(),
            )
        assert unknown.status_code == 422
        assert unknown.json()["errors"][0]["code"] == "unknown_product"
        assert platform.status_code == 422
        assert platform.json()["errors"][0]["code"] == "platform_product"


class TestDraftRace:
    async def test_losing_the_version_number_race_retries_with_the_next_number(
        self, platform_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first attempt reads a stale highest number (as a concurrent draft would leave it),
        collides on `uq_plan_versions_plan_version` inside its savepoint, and the retry drafts
        the next number on the same, still-usable transaction."""
        async with platform_session.begin():
            plan = await _plan(platform_session)
            await _published(platform_session, plan)

        real = selectors.latest_version_no
        stale = {"left": 1}

        async def flaky(session: AsyncSession, plan_id: uuid.UUID) -> int:
            if stale["left"]:
                stale["left"] -= 1
                return 0
            return await real(session, plan_id)

        monkeypatch.setattr(selectors, "latest_version_no", flaky)
        async with platform_session.begin():
            version = await services.draft_version(
                platform_session,
                OWNER,
                plan,
                VersionCreateIn(currency="INR", prices=[_price(kind="flat", amount_minor=1_000)]),
                request=None,
                registry=None,
                reason=REASON,
            )
        assert version.version_no == 2

    async def test_it_gives_up_with_a_409(
        self, platform_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with platform_session.begin():
            plan = await _plan(platform_session)
            await _published(platform_session, plan)

        async def always_stale(_session: AsyncSession, _plan_id: uuid.UUID) -> int:
            return 0

        monkeypatch.setattr(selectors, "latest_version_no", always_stale)
        with pytest.raises(ConflictProblem):
            async with platform_session.begin():
                await services.draft_version(
                    platform_session,
                    OWNER,
                    plan,
                    VersionCreateIn(
                        currency="INR", prices=[_price(kind="flat", amount_minor=1_000)]
                    ),
                    request=None,
                    registry=None,
                    reason=REASON,
                )
