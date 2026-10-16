"""The integrations framework: channel kinds, carrier dispatch behind `log_only`, and the inbound
webhook inbox (a carrier/ERP/POS/marketplace calling us).

Negative authorization and cross-tenant cases sit beside each positive one, as everywhere here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
import structlog
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app import domain
from app.channels import carrier as carrier_module
from app.channels.carrier import CarrierBooking, CarrierClient
from app.channels.services import _aad
from app.channels.sweep import _CONNECTED_CHANNELS_SQL
from app.channels.webhooks import MockMarketplaceVerifier, minimise
from app.config import settings
from app.dispatch import services as dispatch_services
from app.models import Channel, FulfilmentSettings, IntegrationEvent, Shipment
from app.orders import selectors as order_selectors
from platform_core.db import (
    dispose_engine,
    dispose_outbox_engine,
    get_outbox_sessionmaker,
    init_engine,
    init_outbox_engine,
)
from platform_core.pii import seal
from tests.fulfilment_fixtures import Floor, build_floor, dispatch_order, ready_order
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration

API_KEY = "sk_carrier_test_key_0001"  # pragma: allowlist secret
SIGNATURE = "x-mock-signature"


@pytest_asyncio.fixture
async def engines() -> AsyncIterator[None]:
    """The process-wide app and outbox engines: the webhook's pre-tenant lookup runs on the outbox
    role's column grant, which is `None` (and silently the app engine) until this has run."""
    cfg = settings()
    init_engine(cfg)
    init_outbox_engine(cfg)
    yield
    await dispose_outbox_engine()
    await dispose_engine()


async def seed_channel(
    admin_session: Any,
    org: uuid.UUID,
    *,
    provider: str = "mock_marketplace",
    kind: str = "sales",
    status: str = "connected",
    name: str = "Test Channel",
    api_key: str | None = API_KEY,
) -> uuid.UUID:
    """A channel row for any organization, written on the BYPASSRLS session, with its credential
    sealed exactly as `connect_channel` seals it."""
    channel_id = uuid.uuid4()
    enc = None
    if api_key is not None:
        enc, _ = seal(api_key, kind="secret", settings=settings(), aad=_aad(org, channel_id))
    async with admin_session.begin():
        await admin_session.execute(
            Channel.__table__.insert().values(
                id=channel_id,
                organization_id=org,
                provider=provider,
                kind=kind,
                name=name,
                status=status,
                external_shop_id=f"shop-{channel_id.hex[:8]}",
                api_key_enc=enc,
                api_key_masked="****",
                default_currency="INR",
                auto_accept_orders=True,
                consecutive_failures=0,
            )
        )
    return channel_id


def sign(body: bytes, secret: str = API_KEY) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def event_body(event_id: str = "evt-1", **over: Any) -> bytes:
    body = {
        "id": event_id,
        "event": "order.updated",
        "order_id": "ORD-1",
        "status": "shipped",
        "customer_name": "Asha Verma",
        "customer_phone": "9876543210",
        **over,
    }
    return json.dumps(body).encode()


async def post_webhook(
    client: Any,
    channel_id: uuid.UUID | str,
    body: bytes,
    *,
    provider: str = "mock_marketplace",
    signature: str | None = None,
    secret: str = API_KEY,
) -> Any:
    headers = {"content-type": "application/json"}
    headers[SIGNATURE] = sign(body, secret) if signature is None else signature
    return await client.post(
        f"/api/v1/webhooks/operations/{provider}?connection={channel_id}",
        content=body,
        headers=headers,
    )


async def stored_events(admin_session: Any) -> list[dict[str, Any]]:
    async with admin_session.begin():
        result = await admin_session.execute(
            text(
                "SELECT organization_id, channel_id, provider, event_id, event_name, payload, "
                "outcome, outcome_reason FROM operations.integration_events ORDER BY received_at"
            )
        )
        return [dict(r) for r in result.mappings().all()]


