"""Pure manufacturing rules — no I/O, no database, no clock.

Every quantity is an integer of milli-units (`app.inventory.quantity`), every percentage an
integer of basis points (2.5 % is 250). Nothing here ever touches a float: the one place a
fraction appears (a BOM line's quantity per output unit, scaled to an order) is computed as a
single integer numerator over a single integer denominator and **rounded up exactly once**, so
the same inputs give the same figure on every machine and a partial output never rounds twice.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

BPS = 10_000
QTY_SCALE = 1000

# ------------------------------------------------------------------ state machines

#: A BOM version's life. A version is mutable only while `DRAFT`; `PUBLISHED` is frozen (also by a
#: database trigger); `RETIRED` stops it being picked for new orders and is as frozen as published.
BOM_VERSION_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"PUBLISHED"}),
    "PUBLISHED": frozenset({"RETIRED"}),
    "RETIRED": frozenset(),
}

#: A production order's life. `IN_PROGRESS` is entered by the first issue (or an explicit start);
#: only `COMPLETED` and `CANCELLED` are terminal. Cancelling from `IN_PROGRESS` is further refused
#: while material is still in WIP (checked in the service — the map cannot express it).
PRODUCTION_ORDER_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"APPROVED", "CANCELLED"}),
    "APPROVED": frozenset({"IN_PROGRESS", "CANCELLED"}),
    "IN_PROGRESS": frozenset({"COMPLETED", "CANCELLED"}),
    "COMPLETED": frozenset(),
    "CANCELLED": frozenset(),
}

BOM_VERSION_STATUSES = tuple(BOM_VERSION_TRANSITIONS)
PRODUCTION_ORDER_STATUSES = tuple(PRODUCTION_ORDER_TRANSITIONS)

WASTAGE_TYPES = ("CUTTING", "DEFECT", "SPILLAGE", "DAMAGE", "PROCESS_LOSS", "LEFTOVER", "OTHER")
OUTPUT_STATUSES = ("AVAILABLE", "QUALITY_HOLD")


def can_transition_bom_version(current: str, new: str) -> bool:
    return new in BOM_VERSION_TRANSITIONS.get(current, frozenset())


def can_transition_production_order(current: str, new: str) -> bool:
    """A no-op "move" to the same status is a miss too (the convention every `*_TRANSITIONS`
    map in this service documents)."""
    return new in PRODUCTION_ORDER_TRANSITIONS.get(current, frozenset())


# ------------------------------------------------------------------ percentages

_PCT = re.compile(r"^(?P<whole>\d{1,3})(?:\.(?P<frac>\d{1,2}))?$")


class PercentError(ValueError):
    pass


def parse_pct_bps(value: str | int, *, maximum_bps: int = BPS) -> int:
    """`"2.5"` -> 250. At most two decimals (basis points are the unit — no silent rounding),
    never a float, never above `maximum_bps`."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise PercentError("A percentage must be a decimal string, never a float")
    match = _PCT.match(str(value).strip())
    if match is None:
        raise PercentError(f"{value!r} is not a percentage: digits with at most two decimals")
    bps = int(match["whole"]) * 100 + int((match["frac"] or "").ljust(2, "0") or "0")
    if bps > maximum_bps:
        raise PercentError(f"A percentage here cannot exceed {format_bps(maximum_bps)}")
    return bps


def format_bps(bps: int) -> str:
    whole, frac = divmod(bps, 100)
    if not frac:
        return str(whole)
    return f"{whole}.{str(frac).rjust(2, '0').rstrip('0')}"


# ------------------------------------------------------------------ requirement arithmetic


