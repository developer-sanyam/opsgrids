"""Delhivery (Express, B2C), the customer's own account: the `CarrierClient` and the tracking
webhook verifier.

Wire facts are from Delhivery's public developer portal (one.delhivery.com/developer-portal):

* **Auth.** A static API token from the One Panel (Settings > API Setup), sent as
  `Authorization: Token <token>`. No login call; a 401 is `ChannelAuthError`.
* **Booking.** `POST /api/cmu/create.json`, form-encoded `format=json&data=<json>`, one shipment
  with `pickup_location.name` (the warehouse registered at Delhivery, case and space sensitive: the
  connection's `external_shop_id`). The `order` is our order number, which Delhivery requires to be
  unique, so a retried booking cannot make a second shipment. Success is `packages[0].waybill`.
  Delhivery has one id for a parcel, the waybill, so `external_shipment_id` is the AWB.
* **Cancel.** `POST /api/p/edit` with `{"waybill", "cancellation": "true"}`.
* **Webhook.** Delhivery's scan push is enabled by emailing them an endpoint and the authorization
  header they should send; the customer gives them the token shown on the Carriers page. This
  verifier requires `Authorization` to carry that token (as `Token <t>`, `Bearer <t>` or bare) and
  fails closed otherwise.

The base URL is platform configuration (`Settings.delhivery_api_base`), never a tenant value.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

import httpx

from app.channels.base import ChannelAuthError, ChannelRejected, ChannelUnavailable
from app.channels.carrier import (
    CarrierAck,
    CarrierBooking,
    CarrierBookingRequest,
    CarrierCredential,
    CarrierLabel,
    CarrierRate,
    CarrierTracking,
    CarrierTrackingEvent,
    ServiceabilityQuery,
)
from app.channels.webhooks import ParsedEvent

_MAX_RESPONSE_BYTES: Final = 2_000_000
_IST = ZoneInfo("Asia/Kolkata")

#: `(StatusType, Status)` Delhivery reports -> the platform's `Shipment.status`. From Delhivery's
#: documented status table: UD is a forward parcel, RT a return in progress, DL a final state.
#: Manifested, Not Picked and the reverse-pickup types (PP, PU, CN) move nothing.
_STATUS_MAP: Final[dict[tuple[str, str], str]] = {
    ("UD", "IN TRANSIT"): "IN_TRANSIT",
    ("UD", "PENDING"): "IN_TRANSIT",
    ("UD", "DISPATCHED"): "IN_TRANSIT",
    ("DL", "DELIVERED"): "DELIVERED",
    ("RT", "IN TRANSIT"): "IN_TRANSIT",
    ("RT", "PENDING"): "IN_TRANSIT",
    ("RT", "DISPATCHED"): "IN_TRANSIT",
    ("DL", "RTO"): "RETURNED",
}


def map_status(status_type: object, status: object) -> str | None:
    if not isinstance(status_type, str) or not isinstance(status, str):
        return None
    return _STATUS_MAP.get((status_type.strip().upper(), status.strip().upper()))


def _rupees(minor: int) -> str:
    return str(Decimal(minor) / 100)


def _minor(rupees: object) -> int:
    try:
        return int((Decimal(str(rupees)) * 100).to_integral_value())
    except ArithmeticError:
        return 0


def _parse_time(value: object) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return (
        when.replace(tzinfo=_IST).astimezone(UTC) if when.tzinfo is None else when.astimezone(UTC)
    )


class DelhiveryClient:
    """Satisfies `app.channels.carrier.CarrierClient`. A test hands in an `httpx.AsyncClient` over a
    `MockTransport`."""

    name = "delhivery"

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )
        self._timeout_seconds = timeout_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
        json_body: object = None,
    ) -> Any:
        try:
            response = await asyncio.wait_for(
                self._client.request(
                    method,
                    path,
                    params=params,
                    data=form,
                    json=json_body,
                    headers=self._headers,
                ),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ChannelUnavailable("delhivery timed out") from exc
        except httpx.HTTPError as exc:
            raise ChannelUnavailable(
                f"delhivery could not be reached: {type(exc).__name__}"
            ) from exc
        status = response.status_code
        if status in (401, 403):
            raise ChannelAuthError("delhivery rejected this connection's token")
        if status == 429 or status >= 500:
            raise ChannelUnavailable(f"delhivery answered {status}")
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise ChannelUnavailable("delhivery's response exceeded the size this client reads")
        try:
            body = response.json()
        except ValueError:
            body = {}
        if status >= 400:
            raise ChannelRejected("provider_rejected", _remark(body))
        return body

    # ------------------------------------------------------------------ CarrierClient

    async def check_serviceability(self, query: ServiceabilityQuery) -> list[CarrierRate]:
        body = await self._call(
            "GET", "/c/api/pin-codes/json/", params={"filter_codes": query.destination_pincode}
        )
        codes = body.get("delivery_codes") if isinstance(body, dict) else None
        if not codes:
            return []
        entry = codes[0].get("postal_code", {}) if isinstance(codes[0], dict) else {}
        key = "cod" if query.cod_amount_minor else "pre_paid"
        if entry.get(key) != "Y":
            return []
        charges = await self._call(
            "GET",
            "/api/kinko/v1/invoice/charges/.json",
            params={
                "md": "S",
                "ss": "Delivered",
                "o_pin": query.origin_pincode,
                "d_pin": query.destination_pincode,
                "cgm": str(query.weight_grams),
                "pt": "COD" if query.cod_amount_minor else "Pre-paid",
            },
        )
        first = charges[0] if isinstance(charges, list) and charges else charges
        total = first.get("total_amount") if isinstance(first, dict) else None
        return [CarrierRate("S", "Delhivery Surface", _minor(total or 0), "INR", None)]

    async def create_shipment(self, request: CarrierBookingRequest) -> CarrierBooking:
        cod = request.cod_amount_minor
        shipment = {
            "name": request.consignee_name,
            "add": request.consignee_address,
            "pin": request.destination_pincode,
            "city": request.consignee_city,
            "state": request.consignee_state,
            "country": "India",
            "phone": request.consignee_phone,
            "order": request.reference,
            "payment_mode": "COD" if cod else "Prepaid",
            "cod_amount": _rupees(cod) if cod else "0",
            "total_amount": _rupees(request.declared_value_minor),
            "products_desc": f"Order {request.reference}",
            "quantity": "1",
            "weight": str(request.weight_grams),
            "shipment_length": str(request.length_cm),
            "shipment_width": str(request.breadth_cm),
            "shipment_height": str(request.height_cm),
            "shipping_mode": "Surface",
        }
        payload = {"shipments": [shipment], "pickup_location": {"name": request.pickup_location}}
        body = await self._call(
            "POST",
            "/api/cmu/create.json",
            form={"format": "json", "data": json.dumps(payload)},
        )
        packages = body.get("packages") if isinstance(body, dict) else None
        package = packages[0] if isinstance(packages, list) and packages else {}
        waybill = package.get("waybill") if isinstance(package, dict) else None
        if not (isinstance(body, dict) and body.get("success")) or not waybill:
            raise ChannelRejected("booking_refused", _remark(package or body))
        return CarrierBooking(awb=str(waybill), external_shipment_id=str(waybill))

    async def get_label(self, external_shipment_id: str) -> CarrierLabel:
        body = await self._call(
            "GET",
            "/api/p/packing_slip",
            params={"wbns": external_shipment_id, "pdf": "true", "pdf_size": "4R"},
        )
        packages = body.get("packages") if isinstance(body, dict) else None
        first = packages[0] if isinstance(packages, list) and packages else body
        url = first.get("pdf_download_link") if isinstance(first, dict) else None
        if not url:
            raise ChannelRejected("label_unavailable", "the carrier has no label for this shipment")
        return CarrierLabel(url=str(url))

    async def cancel_shipment(self, external_shipment_id: str) -> CarrierAck:
        body = await self._call(
            "POST",
            "/api/p/edit",
            json_body={"waybill": external_shipment_id, "cancellation": "true"},
        )
        if isinstance(body, dict) and body.get("status") is False:
            raise ChannelRejected("cancel_refused", _remark(body))
        return CarrierAck(accepted=True)

    async def track(self, awb: str) -> CarrierTracking:
        body = await self._call("GET", "/api/v1/packages/json/", params={"waybill": awb})
        data = body.get("ShipmentData") if isinstance(body, dict) else None
        shipment = data[0].get("Shipment", {}) if isinstance(data, list) and data else {}
        events = []
        for scan in shipment.get("Scans", []) if isinstance(shipment, dict) else []:
            detail = scan.get("ScanDetail", {}) if isinstance(scan, dict) else {}
            status = map_status(detail.get("ScanType"), detail.get("Scan"))
            when = _parse_time(detail.get("ScanDateTime"))
            if status and when:
                events.append(
                    CarrierTrackingEvent(
                        status=status,
                        occurred_at=when,
                        location=str(detail.get("ScannedLocation", ""))[:200],
                    )
                )
        return CarrierTracking(awb=awb, events=sorted(events, key=lambda e: e.occurred_at))


def _remark(body: object) -> str:
    """The carrier's own reason, bounded: `remarks` (a list) on a package, else `rmk`/`message`."""
    if isinstance(body, dict):
        remarks = body.get("remarks")
        if isinstance(remarks, list) and remarks:
            return "; ".join(str(r) for r in remarks)[:200]
        for key in ("rmk", "remark", "message", "error"):
            if body.get(key):
                return str(body[key])[:200]
    return "the carrier refused this request"


