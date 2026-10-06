"""Staff Add product and Extend trial on console: `add-product-options`, `POST .../subscriptions`,
`POST .../subscriptions/{product_key}/extend-trial` (ADR-0031).

Properties covered, each with its negative case:

1. **Authorization on every route** — a product token is a 404, a staff session without its second
   factor a 403, `support` a 403, no role a 403, no token a 401; Extend trial is **owner only**, so
   `billing_ops` is a 403 there. A refused call reaches no downstream service and writes nothing.
2. **The picker** offers only products the organization has no row for in any state, marks the
   versions it could not take, and names a bundle's members it already pays for.
3. **A paid add must be acknowledged** — refused before any claim otherwise; a trial is the default.
4. **Claim, call, record** — the plan switch's required `Idempotency-Key` contract: replay,
   different body 409, in flight 409, concurrent requests make one call, definitive refusals
   recorded and replayed, unknown outcomes reconciled from a live read.
5. **Extend trial is absolute** — the body's instants reach subscription unchanged, the same instant
   written in IST and in UTC is the same request, and a lost answer is reconciled by reading where
   the trial now ends.

Console's tests mock the downstream, so subscription-side isolation (another organization's
subscription id; the currency check scoped to the named organization) is proven in subscription's
own `tests/test_operator_add_product_extend_trial.py`.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import get_delegation_client
from app.config import settings
from app.main import app
from app.models import SCHEMA, AuditLog, IdempotencyKey
from platform_core.db import get_session
from tests.helpers import as_principal, operator_principal
from tests.test_pricing import (
    FakeUpstreams,
    _stub_directory,
    _stub_plan_version,
    _stub_public_plans,
    _stub_subscriptions,
    _subscription,
    _version_doc,
)

pytestmark = pytest.mark.integration

KEY = "staff-action-key-0001"
IST = timezone(timedelta(hours=5, minutes=30))
SUBSCRIBE_PATH = "/internal/v1/subscriptions"


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


def _urls(org: uuid.UUID, product_key: str = "hrms") -> dict[str, str]:
    base = f"/api/v1/admin/organizations/{org}"
    return {
        "options": f"{base}/add-product-options",
        "add": f"{base}/subscriptions",
        "extend": f"{base}/subscriptions/{product_key}/extend-trial",
    }


def _code(response: httpx.Response) -> str | None:
    errors = response.json().get("errors") or []
    return errors[0].get("code") if errors else None


def _extend_path(subscription_id: uuid.UUID) -> str:
    return f"/internal/v1/subscriptions/{subscription_id}/extend-trial"


def _opened(
    subscription_id: uuid.UUID,
    *,
    product_key: str,
    plan_version_id: uuid.UUID,
    state: str = "trial",
) -> dict[str, Any]:
    """subscription's `SubscriptionOut` for a subscription it just opened."""
    return {
        **_subscription(
            subscription_id=subscription_id,
            product_key=product_key,
            plan_version_id=plan_version_id,
            state=state,
        ),
        "trial_ends_at": "2026-10-20T09:00:00+00:00" if state == "trial" else None,
    }


def _trialling(
    subscription_id: uuid.UUID,
    plan_version_id: uuid.UUID,
    *,
    trial_ends_at: str,
    state: str = "trial",
) -> dict[str, Any]:
    return {
        **_subscription(
            subscription_id=subscription_id, plan_version_id=plan_version_id, state=state
        ),
        "trial_ends_at": trial_ends_at,
        "trial_max_ends_at": "2026-12-30T00:00:00+00:00" if state == "trial" else None,
    }


async def _claims(admin: AsyncSession, org: uuid.UUID) -> list[IdempotencyKey]:
    admin.expunge_all()
    rows = await admin.execute(
        select(IdempotencyKey)
        .where(IdempotencyKey.organization_id == org)
        .order_by(IdempotencyKey.created_at)
    )
    return list(rows.scalars().all())


async def _audits(admin: AsyncSession, org: uuid.UUID, like: str = "subscription.%") -> list:
    admin.expunge_all()
    rows = await admin.execute(
        select(AuditLog)
        .where(AuditLog.organization_id == org, AuditLog.action.like(like))
        .order_by(AuditLog.at, AuditLog.id)
    )
    return list(rows.scalars().all())


def _posts(upstreams: FakeUpstreams, path: str) -> list[dict[str, Any]]:
    return [
        json.loads(r.content)
        for r in upstreams.requests
        if r.method == "POST" and r.url.path == path
    ]


# ------------------------------------------------------------------------------- the world

TRIAL_END = "2026-10-20T09:00:00.123456+00:00"
NEW_END = datetime(2026, 11, 3, 23, 59, 59, tzinfo=IST)


