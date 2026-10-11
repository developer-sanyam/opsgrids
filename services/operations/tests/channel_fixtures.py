"""Shared set-up for the channel connector tests (Fulfilment plan F11): a connected mock-provider
channel, wired to an in-process sandbox marketplace (`app.channels.mock_marketplace`) via
`httpx.ASGITransport` — real HTTP request/response objects, real server-side auth and validation,
no socket opened. Plain module, not conftest, for the same reason `order_fixtures.py`/
`helpers.py` are.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import services as channel_services
from app.channels.mock_marketplace import MockMarketplace, create_app
from app.channels.mock_provider import MockMarketplaceClient
from platform_core.auth import ScopeContext
from tests.order_fixtures import Shop

CHANNEL_READ_PERM = "operations.channels.channel.read:ORG"
CHANNEL_WRITE_PERM = "operations.channels.channel.write:ORG"
CHANNEL_SYNC_PERM = "operations.channels.channel.sync:ORG"
ALL_CHANNEL_PERMS = [CHANNEL_READ_PERM, CHANNEL_WRITE_PERM, CHANNEL_SYNC_PERM]

DEFAULT_SHOP_ID = "shop-1"
DEFAULT_API_KEY = "sk_test_dummy_key"  # pragma: allowlist secret


@dataclass
class Sandbox:
    """The mock marketplace's own in-memory store, plus the ASGI app in front of it."""

    store: MockMarketplace
    app: FastAPI


def build_sandbox(shop_id: str = DEFAULT_SHOP_ID, *, api_key: str = DEFAULT_API_KEY) -> Sandbox:
    store = MockMarketplace()
    store.create_shop(shop_id, api_key=api_key)
    return Sandbox(store=store, app=create_app(store))


def wire_mock_provider(monkeypatch: Any, sandbox: Sandbox) -> None:
    """Redirects `channel_services.build_client` so every `MockMarketplaceClient` it constructs
    talks to `sandbox.app` over `httpx.ASGITransport` instead of a real network address — the one
    seam a real Shopify/Amazon connector would instead point at a genuine base URL. Everything
    past this line (request building, response parsing, exception classification, and the mock
    marketplace's own auth/validation/chaos handling) runs exactly as it would against a real
    provider."""
    real_build_client = channel_services.build_client

    def _fake_build_client(channel: Any, *, api_key: str) -> Any:
        if channel.provider != "mock_marketplace":
            return real_build_client(channel, api_key=api_key)
        transport = httpx.ASGITransport(app=sandbox.app)
        client = httpx.AsyncClient(
            transport=transport, base_url="http://mock-marketplace", timeout=5.0
        )
        return MockMarketplaceClient(
            base_url="http://mock-marketplace",
            shop_id=channel.external_shop_id,
            api_key=api_key,
            client=client,
        )

    monkeypatch.setattr(channel_services, "build_client", _fake_build_client)


async def connect_mock_channel(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    shop: Shop,
    external_shop_id: str = DEFAULT_SHOP_ID,
    api_key: str = DEFAULT_API_KEY,
    auto_accept_orders: bool = True,
    name: str = "Test Marketplace Shop",
) -> Any:
    async with session.begin():
        channel = await channel_services.connect_channel(
            session,
            ctx,
            provider="mock_marketplace",
            name=name,
            external_shop_id=external_shop_id,
            api_key=api_key,
            default_warehouse_id=shop.warehouse_id,
            default_currency="INR",
            auto_accept_orders=auto_accept_orders,
        )
    return channel


async def client_for(channel: Any) -> Any:
    """Opens `channel`'s own sealed credential and builds its `ChannelClient` — what every test
    that drives a sync/push/health-check must use instead of an arbitrary literal API key, since
    the mock marketplace genuinely authenticates every call (`mock_marketplace._authenticate`)
    against the key it was connected with."""
    api_key = await channel_services.open_channel_credential(channel)
    return channel_services.build_client(channel, api_key=api_key)


async def map_sku(
    session: AsyncSession,
    ctx: ScopeContext,
    channel: Any,
    *,
    external_sku: str,
    inventory_item_id: Any,
) -> Any:
    async with session.begin():
        mapping = await channel_services.upsert_sku_mapping(
            session, ctx, channel, external_sku=external_sku, inventory_item_id=inventory_item_id
        )
    return mapping
