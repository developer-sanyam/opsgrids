"""A self-hosted sandbox marketplace — the one concrete `ChannelClient` this build proves the
connector boundary against, in place of the real Shopify/Amazon credentials this environment does
not have (Fulfilment plan F11's own brief: "implement its actual connector, configuration,
sandbox or mocked-provider tests"). This module is the **provider side**: a small, real HTTP API
(`app.channels.mock_provider.MockMarketplaceClient` is the **client side** that calls it) —
genuinely separate code, genuinely serialising and validating a wire payload, so an order
imported through it exercises the real HTTP boundary a real marketplace integration would, not a
Python function call dressed up as one.

What is **real** here, the same "everywhere except the wire" bargain `StubEwayClient`
(`services/finance/app/eway/stub.py`) strikes for a government portal:

* **Authentication.** Every non-admin route requires `Authorization: Bearer <api_key>` matching
  the shop's own configured key, or answers 401 — genuinely refusing a wrong or expired key, not
  a decoration.
* **Validation.** An inventory push with a negative quantity, or a fulfilment update for an
  order id this shop has never seen, is refused (422) with a real reason — not silently accepted.
* **Pagination.** Orders are listed a page at a time behind an opaque cursor, and a shop's order
  feed is never mutated by being read — a re-poll of an old cursor legitimately re-lists orders
  already seen, which is exactly why the platform's own import is idempotent on the order id
  rather than on "was it new in this page" (`app/channels/base.py`'s `list_new_orders` docstring).
* **Chaos.** `POST /_admin/shops/{id}/chaos` switches a shop into `"500"` (every call answers
  500), `"429"` (every call is rate-limited — retryable, unlike a rejection, and classified that
  way by `MockMarketplaceClient`), `"timeout"` (every call hangs — a real `asyncio.sleep`, so a
  client-side timeout fires for real, not a mocked exception) or `"unauthorized"` (every call
  answers 401 regardless of the key presented, simulating a revoked credential) — what
  `tests/test_channels.py` drives to prove the platform's own retry and expired-credential
  handling against a genuinely failing wire, not a monkeypatched connector.

What is **not real**: there is no actual Shopify/Amazon account behind this, no internet egress,
and no persistence past this process's memory — `MockMarketplace` is an in-memory store, reset
by constructing a new one (`tests/channel_fixtures.py`) or restarting `bin/mock_marketplace`.

Runs standalone via `bin/mock_marketplace` (a genuine sandbox a person can point `curl` at) and
in-process in tests via `httpx.ASGITransport` against `create_app()`'s return value — the same
`app` object either way, so nothing about the route or validation code differs between the two.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

#: How many orders one page of `GET /shops/{id}/orders` returns at most — a real feed is always
#: paginated; a caller that wants more asks again with the returned cursor.
_PAGE_SIZE = 25
#: The `"timeout"` chaos mode's own sleep — comfortably past any sane client timeout
#: (`MockMarketplaceClient`'s default is a few seconds) so a real `httpx` timeout fires, without
#: pinning a test's own worst-case runtime to this number (the request is cancelled, not awaited
#: out, the moment the client's timeout trips).
_CHAOS_TIMEOUT_SECONDS = 9999


@dataclass(slots=True)
class _Order:
    external_order_id: str
    placed_at: datetime
    currency: str
    customer_name: str
    customer_phone: str
    lines: list[dict[str, Any]]


@dataclass(slots=True)
class _Shop:
    shop_id: str
    api_key: str
    orders: list[_Order] = field(default_factory=list)
    #: sku -> the last quantity this shop was told is available.
    inventory: dict[str, Decimal] = field(default_factory=dict)
    #: external_order_id -> the last fulfilment update received.
    fulfilments: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: none | 500 | timeout | unauthorized.
    chaos: str = "none"
    #: What every inventory-push and fulfilment-push call received, in order — what a test
    #: reading "did the wire call actually happen, and with what" asserts against, the same role
    #: `StubEwayClient._issued` plays for e-Way.
    inventory_pushes: list[list[dict[str, Any]]] = field(default_factory=list)
    fulfilment_pushes: list[dict[str, Any]] = field(default_factory=list)


class MockMarketplace:
    """The sandbox's own state — one process's worth of shops. A fresh instance is a fresh
    sandbox; nothing here survives past it."""

    def __init__(self) -> None:
        self._shops: dict[str, _Shop] = {}

    def create_shop(self, shop_id: str, *, api_key: str) -> None:
        self._shops[shop_id] = _Shop(shop_id=shop_id, api_key=api_key)

    def shop(self, shop_id: str) -> _Shop | None:
        return self._shops.get(shop_id)

    def seed_order(
        self,
        shop_id: str,
        *,
        external_order_id: str | None = None,
        placed_at: datetime | None = None,
        currency: str = "INR",
        customer_name: str = "Test Customer",
        customer_phone: str = "+91 90000 00000",
        lines: list[dict[str, Any]] | None = None,
    ) -> str:
        """Puts one order into the shop's feed, as though a real buyer had just checked out —
        this is the sandbox's stand-in for "a customer bought something on the marketplace"."""
        shop = self._shops[shop_id]
        oid = external_order_id or f"MOCK-{uuid.uuid4().hex[:10].upper()}"
        shop.orders.append(
            _Order(
                external_order_id=oid,
                placed_at=placed_at or datetime.now(UTC),
                currency=currency,
                customer_name=customer_name,
                customer_phone=customer_phone,
                lines=lines or [],
            )
        )
        return oid


