"""Exact stock quantities (ADR-0014 §2). Pure functions, no I/O.

A quantity is an `int` of **milli-units of the item's stock UOM**: 82.5 m is `82500`, 14.8 kg is
`14800`, one piece is `1000`. One scale for every unit, so arithmetic between quantities of the
same item is plain integer arithmetic and never rounds.

Floats never enter: `parse_qty` accepts a decimal *string* (or an `int` of whole units) and
refuses anything with more than three decimals instead of rounding it; `format_qty` renders the
exact decimal string the wire carries. Conversions between units use `Fraction`, and a result that
is not a whole milli-unit raises unless the caller names a rounding mode.

Only **fixed** conversions live here (1 m = 1000 mm, 1 kg = 1000 g). A roll's length-to-weight
ratio is a measurement of that roll, and "box = 12 pcs" is a fact about one item — neither is a
conversion this module will perform.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

#: Milli-units per unit. Changing this is a rewrite of every quantity ever stored.
QTY_SCALE = 1000

#: Three decimals at most; an optional sign; no exponent, no thousands separator, no spaces.
_DECIMAL = re.compile(r"^(?P<sign>[+-])?(?P<whole>\d+)(?:\.(?P<frac>\d{1,3}))?$")

#: A BIGINT holds 9_223_372_036_854_775_807; keep headroom so a SUM of many rows cannot overflow
#: before the database's own CHECK would catch it.
MAX_ABS_MILLI = 10**15


class QuantityError(ValueError):
    """A quantity that cannot be represented exactly, or a unit that cannot be converted."""


class Rounding(Enum):
    """How an inexact conversion is resolved — only ever chosen explicitly by a caller."""

    HALF_EVEN = "half_even"
    UP = "up"  # away from zero: never under-issue
    DOWN = "down"  # toward zero: never over-promise


@dataclass(frozen=True, slots=True)
class Unit:
    code: str
    #: length | mass | count | area | volume
    dimension: str
    #: Size of one of this unit in the dimension's base unit (m, kg, pcs, m2, l).
    to_base: Fraction
    #: Discrete units (a piece, a box) cannot be held in fractions.
    discrete: bool = False


def _units(*units: Unit) -> dict[str, Unit]:
    return {u.code: u for u in units}


#: The fixed registry. Container units (box, carton, bundle, set, roll) are discrete and have **no
#: fixed size** — their size belongs to an item (Phase 1 `uom_conversions`), so they appear here
#: only to be recognised as discrete, each in a dimension of its own that nothing converts into.
UNITS: dict[str, Unit] = _units(
    Unit("mm", "length", Fraction(1, 1000)),
    Unit("cm", "length", Fraction(1, 100)),
    Unit("m", "length", Fraction(1)),
    Unit("in", "length", Fraction(254, 10000)),
    Unit("yd", "length", Fraction(9144, 10000)),
    Unit("mg", "mass", Fraction(1, 1_000_000)),
    Unit("g", "mass", Fraction(1, 1000)),
    Unit("kg", "mass", Fraction(1)),
    Unit("t", "mass", Fraction(1000)),
    Unit("ml", "volume", Fraction(1, 1000)),
    Unit("l", "volume", Fraction(1)),
    Unit("m2", "area", Fraction(1)),
    Unit("pcs", "count", Fraction(1), discrete=True),
    Unit("dozen", "count", Fraction(12), discrete=True),
    Unit("box", "box", Fraction(1), discrete=True),
    Unit("carton", "carton", Fraction(1), discrete=True),
    Unit("bundle", "bundle", Fraction(1), discrete=True),
    Unit("set", "set", Fraction(1), discrete=True),
    Unit("roll", "roll", Fraction(1), discrete=True),
)

#: Spellings people actually type, folded onto the registry code.
_ALIASES = {
    "pc": "pcs",
    "piece": "pcs",
    "pieces": "pcs",
    "nos": "pcs",
    "ea": "pcs",
    "each": "pcs",
    "units": "pcs",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "mtr": "m",
    "kgs": "kg",
    "gm": "g",
    "gms": "g",
    "grams": "g",
    "litre": "l",
    "liter": "l",
    "ltr": "l",
    "boxes": "box",
    "cartons": "carton",
    "bundles": "bundle",
    "sets": "set",
    "rolls": "roll",
}


def unit(code: str) -> Unit | None:
    """The registry entry for a UOM code, or `None` for a unit the registry does not know.

    Items carry a free-text `uom` today, so an unknown unit is normal, not an error: it is simply
    treated as continuous and convertible to nothing.
    """
    key = code.strip().lower()
    return UNITS.get(_ALIASES.get(key, key))


def is_discrete(code: str) -> bool:
    u = unit(code)
    return bool(u and u.discrete)


def parse_qty(value: str | int, uom: str = "", *, allow_negative: bool = False) -> int:
    """A wire quantity → milli-units, exactly.

    Accepts a decimal string (`"82.5"`, `"0.125"`, `"-3"`) or an `int` of whole units. Refuses a
    `float` (it is already inexact by the time it arrives), a `bool`, more than three decimals,
    and — for a discrete unit — any fraction at all.
    """
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise QuantityError("A quantity must be a decimal string or a whole number, never a float")
    if isinstance(value, int):
        milli = value * QTY_SCALE
    else:
        match = _DECIMAL.match(value.strip())
        if match is None:
            raise QuantityError(
                f"{value!r} is not a quantity: use digits with at most three decimals"
            )
        frac = (match["frac"] or "").ljust(3, "0")
        milli = int(match["whole"]) * QTY_SCALE + int(frac or "0")
        if match["sign"] == "-":
            milli = -milli
    if milli < 0 and not allow_negative:
        raise QuantityError("A quantity cannot be negative here")
    if abs(milli) > MAX_ABS_MILLI:
        raise QuantityError("That quantity is too large")
    if uom and is_discrete(uom) and milli % QTY_SCALE:
        raise QuantityError(f"{uom} is counted in whole units; {format_qty(milli)} is not whole")
    return milli


def format_qty(milli: int) -> str:
    """Milli-units → the exact decimal string, with no trailing zeros (`82500` → `"82.5"`)."""
    sign = "-" if milli < 0 else ""
    whole, frac = divmod(abs(milli), QTY_SCALE)
    if not frac:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{str(frac).rjust(3, '0').rstrip('0')}"


def describe(milli: int, uom: str) -> str:
    """Human form for messages: `"82.5 m"`."""
    return f"{format_qty(milli)} {uom}".rstrip()


def whole_units(milli: int) -> int:
    """The integer the pre-Phase-1 wire field `on_hand` carries — exact whenever the stock is a
    whole number of units, truncated toward zero otherwise. `on_hand_qty` is the exact field."""
    whole = abs(milli) // QTY_SCALE
    return -whole if milli < 0 else whole


def _round(value: Fraction, mode: Rounding) -> int:
    if mode is Rounding.HALF_EVEN:
        return round(value)
    floor = value.numerator // value.denominator
    if floor == value:
        return floor
    toward_zero = floor if value > 0 else floor + 1
    if mode is Rounding.DOWN:
        return toward_zero
    return toward_zero + (1 if value > 0 else -1)


def convert(milli: int, from_uom: str, to_uom: str, *, rounding: Rounding | None = None) -> int:
    """Convert milli-units of one fixed unit into milli-units of another (`1000` m → `1000000` mm).

    Raises `QuantityError` for an unknown unit, a dimension mismatch (m → kg), a unit with no
    fixed size (box), or a result that is not a whole milli-unit when no `rounding` is given.
    """
    src, dst = unit(from_uom), unit(to_uom)
    if src is None or dst is None:
        missing = from_uom if src is None else to_uom
        raise QuantityError(f"{missing!r} has no fixed conversion")
    if src.dimension != dst.dimension:
        raise QuantityError(
            f"Cannot convert {src.code} to {dst.code}: {src.dimension} is not {dst.dimension}. "
            "A per-roll or per-item ratio must be measured, not assumed"
        )
    if src.code == dst.code:
        return milli
    exact = Fraction(milli) * src.to_base / dst.to_base
    if exact.denominator == 1:
        result = exact.numerator
    elif rounding is None:
        raise QuantityError(
            f"{format_qty(milli)} {src.code} is not a whole number of thousandths of a "
            f"{dst.code}; name a rounding mode to convert it"
        )
    else:
        result = _round(exact, rounding)
    if dst.discrete and result % QTY_SCALE:
        raise QuantityError(f"{dst.code} is counted in whole units")
    return result


def value_minor(milli: int, unit_cost_minor: int) -> int:
    """Stock value in integer minor units: `quantity * unit cost`, rounded half-to-even once."""
    return round(Fraction(milli * unit_cost_minor, QTY_SCALE))
