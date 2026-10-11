"""Pure MRP — no I/O, no database, no clock, no float.

The whole planning algorithm lives here so it can be proven exactly. Quantities are integer
milli-units; every division is a single integer division, rounded **up**, in
`manufacturing.domain.required_milli` (the same function production orders use, so MRP and a
production order never disagree about what a BOM line needs).

The algorithm, per run:

1. Gross requirements: every demand is a `Peg` on its item.
2. Items are processed in **low-level-code order** (Kahn's algorithm over the BOM graph, ties
   broken by item id): an item is only netted once every produced parent that could ask for it
   has been. That is what makes one shared supply pool safe — each item is netted exactly once,
   against all of its demand at once.
3. Per item: `net = max(0, gross - supply)` where `supply = max(0, on_hand - reserved) + open PO
   + open production output`. With `net > 0`: a produced item (a BOM in force) is a `MAKE`
   shortage and its net — not its gross — explodes into the components (stock of a sub-assembly
   is used before more is planned); a purchased item is a `BUY` shortage.
4. Pegging: supply covers the earliest demand first (due date, then source), and the uncovered
   tail is what a produced item's explosion is attributed to — so every component shortage
   traces back to the root demand that caused it, with integer-exact shares.

Cycles cannot loop: an item stuck with an unprocessed parent after the queue drains is a cycle,
reported with its path.
"""

from __future__ import annotations

import hashlib
import heapq
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from app.manufacturing.domain import find_cycle, required_milli

SOURCE_TYPES = ("SALES_ORDER", "PRODUCTION_ORDER", "MANUAL")


class MrpCycleError(ValueError):
    """The BOM graph under the demanded items is cyclic. `path` is `[a, ..., a]` (item ids)."""

    def __init__(self, path: list[str]) -> None:
        super().__init__("cycle: " + " -> ".join(path))
        self.path = path


# ------------------------------------------------------------------ inputs


@dataclass(frozen=True, slots=True)
class Peg:
    """A claim on an item: root demand `source_*`, optionally through a produced parent."""

    source_type: str
    source_id: str | None
    source_ref: str
    quantity_milli: int
    due: date | None = None
    parent_item_id: str | None = None

    def sort_key(self) -> tuple[bool, date, str, str, str, str]:
        return (
            self.due is None,
            self.due or date.min,
            self.source_type,
            self.source_ref,
            self.source_id or "",
            self.parent_item_id or "",
        )


@dataclass(frozen=True, slots=True)
class Supply:
    on_hand_milli: int = 0
    reserved_milli: int = 0
    open_po_milli: int = 0
    open_production_milli: int = 0

    @property
    def free_milli(self) -> int:
        return max(0, self.on_hand_milli - self.reserved_milli)

    @property
    def total_milli(self) -> int:
        return self.free_milli + self.open_po_milli + self.open_production_milli


@dataclass(frozen=True, slots=True)
class MrpLine:
    component_item_id: str
    quantity_milli: int
    scrap_bps: int = 0
    #: A chosen substitute's conversion ratio (1000 = 1:1); 1000 when none was chosen.
    ratio_milli: int = 1000
    discrete: bool = False


@dataclass(frozen=True, slots=True)
class MrpBom:
    item_id: str
    output_milli: int
    lines: tuple[MrpLine, ...] = ()


# ------------------------------------------------------------------ outputs


@dataclass(slots=True)
class ItemPlan:
    item_id: str
    level: int
    gross_milli: int
    supply: Supply
    net_milli: int
    #: `MAKE` / `BUY` for a shortage, `None` when supply covered the item.
    action: str | None
    need_by: date | None
    #: The gross contributors, aggregated by (source, parent), in deterministic order.
    pegs: list[Peg] = field(default_factory=list)


@dataclass(slots=True)
class PlanResult:
    items: list[ItemPlan]

    @property
    def shortages(self) -> list[ItemPlan]:
        return [i for i in self.items if i.net_milli > 0]


# ------------------------------------------------------------------ helpers


