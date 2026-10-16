"""Shiprocket, the customer's own account: the `CarrierClient` and the tracking-webhook verifier.

Wire facts are from Shiprocket's public API reference (apidocs.shiprocket.in):

* **Auth.** `POST /auth/login` with the account's API-user `email` and `password` returns a bearer
  `token` (valid for days). The client logs in once per instance and sends `Authorization: Bearer`.
  A rejected login or a 401 is `ChannelAuthError`.
* **Booking** is the "wrapper" `POST /shipments/create/forward-shipment`: one call creates the
  order, assigns a courier, generates the AWB and the label. Its `order_id` is our own order number,
  Shiprocket's client reference. Success is `payload.awb_code` + `payload.shipment_id`.
* **Webhook.** Shiprocket signs nothing. It sends an optional static token in `x-api-key`; this
  verifier requires it (fail closed) and compares it to the per-connection token generated at
  connect (`CarrierCredential.webhook_token`). The path may not contain "shiprocket" or "sr"/"kr"
  (Shiprocket's rule), hence the `parcel-track` slug (`app.channels.webhooks.WEBHOOK_SLUGS`).

The base URL is platform configuration (`Settings.shiprocket_api_base`), never a tenant value, so
this does not go through the egress guard (ADR-0016; classified in `tests/infra`).

`external_shipment_id` is `"<shipment_id>:<awb>"`: label generation asks for the shipment id and
cancellation for the AWB, and the platform stores one string.
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

#: Shiprocket's status label (`sr-status-label` / webhook `current_status`) -> the platform's
#: `Shipment.status`. Labels that mean "still moving" are IN_TRANSIT; anything unlisted (manifest
#: generated, cancelled, `NA`) moves nothing.
_STATUS_MAP: Final[dict[str, str]] = {
    "PICKED UP": "IN_TRANSIT",
    "SHIPPED": "IN_TRANSIT",
    "IN TRANSIT": "IN_TRANSIT",
    "REACHED AT DESTINATION HUB": "IN_TRANSIT",
    "OUT FOR DELIVERY": "IN_TRANSIT",
    "DELIVERED": "DELIVERED",
    "UNDELIVERED": "FAILED",
    "RTO DELIVERED": "RETURNED",
}


def map_status(label: object) -> str | None:
    return _STATUS_MAP.get(label.strip().upper()) if isinstance(label, str) else None


def _rupees(minor: int) -> float:
    return float(Decimal(minor) / 100)


def _minor(rupees: object) -> int:
    return int((Decimal(str(rupees)) * 100).to_integral_value())


def _message(body: object) -> str:
    text = body.get("message") if isinstance(body, dict) else None
    return str(text)[:200] if text else "the carrier refused this request"


class ShiprocketClient:
    """Satisfies `app.channels.carrier.CarrierClient`. One instance per call site; a test hands in
    an `httpx.AsyncClient` over a `MockTransport`."""

    name = "shiprocket"

    def __init__(
        self,
        *,
        base_url: str,
        email: str,
        password: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._email, self._password = email, password
        self._token: str | None = None
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )
        self._timeout_seconds = timeout_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: object = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        try:
            return await asyncio.wait_for(
                self._client.request(method, path, params=params, json=json_body, headers=headers),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ChannelUnavailable("shiprocket timed out") from exc
        except httpx.HTTPError as exc:
            raise ChannelUnavailable(
                f"shiprocket could not be reached: {type(exc).__name__}"
            ) from exc

    async def _login(self) -> str:
        if self._token is None:
            response = await self._send(
                "POST", "/auth/login", json_body={"email": self._email, "password": self._password}
            )
            if response.status_code >= 500 or response.status_code == 429:
                raise ChannelUnavailable(f"shiprocket login answered {response.status_code}")
            token = _json(response).get("token") if response.status_code == 200 else None
            if not isinstance(token, str) or not token:
                raise ChannelAuthError("shiprocket rejected this connection's login")
            self._token = token
        return self._token

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: object = None,
        allow: frozenset[int] = frozenset(),
    ) -> tuple[int, dict[str, Any]]:
        token = await self._login()
        response = await self._send(
            method,
            path,
            params=params,
            json_body=json_body,
            headers={"Authorization": f"Bearer {token}"},
        )
        status = response.status_code
        if status == 401:
            raise ChannelAuthError("shiprocket rejected this connection's credential")
        if status == 429 or status >= 500:
            raise ChannelUnavailable(f"shiprocket answered {status}")
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise ChannelUnavailable("shiprocket's response exceeded the size this client reads")
        body = _json(response)
        if status >= 400 and status not in allow:
            raise ChannelRejected("provider_rejected", _message(body))
        return status, body

    # ------------------------------------------------------------------ CarrierClient

    async def check_serviceability(self, query: ServiceabilityQuery) -> list[CarrierRate]:
        _, body = await self._call(
            "GET",
            "/courier/serviceability/",
            params={
                "pickup_postcode": query.origin_pincode,
                "delivery_postcode": query.destination_pincode,
                "weight": str(query.weight_grams / 1000),
                "cod": "1" if query.cod_amount_minor else "0",
            },
            allow=frozenset({404}),
        )
        companies = (body.get("data") or {}).get("available_courier_companies") or []
        rates = [
            CarrierRate(
                service_code=str(c["courier_company_id"]),
                service_name=str(c.get("courier_name", "")),
                charge_minor=_minor(c.get("rate", 0)),
                currency="INR",
                estimated_days=_int_or_none(c.get("estimated_delivery_days")),
            )
            for c in companies
            if isinstance(c, dict) and "courier_company_id" in c
        ]
        return sorted(rates, key=lambda r: r.charge_minor)

    async def create_shipment(self, request: CarrierBookingRequest) -> CarrierBooking:
        payload: dict[str, Any] = {
            "order_id": request.reference,
            "order_date": request.order_date or datetime.now(_IST).strftime("%Y-%m-%d"),
            "pickup_location": request.pickup_location,
            "billing_customer_name": request.consignee_name,
            "billing_address": request.consignee_address,
            "billing_city": request.consignee_city,
            "billing_state": request.consignee_state,
            "billing_country": "India",
            "billing_pincode": request.destination_pincode,
            "billing_email": request.consignee_email,
            "billing_phone": request.consignee_phone,
            "shipping_is_billing": True,
            "order_items": [
                {
                    "name": f"Order {request.reference}",
                    "sku": request.reference,
                    "units": 1,
                    "selling_price": _rupees(request.declared_value_minor),
                }
            ],
            "payment_method": "COD" if request.cod_amount_minor else "Prepaid",
            "sub_total": _rupees(request.cod_amount_minor or request.declared_value_minor),
            "weight": request.weight_grams / 1000,
            "length": request.length_cm,
            "breadth": request.breadth_cm,
            "height": request.height_cm,
        }
        if request.service_code:
            payload["courier_id"] = int(request.service_code)
        _, body = await self._call("POST", "/shipments/create/forward-shipment", json_body=payload)
        result = body.get("payload")
        result = result if isinstance(result, dict) else {}
        awb, shipment_id = result.get("awb_code"), result.get("shipment_id")
        if body.get("status") != 1 or not awb or shipment_id is None:
            detail = result.get("error_message") or "no AWB was assigned"
            raise ChannelRejected("booking_refused", str(detail)[:200])
        return CarrierBooking(
            awb=str(awb),
            external_shipment_id=f"{shipment_id}:{awb}",
            label_url=result.get("label_url") or None,
        )

    async def get_label(self, external_shipment_id: str) -> CarrierLabel:
        shipment_id, _ = _split(external_shipment_id)
        _, body = await self._call(
            "POST", "/courier/generate/label", json_body={"shipment_id": [int(shipment_id)]}
        )
        url = body.get("label_url")
        if not url:
            raise ChannelRejected("label_unavailable", "the carrier has no label for this shipment")
        return CarrierLabel(url=str(url))

    async def cancel_shipment(self, external_shipment_id: str) -> CarrierAck:
        _, awb = _split(external_shipment_id)
        await self._call("POST", "/orders/cancel/shipment/awbs", json_body={"awbs": [awb]})
        return CarrierAck(accepted=True)

    async def track(self, awb: str) -> CarrierTracking:
        _, body = await self._call("GET", f"/courier/track/awb/{awb}", allow=frozenset({404}))
        activities = (body.get("tracking_data") or {}).get("shipment_track_activities") or []
        events = []
        for a in activities:
            status = map_status(a.get("sr-status-label")) if isinstance(a, dict) else None
            when = _parse_ist(a.get("date")) if status else None
            if status and when:
                events.append(
                    CarrierTrackingEvent(
                        status=status, occurred_at=when, location=str(a.get("location", ""))[:200]
                    )
                )
        return CarrierTracking(awb=awb, events=sorted(events, key=lambda e: e.occurred_at))


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except ValueError:
        return None


def _split(external_shipment_id: str) -> tuple[str, str]:
    shipment_id, _, awb = external_shipment_id.partition(":")
    if not shipment_id.isdigit() or not awb:
        raise ChannelRejected("bad_reference", "not a shiprocket shipment reference")
    return shipment_id, awb


def _parse_ist(value: object, fmt: str = "%Y-%m-%d %H:%M:%S") -> datetime | None:
    try:
        return datetime.strptime(str(value), fmt).replace(tzinfo=_IST).astimezone(UTC)
    except ValueError:
        return None


class ShiprocketVerifier:
    """`WebhookVerifier` for Shiprocket's tracking push: the `x-api-key` header must equal the
    connection's own webhook token. A missing header, a connection with no packed credential or any
    mismatch is False -- there is no unauthenticated mode."""

    provider = "shiprocket"
    known_events = frozenset({"tracking.update"})
    allowed_fields = frozenset(
        {
            "awb",
            "current_status",
            "current_status_id",
            "current_timestamp",
            "courier_name",
            "order_id",
        }
    )

    def verify(self, *, headers: Mapping[str, str], body: bytes, secret: str) -> bool:
        credential = CarrierCredential.unpack(secret)
        presented = headers.get("x-api-key")
        if credential is None or not credential.webhook_token or not presented:
            return False
        return hmac.compare_digest(presented.encode(), credential.webhook_token.encode())

    def parse(self, *, headers: Mapping[str, str], body: bytes) -> ParsedEvent | None:
        try:
            data = json.loads(body)
        except ValueError:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("awb"), str) or not data["awb"]:
            return None
        key = f"{data['awb']}|{data.get('current_status_id')}|{data.get('current_timestamp')}"
        return ParsedEvent(
            event_id=hashlib.sha256(key.encode()).hexdigest()[:40],
            event_name="tracking.update",
            fields=data,
        )


def tracking_update(payload: Mapping[str, Any]) -> tuple[str, str, datetime] | None:
    """`(awb, platform status, occurred_at)` of a recorded Shiprocket event, or None if it moves
    nothing."""
    awb, status = payload.get("awb"), map_status(payload.get("current_status"))
    if not awb or status is None:
        return None
    when = _parse_ist(payload.get("current_timestamp"), "%d %m %Y %H:%M:%S") or datetime.now(UTC)
    return str(awb), status, when
