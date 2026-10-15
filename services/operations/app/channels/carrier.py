"""The carrier connector seam -- one `Protocol` every courier client satisfies.

The sibling of `app/channels/base.py`'s `ChannelClient`, for a `channels.kind = 'carrier'`
connection: the business ships with **its own** account at Delhivery, Shiprocket and the like, and
this platform books, labels, cancels and tracks on that account. It never holds the shipping money
or resells the service.

The same discipline as `ChannelClient`: a structural `Protocol`, neutral dataclasses rather than any
provider's JSON, integer minor units for every amount, and the three failure shapes `ChannelClient`
already defines -- `ChannelUnavailable` (retry the identical request later), `ChannelRejected`
(the carrier refused it; retrying will refuse again) and `ChannelAuthError` (the credential is no
good) -- reused so a caller reacts to a carrier exactly as it does to a marketplace.

**Idempotency.** `CarrierBookingRequest.reference` is the platform's own stable reference for the
shipment (the order number); an adapter passes it as the carrier's client reference so a booking
retried after a lost response finds the shipment it already made instead of making a second.

**No network I/O inside a transaction.** Every method makes a real outbound call, so a caller holds
none open (`docs/io-outside-transactions.md`); `dispatch_order` itself never calls these in this
build (see `app.dispatch.services`), and `tests/infra/test_io_outside_transaction.py` watches the
type name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ServiceabilityQuery:
    origin_pincode: str
    destination_pincode: str
    weight_grams: int
    #: Cash on delivery amount to collect, integer minor units; 0 for prepaid.
    cod_amount_minor: int = 0
    currency: str = "INR"


@dataclass(frozen=True, slots=True)
class CarrierRate:
    """One service the carrier offers for the query, priced in integer minor units."""

    service_code: str
    service_name: str
    charge_minor: int
    currency: str
    estimated_days: int | None = None


@dataclass(frozen=True, slots=True)
class CarrierBookingRequest:
    #: The platform's stable reference for this shipment (the order number): the carrier's
    #: idempotency handle, so a retried booking returns the shipment already made.
    reference: str
    service_code: str
    origin_pincode: str
    destination_pincode: str
    weight_grams: int
    consignee_name: str
    consignee_phone: str
    consignee_address: str
    cod_amount_minor: int = 0
    currency: str = "INR"


@dataclass(frozen=True, slots=True)
class CarrierBooking:
    """What a successful booking returns: the air waybill the parcel travels under and the
    carrier's own shipment id (what label, cancel and track are asked by)."""

    awb: str
    external_shipment_id: str


@dataclass(frozen=True, slots=True)
class CarrierLabel:
    #: A URL the carrier serves the label from, or the label bytes -- whichever the carrier gives.
    url: str | None = None
    content: bytes | None = None
    content_type: str = "application/pdf"


@dataclass(frozen=True, slots=True)
class CarrierAck:
    accepted: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CarrierTrackingEvent:
    #: The platform's `Shipment.status` vocabulary the carrier's status maps onto (IN_TRANSIT,
    #: DELIVERED, FAILED, RETURNED), never the carrier's own words.
    status: str
    occurred_at: datetime
    location: str = ""


@dataclass(frozen=True, slots=True)
class CarrierTracking:
    awb: str
    events: list[CarrierTrackingEvent] = field(default_factory=list)


@runtime_checkable
class CarrierClient(Protocol):
    """The contract dispatch and tracking depend on -- never a concrete carrier."""

    name: str

    async def check_serviceability(self, query: ServiceabilityQuery) -> list[CarrierRate]:
        """The services the carrier offers for the lane, cheapest first. An empty list means the
        carrier does not serve it. Raises `ChannelUnavailable` or `ChannelAuthError`."""
        ...

    async def create_shipment(self, request: CarrierBookingRequest) -> CarrierBooking:
        """Book the shipment and return its AWB. Idempotent on `request.reference`. Raises
        `ChannelRejected` for a lane or address the carrier refuses, `ChannelUnavailable`, or
        `ChannelAuthError`."""
        ...

    async def get_label(self, external_shipment_id: str) -> CarrierLabel:
        """The shipping label for a booked shipment."""
        ...

    async def cancel_shipment(self, external_shipment_id: str) -> CarrierAck:
        """Cancel a booking the carrier has not yet picked up. `ChannelRejected` once it has."""
        ...

    async def track(self, awb: str) -> CarrierTracking:
        """Tracking events for an AWB, oldest first, in the platform's status vocabulary."""
        ...