@pytest.fixture
def world(upstreams: FakeUpstreams, org_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Org with a trialling `hrms` subscription (to extend) and an offerable `crm` version (to
    add), every downstream call stubbed for the happy path."""
    ids = {
        "sub": uuid.uuid4(),
        "pin": uuid.uuid4(),
        "crm_version": uuid.uuid4(),
        "new_sub": uuid.uuid4(),
    }
    _stub_directory(upstreams, org_id)
    _stub_subscriptions(
        upstreams, org_id, [_trialling(ids["sub"], ids["pin"], trial_ends_at=TRIAL_END)]
    )
    upstreams.json(
        "POST",
        SUBSCRIBE_PATH,
        _opened(ids["new_sub"], product_key="crm", plan_version_id=ids["crm_version"]),
        201,
    )
    upstreams.json(
        "POST",
        _extend_path(ids["sub"]),
        _trialling(ids["sub"], ids["pin"], trial_ends_at=NEW_END.astimezone(UTC).isoformat()),
    )
    _stub_public_plans(upstreams, [])
    _stub_offer(upstreams, org_id, {"crm": [ids["crm_version"]]})
    return ids


def _stub_offer(
    upstreams: FakeUpstreams, org: uuid.UUID, offer: dict[str, list[uuid.UUID]]
) -> None:
    """catalog's offerable-versions read for `org`, by product — what the confirm re-reads."""

    def _handler(request: httpx.Request) -> httpx.Response:
        ids = offer.get(request.url.params["product_key"], [])
        return httpx.Response(200, json={"versions": [{"plan_version_id": str(v)} for v in ids]})

    upstreams.on("GET", f"/internal/v1/organizations/{org}/offerable-versions", _handler)


def _add_body(version: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {
        "product_key": "crm",
        "plan_version_id": str(version),
        "reason": "Onboarding the sales team on a trial",
        **extra,
    }


def _extend_body(**extra: Any) -> dict[str, Any]:
    return {
        "new_trial_ends_at": NEW_END.isoformat(),
        "expected_trial_ends_at": TRIAL_END,
        "reason": "Their data migration slipped a fortnight",
        **extra,
    }


async def _post(
    client: AsyncClient,
    url: str,
    body: dict[str, Any],
    *,
    key: str | None = KEY,
    principal: Any = None,
) -> httpx.Response:
    headers = {} if key is None else {"Idempotency-Key": key}
    with as_principal(principal or operator_principal(roles=["owner"])):
        return await client.post(url, json=body, headers=headers)


# ======================================================================== 1. authorization


class TestAuthorization:
    def _calls(self, client: AsyncClient, org: uuid.UUID, world: dict[str, uuid.UUID]):
        urls = _urls(org)
        # One key per action: a key names one request in an organization, whatever the action —
        # reusing it for another action is a 409 `idempotency_key_reused`.
        return {
            "options": lambda: client.get(urls["options"]),
            "add": lambda: client.post(
                urls["add"],
                json=_add_body(world["crm_version"]),
                headers={"Idempotency-Key": "add-product-key-01"},
            ),
            "extend": lambda: client.post(
                urls["extend"],
                json=_extend_body(),
                headers={"Idempotency-Key": "extend-trial-key-01"},
            ),
        }

    @pytest.mark.parametrize("route", ["options", "add", "extend"])
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

    @pytest.mark.parametrize("route", ["extend"])
    async def test_billing_ops_is_refused_on_every_owner_only_route(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        route: str,
    ) -> None:
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await self._calls(client, org_id, world)[route]()
        assert response.status_code == 403, response.text
        assert "owner" in response.json()["detail"]
        assert upstreams.requests == []
        assert await _claims(admin_session, org_id) == []
        assert await _audits(admin_session, org_id, "%") == []

    @pytest.mark.parametrize("route", ["options", "add", "extend"])
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

    async def test_owner_is_admitted_everywhere_and_billing_ops_to_add_product(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        with as_principal(operator_principal(roles=["owner"])):
            calls = self._calls(client, org_id, world)
            assert (await calls["options"]()).status_code == 200
            assert (await calls["add"]()).status_code == 201
            assert (await calls["extend"]()).status_code == 200
        with as_principal(operator_principal(roles=["billing_ops"])):
            calls = self._calls(client, org_id, world)
            assert (await calls["options"]()).status_code == 200
            add = await client.post(
                _urls(org_id)["add"],
                json=_add_body(world["crm_version"]),
                headers={"Idempotency-Key": "billing-ops-key-01"},
            )
            assert add.status_code == 201, add.text

    @pytest.mark.parametrize("route", ["options", "add", "extend"])
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
        with as_principal(operator_principal(roles=["owner"])):
            response = await self._calls(client, org_id, world)[route]()
        assert response.status_code == 404, response.text
        assert [r.url.path for r in upstreams.requests if "internal" in r.url.path] == []
        assert await _claims(admin_session, org_id) == []

    async def test_a_product_key_that_is_not_one_is_refused_before_any_call(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        base = f"/api/v1/admin/organizations/{org_id}"
        with as_principal(operator_principal()):
            odd_path = await client.post(
                f"{base}/subscriptions/Bad..Key/extend-trial",
                json=_extend_body(),
                headers={"Idempotency-Key": KEY},
            )
            odd_body = await client.post(
                f"{base}/subscriptions",
                json={**_add_body(uuid.uuid4()), "product_key": "../admin"},
                headers={"Idempotency-Key": KEY},
            )
        assert odd_path.status_code in (404, 422) and odd_body.status_code == 422
        assert upstreams.requests == []


# ============================================================================== 2. the picker


def _offered(product_key: str, version_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    return {
        "plan_id": str(uuid.uuid4()),
        "plan_code": "standard",
        "plan_name": overrides.pop("plan_name", "Standard"),
        "product_key": product_key,
        "plan_version_id": str(version_id),
        "version_no": 1,
        "state": "published",
        "billing_interval": overrides.pop("billing_interval", "month"),
        "currency": overrides.pop("currency", "INR"),
        "restricted_to_org_id": overrides.pop("restricted_to_org_id", None),
        "published_at": None,
        "prices": overrides.pop(
            "prices",
            [{"id": str(uuid.uuid4()), "kind": "flat", "amount_minor": 99900, "currency": "INR"}],
        ),
        "features": overrides.pop("features", []),
    }


def _public(product_key: str, name: str) -> dict[str, Any]:
    return {
        "product_key": product_key,
        "product_name": name,
        "plan_id": str(uuid.uuid4()),
        "plan_version_id": str(uuid.uuid4()),
        "amount_minor": 99900,
        "currency": "INR",
        "billing_interval": "month",
    }


class TestAddProductOptions:
    async def test_it_offers_only_products_with_no_row_and_says_what_cannot_be_taken(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        _stub_directory(upstreams, org_id)
        _stub_subscriptions(
            upstreams,
            org_id,
            [
                _subscription(subscription_id=uuid.uuid4(), plan_version_id=uuid.uuid4()),
                _subscription(
                    subscription_id=uuid.uuid4(),
                    product_key="crm",
                    plan_version_id=uuid.uuid4(),
                    state="cancelled",
                    currency="USD",
                ),
            ],
        )
        _stub_public_plans(
            upstreams,
            [
                _public("hrms", "People"),
                _public("crm", "Sales"),
                _public("finance", "Books"),
                _public("suite", "Suite"),
                _public("projects", "Projects"),
            ],
        )
        rupees, dollars, once, bundle = (uuid.uuid4() for _ in range(4))

        def _by_product(request: httpx.Request) -> httpx.Response:
            key = request.url.params["product_key"]
            versions = {
                "finance": [
                    _offered(
                        "finance", rupees, features=[{"feature_key": "trial_days", "value": 0}]
                    ),
                    _offered("finance", dollars, currency="USD", plan_name="Global"),
                    _offered("finance", once, billing_interval="one_time", plan_name="Setup"),
                ],
                "suite": [
                    _offered(
                        "suite",
                        bundle,
                        plan_name="Duo",
                        features=[
                            {"feature_key": "bundled_products", "value": ["hrms", "finance"]}
                        ],
                    )
                ],
                "projects": [],
            }[key]
            return httpx.Response(200, json={"versions": versions})

        upstreams.on("GET", f"/internal/v1/organizations/{org_id}/offerable-versions", _by_product)
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get(_urls(org_id)["options"])
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["currency"] == "INR", "a cancelled USD subscription does not set the currency"
        assert body["existing"] == [
            {"product_key": "crm", "state": "cancelled"},
            {"product_key": "hrms", "state": "active"},
        ]
        # hrms (active) and crm (cancelled) are never offered; projects has nothing on offer.
        assert [p["product_key"] for p in body["products"]] == ["finance", "suite"]
        finance = {v["plan_version_id"]: v for v in body["products"][0]["versions"]}
        assert finance[str(rupees)]["blocked_code"] is None
        assert finance[str(rupees)]["offers_trial"] is False
        assert finance[str(dollars)]["blocked_code"] == "org_currency_mismatch"
        assert finance[str(once)]["blocked_code"] == "one_time"
        (duo,) = body["products"][1]["versions"]
        assert duo["bundled_products"] == ["hrms", "finance"]
        assert duo["overlapping_products"] == ["hrms"], "hrms is already paid for separately"
        assert body["products"][0]["product_name"] == "Books"

        asked = sorted(
            r.url.params["product_key"]
            for r in upstreams.requests
            if r.url.path.endswith("offerable-versions")
        )
        assert asked == ["finance", "projects", "suite"], "catalog is asked only for candidates"

    async def test_a_product_a_live_bundle_already_entitles_names_the_bundle(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        """The other direction of the overlap: the organization holds a live `suite` covering
        hrms and crm, and has no row of its own for either. Each is still offered — subscription
        accepts it — but names the bundle that already entitles it, because adding it bills both
        with no credit. A cancelled bundle entitles nothing and is not named."""
        _stub_directory(upstreams, org_id)
        suite = {
            **_subscription(
                subscription_id=uuid.uuid4(), product_key="suite", plan_version_id=uuid.uuid4()
            ),
            "entitled_products": ["hrms", "crm"],
        }
        old_bundle = {
            **_subscription(
                subscription_id=uuid.uuid4(),
                product_key="suite_legacy",
                plan_version_id=uuid.uuid4(),
                state="cancelled",
            ),
            "entitled_products": ["finance"],
        }
        _stub_subscriptions(upstreams, org_id, [suite, old_bundle])
        _stub_public_plans(
            upstreams,
            [_public("hrms", "People"), _public("crm", "Sales"), _public("finance", "Books")],
        )

        def _by_product(request: httpx.Request) -> httpx.Response:
            key = request.url.params["product_key"]
            return httpx.Response(200, json={"versions": [_offered(key, uuid.uuid4())]})

        upstreams.on("GET", f"/internal/v1/organizations/{org_id}/offerable-versions", _by_product)
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get(_urls(org_id)["options"])
        assert response.status_code == 200, response.text
        products = {p["product_key"]: p for p in response.json()["products"]}

        assert sorted(products) == ["crm", "finance", "hrms"], "offered, not hidden"
        assert products["hrms"]["entitled_through"] == ["suite"]
        assert products["crm"]["entitled_through"] == ["suite"]
        assert products["finance"]["entitled_through"] == [], "a cancelled bundle entitles nothing"
        (hrms,) = products["hrms"]["versions"]
        assert hrms["blocked_code"] is None, "a warning, not a block — subscription accepts it"

    async def test_an_organization_with_nothing_live_may_take_any_currency(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        _stub_directory(upstreams, org_id)
        _stub_subscriptions(upstreams, org_id, [])
        _stub_public_plans(upstreams, [_public("finance", "Books")])
        upstreams.json(
            "GET",
            f"/internal/v1/organizations/{org_id}/offerable-versions",
            {"versions": [_offered("finance", uuid.uuid4(), currency="USD")]},
        )
        with as_principal(operator_principal(roles=["owner"])):
            body = (await client.get(_urls(org_id)["options"])).json()
        assert body["currency"] is None
        assert body["products"][0]["versions"][0]["blocked_code"] is None

    @pytest.mark.parametrize("down", ["subscription", "catalog"])
    async def test_an_upstream_being_down_is_a_503(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID, down: str
    ) -> None:
        _stub_directory(upstreams, org_id)
        if down == "subscription":
            upstreams.json(
                "GET", f"/internal/v1/organizations/{org_id}/subscriptions", {"detail": "x"}, 503
            )
        else:
            _stub_subscriptions(upstreams, org_id, [])
            upstreams.json("GET", "/internal/v1/public-flat-plans", {"detail": "x"}, 503)
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(_urls(org_id)["options"])
        assert response.status_code == 503


# ============================================================================ 3. add product


class TestAddProduct:
    async def test_a_paid_add_without_the_acknowledgement_is_refused_before_anything(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        for ack in (None, False):
            body = _add_body(world["crm_version"], trial=False)
            if ack is not None:
                body["acknowledge_immediate_charge"] = ack
            response = await _post(client, _urls(org_id)["add"], body)
            assert response.status_code == 422, response.text
            assert _code(response) == "charge_not_acknowledged"
        assert _posts(upstreams, SUBSCRIBE_PATH) == []
        assert await _claims(admin_session, org_id) == []
        assert await _audits(admin_session, org_id, "%") == []

    async def test_a_trial_is_the_default_and_is_claimed_called_recorded_audited(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        principal = operator_principal(roles=["billing_ops"])
        response = await _post(
            client, _urls(org_id)["add"], _add_body(world["crm_version"]), principal=principal
        )
        assert response.status_code == 201, response.text
        out = response.json()
        assert out["state"] == "trial" and out["charge_claimed"] is False
        assert out["subscription_id"] == str(world["new_sub"]) and out["replayed"] is False

        (sent,) = _posts(upstreams, SUBSCRIBE_PATH)
        assert sent == {
            "organization_id": str(org_id),
            "product_key": "crm",
            "plan_version_id": str(world["crm_version"]),
            "trial": True,
            "operator_id": f"operator:{principal.sub}",
            "note": "Onboarding the sales team on a trial",
        }

        (claim,) = await _claims(admin_session, org_id)
        assert (claim.key, claim.action, claim.status) == (KEY, "add_product", "succeeded")
        requested, applied = await _audits(admin_session, org_id)
        assert requested.action == "subscription.add_product.requested"
        assert applied.action == "subscription.add_product.applied"
        for row in (requested, applied):
            assert row.reason == "Onboarding the sales team on a trial"
            assert row.acting_as == f"operator:{principal.sub}" and row.actor_id == principal.sub
            assert row.after["idempotency_key"] == KEY
        assert requested.entity_id is None and applied.entity_id == world["new_sub"]
        assert requested.after["trial"] is True

    async def test_an_acknowledged_paid_add_starts_paying(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        upstreams.json(
            "POST",
            SUBSCRIBE_PATH,
            _opened(
                world["new_sub"],
                product_key="crm",
                plan_version_id=world["crm_version"],
                state="active",
            ),
            201,
        )
        response = await _post(
            client,
            _urls(org_id)["add"],
            _add_body(world["crm_version"], trial=False, acknowledge_immediate_charge=True),
        )
        assert response.status_code == 201, response.text
        assert response.json()["state"] == "active" and response.json()["charge_claimed"] is True
        (sent,) = _posts(upstreams, SUBSCRIBE_PATH)
        assert sent["trial"] is False
        requested = (await _audits(admin_session, org_id))[0]
        assert requested.after["acknowledged_immediate_charge"] is True

    @pytest.mark.parametrize(
        ("patch", "key"),
        [
            ({"reason": ""}, KEY),
            ({"reason": "   "}, KEY),
            ({"reason": "x" * 501}, KEY),
            ({"surprise": True}, KEY),
            ({"product_key": "Not A Key"}, KEY),
            ({}, None),
            ({}, "short"),
            ({}, "k" * 256),
        ],
        ids=["empty", "blank", "501", "extra", "bad-key", "no-header", "short-header", "long"],
    )
    async def test_a_malformed_request_is_refused_before_any_claim(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        patch: dict[str, Any],
        key: str | None,
    ) -> None:
        body = {**_add_body(world["crm_version"]), **patch}
        response = await _post(client, _urls(org_id)["add"], body, key=key)
        assert response.status_code == 422, response.text
        assert _posts(upstreams, SUBSCRIBE_PATH) == []
        assert await _claims(admin_session, org_id) == []


class TestAddProductIdempotency:
    async def test_a_retry_replays_and_calls_once(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        first = await _post(client, _urls(org_id)["add"], _add_body(world["crm_version"]))
        second = await _post(
            client,
            _urls(org_id)["add"],
            _add_body(world["crm_version"], reason="Same request, reworded"),
        )
        assert (first.status_code, second.status_code) == (201, 201)
        assert second.json() == {**first.json(), "replayed": True}
        assert len(_posts(upstreams, SUBSCRIBE_PATH)) == 1
        assert len(await _audits(admin_session, org_id)) == 2, "a replay writes no audit rows"

    @pytest.mark.parametrize(
        "different",
        [
            {"plan_version_id": "other"},
            {"trial": False, "acknowledge_immediate_charge": True},
            {"product_key": "finance"},
        ],
        ids=["version", "paid", "product"],
    )
    async def test_the_same_key_with_a_different_body_is_a_409(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        different: dict[str, Any],
    ) -> None:
        assert (
            await _post(client, _urls(org_id)["add"], _add_body(world["crm_version"]))
        ).status_code == 201
        body = _add_body(world["crm_version"], **different)
        if body["plan_version_id"] == "other":
            body["plan_version_id"] = str(uuid.uuid4())
        response = await _post(client, _urls(org_id)["add"], body)
        assert response.status_code == 409 and _code(response) == "idempotency_key_reused"
        assert len(_posts(upstreams, SUBSCRIBE_PATH)) == 1

    async def test_a_key_is_scoped_by_organization(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        _stub_directory(upstreams, org_b)
        _stub_offer(upstreams, org_b, {"crm": [world["crm_version"]]})
        await _post(client, _urls(org_id)["add"], _add_body(world["crm_version"]))
        second = await _post(client, _urls(org_b)["add"], _add_body(world["crm_version"]))
        assert second.status_code == 201 and second.json()["replayed"] is False
        assert len(_posts(upstreams, SUBSCRIBE_PATH)) == 2
        assert len(await _claims(admin_session, org_b)) == 1

    async def test_a_key_in_flight_is_a_409_and_makes_no_second_call(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        assert (
            await _post(client, _urls(org_id)["add"], _add_body(world["crm_version"]))
        ).status_code == 201
        await admin_session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.organization_id == org_id)
            .values(status="in_progress", response=None, updated_at=datetime.now(UTC))
        )
        await admin_session.commit()
        retry = await _post(client, _urls(org_id)["add"], _add_body(world["crm_version"]))
        assert retry.status_code == 409 and _code(retry) == "request_in_progress"
        assert len(_posts(upstreams, SUBSCRIBE_PATH)) == 1

    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (409, "already_subscribed"),
            (422, "one_time"),
            (422, "org_currency_mismatch"),
            (422, "trial_not_offered"),
            (422, "not_published"),
            (422, "product_mismatch"),
            (422, "bundle"),
            (422, "reserved_reason"),
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
            SUBSCRIBE_PATH,
            {"detail": detail, "errors": [{"code": code, "message": detail}]},
            status,
        )
        first = await _post(client, _urls(org_id)["add"], _add_body(world["crm_version"]))
        assert first.status_code == status and _code(first) == code
        assert first.json()["detail"] == detail and first.json()["request_id"]
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "refused"
        _requested, refused = await _audits(admin_session, org_id)
        assert refused.action == "subscription.add_product.refused"
        assert refused.after == {"status": status, "code": code, "idempotency_key": KEY}

        again = await _post(client, _urls(org_id)["add"], _add_body(world["crm_version"]))
        assert (again.status_code, _code(again), again.json()["detail"]) == (status, code, detail)
        assert len(_posts(upstreams, SUBSCRIBE_PATH)) == 1


class TestOnlyWhatIsOnOffer:
    """The plan switch's rule (ADR-0029), applied to the add: the confirm re-reads catalog's
    offerable set and refuses any other version, whatever a picker loaded earlier showed."""

    async def test_a_version_no_longer_on_offer_is_refused_recorded_and_replayed(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        superseded = uuid.uuid4()  # published and pinnable by id, but a newer version replaced it
        first = await _post(client, _urls(org_id)["add"], _add_body(superseded))
        assert first.status_code == 422 and _code(first) == "not_offerable"
        assert _posts(upstreams, SUBSCRIBE_PATH) == [], "subscription is never called"
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "refused"
        requested, refused = await _audits(admin_session, org_id)
        assert requested.action == "subscription.add_product.requested"
        assert refused.action == "subscription.add_product.refused"
        assert refused.after["code"] == "not_offerable"

        again = await _post(client, _urls(org_id)["add"], _add_body(superseded))
        assert again.status_code == 422 and _code(again) == "not_offerable"
        assert _posts(upstreams, SUBSCRIBE_PATH) == []

    async def test_a_catalogue_outage_claims_nothing(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        upstreams.json(
            "GET", f"/internal/v1/organizations/{org_id}/offerable-versions", {"detail": "x"}, 503
        )
        response = await _post(client, _urls(org_id)["add"], _add_body(world["crm_version"]))
        assert response.status_code == 503 and _code(response) == "catalog_unavailable"
        assert "changed nothing" in response.json()["detail"]
        assert await _claims(admin_session, org_id) == []
        assert await _audits(admin_session, org_id, "%") == []
        assert _posts(upstreams, SUBSCRIBE_PATH) == []

    async def test_a_recorded_success_still_replays_after_the_catalogue_moves_on(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        body = _add_body(world["crm_version"])
        assert (await _post(client, _urls(org_id)["add"], body)).status_code == 201
        _stub_offer(upstreams, org_id, {"crm": [uuid.uuid4()]})  # a newer version replaced it
        again = await _post(client, _urls(org_id)["add"], body)
        assert again.status_code == 201 and again.json()["replayed"] is True

    def _catalog_down(self, upstreams: FakeUpstreams, org_id: uuid.UUID) -> None:
        upstreams.json(
            "GET", f"/internal/v1/organizations/{org_id}/offerable-versions", {"detail": "x"}, 503
        )

    async def test_a_recorded_paid_add_replays_during_a_catalogue_outage(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The browser lost the 201 of a paid add whose first period is already claimed; the retry
        lands while catalog is down. It must replay the success — never "this request changed
        nothing", which would have the operator tell the customer no charge was set up."""
        upstreams.json(
            "POST",
            SUBSCRIBE_PATH,
            _opened(
                world["new_sub"],
                product_key="crm",
                plan_version_id=world["crm_version"],
                state="active",
            ),
            201,
        )
        body = _add_body(world["crm_version"], trial=False, acknowledge_immediate_charge=True)
        first = await _post(client, _urls(org_id)["add"], body)
        assert first.status_code == 201 and first.json()["charge_claimed"] is True
        self._catalog_down(upstreams, org_id)
        again = await _post(client, _urls(org_id)["add"], body)
        assert again.status_code == 201, again.text
        assert again.json()["replayed"] is True and again.json()["charge_claimed"] is True
        assert len(_posts(upstreams, SUBSCRIBE_PATH)) == 1

    async def test_a_recorded_refusal_replays_during_a_catalogue_outage(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        upstreams.json(
            "POST",
            SUBSCRIBE_PATH,
            {"detail": "exists", "errors": [{"code": "already_subscribed", "message": "exists"}]},
            409,
        )
        body = _add_body(world["crm_version"])
        assert (await _post(client, _urls(org_id)["add"], body)).status_code == 409
        self._catalog_down(upstreams, org_id)
        again = await _post(client, _urls(org_id)["add"], body)
        assert (again.status_code, _code(again)) == (409, "already_subscribed")

    async def test_an_unknown_earlier_attempt_during_an_outage_is_reconciled_or_stays_unknown(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """An earlier attempt with this key has no recorded answer. With catalog down the request
        cannot be repeated (the offer cannot be checked), so the key is left `unknown` and the
        answer says the outcome is unknown — not that nothing changed. Once the live read shows the
        add landed, the same key reconciles even while catalog is still down."""

        def _timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        upstreams.on("POST", SUBSCRIBE_PATH, _timeout)
        body = _add_body(world["crm_version"])
        assert (await _post(client, _urls(org_id)["add"], body)).status_code == 503
        self._catalog_down(upstreams, org_id)
        audits_before = len(await _audits(admin_session, org_id, "%"))

        retry = await _post(client, _urls(org_id)["add"], body)
        assert retry.status_code == 503 and _code(retry) == "outcome_unknown", retry.text
        assert "changed nothing" not in retry.json()["detail"]
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "unknown"
        assert len(await _audits(admin_session, org_id, "%")) == audits_before
        assert len(_posts(upstreams, SUBSCRIBE_PATH)) == 1, "never called without the offer"

        _stub_subscriptions(
            upstreams,
            org_id,
            [_opened(world["new_sub"], product_key="crm", plan_version_id=world["crm_version"])],
        )
        landed = await _post(client, _urls(org_id)["add"], body)
        assert landed.status_code == 201, landed.text
        assert landed.json()["replayed"] is True
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "succeeded"


class TestAddProductConcurrency:
    @pytest_asyncio.fixture
    async def per_request_sessions(self) -> AsyncIterator[None]:
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

    async def test_two_concurrent_requests_with_one_key_open_one_subscription(
        self,
        client: AsyncClient,
        per_request_sessions: None,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        async def _slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.4)
            return httpx.Response(
                201,
                json=_opened(
                    world["new_sub"], product_key="crm", plan_version_id=world["crm_version"]
                ),
            )

        upstreams.on("POST", SUBSCRIBE_PATH, _slow)  # type: ignore[arg-type]
        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(upstreams))

        async def _override() -> AsyncIterator[httpx.AsyncClient]:
            yield mock_client

        app.dependency_overrides[get_delegation_client] = _override
        body = _add_body(world["crm_version"])
        results = await asyncio.gather(
            _post(client, _urls(org_id)["add"], body), _post(client, _urls(org_id)["add"], body)
        )
        assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
        assert _code(next(r for r in results if r.status_code == 409)) == "request_in_progress"
        assert len(_posts(upstreams, SUBSCRIBE_PATH)) == 1


class TestAddProductOutcomeUnknown:
    def _fail(self, upstreams: FakeUpstreams, status: int | None) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            if status is None:
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(status, json={"detail": "boom"})

        upstreams.on("POST", SUBSCRIBE_PATH, _handler)

    @pytest.mark.parametrize("status", [None, 500, 503])
    async def test_a_lost_answer_is_a_503_that_says_so(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        status: int | None,
    ) -> None:
        self._fail(upstreams, status)
        response = await _post(client, _urls(org_id)["add"], _add_body(world["crm_version"]))
        assert response.status_code == 503 and _code(response) == "outcome_unknown"
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "unknown"
        assert (await _audits(admin_session, org_id))[-1].action == (
            "subscription.add_product.outcome_unknown"
        )

    async def test_a_retry_that_finds_it_landed_records_it_without_a_second_call(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        self._fail(upstreams, None)
        body = _add_body(world["crm_version"])
        assert (await _post(client, _urls(org_id)["add"], body)).status_code == 503
        _stub_subscriptions(
            upstreams,
            org_id,
            [_opened(world["new_sub"], product_key="crm", plan_version_id=world["crm_version"])],
        )
        retry = await _post(client, _urls(org_id)["add"], body)
        assert retry.status_code == 201, retry.text
        assert retry.json()["replayed"] is True
        assert retry.json()["subscription_id"] == str(world["new_sub"])
        assert len(_posts(upstreams, SUBSCRIBE_PATH)) == 1, "reconciled, not repeated"
        applied = (await _audits(admin_session, org_id))[-1]
        assert applied.action == "subscription.add_product.applied"
        assert applied.after["reconciled"] is True

    async def test_a_retry_that_finds_another_pin_makes_the_call(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """A row for the product on some *other* version is not this request landing: the call is
        made, and subscription says `already_subscribed`."""
        self._fail(upstreams, 502)
        body = _add_body(world["crm_version"])
        assert (await _post(client, _urls(org_id)["add"], body)).status_code == 503
        _stub_subscriptions(
            upstreams,
            org_id,
            [_opened(uuid.uuid4(), product_key="crm", plan_version_id=uuid.uuid4())],
        )
        upstreams.json(
            "POST",
            SUBSCRIBE_PATH,
            {"detail": "exists", "errors": [{"code": "already_subscribed", "message": "x"}]},
            409,
        )
        retry = await _post(client, _urls(org_id)["add"], body)
        assert retry.status_code == 409 and _code(retry) == "already_subscribed"
        assert len(_posts(upstreams, SUBSCRIBE_PATH)) == 2

    async def test_a_late_first_attempt_is_recognised_not_replayed_as_a_refusal(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The lost first attempt commits between the retry's live read (no row yet) and its call,
        which answers `already_subscribed` — for the row that very attempt made. Console looks
        again and records the success."""
        self._fail(upstreams, None)
        body = _add_body(world["crm_version"])
        assert (await _post(client, _urls(org_id)["add"], body)).status_code == 503

        reads = {"n": 0}
        landed = _opened(world["new_sub"], product_key="crm", plan_version_id=world["crm_version"])

        def _listing(_request: httpx.Request) -> httpx.Response:
            reads["n"] += 1
            return httpx.Response(200, json=[] if reads["n"] == 1 else [landed])

        upstreams.on("GET", f"/internal/v1/organizations/{org_id}/subscriptions", _listing)
        upstreams.json(
            "POST",
            SUBSCRIBE_PATH,
            {"detail": "exists", "errors": [{"code": "already_subscribed", "message": "x"}]},
            409,
        )
        retry = await _post(client, _urls(org_id)["add"], body)
        assert retry.status_code == 201, retry.text
        assert retry.json()["replayed"] is True
        assert retry.json()["subscription_id"] == str(world["new_sub"])
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "succeeded"

    async def test_a_late_landing_whose_re_read_fails_stays_unknown_and_reconciles_later(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The retry's call answers `already_subscribed`, and the look that would tell "the lost
        attempt landed late" from "some other row is there" fails. Neither is known, so the key
        stays `unknown` — never a permanent `refused` replayed for an add that may have been
        made — and the next retry with the same key reconciles from the live read."""
        self._fail(upstreams, None)
        body = _add_body(world["crm_version"])
        assert (await _post(client, _urls(org_id)["add"], body)).status_code == 503

        reads = {"n": 0}
        landed = _opened(world["new_sub"], product_key="crm", plan_version_id=world["crm_version"])

        def _listing(_request: httpx.Request) -> httpx.Response:
            reads["n"] += 1
            if reads["n"] == 2:
                return httpx.Response(503, json={"detail": "still flaky"})
            return httpx.Response(200, json=[] if reads["n"] == 1 else [landed])

        upstreams.on("GET", f"/internal/v1/organizations/{org_id}/subscriptions", _listing)
        upstreams.json(
            "POST",
            SUBSCRIBE_PATH,
            {"detail": "exists", "errors": [{"code": "already_subscribed", "message": "x"}]},
            409,
        )
        retry = await _post(client, _urls(org_id)["add"], body)
        assert retry.status_code == 503 and _code(retry) == "outcome_unknown", retry.text
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "unknown" and claim.response is None
        outcome = (await _audits(admin_session, org_id))[-1]
        assert outcome.action == "subscription.add_product.outcome_unknown"
        assert outcome.after["code"] == "already_subscribed"
        assert outcome.after["reread_failed"] is True

        third = await _post(client, _urls(org_id)["add"], body)
        assert third.status_code == 201, third.text
        assert third.json()["replayed"] is True
        assert third.json()["subscription_id"] == str(world["new_sub"])
        assert len(_posts(upstreams, SUBSCRIBE_PATH)) == 2, "the third reconciles, never calls"
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "succeeded"

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_refused_service_credential_is_never_a_401_to_the_operator(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        status: int,
    ) -> None:
        self._fail(upstreams, status)
        response = await _post(client, _urls(org_id)["add"], _add_body(world["crm_version"]))
        assert response.status_code == 503 and _code(response) == "upstream_refused"


# =========================================================================== 4. extend trial


class TestExtendTrial:
    async def test_the_owner_extends_and_the_instants_reach_subscription_unchanged(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        principal = operator_principal(roles=["owner"])
        response = await _post(client, _urls(org_id)["extend"], _extend_body(), principal=principal)
        assert response.status_code == 200, response.text
        out = response.json()
        assert datetime.fromisoformat(out["trial_ends_at"]) == NEW_END
        assert datetime.fromisoformat(out["previous_trial_ends_at"]) == datetime.fromisoformat(
            TRIAL_END
        )
        assert out["state"] == "trial" and out["replayed"] is False

        (sent,) = _posts(upstreams, _extend_path(world["sub"]))
        assert sent["organization_id"] == str(org_id)
        assert sent["operator_id"] == f"operator:{principal.sub}"
        assert sent["note"] == "Their data migration slipped a fortnight"
        assert datetime.fromisoformat(sent["new_trial_ends_at"]) == NEW_END
        # The end the operator saw goes back exactly — microseconds included — or subscription's
        # comparison under the lock would call every request stale.
        assert datetime.fromisoformat(sent["expected_trial_ends_at"]) == datetime.fromisoformat(
            TRIAL_END
        )

        (claim,) = await _claims(admin_session, org_id)
        assert (claim.action, claim.status) == ("extend_trial", "succeeded")
        requested, applied = await _audits(admin_session, org_id)
        assert requested.action == "subscription.trial_extension.requested"
        assert applied.action == "subscription.trial_extension.applied"
        for row in (requested, applied):
            assert row.entity_id == world["sub"] and row.acting_as == f"operator:{principal.sub}"
            assert row.reason == "Their data migration slipped a fortnight"

    async def test_no_subscription_for_the_product_is_a_404_with_a_code(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        response = await _post(client, _urls(org_id, "crm")["extend"], _extend_body())
        assert response.status_code == 404 and _code(response) == "no_subscription"
        assert await _claims(admin_session, org_id) == []

    @pytest.mark.parametrize(
        ("patch", "key"),
        [
            ({"new_trial_ends_at": "2026-11-03T23:59:59"}, KEY),
            ({"expected_trial_ends_at": "2026-10-20T09:00:00"}, KEY),
            ({"reason": "  "}, KEY),
            ({"days": 14}, KEY),
            ({}, None),
        ],
        ids=["naive-new", "naive-expected", "blank-reason", "relative-days", "no-key"],
    )
    async def test_a_malformed_request_is_refused_before_any_claim(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
        patch: dict[str, Any],
        key: str | None,
    ) -> None:
        response = await _post(client, _urls(org_id)["extend"], _extend_body(**patch), key=key)
        assert response.status_code == 422, response.text
        assert _posts(upstreams, _extend_path(world["sub"])) == []
        assert await _claims(admin_session, org_id) == []

    async def test_the_same_instant_in_another_zone_is_a_replay_not_a_new_request(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        first = await _post(client, _urls(org_id)["extend"], _extend_body())
        again = await _post(
            client,
            _urls(org_id)["extend"],
            _extend_body(new_trial_ends_at=NEW_END.astimezone(UTC).isoformat()),
        )
        assert (first.status_code, again.status_code) == (200, 200)
        assert again.json()["replayed"] is True
        assert len(_posts(upstreams, _extend_path(world["sub"]))) == 1

    async def test_a_key_used_for_another_action_is_a_409(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """A key names one request in an organization: reusing an Add product key for a trial
        extension is a different request, never a replay of the add's answer."""
        assert (
            await _post(client, _urls(org_id)["add"], _add_body(world["crm_version"]))
        ).status_code == 201
        response = await _post(client, _urls(org_id)["extend"], _extend_body())
        assert response.status_code == 409 and _code(response) == "idempotency_key_reused"
        assert _posts(upstreams, _extend_path(world["sub"])) == []

    async def test_a_different_end_under_the_same_key_is_a_409(
        self,
        client: AsyncClient,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        await _post(client, _urls(org_id)["extend"], _extend_body())
        later = (NEW_END + timedelta(days=1)).isoformat()
        response = await _post(
            client, _urls(org_id)["extend"], _extend_body(new_trial_ends_at=later)
        )
        assert response.status_code == 409 and _code(response) == "idempotency_key_reused"
        assert len(_posts(upstreams, _extend_path(world["sub"]))) == 1

    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (409, "state_changed"),
            (422, "not_trial"),
            (422, "claim_in_flight"),
            (422, "trial_cap"),
            (422, "trial_not_extended"),
            (422, "reserved_reason"),
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
            _extend_path(world["sub"]),
            {"detail": detail, "errors": [{"code": code, "message": detail}]},
            status,
        )
        first = await _post(client, _urls(org_id)["extend"], _extend_body())
        assert (first.status_code, _code(first), first.json()["detail"]) == (status, code, detail)
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "refused"
        assert (await _audits(admin_session, org_id))[-1].action == (
            "subscription.trial_extension.refused"
        )
        again = await _post(client, _urls(org_id)["extend"], _extend_body())
        assert (again.status_code, _code(again)) == (status, code)
        assert len(_posts(upstreams, _extend_path(world["sub"]))) == 1


class TestExtendTrialOutcomeUnknown:
    def _fail(self, upstreams: FakeUpstreams, world: dict[str, uuid.UUID]) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        upstreams.on("POST", _extend_path(world["sub"]), _handler)

    async def test_a_retry_that_finds_the_new_end_records_it_without_a_second_call(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        self._fail(upstreams, world)
        first = await _post(client, _urls(org_id)["extend"], _extend_body())
        assert first.status_code == 503 and _code(first) == "outcome_unknown"
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "unknown"

        # It did land: the live trial now ends where it was asked to.
        _stub_subscriptions(
            upstreams,
            org_id,
            [
                _trialling(
                    world["sub"], world["pin"], trial_ends_at=NEW_END.astimezone(UTC).isoformat()
                )
            ],
        )
        retry = await _post(client, _urls(org_id)["extend"], _extend_body())
        assert retry.status_code == 200, retry.text
        assert retry.json()["replayed"] is True
        assert len(_posts(upstreams, _extend_path(world["sub"]))) == 1
        applied = (await _audits(admin_session, org_id))[-1]
        assert applied.action == "subscription.trial_extension.applied"
        assert applied.after["reconciled"] is True

    async def test_a_late_first_attempt_is_recognised_not_replayed_as_a_refusal(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The lost first attempt commits between the retry's live read (old end) and its call,
        which then answers `state_changed` — moved by that very extension. Console looks again and
        records the success, rather than a key that replays "it changed" forever."""
        self._fail(upstreams, world)
        assert (await _post(client, _urls(org_id)["extend"], _extend_body())).status_code == 503

        reads = {"n": 0}
        old = _trialling(world["sub"], world["pin"], trial_ends_at=TRIAL_END)
        new = _trialling(
            world["sub"], world["pin"], trial_ends_at=NEW_END.astimezone(UTC).isoformat()
        )

        def _listing(_request: httpx.Request) -> httpx.Response:
            reads["n"] += 1
            return httpx.Response(200, json=[old if reads["n"] == 1 else new])

        upstreams.on("GET", f"/internal/v1/organizations/{org_id}/subscriptions", _listing)
        upstreams.json(
            "POST",
            _extend_path(world["sub"]),
            {"detail": "moved", "errors": [{"code": "state_changed", "message": "moved"}]},
            409,
        )
        retry = await _post(client, _urls(org_id)["extend"], _extend_body())
        assert retry.status_code == 200, retry.text
        assert retry.json()["replayed"] is True
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "succeeded"
        assert (await _audits(admin_session, org_id))[-1].after["reconciled"] is True

    async def test_a_late_landing_whose_re_read_fails_stays_unknown_and_reconciles_later(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The retry's call answers `state_changed`, and the look that would tell "the lost
        extension landed late" from "someone else moved it" fails. Neither is known, so the key
        stays `unknown` rather than replaying "it changed" forever, and the next retry with the
        same key reconciles from the live read."""
        self._fail(upstreams, world)
        assert (await _post(client, _urls(org_id)["extend"], _extend_body())).status_code == 503

        reads = {"n": 0}
        old = _trialling(world["sub"], world["pin"], trial_ends_at=TRIAL_END)
        new = _trialling(
            world["sub"], world["pin"], trial_ends_at=NEW_END.astimezone(UTC).isoformat()
        )

        def _listing(_request: httpx.Request) -> httpx.Response:
            reads["n"] += 1
            if reads["n"] == 2:
                return httpx.Response(503, json={"detail": "still flaky"})
            return httpx.Response(200, json=[old if reads["n"] == 1 else new])

        upstreams.on("GET", f"/internal/v1/organizations/{org_id}/subscriptions", _listing)
        upstreams.json(
            "POST",
            _extend_path(world["sub"]),
            {"detail": "moved", "errors": [{"code": "state_changed", "message": "moved"}]},
            409,
        )
        retry = await _post(client, _urls(org_id)["extend"], _extend_body())
        assert retry.status_code == 503 and _code(retry) == "outcome_unknown", retry.text
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "unknown" and claim.response is None
        outcome = (await _audits(admin_session, org_id))[-1]
        assert outcome.action == "subscription.trial_extension.outcome_unknown"
        assert outcome.after["code"] == "state_changed"
        assert outcome.after["reread_failed"] is True

        third = await _post(client, _urls(org_id)["extend"], _extend_body())
        assert third.status_code == 200, third.text
        assert third.json()["replayed"] is True
        assert len(_posts(upstreams, _extend_path(world["sub"]))) == 2, "reconciled, not called"
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "succeeded"

    async def test_a_retry_that_finds_the_old_end_makes_the_call(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        upstreams: FakeUpstreams,
        org_id: uuid.UUID,
        world: dict[str, uuid.UUID],
    ) -> None:
        self._fail(upstreams, world)
        assert (await _post(client, _urls(org_id)["extend"], _extend_body())).status_code == 503
        upstreams.json(
            "POST",
            _extend_path(world["sub"]),
            _trialling(world["sub"], world["pin"], trial_ends_at=NEW_END.isoformat()),
        )
        retry = await _post(client, _urls(org_id)["extend"], _extend_body())
        assert retry.status_code == 200 and retry.json()["replayed"] is False
        assert len(_posts(upstreams, _extend_path(world["sub"]))) == 2
        (claim,) = await _claims(admin_session, org_id)
        assert claim.status == "succeeded"


# ============================================================================== the contract


class TestContractKeepsTheReasonOffTheEvent:
    """The published contract must not say the operator's reason rides the subscription's event:
    it never does (a customer org's notification templates render the whole payload), and a
    contract that says so invites someone to "fix" the code to match it."""

    @pytest.mark.parametrize("suffix", ["/subscriptions", "/extend-trial"])
    def test_the_route_says_audit_trail_only(self, suffix: str) -> None:
        paths = app.openapi()["paths"]
        (path,) = [
            p
            for p, operations in paths.items()
            if p.startswith("/api/v1/admin/organizations/")
            and p.endswith(suffix)
            and "post" in operations
        ]
        description = paths[path]["post"]["description"]
        assert "never on the subscription's event" in description
        assert "and the subscription's event" not in description


# ============================================================================ fingerprints


class TestFingerprints:
    def test_add_product_names_the_decision_not_the_words(self) -> None:
        from app.domain import add_product_fingerprint

        base = {
            "organization_id": "o",
            "product_key": "crm",
            "plan_version_id": "v",
            "trial": True,
        }
        assert add_product_fingerprint(**base) == add_product_fingerprint(**base)
        assert add_product_fingerprint(**base) != add_product_fingerprint(
            **{**base, "trial": False}
        )
        assert add_product_fingerprint(**base) != add_product_fingerprint(
            **{**base, "plan_version_id": "w"}
        )

    def test_extend_trial_normalises_the_zone(self) -> None:
        from app.domain import extend_trial_fingerprint

        expected = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
        ist = extend_trial_fingerprint(
            organization_id="o",
            product_key="hrms",
            subscription_id="s",
            new_trial_ends_at=NEW_END,
            expected_trial_ends_at=expected,
        )
        utc = extend_trial_fingerprint(
            organization_id="o",
            product_key="hrms",
            subscription_id="s",
            new_trial_ends_at=NEW_END.astimezone(UTC),
            expected_trial_ends_at=expected.astimezone(IST),
        )
        later = extend_trial_fingerprint(
            organization_id="o",
            product_key="hrms",
            subscription_id="s",
            new_trial_ends_at=NEW_END + timedelta(seconds=1),
            expected_trial_ends_at=expected,
        )
        assert ist == utc and ist != later


# ================================================================ 5. the live composite


class TestPricingCompositeCarriesTheTrialFacts:
    async def test_the_trial_start_and_its_cap_come_through_from_subscription(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        sub_id, pin = uuid.uuid4(), uuid.uuid4()
        _stub_directory(upstreams, org_id)
        _stub_public_plans(upstreams, [])
        _stub_subscriptions(upstreams, org_id, [_trialling(sub_id, pin, trial_ends_at=TRIAL_END)])
        _stub_plan_version(upstreams, pin, _version_doc(pin, amount_minor=1000))
        with as_principal(operator_principal(roles=["support"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/pricing")
        assert response.status_code == 200, response.text
        (product,) = response.json()["products"]
        sub = product["subscription"]
        assert datetime.fromisoformat(sub["trial_ends_at"]) == datetime.fromisoformat(TRIAL_END)
        assert sub["trial_ends_at"].endswith(".123456Z"), "microseconds survive the composite"
        assert datetime.fromisoformat(sub["trial_max_ends_at"]) == datetime(
            2026, 12, 30, tzinfo=UTC
        )
        assert sub["current_period_start"] is not None
