"""Delhivery: the adapter against the vendor's documented requests, the scan-push webhook, the
connect flow, and a live booking through the real adapter over a stubbed transport.

The claim -> call -> record machinery is provider-neutral and is covered in
`test_carrier_shiprocket.py`; here only what is Delhivery's own.
"""

# ruff: noqa: F811, S107 -- shared fixtures are imported; the tokens are test values
from __future__ import annotations

import json
import urllib.parse
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio

from app.channels import registry
from app.channels.base import ChannelAuthError, ChannelRejected, ChannelUnavailable
from app.channels.carrier import (
    CarrierBookingRequest,
    CarrierClient,
    CarrierCredential,
    ServiceabilityQuery,
)
from app.channels.delhivery import DelhiveryClient, DelhiveryVerifier, map_status
from app.config import settings
from tests.fulfilment_fixtures import dispatch_order
from tests.helpers import as_principal, principal_for
from tests.test_carrier_shiprocket import (  # noqa: F401 -- fixtures
    BOOKING,
    READ,
    WRITE,
    _row,
    live,
    seed_carrier,
)
from tests.test_integrations_framework import (  # noqa: F401 -- fixtures
    _ready,
    _shipment,
    engines,
    floor,
    no_network,
    stored_events,
)

pytestmark = pytest.mark.integration

PICKUP = "Main Warehouse"
WAYBILL = "1122345678722"

# --- shapes from one.delhivery.com/developer-portal (requests) --------------------------------
CREATE_OK = {
    "success": True,
    "packages": [
        {"status": "Success", "waybill": WAYBILL, "refnum": "ORD-2026-0001", "remarks": [""]}
    ],
}
CREATE_DUPLICATE = {
    "success": False,
    "rmk": "",
    "packages": [{"status": "Fail", "waybill": "", "remarks": ["Order Id already exists"]}],
}
TRACK = {
    "ShipmentData": [
        {
            "Shipment": {
                "AWB": WAYBILL,
                "Status": {"Status": "Delivered", "StatusType": "DL"},
                "Scans": [
                    {
                        "ScanDetail": {
                            "Scan": "Delivered",
                            "ScanType": "DL",
                            "ScanDateTime": "2026-10-17T11:37:00",
                            "ScannedLocation": "Bengaluru",
                        }
                    },
                    {
                        "ScanDetail": {
                            "Scan": "In Transit",
                            "ScanType": "UD",
                            "ScanDateTime": "2026-10-16T21:02:00",
                            "ScannedLocation": "Hosur",
                        }
                    },
                    {
                        "ScanDetail": {
                            "Scan": "Manifested",
                            "ScanType": "UD",
                            "ScanDateTime": "2026-10-16T10:00:00",
                            "ScannedLocation": "",
                        }
                    },
                ],
            }
        }
    ]
}


def scan_push(
    status: str = "In Transit", status_type: str = "UD", at: str = "2026-10-16T21:02:00"
) -> dict:
    return {
        "Shipment": {
            "AWB": WAYBILL,
            "ReferenceNo": "ORD-2026-0001",
            "Status": {
                "Status": status,
                "StatusType": status_type,
                "StatusDateTime": at,
                "StatusLocation": "Hosur",
                "Instructions": "In Transit",
            },
        }
    }


def _client(handler: Any) -> DelhiveryClient:
    return DelhiveryClient(
        base_url="https://dl.test",
        token="dl-token",
        client=httpx.AsyncClient(
            base_url="https://dl.test", transport=httpx.MockTransport(handler)
        ),
    )


def _request(**over: Any) -> CarrierBookingRequest:
    base: dict[str, Any] = {
        "reference": "ORD-2026-0001",
        "service_code": "",
        "origin_pincode": "122002",
        "destination_pincode": "560001",
        "weight_grams": 500,
        "consignee_name": "Asha Verma",
        "consignee_phone": "9876543210",
        "consignee_address": "12 MG Road",
        "consignee_city": "Bengaluru",
        "consignee_state": "Karnataka",
        "pickup_location": PICKUP,
        "declared_value_minor": 125050,
        "length_cm": 20,
        "breadth_cm": 15,
        "height_cm": 10,
    }
    return CarrierBookingRequest(**{**base, **over})


# =============================================================================== the adapter


