"""Staff plan switching on console: `plan-options`, `plan-change:preview`, `plan-change`.

Properties covered, each with its negative case (ADR-0029, the 2026-10-06 design):

1. **Authorization on every route** — a product token is a 404, a staff session that has not
   completed its second factor is a 403, `support` is a 403 (a bespoke price is a customer's
   negotiated rate), `owner` and `billing_ops` are admitted, no token is a 401 — and a refused call
   reaches no downstream service and writes no claim.
2. **The picker** composes live reads and marks what a subscription cannot take.
3. **The preview** is subscription's own answer, read-only on console's side.
4. **The confirm — claim, call, record.** A *required* `Idempotency-Key` whose claim row is written
   before the downstream call; a retry replays; a different body is a 409; concurrent requests make
   one call; a definitive refusal is recorded and replayed; an unknown outcome is a 503 that a retry
   reconciles from a live read; every attempt leaves an intent row and an outcome row.
5. **No immediate path.** There is no value of `timing` that forces a paying subscription to switch
   now, and the call that reaches subscription is always `timing="next_period"`.
6. **Tenant isolation** — the claim table is RLS-real: another organization's claim is invisible to
   a session bound elsewhere, and a key is scoped by `(organization, key)`.

Console's tests mock the downstream and so cannot prove subscription-side isolation (another
organization's subscription id, a stale expected state) — those live in subscription's own
`tests/test_operator_plan_change.py`.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import get_delegation_client
from app.config import settings
from app.main import app
from app.models import SCHEMA, AuditLog, IdempotencyKey
from platform_core.db import TENANT_GUC, get_session
from tests.helpers import as_principal, operator_principal
from tests.test_pricing import (
    FakeUpstreams,
    _stub_directory,
    _stub_plan_version,
    _stub_subscriptions,
    _subscription,
    _version_doc,
)

pytestmark = pytest.mark.integration

PRODUCT = "hrms"
KEY = "plan-change-key-0001"


@pytest.fixture
def upstreams() -> FakeUpstreams:
    return FakeUpstreams()


@pytest.fixture(autouse=True)
def _wire_upstreams(upstreams: FakeUpstreams) -> AsyncIterator[None]:
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(upstreams))

    async def _override() -> AsyncIterator[httpx.AsyncClient]:
        yield mock_client

    app.dependency_overrides[get_delegation_client] = _override
    yield
    app.dependency_overrides.pop(get_delegation_client, None)


# --------------------------------------------------------------------------------- helpers


def _urls(org: uuid.UUID) -> dict[str, str]:
    base = f"/api/v1/admin/organizations/{org}"
    return {
        "options": f"{base}/plan-options",
        "preview": f"{base}/subscriptions/{PRODUCT}/plan-change:preview",
        "change": f"{base}/subscriptions/{PRODUCT}/plan-change",
    }


def _expected(current: uuid.UUID, *, state: str = "active", pending: uuid.UUID | None = None):
    return {
        "state": state,
        "plan_version_id": str(current),
        "pending_plan_version_id": str(pending) if pending else None,
    }


def _stub_subscription(
    upstreams: FakeUpstreams,
    org: uuid.UUID,
    *,
    subscription_id: uuid.UUID,
    current: uuid.UUID,
    state: str = "active",
    pending: uuid.UUID | None = None,
) -> None:
    _stub_subscriptions(
        upstreams,
        org,
        [
            _subscription(
                subscription_id=subscription_id,
                plan_version_id=current,
                state=state,
                pending_plan_version_id=pending,
            )
        ],
    )


def _offerable_path(org: uuid.UUID) -> str:
    return f"/internal/v1/organizations/{org}/offerable-versions"


def _offered(version_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    """One row of catalog's offerable-versions read, as the picker and the re-check consume it."""
    return {
        "plan_id": str(uuid.uuid4()),
        "plan_code": "growth",
        "plan_name": overrides.pop("plan_name", "Growth"),
        "product_key": PRODUCT,
        "plan_version_id": str(version_id),
        "version_no": overrides.pop("version_no", 1),
        "state": "published",
        "billing_interval": overrides.pop("billing_interval", "month"),
        "currency": overrides.pop("currency", "INR"),
        "restricted_to_org_id": overrides.pop("restricted_to_org_id", None),
        "published_at": None,
        "prices": overrides.pop(
            "prices",
            [{"id": str(uuid.uuid4()), "kind": "flat", "amount_minor": 100000, "currency": "INR"}],
        ),
        "features": overrides.pop("features", []),
        **overrides,
    }


def _stub_offerable(upstreams: FakeUpstreams, org: uuid.UUID, *version_ids: uuid.UUID) -> None:
    """What catalog offers `org` now — the set the preview and the confirm re-check against."""
    upstreams.json("GET", _offerable_path(org), {"versions": [_offered(v) for v in version_ids]})


def _change_path(subscription_id: uuid.UUID) -> str:
    return f"/internal/v1/subscriptions/{subscription_id}/change-plan"


def _preview_path(subscription_id: uuid.UUID) -> str:
    return f"{_change_path(subscription_id)}/preview"


def _changed(
    subscription_id: uuid.UUID,
    *,
    current: uuid.UUID,
    pending: uuid.UUID | None = None,
    state: str = "active",
) -> dict[str, Any]:
    return _subscription(
        subscription_id=subscription_id,
        plan_version_id=current,
        state=state,
        pending_plan_version_id=pending,
    )


def _body(
    target: uuid.UUID, current: uuid.UUID, *, reason: str = "Agreed on the renewal call", **extra
) -> dict[str, Any]:
    return {
        "plan_version_id": str(target),
        "reason": reason,
        "expected": _expected(current),
        **extra,
    }


async def _post_change(
    client: AsyncClient,
    org: uuid.UUID,
    body: dict[str, Any],
    *,
    key: str | None = KEY,
    principal: Any = None,
) -> httpx.Response:
    headers = {} if key is None else {"Idempotency-Key": key}
    with as_principal(principal or operator_principal(roles=["billing_ops"])):
        return await client.post(_urls(org)["change"], json=body, headers=headers)