# =============================================================================== channel kinds


class TestChannelKind:
    def test_every_provider_has_a_known_kind(self) -> None:
        assert set(domain.CHANNEL_PROVIDER_KINDS) == set(domain.CHANNEL_PROVIDERS)
        assert set(domain.CHANNEL_PROVIDER_KINDS.values()) <= set(domain.CHANNEL_KINDS)

    async def test_a_channel_is_a_sales_channel_by_default(
        self, client, org_id, admin_session
    ) -> None:
        channel_id = await seed_channel(admin_session, org_id)
        with as_principal(principal_for(org_id, ["operations.channels.channel.read:ORG"])):
            response = await client.get(f"/api/v1/operations/channels/{channel_id}")
        assert response.json()["kind"] == "sales"

    async def test_connecting_with_the_wrong_kind_for_a_provider_is_refused(
        self, client, org_id
    ) -> None:
        with as_principal(principal_for(org_id, ["operations.channels.channel.write:ORG"])):
            response = await client.post(
                "/api/v1/operations/channels",
                json={
                    "provider": "mock_marketplace",
                    "kind": "carrier",
                    "name": "x",
                    "external_shop_id": "s",
                    "api_key": API_KEY,
                    "default_warehouse_id": str(uuid.uuid4()),
                },
            )
        assert response.status_code == 422, response.text

    async def test_the_list_filters_by_kind_and_stays_in_the_tenant(
        self, client, org_id, admin_session
    ) -> None:
        await seed_channel(admin_session, org_id, provider="p_sales", kind="sales")
        carrier = await seed_channel(admin_session, org_id, provider="p_carrier", kind="carrier")
        await seed_channel(admin_session, uuid.uuid4(), provider="p_carrier", kind="carrier")
        with as_principal(principal_for(org_id, ["operations.channels.channel.read:ORG"])):
            response = await client.get("/api/v1/operations/channels?kind=carrier")
        assert [c["id"] for c in response.json()["items"]] == [str(carrier)]

    async def test_listing_needs_the_read_permission(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, [])):
            response = await client.get("/api/v1/operations/channels?kind=carrier")
        assert response.status_code == 403

    async def test_the_order_sweep_discovers_sales_channels_only(
        self, engines, org_id, admin_session
    ) -> None:
        sales = await seed_channel(admin_session, org_id, provider="p_sales", kind="sales")
        await seed_channel(admin_session, org_id, provider="p_carrier", kind="carrier")
        await seed_channel(admin_session, org_id, provider="p_erp", kind="erp")
        async with get_outbox_sessionmaker()() as bypass, bypass.begin():
            found = {row.id for row in await bypass.execute(_CONNECTED_CHANNELS_SQL)}
        assert found == {sales}


# =============================================================================== dispatch


@pytest_asyncio.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id, stock=300)


async def _ready(client, session, org_id, floor: Floor):  # type: ignore[no-untyped-def]
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
    order_id = await ready_order(client, org_id, session, floor)
    async with session.begin():
        order = await order_selectors.get_order(session, org_id, order_id)
    assert order is not None
    return order_id, order.version


