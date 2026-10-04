"""Receiving rules, as pure functions (inventory Phase 2). No I/O, no clock, no database.

Everything a goods receipt *decides* — how much is an over-receipt, what a box of 12 is in pieces,
what state a purchase order is in after a delivery — lives here so it can be tested exhaustively
with exact arithmetic, and so the service that posts a receipt only has to apply it.

All quantities are integer **milli-units** of the item's stock UOM (ADR-0014 §2). Tolerance is
integer **basis points** (100 bp = 1 %), so a limit is `ordered * bp // 10_000` — floored, which
means a tolerance can never allow even one milli-unit more than it says. Conversions between a
container unit and the stock unit are `Fraction`s through `app.inventory.quantity`, never floats.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from math import gcd

from app.inventory import quantity
from app.inventory.quantity import QuantityError

# ------------------------------------------------------------------ requisition state machine

#: DRAFT -> SUBMITTED (the requester asks) -> APPROVED (someone with `requisition.approve`) ->
#: CONVERTED (a buyer turned it into a draft purchase order). SUBMITTED may instead be REJECTED;
#: anything not yet converted may be CANCELLED. REJECTED, CONVERTED and CANCELLED are terminal.
#: A rejected requisition is not reopened — the requester raises a new one — so the audit trail
#: never has a decision undone.
REQUISITION_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"SUBMITTED", "CANCELLED"}),
    "SUBMITTED": frozenset({"APPROVED", "REJECTED", "CANCELLED"}),
    "APPROVED": frozenset({"CONVERTED", "CANCELLED"}),
    "REJECTED": frozenset(),
    "CONVERTED": frozenset(),
    "CANCELLED": frozenset(),
}


def can_transition_requisition(current: str, new: str) -> bool:
    """Whether a requisition may move `current` -> `new`; a no-op "move" is a miss too, the same
    convention `app.domain.can_transition_po` documents."""
    return new in REQUISITION_TRANSITIONS.get(current, frozenset())


# ------------------------------------------------------------------ tolerance

BP_SCALE = 10_000
_PCT = re.compile(r"^(?P<whole>\d{1,3})(?:\.(?P<frac>\d{1,2}))?$")


def parse_tolerance_pct(value: str) -> int:
    """A wire percentage (`"5"`, `"2.5"`, `"0.25"`) -> basis points, exactly. At most two decimals,
    0 to 100 inclusive; refuses anything else instead of rounding it."""
    match = _PCT.match(value.strip())
    if match is None:
        raise ValueError("A tolerance is a percentage with at most two decimals, e.g. 2.5")
    bp = int(match["whole"]) * 100 + int((match["frac"] or "").ljust(2, "0") or "0")
    if bp > BP_SCALE:
        raise ValueError("A tolerance cannot exceed 100 %")
    return bp


def format_tolerance_pct(bp: int) -> str:
    """Basis points -> the exact percentage string, no trailing zeros (`250` -> `"2.5"`)."""
    whole, frac = divmod(bp, 100)
    if not frac:
        return str(whole)
    return f"{whole}.{str(frac).rjust(2, '0').rstrip('0')}"


def tolerance_limit_milli(ordered_milli: int, tolerance_bp: int) -> int:
    """The most a line may have received in total: the ordered quantity plus the tolerance,
    floored to a whole milli-unit (so 5 % of 82.5 m ordered is 4.125 m exactly, and 5 % of a
    quantity that is not a multiple of 20 milli rounds *down*, never up)."""
    return ordered_milli + ordered_milli * tolerance_bp // BP_SCALE


def classify_outcome(*, ordered_milli: int, cumulative_milli: int, close_short: bool) -> str:
    """How the receipt stands against the line once it is posted: `EXACT` (cumulative equals
    ordered), `OVER` (more than ordered, within tolerance — the caller has already refused
    anything beyond it), `SHORT` (less than ordered and the line was closed short) or `PARTIAL`
    (less than ordered, more is still expected)."""
    if cumulative_milli == ordered_milli:
        return "EXACT"
    if cumulative_milli > ordered_milli:
        return "OVER"
    return "SHORT" if close_short else "PARTIAL"


def line_is_satisfied(*, ordered_milli: int, received_milli: int, short_closed: bool) -> bool:
    """A line no longer holds the purchase order open: fully received (or over), or closed short."""
    return short_closed or received_milli >= ordered_milli


@dataclass(frozen=True, slots=True)
class LineState:
    ordered_milli: int
    received_milli: int
    short_closed: bool


def po_status_after(current: str, lines: Iterable[LineState]) -> str:
    """The purchase order's status once every line's *new* state is known.

    Every line satisfied -> `received`. Otherwise, if anything has been received or closed short
    -> `partially_received`. Otherwise the order is untouched and keeps `current`.
    """
    states = list(lines)
    if states and all(
        line_is_satisfied(
            ordered_milli=s.ordered_milli,
            received_milli=s.received_milli,
            short_closed=s.short_closed,
        )
        for s in states
    ):
        return "received"
    if any(s.received_milli > 0 or s.short_closed for s in states):
        return "partially_received"
    return current


# ------------------------------------------------------------------ unit conversion


def reduce_ratio(numerator: int, denominator: int) -> tuple[int, int]:
    """`24/2` -> `12/1`. A ratio is stored in lowest terms so two spellings of one fact are one."""
    if numerator <= 0 or denominator <= 0:
        raise QuantityError("A unit conversion ratio is a positive numerator and denominator")
    divisor = gcd(numerator, denominator)
    return numerator // divisor, denominator // divisor


#: item-specific container ratios, keyed by canonical unit code: 1 `code` = num/den stock units.
Ratios = Mapping[str, tuple[int, int]]


def to_stock_milli(milli: int, from_uom: str, stock_uom: str, ratios: Ratios) -> int:
    """`milli` milli-units of `from_uom`, as milli-units of the item's `stock_uom`, exactly.

    In order: the same unit (identity); an item-specific ratio (`box` = 12 `pcs`, from
    `uom_conversions`), applied as `milli * num / den` and refused unless the result is a whole
    milli-unit (and a whole unit for a discrete stock UOM); otherwise a fixed registry conversion
    within one dimension (`cm` -> `m`). Anything else raises `QuantityError` — there is no guessing
    a ratio nobody recorded.
    """
    source = quantity.canonical_uom_code(from_uom)
    target = quantity.canonical_uom_code(stock_uom)
    if source == target:
        return milli
    ratio = ratios.get(source)
    if ratio is not None:
        return quantity.convert_by_ratio(
            milli, ratio[0], ratio[1], discrete=quantity.is_discrete(target)
        )
    return quantity.convert(milli, source, target)


def stock_units_per(from_uom: str, stock_uom: str, ratios: Ratios) -> Fraction | None:
    """How many stock units one `from_uom` is, as an exact fraction — `None` when no conversion is
    known (the caller decides what that means: a receipt refuses, a cost falls back)."""
    source = quantity.canonical_uom_code(from_uom)
    target = quantity.canonical_uom_code(stock_uom)
    if source == target:
        return Fraction(1)
    ratio = ratios.get(source)
    if ratio is not None:
        return Fraction(ratio[0], ratio[1])
    src, dst = quantity.unit(source), quantity.unit(target)
    if src is None or dst is None or src.dimension != dst.dimension:
        return None
    return src.to_base / dst.to_base


def unit_cost_per_stock_unit(
    price_minor: int, price_uom: str, stock_uom: str, ratios: Ratios
) -> int:
    """A purchase-order line price (integer minor units per one `price_uom`) as a price per one
    *stock* unit, rounded half-to-even once to a whole minor unit. Identical units, or units with
    no known relation (legacy POs priced in an unregistered unit), pass the price through
    unchanged — exactly what the single-shot receipt always did."""
    per = stock_units_per(price_uom, stock_uom, ratios)
    if per is None or per == 1:
        return price_minor
    return round(Fraction(price_minor) / per)


def weighted_unit_cost(parts: Iterable[tuple[int, int]]) -> int:
    """Quantity-weighted mean of `(quantity_milli, unit_cost_minor)` pairs, half-to-even — used
    when two receipt lines land in one bucket and the ledger holds one cost per bucket-line."""
    pairs = list(parts)
    total = sum(q for q, _ in pairs)
    if total <= 0:
        raise QuantityError("A weighted cost needs a positive quantity")
    return round(Fraction(sum(q * c for q, c in pairs), total))
