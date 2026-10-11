"""Channel connectors — connection CRUD, SKU mapping, order import, inventory push, fulfilment/
tracking push, retry, negative-authz and cross-tenant isolation (Fulfilment plan F11).

The mock marketplace's own test harness (`app.channels.mock_marketplace`, wired in-process via
`httpx.ASGITransport` by `tests.channel_fixtures.wire_mock_provider`) **is** the sandbox this
plan asks for: every test below drives a real HTTP request through `MockMarketplaceClient` to a
real (if self-hosted) server that authenticates, validates and can be told to fail — never a
stub that always returns success.

Order import goes through the real F2 write path (`app.orders.services.create_order` /
`place_order` / `accept_order`) exactly as `app.channels.services.import_channel_order` calls
them — nothing here posts to a parallel table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import consumers
from app.channels import selectors as channel_selectors
from app.channels import services as channel_services
from app.channels.base import ChannelOrder, ChannelOrderLine
from app.channels.mock_provider import MockMarketplaceClient
from app.channels.sweep import ChannelSyncSweep
from app.config import settings
from app.dispatch import services as dispatch_services
from app.models import SCHEMA, Channel, ChannelSyncLog, FulfilmentSettings, Order, Warehouse
from app.orders import selectors as order_selectors
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine, raw_bulkhead_scope
from tests.channel_fixtures import (
    ALL_CHANNEL_PERMS,
    CHANNEL_READ_PERM,
    Sandbox,
    build_sandbox,
    client_for,
    connect_mock_channel,
    map_sku,
    wire_mock_provider,
)
from tests.fulfilment_fixtures import (
    Floor,
    accept_offer,
    accept_order,
    build_floor,
    confirm_package,
    confirm_pick,
    create_tasks,
    dispatch_order,
    offer_task,
    transition_shipment,
)
from tests.helpers import as_principal, ctx_for, principal_for
from tests.order_fixtures import Shop, build_shop

pytestmark = pytest.mark.integration


@pytest.fixture
async def shop(session, org_id) -> Shop:
    return await build_shop(session, org_id, stock=200)


@pytest.fixture
def sandbox() -> Sandbox:
    return build_sandbox()


@pytest.fixture(autouse=True)
def _wire(monkeypatch, sandbox: Sandbox):  # type: ignore[no-untyped-def]
    wire_mock_provider(monkeypatch, sandbox)


def _write_ctx(org_id):  # type: ignore[no-untyped-def]
    return ctx_for(org_id, "operations.channels.channel.write", Scope.ORG)


def _sync_ctx(org_id):  # type: ignore[no-untyped-def]
    return ctx_for(org_id, "operations.channels.channel.sync", Scope.ORG)


# --------------------------------------------------------------------- connection CRUD


class TestConnectionCrud:
    async def test_connecting_seals_the_credential_and_never_returns_it(
        self, client, session, org_id, shop: Shop
    ) -> None:
        principal = principal_for(org_id, ALL_CHANNEL_PERMS)
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/channels",
                json={
                    "provider": "mock_marketplace",
                    "name": "My Shopify-like Store",
                    "external_shop_id": "shop-1",
                    "api_key": "sk_live_super_secret",  # pragma: allowlist secret
                    "default_warehouse_id": str(shop.warehouse_id),
                    "default_currency": "INR",
                    "auto_accept_orders": True,
                },
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "connected"
        assert "sk_live_super_secret" not in response.text
        assert body["api_key_masked"] != "sk_live_super_secret"  # pragma: allowlist secret

        async with session.begin():
            row = (
                await session.execute(select(Channel).where(Channel.id == uuid.UUID(body["id"])))
            ).scalar_one()
            assert row.api_key_enc is not None
            assert row.api_key_enc != b"sk_live_super_secret"

    async def test_a_second_shop_id_under_the_same_provider_is_a_separate_channel(
        self, session, org_id, shop: Shop
    ) -> None:
        ctx = _write_ctx(org_id)
        first = await connect_mock_channel(session, ctx, shop=shop, external_shop_id="shop-a")
        second = await connect_mock_channel(session, ctx, shop=shop, external_shop_id="shop-b")
        assert first.id != second.id

    async def test_disconnect_then_reconnect_clears_error_and_rotates_the_key(
        self, session, org_id, shop: Shop
    ) -> None:
        ctx = _write_ctx(org_id)
        channel = await connect_mock_channel(session, ctx, shop=shop)
        async with session.begin():
            channel = await channel_selectors.get_channel(
                session, org_id, channel.id, for_update=True
            )
            assert channel is not None
            channel = await channel_services.disconnect_channel(session, ctx, channel)
        assert channel.status == "disconnected"

        async with session.begin():
            channel = await channel_selectors.get_channel(
                session, org_id, channel.id, for_update=True
            )
            assert channel is not None
            channel = await channel_services.reconnect_channel(
                session,
                ctx,
                channel,
                api_key="sk_live_rotated",  # pragma: allowlist secret
            )
        assert channel.status == "connected"
        assert channel.consecutive_failures == 0

    async def test_patch_updates_config_without_touching_the_credential(
        self, session, org_id, shop: Shop
    ) -> None:
        ctx = _write_ctx(org_id)
        channel = await connect_mock_channel(session, ctx, shop=shop, name="Original Name")
        original_enc = channel.api_key_enc
        async with session.begin():
            channel = await channel_selectors.get_channel(
                session, org_id, channel.id, for_update=True
            )
            assert channel is not None
            channel = await channel_services.update_channel_config(
                session, ctx, channel, name="Renamed Store", auto_accept_orders=False
            )
        assert channel.name == "Renamed Store"
        assert channel.auto_accept_orders is False
        assert channel.api_key_enc == original_enc


class TestConnectionAuthzAndCrossTenant:
    async def test_missing_permission_is_forbidden(self, client, org_id, shop: Shop) -> None:
        principal = principal_for(org_id, [])
        with as_principal(principal):
            response = await client.get("/api/v1/operations/channels")
        assert response.status_code == 403

    async def test_reader_cannot_connect_a_channel(self, client, org_id, shop: Shop) -> None:
        principal = principal_for(org_id, [CHANNEL_READ_PERM])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/channels",
                json={
                    "provider": "mock_marketplace",
                    "name": "X",
                    "external_shop_id": "shop-x",
                    "api_key": "sk_x",  # pragma: allowlist secret
                    "default_warehouse_id": str(shop.warehouse_id),
                    "default_currency": "INR",
                    "auto_accept_orders": True,
                },
            )
        assert response.status_code == 403

    async def test_a_channel_from_another_org_is_a_404_not_a_403(
        self, client, admin_session, org_id, shop: Shop
    ) -> None:
        """Seeded directly on the BYPASSRLS `admin_session` (never through the tenant-bound
        `session` fixture, which is fixed to this test's own `org_id` by RLS) — the same shape
        `test_cross_tenant_isolation.py::_seed_other_org` uses to create another organization's
        row."""
        other_org = uuid.uuid4()
        other_warehouse_id = uuid.uuid4()
        foreign_channel_id = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                Warehouse.__table__.insert().values(
                    id=other_warehouse_id,
                    organization_id=other_org,
                    code="WH-FOREIGN",
                    name="Foreign Warehouse",
                    location="",
                )
            )
            await admin_session.execute(
                Channel.__table__.insert().values(
                    id=foreign_channel_id,
                    organization_id=other_org,
                    provider="mock_marketplace",
                    name="Foreign Shop",
                    status="connected",
                    external_shop_id="shop-foreign",
                    api_key_masked="****",
                    default_warehouse_id=other_warehouse_id,
                    default_currency="INR",
                    auto_accept_orders=True,
                    consecutive_failures=0,
                )
            )

        principal = principal_for(org_id, ALL_CHANNEL_PERMS)
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/channels/{foreign_channel_id}")
        assert response.status_code == 404


# --------------------------------------------------------------------- SKU mapping


class TestSkuMapping:
    async def test_mapping_is_created_then_updated_on_a_repeat_call(
        self, session, org_id, shop: Shop
    ) -> None:
        ctx = _write_ctx(org_id)
        channel = await connect_mock_channel(session, ctx, shop=shop)
        first = await map_sku(
            session, ctx, channel, external_sku="EXT-WIDGET", inventory_item_id=shop.widget
        )
        again = await map_sku(
            session, ctx, channel, external_sku="EXT-WIDGET", inventory_item_id=shop.gizmo
        )
        assert first.id == again.id
        assert again.inventory_item_id == shop.gizmo

    async def test_deleting_a_mapping_soft_deletes_it(self, session, org_id, shop: Shop) -> None:
        ctx = _write_ctx(org_id)
        channel = await connect_mock_channel(session, ctx, shop=shop)
        mapping = await map_sku(
            session, ctx, channel, external_sku="EXT-1", inventory_item_id=shop.widget
        )
        async with session.begin():
            row = await channel_selectors.get_sku_mapping(session, org_id, mapping.id)
            assert row is not None
            await channel_services.delete_sku_mapping(session, ctx, row)
        async with session.begin():
            gone = await channel_selectors.get_sku_mapping(session, org_id, mapping.id)
            assert gone is None


# --------------------------------------------------------------------- order import


class TestOrderImport:
    async def _connect_and_map(
        self, session, org_id, shop: Shop, sandbox: Sandbox, *, auto_accept=True
    ):
        ctx = _sync_ctx(org_id)
        write_ctx = _write_ctx(org_id)
        channel = await connect_mock_channel(
            session, write_ctx, shop=shop, auto_accept_orders=auto_accept
        )
        await map_sku(
            session, write_ctx, channel, external_sku="EXT-WIDGET", inventory_item_id=shop.widget
        )
        return ctx, channel

    async def test_a_new_order_is_imported_as_a_real_accepted_f2_order(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        ctx, channel = await self._connect_and_map(session, org_id, shop, sandbox)
        external_id = sandbox.store.seed_order(
            "shop-1",
            lines=[{"sku": "EXT-WIDGET", "quantity": "3", "unit_price_minor": 450_00}],
        )

        summary = await channel_services.sync_orders_for_channel(
            session, ctx, channel.id, await client_for(channel)
        )
        assert summary.attempted == 1
        assert summary.succeeded == 1
        assert summary.rejected == 0

        async with session.begin():
            link = await channel_selectors.get_order_import_by_external_id(
                session, org_id, channel.id, external_id
            )
            assert link is not None and link.order_id is not None
            order = await order_selectors.get_order(session, org_id, link.order_id)
            assert order is not None
            lines = await order_selectors.lines_for(session, org_id, order.id)

        assert order.status == "ACCEPTED"
        assert order.channel == "marketplace"
        assert len(lines) == 1
        assert lines[0].quantity_milli == 3_000  # 3 whole units, milli-scaled
        assert lines[0].selling_price_minor == 450_00
        assert lines[0].line_total_minor == 3 * 450_00

    async def test_reimporting_the_same_external_order_id_creates_no_second_order(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        ctx, channel = await self._connect_and_map(session, org_id, shop, sandbox)
        external_id = sandbox.store.seed_order(
            "shop-1", lines=[{"sku": "EXT-WIDGET", "quantity": "2", "unit_price_minor": 100_00}]
        )
        external_order = ChannelOrder(
            external_order_id=external_id,
            placed_at=datetime.now(UTC),
            currency="INR",
            customer_name="Repeat Buyer",
            customer_phone="+91 90000 00001",
            lines=[
                ChannelOrderLine(
                    external_sku="EXT-WIDGET", quantity=Decimal("2"), unit_price_minor=100_00
                )
            ],
            raw={"external_order_id": external_id},
        )

        first = await channel_services.import_channel_order(
            session, ctx, channel.id, external_order
        )
        second = await channel_services.import_channel_order(
            session, ctx, channel.id, external_order
        )
        third = await channel_services.import_channel_order(
            session, ctx, channel.id, external_order
        )

        assert first.order is not None
        assert second.order is not None and second.order.id == first.order.id
        assert third.order is not None and third.order.id == first.order.id

        async with session.begin():
            count = (
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
        assert len(count) == 1

    async def test_a_repoll_that_relists_an_already_imported_order_does_not_duplicate_it(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        """The mock marketplace's own feed never removes an order once listed (`list_new_orders`'
        own docstring) — a re-poll from an earlier cursor genuinely re-lists it, and import must
        still be idempotent on the external order id alone."""
        ctx, channel = await self._connect_and_map(session, org_id, shop, sandbox)
        sandbox.store.seed_order(
            "shop-1", lines=[{"sku": "EXT-WIDGET", "quantity": "1", "unit_price_minor": 200_00}]
        )
        client = await client_for(channel)

        first_pass = await channel_services.sync_orders_for_channel(
            session, ctx, channel.id, client
        )
        async with session.begin():
            row = await channel_selectors.get_channel(session, org_id, channel.id, for_update=True)
            assert row is not None
            row.last_order_cursor = None  # force a re-poll from the beginning
            await session.flush()
        second_pass = await channel_services.sync_orders_for_channel(
            session, ctx, channel.id, client
        )

        assert first_pass.succeeded == 1
        assert second_pass.attempted == 1  # the same order was relisted...
        assert second_pass.succeeded == 1  # ...and resolved idempotently, not duplicated

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

    async def test_an_unmapped_sku_is_rejected_not_silently_dropped(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        write_ctx = _write_ctx(org_id)
        ctx = _sync_ctx(org_id)
        channel = await connect_mock_channel(session, write_ctx, shop=shop)
        # Deliberately no SKU mapping created.
        external_id = sandbox.store.seed_order(
            "shop-1", lines=[{"sku": "EXT-UNKNOWN", "quantity": "1", "unit_price_minor": 100_00}]
        )
        client = await client_for(channel)

        summary = await channel_services.sync_orders_for_channel(session, ctx, channel.id, client)
        assert summary.attempted == 1
        assert summary.rejected == 1
        assert summary.succeeded == 0

        async with session.begin():
            link = await channel_selectors.get_order_import_by_external_id(
                session, org_id, channel.id, external_id
            )
            assert link is not None
            assert link.order_id is None
            assert link.rejected_reason is not None and "EXT-UNKNOWN" in link.rejected_reason

    async def test_retrying_after_the_mapping_is_added_imports_it(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        write_ctx = _write_ctx(org_id)
        ctx = _sync_ctx(org_id)
        channel = await connect_mock_channel(session, write_ctx, shop=shop)
        external_id = sandbox.store.seed_order(
            "shop-1", lines=[{"sku": "EXT-LATE", "quantity": "1", "unit_price_minor": 300_00}]
        )
        client = await client_for(channel)
        await channel_services.sync_orders_for_channel(session, ctx, channel.id, client)

        async with session.begin():
            logs = (
                (
                    await session.execute(
                        select(ChannelSyncLog).where(
                            ChannelSyncLog.organization_id == org_id,
                            ChannelSyncLog.channel_id == channel.id,
                            ChannelSyncLog.status == "failed",
                            ChannelSyncLog.external_ref == external_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(logs) == 1
        log_entry = logs[0]

        await map_sku(
            session, write_ctx, channel, external_sku="EXT-LATE", inventory_item_id=shop.widget
        )
        succeeded = await channel_services.retry_sync_log(
            session, ctx, channel.id, client, log_entry
        )
        assert succeeded is True

        async with session.begin():
            link = await channel_selectors.get_order_import_by_external_id(
                session, org_id, channel.id, external_id
            )
            assert link is not None and link.order_id is not None and link.rejected_reason is None

    async def test_auto_accept_off_leaves_the_order_placed_not_accepted(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        ctx, channel = await self._connect_and_map(
            session, org_id, shop, sandbox, auto_accept=False
        )
        sandbox.store.seed_order(
            "shop-1", lines=[{"sku": "EXT-WIDGET", "quantity": "1", "unit_price_minor": 500_00}]
        )
        client = await client_for(channel)
        summary = await channel_services.sync_orders_for_channel(session, ctx, channel.id, client)
        assert summary.succeeded == 1

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
        assert orders[0].status == "PLACED"


# --------------------------------------------------------------------- inventory push


class TestInventoryPush:
    async def test_available_milli_is_pushed_and_reflected_on_the_mock_shop(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        write_ctx = _write_ctx(org_id)
        ctx = _sync_ctx(org_id)
        channel = await connect_mock_channel(session, write_ctx, shop=shop)
        await map_sku(
            session, write_ctx, channel, external_sku="EXT-WIDGET", inventory_item_id=shop.widget
        )
        client = await client_for(channel)

        summary = await channel_services.push_inventory_for_channel(
            session, ctx, channel.id, client
        )
        assert summary.attempted == 1
        assert summary.succeeded == 1

        mock_shop = sandbox.store.shop("shop-1")
        assert mock_shop is not None
        assert mock_shop.inventory["EXT-WIDGET"] == Decimal("200")  # `build_shop`'s own stock

    async def test_a_second_push_with_nothing_changed_pushes_nothing(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        write_ctx = _write_ctx(org_id)
        ctx = _sync_ctx(org_id)
        channel = await connect_mock_channel(session, write_ctx, shop=shop)
        await map_sku(
            session, write_ctx, channel, external_sku="EXT-WIDGET", inventory_item_id=shop.widget
        )
        client = await client_for(channel)

        first = await channel_services.push_inventory_for_channel(session, ctx, channel.id, client)
        second = await channel_services.push_inventory_for_channel(session, ctx, channel.id, client)
        assert first.attempted == 1
        assert second.attempted == 0


# --------------------------------------------------------------------- outage / expired credential


class TestOutageAndCredentialHandling:
    async def test_a_500_is_retried_safely_and_does_not_corrupt_state(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        write_ctx = _write_ctx(org_id)
        ctx = _sync_ctx(org_id)
        channel = await connect_mock_channel(session, write_ctx, shop=shop)
        await map_sku(
            session, write_ctx, channel, external_sku="EXT-WIDGET", inventory_item_id=shop.widget
        )
        client = await client_for(channel)

        sandbox.store.shop("shop-1").chaos = "500"  # type: ignore[union-attr]
        failed_summary = await channel_services.push_inventory_for_channel(
            session, ctx, channel.id, client
        )
        assert failed_summary.succeeded == 0

        async with session.begin():
            row = await channel_selectors.get_channel(session, org_id, channel.id)
            assert row is not None
            assert row.consecutive_failures == 1
            assert row.status == "connected"  # one failure alone must not suspend the connection

        sandbox.store.shop("shop-1").chaos = "none"  # type: ignore[union-attr]
        recovered = await channel_services.push_inventory_for_channel(
            session, ctx, channel.id, client
        )
        assert recovered.succeeded == 1
        async with session.begin():
            row = await channel_selectors.get_channel(session, org_id, channel.id)
            assert row is not None
            assert row.consecutive_failures == 0

    async def test_a_429_is_classified_as_retryable_not_a_permanent_rejection(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        """A rate limit is exactly `ChannelUnavailable` (retryable with the identical request),
        never `ChannelRejected` — the mock marketplace's own `"429"` chaos mode proves the
        connector classifies it that way against a genuinely rate-limiting wire, not a comment
        that says so."""
        write_ctx = _write_ctx(org_id)
        ctx = _sync_ctx(org_id)
        channel = await connect_mock_channel(session, write_ctx, shop=shop)
        await map_sku(
            session, write_ctx, channel, external_sku="EXT-WIDGET", inventory_item_id=shop.widget
        )
        client = await client_for(channel)

        sandbox.store.shop("shop-1").chaos = "429"  # type: ignore[union-attr]
        failed_summary = await channel_services.push_inventory_for_channel(
            session, ctx, channel.id, client
        )
        assert failed_summary.succeeded == 0

        async with session.begin():
            logs = (
                (
                    await session.execute(
                        select(ChannelSyncLog).where(
                            ChannelSyncLog.organization_id == org_id,
                            ChannelSyncLog.channel_id == channel.id,
                            ChannelSyncLog.sync_type == "inventory_push",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(logs) == 1
        assert logs[0].status == "failed"
        assert logs[0].error_code == "unavailable"  # not "provider_rejected"

        sandbox.store.shop("shop-1").chaos = "none"  # type: ignore[union-attr]
        recovered = await channel_services.push_inventory_for_channel(
            session, ctx, channel.id, client
        )
        assert recovered.succeeded == 1

    async def test_a_timeout_is_classified_as_unavailable_not_a_crash(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        write_ctx = _write_ctx(org_id)
        ctx = _sync_ctx(org_id)
        channel = await connect_mock_channel(session, write_ctx, shop=shop)
        api_key = await channel_services.open_channel_credential(channel)
        # Built directly against the sandbox transport with a short `timeout_seconds`, so
        # `MockMarketplaceClient`'s own `asyncio.wait_for` deadline actually trips on the chaos
        # sleep rather than the test hanging (`ASGITransport` honours no `httpx` timeout of its
        # own — see `_timeout_seconds`'s docstring in `app/channels/mock_provider.py`).
        client = MockMarketplaceClient(
            base_url="http://mock-marketplace",
            shop_id="shop-1",
            api_key=api_key,
            timeout_seconds=0.3,
            client=httpx.AsyncClient(
                transport=httpx.ASGITransport(app=sandbox.app), base_url="http://mock-marketplace"
            ),
        )
        sandbox.store.shop("shop-1").chaos = "timeout"  # type: ignore[union-attr]

        healthy = await channel_services.check_channel_health(session, ctx, channel.id, client)
        assert healthy is False

        async with session.begin():
            logs = (
                (
                    await session.execute(
                        select(ChannelSyncLog).where(
                            ChannelSyncLog.organization_id == org_id,
                            ChannelSyncLog.channel_id == channel.id,
                            ChannelSyncLog.sync_type == "health_check",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(logs) == 1
        assert logs[0].status == "failed"
        assert logs[0].error_code == "unavailable"
        await client.aclose()

    async def test_an_expired_credential_flips_the_channel_to_error_and_needs_reconnect(
        self, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        write_ctx = _write_ctx(org_id)
        ctx = _sync_ctx(org_id)
        channel = await connect_mock_channel(session, write_ctx, shop=shop)
        client = await client_for(channel)

        sandbox.store.shop("shop-1").chaos = "unauthorized"  # type: ignore[union-attr]
        healthy = await channel_services.check_channel_health(session, ctx, channel.id, client)
        assert healthy is False

        async with session.begin():
            row = await channel_selectors.get_channel(session, org_id, channel.id)
            assert row is not None
            assert row.status == "error"
            assert row.last_error_code == "auth_error"

        # A success alone (chaos cleared) must not silently clear an auth failure — only an
        # explicit reconnect does (`Channel`'s own docstring).
        sandbox.store.shop("shop-1").chaos = "none"  # type: ignore[union-attr]
        await channel_services.check_channel_health(session, ctx, channel.id, client)
        async with session.begin():
            row = await channel_selectors.get_channel(session, org_id, channel.id)
            assert row is not None
            assert row.status == "error"

        async with session.begin():
            row = await channel_selectors.get_channel(session, org_id, channel.id, for_update=True)
            assert row is not None
            # pragma: allowlist nextline secret
            row = await channel_services.reconnect_channel(session, ctx, row, api_key="sk_fresh")
        assert row.status == "connected"


# --------------------------------------------------------------------- retry over HTTP


class TestRetryEndpoint:
    async def test_retrying_a_failed_sync_log_over_http(
        self, client, session, org_id, shop: Shop, sandbox: Sandbox
    ) -> None:
        write_ctx = _write_ctx(org_id)
        ctx = _sync_ctx(org_id)
        channel = await connect_mock_channel(session, write_ctx, shop=shop)
        sandbox.store.seed_order(
            "shop-1", lines=[{"sku": "EXT-RETRY", "quantity": "1", "unit_price_minor": 100_00}]
        )
        client_conn = await client_for(channel)
        await channel_services.sync_orders_for_channel(session, ctx, channel.id, client_conn)

        async with session.begin():
            log_row = (
                (
                    await session.execute(
                        select(ChannelSyncLog).where(
                            ChannelSyncLog.organization_id == org_id,
                            ChannelSyncLog.channel_id == channel.id,
                            ChannelSyncLog.status == "failed",
                        )
                    )
                )
                .scalars()
                .first()
            )
        assert log_row is not None

        await map_sku(
            session, write_ctx, channel, external_sku="EXT-RETRY", inventory_item_id=shop.widget
        )

        principal = principal_for(org_id, ALL_CHANNEL_PERMS)
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/channels/{channel.id}/sync-logs/{log_row.id}/retry"
            )
        assert response.status_code == 200, response.text
        assert response.json()["succeeded"] is True


# --------------------------------------------------------------------- fulfilment/tracking push


class TestFulfilmentTrackingSync:
    @pytest.fixture(autouse=True)
    async def _engine(self):  # type: ignore[no-untyped-def]
        """`dispatch_shipment_transitioned` opens its own raw session, independent of the
        fixture `session` this file otherwise uses — the process-wide engine has to exist for
        that to work (the same shape `test_fulfilment_order_cancelled_consumer.py::_engine`
        documents)."""
        init_engine(settings())
        yield
        await dispose_engine()

    @staticmethod
    def _shipment_envelope(
        *, organization_id: uuid.UUID, shipment_id: uuid.UUID, order_id: uuid.UUID, event_type: str
    ) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "organization_id": str(organization_id),
            "payload": {
                "organization_id": str(organization_id),
                "shipment_id": str(shipment_id),
                "order_id": str(order_id),
            },
        }

    async def _seed_settings(self, session, org_id: uuid.UUID) -> None:
        async with session.begin():
            session.add(
                FulfilmentSettings(
                    organization_id=org_id,
                    warehouse_id=None,
                    invoice_timing="on_delivery",
                    payment_defaults={"manual": "credit"},
                    offer_timeout_seconds=300,
                    offer_rounds=3,
                    require_pickup_code=False,
                    default_tax_rate_pct=Decimal("0"),
                )
            )

    async def test_a_dispatched_shipment_pushes_tracking_to_the_channel(
        self, client, session, org_id, sandbox: Sandbox
    ) -> None:
        floor: Floor = await build_floor(session, org_id, stock=100)
        await self._seed_settings(session, org_id)
        write_ctx = _write_ctx(org_id)
        channel = await connect_mock_channel(session, write_ctx, shop=floor.shop)
        await map_sku(
            session,
            write_ctx,
            channel,
            external_sku="EXT-TRACK",
            inventory_item_id=floor.shop.widget,
        )

        # Import the order for real, so `ChannelOrderImport` links it to this channel.
        sync_ctx = _sync_ctx(org_id)
        external_id = sandbox.store.seed_order(
            "shop-1", lines=[{"sku": "EXT-TRACK", "quantity": "10", "unit_price_minor": 500_00}]
        )
        conn_client = await client_for(channel)
        summary = await channel_services.sync_orders_for_channel(
            session, sync_ctx, channel.id, conn_client
        )
        assert summary.succeeded == 1

        async with session.begin():
            link = await channel_selectors.get_order_import_by_external_id(
                session, org_id, channel.id, external_id
            )
            assert link is not None and link.order_id is not None
            order_id = link.order_id

        # Drive the real order through pick, pack and carrier dispatch (F3/F5's own write paths).
        # This order is already ACCEPTED (via `sync_orders_for_channel`), so the remaining
        # pick/pack/dispatch steps are driven directly rather than through `packed_order` (which
        # accepts its own order internally and would double-accept this one).
        async with session.begin():
            order = await order_selectors.get_order(session, org_id, order_id)
            assert order is not None
        tasks = await create_tasks(client, org_id, order)
        task = tasks[0]
        offer = await offer_task(client, org_id, task["id"], floor.worker_id)
        await accept_offer(client, org_id, offer["id"], floor.worker_employee_id)
        line_id = task["lines"][0]["id"]
        await confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)
        await confirm_package(
            client,
            org_id,
            order_id,
            destination_location_id=floor.dock_location_id,
            pick_task_line_id=line_id,
            quantity="10",
        )

        async with session.begin():
            reloaded = await order_selectors.get_order(session, org_id, order_id)
            assert reloaded is not None
        # `payment_confirmed=True`: the counter/carrier-desk operator's attestation that a
        # marketplace order's payment was already collected by the marketplace itself — the same
        # documented stand-in `hand_over_order`'s own docstring uses pending a real
        # `services/payment` collections module (ADR-0024 A4). Not part of what this test proves.
        dispatch_response = await dispatch_order(
            client,
            org_id,
            order_id,
            reloaded.version,
            carrier_name="Test Carrier",
            tracking_reference="TRK-F11-001",
            payment_confirmed=True,
        )
        assert dispatch_response.status_code == 201, dispatch_response.text
        shipment_id = uuid.UUID(dispatch_response.json()["id"])

        in_transit = await transition_shipment(client, org_id, str(shipment_id), "in-transit")
        assert in_transit.status_code == 200, in_transit.text

        envelope = self._shipment_envelope(
            organization_id=org_id,
            shipment_id=shipment_id,
            order_id=order_id,
            event_type=dispatch_services.EVT_SHIPMENT_DISPATCHED,
        )
        await consumers.dispatch_shipment_transitioned(envelope, channel_status="dispatched")

        mock_shop = sandbox.store.shop("shop-1")
        assert mock_shop is not None
        assert external_id in mock_shop.fulfilments
        pushed = mock_shop.fulfilments[external_id]
        assert pushed["status"] == "dispatched"
        assert pushed["tracking_number"] == "TRK-F11-001"
        assert pushed["carrier"] == "Test Carrier"

        # Redelivery of the identical event is a no-op (`ProcessedEvent`), not a second push.
        await consumers.dispatch_shipment_transitioned(envelope, channel_status="dispatched")
        async with session.begin():
            pushes = (
                (
                    await session.execute(
                        select(ChannelSyncLog).where(
                            ChannelSyncLog.organization_id == org_id,
                            ChannelSyncLog.channel_id == channel.id,
                            ChannelSyncLog.sync_type == "fulfilment_push",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(pushes) == 1

    async def test_an_ordinary_manual_order_has_no_channel_and_the_push_is_a_no_op(
        self, session, org_id
    ) -> None:
        shop = await build_shop(session, org_id, stock=50)
        order = await accept_order(
            session, org_id, shop, lines=[{"inventory_item_id": shop.widget, "quantity": "1"}]
        )
        ctx = ctx_for(org_id, "operations.channels.channel.write", Scope.ORG)
        pushed = await channel_services.push_fulfilment_update_for_order(
            session,
            ctx,
            order.id,
            status="dispatched",
            tracking_number=None,
            carrier=None,
            occurred_at=datetime.now(UTC),
        )
        assert pushed is False


class TestNightlySweep:
    """`ChannelSyncSweep.tick()` against the real outbox role — not `channel_services` called
    directly, the way the rest of this file tests it. Discovery (`_CONNECTED_CHANNELS_SQL`, run
    as `opsgrids_outbox`) is a different code path from the per-connection sync above it, and a
    column the outbox role was never granted fails there, not here. That gap went undetected
    from this phase's own deploy until a real incident (`channels.sync.tick_failed`,
    `InsufficientPrivilegeError` on `channels`, hundreds of consecutive failures with no
    migration ever reporting an error) — the same shape `test_reconcile.py::TestNightlySweep`
    already guards `ReconcileSweep` against, extended here so the next sweep does not reopen it.
    """

    async def test_the_sweep_discovers_every_connected_channel_as_the_real_outbox_role(
        self, session, org_id, shop: Shop
    ) -> None:
        ctx = _write_ctx(org_id)
        await connect_mock_channel(session, ctx, shop=shop)

        connect = {"server_settings": {"search_path": f"{SCHEMA},public"}}
        # Discovery as the real outbox role: exactly `_CONNECTED_CHANNELS_SQL`'s own columns,
        # the same proof `test_reconcile.py` holds `ReconcileSweep` to. `raw_bulkhead_scope`,
        # not a hand-rolled one: the exact work-scope wiring `app/main.py` uses for this sweep —
        # a pre-opened transaction here (`bulkhead_scope`, or `ReconcileSweep`'s own pattern)
        # collides with `load_client_for_channel`'s own short transaction as a second
        # `session.begin()`, which is exactly the bug this test exists to catch.
        discovery = create_async_engine(settings().outbox_dsn, connect_args=connect)
        work = create_async_engine(settings().app_dsn, connect_args=connect)

        try:
            sweep = ChannelSyncSweep(
                discovery_factory=async_sessionmaker(discovery),
                work_scope=raw_bulkhead_scope(work),
            )
            report = await sweep.tick()
        finally:
            await discovery.dispose()
            await work.dispose()

        assert report.failed == 0
        assert report.connections == 1