class _InventoryLine(BaseModel):
    sku: str
    available_qty: str


class _FulfilmentIn(BaseModel):
    external_order_id: str
    status: str
    tracking_number: str | None = None
    carrier: str | None = None
    occurred_at: str


class _ChaosIn(BaseModel):
    mode: str = Field(pattern="^(none|500|429|timeout|unauthorized)$")


class _SeedOrderLine(BaseModel):
    sku: str
    quantity: str
    unit_price_minor: int


class _SeedOrderIn(BaseModel):
    external_order_id: str | None = None
    currency: str = "INR"
    customer_name: str = "Test Customer"
    customer_phone: str = "+91 90000 00000"
    lines: list[_SeedOrderLine] = Field(default_factory=list)


class _CreateShopIn(BaseModel):
    shop_id: str
    api_key: str


def create_app(store: MockMarketplace | None = None) -> FastAPI:
    """Builds the sandbox ASGI app. `store` lets a test hand in its own isolated
    `MockMarketplace`; omitted, `bin/mock_marketplace` gets a fresh one for the process."""
    marketplace = store if store is not None else MockMarketplace()
    app = FastAPI(title="Mock Marketplace (sandbox)", docs_url="/docs", openapi_url="/openapi.json")
    app.state.marketplace = marketplace

    def _shop_or_404(shop_id: str) -> _Shop:
        shop = marketplace.shop(shop_id)
        if shop is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown shop")
        return shop

    async def _authenticate(shop_id: str, authorization: str | None) -> _Shop:
        shop = _shop_or_404(shop_id)
        if shop.chaos == "unauthorized":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="credential rejected")
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not token or token != shop.api_key:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid credential")
        if shop.chaos == "500":
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="provider error")
        if shop.chaos == "429":
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limited")
        if shop.chaos == "timeout":
            await asyncio.sleep(_CHAOS_TIMEOUT_SECONDS)
        return shop

    # ------------------------------------------------------------------ admin (seeding, chaos)

    @app.post("/_admin/shops", status_code=status.HTTP_201_CREATED, include_in_schema=False)
    async def admin_create_shop(body: _CreateShopIn) -> dict[str, str]:
        marketplace.create_shop(body.shop_id, api_key=body.api_key)
        return {"shop_id": body.shop_id}

    @app.post("/_admin/shops/{shop_id}/orders", include_in_schema=False)
    async def admin_seed_order(shop_id: str, body: _SeedOrderIn) -> dict[str, str]:
        _shop_or_404(shop_id)
        oid = marketplace.seed_order(
            shop_id,
            external_order_id=body.external_order_id,
            currency=body.currency,
            customer_name=body.customer_name,
            customer_phone=body.customer_phone,
            lines=[line.model_dump() for line in body.lines],
        )
        return {"external_order_id": oid}

    @app.post("/_admin/shops/{shop_id}/chaos", include_in_schema=False)
    async def admin_set_chaos(shop_id: str, body: _ChaosIn) -> dict[str, str]:
        shop = _shop_or_404(shop_id)
        shop.chaos = body.mode
        return {"shop_id": shop_id, "chaos": body.mode}

    # ------------------------------------------------------------------ the provider's own API

    @app.get("/shops/{shop_id}/health")
    async def health(
        shop_id: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authenticate(shop_id, authorization)
        return {"ok": True}

    @app.get("/shops/{shop_id}/orders")
    async def list_orders(
        shop_id: str,
        cursor: str | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        shop = await _authenticate(shop_id, authorization)
        start = 0
        if cursor:
            try:
                start = int(cursor)
            except ValueError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, detail="malformed cursor"
                ) from exc
        page = shop.orders[start : start + _PAGE_SIZE]
        next_cursor = str(start + len(page)) if start + len(page) < len(shop.orders) else None
        return {
            "orders": [
                {
                    "external_order_id": o.external_order_id,
                    "placed_at": o.placed_at.isoformat(),
                    "currency": o.currency,
                    "customer_name": o.customer_name,
                    "customer_phone": o.customer_phone,
                    "lines": o.lines,
                }
                for o in page
            ],
            "next_cursor": next_cursor,
        }

    @app.post("/shops/{shop_id}/inventory")
    async def push_inventory(
        shop_id: str,
        body: list[_InventoryLine],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        shop = await _authenticate(shop_id, authorization)
        if not body:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="no lines given")
        for line in body:
            try:
                qty = Decimal(line.available_qty)
            except InvalidOperation as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{line.sku}: {line.available_qty!r} is not a number",
                ) from exc
            if qty < 0:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{line.sku}: available quantity cannot be negative",
                )
            shop.inventory[line.sku] = qty
        shop.inventory_pushes.append([line.model_dump() for line in body])
        return {"accepted": True, "count": len(body)}

    @app.post("/shops/{shop_id}/fulfilments")
    async def push_fulfilment(
        shop_id: str,
        body: _FulfilmentIn,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        shop = await _authenticate(shop_id, authorization)
        if not any(o.external_order_id == body.external_order_id for o in shop.orders):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"order {body.external_order_id!r} is not one this shop issued",
            )
        record = body.model_dump()
        shop.fulfilments[body.external_order_id] = record
        shop.fulfilment_pushes.append(record)
        return {"accepted": True}

    return app


#: The standalone sandbox process's own app — `bin/mock_marketplace` points uvicorn at this.
#: Tests build their own via `create_app(MockMarketplace())` instead, for isolation
#: (`tests/channel_fixtures.py`).
app = create_app()