async def _claims(admin: AsyncSession, org: uuid.UUID) -> list[IdempotencyKey]:
    admin.expunge_all()
    rows = await admin.execute(
        select(IdempotencyKey)
        .where(IdempotencyKey.organization_id == org)
        .order_by(IdempotencyKey.created_at)
    )
    return list(rows.scalars().all())


async def _audits(admin: AsyncSession, org: uuid.UUID, like: str = "subscription.plan_change%"):
    admin.expunge_all()
    rows = await admin.execute(
        select(AuditLog)
        .where(AuditLog.organization_id == org, AuditLog.action.like(like))
        .order_by(AuditLog.at, AuditLog.id)
    )
    return list(rows.scalars().all())


def _code(response: httpx.Response) -> str | None:
    errors = response.json().get("errors") or []
    return errors[0].get("code") if errors else None


@pytest.fixture
def world(upstreams: FakeUpstreams, org_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """A live paying subscription on `current`, with `target` a dearer version catalog offers this
    organization, all stubbed."""
    ids = {
        "sub": uuid.uuid4(),
        "current": uuid.uuid4(),
        "target": uuid.uuid4(),
    }
    _stub_directory(upstreams, org_id)
    _stub_subscription(upstreams, org_id, subscription_id=ids["sub"], current=ids["current"])
    _stub_offerable(upstreams, org_id, ids["current"], ids["target"])
    upstreams.json(
        "POST",
        _change_path(ids["sub"]),
        _changed(ids["sub"], current=ids["current"], pending=ids["target"]),
    )
    return ids


# ======================================================================== 1. authorization


class TestAuthorization:
    """Every route, every caller. A refusal reaches nothing downstream and claims nothing."""

    def _calls(self, client: AsyncClient, org: uuid.UUID, world: dict[str, uuid.UUID]):
        target, current = world["target"], world["current"]
        urls = _urls(org)
        return {
            "options": lambda: client.get(urls["options"], params={"product_key": PRODUCT}),
            "preview": lambda: client.post(urls["preview"], json={"plan_version_id": str(target)}),
            "change": lambda: client.post(
                urls["change"], json=_body(target, current), headers={"Idempotency-Key": KEY}
            ),
        }

    @pytest.mark.parametrize("route", ["options", "preview", "change"])
    @pytest.mark.parametrize(
        ("principal", "status"),
        [
            (operator_principal(staff=False), 404),
            (operator_principal(amr=["pwd"]), 403),
            (operator_principal(roles=["support"]), 403),
            (operator_principal(roles=[]), 403),
        ],
        ids=["product-audience", "no-otp", "support", "no-role"],
    )
    async def test_refused_callers_reach_nothing(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        route: str,
        principal: Any,
        status: int,
    ) -> None:
        with as_principal(principal):
            response = await self._calls(client, org_id, world)[route]()
        assert response.status_code == status, response.text
        assert upstreams.requests == [], "a refused caller must reach no downstream service"
        assert await _claims(admin_session, org_id) == []
        assert await _audits(admin_session, org_id, "%") == []

    @pytest.mark.parametrize("route", ["options", "preview", "change"])
    async def test_an_unauthenticated_call_is_a_401(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        route: str,
    ) -> None:
        response = await self._calls(client, org_id, world)[route]()
        assert response.status_code == 401, response.text
        assert upstreams.requests == []

    @pytest.mark.parametrize("roles", [["owner"], ["billing_ops"]])
    async def test_owner_and_billing_ops_are_admitted_to_every_route(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        roles: list[str],
    ) -> None:
        _stub_plan_version(
            upstreams, world["current"], _version_doc(world["current"], amount_minor=1000)
        )
        upstreams.json(
            "POST",
            _preview_path(world["sub"]),
            {"expected": _expected(world["current"]), "effective": "next_period"},
        )
        with as_principal(operator_principal(roles=roles)):
            calls = self._calls(client, org_id, world)
            assert (await calls["options"]()).status_code == 200
            assert (await calls["preview"]()).status_code == 200
            assert (await calls["change"]()).status_code == 200

    @pytest.mark.parametrize("route", ["options", "preview", "change"])
    async def test_an_unknown_organization_is_a_404_before_anything_else(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        route: str,
    ) -> None:
        _stub_directory(upstreams, org_id, found=False)
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await self._calls(client, org_id, world)[route]()
        assert response.status_code == 404, response.text
        assert [r.url.path for r in upstreams.requests if "internal" in r.url.path] == []
        assert await _claims(admin_session, org_id) == []

    async def test_a_product_key_that_is_not_a_product_key_is_refused_before_any_call(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        base = f"/api/v1/admin/organizations/{org_id}"
        with as_principal(operator_principal()):
            odd = await client.post(
                f"{base}/subscriptions/..%2F..%2Fadmin/plan-change:preview",
                json={"plan_version_id": str(uuid.uuid4())},
            )
            query = await client.get(f"{base}/plan-options", params={"product_key": "../x"})
        assert odd.status_code in (404, 422) and query.status_code == 422
        assert upstreams.requests == []


# ============================================================================== 2. the picker


class TestPlanOptions:
    def _offered(self, version_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
        return {
            "plan_id": str(uuid.uuid4()),
            "plan_code": "growth",
            "plan_name": overrides.pop("plan_name", "Growth"),
            "product_key": PRODUCT,
            "plan_version_id": str(version_id),
            "version_no": overrides.pop("version_no", 1),
            "state": "published",
            "billing_interval": overrides.pop("billing_interval", "month"),
            "currency": overrides.pop("currency", "INR"),
            "restricted_to_org_id": overrides.pop("restricted_to_org_id", None),
            "published_at": None,
            "prices": overrides.pop(
                "prices",
                [
                    {
                        "id": str(uuid.uuid4()),
                        "kind": "flat",
                        "amount_minor": 100000,
                        "currency": "INR",
                    }
                ],
            ),
            "features": overrides.pop("features", []),
            **overrides,
        }

    async def test_it_composes_the_live_subscription_and_the_offerable_versions(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        sub_id, current, pending = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        plain, bespoke = uuid.uuid4(), uuid.uuid4()
        _stub_directory(upstreams, org_id)
        _stub_subscription(
            upstreams, org_id, subscription_id=sub_id, current=current, pending=pending
        )
        upstreams.json(
            "GET",
            f"/internal/v1/organizations/{org_id}/offerable-versions",
            {
                "versions": [
                    self._offered(current, plan_name="Starter"),
                    self._offered(pending, plan_name="Growth", version_no=2),
                    self._offered(
                        plain,
                        plan_name="Scale",
                        prices=[
                            {
                                "id": str(uuid.uuid4()),
                                "kind": "flat",
                                "amount_minor": 300000,
                                "currency": "INR",
                            },
                            {
                                "id": str(uuid.uuid4()),
                                "kind": "per_seat",
                                "amount_minor": 5000,
                                "currency": "INR",
                                "unit": "seat",
                            },
                            {
                                "id": str(uuid.uuid4()),
                                "kind": "usage",
                                "currency": "INR",
                                "unit_price_micros": 1_500_000,
                                "meter_key": "api",
                            },
                        ],
                        features=[
                            {"feature_key": "trial_days", "value": 30},
                            {"feature_key": "bundled_products", "value": ["hrms", "crm"]},
                        ],
                    ),
                    self._offered(bespoke, plan_name="Growth", restricted_to_org_id=str(org_id)),
                    self._offered(uuid.uuid4(), plan_name="Dollar", currency="USD"),
                    self._offered(uuid.uuid4(), plan_name="Yearly", billing_interval="year"),
                    self._offered(uuid.uuid4(), plan_name="Once", billing_interval="one_time"),
                ]
            },
        )
        _stub_plan_version(upstreams, current, _version_doc(current, amount_minor=100000))
        upstreams.json(
            "GET",
            f"/internal/v1/plan-versions/{pending}",
            {**_version_doc(pending, amount_minor=150000), "plan_name": "Growth"},
        )
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get(_urls(org_id)["options"], params={"product_key": PRODUCT})
        assert response.status_code == 200, response.text
        body = response.json()

        sub = body["subscription"]
        assert sub["id"] == str(sub_id) and sub["state"] == "active"
        assert sub["plan_version_id"] == str(current)
        assert (
            sub["pending_plan_version_id"] == str(pending) and sub["pending_plan_name"] == "Growth"
        )
        assert sub["billing_interval"] == "month" and sub["change_blocked_code"] is None

        by_name = {
            (o["plan_name"], o["billing_interval"], o["currency"]): o for o in body["options"]
        }
        assert by_name[("Starter", "month", "INR")]["is_current"] is True
        assert by_name[("Growth", "month", "INR")]["is_pending"] or any(
            o["is_pending"] for o in body["options"]
        )
        scale = by_name[("Scale", "month", "INR")]
        assert scale["recurring_minor"] == 305000  # flat + per-seat at quantity one; usage ignored
        assert scale["trial_days"] == 30 and scale["bundled_products"] == ["hrms", "crm"]
        assert scale["blocked_code"] is None
        assert any(o["is_bespoke"] for o in body["options"])
        assert by_name[("Dollar", "month", "USD")]["blocked_code"] == "currency"
        assert by_name[("Yearly", "year", "INR")]["blocked_code"] == "cadence"
        assert by_name[("Once", "one_time", "INR")]["blocked_code"] == "cadence"

        # Catalog is asked about *this* organization and *this* product, and nothing else.
        (asked,) = [r for r in upstreams.requests if r.url.path.endswith("offerable-versions")]
        assert asked.url.params["product_key"] == PRODUCT
        assert str(org_id) in asked.url.path

    async def test_after_a_reprice_staff_see_the_new_monthly_and_yearly_and_only_the_month_fits(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        """The 2026-10-06 list prices (ADR-0035): the plan the subscription is on (v1, the old
        monthly price) is superseded, so catalog offers v2 (the new yearly price) and v3 (the new
        monthly one). Staff may switch a monthly subscription to v3; v2 is a different cadence."""
        sub_id, current, yearly, monthly = (uuid.uuid4() for _ in range(4))
        _stub_directory(upstreams, org_id)
        _stub_subscription(upstreams, org_id, subscription_id=sub_id, current=current)

        def _flat(amount_minor: int) -> list[dict[str, Any]]:
            return [
                {
                    "id": str(uuid.uuid4()),
                    "kind": "flat",
                    "amount_minor": amount_minor,
                    "currency": "INR",
                }
            ]

        upstreams.json(
            "GET",
            f"/internal/v1/organizations/{org_id}/offerable-versions",
            {
                "versions": [
                    self._offered(
                        yearly,
                        plan_name="People",
                        version_no=2,
                        billing_interval="year",
                        prices=_flat(3000000),
                    ),
                    self._offered(monthly, plan_name="People", version_no=3, prices=_flat(300000)),
                ]
            },
        )
        _stub_plan_version(upstreams, current, _version_doc(current, amount_minor=199900))
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get(_urls(org_id)["options"], params={"product_key": PRODUCT})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["subscription"]["billing_interval"] == "month"
        by_id = {o["plan_version_id"]: o for o in body["options"]}
        assert set(by_id) == {str(yearly), str(monthly)}, "the old version is no longer offered"
        assert by_id[str(monthly)]["blocked_code"] is None
        assert by_id[str(monthly)]["recurring_minor"] == 300000
        assert by_id[str(yearly)]["blocked_code"] == "cadence"
        assert by_id[str(yearly)]["recurring_minor"] == 3000000

    @pytest.mark.parametrize("state", ["past_due", "suspended", "cancelled"])
    async def test_a_subscription_that_cannot_change_says_so_up_front(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID, state: str
    ) -> None:
        sub_id, current = uuid.uuid4(), uuid.uuid4()
        _stub_directory(upstreams, org_id)
        _stub_subscription(upstreams, org_id, subscription_id=sub_id, current=current, state=state)
        upstreams.json(
            "GET", f"/internal/v1/organizations/{org_id}/offerable-versions", {"versions": []}
        )
        _stub_plan_version(upstreams, current, _version_doc(current, amount_minor=1000))
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get(_urls(org_id)["options"], params={"product_key": PRODUCT})
        assert response.json()["subscription"]["change_blocked_code"] == "state"

    async def test_no_subscription_for_the_product_is_a_404_with_a_code(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        _stub_directory(upstreams, org_id)
        _stub_subscriptions(upstreams, org_id, [])
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get(_urls(org_id)["options"], params={"product_key": PRODUCT})
        assert response.status_code == 404 and _code(response) == "no_subscription"

    async def test_catalog_being_down_is_a_503(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        sub_id, current = uuid.uuid4(), uuid.uuid4()
        _stub_directory(upstreams, org_id)
        _stub_subscription(upstreams, org_id, subscription_id=sub_id, current=current)
        upstreams.json(
            "GET", f"/internal/v1/organizations/{org_id}/offerable-versions", {"detail": "x"}, 503
        )
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get(_urls(org_id)["options"], params={"product_key": PRODUCT})
        assert response.status_code == 503


# ============================================================================== 3. the preview


class TestPreview:
    async def test_it_is_subscriptions_own_answer_and_writes_nothing(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        answer = {
            "expected": _expected(world["current"]),
            "action": "switch",
            "direction": "upgrade",
            "effective": "next_period",
            "effective_at": "2026-02-01T00:00:00+00:00",
            "would_clear_pending": False,
            "entitled_added": [],
            "entitled_removed": [],
            "usage_priced": True,
            # Passed on as subscription said it: the copy's "is anything charged now" rests on it.
            "usage_closed_now": True,
            "refusal_code": None,
            "refusal_detail": None,
        }
        upstreams.json("POST", _preview_path(world["sub"]), answer)
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.post(
                _urls(org_id)["preview"], json={"plan_version_id": str(world["target"])}
            )
        assert response.status_code == 200, response.text
        assert response.json() == {**answer, "effective_at": "2026-02-01T00:00:00Z"}
        (sent,) = [r for r in upstreams.requests if r.url.path == _preview_path(world["sub"])]
        assert json.loads(sent.content) == {
            "organization_id": str(org_id),
            "plan_version_id": str(world["target"]),
        }
        assert await _claims(admin_session, org_id) == []
        assert await _audits(admin_session, org_id, "%") == []

    async def test_a_refusal_is_part_of_the_answer(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        upstreams.json(
            "POST",
            _preview_path(world["sub"]),
            {
                "expected": _expected(world["current"], state="past_due"),
                "refusal_code": "state",
                "refusal_detail": "a past_due subscription cannot change plan",
            },
        )
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.post(
                _urls(org_id)["preview"], json={"plan_version_id": str(world["target"])}
            )
        assert response.status_code == 200
        assert response.json()["refusal_code"] == "state" and response.json()["direction"] is None

    async def test_an_unknown_version_is_a_404_that_keeps_its_code(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        upstreams.json(
            "POST",
            _preview_path(world["sub"]),
            {"detail": "No such plan version", "errors": [{"code": "not_found", "message": "x"}]},
            404,
        )
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.post(
                _urls(org_id)["preview"], json={"plan_version_id": str(uuid.uuid4())}
            )
        assert response.status_code == 404 and _code(response) == "not_found"

    @pytest.mark.parametrize(
        "why", ["superseded-by-a-newer-version", "plan-retired", "never-offered-to-this-org"]
    )
    async def test_a_version_catalog_no_longer_offers_is_refused_as_an_answer(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        why: str,
    ) -> None:
        """Subscription would accept it (it is published and this organization's to see), but the
        catalogue has moved on: a newer version superseded it, or its plan was retired — either
        way it is absent from what catalog offers now. The picker the operator opened earlier may
        still list it; the preview refuses it from a fresh read."""
        stale = world["target"]
        newer = uuid.uuid4()
        offered = {
            "superseded-by-a-newer-version": [world["current"], newer],
            "plan-retired": [world["current"]],
            "never-offered-to-this-org": [world["current"], newer],
        }[why]
        _stub_offerable(upstreams, org_id, *offered)
        upstreams.json(
            "POST",
            _preview_path(world["sub"]),
            {
                "expected": _expected(world["current"]),
                "action": "switch",
                "direction": "upgrade",
                "effective": "next_period",
                "effective_at": "2026-02-01T00:00:00+00:00",
            },
        )
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.post(
                _urls(org_id)["preview"], json={"plan_version_id": str(stale)}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["refusal_code"] == "not_offerable" and body["refusal_detail"]
        assert body["direction"] is None and body["effective"] is None
        assert body["expected"] == _expected(world["current"])
        assert upstreams.calls("GET", _offerable_path(org_id)) == 1
        assert await _claims(admin_session, org_id) == []

    async def test_subscriptions_own_refusal_wins_and_needs_no_catalog_read(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        _stub_offerable(upstreams, org_id, world["current"])  # the target is not on offer either
        upstreams.json(
            "POST",
            _preview_path(world["sub"]),
            {
                "expected": _expected(world["current"], state="past_due"),
                "refusal_code": "state",
                "refusal_detail": "a past_due subscription cannot change plan",
            },
        )
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.post(
                _urls(org_id)["preview"], json={"plan_version_id": str(world["target"])}
            )
        assert response.json()["refusal_code"] == "state"
        assert upstreams.calls("GET", _offerable_path(org_id)) == 0

    async def test_withdrawing_back_to_a_superseded_current_pin_is_not_refused(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The pin the subscription is on is often a version the catalogue has since superseded;
        moving back to it (withdrawing the waiting change) is never a new price."""
        _stub_subscription(
            upstreams,
            org_id,
            subscription_id=world["sub"],
            current=world["current"],
            pending=world["target"],
        )
        _stub_offerable(upstreams, org_id, world["target"])  # the current pin is superseded
        answer = {
            "expected": _expected(world["current"], pending=world["target"]),
            "action": "withdraw_scheduled",
            "effective": "now",
            "would_clear_pending": True,
        }
        upstreams.json("POST", _preview_path(world["sub"]), answer)
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.post(
                _urls(org_id)["preview"], json={"plan_version_id": str(world["current"])}
            )
        assert response.status_code == 200, response.text
        assert response.json()["refusal_code"] is None
        assert response.json()["action"] == "withdraw_scheduled"

    @pytest.mark.parametrize("status", [None, 500, 502, 503])
    async def test_a_failed_preview_says_nothing_was_changed_not_outcome_unknown(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        status: int | None,
    ) -> None:
        """A preview is a read with no Idempotency-Key: "may or may not have been made" and
        "repeat with the same key" would send an operator chasing a change nobody attempted."""

        def _handler(request: httpx.Request) -> httpx.Response:
            if status is None:
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(status, json={"detail": "boom"})

        upstreams.on("POST", _preview_path(world["sub"]), _handler)
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.post(
                _urls(org_id)["preview"], json={"plan_version_id": str(world["target"])}
            )
        assert response.status_code == 503 and _code(response) == "preview_unavailable"
        detail = response.json()["detail"]
        assert "nothing was changed" in detail
        assert "may or may not" not in detail and "Idempotency-Key" not in detail
        assert await _claims(admin_session, org_id) == []

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_refused_credential_on_a_preview_says_nothing_was_changed(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        status: int,
    ) -> None:
        upstreams.json("POST", _preview_path(world["sub"]), {"detail": "no"}, status)
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.post(
                _urls(org_id)["preview"], json={"plan_version_id": str(world["target"])}
            )
        assert response.status_code == 503 and _code(response) == "upstream_refused"
        assert "nothing was changed" in response.json()["detail"]

    async def test_there_is_no_way_to_ask_for_an_immediate_change(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.post(
                _urls(org_id)["preview"],
                json={"plan_version_id": str(world["target"]), "timing": "now"},
            )
        assert response.status_code == 422
        assert not [r for r in upstreams.requests if "internal" in r.url.path]


# =============================================================================== 4. the confirm


class TestConfirmContract:
    async def test_the_idempotency_key_is_required_and_bounded(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        body = _body(world["target"], world["current"])
        for key in (None, "short", "k" * 256):
            response = await _post_change(client, org_id, body, key=key)
            assert response.status_code == 422, (key, response.text)
        assert not [r for r in upstreams.requests if r.method == "POST"]
        assert await _claims(admin_session, org_id) == []

    @pytest.mark.parametrize(
        "patch",
        [
            {"reason": ""},
            {"reason": "   "},
            {"reason": "x" * 501},
            {"timing": "now"},
            {"timing": "immediate"},
            {"surprise": True},
            {"expected": None},
        ],
        ids=["empty", "blank", "501-chars", "now", "immediate", "extra-field", "no-expected"],
    )
    async def test_a_malformed_body_is_refused_before_any_claim(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        patch: dict[str, Any],
    ) -> None:
        body = {**_body(world["target"], world["current"]), **patch}
        response = await _post_change(client, org_id, body)
        assert response.status_code == 422, response.text
        assert not [r for r in upstreams.requests if r.method == "POST"]
        assert await _claims(admin_session, org_id) == []

    async def test_a_success_is_claimed_called_recorded_and_audited(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        principal = operator_principal(roles=["billing_ops"])
        reason = "Customer agreed to the Growth tier on the 3rd; see the ticket. " * 5
        reason = reason.strip()[:300]
        response = await _post_change(
            client,
            org_id,
            _body(world["target"], world["current"], reason=reason),
            principal=principal,
        )
        assert response.status_code == 200, response.text
        out = response.json()
        # A paying subscription's change waits for the next period, and the answer says what
        # subscription *did*: read back off its response, not assumed from the request.
        assert out["effective"] == "next_period" and out["replayed"] is False
        assert out["plan_version_id"] == str(world["current"])
        assert out["pending_plan_version_id"] == str(world["target"])
        assert out["effective_at"] is not None

        (sent,) = [r for r in upstreams.requests if r.url.path == _change_path(world["sub"])]
        assert json.loads(sent.content) == {
            "organization_id": str(org_id),
            "plan_version_id": str(world["target"]),
            "operator_id": f"operator:{principal.sub}",
            # There is no immediate-proration path to ask subscription for.
            "timing": "next_period",
            "note": reason,
            "expected": _expected(world["current"]),
        }
        assert sent.headers["X-Internal-Token"]

        (claim,) = await _claims(admin_session, org_id)
        assert (claim.key, claim.action, claim.status) == (KEY, "plan_change", "succeeded")
        assert claim.response is not None and claim.response["effective"] == "next_period"
        assert "replayed" not in claim.response

        requested, applied = await _audits(admin_session, org_id)
        assert requested.action == "subscription.plan_change.requested"
        assert applied.action == "subscription.plan_change.applied"
        for row in (requested, applied):
            assert row.reason == reason and row.acting_as == f"operator:{principal.sub}"
            assert row.actor_id == principal.sub
            assert row.entity == "subscription" and row.entity_id == world["sub"]
            assert row.after["idempotency_key"] == KEY
        assert requested.after["plan_version_id"] == str(world["target"])
        assert requested.after["expected"] == _expected(world["current"])
        assert applied.after["effective"] == "next_period"

    async def test_a_trial_is_switched_now(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        _stub_subscription(
            upstreams, org_id, subscription_id=world["sub"], current=world["current"], state="trial"
        )
        upstreams.json(
            "POST",
            _change_path(world["sub"]),
            _changed(world["sub"], current=world["target"], state="trial"),
        )
        body = {
            "plan_version_id": str(world["target"]),
            "reason": "Moving the trial to Growth",
            "expected": _expected(world["current"], state="trial"),
        }
        response = await _post_change(client, org_id, body)
        assert response.status_code == 200, response.text
        assert response.json()["effective"] == "now"
        assert response.json()["effective_at"] is None

    async def test_there_is_one_organizations_subscription_and_it_is_resolved_server_side(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The body names no subscription id; the path names (organization, product). The call
        goes to the subscription that organization's own listing returned for that product."""
        other_product = uuid.uuid4()
        _stub_subscriptions(
            upstreams,
            org_id,
            [
                _subscription(
                    subscription_id=other_product, product_key="crm", plan_version_id=uuid.uuid4()
                ),
                _subscription(subscription_id=world["sub"], plan_version_id=world["current"]),
            ],
        )
        response = await _post_change(client, org_id, _body(world["target"], world["current"]))
        assert response.status_code == 200
        posted = [r.url.path for r in upstreams.requests if r.method == "POST"]
        assert posted == [_change_path(world["sub"])]


class TestIdempotency:
    async def test_a_retry_replays_the_result_and_calls_downstream_once(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        body = _body(world["target"], world["current"])
        first = await _post_change(client, org_id, body)
        # A retry may phrase the reason differently — the reason is not what the key names.
        second = await _post_change(
            client, org_id, {**body, "reason": "Agreed on the renewal call (retry)"}
        )
        assert (first.status_code, second.status_code) == (200, 200)
        assert second.json() == {**first.json(), "replayed": True}
        assert upstreams.calls("POST", _change_path(world["sub"])) == 1
        assert len(await _claims(admin_session, org_id)) == 1
        assert len(await _audits(admin_session, org_id)) == 2, "a replay writes no new audit rows"

    async def test_the_same_key_with_a_different_body_is_a_409_and_calls_nothing(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        await _post_change(client, org_id, _body(world["target"], world["current"]))
        different = await _post_change(client, org_id, _body(uuid.uuid4(), world["current"]))
        assert different.status_code == 409 and _code(different) == "idempotency_key_reused"
        stale_view = await _post_change(
            client,
            org_id,
            {**_body(world["target"], world["current"]), "expected": _expected(uuid.uuid4())},
        )
        assert stale_view.status_code == 409 and _code(stale_view) == "idempotency_key_reused"
        assert upstreams.calls("POST", _change_path(world["sub"])) == 1

    async def test_a_key_is_scoped_by_organization(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The same header on another organization is its own request — never a cross-tenant
        replay of the first organization's answer."""
        sub_b, current_b = uuid.uuid4(), uuid.uuid4()
        _stub_directory(upstreams, org_b)
        _stub_subscription(upstreams, org_b, subscription_id=sub_b, current=current_b)
        _stub_offerable(upstreams, org_b, current_b, world["target"])
        upstreams.json(
            "POST",
            _change_path(sub_b),
            _changed(sub_b, current=current_b, pending=world["target"]),
        )
        await _post_change(client, org_id, _body(world["target"], world["current"]))
        second = await _post_change(client, org_b, _body(world["target"], current_b))
        assert second.status_code == 200 and second.json()["replayed"] is False
        assert upstreams.calls("POST", _change_path(sub_b)) == 1
        assert (
            len(await _claims(admin_session, org_id))
            == len(await _claims(admin_session, org_b))
            == 1
        )

    async def test_a_key_in_flight_is_a_409_and_makes_no_second_call(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        first = await _post_change(client, org_id, _body(world["target"], world["current"]))
        assert first.status_code == 200
        await admin_session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.organization_id == org_id)
            .values(status="in_progress", response=None, updated_at=datetime.now(UTC))
        )
        await admin_session.commit()
        retry = await _post_change(client, org_id, _body(world["target"], world["current"]))
        assert retry.status_code == 409 and _code(retry) == "request_in_progress"
        assert upstreams.calls("POST", _change_path(world["sub"])) == 1


class TestConcurrentRequests:
    @pytest_asyncio.fixture
    async def per_request_sessions(self) -> AsyncIterator[None]:
        """Concurrent requests each need their own session (the suite's shared one would
        interleave their transactions) — the app role, as a real request gets."""
        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def _fresh() -> AsyncIterator[AsyncSession]:
            async with maker() as session:
                yield session

        previous = app.dependency_overrides.get(get_session)
        app.dependency_overrides[get_session] = _fresh
        yield
        if previous is not None:
            app.dependency_overrides[get_session] = previous
        await engine.dispose()

    async def test_two_concurrent_requests_with_one_key_make_one_change(
        self,
        client: AsyncClient,
        per_request_sessions: None,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        async def _slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.4)  # long enough for the second request to meet the claim
            return httpx.Response(
                200,
                json=_changed(world["sub"], current=world["current"], pending=world["target"]),
            )

        upstreams.on("POST", _change_path(world["sub"]), _slow)  # type: ignore[arg-type]
        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(upstreams))

        async def _override() -> AsyncIterator[httpx.AsyncClient]:
            yield mock_client

        app.dependency_overrides[get_delegation_client] = _override
        body = _body(world["target"], world["current"])
        results = await asyncio.gather(
            _post_change(client, org_id, body), _post_change(client, org_id, body)
        )
        statuses = sorted(r.status_code for r in results)
        assert statuses == [200, 409], [r.text for r in results]
        loser = next(r for r in results if r.status_code == 409)
        assert _code(loser) == "request_in_progress"
        assert upstreams.calls("POST", _change_path(world["sub"])) == 1
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "succeeded"


class TestDefinitiveRefusals:
    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (422, "state"),
            (422, "cadence"),
            (422, "currency"),
            (422, "already_pinned"),
            (422, "product_mismatch"),
            (422, "not_published"),
            (422, "reserved_reason"),
            (409, "state_changed"),
            (409, "billing_catching_up"),
            (404, "not_found"),
        ],
    )
    async def test_subscriptions_coded_refusal_is_surfaced_recorded_and_replayed(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        status: int,
        code: str,
    ) -> None:
        detail = f"refused because {code}"
        upstreams.json(
            "POST",
            _change_path(world["sub"]),
            {"detail": detail, "errors": [{"code": code, "message": detail}]},
            status,
        )
        body = _body(world["target"], world["current"])
        first = await _post_change(client, org_id, body)
        assert first.status_code == status, first.text
        assert _code(first) == code and first.json()["detail"] == detail
        assert first.json()["request_id"], "the operator is given something to quote"

        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "refused"
        _requested, refused = await _audits(admin_session, org_id)
        assert refused.action == "subscription.plan_change.refused"
        assert refused.after["status"] == status and refused.after["code"] == code

        # A retry replays the same refusal — no second call. A new key is a new request.
        again = await _post_change(client, org_id, body)
        assert (again.status_code, _code(again), again.json()["detail"]) == (status, code, detail)
        assert upstreams.calls("POST", _change_path(world["sub"])) == 1

    async def test_a_new_key_is_a_new_request_after_a_refusal(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        upstreams.json(
            "POST",
            _change_path(world["sub"]),
            {"detail": "x", "errors": [{"code": "billing_catching_up", "message": "x"}]},
            409,
        )
        body = _body(world["target"], world["current"])
        assert (
            await _post_change(client, org_id, body, key="first-attempt-key")
        ).status_code == 409
        upstreams.json(
            "POST",
            _change_path(world["sub"]),
            _changed(world["sub"], current=world["current"], pending=world["target"]),
        )
        assert (
            await _post_change(client, org_id, body, key="second-attempt-key")
        ).status_code == 200


class TestOnlyWhatIsOnOffer:
    """The confirm re-reads what catalog offers this organization now. A UI gate hides a control;
    it does not protect data — a picker loaded before a newer version was published or the plan
    retired, or a direct API call with any published id, must not pin a withdrawn price."""

    async def test_a_target_not_on_offer_is_refused_recorded_and_replayed_without_a_call(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        # Since the picker loaded, a newer version superseded the target (or its plan retired).
        _stub_offerable(upstreams, org_id, world["current"], uuid.uuid4())
        body = _body(world["target"], world["current"])
        first = await _post_change(client, org_id, body)
        assert first.status_code == 422, first.text
        assert _code(first) == "not_offerable" and first.json()["detail"]
        assert upstreams.calls("POST", _change_path(world["sub"])) == 0

        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "refused"
        requested, refused = await _audits(admin_session, org_id)
        assert requested.action == "subscription.plan_change.requested"
        assert refused.action == "subscription.plan_change.refused"
        assert refused.after["status"] == 422 and refused.after["code"] == "not_offerable"

        again = await _post_change(client, org_id, body)
        assert (again.status_code, _code(again)) == (422, "not_offerable")
        assert upstreams.calls("POST", _change_path(world["sub"])) == 0
        assert len(await _audits(admin_session, org_id)) == 2, "a replay writes no new audit rows"

    async def test_withdrawing_back_to_a_superseded_current_pin_still_reaches_subscription(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        _stub_subscription(
            upstreams,
            org_id,
            subscription_id=world["sub"],
            current=world["current"],
            pending=world["target"],
        )
        _stub_offerable(upstreams, org_id, world["target"])  # the current pin is superseded
        upstreams.json(
            "POST", _change_path(world["sub"]), _changed(world["sub"], current=world["current"])
        )
        body = {
            "plan_version_id": str(world["current"]),
            "reason": "Customer changed their mind",
            "expected": _expected(world["current"], pending=world["target"]),
        }
        response = await _post_change(client, org_id, body)
        assert response.status_code == 200, response.text
        assert response.json()["pending_plan_version_id"] is None
        assert upstreams.calls("POST", _change_path(world["sub"])) == 1

    async def test_a_recorded_success_still_replays_after_the_catalogue_moves_on(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        body = _body(world["target"], world["current"])
        assert (await _post_change(client, org_id, body)).status_code == 200
        _stub_offerable(upstreams, org_id, world["current"], uuid.uuid4())
        again = await _post_change(client, org_id, body)
        assert again.status_code == 200 and again.json()["replayed"] is True
        assert upstreams.calls("POST", _change_path(world["sub"])) == 1

    async def test_a_catalogue_outage_claims_nothing_and_calls_nothing(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        upstreams.json("GET", _offerable_path(org_id), {"detail": "down"}, 503)
        response = await _post_change(client, org_id, _body(world["target"], world["current"]))
        assert response.status_code == 503 and _code(response) == "catalog_unavailable"
        assert "changed nothing" in response.json()["detail"]
        assert upstreams.calls("POST", _change_path(world["sub"])) == 0
        assert await _claims(admin_session, org_id) == []
        assert await _audits(admin_session, org_id, "%") == []

    async def test_a_retry_after_an_unknown_outcome_is_refused_if_the_target_left_the_offer(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The first attempt's answer was lost and it did not land; meanwhile the catalogue moved
        on. The retry reconciles, finds nothing landed, and refuses rather than pinning it."""
        upstreams.json("POST", _change_path(world["sub"]), {"detail": "boom"}, 502)
        body = _body(world["target"], world["current"])
        assert (await _post_change(client, org_id, body)).status_code == 503
        _stub_offerable(upstreams, org_id, world["current"], uuid.uuid4())
        retry = await _post_change(client, org_id, body)
        assert (retry.status_code, _code(retry)) == (422, "not_offerable")
        assert upstreams.calls("POST", _change_path(world["sub"])) == 1
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "refused"


class TestOutcomeUnknown:
    def _fail(self, upstreams: FakeUpstreams, world: dict[str, uuid.UUID], status: int | None):
        def _handler(request: httpx.Request) -> httpx.Response:
            if status is None:
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(status, json={"detail": "boom"})

        upstreams.on("POST", _change_path(world["sub"]), _handler)

    @pytest.mark.parametrize("status", [None, 500, 502, 503])
    async def test_a_lost_answer_is_a_503_that_says_the_outcome_is_unknown(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        status: int | None,
    ) -> None:
        self._fail(upstreams, world, status)
        response = await _post_change(client, org_id, _body(world["target"], world["current"]))
        assert response.status_code == 503 and _code(response) == "outcome_unknown"
        assert "may or may not" in response.json()["detail"]
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "unknown" and claim.response is None
        _requested, unknown = await _audits(admin_session, org_id)
        assert unknown.action == "subscription.plan_change.outcome_unknown"

    async def test_a_retry_that_finds_the_change_landed_records_it_without_a_second_call(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        self._fail(upstreams, world, None)
        body = _body(world["target"], world["current"])
        assert (await _post_change(client, org_id, body)).status_code == 503

        # The change did land: the live subscription now has it scheduled.
        _stub_subscription(
            upstreams,
            org_id,
            subscription_id=world["sub"],
            current=world["current"],
            pending=world["target"],
        )
        retry = await _post_change(client, org_id, body)
        assert retry.status_code == 200, retry.text
        assert retry.json()["effective"] == "next_period" and retry.json()["replayed"] is True
        assert upstreams.calls("POST", _change_path(world["sub"])) == 1, "reconciled, not repeated"
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "succeeded"
        applied = (await _audits(admin_session, org_id))[-1]
        assert applied.action == "subscription.plan_change.applied"
        assert applied.after["reconciled"] is True
        # And now it is an ordinary recorded success.
        again = await _post_change(client, org_id, body)
        assert again.json()["replayed"] is True
        assert upstreams.calls("POST", _change_path(world["sub"])) == 1

    async def test_a_retry_that_finds_it_did_not_land_makes_the_call(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        self._fail(upstreams, world, 502)
        body = _body(world["target"], world["current"])
        assert (await _post_change(client, org_id, body)).status_code == 503
        upstreams.json(
            "POST",
            _change_path(world["sub"]),
            _changed(world["sub"], current=world["current"], pending=world["target"]),
        )
        retry = await _post_change(client, org_id, body)
        assert retry.status_code == 200, retry.text
        assert retry.json()["replayed"] is False
        assert upstreams.calls("POST", _change_path(world["sub"])) == 2
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "succeeded"

    @pytest.mark.parametrize("landed", [True, False], ids=["late-first-attempt", "someone-else"])
    async def test_a_reconciling_retry_that_meets_state_changed_looks_again_before_refusing(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        landed: bool,
    ) -> None:
        """The first attempt timed out at console but was only slow (waiting on the row lock).
        The retry's live read still shows the old state, so it calls again — and loses the race:
        the first attempt commits, and this call is told `state_changed`. That is the change
        landing, not a refusal; recording it as refused would replay "it changed since you
        looked" for the key forever. When something *else* moved it, it is still a refusal."""
        self._fail(upstreams, world, None)
        body = _body(world["target"], world["current"])
        assert (await _post_change(client, org_id, body)).status_code == 503

        moved = {"yes": False}
        other = uuid.uuid4()

        def _subscriptions(_request: httpx.Request) -> httpx.Response:
            pending = (world["target"] if landed else other) if moved["yes"] else None
            return httpx.Response(
                200,
                json=[
                    _subscription(
                        subscription_id=world["sub"],
                        plan_version_id=world["current"],
                        pending_plan_version_id=pending,
                    )
                ],
            )

        def _lost_the_race(_request: httpx.Request) -> httpx.Response:
            moved["yes"] = True
            detail = "this subscription changed since you looked at it"
            return httpx.Response(
                409,
                json={"detail": detail, "errors": [{"code": "state_changed", "message": detail}]},
            )

        upstreams.on("GET", f"/internal/v1/organizations/{org_id}/subscriptions", _subscriptions)
        upstreams.on("POST", _change_path(world["sub"]), _lost_the_race)
        retry = await _post_change(client, org_id, body)
        (claim,) = await _claims(admin_session, org_id)
        outcome = (await _audits(admin_session, org_id))[-1]
        if landed:
            assert retry.status_code == 200, retry.text
            assert retry.json()["effective"] == "next_period" and retry.json()["replayed"] is True
            assert claim.status == "succeeded"
            assert outcome.action == "subscription.plan_change.applied"
            assert outcome.after["reconciled"] is True
        else:
            assert (retry.status_code, _code(retry)) == (409, "state_changed")
            assert claim.status == "refused"
            assert outcome.action == "subscription.plan_change.refused"

    async def test_a_crashed_request_is_reconciled_after_its_lease(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The process died between the claim and the call (or the record): the row sits
        `in_progress`. Inside the lease that is a request still running; past it, a retry
        reconciles from a live read."""
        body = _body(world["target"], world["current"])
        first = await _post_change(client, org_id, body)
        assert first.status_code == 200
        await admin_session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.organization_id == org_id)
            .values(
                status="in_progress",
                response=None,
                updated_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        await admin_session.commit()
        _stub_subscription(
            upstreams,
            org_id,
            subscription_id=world["sub"],
            current=world["current"],
            pending=world["target"],
        )
        retry = await _post_change(client, org_id, body)
        assert retry.status_code == 200 and retry.json()["replayed"] is True
        assert upstreams.calls("POST", _change_path(world["sub"])) == 1

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_refused_service_credential_is_never_a_401_to_the_operator(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        status: int,
    ) -> None:
        self._fail(upstreams, world, status)
        response = await _post_change(client, org_id, _body(world["target"], world["current"]))
        assert response.status_code == 503 and _code(response) == "upstream_refused"
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "unknown", "un-final: a retry reconciles and tries again"


# ====================================================================== 6. tenant isolation


class TestClaimTableIsolation:
    async def test_the_claim_table_is_rls_real(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        unbound_app_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        await _post_change(client, org_id, _body(world["target"], world["current"]))
        assert len(await _claims(admin_session, org_id)) == 1

        # The app role with no tenant bound sees none of it; bound to another organization, none
        # of it either; bound to the owner, exactly its own.
        async with unbound_app_session.begin():
            assert (await unbound_app_session.execute(select(IdempotencyKey))).scalars().all() == []
        for org, expected in ((org_b, 0), (org_id, 1)):
            async with unbound_app_session.begin():
                await unbound_app_session.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :org, true)"), {"org": str(org)}
                )
                rows = (await unbound_app_session.execute(select(IdempotencyKey))).scalars().all()
                assert len(rows) == expected

    async def test_a_claim_cannot_be_written_for_another_organization(
        self, unbound_app_session: AsyncSession, org_id: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """WITH CHECK: bound to org A, an insert naming org B is refused by the database."""
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError):
            async with unbound_app_session.begin():
                await unbound_app_session.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :org, true)"), {"org": str(org_id)}
                )
                unbound_app_session.add(
                    IdempotencyKey(
                        organization_id=org_b,
                        key="k" * 16,
                        action="plan_change",
                        fingerprint="f" * 64,
                        status="in_progress",
                    )
                )