class TestAdapter:
    def test_it_satisfies_the_carrier_protocol(self) -> None:
        assert isinstance(_client(lambda r: httpx.Response(200)), CarrierClient)

    async def test_create_posts_the_documented_form_and_reads_the_waybill(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=CREATE_OK)

        booking = await _client(handler).create_shipment(_request(cod_amount_minor=125050))
        (request,) = seen
        assert request.method == "POST" and request.url.path == "/api/cmu/create.json"
        assert request.headers["authorization"] == "Token dl-token"
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        form = dict(urllib.parse.parse_qsl(request.content.decode()))
        assert form["format"] == "json"
        data = json.loads(form["data"])
        assert data["pickup_location"] == {"name": PICKUP}
        shipment = data["shipments"][0]
        assert shipment["order"] == "ORD-2026-0001"
        assert shipment["pin"] == "560001"
        assert shipment["payment_mode"] == "COD"
        assert shipment["cod_amount"] == "1250.5"
        assert shipment["weight"] == "500"
        assert (
            shipment["shipment_length"],
            shipment["shipment_width"],
            shipment["shipment_height"],
        ) == (
            "20",
            "15",
            "10",
        )
        assert booking.awb == WAYBILL and booking.external_shipment_id == WAYBILL

    async def test_a_prepaid_booking_says_prepaid_with_no_cod_amount(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=CREATE_OK)

        await _client(handler).create_shipment(_request())
        shipment = json.loads(dict(urllib.parse.parse_qsl(seen[0].content.decode()))["data"])[
            "shipments"
        ][0]
        assert shipment["payment_mode"] == "Prepaid" and shipment["cod_amount"] == "0"

    async def test_special_characters_survive_the_form_encoding(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=CREATE_OK)

        await _client(handler).create_shipment(
            _request(consignee_address="Flat 4 & 5, #12; 100% Rd")
        )
        form = dict(urllib.parse.parse_qsl(seen[0].content.decode()))
        assert json.loads(form["data"])["shipments"][0]["add"] == "Flat 4 & 5, #12; 100% Rd"

    async def test_a_duplicate_order_is_rejected_with_the_carriers_remark(self) -> None:
        with pytest.raises(ChannelRejected) as excinfo:
            await _client(lambda r: httpx.Response(200, json=CREATE_DUPLICATE)).create_shipment(
                _request()
            )
        assert "already exists" in excinfo.value.message

    @pytest.mark.parametrize(
        ("status", "error"),
        [
            (401, ChannelAuthError),
            (403, ChannelAuthError),
            (500, ChannelUnavailable),
            (429, ChannelUnavailable),
        ],
    )
    async def test_failures_map_to_the_three_channel_shapes(self, status: int, error: type) -> None:
        with pytest.raises(error):
            await _client(lambda r: httpx.Response(status, json={})).create_shipment(_request())

    async def test_a_served_pincode_returns_a_priced_rate(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/c/api/pin-codes/json/":
                assert request.url.params["filter_codes"] == "560001"
                return httpx.Response(
                    200,
                    json={
                        "delivery_codes": [
                            {"postal_code": {"pin": 560001, "cod": "Y", "pre_paid": "Y"}}
                        ]
                    },
                )
            assert dict(request.url.params) == {
                "md": "S",
                "ss": "Delivered",
                "o_pin": "122002",
                "d_pin": "560001",
                "cgm": "500",
                "pt": "Pre-paid",
            }
            return httpx.Response(200, json=[{"total_amount": 74.5}])

        rates = await _client(handler).check_serviceability(
            ServiceabilityQuery("122002", "560001", 500)
        )
        assert [(r.service_code, r.charge_minor) for r in rates] == [("S", 7450)]

    @pytest.mark.parametrize(
        ("reply", "cod"),
        [
            ({"delivery_codes": []}, 0),
            ({"delivery_codes": [{"postal_code": {"cod": "N", "pre_paid": "Y"}}]}, 1000),
        ],
    )
    async def test_an_unserved_lane_is_an_empty_list(self, reply: dict, cod: int) -> None:
        client = _client(lambda r: httpx.Response(200, json=reply))
        assert (
            await client.check_serviceability(
                ServiceabilityQuery("1", "560001", 1, cod_amount_minor=cod)
            )
            == []
        )

    async def test_cancel_sends_the_documented_body(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"status": True, "remark": "ok"})

        ack = await _client(handler).cancel_shipment(WAYBILL)
        assert ack.accepted
        assert seen[0].url.path == "/api/p/edit"
        assert json.loads(seen[0].content) == {"waybill": WAYBILL, "cancellation": "true"}

    async def test_a_refused_cancel_is_rejected(self) -> None:
        reply = {"status": False, "remark": "cannot cancel after pickup"}
        with pytest.raises(ChannelRejected):
            await _client(lambda r: httpx.Response(200, json=reply)).cancel_shipment(WAYBILL)

    async def test_the_label_is_the_pdf_link(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert dict(request.url.params) == {"wbns": WAYBILL, "pdf": "true", "pdf_size": "4R"}
            return httpx.Response(
                200, json={"packages": [{"pdf_download_link": "https://l/x.pdf"}]}
            )

        assert (await _client(handler).get_label(WAYBILL)).url == "https://l/x.pdf"

    async def test_tracking_is_oldest_first_in_platform_statuses(self) -> None:
        tracking = await _client(lambda r: httpx.Response(200, json=TRACK)).track(WAYBILL)
        assert [e.status for e in tracking.events] == ["IN_TRANSIT", "DELIVERED"]
        assert tracking.events[0].occurred_at == datetime(2026, 10, 16, 15, 32, tzinfo=UTC)

    def test_status_mapping_follows_the_documented_table(self) -> None:
        assert map_status("UD", "Dispatched") == "IN_TRANSIT"
        assert map_status("DL", "Delivered") == "DELIVERED"
        assert map_status("DL", "RTO") == "RETURNED"
        assert map_status("UD", "Manifested") is None
        assert map_status("PP", "Open") is None
        assert map_status(None, "Delivered") is None


# =============================================================================== the verifier


class TestVerifier:
    cred = CarrierCredential("dl-token", "", "tok-abc")
    verifier = DelhiveryVerifier()

    @pytest.mark.parametrize("header", ["tok-abc", "Token tok-abc", "Bearer tok-abc"])
    def test_the_connections_own_token_passes_in_any_common_form(self, header: str) -> None:
        assert self.verifier.verify(
            headers={"authorization": header}, body=b"{}", secret=self.cred.pack()
        )

    @pytest.mark.parametrize(
        "headers", [{}, {"authorization": ""}, {"authorization": "Token nope"}]
    )
    def test_a_missing_or_wrong_token_fails_closed(self, headers: dict[str, str]) -> None:
        assert not self.verifier.verify(headers=headers, body=b"{}", secret=self.cred.pack())

    def test_a_connection_without_a_packed_credential_fails_closed(self) -> None:
        assert not self.verifier.verify(
            headers={"authorization": "dl-token"}, body=b"{}", secret="dl-token"
        )

    def test_the_scan_is_flattened_to_scalars_with_a_stable_event_id(self) -> None:
        body = json.dumps(scan_push()).encode()
        a, b = (self.verifier.parse(headers={}, body=body) for _ in range(2))
        assert a and b and a.event_id == b.event_id
        assert a.fields["awb"] == WAYBILL and a.fields["status_type"] == "UD"
        later = self.verifier.parse(
            headers={}, body=json.dumps(scan_push(at="2026-10-17T01:00:00")).encode()
        )
        assert later and later.event_id != a.event_id

    @pytest.mark.parametrize(
        "body", [b"not json", b"{}", b'{"Shipment": {"AWB": "1"}}', b'{"Shipment": {"Status": {}}}']
    )
    def test_an_unidentifiable_body_is_refused(self, body: bytes) -> None:
        assert self.verifier.parse(headers={}, body=body) is None


# =============================================================================== the webhook


def _post(client: Any, channel_id: uuid.UUID, body: dict, token: str | None = "tok-abc") -> Any:
    headers = {"content-type": "application/json"}
    if token is not None:
        headers["authorization"] = f"Token {token}"
    return client.post(
        f"/api/v1/webhooks/operations/delhivery?connection={channel_id}",
        content=json.dumps(body).encode(),
        headers=headers,
    )


@pytest_asyncio.fixture
async def shipped(
    client, session, org_id, admin_session, floor, engines
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    channel_id = await seed_carrier(admin_session, org_id, provider="delhivery")
    order_id, version = await _ready(client, session, org_id, floor)
    response = await dispatch_order(
        client,
        org_id,
        order_id,
        version,
        carrier_name=None,
        tracking_reference=WAYBILL,
        channel_id=channel_id,
    )
    assert response.status_code == 201, response.text
    yield channel_id, order_id


class TestWebhook:
    async def test_a_verified_scan_is_recorded_then_moves_the_shipment(
        self, client, session, org_id, admin_session, shipped
    ) -> None:
        channel_id, order_id = shipped
        response = await _post(client, channel_id, scan_push())
        assert response.status_code == 200, response.text
        events = await stored_events(admin_session)
        assert len(events) == 1 and events[0]["provider"] == "delhivery"
        assert events[0]["payload"]["awb"] == WAYBILL
        assert (await _shipment(session, org_id, order_id)).status == "IN_TRANSIT"

    async def test_delivered_then_a_late_in_transit_does_not_regress(
        self, client, session, org_id, admin_session, shipped
    ) -> None:
        channel_id, order_id = shipped
        await _post(client, channel_id, scan_push("Delivered", "DL", "2026-10-17T11:00:00"))
        assert (await _shipment(session, org_id, order_id)).status == "DELIVERED"
        await _post(client, channel_id, scan_push("In Transit", "UD", "2026-10-16T09:00:00"))
        assert (await _shipment(session, org_id, order_id)).status == "DELIVERED"

    async def test_a_redelivery_records_once(self, client, admin_session, shipped) -> None:
        channel_id, _ = shipped
        await _post(client, channel_id, scan_push())
        again = await _post(client, channel_id, scan_push())
        assert again.status_code == 200 and again.json()["duplicate"] is True
        assert len(await stored_events(admin_session)) == 1

    @pytest.mark.parametrize("token", [None, "", "wrong"])
    async def test_a_missing_or_wrong_token_is_a_400_and_records_nothing(
        self, client, session, org_id, admin_session, shipped, token
    ) -> None:
        channel_id, order_id = shipped
        response = await _post(client, channel_id, scan_push(), token)
        assert response.status_code == 400
        assert await stored_events(admin_session) == []
        assert (await _shipment(session, org_id, order_id)).status == "PICKUP_HANDOVER"

    async def test_cross_tenant_token_and_awb_do_not_leak(
        self, client, session, org_id, admin_session, shipped
    ) -> None:
        channel_id, order_id = shipped
        other = await seed_carrier(
            admin_session, uuid.uuid4(), provider="delhivery", token="tok-other"
        )
        assert (await _post(client, channel_id, scan_push(), "tok-other")).status_code == 400
        # the other tenant's connection accepts its own token but cannot move this tenant's parcel
        assert (await _post(client, other, scan_push(), "tok-other")).status_code == 200
        assert (await _shipment(session, org_id, order_id)).status == "PICKUP_HANDOVER"

    async def test_a_shiprocket_connection_does_not_answer_on_the_delhivery_path(
        self, client, org_id, admin_session, engines
    ) -> None:
        wrong = await seed_carrier(admin_session, org_id, provider="shiprocket")
        assert (await _post(client, wrong, scan_push())).status_code == 400


# =============================================================================== connect + live


class TestConnectAndBook:
    async def test_a_token_alone_connects_and_the_setup_path_is_delhivers(
        self, client, org_id, floor
    ) -> None:
        body = {
            "provider": "delhivery",
            "kind": "carrier",
            "name": "Delhivery Pune",
            "external_shop_id": PICKUP,
            "api_key": "dl-token-123456",
            "default_warehouse_id": str(floor.shop.warehouse_id),
        }
        with as_principal(principal_for(org_id, [WRITE])):
            created = await client.post("/api/v1/operations/channels", json=body)
            assert created.status_code == 201, created.text
            assert "dl-token-123456" not in created.text
            setup = await client.get(
                f"/api/v1/operations/channels/{created.json()['id']}/webhook-setup"
            )
        assert setup.json()["path"] == (
            f"/api/v1/webhooks/operations/delhivery?connection={created.json()['id']}"
        )

    async def test_connecting_needs_the_write_permission(self, client, org_id, floor) -> None:
        body = {
            "provider": "delhivery",
            "kind": "carrier",
            "name": "x",
            "external_shop_id": PICKUP,
            "api_key": "t",
            "default_warehouse_id": str(floor.shop.warehouse_id),
        }
        with as_principal(principal_for(org_id, [READ])):
            assert (await client.post("/api/v1/operations/channels", json=body)).status_code == 403

    async def test_live_booking_through_the_real_adapter_records_the_waybill(
        self, client, session, org_id, admin_session, floor, live, monkeypatch
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id, provider="delhivery")
        order_id, version = await _ready(client, session, org_id, floor)
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=CREATE_OK)

        adapter = _client(handler)
        monkeypatch.setattr(registry, "build_carrier_client", lambda provider, cred: adapter)
        assert settings().carrier_booking_mode == "live"
        response = await dispatch_order(
            client,
            org_id,
            order_id,
            version,
            carrier_name=None,
            tracking_reference=None,
            channel_id=channel_id,
            booking=BOOKING,
        )
        assert response.status_code == 201, response.text
        assert response.json()["tracking_reference"] == WAYBILL
        assert response.json()["external_shipment_id"] == WAYBILL
        assert len(seen) == 1
        data = json.loads(dict(urllib.parse.parse_qsl(seen[0].content.decode()))["data"])
        assert data["pickup_location"]["name"] == PICKUP
        row = await _row(
            admin_session, "SELECT status FROM operations.shipments WHERE order_id = :o", o=order_id
        )
        assert row["status"] == "PICKUP_HANDOVER"
