"""Shiprocket: the adapter against the vendor's documented examples, the tracking webhook, the
connect flow, and live booking's claim -> call -> record split.

Negative authorization and cross-tenant cases sit beside each positive one.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app import domain
from app.channels import registry
from app.channels.base import ChannelAuthError, ChannelRejected, ChannelUnavailable
from app.channels.carrier import (
    CarrierAck,
    CarrierBooking,
    CarrierBookingRequest,
    CarrierClient,
    CarrierCredential,
    CarrierLabel,
    CarrierRate,
    CarrierTracking,
    ServiceabilityQuery,
)
from app.channels.services import _aad
from app.channels.shiprocket import ShiprocketClient, ShiprocketVerifier, map_status
from app.config import settings
from app.models import Channel
from platform_core.pii import seal
from tests.fulfilment_fixtures import dispatch_order
from tests.helpers import as_principal, principal_for
from tests.test_integrations_framework import (  # noqa: F401 -- fixtures
    _ready,
    _shipment,
    engines,
    floor,
    no_network,
    post_webhook,
    stored_events,
)

# ruff: noqa: F811, S107 -- shared fixtures are imported; the tokens are test values
pytestmark = pytest.mark.integration

BOOKING = {
    "consignee_name": "Asha Verma",
    "consignee_phone": "9876543210",
    "consignee_email": "asha@example.com",
    "consignee_address": "12 MG Road",
    "consignee_city": "Bengaluru",
    "consignee_state": "Karnataka",
    "destination_pincode": "560001",
    "weight_grams": 500,
}
WRITE = "operations.channels.channel.write:ORG"
READ = "operations.channels.channel.read:ORG"
PICKUP = "Main Warehouse"

# --- recorded examples from apidocs.shiprocket.in ------------------------------------------------
DOC_FORWARD_OK = {
    "status": 1,
    "payload": {
        "order_created": 1,
        "awb_generated": 1,
        "order_id": 222521420,
        "shipment_id": 222002884,
        "awb_code": "14326421307048",
        "courier_company_id": 25,
        "courier_name": "Xpressbees 5kg",
        "label_url": "https://kr-shipmultichannel.s3.ap-southeast-1.amazonaws.com/25149/labels/x.pdf",
    },
}
DOC_FORWARD_REFUSED = {
    "status": 0,
    "payload": {"action": "Adding pickup location", "error_message": "Oops! Invalid Data."},
}
DOC_TRACK = {
    "tracking_data": {
        "track_status": 1,
        "shipment_status": 7,
        "shipment_track_activities": [
            {
                "date": "2022-07-19 11:37:00",
                "status": "DLVD",
                "location": "MADANPALLI, ANDHRA PRADESH",
                "sr-status": "7",
                "sr-status-label": "DELIVERED",
            },
            {
                "date": "2022-07-18 21:02:00",
                "status": "IT",
                "location": "BLR/FC1, BANGALORE",
                "sr-status": "18",
                "sr-status-label": "IN TRANSIT",
            },
            {
                "date": "2022-07-18 10:00:00",
                "status": "X",
                "location": "",
                "sr-status": "NA",
                "sr-status-label": "NA",
            },
        ],
    }
}
DOC_WEBHOOK = {
    "awb": "19041424751540",
    "courier_name": "Delhivery Surface",
    "current_status": "IN TRANSIT",
    "current_status_id": 18,
    "shipment_status": "IN TRANSIT",
    "shipment_status_id": 18,
    "current_timestamp": "23 05 2023 11:43:52",
    "order_id": "1373900_150876814",
    "sr_order_id": 348456385,
    "scans": [{"date": "2023-05-19 11:59:16", "activity": "Manifested", "location": "Chomu"}],
}
SLUG = "parcel-track"


def _client(handler: Any) -> ShiprocketClient:
    return ShiprocketClient(
        base_url="https://sr.test/v1/external",
        email="api@acme.test",
        password="s3cret-pw",  # pragma: allowlist secret
        client=httpx.AsyncClient(
            base_url="https://sr.test/v1/external", transport=httpx.MockTransport(handler)
        ),
    )


def _router(routes: dict[tuple[str, str], tuple[int, Any]], seen: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path.removeprefix("/v1/external")
        if path == "/auth/login":
            return httpx.Response(200, json={"token": "tok-1"})
        status, body = routes[(request.method, path)]
        return httpx.Response(status, json=body)

    return handler


def _request(**over: Any) -> CarrierBookingRequest:
    base: dict[str, Any] = {
        "reference": "ORD-2026-0001",
        "service_code": "",
        "origin_pincode": "",
        "destination_pincode": "560001",
        "weight_grams": 500,
        "consignee_name": "Asha Verma",
        "consignee_phone": "9876543210",
        "consignee_address": "12 MG Road",
        "consignee_city": "Bengaluru",
        "consignee_state": "Karnataka",
        "consignee_email": "asha@example.com",
        "pickup_location": PICKUP,
        "order_date": "2026-10-16",
        "declared_value_minor": 125050,
    }
    return CarrierBookingRequest(**{**base, **over})


# =============================================================================== the adapter


class TestAdapter:
    def test_it_satisfies_the_carrier_protocol(self) -> None:
        assert isinstance(_client(lambda r: httpx.Response(200)), CarrierClient)

    async def test_create_builds_the_documented_request_and_reads_the_documented_reply(
        self,
    ) -> None:
        seen: list[httpx.Request] = []
        routes = {("POST", "/shipments/create/forward-shipment"): (200, DOC_FORWARD_OK)}
        booking = await _client(_router(routes, seen)).create_shipment(
            _request(cod_amount_minor=125050, service_code="25")
        )
        login, create = seen
        assert json.loads(login.content) == {"email": "api@acme.test", "password": "s3cret-pw"}
        assert create.headers["authorization"] == "Bearer tok-1"
        body = json.loads(create.content)
        assert body["order_id"] == "ORD-2026-0001"
        assert body["pickup_location"] == PICKUP
        assert body["billing_pincode"] == "560001"
        assert body["shipping_is_billing"] is True
        assert body["payment_method"] == "COD"
        assert body["sub_total"] == 1250.5
        assert body["weight"] == 0.5
        assert body["courier_id"] == 25
        assert body["order_items"][0]["selling_price"] == 1250.5
        assert booking.awb == "14326421307048"
        assert booking.external_shipment_id == "222002884:14326421307048"
        assert booking.label_url and booking.label_url.endswith("x.pdf")

    async def test_a_prepaid_booking_says_prepaid(self) -> None:
        seen: list[httpx.Request] = []
        routes = {("POST", "/shipments/create/forward-shipment"): (200, DOC_FORWARD_OK)}
        await _client(_router(routes, seen)).create_shipment(_request())
        assert json.loads(seen[1].content)["payment_method"] == "Prepaid"
        assert "courier_id" not in json.loads(seen[1].content)

    async def test_a_refused_booking_is_rejected_not_retried(self) -> None:
        routes = {("POST", "/shipments/create/forward-shipment"): (200, DOC_FORWARD_REFUSED)}
        with pytest.raises(ChannelRejected) as excinfo:
            await _client(_router(routes, [])).create_shipment(_request())
        assert "Invalid Data" in excinfo.value.message

    async def test_a_422_is_rejected_with_the_carriers_message(self) -> None:
        routes = {
            ("POST", "/shipments/create/forward-shipment"): (
                422,
                {"message": "Oops! Invalid Data.", "status_code": 422},
            )
        }
        with pytest.raises(ChannelRejected):
            await _client(_router(routes, [])).create_shipment(_request())

    @pytest.mark.parametrize(
        ("status", "error"),
        [(500, ChannelUnavailable), (429, ChannelUnavailable), (401, ChannelAuthError)],
    )
    async def test_failures_map_to_the_three_channel_shapes(self, status: int, error: type) -> None:
        routes = {("POST", "/shipments/create/forward-shipment"): (status, {"message": "x"})}
        with pytest.raises(error):
            await _client(_router(routes, [])).create_shipment(_request())

    async def test_a_rejected_login_is_an_auth_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "Invalid email or password"})

        with pytest.raises(ChannelAuthError):
            await _client(handler).create_shipment(_request())

    async def test_serviceability_lists_rates_cheapest_first(self) -> None:
        reply = {
            "data": {
                "available_courier_companies": [
                    {
                        "courier_company_id": 43,
                        "courier_name": "Delhivery Surface",
                        "rate": 54,
                        "estimated_delivery_days": "4",
                    },
                    {"courier_company_id": 225, "courier_name": "India Post", "rate": 41.5},
                ]
            }
        }
        seen: list[httpx.Request] = []
        rates = await _client(
            _router({("GET", "/courier/serviceability/"): (200, reply)}, seen)
        ).check_serviceability(ServiceabilityQuery("110030", "122002", 2000, cod_amount_minor=100))
        assert [r.service_code for r in rates] == ["225", "43"]
        assert rates[0] == CarrierRate("225", "India Post", 4150, "INR", None)
        assert rates[1].estimated_days == 4
        query = dict(seen[1].url.params)
        assert query == {
            "pickup_postcode": "110030",
            "delivery_postcode": "122002",
            "weight": "2.0",
            "cod": "1",
        }

    async def test_an_unserved_lane_is_an_empty_list(self) -> None:
        routes = {("GET", "/courier/serviceability/"): (404, {"message": "no couriers"})}
        client = _client(_router(routes, []))
        assert await client.check_serviceability(ServiceabilityQuery("1", "2", 1)) == []

    async def test_label_and_cancel_use_the_shipment_id_and_the_awb(self) -> None:
        seen: list[httpx.Request] = []
        routes = {
            ("POST", "/courier/generate/label"): (
                200,
                {"label_created": 1, "label_url": "https://l/x.pdf"},
            ),
            ("POST", "/orders/cancel/shipment/awbs"): (200, {"message": "in progress"}),
        }
        client = _client(_router(routes, seen))
        label = await client.get_label("222002884:14326421307048")
        ack = await client.cancel_shipment("222002884:14326421307048")
        assert label == CarrierLabel(url="https://l/x.pdf")
        assert ack == CarrierAck(accepted=True)
        assert json.loads(seen[1].content) == {"shipment_id": [222002884]}
        assert json.loads(seen[2].content) == {"awbs": ["14326421307048"]}

    async def test_a_malformed_reference_is_rejected_before_any_call(self) -> None:
        with pytest.raises(ChannelRejected):
            await _client(lambda r: httpx.Response(500)).cancel_shipment("not-a-reference")

    async def test_tracking_is_oldest_first_in_platform_statuses(self) -> None:
        routes = {("GET", "/courier/track/awb/788830567028"): (200, DOC_TRACK)}
        tracking = await _client(_router(routes, [])).track("788830567028")
        assert isinstance(tracking, CarrierTracking)
        assert [e.status for e in tracking.events] == ["IN_TRANSIT", "DELIVERED"]
        assert tracking.events[0].occurred_at == datetime(2022, 7, 18, 15, 32, tzinfo=UTC)

    def test_status_mapping(self) -> None:
        assert map_status("Out For Delivery") == "IN_TRANSIT"
        assert map_status("RTO DELIVERED") == "RETURNED"
        assert map_status("UNDELIVERED") == "FAILED"
        assert map_status("MANIFEST GENERATED") is None
        assert map_status(None) is None


# =============================================================================== the verifier


class TestVerifier:
    cred = CarrierCredential("a@b.test", "pw", "tok-abc")
    verifier = ShiprocketVerifier()

    def test_only_the_connections_own_token_passes(self) -> None:
        packed = self.cred.pack()
        ok = self.verifier.verify(headers={"x-api-key": "tok-abc"}, body=b"{}", secret=packed)
        assert ok is True

    @pytest.mark.parametrize(
        "headers", [{}, {"x-api-key": ""}, {"x-api-key": "tok-abd"}, {"x-api-key": "tok-abc "}]
    )
    def test_a_missing_or_wrong_token_fails_closed(self, headers: dict[str, str]) -> None:
        assert not self.verifier.verify(headers=headers, body=b"{}", secret=self.cred.pack())

    def test_a_connection_without_a_packed_credential_fails_closed(self) -> None:
        assert not self.verifier.verify(
            headers={"x-api-key": "plain-key"}, body=b"{}", secret="plain-key"
        )

    def test_the_event_id_is_stable_per_awb_status_and_time(self) -> None:
        body = json.dumps(DOC_WEBHOOK).encode()
        a, b = (self.verifier.parse(headers={}, body=body) for _ in range(2))
        assert a and b and a.event_id == b.event_id and len(a.event_id) == 40
        other = self.verifier.parse(
            headers={}, body=json.dumps({**DOC_WEBHOOK, "current_status_id": 7}).encode()
        )
        assert other and other.event_id != a.event_id

    def test_a_body_without_an_awb_is_unidentifiable(self) -> None:
        assert self.verifier.parse(headers={}, body=b'{"current_status": "X"}') is None
        assert self.verifier.parse(headers={}, body=b"not json") is None


# =============================================================================== helpers (db)


async def seed_carrier(
    admin_session: Any,
    org: uuid.UUID,
    *,
    status: str = "connected",
    token: str = "tok-abc",
    provider: str = "shiprocket",
) -> uuid.UUID:
    channel_id = uuid.uuid4()
    packed = CarrierCredential("api@acme.test", "pw", token).pack()
    enc, _ = seal(packed, kind="secret", settings=settings(), aad=_aad(org, channel_id))
    async with admin_session.begin():
        await admin_session.execute(
            Channel.__table__.insert().values(
                id=channel_id,
                organization_id=org,
                provider=provider,
                kind="carrier",
                name="Shiprocket",
                status=status,
                external_shop_id=PICKUP,
                api_key_enc=enc,
                api_key_masked="****.test",
                default_currency="INR",
                auto_accept_orders=False,
                consecutive_failures=0,
            )
        )
    return channel_id


class FakeCarrier:
    """A scripted courier. `on_call` runs while the booking call is in flight."""

    name = "fake"

    def __init__(self, outcomes: list[Any], on_call: Any = None) -> None:
        self.outcomes = outcomes
        self.on_call = on_call
        self.calls: list[CarrierBookingRequest] = []

    async def create_shipment(self, request: CarrierBookingRequest) -> CarrierBooking:
        self.calls.append(request)
        if self.on_call:
            await self.on_call()
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[no-any-return]

    async def check_serviceability(self, query: ServiceabilityQuery) -> list[CarrierRate]:
        return []

    async def get_label(self, external_shipment_id: str) -> CarrierLabel:
        return CarrierLabel()

    async def cancel_shipment(self, external_shipment_id: str) -> CarrierAck:
        return CarrierAck(accepted=True)

    async def track(self, awb: str) -> CarrierTracking:
        return CarrierTracking(awb=awb)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(settings(), "carrier_booking_mode", "live")

    def install(fake: FakeCarrier) -> FakeCarrier:
        monkeypatch.setattr(registry, "build_carrier_client", lambda provider, cred: fake)
        return fake

    return install


async def _row(admin_session: Any, sql: str, **params: Any) -> Any:
    async with admin_session.begin():
        return (await admin_session.execute(text(sql), params)).mappings().one_or_none()


GOOD = CarrierBooking(awb="AWB-1", external_shipment_id="900:AWB-1", label_url="https://l")


# =============================================================================== live booking


class TestLiveBooking:
    async def test_claim_call_record_books_and_hands_over(
        self, client, session, org_id, admin_session, floor, live
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id)
        order_id, version = await _ready(client, session, org_id, floor)
        observed: dict[str, Any] = {}

        async def while_calling() -> None:
            # The claim is committed and visible; the order row is not locked by the request.
            observed["shipment"] = await _row(
                admin_session,
                "SELECT status FROM operations.shipments WHERE order_id = :o",
                o=order_id,
            )
            async with admin_session.begin():
                await admin_session.execute(
                    text("SELECT 1 FROM operations.orders WHERE id = :o FOR UPDATE NOWAIT"),
                    {"o": order_id},
                )
                observed["order_unlocked"] = True

        fake = live(FakeCarrier([GOOD], while_calling))
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
        body = response.json()
        assert body["status"] == "PICKUP_HANDOVER"
        assert body["tracking_reference"] == "AWB-1"
        assert body["external_shipment_id"] == "900:AWB-1"
        assert observed["shipment"]["status"] == "BOOKING"
        assert observed["order_unlocked"] is True
        assert len(fake.calls) == 1
        request = fake.calls[0]
        assert request.reference.startswith("ORD-")
        assert request.pickup_location == PICKUP
        assert request.destination_pincode == "560001"
        order = await _row(
            admin_session, "SELECT status FROM operations.orders WHERE id = :o", o=order_id
        )
        assert order["status"] == "DELIVERED"

    async def test_a_failed_call_marks_the_shipment_failed_and_retry_books_once(
        self, client, session, org_id, admin_session, floor, live
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id)
        order_id, version = await _ready(client, session, org_id, floor)
        fake = live(FakeCarrier([ChannelUnavailable("timed out"), GOOD]))

        def post() -> Any:
            return dispatch_order(
                client,
                org_id,
                order_id,
                version,
                carrier_name=None,
                tracking_reference=None,
                channel_id=channel_id,
                booking=BOOKING,
            )

        first = await post()
        assert first.status_code == 503, first.text
        shipment = await _shipment(session, org_id, order_id)
        assert shipment.status == "BOOKING_FAILED"
        assert shipment.failure_reason
        order = await _row(
            admin_session, "SELECT status FROM operations.orders WHERE id = :o", o=order_id
        )
        assert order["status"] == "READY"  # nothing was handed over

        second = await post()
        assert second.status_code == 201, second.text
        assert second.json()["id"] == str(shipment.id)  # the same row, taken back
        assert second.json()["status"] == "PICKUP_HANDOVER"
        assert len(fake.calls) == 2
        count = await _row(
            admin_session,
            "SELECT count(*) AS n FROM operations.shipments WHERE order_id = :o",
            o=order_id,
        )
        assert count["n"] == 1

    async def test_a_dispatched_order_is_never_booked_again(
        self, client, session, org_id, admin_session, floor, live
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id)
        order_id, version = await _ready(client, session, org_id, floor)
        fake = live(FakeCarrier([GOOD, GOOD]))
        for expected in (201, 409):
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
            assert response.status_code == expected, response.text
        assert len(fake.calls) == 1

    async def test_a_fresh_claim_blocks_a_second_booking(
        self, client, session, org_id, admin_session, floor, live
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id)
        order_id, version = await _ready(client, session, org_id, floor)
        fake = live(FakeCarrier([GOOD]))
        responses: list[Any] = []

        async def while_calling() -> None:
            # A second request arrives while the first is at the carrier.
            responses.append(
                await dispatch_order(
                    client,
                    org_id,
                    order_id,
                    version,
                    carrier_name=None,
                    tracking_reference=None,
                    channel_id=channel_id,
                    booking=BOOKING,
                )
            )

        fake.on_call = while_calling
        first = await dispatch_order(
            client,
            org_id,
            order_id,
            version,
            carrier_name=None,
            tracking_reference=None,
            channel_id=channel_id,
            booking=BOOKING,
        )
        assert first.status_code == 201, first.text
        assert responses[0].status_code == 409, responses[0].text
        assert len(fake.calls) == 1

    async def test_a_stale_claim_can_be_taken_over(
        self, client, session, org_id, admin_session, floor, live
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id)
        order_id, version = await _ready(client, session, org_id, floor)
        live(FakeCarrier([ChannelUnavailable("x")]))
        await dispatch_order(
            client,
            org_id,
            order_id,
            version,
            carrier_name=None,
            tracking_reference=None,
            channel_id=channel_id,
            booking=BOOKING,
        )
        # Simulate a crashed worker: the row is stuck in BOOKING and old.
        async with admin_session.begin():
            await admin_session.execute(
                text(
                    "UPDATE operations.shipments SET status = 'BOOKING', "
                    "updated_at = now() - interval '10 minutes' WHERE order_id = :o"
                ),
                {"o": order_id},
            )
        fake = live(FakeCarrier([GOOD]))
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
        assert len(fake.calls) == 1

    async def test_an_order_that_moved_during_the_call_keeps_the_awb_and_retry_does_not_rebook(
        self, client, session, org_id, admin_session, floor, live
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id)
        order_id, version = await _ready(client, session, org_id, floor)

        async def order_moves() -> None:
            async with admin_session.begin():
                await admin_session.execute(
                    text("UPDATE operations.orders SET version = version + 1 WHERE id = :o"),
                    {"o": order_id},
                )

        fake = live(FakeCarrier([GOOD], order_moves))
        first = await dispatch_order(
            client,
            org_id,
            order_id,
            version,
            carrier_name=None,
            tracking_reference=None,
            channel_id=channel_id,
            booking=BOOKING,
        )
        assert first.status_code == 409, first.text
        shipment = await _shipment(session, org_id, order_id)
        assert shipment.status == "BOOKING_FAILED"
        assert shipment.tracking_reference == "AWB-1"  # kept, so it can be cancelled at the carrier
        assert "AWB-1" in (shipment.failure_reason or "")

        second = await dispatch_order(
            client,
            org_id,
            order_id,
            version + 1,
            carrier_name=None,
            tracking_reference=None,
            channel_id=channel_id,
            booking=BOOKING,
        )
        assert second.status_code == 201, second.text
        assert len(fake.calls) == 1  # the booking already made was reused
        assert second.json()["tracking_reference"] == "AWB-1"

    @pytest.mark.parametrize(
        ("error", "code"),
        [(ChannelRejected("bad", "pincode not served"), 422), (ChannelAuthError("no"), 409)],
    )
    async def test_a_refusal_or_bad_credential_fails_the_booking_without_a_handover(
        self, client, session, org_id, admin_session, floor, live, error, code
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id)
        order_id, version = await _ready(client, session, org_id, floor)
        live(FakeCarrier([error]))
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
        assert response.status_code == code, response.text
        assert (await _shipment(session, org_id, order_id)).status == "BOOKING_FAILED"
        channel = await _row(
            admin_session, "SELECT status FROM operations.channels WHERE id = :c", c=channel_id
        )
        assert channel["status"] == ("error" if code == 409 else "connected")

    async def test_booking_details_are_required_in_live_mode(
        self, client, session, org_id, admin_session, floor, live
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id)
        order_id, version = await _ready(client, session, org_id, floor)
        fake = live(FakeCarrier([GOOD]))
        response = await dispatch_order(
            client, org_id, order_id, version, carrier_name=None, channel_id=channel_id
        )
        assert response.status_code == 422
        assert fake.calls == []

    async def test_a_bad_pincode_is_refused_at_the_boundary(
        self, client, session, org_id, admin_session, floor, live
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id)
        order_id, version = await _ready(client, session, org_id, floor)
        fake = live(FakeCarrier([GOOD]))
        response = await dispatch_order(
            client,
            org_id,
            order_id,
            version,
            carrier_name=None,
            channel_id=channel_id,
            booking={**BOOKING, "destination_pincode": "56"},
        )
        assert response.status_code == 422
        assert fake.calls == []

    async def test_booking_needs_the_dispatch_write_permission(
        self, client, session, org_id, admin_session, floor, live
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id)
        order_id, version = await _ready(client, session, org_id, floor)
        fake = live(FakeCarrier([GOOD]))
        response = await dispatch_order(
            client,
            org_id,
            order_id,
            version,
            carrier_name=None,
            channel_id=channel_id,
            booking=BOOKING,
            perms=["operations.dispatch.shipment.read:ORG"],
        )
        assert response.status_code == 403
        assert fake.calls == []

    async def test_another_organizations_carrier_is_not_found_and_not_called(
        self, client, session, org_id, admin_session, floor, live
    ) -> None:
        foreign = await seed_carrier(admin_session, uuid.uuid4())
        order_id, version = await _ready(client, session, org_id, floor)
        fake = live(FakeCarrier([GOOD]))
        response = await dispatch_order(
            client,
            org_id,
            order_id,
            version,
            carrier_name=None,
            channel_id=foreign,
            booking=BOOKING,
        )
        assert response.status_code == 404
        assert fake.calls == []
        count = await _row(
            admin_session,
            "SELECT count(*) AS n FROM operations.shipments WHERE order_id = :o",
            o=order_id,
        )
        assert count["n"] == 0

    async def test_a_booking_state_has_no_manual_transition(self) -> None:
        for state in ("BOOKING", "BOOKING_FAILED"):
            assert not domain.can_transition_shipment(state, "IN_TRANSIT")


class TestLogOnly:
    async def test_log_only_makes_no_network_call_even_with_booking_details(
        self, client, session, org_id, admin_session, floor, no_network
    ) -> None:
        assert settings().carrier_booking_mode == "log_only"
        channel_id = await seed_carrier(admin_session, org_id)
        order_id, version = await _ready(client, session, org_id, floor)
        response = await dispatch_order(
            client,
            org_id,
            order_id,
            version,
            carrier_name=None,
            tracking_reference="MANUAL-9",
            channel_id=channel_id,
            booking=BOOKING,
        )
        assert response.status_code == 201, response.text
        assert response.json()["tracking_reference"] == "MANUAL-9"
        assert response.json()["status"] == "PICKUP_HANDOVER"


# =============================================================================== connect


class TestConnect:
    async def _warehouse(self, floor) -> str:  # type: ignore[no-untyped-def]
        return str(floor.shop.warehouse_id)

    def _body(self, warehouse: str, **over: Any) -> dict[str, Any]:
        return {
            "provider": "shiprocket",
            "kind": "carrier",
            "name": "Our Shiprocket",
            "external_shop_id": PICKUP,
            "api_key": "api@acme.test",
            "api_secret": "pw-123456",  # pragma: allowlist secret
            "default_warehouse_id": warehouse,
            **over,
        }

    async def test_connecting_seals_both_halves_and_never_returns_them(
        self, client, org_id, floor, admin_session
    ) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            response = await client.post(
                "/api/v1/operations/channels", json=self._body(await self._warehouse(floor))
            )
        assert response.status_code == 201, response.text
        assert "pw-123456" not in response.text
        assert response.json()["kind"] == "carrier"
        row = await _row(
            admin_session,
            "SELECT api_key_masked, api_key_enc FROM operations.channels WHERE id = :c",
            c=uuid.UUID(response.json()["id"]),
        )
        assert "pw-123456" not in row["api_key_masked"]
        assert b"pw-123456" not in bytes(row["api_key_enc"])

    @pytest.mark.parametrize(
        "over", [{"api_secret": None}, {"kind": "sales"}, {"provider": "nope"}]
    )
    async def test_an_incomplete_or_mismatched_connection_is_refused(
        self, client, org_id, floor, over
    ) -> None:
        with as_principal(principal_for(org_id, [WRITE])):
            response = await client.post(
                "/api/v1/operations/channels",
                json=self._body(await self._warehouse(floor), **over),
            )
        assert response.status_code == 422, response.text

    async def test_connecting_needs_the_write_permission(self, client, org_id, floor) -> None:
        with as_principal(principal_for(org_id, [READ])):
            response = await client.post(
                "/api/v1/operations/channels", json=self._body(await self._warehouse(floor))
            )
        assert response.status_code == 403

    async def test_webhook_setup_reveals_the_token_audited_and_scoped(
        self, client, org_id, admin_session
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id, token="tok-reveal")
        with as_principal(principal_for(org_id, [WRITE])):
            response = await client.get(f"/api/v1/operations/channels/{channel_id}/webhook-setup")
        assert response.status_code == 200, response.text
        assert response.json() == {
            "path": f"/api/v1/webhooks/operations/{SLUG}?connection={channel_id}",
            "token": "tok-reveal",
        }
        audit = await _row(
            admin_session,
            "SELECT count(*) AS n, bool_or(after::text LIKE '%tok-reveal%') AS leaked "
            "FROM operations.audit_logs WHERE action = 'channel.webhook_setup.revealed'",
        )
        assert audit["n"] == 1 and not audit["leaked"]

    async def test_webhook_setup_is_not_for_read_only_callers_or_other_tenants(
        self, client, org_id, admin_session
    ) -> None:
        mine = await seed_carrier(admin_session, org_id)
        theirs = await seed_carrier(admin_session, uuid.uuid4())
        with as_principal(principal_for(org_id, [READ])):
            denied = await client.get(f"/api/v1/operations/channels/{mine}/webhook-setup")
        with as_principal(principal_for(org_id, [WRITE])):
            foreign = await client.get(f"/api/v1/operations/channels/{theirs}/webhook-setup")
        assert denied.status_code == 403
        assert foreign.status_code == 404

    async def test_reconnecting_keeps_the_webhook_token(
        self, client, org_id, admin_session
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id, status="error", token="tok-keep")
        with as_principal(principal_for(org_id, [WRITE])):
            done = await client.post(
                f"/api/v1/operations/channels/{channel_id}/reconnect",
                json={
                    "api_key": "new@acme.test",
                    "api_secret": "new-pw",
                },  # pragma: allowlist secret
            )
            setup = await client.get(f"/api/v1/operations/channels/{channel_id}/webhook-setup")
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "connected"
        assert setup.json()["token"] == "tok-keep"


# =============================================================================== the webhook


def _hook(body: dict[str, Any], token: str | None = "tok-abc") -> tuple[bytes, dict[str, str]]:
    headers = {"content-type": "application/json"}
    if token is not None:
        headers["x-api-key"] = token
    return json.dumps(body).encode(), headers


async def _post(
    client: Any, channel_id: uuid.UUID, body: dict[str, Any], token: str | None = "tok-abc"
) -> Any:
    raw, headers = _hook(body, token)
    return await client.post(
        f"/api/v1/webhooks/operations/{SLUG}?connection={channel_id}", content=raw, headers=headers
    )


@pytest_asyncio.fixture
async def shipped(
    client, session, org_id, admin_session, floor, engines
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """A shipment dispatched through a Shiprocket connection under AWB 19041424751540."""
    channel_id = await seed_carrier(admin_session, org_id)
    order_id, version = await _ready(client, session, org_id, floor)
    response = await dispatch_order(
        client,
        org_id,
        order_id,
        version,
        carrier_name=None,
        tracking_reference=DOC_WEBHOOK["awb"],
        channel_id=channel_id,
    )
    assert response.status_code == 201, response.text
    yield channel_id, order_id


class TestWebhook:
    async def test_a_verified_event_is_recorded_then_moves_the_shipment(
        self, client, session, org_id, admin_session, shipped
    ) -> None:
        channel_id, order_id = shipped
        response = await _post(client, channel_id, DOC_WEBHOOK)
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "received"
        events = await stored_events(admin_session)
        assert len(events) == 1
        assert events[0]["event_name"] == "tracking.update"
        assert "scans" not in events[0]["payload"]  # minimised to the allow-list
        assert (await _shipment(session, org_id, order_id)).status == "IN_TRANSIT"

    async def test_a_redelivery_records_once_and_changes_nothing_more(
        self, client, session, org_id, admin_session, shipped
    ) -> None:
        channel_id, _order_id = shipped
        await _post(client, channel_id, DOC_WEBHOOK)
        again = await _post(client, channel_id, DOC_WEBHOOK)
        assert again.status_code == 200 and again.json()["duplicate"] is True
        assert len(await stored_events(admin_session)) == 1
        transitions = await _row(
            admin_session, "SELECT count(*) AS n FROM operations.shipment_transitions"
        )
        assert transitions["n"] == 1

    async def test_delivered_walks_through_in_transit(
        self, client, session, org_id, admin_session, shipped
    ) -> None:
        channel_id, order_id = shipped
        done = {**DOC_WEBHOOK, "current_status": "DELIVERED", "current_status_id": 7}
        assert (await _post(client, channel_id, done)).status_code == 200
        shipment = await _shipment(session, org_id, order_id)
        assert shipment.status == "DELIVERED" and shipment.delivered_at is not None

    async def test_a_stale_report_is_recorded_but_does_not_regress_the_shipment(
        self, client, session, org_id, admin_session, shipped
    ) -> None:
        channel_id, order_id = shipped
        done = {**DOC_WEBHOOK, "current_status": "DELIVERED", "current_status_id": 7}
        await _post(client, channel_id, done)
        late = {**DOC_WEBHOOK, "current_timestamp": "22 05 2023 09:00:00"}
        assert (await _post(client, channel_id, late)).status_code == 200
        assert (await _shipment(session, org_id, order_id)).status == "DELIVERED"

    @pytest.mark.parametrize("token", [None, "", "wrong-token"])
    async def test_a_missing_or_wrong_token_is_a_400_and_records_nothing(
        self, client, session, org_id, admin_session, shipped, token
    ) -> None:
        channel_id, order_id = shipped
        response = await _post(client, channel_id, DOC_WEBHOOK, token)
        assert response.status_code == 400, response.text
        assert await stored_events(admin_session) == []
        assert (await _shipment(session, org_id, order_id)).status == "PICKUP_HANDOVER"

    async def test_another_tenants_token_does_not_open_this_connection(
        self, client, org_id, admin_session, shipped
    ) -> None:
        channel_id, _ = shipped
        other = await seed_carrier(admin_session, uuid.uuid4(), token="tok-other")
        assert (await _post(client, channel_id, DOC_WEBHOOK, "tok-other")).status_code == 400
        # and the other tenant's connection never sees this tenant's parcel
        assert (await _post(client, other, DOC_WEBHOOK, "tok-other")).status_code == 200
        assert len(await stored_events(admin_session)) == 1

    async def test_an_event_for_another_tenants_awb_moves_nothing(
        self, client, session, org_id, admin_session, shipped
    ) -> None:
        _, order_id = shipped
        other = await seed_carrier(admin_session, uuid.uuid4(), token="tok-other")
        await _post(client, other, DOC_WEBHOOK, "tok-other")
        assert (await _shipment(session, org_id, order_id)).status == "PICKUP_HANDOVER"

    async def test_a_disconnected_connection_and_unknown_slug_answer_400(
        self, client, org_id, admin_session, engines
    ) -> None:
        gone = await seed_carrier(admin_session, org_id, status="disconnected")
        assert (await _post(client, gone, DOC_WEBHOOK)).status_code == 400
        raw, headers = _hook(DOC_WEBHOOK)
        unknown = await client.post(
            f"/api/v1/webhooks/operations/nope?connection={gone}", content=raw, headers=headers
        )
        assert unknown.status_code == 400

    async def test_an_oversized_body_is_refused(
        self, client, org_id, admin_session, engines
    ) -> None:
        channel_id = await seed_carrier(admin_session, org_id)
        big = {**DOC_WEBHOOK, "pad": "x" * (300 * 1024)}
        assert (await _post(client, channel_id, big)).status_code == 413
