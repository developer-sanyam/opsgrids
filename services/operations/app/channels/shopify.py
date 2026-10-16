"""The `ChannelClient` for `provider = "shopify"`: one merchant's own store, reached with the
Admin API access token of a custom app that merchant created (no public app listing).

Admin GraphQL API, version `2026-10` (the "Latest" row of shopify.dev/docs/api/usage/versioning on
2026-10-16). Bump `API_VERSION` deliberately and re-read the mutation notes: from 2026-04
`inventorySetQuantities` requires an `@idempotent` key, which `push_inventory` sends.

**The shop domain is tenant-typed**, so it is validated as `<handle>.myshopify.com` before it is
stored or used (`validate_shop_domain`) and every call goes through `EgressGuard.request`
(ADR-0016): https only, resolved once, refused if it resolves to a non-public address, pinned to
the vetted address, no redirects, bounded response.

The sealed credential holds the access token and, optionally, the app's webhook secret (the
custom app's API secret key, which signs `X-Shopify-Hmac-Sha256`), joined by a newline
(`pack_credential`). Neither is ever logged.

Customer PII (buyer name, phone) is carried into `ChannelOrder` exactly as the mock provider's
orders are; the shipping address is never read.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import httpx

from app.channels.base import (
    ChannelAuthError,
    ChannelFulfilmentUpdate,
    ChannelHealth,
    ChannelInventoryUpdate,
    ChannelOrder,
    ChannelOrderLine,
    ChannelOrderPage,
    ChannelRejected,
    ChannelSyncAck,
    ChannelUnavailable,
)
from platform_core.egress import EgressBlockedError, EgressGuard
from platform_core.logging import get_logger

log = get_logger("operations.channels.shopify")

API_VERSION: Final = "2026-10"
_SHOP_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,58}[a-z0-9]\.myshopify\.com$")
_PAGE_SIZE: Final = 50
_INVENTORY_CHUNK: Final = 100
_MAX_RESPONSE_BYTES: Final = 2_000_000
_ZERO_DECIMAL = frozenset({"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "PYG", "RWF"})
_THREE_DECIMAL = frozenset({"KWD", "BHD", "OMR", "JOD", "TND"})


def validate_shop_domain(value: str) -> str:
    """The normalised `<handle>.myshopify.com`, or `ValueError`. Accepts a pasted URL's scheme and
    trailing slash; refuses everything else (custom domains, ports, paths, userinfo)."""
    domain = re.sub(r"^https://", "", value.strip().lower()).rstrip("/")
    if not _SHOP_DOMAIN.fullmatch(domain):
        raise ValueError("The shop domain must look like your-store.myshopify.com")
    return domain


def pack_credential(access_token: str, webhook_secret: str | None) -> str:
    return f"{access_token}\n{webhook_secret}" if webhook_secret else access_token


def unpack_credential(credential: str) -> tuple[str, str | None]:
    token, _, secret = credential.partition("\n")
    return token, (secret or None)


def _minor(amount: str, currency: str) -> int:
    exponent = 0 if currency in _ZERO_DECIMAL else 3 if currency in _THREE_DECIMAL else 2
    return int((Decimal(amount) * 10**exponent).to_integral_value())


_ORDERS_QUERY = """
query Orders($first: Int!, $query: String) {
  orders(first: $first, query: $query, sortKey: CREATED_AT) {
    nodes {
      legacyResourceId createdAt currencyCode phone
      shippingAddress { name phone }
      customer { displayName phone }
      lineItems(first: 100) {
        nodes { sku quantity discountedUnitPriceSet { shopMoney { amount } } }
      }
    }
  }
}"""

_SKU_QUERY = """
query Skus($query: String!) {
  locations(first: 1, includeInactive: false) { nodes { id } }
  productVariants(first: 250, query: $query) { nodes { sku inventoryItem { id } } }
}"""

_SET_QUANTITIES = """
mutation SetQuantities($input: InventorySetQuantitiesInput!, $key: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $key) {
    userErrors { field message }
  }
}"""

_FULFILMENT_ORDERS = """
query FulfilmentOrders($id: ID!) {
  order(id: $id) { fulfillmentOrders(first: 20) { nodes { id status } } }
}"""

_FULFIL = """
mutation Fulfil($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) { userErrors { field message } }
}"""


class ShopifyClient:
    """Satisfies `app.channels.base.ChannelClient`. Built per call by `build_client`; a test hands
    in its own `guard` (stub resolver + `httpx.MockTransport`)."""

    name = "shopify"

    def __init__(
        self, *, shop_domain: str, access_token: str, guard: EgressGuard | None = None
    ) -> None:
        self._domain = validate_shop_domain(shop_domain)
        self._headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }
        self._guard = guard or EgressGuard()

    # ------------------------------------------------------------------ the wire call, guarded

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        url = f"https://{self._domain}/admin/api/{API_VERSION}/graphql.json"
        try:
            response = await self._guard.request(
                "POST",
                url,
                headers=self._headers,
                content=json.dumps({"query": query, "variables": variables}).encode(),
            )
        except EgressBlockedError as exc:
            log.warning("shopify.egress_blocked", shop=self._domain)
            raise ChannelUnavailable("shopify could not be reached safely") from exc
        except httpx.HTTPError as exc:
            raise ChannelUnavailable(f"shopify could not be reached: {type(exc).__name__}") from exc

        status = response.status_code
        if status in (401, 403):
            raise ChannelAuthError("shopify rejected this connection's access token")
        if status == 429:
            wait = response.headers.get("retry-after", "")
            raise ChannelUnavailable(f"shopify rate-limited this request (429) retry-after={wait}")
        if status >= 500:
            raise ChannelUnavailable(f"shopify answered {status}")
        if status >= 400:
            raise ChannelRejected("provider_rejected", f"HTTP {status}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ChannelUnavailable("shopify returned a body this client could not parse") from exc
        if not isinstance(body, dict):
            raise ChannelUnavailable("shopify returned an unexpected shape")
        errors = body.get("errors")
        if errors:
            codes = {
                str((e.get("extensions") or {}).get("code", ""))
                for e in errors
                if isinstance(e, dict)
            }
            if "THROTTLED" in codes:
                raise ChannelUnavailable("shopify rate-limited this request (THROTTLED)")
            if "ACCESS_DENIED" in codes:
                raise ChannelAuthError("shopify access token lacks a required scope")
            raise ChannelRejected("provider_rejected", "the GraphQL request was refused")
        data = body.get("data")
        if not isinstance(data, dict):
            raise ChannelUnavailable("shopify returned no data")
        return data

    @staticmethod
    def _check_user_errors(payload: object) -> None:
        errors = payload.get("userErrors") if isinstance(payload, dict) else None
        if errors:
            message = "; ".join(str(e.get("message", "")) for e in errors if isinstance(e, dict))
            raise ChannelRejected("provider_rejected", message[:300] or "the shop refused this")

    # ------------------------------------------------------------------ ChannelClient

    async def check_health(self) -> ChannelHealth:
        data = await self._graphql("query { shop { name } }", {})
        shop = data.get("shop")
        return ChannelHealth(healthy=isinstance(shop, dict) and bool(shop.get("name")))

    async def list_new_orders(self, *, cursor: str | None) -> ChannelOrderPage:
        # The cursor is the last order's `createdAt`; `>=` re-lists it, which is harmless because
        # import is idempotent on the order id. shortcut: more than _PAGE_SIZE orders sharing one
        # second would stall, upgrade to GraphQL `after` cursors if a shop ever does.
        since = _parse_ts(cursor) if cursor else None
        query = f"created_at:>='{since}'" if since else None
        data = await self._graphql(_ORDERS_QUERY, {"first": _PAGE_SIZE, "query": query})
        nodes = (data.get("orders") or {}).get("nodes") or []
        orders = [parse_order(n) for n in nodes if isinstance(n, dict)]
        last = nodes[-1].get("createdAt") if nodes else None
        return ChannelOrderPage(orders=orders, next_cursor=str(last) if last else None)

    async def push_inventory(self, updates: list[ChannelInventoryUpdate]) -> ChannelSyncAck:
        for start in range(0, len(updates), _INVENTORY_CHUNK):
            await self._push_inventory_chunk(updates[start : start + _INVENTORY_CHUNK])
        return ChannelSyncAck(accepted=True)

    async def _push_inventory_chunk(self, chunk: list[ChannelInventoryUpdate]) -> None:
        # One lookup for the whole chunk (never a query per SKU).
        skus = [u.external_sku for u in chunk]
        found = await self._graphql(
            _SKU_QUERY, {"query": " OR ".join(f"sku:'{_escape(s)}'" for s in skus)}
        )
        locations = (found.get("locations") or {}).get("nodes") or []
        if not locations:
            raise ChannelRejected("no_location", "the shop has no active location")
        by_sku = {
            n["sku"]: n["inventoryItem"]["id"]
            for n in (found.get("productVariants") or {}).get("nodes") or []
            if n.get("sku") and n.get("inventoryItem")
        }
        missing = sorted(set(skus) - by_sku.keys())
        if missing:
            raise ChannelRejected("unknown_sku", f"the shop has no variant with SKU {missing[0]}")
        quantities = [
            {
                "inventoryItemId": by_sku[u.external_sku],
                "locationId": locations[0]["id"],
                "quantity": int(u.available_qty),
            }
            for u in chunk
        ]
        # A deterministic key: a retry of the identical push is the same request to Shopify.
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(quantities, sort_keys=True)))
        input_ = {
            "name": "available",
            "reason": "correction",
            "ignoreCompareQuantity": True,
            "quantities": quantities,
        }
        data = await self._graphql(_SET_QUANTITIES, {"input": input_, "key": key})
        self._check_user_errors(data.get("inventorySetQuantities"))

    async def push_fulfilment_update(self, update: ChannelFulfilmentUpdate) -> ChannelSyncAck:
        if update.status != "dispatched":
            return ChannelSyncAck(accepted=True, detail=f"{update.status} needs no Shopify call")
        order_gid = f"gid://shopify/Order/{update.external_order_id}"
        data = await self._graphql(_FULFILMENT_ORDERS, {"id": order_gid})
        nodes = ((data.get("order") or {}).get("fulfillmentOrders") or {}).get("nodes") or []
        open_ids = [n["id"] for n in nodes if n.get("status") in ("OPEN", "IN_PROGRESS")]
        if not open_ids:
            return ChannelSyncAck(accepted=True, detail="nothing left to fulfil")
        tracking: dict[str, str] = {}
        if update.tracking_number:
            tracking["number"] = update.tracking_number
        if update.carrier:
            tracking["company"] = update.carrier
        fulfillment: dict[str, Any] = {
            "notifyCustomer": False,
            "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": i} for i in open_ids],
        }
        if tracking:
            fulfillment["trackingInfo"] = tracking
        result = await self._graphql(_FULFIL, {"fulfillment": fulfillment})
        self._check_user_errors(result.get("fulfillmentCreate"))
        return ChannelSyncAck(accepted=True)


def _escape(sku: str) -> str:
    return sku.replace("\\", "\\\\").replace("'", "\\'")


def _parse_ts(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ChannelRejected("bad_cursor", "the stored order cursor is not a timestamp") from None
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_order(node: dict[str, Any]) -> ChannelOrder:
    """One Admin API order node as the neutral `ChannelOrder`. `raw` is the neutral shape in the
    mock provider's key names -- not Shopify's payload -- so a retried import
    (`retry_sync_log`) rebuilds the order from it and no address or email is ever stored."""
    currency = str(node.get("currencyCode") or "INR")
    ship = node.get("shippingAddress") or {}
    customer = node.get("customer") or {}
    lines = [
        ChannelOrderLine(
            external_sku=str(li.get("sku") or ""),
            quantity=Decimal(str(li.get("quantity", 0))),
            unit_price_minor=_minor(
                str(
                    ((li.get("discountedUnitPriceSet") or {}).get("shopMoney") or {}).get(
                        "amount", "0"
                    )
                ),
                currency,
            ),
        )
        for li in (node.get("lineItems") or {}).get("nodes") or []
    ]
    placed_at = datetime.fromisoformat(str(node["createdAt"]).replace("Z", "+00:00"))
    name = str(ship.get("name") or customer.get("displayName") or "")
    phone = str(node.get("phone") or ship.get("phone") or customer.get("phone") or "")
    external_id = str(node["legacyResourceId"])
    raw: dict[str, object] = {
        "external_order_id": external_id,
        "placed_at": placed_at.isoformat(),
        "currency": currency,
        "customer_name": name,
        "customer_phone": phone,
        "lines": [
            {
                "sku": line.external_sku,
                "quantity": str(line.quantity),
                "unit_price_minor": line.unit_price_minor,
            }
            for line in lines
        ],
    }
    return ChannelOrder(
        external_order_id=external_id,
        placed_at=placed_at,
        currency=currency,
        customer_name=name,
        customer_phone=phone,
        lines=lines,
        raw=raw,
    )
