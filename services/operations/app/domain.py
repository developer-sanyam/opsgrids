"""Pure rollups and vocabulary for operations. No I/O, no session — only model instances in,
plain values out, so this is trivially testable and mirrors the SPA's `data/derive.ts`.

The overview's KPIs, the spend-by-category bars, the PO-status donut, the spend trend and the
"needs attention" queue all derive from the same purchase-order / inventory / supplier rows the
tables render, exactly as the SPA derives them, so the numbers agree whichever side computes.
"""

from __future__ import annotations

import heapq
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Protocol

from app.inventory import quantity
from app.models import InventoryItem, OrderLine, PurchaseOrder, PurchaseOrderLine, Supplier

# ------------------------------------------------------------------ vocabulary (labels match SPA)

CATEGORY_ORDER: tuple[str, ...] = (
    "raw_materials",
    "packaging",
    "components",
    "mro",
    "logistics",
    "services",
)
CATEGORY_LABELS: dict[str, str] = {
    "raw_materials": "Raw materials",
    "packaging": "Packaging",
    "components": "Components",
    "mro": "MRO",
    "logistics": "Logistics",
    "services": "Services",
}

PO_STATUS_ORDER: tuple[str, ...] = ("draft", "approved", "received", "closed", "cancelled")
PO_STATUS_LABELS: dict[str, str] = {
    "draft": "Draft",
    "approved": "Approved",
    "received": "Received",
    "closed": "Closed",
    "cancelled": "Cancelled",
}

#: PO statuses that count as "still in flight".
OPEN_STATUSES: frozenset[str] = frozenset({"draft", "approved"})

#: The lifecycle: draft -> approved -> received -> closed, with a `cancelled` terminal state
#: reachable from draft or approved (an approved PO that will never arrive must not stay "open"
#: forever, inflating `open_pos` and the late-delivery attention queue). Approval is a
#: prerequisite gate on receiving, not merely a commitment step: `purchase_order.approve` is
#: floored at ORG specifically so a buyer can never approve their own PO, and a buyer who could
#: still receive straight from `draft` would walk straight around that gate — stock would move
#: and spend would commit with no second person ever having signed off.
PO_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"approved", "cancelled"}),
    "approved": frozenset({"received", "cancelled"}),
    "received": frozenset({"closed"}),
    "closed": frozenset(),
    "cancelled": frozenset(),
}


def can_transition_po(current: str, new: str) -> bool:
    """Whether a purchase order may move `current` -> `new`. Every other move — including a
    no-op "move" to the same status — answers 409."""
    return new in PO_TRANSITIONS.get(current, frozenset())


MONTH_LABELS: tuple[str, ...] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


# ------------------------------------------------------------------ line / total arithmetic


def line_amount_minor(quantity: int, unit_price_minor: int) -> int:
    """A single line's total — `quantity * unit_price_minor`, in minor units. This is the
    footing rule: `amount_minor` is stored as exactly this, and a PO total is the sum of them."""
    return quantity * unit_price_minor


def po_total_minor(lines: Sequence[PurchaseOrderLine]) -> int:
    """A purchase order's total in minor units — summed from its line `amount_minor`, never
    stored. The lines foot to this total by construction."""
    return sum(line.amount_minor for line in lines)


# ------------------------------------------------------------------ purchase-order rollups


def is_open(po: PurchaseOrder) -> bool:
    """ "Open" purchase orders are the ones still in flight: draft or approved."""
    return po.status in OPEN_STATUSES


def open_po_count(pos: Sequence[PurchaseOrder]) -> int:
    return sum(1 for po in pos if is_open(po))


def is_late(po: PurchaseOrder, today: date) -> bool:
    """An open PO is late when its expected date has passed and nothing has been received."""
    return is_open(po) and po.received_date is None and po.expected_date < today


def _pct(part: int, whole: int) -> int:
    return 0 if whole == 0 else round(part / whole * 100)


def spend_by_category(
    pos: Sequence[PurchaseOrder], totals: dict[str, int]
) -> list[dict[str, object]]:
    """Committed spend grouped by category, largest first. Draft and cancelled POs are excluded
    — spend is only committed once a PO is approved, and a cancelled PO committed nothing in the
    end. `totals` maps a PO id (str) to its derived total. Percentages are shares of the resolved
    total."""
    committed = [po for po in pos if po.status not in ("draft", "cancelled")]
    by_cat: dict[str, int] = {}
    for po in committed:
        by_cat[po.category] = by_cat.get(po.category, 0) + totals.get(str(po.id), 0)
    grand = sum(by_cat.values())
    rows = [
        {
            "key": key,
            "name": CATEGORY_LABELS.get(key, key),
            "spend_minor": spend,
            "pct": _pct(spend, grand),
        }
        for key, spend in by_cat.items()
    ]
    rows.sort(key=lambda r: r["spend_minor"], reverse=True)  # type: ignore[arg-type,return-value]
    return rows


def po_status_mix(pos: Sequence[PurchaseOrder]) -> list[dict[str, object]]:
    """Purchase orders grouped by status, in lifecycle order, as donut slices."""
    total = len(pos)
    out: list[dict[str, object]] = []
    for key in PO_STATUS_ORDER:
        count = sum(1 for po in pos if po.status == key)
        if count:
            out.append(
                {
                    "key": key,
                    "label": PO_STATUS_LABELS[key],
                    "count": count,
                    "pct": _pct(count, total),
                }
            )
    return out


