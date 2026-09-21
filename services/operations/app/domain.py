"""Pure rollups and vocabulary for operations. No I/O, no session — only model instances in,
plain values out, so this is trivially testable and mirrors the SPA's `data/derive.ts`.

The overview's KPIs, the spend-by-category bars, the PO-status donut, the spend trend and the
"needs attention" queue all derive from the same purchase-order / inventory / supplier rows the
tables render, exactly as the SPA derives them, so the numbers agree whichever side computes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.models import InventoryItem, PurchaseOrder, PurchaseOrderLine, Supplier

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
#: forever, inflating `open_pos` and the late-delivery attention queue). Receiving directly from
#: `draft` is deliberately still allowed — approval is a commitment step, not a prerequisite gate
#: on receiving; tightening that is a product decision, not a CRUD gap.
PO_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"approved", "received", "cancelled"}),
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


def weighted_on_time_pct(suppliers: Sequence[Supplier]) -> float:
    """On-time delivery rate across suppliers, weighted by spend so a big supplier's record
    counts for more than a marginal one. Returns a 0-100 percentage."""
    total_spend = sum(s.spend_minor for s in suppliers)
    if total_spend == 0:
        return 0.0
    weighted = sum(float(s.on_time_pct) * s.spend_minor for s in suppliers)
    return round(weighted / total_spend * 10) / 10


# ------------------------------------------------------------------ inventory rollups


def inventory_value_minor(items: Sequence[InventoryItem]) -> int:
    """Total value of stock on hand, in minor units."""
    return sum(it.on_hand * it.unit_value_minor for it in items)


def is_low_stock(item: InventoryItem) -> bool:
    """An item is low when at or below its reorder point."""
    return item.on_hand <= item.reorder_point


def is_out_of_stock(item: InventoryItem) -> bool:
    return item.on_hand == 0


def low_stock_items(items: Sequence[InventoryItem]) -> list[InventoryItem]:
    """The items at or below their reorder point, most-depleted first."""
    low = [it for it in items if is_low_stock(it)]
    low.sort(key=lambda it: it.on_hand / it.reorder_point if it.reorder_point else 0.0)
    return low


def low_stock_count(items: Sequence[InventoryItem]) -> int:
    return sum(1 for it in items if is_low_stock(it))


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
                    else f"{item.on_hand} {item.uom} on hand · reorder at {item.reorder_point}"
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
