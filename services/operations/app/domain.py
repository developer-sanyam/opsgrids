"""Pure rollups and vocabulary for operations. No I/O, no session — only model instances in,
plain values out, so this is trivially testable and mirrors the SPA's `data/derive.ts`.

The overview's KPIs, the spend-by-category bars, the PO-status donut, the spend trend and the
"needs attention" queue all derive from the same purchase-order / inventory / supplier rows the
tables render, exactly as the SPA derives them, so the numbers agree whichever side computes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

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