def spend_trend(
    pos: Sequence[PurchaseOrder], totals: dict[str, int], *, months: int = 12
) -> list[dict[str, object]]:
    """Committed spend per calendar month, in minor units — the trend chart's representative
    series. Committed POs (anything past draft, excluding cancelled) are bucketed by
    `created_date` month. Returns a point per month present, oldest first."""
    committed = [po for po in pos if po.status not in ("draft", "cancelled")]
    by_month: dict[tuple[int, int], int] = {}
    for po in committed:
        key = (po.created_date.year, po.created_date.month)
        by_month[key] = by_month.get(key, 0) + totals.get(str(po.id), 0)
    ordered = sorted(by_month.items())[-months:]
    return [
        {"label": MONTH_LABELS[month - 1], "spend_minor": spend}
        for (_year, month), spend in ordered
    ]


def top_suppliers(suppliers: Sequence[Supplier], *, limit: int = 6) -> list[dict[str, object]]:
    """Suppliers by lifetime spend, largest first, with each one's share of the total."""
    grand = sum(s.spend_minor for s in suppliers)
    ranked = sorted(suppliers, key=lambda s: s.spend_minor, reverse=True)[:limit]
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "category": s.category,
            "spend_minor": s.spend_minor,
            "pct": _pct(s.spend_minor, grand),
        }
        for s in ranked
    ]


def on_time_delivery_pct(
    purchase_orders: Sequence[PurchaseOrder], totals: dict[str, int]
) -> float | None:
    """Real, spend-weighted on-time delivery rate, computed fresh from receipts rather than read
    off `Supplier.on_time_pct` — a figure set once when a supplier is created and never revisited
    by a receipt (dashboard-data audit §3.5 row 13: "a weighted figure is stale seed data").

    Weighted by each received PO's own total (`totals`, the overview's own PO-id → amount map)
    so one large late delivery counts for more than a marginal one — the same intent the stale
    per-supplier figure had, now backed by real rows. `None`, not `0.0`, when nothing has been
    received yet: a 0% rate would claim every delivery was late, when in fact none has landed to
    judge.
    """
    received = [po for po in purchase_orders if po.received_date is not None]
    total_spend = sum(totals.get(str(po.id), 0) for po in received)
    if total_spend == 0:
        return None
    on_time_spend = sum(
        totals.get(str(po.id), 0)
        for po in received
        if po.received_date is not None and po.received_date <= po.expected_date
    )
    return round(on_time_spend / total_spend * 1000) / 10


# ------------------------------------------------------------------ inventory rollups


def inventory_value_minor(items: Sequence[InventoryItem]) -> int:
    """Total value of stock on hand, in minor units — each item's exact milli-unit quantity times
    its unit value, rounded once per item (ADR-0014 §2)."""
    return sum(quantity.value_minor(it.on_hand_milli, it.unit_value_minor) for it in items)


def is_low_stock(item: InventoryItem) -> bool:
    """An item is low when at or below its reorder point (whole units, compared exactly)."""
    return item.on_hand_milli <= item.reorder_point * quantity.QTY_SCALE


def is_out_of_stock(item: InventoryItem) -> bool:
    return item.on_hand_milli == 0


def low_stock_items(items: Sequence[InventoryItem]) -> list[InventoryItem]:
    """The items at or below their reorder point, most-depleted first."""
    low = [it for it in items if is_low_stock(it)]
    low.sort(
        key=lambda it: (
            Fraction(it.on_hand_milli, it.reorder_point * quantity.QTY_SCALE)
            if it.reorder_point
            else Fraction(0)
        )
    )
    return low


def low_stock_count(items: Sequence[InventoryItem]) -> int:
    return sum(1 for it in items if is_low_stock(it))


def out_of_stock_count(items: Sequence[InventoryItem]) -> int:
    return sum(1 for it in items if is_out_of_stock(it))


# ------------------------------------------------------------------ orders (ADR-0025)

#: phone | manual | sales | walk_in — `Order.channel`'s vocabulary.
ORDER_CHANNELS: tuple[str, ...] = ("phone", "manual", "sales", "walk_in")
#: prepaid | cash | credit — `FulfilmentSettings.payment_defaults`' values.
PAYMENT_TERMS: tuple[str, ...] = ("prepaid", "cash", "credit")

#: ADR-0025 §1's exact vocabulary. `COMPLETED`, `CANCELLED` and `REJECTED` are terminal.
ORDER_STATUSES: tuple[str, ...] = (
    "DRAFT",
    "PLACED",
    "ACCEPTED",
    "IN_FULFILMENT",
    "ON_HOLD",
    "READY",
    "HANDED_OVER",
    "DELIVERED",
    "COMPLETED",
    "CANCELLED",
    "REJECTED",
)
ORDER_TERMINAL_STATUSES: frozenset[str] = frozenset({"COMPLETED", "CANCELLED", "REJECTED"})