async def _shipment(session, org_id, order_id) -> Shipment:  # type: ignore[no-untyped-def]
    async with session.begin():
        return (
            await session.execute(
                select(Shipment).where(
                    Shipment.organization_id == org_id, Shipment.order_id == order_id
                )
            )
        ).scalar_one()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any outbound HTTP request fails the test: `log_only` must make no call."""

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("a carrier booking made a network call in log_only mode")

    # The real network transports only: the test client itself rides `ASGITransport`.
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _boom)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _boom)


class TestCarrierDispatch:
    async def test_log_only_logs_the_booking_and_makes_no_call(
        self, client, session, org_id, admin_session, floor, no_network
    ) -> None:
        assert settings().carrier_booking_mode == "log_only"
        channel_id = await seed_channel(
            admin_session, org_id, provider="p_carrier", kind="carrier", name="Fast Couriers"
        )
        order_id, version = await _ready(client, session, org_id, floor)

        with structlog.testing.capture_logs() as logs:
            response = await dispatch_order(
                client,
                org_id,
                order_id,
                version,
                carrier_name=None,
                tracking_reference="MANUAL-1",
                channel_id=channel_id,
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["channel_id"] == str(channel_id)
        assert body["carrier_name"] == "Fast Couriers"
        assert body["tracking_reference"] == "MANUAL-1"
        assert body["external_shipment_id"] is None
        assert [e for e in logs if e["event"] == "carrier.booking.log_only"]
        assert API_KEY not in str(logs)

    async def test_a_manual_carrier_name_still_works_without_a_channel(
        self, client, session, org_id, floor, no_network
    ) -> None:
        order_id, version = await _ready(client, session, org_id, floor)
        response = await dispatch_order(client, org_id, order_id, version)
        assert response.status_code == 201, response.text
        assert response.json()["channel_id"] is None

    async def test_another_organizations_carrier_is_not_found(
        self, client, session, org_id, admin_session, floor
    ) -> None:
        foreign = await seed_channel(admin_session, uuid.uuid4(), provider="p", kind="carrier")
        order_id, version = await _ready(client, session, org_id, floor)
        response = await dispatch_order(
            client, org_id, order_id, version, carrier_name=None, channel_id=foreign
        )
        assert response.status_code == 404, response.text

    @pytest.mark.parametrize(
        ("kind", "status", "expected"),
        [("sales", "connected", 422), ("carrier", "disconnected", 409)],
    )
    async def test_only_a_connected_carrier_can_be_shipped_through(
        self, client, session, org_id, admin_session, floor, kind, status, expected
    ) -> None:
        channel_id = await seed_channel(
            admin_session, org_id, provider="p", kind=kind, status=status
        )
        order_id, version = await _ready(client, session, org_id, floor)
        response = await dispatch_order(
            client, org_id, order_id, version, carrier_name=None, channel_id=channel_id
        )
        assert response.status_code == expected, response.text
        assert await _no_shipment(session, org_id, order_id)

    async def test_naming_no_carrier_at_all_is_refused(
        self, client, session, org_id, floor
    ) -> None:
        order_id, version = await _ready(client, session, org_id, floor)
        response = await dispatch_order(client, org_id, order_id, version, carrier_name=None)
        assert response.status_code == 422, response.text

    async def test_dispatching_needs_the_write_permission(
        self, client, session, org_id, admin_session, floor
    ) -> None:
        channel_id = await seed_channel(admin_session, org_id, provider="p", kind="carrier")
        order_id, version = await _ready(client, session, org_id, floor)
        response = await dispatch_order(
            client,
            org_id,
            order_id,
            version,
            carrier_name=None,
            channel_id=channel_id,
            perms=["operations.dispatch.shipment.read:ORG"],
        )
        assert response.status_code == 403

    async def test_live_mode_needs_booking_details_and_dispatch_records_a_made_booking(
        self, client, session, org_id, admin_session, floor, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings(), "carrier_booking_mode", "live")
        channel_id = await seed_channel(admin_session, org_id, provider="p", kind="carrier")
        order_id, version = await _ready(client, session, org_id, floor)

        refused = await dispatch_order(
            client, org_id, order_id, version, carrier_name=None, channel_id=channel_id
        )
        assert refused.status_code == 422, refused.text
        assert await _no_shipment(session, org_id, order_id)

        ctx = ctx_for(org_id, "operations.dispatch.shipment.write", domain_scope())
        async with session.begin():
            order = await order_selectors.get_order(session, org_id, order_id, for_update=True)
            assert order is not None
            shipment = await dispatch_services.dispatch_order(
                session,
                ctx,
                order,
                expected_version=version,
                carrier_name=None,
                tracking_reference=None,
                channel_id=channel_id,
                booking=CarrierBooking(awb="AWB123", external_shipment_id="EXT-9"),
            )
        assert shipment.tracking_reference == "AWB123"
        assert shipment.external_shipment_id == "EXT-9"


def domain_scope():  # type: ignore[no-untyped-def]
    from platform_core.auth import Scope

    return Scope.ORG


async def _no_shipment(session, org_id, order_id) -> bool:  # type: ignore[no-untyped-def]
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(Shipment).where(
                        Shipment.organization_id == org_id, Shipment.order_id == order_id
                    )
                )
            )
            .scalars()
            .all()
        )
    return rows == []


def test_the_carrier_interface_is_a_runtime_checkable_protocol() -> None:
    class Fake:
        name = "fake"

        async def check_serviceability(self, query):  # type: ignore[no-untyped-def]
            return []

        async def create_shipment(self, request):  # type: ignore[no-untyped-def]
            return CarrierBooking(awb="A", external_shipment_id="E")

        async def get_label(self, external_shipment_id):  # type: ignore[no-untyped-def]
            return carrier_module.CarrierLabel(url="https://example.invalid/l")

        async def cancel_shipment(self, external_shipment_id):  # type: ignore[no-untyped-def]
            return carrier_module.CarrierAck(accepted=True)

        async def track(self, awb):  # type: ignore[no-untyped-def]
            return carrier_module.CarrierTracking(awb=awb)

    assert isinstance(Fake(), CarrierClient)
    assert not isinstance(object(), CarrierClient)


# =============================================================================== webhooks


class TestWebhookInbox:
    async def test_a_signed_delivery_is_recorded_once_and_minimised(
        self, engines, client, org_id, admin_session
    ) -> None:
        channel_id = await seed_channel(admin_session, org_id)
        body = event_body()

        first = await post_webhook(client, channel_id, body)
        again = await post_webhook(client, channel_id, body)

        assert first.status_code == 200, first.text
        assert first.json() == {"event_id": "evt-1", "outcome": "received", "duplicate": False}
        assert again.status_code == 200 and again.json()["duplicate"] is True
        (row,) = await stored_events(admin_session)
        assert row["organization_id"] == org_id and row["channel_id"] == channel_id
        assert row["payload"] == {"order_id": "ORD-1", "status": "shipped"}
        assert "Asha" not in str(row) and "9876543210" not in str(row)

    async def test_an_unknown_event_is_recorded_as_ignored(
        self, engines, client, org_id, admin_session
    ) -> None:
        channel_id = await seed_channel(admin_session, org_id)
        response = await post_webhook(client, channel_id, event_body("evt-2", event="shop.themed"))
        assert response.json()["outcome"] == "ignored"
        (row,) = await stored_events(admin_session)
        assert row["outcome_reason"] == "unknown_event"

    @pytest.mark.parametrize("signature", ["", "0" * 64, "not-hex"])
    async def test_a_bad_or_missing_signature_fails_closed_and_records_nothing(
        self, engines, client, org_id, admin_session, signature
    ) -> None:
        channel_id = await seed_channel(admin_session, org_id)
        body = event_body()
        headers = {"content-type": "application/json"}
        if signature:
            headers[SIGNATURE] = signature
        response = await client.post(
            f"/api/v1/webhooks/operations/mock_marketplace?connection={channel_id}",
            content=body,
            headers=headers,
        )
        assert response.status_code == 400, response.text
        assert await stored_events(admin_session) == []

    async def test_a_signature_made_with_another_connections_key_is_refused(
        self, engines, client, org_id, admin_session
    ) -> None:
        """Cross-tenant: organization B's key cannot authenticate a body for organization A's
        connection, and nothing lands under either organization."""
        mine = await seed_channel(admin_session, org_id)
        await seed_channel(admin_session, uuid.uuid4(), api_key="sk_other_orgs_key_0002")
        body = event_body()
        response = await post_webhook(
            client, mine, body, signature=sign(body, "sk_other_orgs_key_0002")
        )
        assert response.status_code == 400
        assert await stored_events(admin_session) == []

    @pytest.mark.parametrize(
        "case",
        ["unknown_provider", "bad_id", "unknown_connection", "wrong_provider", "disconnected",
         "no_credential", "removed"],
    )  # fmt: skip
    async def test_every_unverifiable_delivery_gets_the_same_400(
        self, engines, client, org_id, admin_session, case
    ) -> None:
        body = event_body()
        provider = "mock_marketplace"
        channel_id: Any = await seed_channel(admin_session, org_id)
        if case == "unknown_provider":
            provider = "nonesuch"
        elif case == "bad_id":
            channel_id = "not-a-uuid"
        elif case == "unknown_connection":
            channel_id = uuid.uuid4()
        elif case == "wrong_provider":
            channel_id = await seed_channel(admin_session, org_id, provider="p_other")
        elif case == "disconnected":
            channel_id = await seed_channel(admin_session, org_id, status="disconnected")
        elif case == "no_credential":
            channel_id = await seed_channel(admin_session, org_id, api_key=None)
        elif case == "removed":
            async with admin_session.begin():
                await admin_session.execute(
                    text("UPDATE operations.channels SET deleted_at = now() WHERE id = :i"),
                    {"i": channel_id},
                )
        response = await post_webhook(client, channel_id, body, provider=provider)
        assert response.status_code == 400, response.text
        assert response.json()["type"].endswith("invalid-webhook")
        assert await stored_events(admin_session) == []

    async def test_an_oversized_body_is_refused_before_any_signature_work(
        self, engines, client, org_id, admin_session
    ) -> None:
        channel_id = await seed_channel(admin_session, org_id)
        body = b"x" * (256 * 1024 + 1)
        response = await post_webhook(client, channel_id, body)
        assert response.status_code == 413

    async def test_an_unidentifiable_authentic_body_is_a_400(
        self, engines, client, org_id, admin_session
    ) -> None:
        channel_id = await seed_channel(admin_session, org_id)
        for body in (b"[1, 2]", b"not json", json.dumps({"event": "order.updated"}).encode()):
            response = await post_webhook(client, channel_id, body)
            assert response.status_code == 400, body
        assert await stored_events(admin_session) == []

    async def test_the_inbox_is_tenant_isolated_and_append_only(
        self, engines, client, session, org_id, admin_session
    ) -> None:
        mine = await seed_channel(admin_session, org_id)
        await post_webhook(client, mine, event_body())

        async with session.begin():
            assert len((await session.execute(select(IntegrationEvent))).scalars().all()) == 1
            await session.execute(
                text("SELECT set_config('app.organization_id', :o, true)"), {"o": str(uuid.uuid4())}
            )
            assert (await session.execute(select(IntegrationEvent))).scalars().all() == []
        for statement in ("UPDATE operations.integration_events SET outcome = 'ignored'",
                          "DELETE FROM operations.integration_events"):  # fmt: skip
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(
                        text("SELECT set_config('app.organization_id', :o, true)"),
                        {"o": str(org_id)},
                    )
                    await session.execute(text(statement))


class TestMinimise:
    def test_only_allow_listed_scalars_survive(self) -> None:
        kept = minimise(
            {"a": "x" * 500, "b": 3, "c": {"nested": 1}, "d": [1], "e": None, "f": True, "z": "no"},
            frozenset({"a", "b", "c", "d", "e", "f"}),
        )
        assert kept == {"a": "x" * 200, "b": 3, "e": None, "f": True}

    def test_the_mock_verifier_fails_closed_on_missing_pieces(self) -> None:
        v = MockMarketplaceVerifier()
        body = b"{}"
        assert not v.verify(headers={}, body=body, secret="k")
        assert not v.verify(headers={SIGNATURE: sign(body, "k")}, body=body, secret="")
        assert v.verify(headers={SIGNATURE: sign(body, "k")}, body=body, secret="k")
