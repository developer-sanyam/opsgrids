"""Pure functions over the chain walk. No I/O — the walk itself is a query (`app/selectors.py`).

ADR-0006 §5 is explicit about the split: the recursive CTE returns at most 33 rows (the 32-rung
depth cap plus the requester's own starting row), and *choosing* an approver from those rows is a
property of picking, not of traversal — "a small pure function in `app/domain.py` ...that stays
pure and stays trivially testable over a list of at most 33 items, which is what `domain.py` is
for; what it is not is a substitute for a query." `pick_approver` is that function. It is not
exercised by an endpoint in this slice (nothing opens a request yet), but the chain endpoint
(`GET /chain/{employee_id}`) needs the identical skip rule to mark which rungs it walked past, so
it is written once, here, rather than re-implemented at each call site.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

#: The number of rungs *above the requester* the walk (and employee's own cycle check, data
#: model §4.3) will accept — a 32-rung proposed chain is accepted, a 33-rung one refused, on both
#: sides (ADR-0006 §5). The requester's own row is not a rung, so `app/selectors.py`'s CTE caps
#: total rows at `MAX_CHAIN_DEPTH + 1`, not at `MAX_CHAIN_DEPTH`.
MAX_CHAIN_DEPTH = 32


@dataclass(frozen=True, slots=True)
class Rung:
    """One row the chain-walk CTE returned — a candidate approver at some depth."""

    employee_id: uuid.UUID
    reporting_manager_id: uuid.UUID | None
    dotted_line_manager_id: uuid.UUID | None
    status: str
    display_name: str
    employee_no: str
    location_code: str | None
    depth: int


def pick_approver(rungs: list[Rung], requester_id: uuid.UUID) -> Rung | None:
    """The first rung that is neither `exited` nor the requester.

    `rungs` is the CTE's output in depth order, starting at the requester's own row (depth 1)
    and walking upward through `reporting_manager_id`. The chain walk does not stop at an exited
    node — skipping is a property of *choosing*, not of traversal, so the query returns every
    rung and this function is what skips: `requester → exited manager → director` must reach the
    director. Returns `None` when nobody usable is found — an empty chain, a chain that is all
    exited managers, or a chain that never got past the requester's own row (no manager at all).
    """
    for rung in rungs:
        if rung.employee_id == requester_id:
            continue
        if rung.status == "exited":
            continue
        return rung
    return None


def skip_flags(rungs: list[Rung], requester_id: uuid.UUID) -> list[tuple[Rung, bool]]:
    """Every rung after the requester's own row, paired with whether it was skipped.

    Used to render `GET /chain` (ADR-0006 §5: "marks skipped exited rungs so the shape of a
    chain is explainable rather than mysterious"), and shares `pick_approver`'s exact rule so the
    two never drift: a rung is skipped here if and only if `pick_approver` would have passed over
    it. The requester's own starting row (depth 1) is never part of the displayed chain — the
    chain shows who approves *for* them, not themselves.

    `pick_approver` stops at the **first** rung that is neither exited nor the requester — it
    never looks past that point. So only an exited rung *before* that pick is one it actually
    walked past; an exited rung beyond it (a director who has since exited, above the manager who
    was picked) was never evaluated at all, and marking it skipped would claim the walk passed
    over someone it never reached.
    """
    picked = False
    out: list[tuple[Rung, bool]] = []
    for rung in rungs:
        if rung.employee_id == requester_id:
            continue
        if picked:
            out.append((rung, False))
            continue
        is_exited = rung.status == "exited"
        out.append((rung, is_exited))
        if not is_exited:
            picked = True
    return out
