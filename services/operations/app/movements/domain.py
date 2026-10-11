"""Pure rules for transfers, adjustments and reservations (inventory Phase 3) — no I/O.

The state machine follows ADR-0014 §6: a `*_TRANSITIONS` map, checked in one place, and a reversal
is never a silent step backwards (a dispatched transfer cannot be cancelled; it is received, or its
shortage is written off). Thresholds are compared per line in the base unit of the item's dimension
(`to_base_milli`) — metres are never summed with pieces — and a threshold of `None` means
"none set".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.inventory.quantity import unit
from platform_core.errors import ConflictProblem

#: DRAFT -> REQUESTED -> APPROVED -> IN_TRANSIT -> RECEIVED, with CANCELLED reachable before
#: dispatch and REJECTED reachable from REQUESTED. RECEIVED, CANCELLED and REJECTED are terminal.
TRANSFER_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "DRAFT": frozenset({"REQUESTED", "CANCELLED"}),
    "REQUESTED": frozenset({"APPROVED", "REJECTED", "CANCELLED"}),
    "APPROVED": frozenset({"IN_TRANSIT", "CANCELLED"}),
    "IN_TRANSIT": frozenset({"RECEIVED"}),
    "RECEIVED": frozenset(),
    "CANCELLED": frozenset(),
    "REJECTED": frozenset(),
}

#: PENDING_APPROVAL is the only state with a way out; the other four are final.
ADJUSTMENT_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "PENDING_APPROVAL": frozenset({"APPLIED", "REJECTED", "CANCELLED", "FAILED"}),
    "APPLIED": frozenset(),
    "REJECTED": frozenset(),
    "CANCELLED": frozenset(),
    "FAILED": frozenset(),
}

#: What a person may attach a reservation to. `order` is the sales order (the value the orders
#: module has always written); the other two name the demand a hand-made reservation is for.
RESERVATION_REFERENCES = ("order", "production_order", "manual")


def assert_transition(
    table: Mapping[str, frozenset[str]], current: str, target: str, *, noun: str
) -> None:
    """Raise a 409 naming the states when `current -> target` is not an allowed step."""
    if target not in table.get(current, frozenset()):
        raise ConflictProblem(f"A {noun} that is {current} cannot become {target}")


def to_base_milli(milli: int, uom: str) -> int:
    """`abs(milli)` of `uom` in its dimension's base unit (m, kg, pcs, ...), rounded up so a
    borderline quantity errs towards approval — so a threshold of "10 kg" means 10 kg whether the
    item is stocked in kg or g. A unit with no known size (a box, a free-text unit) is compared as
    it is. shortcut: one threshold still spans dimensions (kg vs m vs pcs); per-item or
    per-dimension thresholds are the upgrade."""
    known = unit(uom)
    if known is None:
        return abs(milli)
    scaled = abs(milli) * known.to_base
    return -(-scaled.numerator // scaled.denominator)


def over_threshold(threshold_milli: int | None, quantities_milli: Iterable[int]) -> bool:
    """Whether any line reaches the threshold. No threshold, no approval."""
    if threshold_milli is None:
        return False
    return any(abs(q) >= threshold_milli for q in quantities_milli)


def transfer_route(threshold_milli: int | None, quantities_milli: Iterable[int]) -> str:
    """How a requested transfer is signed off.

    * no threshold set -> `DIRECT`: a holder of `inventory.transfer.approve` (not the requester)
    * a threshold, every line under it -> `AUTO`: approved on the spot, nothing to wait for
    * a threshold, a line at or over it -> `SERVICE`: the approval service decides (ADR-0006)
    """
    if threshold_milli is None:
        return "DIRECT"
    return "SERVICE" if over_threshold(threshold_milli, quantities_milli) else "AUTO"


def format_number(prefix: str, n: int) -> str:
    """`TRF-000042`. Six digits is a floor, not a cap."""
    return f"{prefix}-{n:06d}"


def merge_amounts[K](pairs: Iterable[tuple[K, int]]) -> dict[K, int]:
    """Sum amounts that land on the same key (two transfer lines feeding one in-transit bucket) —
    the kernel refuses a move that touches a bucket twice, so the caller folds them first."""
    merged: dict[K, int] = {}
    for key, amount in pairs:
        merged[key] = merged.get(key, 0) + amount
    return merged
