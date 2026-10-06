"""Online payment for an order through a merchant's **Cashfree** account (ADR-0033), from the
order's side: the payer negotiation, the provider-shaped checkout session, and the phone that goes
to payment only when the provider needs it — and is never invented, logged or audited here.

payment's own process cannot be imported here (every service exposes a top-level `app`), so the
routes talk to a recording double of `CollectionsClient` that answers as payment does; payment's
side of the same contract is proven in `services/payment/tests/test_merchant_cashfree_api.py`.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import structlog
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import AuditLog
from app.orders import selectors as order_selectors
from app.orders.payment_client import (
    HttpCollectionsClient,
    PayerRequired,
    PhoneRequired,
    get_collections_client,
)
from tests.fulfilment_fixtures import Floor, accept_order, build_floor
from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

COLLECT = "operations.orders.payment.collect:ORG"
READ = "operations.orders.order.read:ORG"
PHONE = "9999900000"
#: Captured once: a test patches `httpx.AsyncClient`, and a second patch must wrap the real one.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture
async def floor(session, org_id) -> Floor:  # type: ignore[no-untyped-def]
    return await build_floor(session, org_id, stock=100)


def _collection(order_id: uuid.UUID) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "reference_type": "operations.order",
        "reference_id": str(order_id),
        "state": "created",
        "amount_minor": 1,
        "currency": "INR",
        "provider": "cashfree",
        "provider_order_id": None,
        "provider_payment_id": None,
        "failure_reason": None,
        "refunded_minor": 0,
        "refunds": [],
        "history": [],
        "created_at": now,
        "updated_at": now,
    }


class CashfreeCollections:
    """A recording `CollectionsClient` that answers as payment does for a Cashfree account: a
    checkout without a payer is `payer-required`; with one, a Cashfree session."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def list_for_order(self, organization_id, order_id):  # type: ignore[no-untyped-def]
        return {
            "merchant_account_configured": True,
            "mode": "test",
            "wire": "stub",
            "provider": "cashfree",
            "provider_wire": "stub",
            "collections": [],
        }

    async def checkout(  # type: ignore[no-untyped-def]
        self, organization_id, order_id, *, amount_minor, currency, payer=None
    ):
        self.calls.append(
            {"org": organization_id, "order": order_id, "amount": amount_minor, "payer": payer}
        )
        if payer is None:
            raise PayerRequired("This payment provider needs the paying customer's phone number")
        collection = _collection(order_id)
        return {
            "collection": collection,
            "checkout": {
                "provider": "cashfree",
                "payment_session_id": "session_dev_only_0001",
                "mode": "sandbox",
                "provider_order_id": collection["id"],
                "amount_minor": amount_minor,
                "currency": currency,
                "openable": False,
            },
        }


@pytest.fixture
def cashfree(client) -> CashfreeCollections:  # type: ignore[no-untyped-def]
    fake = CashfreeCollections()
    app.dependency_overrides[get_collections_client] = lambda: fake
    return fake


async def _order(session, org_id, floor: Floor, *, phone: str | None = None):  # type: ignore[no-untyped-def]
    order = await accept_order(
        session,
        org_id,
        floor.shop,
        lines=[{"inventory_item_id": floor.shop.widget, "quantity": "2"}],
    )
    if phone is not None:
        async with session.begin():
            row = await order_selectors.get_order(session, org_id, order.id, for_update=True)
            assert row is not None
            row.customer_phone = phone
    return order


def _base(order_id: uuid.UUID) -> str:
    return f"/api/v1/operations/orders/{order_id}/payments"


