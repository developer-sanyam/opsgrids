"""Pure quality rules — no I/O (service anatomy: `app/domain.py` for real algorithms only).

Three things live here because they are algorithms with edge cases, not plumbing:

* the inspection **state machine** (`INSPECTION_TRANSITIONS`);
* judging one measured value against a parameter's bounds (`evaluate_param`) and what a set of
  judgements allows (`can_release`, `suggested_outcome`);
* splitting an inspected quantity into accepted and rejected (`split_quantity`).

Quantities and bounds are milli-scaled integers throughout; nothing here touches a float.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

#: An inspection's whole life. `HOLD` is "needs a second look" (stock stays on quality hold);
#: `REWORK` sends the stock back for correction and returns to `PENDING` through a re-inspection.
#: `PASSED`, `FAILED` and `PARTIAL_PASS` are terminal — the stock has moved, and a correction is a
#: new inspection (the ledger is append-only; so is the story of why stock moved).
INSPECTION_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "PENDING": frozenset({"PASSED", "FAILED", "HOLD", "REWORK", "PARTIAL_PASS"}),
    "HOLD": frozenset({"PASSED", "FAILED", "REWORK", "PARTIAL_PASS"}),
    "REWORK": frozenset({"PENDING", "FAILED"}),
    "PASSED": frozenset(),
    "FAILED": frozenset(),
    "PARTIAL_PASS": frozenset(),
}

#: States in which the inspected stock has been decided and results can no longer change.
FINAL_STATUSES = frozenset(s for s, nxt in INSPECTION_TRANSITIONS.items() if not nxt)

#: States in which measurements may still be recorded.
RESULT_STATUSES = frozenset({"PENDING", "HOLD"})

#: Statuses whose stock-decision left a rejected quantity behind to be disposed of.
DISPOSABLE_STATUSES = frozenset({"FAILED", "PARTIAL_PASS"})

REJECT_BUCKETS = ("REJECTED", "QUARANTINE")
DISPOSITIONS = ("RETURN_TO_VENDOR", "SCRAP")


def can_transition_inspection(current: str, new: str) -> bool:
    """Whether an inspection may move `current` -> `new`. A no-op "move" to the same status is
    refused too (409), the same convention every other state machine in this service documents."""
    return new in INSPECTION_TRANSITIONS.get(current, frozenset())


def lot_quality_status(inspection_statuses: Iterable[str]) -> str:
    """A lot's `quality_status` from the statuses of every inspection over it: anything still open
    keeps it `PENDING` (`ON_HOLD` when one is on hold); once all are decided it is `APPROVED` if
    any stock was released, else `REJECTED`."""
    statuses = set(inspection_statuses)
    if "HOLD" in statuses:
        return "ON_HOLD"
    if statuses & {"PENDING", "REWORK"}:
        return "PENDING"
    return "APPROVED" if statuses & {"PASSED", "PARTIAL_PASS"} else "REJECTED"


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """The slice of a template parameter judging needs."""

    kind: str  # numeric | pass_fail
    min_milli: int | None = None
    max_milli: int | None = None


def evaluate_param(spec: ParamSpec, *, value_milli: int | None, passed: bool | None) -> bool:
    """Judge one reading. A numeric parameter passes when the value is inside its bounds
    (inclusive; an open side is unbounded); a pass/fail parameter passes exactly when the
    inspector says so. The server decides — a client-supplied verdict on a numeric parameter is
    ignored, never trusted."""
    if spec.kind == "pass_fail":
        if passed is None:
            raise ValueError("A pass/fail parameter needs a passed verdict")
        return passed
    if value_milli is None:
        raise ValueError("A numeric parameter needs a value")
    if spec.min_milli is not None and value_milli < spec.min_milli:
        return False
    return not (spec.max_milli is not None and value_milli > spec.max_milli)


@dataclass(frozen=True, slots=True)
class Judged:
    passed: bool
    is_critical: bool


def can_release(results: Iterable[Judged]) -> bool:
    """A failed *critical* parameter makes release impossible; reject, rework or hold instead.
    Non-critical failures are the approver's call (a note is required — enforced by the caller)."""
    return not any(r.is_critical and not r.passed for r in results)


def suggested_outcome(results: Iterable[Judged]) -> str:
    """What the readings alone recommend, for the UI to surface — never applied automatically."""
    rows = list(results)
    if not rows:
        return "PENDING"
    if any(r.is_critical and not r.passed for r in rows):
        return "FAILED"
    if any(not r.passed for r in rows):
        return "HOLD"
    return "PASSED"


def has_noncritical_failure(results: Iterable[Judged]) -> bool:
    return any(not r.passed and not r.is_critical for r in results)


def split_quantity(quantity_milli: int, accepted_milli: int | None) -> tuple[int, int, str]:
    """`(accepted, rejected, status)` for a release. `None` accepts everything (`PASSED`); anything
    in `(0, quantity)` is a `PARTIAL_PASS`. Zero accepted is a rejection, and more than the
    inspected quantity is impossible — both are refused here rather than clamped."""
    if quantity_milli <= 0:
        raise ValueError("Nothing to split")
    if accepted_milli is None or accepted_milli == quantity_milli:
        return quantity_milli, 0, "PASSED"
    if accepted_milli <= 0:
        raise ValueError("Accepting nothing is a rejection, not a release")
    if accepted_milli > quantity_milli:
        raise ValueError("Cannot accept more than was inspected")
    return accepted_milli, quantity_milli - accepted_milli, "PARTIAL_PASS"