#: The content of ADR-0025 §1's transition table, in full. `IN_FULFILMENT`, `READY`,
#: `HANDED_OVER`, `DELIVERED` and `COMPLETED` are real edges in this map even though no F2
#: endpoint drives them yet — they wait on `app/fulfilment/`'s pick tasks (F3) and the billing
#: gate (F4/F5). The map is the whole ADR, not only the door F2 builds through it, exactly as
#: `PO_TRANSITIONS` above is the whole PO lifecycle and not only what one phase happened to need.
ORDER_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"PLACED", "CANCELLED"}),
    "PLACED": frozenset({"ACCEPTED", "REJECTED", "CANCELLED"}),
    "ACCEPTED": frozenset({"IN_FULFILMENT", "ON_HOLD", "CANCELLED"}),
    "IN_FULFILMENT": frozenset({"READY", "ON_HOLD", "CANCELLED"}),
    "ON_HOLD": frozenset({"ACCEPTED", "IN_FULFILMENT", "READY", "CANCELLED"}),
    "READY": frozenset({"HANDED_OVER", "ON_HOLD", "CANCELLED"}),
    "HANDED_OVER": frozenset({"DELIVERED"}),
    "DELIVERED": frozenset({"COMPLETED"}),
    "COMPLETED": frozenset(),
    "CANCELLED": frozenset(),
    "REJECTED": frozenset(),
}


def can_transition_order(current: str, new: str) -> bool:
    """Whether an order may move `current` -> `new`. Every other move — including a no-op "move"
    to the same status — answers 409, the same convention `can_transition_po` documents."""
    return new in ORDER_TRANSITIONS.get(current, frozenset())


def order_release_target(*, has_open_tasks: bool, all_tasks_picked: bool) -> str:
    """Where a hold released from `ON_HOLD` goes — **recomputed from facts, never remembered**
    (ADR-0025 §1): no tasks yet (neither open nor picked) -> `ACCEPTED`; open tasks remain ->
    `IN_FULFILMENT`; every task picked -> `READY`.

    `app/orders/services.py` calls this with `has_open_tasks=False, all_tasks_picked=False`
    today: `app/fulfilment/` (F3) has not landed, so there is never a task to ask about, and every
    release lands on `ACCEPTED` until one exists.
    """
    if has_open_tasks:
        return "IN_FULFILMENT"
    if all_tasks_picked:
        return "READY"
    return "ACCEPTED"


def order_line_amount_minor(quantity_milli: int, selling_price_minor: int) -> int:
    """A line's total: `quantity_milli * selling_price_minor / 1000`, rounded half-to-even once
    (ADR-0014 §2's money-times-quantity rule) — `quantity.value_minor`, reused rather than
    reimplemented, since it is exactly the same arithmetic the stock-value rollup already does."""
    return quantity.value_minor(quantity_milli, selling_price_minor)


def order_subtotal_minor(lines: Sequence[OrderLine]) -> int:
    """An order's subtotal — the sum of its lines' `line_total_minor`. A line still unpriced
    (before `place` has run) contributes 0."""
    return sum(line.line_total_minor or 0 for line in lines)


def expected_gst_amount_minor(taxable_minor: int, rate_pct: Decimal) -> int:
    """The GST `request_invoice_for_order` expects finance to compute on one taxable amount —
    **deliberately mirrors** `services/finance/app/domain.py::gst_amount_minor` (same formula,
    same `Decimal`, same `ROUND_HALF_UP`) rather than importing it: no cross-schema import exists
    between services here any more than a cross-schema foreign key does (ADR-0000 §10 is about
    tables, but the same reasoning holds for code — the two must agree by *contract*, which is
    exactly what `evaluate_order_invoice_request`'s own exact-integer-equality check enforces, not
    by silently sharing an implementation that could drift without either side noticing).

    Finance's `split_gst` divides this same total between CGST/SGST or hands it whole to IGST
    depending on `intra_state`, but never re-rounds it doing so — `TaxSplit.total_minor` always
    equals this function's result regardless of the split (`services/finance` ADR-0024 §1 review:
    the two halves sum to the one rounding, not each rounded on its own). So a request that omits
    `place_of_supply` (this build always does — operations tracks no GST state codes yet) is
    still checkable here without knowing finance's own seller registration: `intra_state` cannot
    change the *total*, only how it is split into heads, and this function only ever needs the
    total.
    """
    exact = Decimal(taxable_minor) * rate_pct / Decimal(100)
    return int(exact.to_integral_value(rounding=ROUND_HALF_UP))


#: The order states an online payment may be taken in (ADR-0024 A4; Fulfilment F10): priced
#: (`PLACED` onward — the total is fixed at `place` and never repriced) and not yet gone. After
#: handover the counter or carrier path has already settled payment, and a cancelled or rejected
#: order has nothing to pay for.
ONLINE_PAYMENT_STATUSES: frozenset[str] = frozenset(
    {"PLACED", "ACCEPTED", "IN_FULFILMENT", "ON_HOLD", "READY"}
)


def online_payment_blocker(
    *, number: str, status: str, total_minor: int, paid: bool, payment_term: str
) -> str | None:
    """Why this order cannot be paid online right now, as the sentence a 409 carries — or None.

    The same payment terms the billing gate reads (`check_billing_gate`): `prepaid` and `cash`
    orders are paid before the goods leave, so either may be paid online instead of at the
    counter; a `credit` order is paid against its invoice later, never here."""
    if status not in ONLINE_PAYMENT_STATUSES:
        return f"Order {number} is {status}; there is nothing to pay online"
    if total_minor <= 0:
        return f"Order {number} has no amount to pay"
    if paid:
        return f"Order {number} is already paid"
    if payment_term not in ("prepaid", "cash"):
        return (
            f"Order {number} is on {payment_term} terms; it is paid against its invoice, "
            "not online here"
        )
    return None


# ------------------------------------------------------------------ fulfilment (ADR-0025 §2)

