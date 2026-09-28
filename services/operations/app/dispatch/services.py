"""Writes for dispatch — the carrier handover and its own shipment lifecycle (ADR-0024 §6;
`docs/fulfilment/plan.md` F5).

`dispatch_order` is the delivery/courier counterpart to `app.orders.services.hand_over_order`'s
counter/walk-in path: same order-row-first locking, same `check_billing_gate`, same
`complete_handover` two-transition write — only how the handover fact gets established differs
(a shipment's own creation here, a pickup-code/payment attestation there). `transition_shipment`
is the shipment's *own* lifecycle after that (`PICKUP_HANDOVER -> IN_TRANSIT -> DELIVERED`, or
`FAILED`/`RETURNED`) — never a second write to `orders.status`, which already reached `DELIVERED`
the moment the shipment was created (ADR-0025 §1: everything past the order's own short
commercial status lives on its own record).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.dispatch import selectors
from app.inventory import ledger
from app.inventory.ledger import MoveLine
from app.models import AuditLog, Order, OutboxEvent, Shipment, ShipmentTransition
from app.orders import selectors as order_selectors
from app.orders.services import check_billing_gate, complete_handover
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem
from platform_core.events import publish_event
from platform_core.logging import get_logger

log = get_logger("operations.dispatch")

EVT_SHIPMENT_CREATED = "operations.dispatch.shipment.created"
EVT_SHIPMENT_DISPATCHED = "operations.dispatch.shipment.dispatched"
EVT_SHIPMENT_DELIVERED = "operations.dispatch.shipment.delivered"
EVT_SHIPMENT_FAILED = "operations.dispatch.shipment.failed"
EVT_SHIPMENT_RETURNED = "operations.dispatch.shipment.returned"
#: A fire-and-forget request to `services/finance`'s own e-Way bill machinery
#: (`services/finance/app/eway/` — real validation and idempotency behind a `Protocol`, only the
#: NIC/GSP wire call itself is stubbed), never a second implementation here. **No finance
#: consumer exists for this event yet** — a documented follow-up (this slice's own report),
#: exactly the "producer ships ahead of its consumer" shape
#: `operations.orders.order.invoice_requested` set for F4 (`app.orders.services
#: .EVT_ORDER_INVOICE_REQUESTED`'s own docstring).
EVT_SHIPMENT_EWAY_REQUESTED = "operations.dispatch.shipment.eway_requested"

#: Mirrors `services/finance/app/domain.py::EWAY_THRESHOLD_MINOR` (₹50,000, in paise) —
#: **deliberately not imported**: no cross-service import exists in this platform any more than a
#: cross-schema foreign key does (ADR-0000 §10), the same reasoning `app.domain
#: .expected_gst_amount_minor`'s own docstring gives for mirroring finance's GST rounding rather
#: than importing it. This is a coarser pre-filter than finance's own `requires_eway_bill`
#: (which also needs a document type, a supply type and a reported status — facts an *invoice*
#: carries, not an order) — it exists so a trivial shipment never even reaches finance's queue,
#: not as the final word on whether a bill is actually required. Finance's own e-Way machinery,
#: once a consumer exists, makes the real call from the invoice it already holds.
_EWAY_VALUE_THRESHOLD_MINOR = 50_000_00

#: `Shipment.status` -> the event this module fires when `transition_shipment` reaches it.
_TRANSITION_EVENTS: dict[str, str] = {
    "IN_TRANSIT": EVT_SHIPMENT_DISPATCHED,
    "DELIVERED": EVT_SHIPMENT_DELIVERED,
    "FAILED": EVT_SHIPMENT_FAILED,
    "RETURNED": EVT_SHIPMENT_RETURNED,
}


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


async def dispatch_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order: Order,
    *,
    expected_version: int,
    carrier_name: str,
    tracking_reference: str | None,
    request: Request | None = None,
) -> Shipment:
    """The carrier handover (Fulfilment plan F5). `order` must already be locked by the caller
    (`app/api/routes.py`, the same `FOR UPDATE` shape `hand_over_order` needs).

    Version and transition-validity are checked **first**, before any other work — the reservation
    lookup, the dispatch `issue` move and the shipment row are all skipped on a stale version or a
    non-`READY` order, the same fail-fast order `hand_over_order` uses. Then, in this one
    transaction:

    1. `check_billing_gate(order)` — the *same* function the counter path calls, unchanged.
    2. For each order line, finds the reservation currently holding its promise, wherever pick
       and pack last transferred it (`app.dispatch.selectors.leaf_reservations_for_order`,
       ADR-0024 §6), and posts one `issue` move that **draws** against every one of them
       (`MoveLine.reservation_id`, never `transfer_reservation_id`) — the first point in the whole
       flow a reservation is actually consumed, not merely relocated. A line whose leaf
       reservation has nothing left open (a full short-pick) contributes no move line; an order
       with nothing left to dispatch at all is refused before any row is written.
    3. Creates the `Shipment` row, already at `PICKUP_HANDOVER` — the carrier has the goods now,
       the fact that satisfies the gate on this path the way a verified pickup code or an
       operator's payment attestation does on the counter's.
    4. Writes `READY -> HANDED_OVER -> DELIVERED` (`app.orders.services.complete_handover`,
       shared with the counter path so the two-transition write exists in exactly one place).
    5. Emits `operations.dispatch.shipment.created`, and — when the order's own total crosses the
       e-Way value threshold — `operations.dispatch.shipment.eway_requested`, fire-and-forget.
    """
    if order.version != expected_version:
        raise ConflictProblem(
            f"Order {order.number} is at version {order.version}, not {expected_version}; "
            "reload it and try again"
        )
    if not domain.can_transition_order(order.status, "HANDED_OVER"):
        raise ConflictProblem(f"Order {order.number} is {order.status} and cannot be dispatched")

    check_billing_gate(order)

    org_id = ctx.organization_id
    lines = await order_selectors.lines_for(session, org_id, order.id)
    leaves = await selectors.leaf_reservations_for_order(session, org_id, order.id)
    balances = await selectors.balances_by_id(
        session, org_id, [r.balance_id for r in leaves.values()]
    )

    move_lines: list[MoveLine] = []
    for line in lines:
        leaf = leaves.get(line.id)
        if leaf is None or leaf.status != "active":
            continue
        open_remainder = (
            leaf.quantity_milli - leaf.consumed_milli - leaf.released_milli - leaf.transferred_milli
        )
        if open_remainder <= 0:
            continue
        balance = balances.get(leaf.balance_id)
        if balance is None:  # pragma: no cover - defensive, the reservation's own FK guarantees it
            raise ConflictProblem(f"{line.id}'s reservation has no stock bucket to draw from")
        move_lines.append(
            MoveLine(
                item_id=line.inventory_item_id,
                location_id=balance.location_id,
                quantity_milli=-open_remainder,
                reservation_id=leaf.id,
            )
        )
    if not move_lines:
        raise ConflictProblem(
            f"Order {order.number} has nothing staged to dispatch — was it actually picked?"
        )

    shipment = Shipment(
        organization_id=org_id,
        created_by=str(ctx.principal.sub),
        order_id=order.id,
        warehouse_id=order.warehouse_id,
        status="PICKUP_HANDOVER",
        carrier_name=carrier_name,
        tracking_reference=tracking_reference,
    )
    session.add(shipment)
    await session.flush()

    await ledger.post_move(
        session,
        ctx,
        kind="issue",
        lines=move_lines,
        occurred_on=date.today(),
        reference_type="shipment",
        reference_id=shipment.id,
        idempotency_key=f"dispatch:{shipment.id}",
        occurred_on_explicit=False,
        request=request,
    )

    await complete_handover(session, ctx, order, request=request)

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="shipment.created",
        entity="shipment",
        entity_id=shipment.id,
        after={
            "order_id": str(order.id),
            "carrier_name": carrier_name,
            "tracking_reference": tracking_reference,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_SHIPMENT_CREATED,
        aggregate_type="shipment",
        aggregate_id=shipment.id,
        payload={
            "organization_id": str(org_id),
            "shipment_id": str(shipment.id),
            "order_id": str(order.id),
            "order_number": order.number,
            "carrier_name": carrier_name,
            "tracking_reference": tracking_reference,
        },
        actor=_actor(ctx),
    )

    if order.total_minor > _EWAY_VALUE_THRESHOLD_MINOR:
        publish_event(
            session,
            OutboxEvent,
            organization_id=org_id,
            event_type=EVT_SHIPMENT_EWAY_REQUESTED,
            aggregate_type="shipment",
            aggregate_id=shipment.id,
            payload={
                "organization_id": str(org_id),
                "shipment_id": str(shipment.id),
                "order_id": str(order.id),
                "order_number": order.number,
                "consignment_value_minor": order.total_minor,
                "currency": order.currency,
                "carrier_name": carrier_name,
                "tracking_reference": tracking_reference,
                "dispatched_at": datetime.now(UTC).isoformat(),
            },
            actor=_actor(ctx),
        )
    return shipment


async def transition_shipment(
    session: AsyncSession,
    ctx: ScopeContext,
    shipment: Shipment,
    *,
    to_status: str,
    reason: str = "",
    tracking_reference: str | None = None,
    request: Request | None = None,
) -> Shipment:
    """Advance a shipment along its own lifecycle (`app.domain.SHIPMENT_TRANSITIONS`) — never the
    order's own status, which already reached `DELIVERED` the moment this shipment was created
    (`dispatch_order`'s own docstring: the order's short commercial status and this row's carrier
    lifecycle are deliberately separate records, ADR-0025 §1). `shipment` must already be locked
    by the caller (`app/api/routes.py`, the same `FOR UPDATE` shape every other transition in this
    service uses).
    """
    if not domain.can_transition_shipment(shipment.status, to_status):
        raise ConflictProblem(
            f"Shipment {shipment.id} is {shipment.status} and cannot move to {to_status}"
        )
    org_id = ctx.organization_id
    from_status = shipment.status
    shipment.status = to_status
    if tracking_reference is not None:
        shipment.tracking_reference = tracking_reference
    if to_status == "DELIVERED":
        shipment.delivered_at = datetime.now(UTC)
    if to_status == "FAILED":
        shipment.failure_reason = reason or None
    shipment.updated_by = str(ctx.principal.sub)
    shipment.updated_at = datetime.now(UTC)
    session.add(
        ShipmentTransition(
            organization_id=org_id,
            shipment_id=shipment.id,
            from_status=from_status,
            to_status=to_status,
            actor=str(ctx.principal.sub),
            reason=reason,
        )
    )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="shipment.transitioned",
        entity="shipment",
        entity_id=shipment.id,
        before={"status": from_status},
        after={"status": to_status, "reason": reason} if reason else {"status": to_status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=_TRANSITION_EVENTS[to_status],
        aggregate_type="shipment",
        aggregate_id=shipment.id,
        payload={
            "organization_id": str(org_id),
            "shipment_id": str(shipment.id),
            "order_id": str(shipment.order_id),
            **({"reason": reason} if reason else {}),
        },
        actor=_actor(ctx),
    )
    return shipment