class DelhiveryVerifier:
    """`WebhookVerifier` for Delhivery's scan push. The `Authorization` header must carry the
    connection's own webhook token; anything else, or a connection with no packed credential, is
    False. The scan is flattened to the scalars the inbox may keep."""

    provider = "delhivery"
    known_events = frozenset({"tracking.update"})
    allowed_fields = frozenset({"awb", "status", "status_type", "status_datetime", "reference"})

    def verify(self, *, headers: Mapping[str, str], body: bytes, secret: str) -> bool:
        credential = CarrierCredential.unpack(secret)
        presented = headers.get("authorization", "").strip()
        for prefix in ("Token ", "Bearer "):
            presented = presented.removeprefix(prefix)
        if credential is None or not credential.webhook_token or not presented:
            return False
        return hmac.compare_digest(presented.encode(), credential.webhook_token.encode())

    def parse(self, *, headers: Mapping[str, str], body: bytes) -> ParsedEvent | None:
        try:
            data = json.loads(body)
        except ValueError:
            return None
        shipment = data.get("Shipment") if isinstance(data, dict) else None
        status = shipment.get("Status") if isinstance(shipment, dict) else None
        if not isinstance(shipment, dict) or not isinstance(status, dict):
            return None
        awb = shipment.get("AWB")
        if not isinstance(awb, str) or not awb:
            return None
        fields = {
            "awb": awb,
            "status": status.get("Status"),
            "status_type": status.get("StatusType"),
            "status_datetime": status.get("StatusDateTime"),
            "reference": shipment.get("ReferenceNo"),
        }
        key = f"{awb}|{fields['status_type']}|{fields['status']}|{fields['status_datetime']}"
        return ParsedEvent(
            event_id=hashlib.sha256(key.encode()).hexdigest()[:40],
            event_name="tracking.update",
            fields=fields,
        )


def tracking_update(payload: Mapping[str, Any]) -> tuple[str, str, datetime] | None:
    """`(awb, platform status, occurred_at)` of a recorded Delhivery event, or None if it moves
    nothing."""
    awb = payload.get("awb")
    status = map_status(payload.get("status_type"), payload.get("status"))
    if not awb or status is None:
        return None
    return str(awb), status, _parse_time(payload.get("status_datetime")) or datetime.now(UTC)