#: ADR-0025 §2's exact vocabulary. `CLOSED` and `CANCELLED` are terminal.
PICK_TASK_STATUSES: tuple[str, ...] = (
    "CREATED",
    "OFFERED",
    "ESCALATED",
    "ACCEPTED",
    "IN_PROGRESS",
    "EXCEPTION",
    "PICKED",
    "CLOSED",
    "CANCELLED",
)
PICK_TASK_TERMINAL_STATUSES: frozenset[str] = frozenset({"CLOSED", "CANCELLED"})

#: The content of ADR-0025 §2's transition table, in full — the same shape `PO_TRANSITIONS` and
#: `ORDER_TRANSITIONS` above use, and the same "even a no-op move to the same status is a miss"
#: convention `can_transition_po`/`can_transition_order` document. A few edges are worth calling
#: out because they read like they might be missing:
#:
#: * There is no `OFFERED -> ESCALATED` edge. A failed round always lands the task on `CREATED`
#:   first (the offer's own failure), and *separately*, `CREATED -> ESCALATED` fires when that
#:   landing finds the round budget spent — two edges taken in the same transaction when a round
#:   is the last one, never a direct hop the ADR's table does not draw.
#: * `PICKED` has exactly one way out, `CLOSED` — a picked task is not cancellable (ADR-0025 §2:
#:   "Its work happened"); reversing it is a putaway task on the order's cancellation, a different
#:   transition on a different record, not this map growing a `PICKED -> CANCELLED` edge.
#: * `ESCALATED -> OFFERED` is a manager assigning by hand — manual assignment is just another
#:   path into `OFFERED`, the same edge `CREATED -> OFFERED` uses, not a second status.
PICK_TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"OFFERED", "ESCALATED", "CANCELLED"}),
    "OFFERED": frozenset({"CREATED", "ACCEPTED", "CANCELLED"}),
    "ESCALATED": frozenset({"OFFERED", "CANCELLED"}),
    "ACCEPTED": frozenset({"CREATED", "IN_PROGRESS", "CANCELLED"}),
    "IN_PROGRESS": frozenset({"EXCEPTION", "PICKED", "CANCELLED"}),
    "EXCEPTION": frozenset({"IN_PROGRESS", "CANCELLED"}),
    "PICKED": frozenset({"CLOSED"}),
    "CLOSED": frozenset(),
    "CANCELLED": frozenset(),
}

#: A task in one of these statuses has a live worker actively holding it — cancelling out from
#: here is the "stronger permission on an edge that IS in the map" ADR-0025 §2 describes for
#: `operations.fulfilment.task.override`; cancelling from any other open status is ordinary
#: dispatcher work (`operations.fulfilment.task.assign`).
PICK_TASK_OVERRIDE_REQUIRED_FOR_CANCEL: frozenset[str] = frozenset(
    {"ACCEPTED", "IN_PROGRESS", "EXCEPTION"}
)


def can_transition_pick_task(current: str, new: str) -> bool:
    """Whether a pick task may move `current` -> `new`. Every other move — including a no-op
    "move" to the same status — answers 409, the same convention `can_transition_order`
    documents."""
    return new in PICK_TASK_TRANSITIONS.get(current, frozenset())


#: `AssignmentOffer.state`'s exact vocabulary (ADR-0025 §3).
OFFER_STATES: tuple[str, ...] = (
    "OFFERED",
    "ACCEPTED",
    "DECLINED",
    "EXPIRED",
    "WITHDRAWN",
    "RELEASED",
)
#: An offer in one of these states is no longer answerable — the conditional updates in
#: `app.fulfilment.services` all guard on the complement of this set (`state = 'OFFERED'`).
OFFER_TERMINAL_STATES: frozenset[str] = frozenset(
    {"ACCEPTED", "DECLINED", "EXPIRED", "WITHDRAWN", "RELEASED"}
)

#: `pick_exceptions.reason`'s vocabulary (plan F3: "short pick, damaged, not found").
PICK_EXCEPTION_REASONS: tuple[str, ...] = ("short_pick", "damaged", "not_found", "other")

#: `worker_presence.status`'s vocabulary.
WORKER_PRESENCE_STATES: tuple[str, ...] = ("online", "offline")

#: `packages.status`'s vocabulary — F3 always writes `PACKED` directly (`Package`'s own
#: docstring); `OPEN` is reserved for a later phase that splits pack into steps.
PACKAGE_STATUSES: tuple[str, ...] = ("OPEN", "PACKED")


#: A task with a live worker still working it, or waiting to be — "open" in the exact reading
#: `app.fulfilment.services._maybe_ready_order` and `order_release_target`'s own caller
#: (`app.orders.services.release_hold_order`, MAJOR 1 #2, security review) both need. Kept here,
#: not only as a private constant in `app.fulfilment.services`, so `app/orders/` can share the
#: exact same vocabulary without importing `app.fulfilment` (ADR-0024 §1 forbids that direction).
PICK_TASK_OPEN_STATUSES: frozenset[str] = frozenset(
    {"CREATED", "OFFERED", "ESCALATED", "ACCEPTED", "IN_PROGRESS", "EXCEPTION"}
)
#: A task whose picking work is done — `PICKED` or already `CLOSED` — the other half of the same
#: reading.
PICK_TASK_SETTLED_STATUSES: frozenset[str] = frozenset({"PICKED", "CLOSED"})


def pick_task_line_settled(*, quantity_milli: int, picked_milli: int, short_milli: int) -> bool:
    """A task line is settled once every one of its units is accounted for — picked, or formally
    shorted — never left implicit. `PickTask` reaches `PICKED` only once every one of its lines
    is settled (plan F3's exit criterion: a short line is visibly flagged, not silently dropped)."""
    return picked_milli + short_milli >= quantity_milli


