"""The Shopify channel: request building against the Admin GraphQL docs' shapes, order mapping,
rate-limit classification, the shop-domain and EgressGuard gates, the signed `orders/create`
webhook (fails closed), authorization, cross-tenant isolation and idempotent import.

No network: the guard's resolver and transport are stubs, so the pinning wrapper still runs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.channels import selectors as channel_selectors
from app.channels import services as channel_services
from app.channels.base import (
    ChannelAuthError,
    ChannelFulfilmentUpdate,
    ChannelInventoryUpdate,
    ChannelRejected,
    ChannelUnavailable,
)
from app.channels.shopify import (
    API_VERSION,
    ShopifyClient,
    pack_credential,
    parse_order,
    validate_shop_domain,
)
from app.models import Channel, Order
from platform_core.auth import Scope
from platform_core.egress import EgressGuard
from tests.channel_fixtures import ALL_CHANNEL_PERMS, CHANNEL_READ_PERM, map_sku
from tests.helpers import as_principal, ctx_for, principal_for
from tests.order_fixtures import Shop, build_shop
from tests.test_integrations_framework import engines as _engines
from tests.test_integrations_framework import seed_channel, stored_events

pytestmark = pytest.mark.integration

engines = _engines  # the shared fixture, re-exported so pytest finds it here

DOMAIN = "demo-store.myshopify.com"
TOKEN = "shpat_test_token_0001"  # pragma: allowlist secret
WEBHOOK_SECRET = "shpss_test_webhook_secret"  # pragma: allowlist secret

# An order node in the shape `ORDERS_QUERY` selects (docs: Order, LineItem, MoneyBag).
ORDER_NODE: dict[str, Any] = {
    "legacyResourceId": "450789469",
    "createdAt": "2026-10-16T05:12:33Z",
    "currencyCode": "INR",
    "phone": None,
    "shippingAddress": {"name": "Asha Verma", "phone": "+919876543210"},
    "customer": {"displayName": "Asha V", "phone": None},
    "lineItems": {
        "nodes": [
            {
                "sku": "EXT-WIDGET",
                "quantity": 3,
                "discountedUnitPriceSet": {"shopMoney": {"amount": "450.50"}},
            }
        ]
    },
}


def guarded(handler: Any, resolved: str = "23.227.38.65") -> EgressGuard:
    async def resolver(host: str) -> list[str]:
        return [resolved]

    return EgressGuard(resolver=resolver, inner_transport=httpx.MockTransport(handler))


def client_with(handler: Any, **kw: Any) -> ShopifyClient:
    return ShopifyClient(shop_domain=DOMAIN, access_token=TOKEN, guard=guarded(handler, **kw))


def graphql_reply(data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


class Recorder:
    """A transport handler that records requests and answers from a queue."""

    def __init__(self, *replies: httpx.Response) -> None:
        self.requests: list[httpx.Request] = []
        self._replies = list(replies)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._replies.pop(0)

    def body(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


# --------------------------------------------------------------------- domain and egress


class TestShopDomain:
    @pytest.mark.parametrize("value", [DOMAIN, f"https://{DOMAIN}/", "  DEMO-STORE.MyShopify.com "])
    def test_a_myshopify_domain_is_normalised(self, value: str) -> None:
        assert validate_shop_domain(value) == DOMAIN

    @pytest.mark.parametrize(
        "value",
        [
            "evil.example.com",
            "myshopify.com",
            f"{DOMAIN}.evil.com",
            f"evil.com/{DOMAIN}",
            f"{DOMAIN}:8443",
            f"user@{DOMAIN}",
            "169.254.169.254",
            "http://demo-store.myshopify.com.attacker.io",
        ],
    )
    def test_anything_else_is_refused(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_shop_domain(value)

    def test_the_client_refuses_a_non_myshopify_domain(self) -> None:
        with pytest.raises(ValueError):
            ShopifyClient(shop_domain="evil.example.com", access_token=TOKEN)

    async def test_the_egress_guard_blocks_a_domain_that_resolves_to_private_space(self) -> None:
        recorder = Recorder(graphql_reply({"shop": {"name": "x"}}))
        client = client_with(recorder, resolved="10.0.0.5")
        with pytest.raises(ChannelUnavailable):
            await client.check_health()
        assert recorder.requests == []  # nothing was sent anywhere

    async def test_a_redirect_is_refused_not_followed(self) -> None:
        recorder = Recorder(httpx.Response(302, headers={"location": "http://169.254.169.254/"}))
        with pytest.raises(ChannelUnavailable):
            await client_with(recorder).check_health()

    async def test_connect_rejects_a_non_myshopify_domain_over_http(
        self, client, org_id, session
    ) -> None:
        shop = await build_shop(session, org_id, stock=10)
        with as_principal(principal_for(org_id, ALL_CHANNEL_PERMS)):
            response = await client.post(
                "/api/v1/operations/channels",
                json={
                    "provider": "shopify",
                    "name": "Evil",
                    "external_shop_id": "internal.corp.example.com",
                    "api_key": TOKEN,
                    "default_warehouse_id": str(shop.warehouse_id),
                },
            )
        assert response.status_code == 422, response.text


# --------------------------------------------------------------------- request building


class TestRequests:
    async def test_the_health_query_hits_the_versioned_endpoint_with_the_token(self) -> None:
        recorder = Recorder(graphql_reply({"shop": {"name": "Demo"}}))
        health = await client_with(recorder).check_health()
        assert health.healthy is True
        request = recorder.requests[0]
        assert request.method == "POST"
        assert request.url.path == f"/admin/api/{API_VERSION}/graphql.json"
        assert request.headers["x-shopify-access-token"] == TOKEN
        assert request.headers["host"] == DOMAIN  # the pin changes the socket, not the Host
        assert recorder.body()["query"] == "query { shop { name } }"

    async def test_orders_are_listed_from_the_cursor_oldest_first(self) -> None:
        recorder = Recorder(graphql_reply({"orders": {"nodes": [ORDER_NODE]}}))
        page = await client_with(recorder).list_new_orders(cursor="2026-10-16T05:00:00Z")
        variables = recorder.body()["variables"]
        assert variables == {"first": 50, "query": "created_at:>='2026-10-16T05:00:00Z'"}
        assert "sortKey: CREATED_AT" in recorder.body()["query"]
        assert [o.external_order_id for o in page.orders] == ["450789469"]
        assert page.next_cursor == "2026-10-16T05:12:33Z"

    async def test_a_garbage_cursor_is_never_interpolated(self) -> None:
        recorder = Recorder()
        with pytest.raises(ChannelRejected):
            await client_with(recorder).list_new_orders(cursor="x' OR 1=1 --")
        assert recorder.requests == []

    async def test_an_empty_page_keeps_the_cursor(self) -> None:
        recorder = Recorder(graphql_reply({"orders": {"nodes": []}}))
        page = await client_with(recorder).list_new_orders(cursor=None)
        assert page.orders == [] and page.next_cursor is None
        assert recorder.body()["variables"]["query"] is None

    async def test_inventory_is_set_with_one_lookup_and_an_idempotency_key(self) -> None:
        recorder = Recorder(
            graphql_reply(
                {
                    "locations": {"nodes": [{"id": "gid://shopify/Location/1"}]},
                    "productVariants": {
                        "nodes": [
                            {"sku": "A", "inventoryItem": {"id": "gid://shopify/InventoryItem/10"}},
                            {"sku": "B", "inventoryItem": {"id": "gid://shopify/InventoryItem/11"}},
                        ]
                    },
                }
            ),
            graphql_reply({"inventorySetQuantities": {"userErrors": []}}),
        )
        updates = [
            ChannelInventoryUpdate(external_sku="A", available_qty=Decimal("5")),
            ChannelInventoryUpdate(external_sku="B", available_qty=Decimal("0")),
        ]
        ack = await client_with(recorder).push_inventory(updates)
        assert ack.accepted and len(recorder.requests) == 2  # lookup once, then one mutation
        assert recorder.body(0)["variables"]["query"] == "sku:'A' OR sku:'B'"
        mutation = recorder.body(1)
        assert "@idempotent(key: $key)" in mutation["query"]
        assert mutation["variables"]["input"] == {
            "name": "available",
            "reason": "correction",
            "ignoreCompareQuantity": True,
            "quantities": [
                {
                    "inventoryItemId": "gid://shopify/InventoryItem/10",
                    "locationId": "gid://shopify/Location/1",
                    "quantity": 5,
                },
                {
                    "inventoryItemId": "gid://shopify/InventoryItem/11",
                    "locationId": "gid://shopify/Location/1",
                    "quantity": 0,
                },
            ],
        }
        uuid.UUID(mutation["variables"]["key"])

    async def test_an_unknown_sku_is_a_rejection_and_nothing_is_written(self) -> None:
        recorder = Recorder(
            graphql_reply(
                {
                    "locations": {"nodes": [{"id": "gid://shopify/Location/1"}]},
                    "productVariants": {"nodes": []},
                }
            )
        )
        with pytest.raises(ChannelRejected) as raised:
            await client_with(recorder).push_inventory(
                [ChannelInventoryUpdate(external_sku="NOPE", available_qty=Decimal("1"))]
            )
        assert raised.value.code == "unknown_sku" and len(recorder.requests) == 1

    async def test_a_dispatched_shipment_creates_a_fulfilment_with_tracking(self) -> None:
        recorder = Recorder(
            graphql_reply(
                {
                    "order": {
                        "fulfillmentOrders": {
                            "nodes": [
                                {"id": "gid://shopify/FulfillmentOrder/7", "status": "OPEN"},
                                {"id": "gid://shopify/FulfillmentOrder/8", "status": "CLOSED"},
                            ]
                        }
                    }
                }
            ),
            graphql_reply({"fulfillmentCreate": {"userErrors": []}}),
        )
        update = ChannelFulfilmentUpdate(
            external_order_id="450789469",
            status="dispatched",
            tracking_number="AWB123",
            carrier="Delhivery",
            occurred_at=datetime.now(UTC),
        )
        ack = await client_with(recorder).push_fulfilment_update(update)
        assert ack.accepted
        assert recorder.body(0)["variables"] == {"id": "gid://shopify/Order/450789469"}
        assert recorder.body(1)["variables"]["fulfillment"] == {
            "notifyCustomer": False,
            "lineItemsByFulfillmentOrder": [
                {"fulfillmentOrderId": "gid://shopify/FulfillmentOrder/7"}
            ],
            "trackingInfo": {"number": "AWB123", "company": "Delhivery"},
        }

    async def test_a_delivered_update_makes_no_call(self) -> None:
        recorder = Recorder()
        update = ChannelFulfilmentUpdate(
            external_order_id="1",
            status="delivered",
            tracking_number=None,
            carrier=None,
            occurred_at=datetime.now(UTC),
        )
        assert (await client_with(recorder).push_fulfilment_update(update)).accepted
        assert recorder.requests == []

    async def test_user_errors_are_a_rejection(self) -> None:
        recorder = Recorder(
            graphql_reply(
                {"order": {"fulfillmentOrders": {"nodes": [{"id": "gid://x/1", "status": "OPEN"}]}}}
            ),
            graphql_reply(
                {"fulfillmentCreate": {"userErrors": [{"field": ["x"], "message": "bad"}]}}
            ),
        )
        update = ChannelFulfilmentUpdate(
            external_order_id="1",
            status="dispatched",
            tracking_number="T",
            carrier="C",
            occurred_at=datetime.now(UTC),
        )
        with pytest.raises(ChannelRejected):
            await client_with(recorder).push_fulfilment_update(update)


class TestFailureMapping:
    async def test_a_429_is_unavailable_so_the_sweep_backs_off(self) -> None:
        recorder = Recorder(httpx.Response(429, headers={"retry-after": "2.0"}))
        with pytest.raises(ChannelUnavailable, match="429"):
            await client_with(recorder).check_health()

    async def test_a_graphql_throttle_is_unavailable(self) -> None:
        reply = httpx.Response(200, json={"errors": [{"extensions": {"code": "THROTTLED"}}]})
        with pytest.raises(ChannelUnavailable, match="THROTTLED"):
            await client_with(Recorder(reply)).check_health()

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_bad_token_is_an_auth_error(self, status: int) -> None:
        with pytest.raises(ChannelAuthError):
            await client_with(Recorder(httpx.Response(status))).check_health()

    async def test_a_5xx_is_unavailable(self) -> None:
        with pytest.raises(ChannelUnavailable):
            await client_with(Recorder(httpx.Response(503))).check_health()


# --------------------------------------------------------------------- order mapping


class TestOrderMapping:
    def test_an_order_maps_to_the_neutral_shape_in_minor_units(self) -> None:
        order = parse_order(ORDER_NODE)
        assert order.external_order_id == "450789469"
        assert order.currency == "INR"
        assert order.placed_at == datetime(2026, 10, 16, 5, 12, 33, tzinfo=UTC)
        assert order.customer_name == "Asha Verma"
        assert order.customer_phone == "+919876543210"
        (line,) = order.lines
        assert (line.external_sku, line.quantity, line.unit_price_minor) == (
            "EXT-WIDGET",
            Decimal("3"),
            45050,
        )

    def test_currency_exponents_are_respected(self) -> None:
        node = {**ORDER_NODE, "currencyCode": "JPY"}
        node["lineItems"] = {
            "nodes": [
                {
                    "sku": "A",
                    "quantity": 1,
                    "discountedUnitPriceSet": {"shopMoney": {"amount": "1200"}},
                }
            ]
        }
        assert parse_order(node).lines[0].unit_price_minor == 1200

    def test_the_stored_raw_payload_carries_no_address(self) -> None:
        raw = json.dumps(parse_order(ORDER_NODE).raw)
        assert "shippingAddress" not in raw and "address" not in raw
        assert "EXT-WIDGET" in raw


# --------------------------------------------------------------------- webhook


def hmac_header(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


async def post_shopify(
    client: Any,
    channel_id: uuid.UUID,
    body: bytes,
    *,
    signature: str | None = None,
    webhook_id: str = "b54557e4-bdd9-4b37-8a5f-bf7d70bcd043",
    topic: str = "orders/create",
) -> Any:
    headers = {
        "content-type": "application/json",
        "x-shopify-topic": topic,
        "x-shopify-webhook-id": webhook_id,
    }
    if signature != "":
        headers["x-shopify-hmac-sha256"] = hmac_header(body) if signature is None else signature
    return await client.post(
        f"/api/v1/webhooks/operations/shopify?connection={channel_id}",
        content=body,
        headers=headers,
    )


ORDER_BODY = json.dumps(
    {"id": 450789469, "name": "#1001", "email": "asha@example.com", "phone": "+919876543210"}
).encode()


class TestWebhook:
    async def test_a_signed_delivery_is_recorded_once_and_minimised(
        self, engines, client, org_id, admin_session
    ) -> None:
        channel_id = await seed_channel(
            admin_session,
            org_id,
            provider="shopify",
            api_key=pack_credential(TOKEN, WEBHOOK_SECRET),
        )
        first = await post_shopify(client, channel_id, ORDER_BODY)
        again = await post_shopify(client, channel_id, ORDER_BODY)
        assert first.status_code == 200, first.text
        assert first.json()["duplicate"] is False and again.json()["duplicate"] is True
        (row,) = await stored_events(admin_session)
        assert row["event_name"] == "orders/create" and row["outcome"] == "received"
        assert row["payload"] == {"id": 450789469, "name": "#1001"}
        assert "asha" not in str(row) and "9876543210" not in str(row)

    @pytest.mark.parametrize("signature", ["", "AAAA", "not base64 !!"])
    async def test_a_bad_or_missing_signature_fails_closed(
        self, engines, client, org_id, admin_session, signature
    ) -> None:
        channel_id = await seed_channel(
            admin_session,
            org_id,
            provider="shopify",
            api_key=pack_credential(TOKEN, WEBHOOK_SECRET),
        )
        response = await post_shopify(client, channel_id, ORDER_BODY, signature=signature)
        assert response.status_code == 400
        assert await stored_events(admin_session) == []

    async def test_a_tampered_body_fails_closed(
        self, engines, client, org_id, admin_session
    ) -> None:
        channel_id = await seed_channel(
            admin_session,
            org_id,
            provider="shopify",
            api_key=pack_credential(TOKEN, WEBHOOK_SECRET),
        )
        response = await post_shopify(
            client, channel_id, ORDER_BODY + b" ", signature=hmac_header(ORDER_BODY)
        )
        assert response.status_code == 400 and await stored_events(admin_session) == []

    async def test_a_connection_without_a_webhook_secret_accepts_nothing(
        self, engines, client, org_id, admin_session
    ) -> None:
        channel_id = await seed_channel(
            admin_session, org_id, provider="shopify", api_key=pack_credential(TOKEN, None)
        )
        # Signed with the access token, the only secret it holds: still refused.
        response = await post_shopify(
            client, channel_id, ORDER_BODY, signature=hmac_header(ORDER_BODY, TOKEN)
        )
        assert response.status_code == 400 and await stored_events(admin_session) == []

    async def test_another_connections_secret_cannot_authenticate_this_one(
        self, engines, client, org_id, admin_session
    ) -> None:
        """Cross-tenant: organization B's secret does not verify a body for A's connection."""
        channel_a = await seed_channel(
            admin_session,
            org_id,
            provider="shopify",
            api_key=pack_credential(TOKEN, WEBHOOK_SECRET),
        )
        await seed_channel(
            admin_session,
            uuid.uuid4(),
            provider="shopify",
            api_key=pack_credential(TOKEN, "shpss_other_orgs_secret"),  # pragma: allowlist secret
        )
        response = await post_shopify(
            client,
            channel_a,
            ORDER_BODY,
            signature=hmac_header(ORDER_BODY, "shpss_other_orgs_secret"),
        )
        assert response.status_code == 400 and await stored_events(admin_session) == []


# --------------------------------------------------------------------- authz, tenancy, import


class TestConnectAndImport:
    async def test_a_reader_cannot_connect_a_shopify_store(self, client, org_id, session) -> None:
        shop = await build_shop(session, org_id, stock=10)
        with as_principal(principal_for(org_id, [CHANNEL_READ_PERM])):
            response = await client.post(
                "/api/v1/operations/channels",
                json={
                    "provider": "shopify",
                    "name": "S",
                    "external_shop_id": DOMAIN,
                    "api_key": TOKEN,
                    "default_warehouse_id": str(shop.warehouse_id),
                },
            )
        assert response.status_code == 403

    async def test_connect_seals_both_secrets_and_never_returns_them(
        self, client, org_id, session
    ) -> None:
        shop = await build_shop(session, org_id, stock=10)
        with as_principal(principal_for(org_id, ALL_CHANNEL_PERMS)):
            response = await client.post(
                "/api/v1/operations/channels",
                json={
                    "provider": "shopify",
                    "name": "S",
                    "external_shop_id": f"https://{DOMAIN}/",
                    "api_key": TOKEN,
                    "webhook_secret": WEBHOOK_SECRET,
                    "default_warehouse_id": str(shop.warehouse_id),
                },
            )
        assert response.status_code == 201, response.text
        assert TOKEN not in response.text and WEBHOOK_SECRET not in response.text
        assert response.json()["external_shop_id"] == DOMAIN
        async with session.begin():
            row = (
                await session.execute(
                    select(Channel).where(Channel.id == uuid.UUID(response.json()["id"]))
                )
            ).scalar_one()
            assert row.api_base_url == f"https://{DOMAIN}"
            credential = await channel_services.open_channel_credential(row)
        assert credential == pack_credential(TOKEN, WEBHOOK_SECRET)

    async def test_a_polled_order_imports_once_however_often_it_is_listed(
        self, session, org_id
    ) -> None:
        shop: Shop = await build_shop(session, org_id, stock=200)
        ctx = ctx_for(org_id, "operations.channels.channel.write", Scope.ORG)
        async with session.begin():
            channel = await channel_services.connect_channel(
                session,
                ctx,
                provider="shopify",
                name="S",
                external_shop_id=DOMAIN,
                api_key=TOKEN,
                default_warehouse_id=shop.warehouse_id,
                default_currency="INR",
                auto_accept_orders=True,
            )
        await map_sku(
            session, ctx, channel, external_sku="EXT-WIDGET", inventory_item_id=shop.widget
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return graphql_reply({"orders": {"nodes": [ORDER_NODE]}})

        client = client_with(handler)
        for _ in range(3):  # the `>=` cursor re-lists the last order every poll
            summary = await channel_services.sync_orders_for_channel(
                session, ctx, channel.id, client
            )
            assert summary.attempted == 1 and summary.rejected == 0

        async with session.begin():
            orders = (
                (
                    await session.execute(
                        select(Order).where(
                            Order.organization_id == org_id, Order.channel == "marketplace"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(orders) == 1
        async with session.begin():
            stored = await channel_selectors.get_channel(session, org_id, channel.id)
        assert stored is not None and stored.last_order_cursor == "2026-10-16T05:12:33Z"
