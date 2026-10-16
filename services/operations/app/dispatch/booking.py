"""Booking a shipment with the customer's own courier: claim -> call -> record
(`docs/io-outside-transactions.md`).

A carrier call must never run inside the transaction that locks the order, so dispatch through a
carrier connection in `live` mode is three steps, each its own transaction on the raw session:

1. **Claim.** Lock the order, run every dispatch precondition (`check_dispatchable`), and write the
   shipment row in `BOOKING` state -- or take back the order's `BOOKING_FAILED` row. Commit. The
   unique `(organization, order)` shipment index plus the order's `READY` status are what stop a
   second booking: a concurrent request sees `BOOKING` and is refused (409), unless the claim is
   stale (a crashed worker), when it may be taken over.
2. **Call.** The carrier is called with no transaction open. The carrier gets the order number as
   its client reference. A booking that already succeeded earlier (the row holds the AWB) is not
   made again.
3. **Record.** A new transaction re-locks the order and the shipment and runs the ordinary
   `dispatch_order` against the claimed row: AWB, carrier shipment id, stock `issue`, the order's
   hand-over, audit, events. If the order moved while the carrier was called, the AWB is kept on
   the row (`BOOKING_FAILED`) so an operator can cancel it at the carrier; a retry reuses it.

A failed call marks the row `BOOKING_FAILED` with the reason; posting the same dispatch again is
the retry path. The order is only ever handed over in step 3.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.channels import registry
from app.channels import selectors as channel_selectors
from app.channels import services as channel_services
from app.channels.base import ChannelAuthError, ChannelRejected, ChannelUnavailable
from app.channels.carrier import CarrierBooking, CarrierBookingRequest, CarrierClient
from app.dispatch import selectors
from app.dispatch import services as dispatch_services
from app.models import AuditLog, Channel, Order, Shipment
from app.orders import selectors as order_selectors
from app.schemas import CarrierBookingIn, ShipmentIn, ShipmentOut
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.db import assert_tenant, set_tenant
from platform_core.errors import (
    ConflictProblem,
    NotFoundProblem,
    ProblemError,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.logging import get_logger

log = get_logger("operations.dispatch.booking")

#: A `BOOKING` row untouched this long belongs to a crashed request; a new claim may take it over.
STALE_CLAIM = timedelta(minutes=5)


async def _bind(session: AsyncSession, ctx: ScopeContext) -> None:
    await set_tenant(session, ctx.organization_id)
    await assert_tenant(session, ctx.organization_id)


def _request_for(
    order: Order, channel: Channel, details: CarrierBookingIn
) -> CarrierBookingRequest:
    return CarrierBookingRequest(
        reference=order.number,
        service_code=details.service_code or "",
        origin_pincode="",
        destination_pincode=details.destination_pincode,
        weight_grams=details.weight_grams,
        consignee_name=details.consignee_name,
        consignee_phone=details.consignee_phone,
        consignee_address=details.consignee_address,
        cod_amount_minor=details.cod_amount_minor,
        currency=order.currency,
        consignee_city=details.consignee_city,
        consignee_state=details.consignee_state,
        consignee_email=details.consignee_email or "",
        length_cm=details.length_cm,
        breadth_cm=details.breadth_cm,
        height_cm=details.height_cm,
        pickup_location=channel.external_shop_id,
        order_date=order.created_at.date().isoformat(),
        declared_value_minor=order.total_minor,
    )


async def book_and_dispatch(
    session: AsyncSession, ctx: ScopeContext, payload: ShipmentIn, *, request: Request | None = None
) -> ShipmentOut:
    """Live carrier dispatch. `session` is the **raw** session: this function owns three
    transactions on it. Raises the usual problems; a carrier failure leaves the shipment
    `BOOKING_FAILED` and the order untouched."""
    details, channel_id = payload.booking, payload.channel_id
    if details is None or channel_id is None:
        raise ValidationProblem("booking details are required to book with a carrier")
    org_id = ctx.organization_id

    # ---- 1. claim
    async with session.begin():
        await _bind(session, ctx)
        order = await order_selectors.get_order(session, org_id, payload.order_id, for_update=True)
        if order is None:
            raise NotFoundProblem("That order is not part of this organization")
        shipment = await selectors.shipment_for_order(session, org_id, order.id, for_update=True)
        now = datetime.now(UTC)
        if shipment is not None and shipment.status == "BOOKING":
            if now - shipment.updated_at < STALE_CLAIM:
                raise ConflictProblem(f"Order {order.number} is already being booked")
        elif shipment is not None and shipment.status != "BOOKING_FAILED":
            raise ConflictProblem(f"Order {order.number} already has a shipment")
        await dispatch_services.check_dispatchable(
            session,
            ctx,
            order,
            expected_version=payload.order_version,
            payment_confirmed=payload.payment_confirmed,
        )
        channel = await channel_selectors.get_channel(session, org_id, channel_id)
        if channel is None:
            raise NotFoundProblem("That carrier is not part of this organization")
        if channel.kind != "carrier":
            raise ValidationProblem(f"Channel {channel.id} is not a carrier connection")
        if channel.status != "connected":
            raise ConflictProblem(f"Carrier {channel.name} is {channel.status}, not connected")
        credential = await channel_services.open_carrier_credential(channel)
        client: CarrierClient = registry.build_carrier_client(channel.provider, credential)
        prior: CarrierBooking | None = None
        if shipment is None:
            shipment = Shipment(
                organization_id=org_id,
                created_by=str(ctx.principal.sub),
                order_id=order.id,
                warehouse_id=order.warehouse_id,
                status="BOOKING",
                carrier_name=payload.carrier_name or channel.name,
                channel_id=channel.id,
            )
            session.add(shipment)
        else:
            if shipment.external_shipment_id and shipment.tracking_reference:
                prior = CarrierBooking(
                    awb=shipment.tracking_reference,
                    external_shipment_id=shipment.external_shipment_id,
                )
            shipment.status = "BOOKING"
            shipment.failure_reason = None
            shipment.channel_id = channel.id
        shipment.updated_at = now
        shipment.updated_by = str(ctx.principal.sub)
        await session.flush()
        record_audit(
            session,
            AuditLog,
            organization_id=org_id,
            actor_id=ctx.principal.sub,
            action="shipment.booking_claimed",
            entity="shipment",
            entity_id=shipment.id,
            after={"order_id": str(order.id), "channel_id": str(channel.id)},
            request=request,
        )
        shipment_id, carrier_name = shipment.id, shipment.carrier_name
        booking_request = _request_for(order, channel, details)
        provider = channel.provider

    # ---- 2. call (no transaction open)
    try:
        booking = prior or await client.create_shipment(booking_request)
    except (ChannelUnavailable, ChannelRejected, ChannelAuthError) as exc:
        await _fail(session, ctx, shipment_id, channel_id, exc, request=request)
        raise _problem(exc, carrier_name) from exc
    finally:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            await aclose()
    log.info(
        "carrier.booking.made",
        organization_id=str(org_id),
        shipment_id=str(shipment_id),
        provider=provider,
    )

    # ---- 3. record
    failure: ProblemError | None = None
    result: ShipmentOut | None = None
    async with session.begin():
        await _bind(session, ctx)
        order = await order_selectors.get_order(session, org_id, payload.order_id, for_update=True)
        claimed = await selectors.get_shipment(session, org_id, shipment_id, for_update=True)
        if order is None or claimed is None or claimed.status != "BOOKING":
            raise ConflictProblem("The booking is no longer open; check the shipment")
        try:
            async with session.begin_nested():
                done = await dispatch_services.dispatch_order(
                    session,
                    ctx,
                    order,
                    expected_version=payload.order_version,
                    carrier_name=payload.carrier_name,
                    tracking_reference=None,
                    channel_id=channel_id,
                    booking=booking,
                    claimed=claimed,
                    payment_confirmed=payload.payment_confirmed,
                    request=request,
                )
        except ProblemError as exc:
            # The order moved while the carrier was called. Keep the AWB so it can be cancelled.
            claimed.status = "BOOKING_FAILED"
            claimed.tracking_reference = booking.awb
            claimed.external_shipment_id = booking.external_shipment_id
            claimed.failure_reason = f"Booked as {booking.awb}, but dispatch was refused: {exc}"[
                :500
            ]
            claimed.updated_at = datetime.now(UTC)
            failure = exc
        else:
            result = selectors.shipment_out(done)
    if failure is not None or result is None:
        raise failure or ConflictProblem("The booking could not be recorded")
    return result


def _problem(exc: Exception, carrier_name: str) -> ProblemError:
    if isinstance(exc, ChannelAuthError):
        return ConflictProblem(
            f"{carrier_name} rejected this connection's credential; reconnect it and retry"
        )
    if isinstance(exc, ChannelRejected):
        return ValidationProblem(f"{carrier_name} refused the booking: {exc.message}")
    return UpstreamUnavailableProblem(
        f"{carrier_name} could not be reached; the booking can be retried"
    )


async def _fail(
    session: AsyncSession,
    ctx: ScopeContext,
    shipment_id: uuid.UUID,
    channel_id: uuid.UUID,
    exc: Exception,
    *,
    request: Request | None,
) -> None:
    """Step 2 failed: its own transaction marks the row `BOOKING_FAILED` (the retry path) and, for
    a rejected credential, the connection `error`."""
    reason = exc.message if isinstance(exc, ChannelRejected) else str(exc)
    async with session.begin():
        await _bind(session, ctx)
        shipment = await selectors.get_shipment(
            session, ctx.organization_id, shipment_id, for_update=True
        )
        if shipment is not None and shipment.status == "BOOKING":
            shipment.status = "BOOKING_FAILED"
            shipment.failure_reason = reason[:500]
            shipment.updated_at = datetime.now(UTC)
            record_audit(
                session,
                AuditLog,
                organization_id=ctx.organization_id,
                actor_id=ctx.principal.sub,
                action="shipment.booking_failed",
                entity="shipment",
                entity_id=shipment.id,
                after={"reason": reason[:200]},
                request=request,
            )
        if isinstance(exc, ChannelAuthError):
            channel = await channel_selectors.get_channel(session, ctx.organization_id, channel_id)
            if channel is not None:
                channel.status = "error"
                channel.last_error_code = "auth_error"
                channel.last_error_message = "the carrier rejected the credential"