# ------------------------------------------------------------------ dispatch (ADR-0024 §6;
# Fulfilment plan F5)

#: `Shipment.status`'s exact vocabulary — the carrier lifecycle, kept on its own record apart
#: from `orders.status` the same way pick/offer/invoice/payment status already are (ADR-0025 §1).
#: `app.dispatch.services.dispatch_order` creates a shipment already at `PICKUP_HANDOVER` (the
#: carrier has physically taken the goods, in the same call that moves the order `READY ->
#: HANDED_OVER -> DELIVERED`) — there is no separate `CREATED` state to transition out of.
#: `DELIVERED` and `RETURNED` are terminal; `FAILED` is not — a failed attempt can be retried
#: (`-> IN_TRANSIT`) or sent back (`-> RETURNED`).
SHIPMENT_STATUSES: tuple[str, ...] = (
    "PICKUP_HANDOVER",
    "IN_TRANSIT",
    "DELIVERED",
    "FAILED",
    "RETURNED",
)
SHIPMENT_TERMINAL_STATUSES: frozenset[str] = frozenset({"DELIVERED", "RETURNED"})

#: The whole shipment lifecycle Fulfilment plan F5 names ("pickup handover, in-transit,
#: delivered, failed, returned"), in the same `dict[str, frozenset[str]]` shape
#: `PO_TRANSITIONS`/`ORDER_TRANSITIONS`/`PICK_TASK_TRANSITIONS` above use, and the same "no
#: self-edges, an unlisted move is 409" convention `can_transition_po` documents. `DELIVERED` is
#: reached only from `IN_TRANSIT` — going straight from `PICKUP_HANDOVER` with no transit leg
#: ever recorded is not an edge this build offers; a same-day local collection with nothing to
#: track in transit is the counter path (`app.orders.services.hand_over_order`), not this one.
#: `PICKUP_HANDOVER -> FAILED`/`-> RETURNED` are real edges (an attempt or a refusal can be
#: recorded before any transit update ever lands); `FAILED -> IN_TRANSIT` is a retried attempt.
SHIPMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "PICKUP_HANDOVER": frozenset({"IN_TRANSIT", "FAILED", "RETURNED"}),
    "IN_TRANSIT": frozenset({"DELIVERED", "FAILED", "RETURNED"}),
    "FAILED": frozenset({"IN_TRANSIT", "RETURNED"}),
    "DELIVERED": frozenset(),
    "RETURNED": frozenset(),
}


def can_transition_shipment(current: str, new: str) -> bool:
    """Whether a shipment may move `current` -> `new`. Every other move — including a no-op
    "move" to the same status — answers 409, the same convention `can_transition_order`/
    `can_transition_pick_task` document."""
    return new in SHIPMENT_TRANSITIONS.get(current, frozenset())


# ------------------------------------------------------------------ fulfilment auto-assignment
# (ADR-0024 §9: "every fulfilment decision is a pure function ... with a total order so the same
# inputs always give the same answer"; this build closes the gap ADR-0025 §2 left open: "Until
# auto-assignment ships ... every offer is a manual one")


@dataclass(frozen=True, slots=True)
class WorkerCandidate:
    """One worker's eligibility/ranking inputs for one auto-assignment pass — plain values, no
    ORM, no session (`app.fulfilment.services._worker_candidates` builds these from a handful of
    batched selector queries), so `eligible_workers_for_task`/`rank_workers_for_offer` are
    testable without a database, exactly like `PICK_TASK_TRANSITIONS`/`can_transition_pick_task`
    above."""

    worker_id: uuid.UUID
    is_active: bool
    #: `WORKER_PRESENCE_STATES` — "online" | "offline".
    presence_status: str
    zone_location_ids: frozenset[uuid.UUID]
    skills: frozenset[str]
    #: This worker's current count of live offers (`OFFERED`/`ACCEPTED`) — the ranking's "current
    #: open-task workload" (ADR-0024 §9), since at most one live offer exists per task
    #: (`uq_assignment_offers_org_task_live`), so counting live offers **is** counting open tasks.
    open_task_count: int
    #: The most recent `offered_at` across every round this worker has ever been offered, live or
    #: not — `None` for a worker never offered anything, which ranks as the most idle of all.
    last_offered_at: datetime | None = None


def is_worker_eligible_for_task(
    candidate: WorkerCandidate,
    *,
    zone_location_id: uuid.UUID | None,
    required_skills: frozenset[str],
    excluded_worker_ids: frozenset[uuid.UUID] = frozenset(),
) -> bool:
    """Eligibility, in the exact order this build's own scope names it: active, presence online,
    zone access (a task whose lines could not all be grouped under one zone — a single-bin shop —
    has `zone_location_id is None` and imposes no zone requirement at all, mirroring
    `offer_task`'s own manual-path check), every skill the task's lines require is one this worker
    holds (a task with no `required_skill`'d lines imposes no skill requirement — the common case,
    since `InventoryItem.required_skill` defaults `NULL`), and not previously excluded.

    `excluded_worker_ids` is ADR-0025 §2's own rule, carried over unchanged: a worker whose offer
    on *this task* was declined, expired or released "is excluded from every later automatic round
    of this task (a manager may still pick them by hand)". Without it, a worker who just declined
    would usually still rank first next round (nothing else about them changed) and the engine
    would re-offer them forever until the round budget ran out — the exact ping-pong that sentence
    exists to prevent. A manual override (`offer_task`'s own `has_override` path) is untouched by
    this: it never calls through here at all."""
    if candidate.worker_id in excluded_worker_ids:
        return False
    if not candidate.is_active:
        return False
    if candidate.presence_status != "online":
        return False
    if zone_location_id is not None and zone_location_id not in candidate.zone_location_ids:
        return False
    return required_skills.issubset(candidate.skills)


