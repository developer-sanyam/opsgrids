"""Online payment for an order through a **redirect-checkout** provider (PhonePe), from the order's
side: the session payment returns (a URL to open, or a form to post) is handed to the browser as
it came, with the order number, the order's own total and no payer — PhonePe needs none.

payment's own process cannot be imported here, so the routes talk to a recording double of
`CollectionsClient`; payment's side is proven in
`services/payment/tests/test_merchant_phonepe_api.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.main import app
from app.orders.payment_client import get_collections_client
from tests.fulfilment_fixtures import Floor, accept_order, build_floor
from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

COLLECT = "operations.orders.payment.collect:ORG"
READ = "operations.orders.order.read:ORG"
URL = "https://mercury-uat.phonepe.example/transact/uat_v3?token=dev-only"


@pytest.fixture
async def floor(session, org_id) -> Floor:  # type: ignore[no-untyped-def]
    return await build_floor(session, org_id, stock=100)


class PhonePeCollections:
    """Answers as payment does for a PhonePe account: no payer asked for, a redirect session."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def list_for_order(self, organization_id, order_id):  # type: ignore[no-untyped-def]
        return {
            "merchant_account_configured": True,
            "mode": "test",
            "wire": "stub",
            "provider": "phonepe",
            "provider_wire": "live",
            "collections": [],
        }

    async def checkout(  # type: ignore[no-untyped-def]
        self, organization_id, order_id, *, amount_minor, currency, payer=None
    ):
        self.calls.append({"amount": amount_minor, "payer": payer})
        now = datetime.now(UTC).isoformat()
        collection = {
            "id": str(uuid.uuid4()),
            "reference_type": "operations.order",
            "reference_id": str(order_id),
            "state": "created",
            "amount_minor": amount_minor,
            "currency": currency,
            "provider": "phonepe",
            "provider_order_id": None,
            "provider_payment_id": None,
            "failure_reason": None,
            "refunded_minor": 0,
            "refunds": [],
            "history": [],
            "created_at": now,
            "updated_at": now,
        }
        return {
            "collection": collection,
            "checkout": {
                "provider": "phonepe",
                "method": "GET",
                "url": URL,
                "fields": {},
                "provider_order_id": collection["id"],
                "amount_minor": amount_minor,
                "currency": currency,
                "openable": True,
            },
        }


@pytest.fixture
def phonepe(client) -> PhonePeCollections:  # type: ignore[no-untyped-def]
    fake = PhonePeCollections()
    app.dependency_overrides[get_collections_client] = lambda: fake
    return fake


async def _order(session, org_id, floor: Floor):  # type: ignore[no-untyped-def]
    return await accept_order(
        session,
        org_id,
        floor.shop,
        lines=[{"inventory_item_id": floor.shop.widget, "quantity": "2"}],
    )


def _base(order_id: uuid.UUID) -> str:
    return f"/api/v1/operations/orders/{order_id}/payments"


async def test_the_redirect_session_reaches_the_browser_for_the_orders_own_total(
    client, org_id, session, floor: Floor, phonepe: PhonePeCollections
) -> None:
    order = await _order(session, org_id, floor)
    with as_principal(principal_for(org_id, [COLLECT])):
        response = await client.post(f"{_base(order.id)}/checkout")
    assert response.status_code == 200, response.text
    assert phonepe.calls == [{"amount": order.total_minor, "payer": None}]
    checkout = response.json()["checkout"]
    assert checkout == {
        "provider": "phonepe",
        "method": "GET",
        "url": URL,
        "fields": {},
        "provider_order_id": checkout["provider_order_id"],
        "amount_minor": order.total_minor,
        "currency": "INR",
        "openable": True,
        "order_number": order.number,
    }


async def test_another_organizations_order_is_404_and_payment_is_never_asked(
    client, org_id, session, floor: Floor, phonepe: PhonePeCollections
) -> None:
    order = await _order(session, org_id, floor)
    with as_principal(principal_for(uuid.uuid4(), [COLLECT])):
        assert (await client.post(f"{_base(order.id)}/checkout")).status_code == 404
    assert phonepe.calls == []


async def test_without_the_collect_permission_it_is_403_and_payment_is_never_asked(
    client, org_id, session, floor: Floor, phonepe: PhonePeCollections
) -> None:
    order = await _order(session, org_id, floor)
    with as_principal(principal_for(org_id, [READ])):
        assert (await client.post(f"{_base(order.id)}/checkout")).status_code == 403
    assert phonepe.calls == []