def _ceil_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    return -(-numerator // denominator)


def _round_up_discrete(milli: int) -> int:
    return _ceil_div(milli, QTY_SCALE) * QTY_SCALE


def required_milli(
    *,
    component_milli: int,
    output_milli: int,
    planned_milli: int,
    scrap_bps: int = 0,
    ratio_milli: int = 1000,
    discrete: bool = False,
) -> int:
    """What a BOM line needs for `planned_milli` of output, scrap allowance included.

    `component_milli` is the line's quantity per `output_milli` of the produced item (one BOM
    batch). `ratio_milli` converts the BOM item to a substitute (1000 = 1:1). One integer
    division, rounded **up** (never plan less than is needed); a discrete unit is rounded up to
    whole units after.
    """
    numerator = component_milli * planned_milli * (BPS + scrap_bps) * ratio_milli
    value = _ceil_div(numerator, output_milli * BPS * QTY_SCALE)
    return _round_up_discrete(value) if discrete else value


def theoretical_milli(
    *,
    component_milli: int,
    output_milli: int,
    good_milli: int,
    ratio_milli: int = 1000,
    discrete: bool = False,
) -> int:
    """Cumulative standard consumption (no scrap allowance) for `good_milli` produced in total.

    Always called with the **running total** of good output, and the difference against what is
    already consumed is what a new output draws — so splitting 100 into 33 + 67 consumes exactly
    what 100 in one go would, with no rounding drift between the parts.
    """
    return required_milli(
        component_milli=component_milli,
        output_milli=output_milli,
        planned_milli=good_milli,
        scrap_bps=0,
        ratio_milli=ratio_milli,
        discrete=discrete,
    )


def allowance_milli(theoretical_planned_milli: int, threshold_bps: int) -> int:
    """How much of a material may be wasted before the threshold is crossed."""
    return _ceil_div(theoretical_planned_milli * threshold_bps, BPS)


def pick_threshold(
    rows: Iterable[tuple[str | None, str | None, int]],
    *,
    item_id: str,
    wastage_type: str,
) -> tuple[str | None, int] | None:
    """The most specific configured threshold for a produced item and a wastage type: item+type,
    then item, then type, then the organization-wide default. `rows` are
    `(item_id | None, wastage_type | None, max_bps)`. Returns `(row_wastage_type, max_bps)` — the
    type the matched row is scoped to (None: every type) is what its wastage is summed over — or
    `None` when nothing matches."""
    best: tuple[int, str | None, int] | None = None
    for row_item, row_type, bps in rows:
        if row_item is not None and row_item != item_id:
            continue
        if row_type is not None and row_type != wastage_type:
            continue
        specificity = (2 if row_item is not None else 0) + (1 if row_type is not None else 0)
        if best is None or specificity > best[0]:
            best = (specificity, row_type, bps)
    return None if best is None else (best[1], best[2])


def allocate_fifo(wip: list[tuple[str, int]], need_milli: int) -> tuple[list[tuple[str, int]], int]:
    """Take `need_milli` from `wip` — `(pick_id, wip_milli)` pairs in the order they were issued.
    Returns `(draws, shortfall)`; `shortfall` is 0 when fully covered."""
    draws: list[tuple[str, int]] = []
    left = need_milli
    for pick_id, available in wip:
        if left <= 0:
            break
        take = min(available, left)
        if take > 0:
            draws.append((pick_id, take))
            left -= take
    return draws, max(left, 0)


# ------------------------------------------------------------------ effective dates


def in_window(on: date, start: date | None, end: date | None) -> bool:
    return (start is None or start <= on) and (end is None or on <= end)


@dataclass(frozen=True, slots=True)
class VersionFacts:
    id: str
    version_no: int
    effective_from: date | None
    effective_to: date | None
    status: str


def resolve_version(versions: Iterable[VersionFacts], on: date) -> VersionFacts | None:
    """The published version in force on `on`: the one with the latest start date not after it
    (a later version supersedes an earlier one from its own start date, with no edit to the
    earlier row), skipping any whose own end date has passed. Ties go to the higher number."""
    live = [
        v
        for v in versions
        if v.status == "PUBLISHED" and in_window(on, v.effective_from, v.effective_to)
    ]
    if not live:
        return None
    return max(live, key=lambda v: (v.effective_from or date.min, v.version_no))


# ------------------------------------------------------------------ cycles and explosion


def find_cycle(root: str, edges: Mapping[str, Iterable[str]]) -> list[str] | None:
    """A path `root -> ... -> root` if `root` can reach itself through `edges` (parent item ->
    component items), else `None`. Iterative, so a deep BOM cannot exhaust the stack."""
    stack: list[tuple[str, list[str]]] = [(root, [root])]
    seen: set[str] = set()
    while stack:
        node, path = stack.pop()
        for child in edges.get(node, ()):
            if child == root:
                return [*path, root]
            if child in seen:
                continue
            seen.add(child)
            stack.append((child, [*path, child]))
    return None


@dataclass(frozen=True, slots=True)
class ExplodeLine:
    component_item_id: str
    quantity_milli: int
    scrap_bps: int = 0
    discrete: bool = False


@dataclass(frozen=True, slots=True)
class ExplodeBom:
    item_id: str
    output_milli: int
    lines: tuple[ExplodeLine, ...] = ()


@dataclass(slots=True)
class ExplodeNode:
    item_id: str
    level: int
    quantity_milli: int
    is_produced: bool
    children: list[ExplodeNode] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "level": self.level,
            "quantity_milli": self.quantity_milli,
            "is_produced": self.is_produced,
            "children": [c.as_dict() for c in self.children],
        }


MAX_DEPTH = 25


def explode(
    item_id: str,
    quantity_milli: int,
    resolve: Callable[[str], ExplodeBom | None],
    *,
    _level: int = 0,
    _path: tuple[str, ...] = (),
) -> ExplodeNode:
    """The multi-level requirement tree for `quantity_milli` of `item_id`. `resolve(item)` gives
    the BOM in force for an item, or `None` for a purchased (leaf) item. Each level's need is
    rounded up once, in `required_milli`, before it is carried down into the next BOM — what is
    shown is what would actually be planned at that level. A cycle or an absurd depth is refused,
    never looped on."""
    if item_id in _path:
        raise ValueError("cycle: " + " -> ".join([*_path, item_id]))
    if _level > MAX_DEPTH:
        raise ValueError(f"bill of materials is nested deeper than {MAX_DEPTH} levels")
    bom = resolve(item_id)
    node = ExplodeNode(item_id, _level, quantity_milli, is_produced=bom is not None)
    if bom is None:
        return node
    for line in bom.lines:
        need = required_milli(
            component_milli=line.quantity_milli,
            output_milli=bom.output_milli,
            planned_milli=quantity_milli,
            scrap_bps=line.scrap_bps,
            discrete=line.discrete,
        )
        node.children.append(
            explode(
                line.component_item_id,
                need,
                resolve,
                _level=_level + 1,
                _path=(*_path, item_id),
            )
        )
    return node