def eligible_workers_for_task(
    candidates: Sequence[WorkerCandidate],
    *,
    zone_location_id: uuid.UUID | None,
    required_skills: frozenset[str],
    excluded_worker_ids: frozenset[uuid.UUID] = frozenset(),
) -> list[WorkerCandidate]:
    """Every candidate `is_worker_eligible_for_task` admits, in input order (ranking is a separate
    step, `rank_workers_for_offer` below) — an empty list is a legitimate answer, not an error:
    the caller turns "nobody eligible" into a visible, dispatcher-facing reason rather than a
    silently stuck task."""
    return [
        c
        for c in candidates
        if is_worker_eligible_for_task(
            c,
            zone_location_id=zone_location_id,
            required_skills=required_skills,
            excluded_worker_ids=excluded_worker_ids,
        )
    ]


def rank_workers_for_offer(candidates: Sequence[WorkerCandidate]) -> list[WorkerCandidate]:
    """Deterministic ranking among **already eligible** workers (ADR-0024 §9's own example:
    "open-task count, then idle time ... a final tie-break on id"): fewest open tasks first, then
    longest idle first (a worker never yet offered anything sorts before one offered a moment
    ago), then worker id ascending so two workers tied on both keys still resolve to one, stable
    answer regardless of query or dict iteration order — the same total-order guarantee
    `PICK_TASK_TRANSITIONS`'s own module comment documents for the state maps."""
    epoch = datetime.min.replace(tzinfo=UTC)
    return sorted(
        candidates,
        key=lambda c: (c.open_task_count, c.last_offered_at or epoch, str(c.worker_id)),
    )


def select_worker_for_offer(
    candidates: Sequence[WorkerCandidate],
    *,
    zone_location_id: uuid.UUID | None,
    required_skills: frozenset[str],
    excluded_worker_ids: frozenset[uuid.UUID] = frozenset(),
) -> WorkerCandidate | None:
    """The whole auto-assignment decision in one pure call: eligible, then ranked, then the front
    of the queue — or `None` when nobody qualifies. `app.fulfilment.services.attempt_auto_assign`
    is the only caller; everything about *why* a worker was or was not chosen is decidable from
    this function's own inputs and outputs alone, which is what "explainable to a warehouse lead"
    (ADR-0024 §9) means in practice."""
    ranked = rank_workers_for_offer(
        eligible_workers_for_task(
            candidates,
            zone_location_id=zone_location_id,
            required_skills=required_skills,
            excluded_worker_ids=excluded_worker_ids,
        )
    )
    return ranked[0] if ranked else None


# ------------------------------------------------------------------ counting (ADR-0026, plan F8)

#: A count session's lifecycle. `ACTIVE` is where workers submit counts; a manager moves it to
#: `REVIEW` to stop accepting new counts and start approving/rejecting discrepancies; `CLOSED` is
#: only reachable once every line's `review_status` has left `PENDING` (checked in
#: `app.counting.services.close_session`, not representable in this map alone). `CANCELLED` is
#: reachable from either open state — abandoning a count posts nothing for any line that had not
#: already been approved.
COUNT_SESSION_TRANSITIONS: dict[str, frozenset[str]] = {
    "ACTIVE": frozenset({"REVIEW", "CANCELLED"}),
    "REVIEW": frozenset({"CLOSED", "CANCELLED"}),
    "CLOSED": frozenset(),
    "CANCELLED": frozenset(),
}


def can_transition_count_session(current: str, new: str) -> bool:
    """Whether a count session may move `current` -> `new`. Every other move — including a no-op
    "move" to the same status — answers 409, the same convention `can_transition_pick_task`/
    `can_transition_shipment` document."""
    return new in COUNT_SESSION_TRANSITIONS.get(current, frozenset())


# ------------------------------------------------------------------ operational alerts (plan
# §4; F12) — `OperationalAlert.kind`'s exact vocabulary.

ALERT_KINDS: tuple[str, ...] = ("low_stock", "task_delayed", "shipment_delayed")


class _AlertContactSource(Protocol):
    """Structural, not `FulfilmentSettings` itself: `app.alerts.selectors.AlertSettings` (the
    sweep's own lightweight resolution, a frozen dataclass) and the ORM row both satisfy this,
    and `domain` may not import `app.alerts` (it sits above `domain` in the service anatomy).
    Read-only properties, not plain attributes: a frozen dataclass's fields are read-only from
    the type checker's own view, and a protocol member only both sides can satisfy is one
    neither is required to let this function *write*, which nothing here ever does."""

    @property
    def alert_email(self) -> str | None: ...
    @property
    def alert_phone(self) -> str | None: ...


def alert_contact(settings: _AlertContactSource) -> dict[str, str | None]:
    """The `payload.alert_contact` block every alert event carries — an organization's resolved
    fulfilment settings, the same three-step (warehouse row, else org row, else hardcoded
    default) preference `task_sla_minutes` is resolved through. Both fields null is a valid
    answer: an organization that has not opted in gets an alert row (`GET /operations/alerts`)
    but no automation can address it from the event payload alone."""
    return {"email": settings.alert_email, "phone": settings.alert_phone}


