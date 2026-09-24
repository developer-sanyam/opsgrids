"""`GET`/`POST /admin/organizations/{id}/pricing` — console's client-pricing endpoints.

Properties covered, each with its negative case:

1. **Audience/role boundary** — a product token 404s (ADR-0003 §2); an operator missing the
   required role gets 403; `support` may read but not write.
2. **Composition** — the GET composes account's directory existence check, catalog's public and
   pinned prices, subscription's live subscription, and console's own audit history into one view.
3. **The POST orchestration and its timing rule (revised, fix round 1)** — a trial re-pins
   `"now"`; an *active, paying* subscription always gets `timing="next_period"` sent to
   subscription, up or down, and `effective` reports exactly what subscription's own response
   says happened; no subscription, `apply_to_subscription=false`, and subscription's 422
   business-rule refusal are all `"not_subscribed"`. A 409 (`BillingCatchingUp`) is its own
   surfaced 409, never folded into a false `"not_subscribed"` success (MAJOR-4); neither is an
   unclassified failure, which is a 503.
4. **Idempotency** — a repeated `Idempotency-Key` with the same body replays the first result
   without a second call to catalog or subscription; the same key with a different body is 409;
   the same key reused for a *different organization* is its own independent request, never a
   cross-tenant replay.
5. **Upstream errors map onto the documented codes** — catalog's 404/409/403 pass through
   (MAJOR-2); a `product_key` shaped like a path-traversal probe is refused before any call is
   made (MAJOR-1).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from app.api.routes import get_delegation_client
from app.main import app
from tests.helpers import as_principal, operator_principal

pytestmark = pytest.mark.integration

PRODUCT_KEY = "hrms"


def _directory_body(organization_id: uuid.UUID) -> dict[str, Any]:
    return {
        "organization_id": str(organization_id),
        "slug": "acme",
        "name": "Acme",
        "status": "active",
        "country": "IN",
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def _version_doc(
    version_id: uuid.UUID,
    *,
    amount_minor: int,
    currency: str = "INR",
    billing_interval: str = "month",
    restricted_to_org_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": str(version_id),
        "plan_id": str(uuid.uuid4()),
        "product_key": PRODUCT_KEY,
        "version_no": 1,
        "state": "published",
        "billing_interval": billing_interval,
        "currency": currency,
        "restricted_to_org_id": str(restricted_to_org_id) if restricted_to_org_id else None,
        "prices": [{"kind": "flat", "amount_minor": amount_minor, "currency": currency}],
        "features": [],
        "seat_classes": [],
    }


def _subscription(
    *,
    subscription_id: uuid.UUID,
    product_key: str = PRODUCT_KEY,
    state: str = "active",
    plan_version_id: uuid.UUID,
    pending_plan_version_id: uuid.UUID | None = None,
    currency: str = "INR",
) -> dict[str, Any]:
    return {
        "id": str(subscription_id),
        "product_key": product_key,
        "plan_version_id": str(plan_version_id),
        "state": state,
        "trial_ends_at": None,
        "current_period_start": "2026-01-01T00:00:00+00:00",
        "current_period_end": "2026-02-01T00:00:00+00:00",
        "cancel_at": None,
        "pending_plan_version_id": str(pending_plan_version_id)
        if pending_plan_version_id
        else None,
        "currency": currency,
        "entitled_products": [product_key],
        "items": [],
        "dunning": None,
    }


class FakeUpstreams:
    """Routes every catalog/subscription/account call the pricing endpoints make, keyed on
    method and path. An unstubbed route raises rather than silently answering 200/empty, so a
    test that forgets to wire a call fails loudly instead of passing for the wrong reason."""

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}
        self.requests: list[httpx.Request] = []

    def json(self, method: str, path: str, body: Any, status_code: int = 200) -> None:
        self._routes[(method, path)] = lambda _request: httpx.Response(status_code, json=body)

    def on(
        self, method: str, path: str, handler: Callable[[httpx.Request], httpx.Response]
    ) -> None:
        self._routes[(method, path)] = handler

    def calls(self, method: str, path: str) -> int:
        return sum(1 for r in self.requests if r.method == method and r.url.path == path)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self._routes.get((request.method, request.url.path))
        if handler is None:
            raise AssertionError(f"unstubbed upstream call: {request.method} {request.url}")
        return handler(request)


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


def _stub_directory(
    upstreams: FakeUpstreams, organization_id: uuid.UUID, *, found: bool = True
) -> None:
    upstreams.json(
        "GET",
        f"/api/v1/admin/directory/organizations/{organization_id}",
        _directory_body(organization_id),
        status_code=200 if found else 404,
    )


def _stub_public_plans(upstreams: FakeUpstreams, plans: list[dict[str, Any]]) -> None:
    upstreams.json("GET", "/internal/v1/public-flat-plans", {"plans": plans})


def _stub_subscriptions(
    upstreams: FakeUpstreams, organization_id: uuid.UUID, rows: list[dict[str, Any]]
) -> None:
    upstreams.json("GET", f"/internal/v1/organizations/{organization_id}/subscriptions", rows)


def _stub_plan_version(
    upstreams: FakeUpstreams, version_id: uuid.UUID, doc: dict[str, Any] | None
) -> None:
    upstreams.json(
        "GET",
        f"/internal/v1/plan-versions/{version_id}",
        doc if doc is not None else {"detail": "not found"},
        status_code=200 if doc is not None else 404,
    )


def _stub_bespoke_price(
    upstreams: FakeUpstreams,
    organization_id: uuid.UUID,
    product_key: str,
    doc: dict[str, Any],
    *,
    status_code: int = 200,
) -> None:
    upstreams.json(
        "POST",
        f"/admin/internal/catalog/organizations/{organization_id}/products/{product_key}/bespoke-price",
        doc,
        status_code=status_code,
    )


def _stub_change_plan(
    upstreams: FakeUpstreams,
    subscription_id: uuid.UUID,
    doc: dict[str, Any],
    *,
    status_code: int = 200,
) -> None:
    upstreams.json(
        "POST",
        f"/internal/v1/subscriptions/{subscription_id}/change-plan",
        doc,
        status_code=status_code,
    )


def _last_change_plan_body(upstreams: FakeUpstreams, subscription_id: uuid.UUID) -> dict[str, Any]:
    path = f"/internal/v1/subscriptions/{subscription_id}/change-plan"
    matches = [r for r in upstreams.requests if r.method == "POST" and r.url.path == path]
    assert matches, f"no change-plan call recorded for {subscription_id}"
    body: dict[str, Any] = json.loads(matches[-1].content)
    return body


# --------------------------------------------------------------------- GET: authz


class TestPricingReadAuthorization:
    async def test_a_product_audience_token_is_404(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal(staff=False)):
            resp = await client.get(f"/api/v1/admin/organizations/{org_id}/pricing")
        assert resp.status_code == 404

    async def test_support_may_read(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        _stub_directory(upstreams, org_id)
        _stub_public_plans(upstreams, [])
        _stub_subscriptions(upstreams, org_id, [])
        with as_principal(operator_principal(roles=["support"])):
            resp = await client.get(f"/api/v1/admin/organizations/{org_id}/pricing")
        assert resp.status_code == 200

    async def test_an_unknown_organization_is_404(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        _stub_directory(upstreams, org_id, found=False)
        with as_principal(operator_principal()):
            resp = await client.get(f"/api/v1/admin/organizations/{org_id}/pricing")
        assert resp.status_code == 404


# --------------------------------------------------------------------- GET: composition


class TestPricingRead:
    async def test_it_composes_public_current_and_history(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        public_version = uuid.uuid4()
        bespoke_version = uuid.uuid4()
        subscription_id = uuid.uuid4()

        _stub_directory(upstreams, org_id)
        _stub_public_plans(
            upstreams,
            [
                {
                    "product_key": PRODUCT_KEY,
                    "product_name": "People",
                    "plan_id": str(uuid.uuid4()),
                    "plan_version_id": str(public_version),
                    "amount_minor": 199900,
                    "currency": "INR",
                    "billing_interval": "month",
                }
            ],
        )
        _stub_subscriptions(
            upstreams,
            org_id,
            [
                _subscription(
                    subscription_id=subscription_id, plan_version_id=bespoke_version, state="active"
                )
            ],
        )
        _stub_plan_version(
            upstreams,
            bespoke_version,
            _version_doc(bespoke_version, amount_minor=75000, restricted_to_org_id=org_id),
        )

        with as_principal(operator_principal()):
            resp = await client.get(f"/api/v1/admin/organizations/{org_id}/pricing")
        assert resp.status_code == 200
        products = {p["product_key"]: p for p in resp.json()["products"]}
        row = products[PRODUCT_KEY]
        assert row["product_name"] == "People"
        assert row["public_price"]["amount_minor"] == 199900
        assert row["public_price"]["is_bespoke"] is False
        assert row["current_price"]["amount_minor"] == 75000
        assert row["current_price"]["is_bespoke"] is True
        assert row["subscription"]["id"] == str(subscription_id)
        assert row["subscription"]["state"] == "active"

    async def test_a_product_with_no_subscription_still_lists_with_nulls(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        _stub_directory(upstreams, org_id)
        _stub_public_plans(
            upstreams,
            [
                {
                    "product_key": "crm",
                    "product_name": "Revenue",
                    "plan_id": str(uuid.uuid4()),
                    "plan_version_id": str(uuid.uuid4()),
                    "amount_minor": 199900,
                    "currency": "INR",
                    "billing_interval": "month",
                }
            ],
        )
        _stub_subscriptions(upstreams, org_id, [])

        with as_principal(operator_principal()):
            resp = await client.get(f"/api/v1/admin/organizations/{org_id}/pricing")
        assert resp.status_code == 200
        row = next(p for p in resp.json()["products"] if p["product_key"] == "crm")
        assert row["subscription"] is None
        assert row["current_price"] is None
        assert row["public_price"]["amount_minor"] == 199900
        assert row["history"] == []


# --------------------------------------------------------------------- POST: authz


class TestPricingWriteAuthorization:
    async def test_a_product_audience_token_is_404(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal(staff=False)):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "negotiated",
                },
            )
        assert resp.status_code == 404

    async def test_support_may_not_write(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal(roles=["support"])):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "negotiated",
                },
            )
        assert resp.status_code == 403

    async def test_billing_ops_may_write(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        version = uuid.uuid4()
        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(version, amount_minor=50000)
        )
        _stub_subscriptions(upstreams, org_id, [])
        with as_principal(operator_principal(roles=["billing_ops"])):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "negotiated",
                },
            )
        assert resp.status_code == 201, resp.text


# --------------------------------------------------------------------- POST: validation


class TestPricingWriteValidation:
    async def test_an_unknown_field_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "negotiated",
                    "sneaky": "field",
                },
            )
        assert resp.status_code == 422

    async def test_a_non_positive_amount_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 0,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "negotiated",
                },
            )
        assert resp.status_code == 422

    async def test_an_invalid_currency_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "XX",
                    "billing_interval": "month",
                    "reason": "negotiated",
                },
            )
        assert resp.status_code == 422

    async def test_a_missing_reason_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                },
            )
        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "product_key",
        [
            "../../../../../admin/catalog/plans?",
            "hrms/../../evil",
            "HRMS",
            "hrms bad",
            "a" * 41,
        ],
    )
    async def test_a_product_key_outside_the_pattern_is_refused(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID, product_key: str
    ) -> None:
        """MAJOR-1 (fix round 1): rejected by pydantic before any upstream call is made — the
        probe value that used to reach catalog_client and become part of a URL now never leaves
        this validator. No upstream is stubbed on purpose: a call here is a test failure by
        itself (`FakeUpstreams` raises on an unstubbed request)."""
        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": product_key,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "negotiated",
                },
            )
        assert resp.status_code == 422, resp.text
        assert upstreams.requests == []

    async def test_an_amount_over_the_ceiling_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 10_000_000_001,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "negotiated",
                },
            )
        assert resp.status_code == 422

    async def test_an_idempotency_key_over_255_characters_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "negotiated",
                },
                headers={"Idempotency-Key": "k" * 256},
            )
        assert resp.status_code == 422


# --------------------------------------------------------------------- POST: the timing rule


class TestPricingWriteTiming:
    async def test_a_trial_is_applied_now(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        old_version = uuid.uuid4()
        new_version = uuid.uuid4()
        subscription_id = uuid.uuid4()

        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(new_version, amount_minor=50000)
        )
        _stub_subscriptions(
            upstreams,
            org_id,
            [
                _subscription(
                    subscription_id=subscription_id, plan_version_id=old_version, state="trial"
                )
            ],
        )
        _stub_change_plan(
            upstreams,
            subscription_id,
            _subscription(
                subscription_id=subscription_id, plan_version_id=new_version, state="trial"
            ),
        )

        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "trial discount",
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["effective"] == "now"
        assert body["applied_to_subscription_id"] == str(subscription_id)
        assert body["plan_version_id"] == str(new_version)
        # PAID-UPGRADE TIMING (fix round 1): a trial has nothing to defer, so subscription's own
        # immediate default decides — console must not force a timing on it.
        assert "timing" not in _last_change_plan_body(upstreams, subscription_id)

    async def test_a_paid_upgrade_is_also_scheduled_for_next_period(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        """PAID-UPGRADE TIMING (fix round 1): the independent review changed this from the
        original "an upgrade re-pins now" rule — an active, paying subscription defers *every*
        staff price change to the next period, whichever direction it moves the price, so a
        higher bespoke price never prorates and charges a card immediately for a price the
        customer never chose at checkout."""
        old_version = uuid.uuid4()
        new_version = uuid.uuid4()
        subscription_id = uuid.uuid4()

        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(new_version, amount_minor=90000)
        )
        _stub_subscriptions(
            upstreams,
            org_id,
            [
                _subscription(
                    subscription_id=subscription_id, plan_version_id=old_version, state="active"
                )
            ],
        )
        _stub_change_plan(
            upstreams,
            subscription_id,
            _subscription(
                subscription_id=subscription_id,
                plan_version_id=old_version,
                pending_plan_version_id=new_version,
                state="active",
            ),
        )

        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 90000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "expansion deal",
                },
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()["effective"] == "next_period"
        assert _last_change_plan_body(upstreams, subscription_id)["timing"] == "next_period"

    async def test_a_paid_downgrade_is_scheduled_for_next_period(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        old_version = uuid.uuid4()
        new_version = uuid.uuid4()
        subscription_id = uuid.uuid4()

        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(new_version, amount_minor=10000)
        )
        _stub_subscriptions(
            upstreams,
            org_id,
            [
                _subscription(
                    subscription_id=subscription_id, plan_version_id=old_version, state="active"
                )
            ],
        )
        # A downgrade does not move the pin now — subscription answers with the *old* pin still
        # active and the new one waiting as `pending_plan_version_id` (ADR-0005 addendum §4).
        _stub_change_plan(
            upstreams,
            subscription_id,
            _subscription(
                subscription_id=subscription_id,
                plan_version_id=old_version,
                pending_plan_version_id=new_version,
                state="active",
            ),
        )

        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 10000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "discount",
                },
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()["effective"] == "next_period"
        assert _last_change_plan_body(upstreams, subscription_id)["timing"] == "next_period"

    async def test_no_subscription_is_not_subscribed(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        new_version = uuid.uuid4()
        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(new_version, amount_minor=50000)
        )
        _stub_subscriptions(upstreams, org_id, [])

        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "new deal",
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["effective"] == "not_subscribed"
        assert body["applied_to_subscription_id"] is None

    async def test_apply_to_subscription_false_never_calls_subscription(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        new_version = uuid.uuid4()
        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(new_version, amount_minor=50000)
        )

        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "quote only",
                    "apply_to_subscription": False,
                },
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()["effective"] == "not_subscribed"
        assert upstreams.calls("GET", f"/internal/v1/organizations/{org_id}/subscriptions") == 0

    async def test_a_business_rule_refusal_from_subscription_is_not_subscribed_not_a_failure(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        """A `past_due`/`suspended`/`cancelled` subscription refuses `change-plan` outright
        (subscription's own rule). The bespoke price still gets created; the request still
        succeeds; only the re-pin is skipped (documented deviation — see `app/pricing.py`)."""
        old_version = uuid.uuid4()
        new_version = uuid.uuid4()
        subscription_id = uuid.uuid4()

        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(new_version, amount_minor=50000)
        )
        _stub_subscriptions(
            upstreams,
            org_id,
            [
                _subscription(
                    subscription_id=subscription_id, plan_version_id=old_version, state="past_due"
                )
            ],
        )
        _stub_change_plan(
            upstreams,
            subscription_id,
            {"detail": "a past_due subscription cannot change plan"},
            status_code=422,
        )

        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "renegotiated",
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["effective"] == "not_subscribed"
        assert body["applied_to_subscription_id"] is None
        assert body["plan_version_id"] == str(new_version)


# ------------------------------------------------------- POST: subscription errors (MAJOR-4)


class TestPricingWriteSubscriptionErrors:
    """Before this fix, `_apply_to_subscription` folded *every* non-422 4xx from
    `change-plan` — a 409 `BillingCatchingUp` included — into a `201` claiming
    `effective="not_subscribed"`. That is actively wrong for a `BillingCatchingUp` 409: the
    subscription is very much still subscribed, and reporting it as if it were not hid the same
    shape of failure a broken credential would produce. Only subscription's own 422 business-rule
    refusal (past_due/suspended/cancelled) may still fold into `not_subscribed`; everything else
    is either this endpoint's own 409, or a 503 — never a false success.
    """

    async def test_a_billing_catching_up_409_is_surfaced_not_folded_into_a_success(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        old_version = uuid.uuid4()
        new_version = uuid.uuid4()
        subscription_id = uuid.uuid4()

        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(new_version, amount_minor=50000)
        )
        _stub_subscriptions(
            upstreams,
            org_id,
            [
                _subscription(
                    subscription_id=subscription_id, plan_version_id=old_version, state="active"
                )
            ],
        )
        _stub_change_plan(
            upstreams,
            subscription_id,
            {"detail": "billing has not caught up for this subscription"},
            status_code=409,
        )

        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "renegotiated",
                },
            )
        assert resp.status_code == 409, resp.text
        assert "billing is catching up" in resp.json()["detail"]

    async def test_an_unclassified_subscription_failure_is_503_not_not_subscribed(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        """M10: a 5xx (or anything not 422/409) from subscription must never read as
        `not_subscribed` — that silently hides a real outage or a credential misconfiguration
        behind what looks like a normal, successful response."""
        old_version = uuid.uuid4()
        new_version = uuid.uuid4()
        subscription_id = uuid.uuid4()

        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(new_version, amount_minor=50000)
        )
        _stub_subscriptions(
            upstreams,
            org_id,
            [
                _subscription(
                    subscription_id=subscription_id, plan_version_id=old_version, state="active"
                )
            ],
        )
        _stub_change_plan(upstreams, subscription_id, {"detail": "internal error"}, status_code=500)

        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "renegotiated",
                },
            )
        assert resp.status_code == 503, resp.text

    async def test_a_replayed_billing_catching_up_result_stays_a_409_on_retry(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        """A retried request under the same `Idempotency-Key` must not turn the first attempt's
        409 into a fabricated 201 success — the replay path re-raises the same 409."""
        old_version = uuid.uuid4()
        new_version = uuid.uuid4()
        subscription_id = uuid.uuid4()

        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(new_version, amount_minor=50000)
        )
        _stub_subscriptions(
            upstreams,
            org_id,
            [
                _subscription(
                    subscription_id=subscription_id, plan_version_id=old_version, state="active"
                )
            ],
        )
        _stub_change_plan(
            upstreams,
            subscription_id,
            {"detail": "billing has not caught up for this subscription"},
            status_code=409,
        )

        body = {
            "product_key": PRODUCT_KEY,
            "amount_minor": 50000,
            "currency": "INR",
            "billing_interval": "month",
            "reason": "renegotiated",
        }
        with as_principal(operator_principal()):
            first = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json=body,
                headers={"Idempotency-Key": "catching-up-retry"},
            )
            second = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json=body,
                headers={"Idempotency-Key": "catching-up-retry"},
            )
        assert first.status_code == 409
        assert second.status_code == 409
        # The replay must not have called subscription's change-plan a second time.
        assert (
            upstreams.calls("POST", f"/internal/v1/subscriptions/{subscription_id}/change-plan")
            == 1
        )


# --------------------------------------------------------------------- POST: catalog errors


class TestPricingWriteCatalogErrors:
    async def test_an_unknown_product_is_404(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams,
            org_id,
            "no-such-product",
            {"detail": "No such product"},
            status_code=404,
        )
        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": "no-such-product",
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "x",
                },
            )
        assert resp.status_code == 404

    async def test_a_product_with_no_public_flat_plan_is_409(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams,
            org_id,
            PRODUCT_KEY,
            {"detail": "no public flat plan to clone"},
            status_code=409,
        )
        with as_principal(operator_principal()):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "x",
                },
            )
        assert resp.status_code == 409

    async def test_a_403_from_catalogs_own_role_check_is_a_403_not_a_503(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        """MAJOR-2 (fix round 1): catalog's `require_operator` role check refusing the forwarded
        operator's role is a real, caller-facing authorization decision — it used to be folded
        into the generic 503 "catalog is unavailable" bucket alongside a genuine credential
        failure, which hid an authz refusal behind an outage-shaped error."""
        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams,
            org_id,
            PRODUCT_KEY,
            {"detail": "This action needs one of these operator roles: billing_ops, owner"},
            status_code=403,
        )
        with as_principal(operator_principal(roles=["billing_ops"])):
            resp = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "x",
                },
            )
        assert resp.status_code == 403, resp.text


# --------------------------------------------------------------------- POST: idempotency


class TestPricingIdempotency:
    async def test_a_repeated_key_with_the_same_body_replays_without_recalling_upstreams(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        new_version = uuid.uuid4()
        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(new_version, amount_minor=50000)
        )
        _stub_subscriptions(upstreams, org_id, [])

        body = {
            "product_key": PRODUCT_KEY,
            "amount_minor": 50000,
            "currency": "INR",
            "billing_interval": "month",
            "reason": "negotiated",
        }
        with as_principal(operator_principal()):
            first = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json=body,
                headers={"Idempotency-Key": "retry-1"},
            )
            second = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json=body,
                headers={"Idempotency-Key": "retry-1"},
            )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json() == second.json()
        assert (
            upstreams.calls(
                "POST",
                f"/admin/internal/catalog/organizations/{org_id}/products/{PRODUCT_KEY}/bespoke-price",
            )
            == 1
        ), "the second call must not repeat the catalog request"

    async def test_the_same_key_with_a_different_body_is_a_conflict(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        new_version = uuid.uuid4()
        _stub_directory(upstreams, org_id)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(new_version, amount_minor=50000)
        )
        _stub_subscriptions(upstreams, org_id, [])

        with as_principal(operator_principal()):
            first = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 50000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "negotiated",
                },
                headers={"Idempotency-Key": "retry-2"},
            )
            second = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json={
                    "product_key": PRODUCT_KEY,
                    "amount_minor": 60000,
                    "currency": "INR",
                    "billing_interval": "month",
                    "reason": "negotiated",
                },
                headers={"Idempotency-Key": "retry-2"},
            )
        assert first.status_code == 201
        assert second.status_code == 409

    async def test_the_same_key_reused_for_a_different_organization_is_independent(
        self, client: AsyncClient, upstreams: FakeUpstreams, org_id: uuid.UUID
    ) -> None:
        """M5a/M5b: `Idempotency-Key` scopes by (organization, key) — reusing the same header
        value for a *different* organization must never replay the first organization's result,
        and must not be treated as "the same key with a different body" either. Each organization
        gets its own, independent call to catalog."""
        other_org = uuid.uuid4()
        version_for_org = uuid.uuid4()
        version_for_other_org = uuid.uuid4()

        _stub_directory(upstreams, org_id)
        _stub_directory(upstreams, other_org)
        _stub_bespoke_price(
            upstreams, org_id, PRODUCT_KEY, _version_doc(version_for_org, amount_minor=50000)
        )
        _stub_bespoke_price(
            upstreams,
            other_org,
            PRODUCT_KEY,
            _version_doc(version_for_other_org, amount_minor=50000),
        )
        _stub_subscriptions(upstreams, org_id, [])
        _stub_subscriptions(upstreams, other_org, [])

        body = {
            "product_key": PRODUCT_KEY,
            "amount_minor": 50000,
            "currency": "INR",
            "billing_interval": "month",
            "reason": "negotiated",
        }
        with as_principal(operator_principal()):
            for_org = await client.post(
                f"/api/v1/admin/organizations/{org_id}/pricing",
                json=body,
                headers={"Idempotency-Key": "shared-across-orgs"},
            )
            for_other_org = await client.post(
                f"/api/v1/admin/organizations/{other_org}/pricing",
                json=body,
                headers={"Idempotency-Key": "shared-across-orgs"},
            )
        assert for_org.status_code == 201, for_org.text
        assert for_other_org.status_code == 201, for_other_org.text
        assert for_org.json()["plan_version_id"] == str(version_for_org)
        assert for_other_org.json()["plan_version_id"] == str(version_for_other_org), (
            "the second organization's own request must not replay the first organization's "
            "cached result"
        )
        assert (
            upstreams.calls(
                "POST",
                f"/admin/internal/catalog/organizations/{org_id}/products/{PRODUCT_KEY}/bespoke-price",
            )
            == 1
        )
        assert (
            upstreams.calls(
                "POST",
                f"/admin/internal/catalog/organizations/{other_org}/products/{PRODUCT_KEY}/bespoke-price",
            )
            == 1
        )
