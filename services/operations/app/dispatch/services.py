"""Writes for dispatch — the carrier handover and its own shipment lifecycle (ADR-0024 §6;
`docs/fulfilment/plan.md` F5).

`dispatch_order` is the delivery/courier counterpart to `app.orders.services.hand_over_order`'s
counter/walk-in path: same order-row-first locking, same `check_billing_gate`, same
`attest_payment_if_confirmed`, same `complete_handover` two-transition write (which now also
posts the stock-consuming `issue` move itself, security review M2) — only how the handover fact
gets established differs (a shipment's own creation here, a pickup-code/payment attestation
there), and this path additionally checks that every line was genuinely picked and, if packing
was started, finished (`app.dispatch.selectors.unfulfilled_order_lines`, security review B1/M1) —
a carrier-specific requirement the counter path has no equivalent of. `transition_shipment` is
the shipment's *own* lifecycle after that (`PICKUP_HANDOVER -> IN_TRANSIT -> DELIVERED`, or
`FAILED`/`RETURNED`) — never a second write to `orders.status`, which already reached `DELIVERED`
the moment the shipment was created (ADR-0025 §1: everything past the order's own short
commercial status lives on its own record).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.dispatch import selectors
from app.inventory.quantity import format_qty
from app.models import AuditLog, Order, OutboxEvent, Shipment, ShipmentTransition
from app.orders import selectors as order_selectors
from app.orders.services import attest_payment_if_confirmed, check_billing_gate, complete_handover
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
    payment_confirmed: bool = False,
    request: Request | None = None,
) -> Shipment:
    """The carrier handover (Fulfilment plan F5). `order` must already be locked by the caller
    (`app/api/routes.py`, the same `FOR UPDATE` shape `hand_over_order` needs).

    Version and transition-validity are checked **first**, before any other work — the coverage
    check, the billing gate and the shipment row are all skipped on a stale version or a
    non-`READY` order, the same fail-fast order `hand_over_order` uses. Then, in this one
    transaction:

    1. `attest_payment_if_confirmed` — the carrier-desk operator's own attestation that a
       cash/prepaid order was paid, the exact counterpart to the counter path's own
       `payment_confirmed` handling (security review M3: without this, a cash/prepaid order could
       never pass the gate below on this path at all).
    2. `check_billing_gate(order)` — the *same* function the counter path calls, unchanged.
       **`require_pickup_code` is deliberately left exactly as it is here.** An org that opted
       into requiring a pickup code has no carrier-side equivalent of one yet, so this path fails
       closed rather than silently bypassing a condition it cannot currently satisfy — the same
       fail-closed precedent F4's own security review already set (a gate a real product gap
       cannot yet pass is correct to leave failing, not a bug to route around).
    3. `app.dispatch.selectors.unfulfilled_order_lines` — every line must have been genuinely
       picked, and, if packing was started for it, finished (security review B1/M1). A line never
       picked at all (including one whose pick task was cancelled, M1) or one left with a genuine
       partial-pack remainder (B1's split-pack finding) each refuse with a 409 naming the line and
       the missing quantity, before any row is written — never a silent partial dispatch.
    4. Creates the `Shipment` row, already at `PICKUP_HANDOVER` — the carrier has the goods now,
       the fact that satisfies the gate on this path the way a verified pickup code or an
       operator's payment attestation does on the counter's.
    5. Writes `READY -> HANDED_OVER -> DELIVERED`, **and now also posts the stock-consuming
       `issue` move** (`app.orders.services.complete_handover`, security review M2 — previously
       only this function's own job, leaving every counter hand-over writing zero ledger activity;
       now shared so the consuming move exists in exactly one place either path can reach).
    6. Emits `operations.dispatch.shipment.created`, and — when the order's own tax-inclusive
       total (an approximation, `app.domain.expected_gst_amount_minor` over the order's subtotal)
       crosses the e-Way value threshold, and the order is priced in INR — emits
       `operations.dispatch.shipment.eway_requested`, fire-and-forget.
    """
    if order.version != expected_version:
        raise ConflictProblem(
            f"Order {order.number} is at version {order.version}, not {expected_version}; "
            "reload it and try again"
        )
    if not domain.can_transition_order(order.status, "HANDED_OVER"):
        raise ConflictProblem(f"Order {order.number} is {order.status} and cannot be dispatched")

    # M3 (security review): the carrier-desk counterpart to `hand_over_order`'s own
    # `payment_confirmed` handling — must run *before* the gate below, the same order the counter
    # path uses, or a cash/prepaid order can never pass it on this path.
    attest_payment_if_confirmed(order, payment_confirmed=payment_confirmed)
    # `require_pickup_code` fails closed here on purpose (see this function's own docstring,
    # point 2) — not an oversight, and not something to route around.
    check_billing_gate(order)

    org_id = ctx.organization_id
    lines = await order_selectors.lines_for(session, org_id, order.id)
    never_picked, partial_pack = await selectors.unfulfilled_order_lines(
        session, org_id, order.id, lines
    )
    if never_picked or partial_pack:
        problem_lines = never_picked or partial_pack
        item_ids = [line.inventory_item_id for line, _ in problem_lines]
        items = await order_selectors.items_for_lines(session, org_id, item_ids)
        detail = ", ".join(
            f"{items[line.inventory_item_id].sku if line.inventory_item_id in items else line.id}"
            f" ({format_qty(qty)})"
            for line, qty in problem_lines
        )
        if never_picked:
            raise ConflictProblem(
                f"Order {order.number} cannot be dispatched — never picked: {detail}"
            )
        raise ConflictProblem(
            f"Order {order.number} cannot be dispatched — staged but not fully packed: {detail}"
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

    # M2 (security review): the reservation-consuming `issue` move is now posted inside
    # `complete_handover` itself, identically for both handover paths — see that function's own
    # docstring.
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

    # Security review cheap-fix: finance's real e-Way check is against the tax-*inclusive*
    # invoice total, not the bare pre-tax subtotal `order.total_minor` is — this pre-filter now
    # approximates that the same way `app.orders.services.request_invoice_for_order` already does
    # (`domain.expected_gst_amount_minor`, mirroring finance's own rounding, no cross-service
    # import, ADR-0000 §10). India-specific, so it only ever applies in INR.
    tax_rate_pct = Decimal((order.settings_snapshot or {}).get("default_tax_rate_pct", "0"))
    tax_inclusive_total_minor = order.total_minor + domain.expected_gst_amount_minor(
        order.total_minor, tax_rate_pct
    )
    if order.currency == "INR" and tax_inclusive_total_minor > _EWAY_VALUE_THRESHOLD_MINOR:
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
    # Cheap fix (security review): a blank/whitespace-only value is treated the same as absent —
    # `is not None` alone let an empty string sent by mistake silently overwrite a previously
    # stored tracking reference.
    if tracking_reference is not None and tracking_reference.strip():
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