# ------------------------------------------------------------------ needs-attention queue


def attention_items(
    pos: Sequence[PurchaseOrder],
    inventory: Sequence[InventoryItem],
    today: date,
) -> list[dict[str, object]]:
    """The "needs attention" queue: out-of-stock and low-stock items, plus late deliveries,
    ordered by severity (danger first). Each item names what is wrong and links to where it can
    be fixed — mirrors the SPA's `attentionItems`."""
    stock: list[dict[str, object]] = []
    for item in low_stock_items(inventory):
        out = is_out_of_stock(item)
        stock.append(
            {
                "id": f"stock-{item.id}",
                "kind": "out_of_stock" if out else "low_stock",
                "title": item.name,
                "detail": (
                    f"Out of stock at {item.warehouse}"
                    if out
                    else f"{quantity.describe(item.on_hand_milli, item.uom)} on hand · "
                    f"reorder at {item.reorder_point}"
                ),
                "href": "/inventory",
                "tone": "danger" if out else "late",
            }
        )

    late_pos = sorted(
        (po for po in pos if is_late(po, today)),
        key=lambda po: (po.expected_date - today).days,
    )
    late: list[dict[str, object]] = []
    for po in late_pos:
        overdue = abs((po.expected_date - today).days)
        late.append(
            {
                "id": f"late-{po.id}",
                "kind": "late_delivery",
                "title": f"{po.number} · {po.supplier_name}",
                "detail": (
                    f"{overdue} day{'' if overdue == 1 else 's'} overdue · "
                    f"expected at {po.warehouse}"
                ),
                "href": f"/purchase-orders/{po.id}",
                "tone": "danger",
            }
        )

    # Danger (out-of-stock, late) first, then low-stock warnings.
    combined = late + stock
    combined.sort(key=lambda a: 0 if a["tone"] == "danger" else 1)
    return combined


# ------------------------------------------------------------------ warehouse geometry (plan §2/
# F7) — layout-version lifecycle, draft validation, and the walk-graph routing algorithm. Every
# function here is pure (ADR-0024 §9: deterministic, testable without a database, explainable) —
# `app.warehouse.selectors`/`.services` load rows and hand plain values in.

#: draft -> published is the ordinary publish; published -> archived happens as a *side effect* of
#: publishing a different version, never a direct caller-requested move, so it is not an edge
#: here. archived -> published is a rollback (`publish_layout_version` called again on an old
#: version). draft has no edge back from anywhere — an abandoned draft is discarded outright
#: (`DELETE /layout-versions/{id}`), never reached by "transitioning" a stale one into a fresh
#: draft.
LAYOUT_VERSION_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"published"}),
    "published": frozenset(),
    "archived": frozenset({"published"}),
}


def can_transition_layout_version(current: str, new: str) -> bool:
    """Whether a layout version may move `current` -> `new` by a direct publish/rollback call.
    The complementary `published -> archived` move is written by `publish_layout_version` itself,
    on the version being *superseded* — never requested as a transition of its own."""
    return new in LAYOUT_VERSION_TRANSITIONS.get(current, frozenset())


@dataclass(frozen=True, slots=True)
class PlacementBox:
    """One location's footprint on a floor, in the units `app.warehouse.selectors` reads off
    `LocationPlacement` — enough to check for overlap and nothing else, so this function has no
    reason to touch the database."""

    location_id: uuid.UUID
    x_mm: int
    y_mm: int
    width_mm: int
    depth_mm: int


def _boxes_overlap(a: PlacementBox, b: PlacementBox) -> bool:
    """Axis-aligned bounding-box intersection — strict, so two boxes that merely touch edges
    (`a`'s right edge exactly at `b`'s left edge) do not count as overlapping."""
    return (
        a.x_mm < b.x_mm + b.width_mm
        and b.x_mm < a.x_mm + a.width_mm
        and a.y_mm < b.y_mm + b.depth_mm
        and b.y_mm < a.y_mm + a.depth_mm
    )