def allocate_proportional(total: int, weights: Sequence[int]) -> list[int]:
    """Split `total` across `weights` exactly: floor shares, then the leftover units one each to
    the largest fractional remainders (ties: the earliest index). `sum(result) == total`
    whenever any weight is positive. Integer-only, deterministic."""
    weight_sum = sum(weights)
    if total <= 0 or weight_sum <= 0:
        return [0] * len(weights)
    shares = [total * w // weight_sum for w in weights]
    remainders = [total * w % weight_sum for w in weights]
    leftover = total - sum(shares)
    order = sorted(range(len(weights)), key=lambda i: (-remainders[i], i))
    for i in order[:leftover]:
        shares[i] += 1
    return shares


def _merge(pegs: Iterable[Peg]) -> list[Peg]:
    merged: dict[tuple[str, str | None, str, str | None, date | None], int] = defaultdict(int)
    for p in pegs:
        merged[(p.source_type, p.source_id, p.source_ref, p.parent_item_id, p.due)] += (
            p.quantity_milli
        )
    out = [
        Peg(st, sid, ref, qty, due, parent)
        for (st, sid, ref, parent, due), qty in merged.items()
        if qty > 0
    ]
    out.sort(key=Peg.sort_key)
    return out


# ------------------------------------------------------------------ the plan


def plan(
    demands: Iterable[Peg],
    demand_items: Iterable[str],
    supply_of: Callable[[str], Supply],
    bom_of: Callable[[str], MrpBom | None],
) -> PlanResult:
    """Net and explode. `demands` is parallel to `demand_items` (the item each peg is for).

    `bom_of(item)` is the BOM in force on the plan date, or `None` for a purchased item. It is
    called for every reachable item, once; `supply_of(item)` once per item.
    """
    pegs_by_item: dict[str, list[Peg]] = defaultdict(list)
    for item, peg in zip(demand_items, demands, strict=True):
        if peg.quantity_milli > 0:
            pegs_by_item[item].append(peg)

    # --- the reachable graph
    boms: dict[str, MrpBom | None] = {}
    children: dict[str, list[str]] = {}
    stack = sorted(pegs_by_item)
    while stack:
        item = stack.pop()
        if item in boms:
            continue
        bom = bom_of(item)
        boms[item] = bom
        kids = sorted({line.component_item_id for line in bom.lines}) if bom else []
        children[item] = kids
        stack.extend(k for k in kids if k not in boms)

    indegree: dict[str, int] = dict.fromkeys(boms, 0)
    for kids in children.values():
        for kid in kids:
            indegree[kid] += 1

    # --- Kahn: parents strictly before children, deterministic by item id
    level: dict[str, int] = dict.fromkeys(boms, 0)
    ready = [i for i, d in indegree.items() if d == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        item = heapq.heappop(ready)
        order.append(item)
        for kid in children[item]:
            level[kid] = max(level[kid], level[item] + 1)
            indegree[kid] -= 1
            if indegree[kid] == 0:
                heapq.heappush(ready, kid)
    if len(order) < len(boms):
        stuck = sorted(i for i, d in indegree.items() if d > 0)
        edges = {i: children[i] for i in stuck}
        for root in stuck:
            cycle = find_cycle(root, edges)
            if cycle is not None:
                raise MrpCycleError(cycle)
        raise MrpCycleError(stuck)  # pragma: no cover - a stuck node always sits on a cycle

    # --- net, item by item
    items: list[ItemPlan] = []
    for item in order:
        pegs = _merge(pegs_by_item.get(item, ()))
        gross = sum(p.quantity_milli for p in pegs)
        if gross <= 0:
            continue
        supply = supply_of(item)
        net = max(0, gross - supply.total_milli)
        bom = boms[item]
        action = None if net == 0 else ("MAKE" if bom is not None else "BUY")

        # supply covers the earliest demand first; the uncovered tail is what is planned for
        remaining = supply.total_milli
        uncovered: list[int] = []
        for p in pegs:
            covered = min(p.quantity_milli, remaining)
            remaining -= covered
            uncovered.append(p.quantity_milli - covered)
        tail_dues = [p.due for p, u in zip(pegs, uncovered, strict=True) if u > 0 and p.due]
        need_by = min(tail_dues) if net > 0 and tail_dues else None

        items.append(ItemPlan(item, level[item], gross, supply, net, action, need_by, pegs))

        if net > 0 and bom is not None:
            for line in bom.lines:
                need = required_milli(
                    component_milli=line.quantity_milli,
                    output_milli=bom.output_milli,
                    planned_milli=net,
                    scrap_bps=line.scrap_bps,
                    ratio_milli=line.ratio_milli,
                    discrete=line.discrete,
                )
                shares = allocate_proportional(need, uncovered)
                for p, share in zip(pegs, shares, strict=True):
                    if share > 0:
                        pegs_by_item[line.component_item_id].append(
                            Peg(
                                p.source_type,
                                p.source_id,
                                p.source_ref,
                                share,
                                p.due,
                                parent_item_id=item,
                            )
                        )
    return PlanResult(items)


def result_digest(result: PlanResult) -> str:
    """A stable fingerprint of the shortages, independent of ids the database assigns."""
    canonical = [
        [
            s.item_id,
            s.action,
            s.level,
            s.gross_milli,
            s.supply.on_hand_milli,
            s.supply.reserved_milli,
            s.supply.open_po_milli,
            s.supply.open_production_milli,
            s.net_milli,
            s.need_by.isoformat() if s.need_by else None,
            [[p.source_type, p.source_id, p.parent_item_id, p.quantity_milli] for p in s.pegs],
        ]
        for s in sorted(result.shortages, key=lambda s: s.item_id)
    ]
    return hashlib.sha256(
        json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
