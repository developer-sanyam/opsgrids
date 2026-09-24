"""`GET`/`POST /admin/organizations/{id}/pricing` — console's client-pricing endpoints.

Properties covered, each with its negative case:

1. **Audience/role boundary** — a product token 404s (ADR-0003 §2); an operator missing the
   required role gets 403; `support` may read but not write.
2. **Composition** — the GET composes account's directory existence check, catalog's public and
   pinned prices, subscription's live subscription, and console's own audit history into one view.
3. **The POST orchestration and its timing rule** — trial and paid-upgrade re-pins are `"now"`;
   a paid downgrade is `"next_period"`; no subscription, `apply_to_subscription=false`, and a
   subscription state subscription itself refuses to re-pin are all `"not_subscribed"`.
4. **Idempotency** — a repeated `Idempotency-Key` with the same body replays the first result
   without a second call to catalog or subscription; the same key with a different body is 409.
5. **Upstream errors map onto the documented codes** — catalog's 404/409 pass through; a business
   rule refusal from subscription does not fail the request.
"""

from __future__ import annotations

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