def overlapping_placements(
    placements: Sequence[PlacementBox],
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Every pair of placements whose footprints overlap, each pair reported once. O(n²) — a
    floor's placement count is admin-authored geometry, not a live-traffic table, so the simple
    pairwise check is the right trade against a spatial index nothing here needs yet. Sorted
    input so the result (and which of a pair is first) is stable across calls regardless of the
    order the caller happened to load rows in."""
    items = sorted(placements, key=lambda p: str(p.location_id))
    pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            if _boxes_overlap(a, b):
                pairs.append((a.location_id, b.location_id))
    return pairs


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One `WalkNode`, reduced to what connectivity and routing need."""

    id: uuid.UUID
    location_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One `WalkEdge`, reduced to what connectivity and routing need."""

    from_node_id: uuid.UUID
    to_node_id: uuid.UUID
    distance_mm: int
    is_traversable: bool
    bidirectional: bool


def disconnected_required_nodes(
    nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]
) -> list[uuid.UUID]:
    """Which "required" nodes — the ones standing for an actual location, `location_id is not
    None` — cannot reach the rest of the required nodes over *any* traversable edge.

    Connectivity here is checked as an **undirected** graph even though `bidirectional=False`
    edges make routing itself directed: two one-way edges pointing opposite ways would otherwise
    print as "connected" for validation while being unusable for a real round trip (walk out one
    way, and there is no way back). This is deliberately the more conservative reading — it can
    flag a graph a one-way-aware search would still route through, never the other way round.

    Zero or one required node is trivially fully connected (nothing else to reach), so both
    return `[]`. Junction-only nodes (`location_id is None`) still participate as connective
    tissue between two required nodes; they are just never themselves reported as disconnected.
    """
    required = {n.id for n in nodes if n.location_id is not None}
    if len(required) <= 1:
        return []

    adjacency: dict[uuid.UUID, set[uuid.UUID]] = {n.id: set() for n in nodes}
    for e in edges:
        if not e.is_traversable:
            continue
        if e.from_node_id not in adjacency or e.to_node_id not in adjacency:
            continue  # defensive — an edge naming a node outside this graph
        adjacency[e.from_node_id].add(e.to_node_id)
        adjacency[e.to_node_id].add(e.from_node_id)  # undirected for connectivity, see docstring

    # Flood-fill from one required node; every required node not reached is disconnected from it
    # — and, because "connected to" is symmetric here, from every other required node too.
    start = min(required, key=str)
    seen: set[uuid.UUID] = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for neighbour in adjacency.get(current, ()):
            if neighbour not in seen:
                seen.add(neighbour)
                frontier.append(neighbour)

    return sorted((required - seen), key=str)


@dataclass(frozen=True, slots=True)
class RoutePlan:
    """A computed walk path: the ordered nodes it passes through, and its total distance."""

    node_ids: list[uuid.UUID]
    distance_mm: int


def shortest_walk_path(
    nodes: Sequence[GraphNode],
    edges: Sequence[GraphEdge],
    start_node_id: uuid.UUID,
    end_node_id: uuid.UUID,
) -> RoutePlan | None:
    """Dijkstra's algorithm over a floor's walk graph, weighted by `distance_mm`.

    **Why Dijkstra, not A\\*:** every `WalkEdge.distance_mm` is a real, non-negative walking
    distance (the `distance_positive` CHECK), which is exactly Dijkstra's precondition, and a
    warehouse floor's graph is small (tens to low hundreds of nodes — an admin draws it by hand).
    A\\* only buys anything over Dijkstra when an admissible heuristic prunes a search space too
    big to explore in full; at this scale the extra machinery (and the risk of a heuristic that is
    accidentally inadmissible, which silently breaks optimality rather than merely slowing down)
    is not worth it for the distance it saves. Both are equally deterministic; this codebase's own
    total-order requirement (ADR-0024 §9) is met by tie-breaking on node id below, not by which of
    the two algorithms is chosen.

    Returns `None` when either endpoint is not in `nodes` at all, or no traversable path connects
    them (a disconnected graph, or every connecting edge currently blocked) — never raises, so the
    caller (`app.warehouse.selectors.compute_route`) turns "no path" into its own 409, not this
    function's job to know about HTTP.
    """
    node_ids = {n.id for n in nodes}
    if start_node_id not in node_ids or end_node_id not in node_ids:
        return None
    if start_node_id == end_node_id:
        return RoutePlan(node_ids=[start_node_id], distance_mm=0)

    # Adjacency, pre-sorted by (weight, neighbour id) so that whenever two edges out of the same
    # node tie exactly on distance, the neighbour explored — and so recorded as `prev` — is always
    # the same one regardless of the order the caller's `edges` happened to list them in.
    adjacency: dict[uuid.UUID, list[tuple[uuid.UUID, int]]] = {n.id: [] for n in nodes}
    for e in edges:
        if not e.is_traversable:
            continue
        if e.from_node_id not in adjacency or e.to_node_id not in adjacency:
            continue  # defensive — an edge naming a node outside this graph
        adjacency[e.from_node_id].append((e.to_node_id, e.distance_mm))
        if e.bidirectional:
            adjacency[e.to_node_id].append((e.from_node_id, e.distance_mm))
    for neighbours in adjacency.values():
        neighbours.sort(key=lambda pair: (pair[1], str(pair[0])))

    INF = float("inf")
    best: dict[uuid.UUID, float] = {n.id: INF for n in nodes}
    prev: dict[uuid.UUID, uuid.UUID | None] = {n.id: None for n in nodes}
    best[start_node_id] = 0
    visited: set[uuid.UUID] = set()
    # `str(node_id)` as the tie-break key on the heap: two entries at the same distance pop in a
    # fixed order regardless of insertion order, so a tie in the frontier never makes the result
    # depend on dict/set iteration order.
    heap: list[tuple[float, str, uuid.UUID]] = [(0.0, str(start_node_id), start_node_id)]
    while heap:
        dist, _, node_id = heapq.heappop(heap)
        if node_id in visited:
            continue
        visited.add(node_id)
        if node_id == end_node_id:
            break
        for neighbour_id, weight in adjacency[node_id]:
            if neighbour_id in visited:
                continue
            candidate = dist + weight
            if candidate < best[neighbour_id]:
                best[neighbour_id] = candidate
                prev[neighbour_id] = node_id
                heapq.heappush(heap, (candidate, str(neighbour_id), neighbour_id))

    if best[end_node_id] == INF:
        return None

    path: list[uuid.UUID] = []
    cursor: uuid.UUID | None = end_node_id
    while cursor is not None:
        path.append(cursor)
        cursor = prev[cursor]
    path.reverse()
    return RoutePlan(node_ids=path, distance_mm=int(best[end_node_id]))