class TestCheckout:
    async def test_the_phone_is_sent_only_after_payment_asks_and_the_session_is_cashfrees(
        self, client, org_id, session, floor: Floor, cashfree: CashfreeCollections
    ) -> None:
        order = await _order(session, org_id, floor)
        with (
            structlog.testing.capture_logs() as logs,
            as_principal(principal_for(org_id, [COLLECT])),
        ):
            response = await client.post(f"{_base(order.id)}/checkout", json={"phone": "1"})
        assert response.status_code == 200, response.text
        first, second = cashfree.calls
        assert first["payer"] is None  # never sent unless the provider needs it
        assert second["payer"] == {"name": "Asha Verma", "phone": PHONE}
        assert second["amount"] == order.total_minor  # still the order's own total
        checkout = response.json()["checkout"]
        assert checkout == {
            "provider": "cashfree",
            "payment_session_id": "session_dev_only_0001",
            "mode": "sandbox",
            "provider_order_id": checkout["provider_order_id"],
            "amount_minor": order.total_minor,
            "currency": "INR",
            "openable": False,
            "order_number": order.number,
        }
        assert "prefill" not in checkout and "key_id" not in checkout
        assert PHONE not in json.dumps(logs, default=str)
        async with session.begin():
            audits = (
                (await session.execute(select(AuditLog).where(AuditLog.entity_id == order.id)))
                .scalars()
                .all()
            )
        assert PHONE not in json.dumps([(a.before, a.after) for a in audits], default=str)

    @pytest.mark.parametrize("phone", ["", "   "])
    async def test_an_order_with_no_phone_is_phone_required_and_none_is_invented(
        self, client, org_id, session, floor: Floor, cashfree: CashfreeCollections, phone: str
    ) -> None:
        order = await _order(session, org_id, floor, phone=phone)
        with as_principal(principal_for(org_id, [COLLECT])):
            response = await client.post(f"{_base(order.id)}/checkout")
        assert response.status_code == 422
        assert response.json()["type"].endswith("/phone-required")
        assert [c["payer"] for c in cashfree.calls] == [None]  # asked once, nothing sent

    async def test_the_payments_view_names_the_provider_and_its_wire(
        self, client, org_id, session, floor: Floor, cashfree: CashfreeCollections
    ) -> None:
        order = await _order(session, org_id, floor)
        with as_principal(principal_for(org_id, [READ])):
            body = (await client.get(_base(order.id))).json()
        assert (body["provider"], body["provider_wire"], body["wire"]) == (
            "cashfree",
            "stub",
            "stub",
        )

    async def test_another_organizations_order_is_404_and_payment_is_never_asked(
        self, client, org_id, session, floor: Floor, cashfree: CashfreeCollections
    ) -> None:
        order = await _order(session, org_id, floor)
        with as_principal(principal_for(uuid.uuid4(), [COLLECT])):
            assert (await client.post(f"{_base(order.id)}/checkout")).status_code == 404
        assert cashfree.calls == []

    async def test_without_the_collect_permission_it_is_403_and_payment_is_never_asked(
        self, client, org_id, session, floor: Floor, cashfree: CashfreeCollections
    ) -> None:
        order = await _order(session, org_id, floor)
        with as_principal(principal_for(org_id, [READ])):
            assert (await client.post(f"{_base(order.id)}/checkout")).status_code == 403
        assert cashfree.calls == []


class TestPaymentClient:
    """payment's two Cashfree refusals are told apart by their problem `type`."""

    async def _call(self, monkeypatch: pytest.MonkeyPatch, kind: str) -> Exception:
        def handler(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            assert "payer" not in sent
            return httpx.Response(
                422,
                json={"type": f"https://errors.opsgrids.dev/{kind}", "detail": "payment said so"},
            )

        original = _REAL_ASYNC_CLIENT
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **k: original(*a, **{**k, "transport": httpx.MockTransport(handler)}),
        )
        client = HttpCollectionsClient("http://payment.test", settings(), timeout=1.0)
        try:
            await client.checkout(uuid.uuid4(), uuid.uuid4(), amount_minor=100, currency="INR")
        except Exception as exc:
            return exc
        raise AssertionError("no refusal")

    async def test_payer_required_and_phone_required_are_their_own_problems(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert isinstance(await self._call(monkeypatch, "payer-required"), PayerRequired)
        assert isinstance(await self._call(monkeypatch, "phone-required"), PhoneRequired)
        other = await self._call(monkeypatch, "validation")
        assert not isinstance(other, PayerRequired | PhoneRequired)

    async def test_the_payer_travels_in_the_body_only_when_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bodies: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"collection": {}, "checkout": None})

        original = _REAL_ASYNC_CLIENT
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **k: original(*a, **{**k, "transport": httpx.MockTransport(handler)}),
        )
        client = HttpCollectionsClient("http://payment.test", settings(), timeout=1.0)
        await client.checkout(uuid.uuid4(), uuid.uuid4(), amount_minor=100, currency="INR")
        await client.checkout(
            uuid.uuid4(), uuid.uuid4(), amount_minor=100, currency="INR", payer={"phone": PHONE}
        )
        assert "payer" not in bodies[0] and bodies[1]["payer"] == {"phone": PHONE}
