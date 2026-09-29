"""The `ChannelClient` for `provider = "mock_marketplace"` — real HTTP calls to the sandbox
marketplace (`app.channels.mock_marketplace`), the client half of the pair described there.

The base URL is platform configuration (`Settings.mock_marketplace_api_base`), not a value a
tenant supplies — the same "fixed, operator-configured base... does not route through
`platform_core.egress`'s SSRF guard" reasoning `services/notification/app/whatsapp_provider.py`'s
module docstring gives for the Graph API base, copied here rather than re-argued. **A real
provider that lets a tenant name their own shop domain does not get to make the same claim** —
see `Channel.api_base_url`'s own docstring for what that would need (`platform_core.egress
.EgressGuard`, ADR-0016) — and is exactly why that column exists even though this adapter never
reads it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Final

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
from platform_core.logging import get_logger

log = get_logger("operations.channels.mock_provider")

#: Small JSON responses only — the same bound `whatsapp_provider.py` sets on a provider response,
#: for the same reason: a provider that somehow answered with megabytes must not be read fully
#: into memory over this call.
_MAX_RESPONSE_BYTES: Final = 2_000_000


class MockMarketplaceClient:
    """Satisfies `app.channels.base.ChannelClient` for the sandbox provider. Owns one
    `httpx.AsyncClient` per instance (`app.channels.services.build_client` constructs one per
    call, mirroring `EwayClient.build_client`'s "no long-lived state" shape); a test may hand in
    its own `httpx.AsyncClient(transport=ASGITransport(app=mock_marketplace.app))` to run this
    entirely in-process, with no socket opened at all.
    """

    name = "mock_marketplace"

    def __init__(
        self,
        *,
        base_url: str,
        shop_id: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._shop_id = shop_id
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )
        #: A hard wall-clock deadline enforced independently of `self._client`'s own transport
        #: (`asyncio.wait_for`, below) — `httpx.AsyncClient(timeout=...)` only bounds a real
        #: network transport's connect/read/write phases; `httpx.ASGITransport` (what
        #: `tests.channel_fixtures.wire_mock_provider` wires this client to) awaits the ASGI app
        #: directly and honours no timeout of its own, so a provider that hangs — the sandbox's
        #: own `"timeout"` chaos mode, or a real one wedged mid-response — would otherwise block
        #: forever regardless of `timeout_seconds`.
        self._timeout_seconds = timeout_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> MockMarketplaceClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ the wire call, guarded

    async def _call(
        self, method: str, path: str, *, params: dict[str, str] | None = None, json: object = None
    ) -> dict[str, object]:
        try:
            response = await asyncio.wait_for(
                self._client.request(
                    method,
                    f"/shops/{self._shop_id}{path}",
                    params=params,
                    json=json,
                    headers=self._headers,
                ),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ChannelUnavailable(f"{self.name} timed out") from exc
        except httpx.HTTPError as exc:
            raise ChannelUnavailable(
                f"{self.name} could not be reached: {type(exc).__name__}"
            ) from exc

        if response.status_code == 401:
            raise ChannelAuthError(f"{self.name} rejected this connection's credential")
        if response.status_code == 429:
            # Rate-limited, not refused: the request's *contents* were never judged, so this is
            # `ChannelUnavailable` (retryable with the identical request) — the same bucket a 5xx
            # falls into below — never `ChannelRejected`, which would tell a caller (and
            # `app.channels.services._record_outcome`'s health accounting) that retrying
            # unchanged will fail again, which is the opposite of true here.
            raise ChannelUnavailable(f"{self.name} rate-limited this request (429)")
        if response.status_code == 422:
            detail = _detail(response)
            raise ChannelRejected(
                "provider_rejected", detail or "the provider refused this request"
            )
        if response.status_code >= 500:
            raise ChannelUnavailable(f"{self.name} answered {response.status_code}")
        if response.status_code >= 400:
            detail = _detail(response)
            raise ChannelRejected("provider_rejected", detail or f"HTTP {response.status_code}")
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise ChannelUnavailable(f"{self.name}'s response exceeded the size this client reads")
        try:
            body = response.json()
        except ValueError as exc:
            raise ChannelUnavailable(
                f"{self.name} returned a body this client could not parse"
            ) from exc
        if not isinstance(body, dict):
            raise ChannelUnavailable(f"{self.name} returned an unexpected shape")
        return body

    # ------------------------------------------------------------------ ChannelClient

    async def check_health(self) -> ChannelHealth:
        body = await self._call("GET", "/health")
        return ChannelHealth(healthy=bool(body.get("ok")))

    async def list_new_orders(self, *, cursor: str | None) -> ChannelOrderPage:
        params = {"cursor": cursor} if cursor else None
        body = await self._call("GET", "/orders", params=params)
        raw_orders = body.get("orders")
        orders: list[ChannelOrder] = []
        for raw in raw_orders if isinstance(raw_orders, list) else []:
            if not isinstance(raw, dict):
                continue
            orders.append(parse_order(raw))
        next_cursor = body.get("next_cursor")
        return ChannelOrderPage(
            orders=orders, next_cursor=str(next_cursor) if next_cursor is not None else None
        )

    async def push_inventory(self, updates: list[ChannelInventoryUpdate]) -> ChannelSyncAck:
        payload = [{"sku": u.external_sku, "available_qty": str(u.available_qty)} for u in updates]
        body = await self._call("POST", "/inventory", json=payload)
        return ChannelSyncAck(accepted=bool(body.get("accepted")))

    async def push_fulfilment_update(self, update: ChannelFulfilmentUpdate) -> ChannelSyncAck:
        payload = {
            "external_order_id": update.external_order_id,
            "status": update.status,
            "tracking_number": update.tracking_number,
            "carrier": update.carrier,
            "occurred_at": update.occurred_at.isoformat(),
        }
        body = await self._call("POST", "/fulfilments", json=payload)
        return ChannelSyncAck(accepted=bool(body.get("accepted")))


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
    return ""


def parse_order(raw: dict[str, object]) -> ChannelOrder:
    """Parses one order exactly as `list_new_orders` receives it from the wire, into the neutral
    `ChannelOrder` shape — public (not `_`-prefixed) because `app.channels.services.retry_sync_log`
    reuses it to rebuild a `ChannelOrder` from a `ChannelOrderImport.raw_payload` when retrying a
    rejected import, without a second parser to keep in sync with this one."""
    raw_lines = raw.get("lines")
    lines: list[ChannelOrderLine] = []
    for line in raw_lines if isinstance(raw_lines, list) else []:
        if not isinstance(line, dict):
            continue
        lines.append(
            ChannelOrderLine(
                external_sku=str(line.get("sku", "")),
                quantity=Decimal(str(line.get("quantity", "0"))),
                unit_price_minor=int(line.get("unit_price_minor", 0) or 0),
            )
        )
    placed_at_raw = str(raw.get("placed_at", ""))
    placed_at = datetime.fromisoformat(placed_at_raw) if placed_at_raw else datetime.now()
    return ChannelOrder(
        external_order_id=str(raw.get("external_order_id", "")),
        placed_at=placed_at,
        currency=str(raw.get("currency", "INR")),
        customer_name=str(raw.get("customer_name", "")),
        customer_phone=str(raw.get("customer_phone", "")),
        lines=lines,
        raw=raw,
    )
